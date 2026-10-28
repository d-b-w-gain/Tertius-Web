"""Tenant-scoped persistent store for incremental component compile (#313).

Compile jobs are short-lived KEDA pods whose only writable disk is an
``emptyDir``, so cadcache's local folder would start empty on every compile.
The trusted compile worker therefore keeps entries in a NATS JetStream Object
Store bucket and mediates every read and write:

1. before the sandbox runs, it restores the entries the project's previous
   successful compile used into a fresh local folder;
2. the sandbox (unrestricted ``design.py``) sees only that folder through
   ``CADCACHE_DIR``-style configuration, never the store or other tenants;
3. after a successful compile, it uploads the entries the run built and
   records the project's entry list for the next compile.

Object names are derived on the worker from hashed tenant and project IDs, so
an identical cache key in two tenants resolves to two different objects.
Entries are immutable and content-verified by the Object Store; any missing,
oversized or corrupt object is a cache miss, never a failed compile.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from time import perf_counter
from typing import Any, TypeGuard

from core.object_store import (
    ObjectIntegrityError,
    ObjectStoreUnavailableError,
    _BoundedBytesIO,
    _is_integrity_error,
    _is_not_found,
)
from core.telemetry import counter_add, histogram_record

logger = logging.getLogger(__name__)

COMPONENT_CACHE_BUCKET = "TERTIUS_COMPONENT_CACHE"
MANIFEST_SCHEMA_VERSION = "tertius.component-cache-manifest.v1"
MAX_MANIFEST_ENTRIES = 4000
MAX_MANIFEST_BYTES = 1024 * 1024
ENTRY_SUFFIXES = ("brep", "json")
_ENTRY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}/[0-9a-f]{24}$")
_FETCH_CONCURRENCY = 8
STORE_OPERATIONS = frozenset({"manifest_read", "manifest_write", "prefetch", "publish"})


def valid_entry_name(entry: Any) -> TypeGuard[str]:
    return isinstance(entry, str) and bool(_ENTRY_RE.match(entry)) and ".." not in entry


@dataclass(frozen=True)
class CacheScope:
    """Where one project's entries live. Raw tenant and project IDs never
    appear in object names, logs or metrics."""

    tenant: str
    project: str

    @classmethod
    def for_project(cls, tenant_id: object, project_id: object) -> "CacheScope":
        tenant = hashlib.sha256(
            f"tertius.component-cache.tenant:{tenant_id}".encode("utf-8")
        ).hexdigest()[:32]
        project = hashlib.sha256(
            f"tertius.component-cache.project:{tenant}:{project_id}".encode("utf-8")
        ).hexdigest()[:32]
        return cls(tenant=tenant, project=project)

    def entry_object(self, entry: str, suffix: str) -> str:
        if not valid_entry_name(entry) or suffix not in ENTRY_SUFFIXES:
            raise ValueError("invalid component cache entry name")
        return f"t/{self.tenant}/e/{entry}.{suffix}"

    def manifest_object(self) -> str:
        return f"t/{self.tenant}/p/{self.project}/manifest.json"


@dataclass
class StoreResult:
    entries: int = 0
    bytes: int = 0
    skipped: int = 0
    failed: int = 0
    outcome: str = "ok"
    seconds: float = 0.0
    manifest_entries: list[str] = field(default_factory=list)


def _object_digest(content: bytes) -> str:
    return "SHA-256=" + base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode("ascii")


class ComponentCacheStore:
    def __init__(self, store: Any, *, max_entry_bytes: int):
        if isinstance(max_entry_bytes, bool) or not isinstance(max_entry_bytes, int):
            raise TypeError("max_entry_bytes must be an integer")
        if max_entry_bytes <= 0:
            raise ValueError("max_entry_bytes must be positive")
        self.store = store
        self.max_entry_bytes = max_entry_bytes

    async def _info(self, name: str) -> Any | None:
        try:
            info = await self.store.get_info(name)
        except Exception as exc:
            if _is_not_found(exc):
                return None
            if _is_integrity_error(exc):
                raise ObjectIntegrityError("object metadata integrity check failed") from exc
            raise ObjectStoreUnavailableError("object store operation failed") from exc
        if getattr(info, "deleted", False):
            return None
        try:
            if info.is_link():
                raise ObjectIntegrityError("object metadata integrity check failed")
        except AttributeError as exc:
            raise ObjectIntegrityError("object metadata integrity check failed") from exc
        if getattr(info, "name", None) != name:
            raise ObjectIntegrityError("object metadata integrity check failed")
        return info

    async def read(self, name: str, max_bytes: int | None = None) -> bytes | None:
        """The object's content, or None when it is absent. Raises
        ObjectIntegrityError for a corrupt or oversized object and
        ObjectStoreUnavailableError when the store can't be reached."""
        limit = max_bytes or self.max_entry_bytes
        info = await self._info(name)
        if info is None:
            return None
        size = getattr(info, "size", None)
        if not isinstance(size, int) or size <= 0 or size > limit:
            raise ObjectIntegrityError("object is empty or too large")
        output = _BoundedBytesIO(limit)
        try:
            await self.store.get(name, writeinto=output)
        except ObjectIntegrityError:
            raise
        except Exception as exc:
            if _is_not_found(exc):
                return None
            if _is_integrity_error(exc):
                raise ObjectIntegrityError("object integrity check failed") from exc
            raise ObjectStoreUnavailableError("object store operation failed") from exc
        content = output.getvalue()
        if len(content) != size:
            raise ObjectIntegrityError("object integrity check failed")
        return content

    async def write(
        self,
        name: str,
        content: bytes,
        *,
        replace_existing: bool = False,
        max_bytes: int | None = None,
    ) -> bool:
        """Store an object; an identical existing object is left alone, so
        retries and duplicate deliveries are idempotent. Returns True when
        something was written."""
        if not isinstance(content, bytes) or not content:
            raise ObjectIntegrityError("object is empty")
        if len(content) > (max_bytes or self.max_entry_bytes):
            raise ObjectIntegrityError("object is too large")
        try:
            info = await self._info(name)
        except ObjectIntegrityError:
            info = None
        if info is not None and getattr(info, "digest", None) == _object_digest(content):
            return False
        if info is not None and not replace_existing:
            # Entries are immutable: a different object under the same
            # content-derived name is left for verification to reject.
            return False
        try:
            await self.store.put(name, content)
        except Exception as exc:
            raise ObjectStoreUnavailableError("object store operation failed") from exc
        return True


