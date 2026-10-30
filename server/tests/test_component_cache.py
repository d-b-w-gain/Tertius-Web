from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
from types import SimpleNamespace
from uuid import uuid4

import pytest

from core import component_cache as cc
from core.component_cache import (
    CacheScope,
    ComponentCacheStore,
    prefetch_entries,
    publish_entries,
    report_entries,
)

ENTRY = "roof_cladding/0123456789abcdef01234567"
OTHER = "wall/89abcdef0123456789abcdef"


class FakeObjectStore:
    """In-memory stand-in for a nats-py ObjectStore."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.corrupt: set[str] = set()
        self.unavailable = False
        self.puts: list[str] = []

    def _check(self):
        if self.unavailable:
            raise ConnectionError("nats is down")

    async def get_info(self, name):
        from nats.js.errors import ObjectNotFoundError

        self._check()
        if name not in self.objects:
            raise ObjectNotFoundError
        content = self.objects[name]
        return SimpleNamespace(
            name=name,
            size=len(content),
            deleted=False,
            digest="SHA-256="
            + base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode(),
            is_link=lambda: False,
        )

    async def get(self, name, writeinto):
        from nats.js.errors import DigestMismatchError, ObjectNotFoundError

        self._check()
        if name not in self.objects:
            raise ObjectNotFoundError
        if name in self.corrupt:
            raise DigestMismatchError
        writeinto.write(self.objects[name])

    async def put(self, name, content):
        self._check()
        self.puts.append(name)
        self.objects[name] = bytes(content)


def write_entry(cache_dir, entry, brep=b"BREP-DATA", sidecar=None):
    folder, key = entry.split("/")
    (cache_dir / folder).mkdir(parents=True, exist_ok=True)
    (cache_dir / folder / f"{key}.brep").write_bytes(brep)
    (cache_dir / folder / f"{key}.json").write_text(
        json.dumps(sidecar or {"key": key}), encoding="utf-8"
    )


def report(*components):
    return {
        "schema_version": "tertius.components.v1",
        "counts": {},
        "components": [
            {"component": entry.split("/")[0], "entry": entry, "cache_status": status}
            for entry, status in components
        ],
    }


def run(coro):
    return asyncio.run(coro)


def test_scope_hides_raw_ids_and_separates_tenants_and_projects():
    tenant_a, tenant_b, project = uuid4(), uuid4(), uuid4()
    a = CacheScope.for_project(tenant_a, project)
    b = CacheScope.for_project(tenant_b, project)
    a_other = CacheScope.for_project(tenant_a, uuid4())

    assert a.entry_object(ENTRY, "brep") != b.entry_object(ENTRY, "brep")
    assert a.entry_object(ENTRY, "brep") == a_other.entry_object(ENTRY, "brep")
    assert a.manifest_object() != a_other.manifest_object()
    for name in (a.entry_object(ENTRY, "json"), a.manifest_object()):
        assert str(tenant_a) not in name and str(project) not in name
        assert str(tenant_a).replace("-", "") not in name


@pytest.mark.parametrize(
    "entry",
    [
        "../etc/0123456789abcdef01234567",
        "roof/0123456789abcdef0123456",
        "roof/0123456789ABCDEF01234567",
        "roof/sub/0123456789abcdef01234567",
        "",
        None,
    ],
)
def test_invalid_entry_names_are_rejected(entry):
    assert cc.valid_entry_name(entry) is False
    with pytest.raises(ValueError):
        CacheScope.for_project("t", "p").entry_object(entry, "brep")


def test_publish_then_prefetch_restores_whole_entries(tmp_path):
    store = FakeObjectStore()
    cache = ComponentCacheStore(store, max_entry_bytes=1024)
    scope = CacheScope.for_project("tenant", "project")
    built_dir = tmp_path / "built"
    write_entry(built_dir, ENTRY)
    write_entry(built_dir, OTHER, brep=b"WALL")

    published = run(
        publish_entries(cache, scope, built_dir, report((ENTRY, "built"), (OTHER, "built")))
    )
    assert (published.outcome, published.entries) == ("ok", 2)
    assert published.manifest_entries == [ENTRY, OTHER]
    # Sidecars go up after their BREPs, and the manifest last.
    assert store.puts[-1] == scope.manifest_object()
    assert store.puts.index(scope.entry_object(ENTRY, "brep")) < store.puts.index(
        scope.entry_object(ENTRY, "json")
    )

    restored_dir = tmp_path / "restored"
    prefetched = run(prefetch_entries(cache, scope, restored_dir, max_bytes=10_000))
    assert (prefetched.outcome, prefetched.entries) == ("ok", 2)
    for entry in (ENTRY, OTHER):
        folder, key = entry.split("/")
        assert (restored_dir / folder / f"{key}.brep").read_bytes() == (
            built_dir / folder / f"{key}.brep"
        ).read_bytes()
        assert (restored_dir / folder / f"{key}.json").is_file()


def test_republishing_the_same_entries_is_idempotent(tmp_path):
    store = FakeObjectStore()
    cache = ComponentCacheStore(store, max_entry_bytes=1024)
    scope = CacheScope.for_project("tenant", "project")
    write_entry(tmp_path, ENTRY)

    run(publish_entries(cache, scope, tmp_path, report((ENTRY, "built"))))
    puts = len(store.puts)
    again = run(publish_entries(cache, scope, tmp_path, report((ENTRY, "built"))))

    assert (again.entries, again.skipped) == (0, 1)
    assert len(store.puts) == puts  # unchanged manifest is not rewritten either


def test_hits_stay_in_the_manifest_without_being_uploaded(tmp_path):
    store = FakeObjectStore()
    cache = ComponentCacheStore(store, max_entry_bytes=1024)
    scope = CacheScope.for_project("tenant", "project")
    write_entry(tmp_path, OTHER)

    result = run(
        publish_entries(cache, scope, tmp_path, report((ENTRY, "hit"), (OTHER, "built")))
    )

    assert result.manifest_entries == [ENTRY, OTHER]
    assert scope.entry_object(ENTRY, "brep") not in store.puts


def test_another_tenant_cannot_read_entries_even_with_the_same_ids(tmp_path):
    store = FakeObjectStore()
    cache = ComponentCacheStore(store, max_entry_bytes=1024)
    project = uuid4()
    owner = CacheScope.for_project(uuid4(), project)
    intruder = CacheScope.for_project(uuid4(), project)
    write_entry(tmp_path / "built", ENTRY)
    run(publish_entries(cache, owner, tmp_path / "built", report((ENTRY, "built"))))

    result = run(prefetch_entries(cache, intruder, tmp_path / "intruder", max_bytes=10_000))

    assert (result.outcome, result.entries) == ("miss", 0)
    assert not (tmp_path / "intruder").exists()
    # Even naming the owner's entry directly resolves inside the intruder's
    # own namespace, where nothing exists.
    assert run(cache.read(intruder.entry_object(ENTRY, "brep"))) is None


def test_corrupt_entry_becomes_a_miss(tmp_path):
    store = FakeObjectStore()
    cache = ComponentCacheStore(store, max_entry_bytes=1024)
    scope = CacheScope.for_project("tenant", "project")
    write_entry(tmp_path / "built", ENTRY)
    write_entry(tmp_path / "built", OTHER)
    run(publish_entries(cache, scope, tmp_path / "built", report((ENTRY, "built"), (OTHER, "built"))))
    store.corrupt.add(scope.entry_object(ENTRY, "brep"))

    result = run(prefetch_entries(cache, scope, tmp_path / "restored", max_bytes=10_000))

    assert (result.outcome, result.entries, result.failed) == ("integrity_failed", 1, 1)
    assert not (tmp_path / "restored" / "roof_cladding").exists()
    assert (tmp_path / "restored" / "wall").is_dir()


def test_oversized_object_is_rejected_on_read_and_write(tmp_path):
    store = FakeObjectStore()
    cache = ComponentCacheStore(store, max_entry_bytes=8)
    scope = CacheScope.for_project("tenant", "project")
    store.objects[scope.entry_object(ENTRY, "brep")] = b"x" * 9

    with pytest.raises(cc.ObjectIntegrityError):
        run(cache.read(scope.entry_object(ENTRY, "brep")))
    write_entry(tmp_path, OTHER, brep=b"y" * 9)
    result = run(publish_entries(cache, scope, tmp_path, report((OTHER, "built"))))
    assert result.failed == 1
    assert result.manifest_entries == []


def test_prefetch_respects_its_byte_budget(tmp_path):
    store = FakeObjectStore()
    cache = ComponentCacheStore(store, max_entry_bytes=1024)
    scope = CacheScope.for_project("tenant", "project")
    write_entry(tmp_path / "built", ENTRY, brep=b"a" * 200)
    write_entry(tmp_path / "built", OTHER, brep=b"b" * 200)
    run(publish_entries(cache, scope, tmp_path / "built", report((ENTRY, "built"), (OTHER, "built"))))

    result = run(prefetch_entries(cache, scope, tmp_path / "restored", max_bytes=300))

    assert (result.entries, result.skipped) == (1, 1)


def test_store_outage_is_reported_not_raised(tmp_path):
    store = FakeObjectStore()
    store.unavailable = True
    cache = ComponentCacheStore(store, max_entry_bytes=1024)
    scope = CacheScope.for_project("tenant", "project")
    write_entry(tmp_path, ENTRY)

    assert run(prefetch_entries(cache, scope, tmp_path / "r", max_bytes=10)).outcome == "unavailable"
    assert run(publish_entries(cache, scope, tmp_path, report((ENTRY, "built")))).outcome == "unavailable"


def test_report_entries_ignores_unknown_statuses_and_names():
    used, built = report_entries(
        {
            "components": [
                {"entry": ENTRY, "cache_status": "built"},
                {"entry": ENTRY, "cache_status": "built"},
                {"entry": OTHER, "cache_status": "hit"},
                {"entry": "../x/0123456789abcdef01234567", "cache_status": "built"},
                {"entry": None, "cache_status": "uncacheable"},
                "junk",
            ]
        }
    )
    assert used == [ENTRY, OTHER]
    assert built == [ENTRY]


def test_cache_telemetry_uses_bounded_labels_only(monkeypatch, tmp_path):
    recorded: list[tuple[str, dict]] = []
    monkeypatch.setattr(cc, "counter_add", lambda name, value, attrs: recorded.append((name, attrs)))
    monkeypatch.setattr(cc, "histogram_record", lambda name, value, attrs: recorded.append((name, attrs)))
    store = FakeObjectStore()
    cache = ComponentCacheStore(store, max_entry_bytes=1024)
    tenant, project = uuid4(), uuid4()
    scope = CacheScope.for_project(tenant, project)
    write_entry(tmp_path, ENTRY)

    run(publish_entries(cache, scope, tmp_path, report((ENTRY, "built"))))
    run(prefetch_entries(cache, scope, tmp_path / "r", max_bytes=10_000))
    cc.record_component_report(
        {
            "counts": {"hit": 3, "built": 1, "uncacheable": 1},
            "components": [{"build_seconds": 0.5, "load_seconds": 0.1, "part_number": "AAA-0000-00000"}],
        },
        "glb",
    )
    cc.record_compile_phases({"design": 1.0, "export": 2.0, "secret-phase": 3.0}, "glb")

    allowed = {
        "operation": {"prefetch", "publish", "manifest_read", "manifest_write", "open", "build", "load"},
        "outcome": {"ok", "miss", "unavailable", "integrity_failed"},
        "cache_status": {"hit", "built", "uncacheable", "uncached"},
        "export_format": {"glb"},
        "phase": cc.COMPILE_PHASES,
    }
    assert recorded
    for name, attrs in recorded:
        assert name.startswith("tertius.compile.")
        for key, value in attrs.items():
            assert value in allowed[key], (name, key, value)
            assert not re.search(r"[0-9a-f]{16}", str(value))
    assert not any(attrs.get("phase") == "secret-phase" for _, attrs in recorded)
