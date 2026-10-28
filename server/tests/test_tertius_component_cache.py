from __future__ import annotations

import cadcache
import pytest

from tertius import component_cache as tcc


def fake_events(monkeypatch, events):
    monkeypatch.setattr(cadcache, "events", lambda: list(events))


def event(name, kind, key="k" * 64, number="AAA-0000-00000", seconds=1.0, entry=None, reason=None):
    return cadcache.Event(name, kind, seconds, key, number, entry, reason)


def test_salt_carries_contract_runtime_and_tessellation():
    glb = tcc.cache_salt("glb", "rough")
    assert glb["contract_schema_version"] == tcc.CONTRACT_SCHEMA_VERSION
    assert glb["artifact_schema_version"] == tcc.ARTIFACT_SCHEMA_VERSION
    assert glb["cadcache"] == cadcache.__version__
    assert len(glb["tertius_runtime"]) == 64
    assert glb["export"] == {"family": "mesh", "tessellation": [100.0, 0.6]}
    assert tcc.cache_salt("gltf", "rough") == glb
    assert tcc.cache_salt("glb", "sketch") != glb
    assert tcc.cache_salt("step", None)["export"] == {"family": "exact", "tessellation": None}
    assert tcc.cache_salt("stl", "high") == tcc.cache_salt("step", None)


@pytest.mark.parametrize(
    "quality, expected",
    [(None, (10.0, 0.3)), ("normal", (10.0, 0.3)), ("high", (1.0, 0.3)), ("unknown", (10.0, 0.3))],
)
def test_gltf_tessellation_matches_previous_sandbox_defaults(quality, expected):
    assert tcc.gltf_tessellation(quality) == expected


def test_report_groups_calls_per_component_geometry(monkeypatch, tmp_path):
    fake_events(
        monkeypatch,
        [
            event("wall", "built", key="a" * 64, entry=str(tmp_path / "wall" / ("a" * 24 + ".brep"))),
            event("wall", "cached", key="a" * 64, seconds=0.25, entry=str(tmp_path / "wall" / ("a" * 24 + ".brep"))),
            event("wall", "cached", key="b" * 64, number="BBB-0000-00000", seconds=0.5,
                  entry=str(tmp_path / "wall" / ("b" * 24 + ".brep"))),
            event("panel", "uncacheable", reason=tcc.SESSION_SIDE_EFFECT),
            event("place", "assembly", key=None, number=None),
        ],
    )
    report = tcc.component_report(True)
    assert report["schema_version"] == tcc.REPORT_SCHEMA_VERSION
    assert report["counts"] == {
        "hit": 2, "built": 1, "uncacheable": 1, "uncached": 0, "assembly": 1, "truncated": 0,
    }
    by_entry = {item["entry"]: item for item in report["components"]}
    first = by_entry["wall/" + "a" * 24]
    assert (first["cache_status"], first["instances"], first["build_seconds"], first["load_seconds"]) == (
        "built", 2, 1.0, 0.25,
    )
    assert by_entry["wall/" + "b" * 24]["cache_status"] == "hit"
    panel = by_entry[None]
    assert (panel["cache_status"], panel["diagnostic"]) == ("uncacheable", tcc.SESSION_SIDE_EFFECT)


def test_report_is_bounded(monkeypatch):
    monkeypatch.setattr(tcc, "MAX_REPORT_COMPONENTS", 3)
    fake_events(monkeypatch, [event("p", "built", key=f"{i:064x}") for i in range(5)])
    report = tcc.component_report(True)
    assert len(report["components"]) == 3
    assert report["counts"]["truncated"] == 2


def test_registration_outside_a_compile_session_is_not_stored(tmp_path):
    tcc.configure_component_cache(tmp_path, tmp_path, "stl", None)
    try:
        import build123d as bd

        @cadcache.part
        def registering(w):
            tcc.record_registration({"kind": "component", "token": "t"})
            return bd.Box(w, 1, 1)

        cadcache.reset()
        registering(2)
        registering(2)
        events = cadcache.events()
        assert [e.kind for e in events] == ["uncacheable", "uncacheable"]
        assert events[0].reason == tcc.SESSION_SIDE_EFFECT
        # Outside a build, recording does nothing.
        tcc.record_registration({"kind": "component", "token": "t"})
    finally:
        settings = cadcache.core.settings
        settings.cache_dir = settings.root = settings.salt = None
        settings.before_save = settings.on_hit = settings.rebind = settings.validate = None
        settings.attributes = ()
        cadcache.reset()