def _record_store(operation: str, result: StoreResult) -> None:
    counter_add(
        "tertius.compile.component_cache.store.operations",
        1,
        {"operation": operation, "outcome": result.outcome},
    )
    histogram_record(
        "tertius.compile.component_cache.store.duration",
        result.seconds,
        {"operation": operation, "outcome": result.outcome},
    )
    if result.bytes:
        histogram_record(
            "tertius.compile.component_cache.store.bytes",
            result.bytes,
            {"operation": operation},
        )


def _parse_manifest(content: bytes) -> list[str]:
    document = json.loads(content)
    if not isinstance(document, dict) or document.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError("unsupported component cache manifest")
    entries = document.get("entries")
    if not isinstance(entries, list):
        raise ValueError("component cache manifest has no entry list")
    return [entry for entry in entries[:MAX_MANIFEST_ENTRIES] if valid_entry_name(entry)]


def _local_paths(cache_dir: Path, entry: str) -> tuple[Path, Path]:
    folder, key = entry.split("/", 1)
    base = Path(cache_dir).resolve()
    brep = (base / folder / f"{key}.brep").resolve()
    sidecar = (base / folder / f"{key}.json").resolve()
    if base not in brep.parents or base not in sidecar.parents:
        raise ValueError("component cache entry escapes the cache folder")
    return brep, sidecar


async def prefetch_entries(
    cache: ComponentCacheStore,
    scope: CacheScope,
    cache_dir: Path,
    *,
    max_bytes: int,
) -> StoreResult:
    """Restore the entries the project's last successful compile used. Only
    whole entries are restored (BREP first, sidecar last, as cadcache writes
    them); anything missing or corrupt is left out and becomes a miss."""
    started = perf_counter()
    result = StoreResult()
    entries: list[str] = []
    try:
        manifest = await cache.read(scope.manifest_object(), MAX_MANIFEST_BYTES)
        if manifest is None:
            result.outcome = "miss"
        else:
            entries = _parse_manifest(manifest)
    except ObjectStoreUnavailableError:
        result.outcome = "unavailable"
        result.seconds = perf_counter() - started
        _record_store("prefetch", result)
        return result
    except (ObjectIntegrityError, ValueError):
        result.outcome = "integrity_failed"

    semaphore = asyncio.Semaphore(_FETCH_CONCURRENCY)
    budget = {"remaining": max_bytes}

    async def fetch(entry: str) -> None:
        async with semaphore:
            if budget["remaining"] <= 0:
                result.skipped += 1
                return
            try:
                brep = await cache.read(scope.entry_object(entry, "brep"))
                sidecar = await cache.read(scope.entry_object(entry, "json"))
            except ObjectIntegrityError:
                result.failed += 1
                return
            if brep is None or sidecar is None:
                result.skipped += 1
                return
            size = len(brep) + len(sidecar)
            if size > budget["remaining"]:
                result.skipped += 1
                return
            budget["remaining"] -= size
            brep_path, sidecar_path = _local_paths(cache_dir, entry)
            brep_path.parent.mkdir(parents=True, exist_ok=True)
            brep_path.write_bytes(brep)
            sidecar_path.write_bytes(sidecar)
            result.entries += 1
            result.bytes += size

    try:
        await asyncio.gather(*(fetch(entry) for entry in entries))
    except ObjectStoreUnavailableError:
        result.outcome = "unavailable"
    if result.failed and result.outcome == "ok":
        result.outcome = "integrity_failed"
    result.seconds = perf_counter() - started
    _record_store("prefetch", result)
    return result


def report_entries(report: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(entries the run used, entries it built) from a component report."""
    used: list[str] = []
    built: list[str] = []
    for component in report.get("components") or []:
        if not isinstance(component, dict):
            continue
        entry = component.get("entry")
        status = component.get("cache_status")
        if not valid_entry_name(entry) or status not in {"built", "hit"}:
            continue
        if entry not in used:
            used.append(entry)
        if status == "built" and entry not in built:
            built.append(entry)
    return used[:MAX_MANIFEST_ENTRIES], built


async def publish_entries(
    cache: ComponentCacheStore,
    scope: CacheScope,
    cache_dir: Path,
    report: dict[str, Any],
) -> StoreResult:
    """Upload the entries this run built, then record the entries it used as
    the project's manifest. The manifest only names entries known to be in
    the store, so the next prefetch never restores half an entry."""
    started = perf_counter()
    result = StoreResult()
    used, built = report_entries(report)
    failed: set[str] = set()
    try:
        for entry in built:
            try:
                brep_path, sidecar_path = _local_paths(cache_dir, entry)
                brep = brep_path.read_bytes()
                sidecar = sidecar_path.read_bytes()
                json.loads(sidecar)
                wrote = await cache.write(scope.entry_object(entry, "brep"), brep)
                wrote = await cache.write(scope.entry_object(entry, "json"), sidecar) or wrote
            except (OSError, ValueError, ObjectIntegrityError):
                failed.add(entry)
                result.failed += 1
                continue
            if wrote:
                result.entries += 1
                result.bytes += len(brep) + len(sidecar)
            else:
                result.skipped += 1
        manifest_entries = [entry for entry in used if entry not in failed]
        manifest = json.dumps(
            {"schema_version": MANIFEST_SCHEMA_VERSION, "entries": manifest_entries},
            separators=(",", ":"),
        ).encode("utf-8")
        await cache.write(
            scope.manifest_object(),
            manifest,
            replace_existing=True,
            max_bytes=MAX_MANIFEST_BYTES,
        )
        result.manifest_entries = manifest_entries
    except ObjectStoreUnavailableError:
        result.outcome = "unavailable"
    except ObjectIntegrityError:
        result.outcome = "integrity_failed"
    if result.failed and result.outcome == "ok":
        result.outcome = "integrity_failed"
    result.seconds = perf_counter() - started
    _record_store("publish", result)
    return result


def record_component_report(report: dict[str, Any], export_format: str) -> None:
    counts = report.get("counts") or {}
    for status in ("hit", "built", "uncacheable", "uncached"):
        value = counts.get(status)
        if isinstance(value, int) and value > 0:
            counter_add(
                "tertius.compile.component_cache.components",
                value,
                {"cache_status": status, "export_format": export_format},
            )
    seconds: dict[str, float] = {"build": 0.0, "load": 0.0}
    for component in report.get("components") or []:
        if not isinstance(component, dict):
            continue
        for kind in seconds:
            value = component.get(f"{kind}_seconds")
            if isinstance(value, (int, float)) and value > 0:
                seconds[kind] += float(value)
    for kind, value in seconds.items():
        histogram_record(
            "tertius.compile.component_cache.component.duration",
            value,
            {"operation": kind, "export_format": export_format},
        )


COMPILE_PHASES = frozenset(
    {"startup", "design", "metadata", "export", "patch", "dedup", "finish"}
)


def record_compile_phases(phases: dict[str, Any], export_format: str) -> None:
    for phase, value in phases.items():
        if phase in COMPILE_PHASES and isinstance(value, (int, float)) and value >= 0:
            histogram_record(
                "tertius.compile.phase.duration",
                float(value),
                {"phase": phase, "export_format": export_format},
            )


async def open_component_cache_store(jetstream: Any, settings: Any) -> ComponentCacheStore:
    from nats.js.api import ObjectStoreConfig, StorageType
    from nats.js.errors import BucketNotFoundError, NotFoundError

    bucket = COMPONENT_CACHE_BUCKET
    created = False
    try:
        store = await jetstream.object_store(bucket)
    except (BucketNotFoundError, NotFoundError):
        try:
            store = await jetstream.create_object_store(
                bucket=bucket,
                config=ObjectStoreConfig(
                    bucket=bucket,
                    description="Tenant-scoped incremental compile component cache",
                    ttl=settings.component_cache_ttl_seconds,
                    max_bytes=settings.component_cache_max_bytes,
                    storage=StorageType.FILE,
                ),
            )
            created = True
        except Exception as create_exc:
            try:
                store = await jetstream.object_store(bucket)
            except Exception:
                raise ObjectStoreUnavailableError("object store operation failed") from create_exc
    except Exception as exc:
        raise ObjectStoreUnavailableError("object store operation failed") from exc

    if not created:
        try:
            status = await store.status()
            current = status.stream_info.config
            expected_max_age = float(settings.component_cache_ttl_seconds)
            expected_max_bytes = settings.component_cache_max_bytes
            if current.max_age != expected_max_age or current.max_bytes != expected_max_bytes:
                await jetstream.update_stream(
                    config=replace(current, max_age=expected_max_age, max_bytes=expected_max_bytes)
                )
        except Exception as exc:
            raise ObjectStoreUnavailableError("object store operation failed") from exc
    return ComponentCacheStore(store, max_entry_bytes=settings.component_cache_max_entry_bytes)


def load_json_file(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None

