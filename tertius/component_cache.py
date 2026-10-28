"""Incremental component compile inside the compile sandbox.

Designs opt in by decorating component builders with cadcache's ``@part``
(issue #312). The decorator is the explicit component registration from
``docs/superpowers/specs/2026-07-16-incremental-component-contract.md``:

- ``component_id`` is the builder's qualified name;
- ``inputs`` are the bound call arguments;
- ``dependencies`` are what cadcache collects itself: helper code reached in
  the project, plain-data globals read, and files opened while building;
- placement stays outside the part, so moving an instance never invalidates
  its geometry.

The pre-export key is cadcache's key plus the salt built here: contract and
artifact schema versions, the Tertius runtime digest, cadcache's version and
the export family with its tessellation. cadcache itself adds Python,
Build123D and OCP versions. A GLB compile meshes each newly built part at the
export tessellation before it is stored, so a later hit carries its mesh and
the GLB exporter skips re-meshing it (issue #313).

The sandbox only ever sees a local cache folder. The trusted compile worker
fills it from tenant-scoped storage before the run and publishes new entries
after it (``server/core/component_cache.py``).
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any

CONTRACT_SCHEMA_VERSION = 1
ARTIFACT_SCHEMA_VERSION = 1
SOURCE_MAP_VERSION = 1
REPORT_SCHEMA_VERSION = "tertius.components.v1"
REPORT_FILENAME = "tertius-components.json"
MAX_REPORT_COMPONENTS = 2000

# Python attributes Tertius designs carry as plain data on shapes. BREP drops
# Python attributes, so cadcache keeps these in each entry's sidecar. The
# registration tokens let a hit find the nodes its replayed registrations
# belong to (CompileSession.replay_registrations).
CACHED_SHAPE_ATTRIBUTES = (
    "tertius_bom",
    "tertius_component_token",
    "tertius_connection_token",
)

SESSION_SIDE_EFFECT = "component_replay_ineligible_session_side_effect"

# (linear, angular) deflection for each GLB/glTF quality; the compile sandbox
# exports with exactly these values, and they are part of the cache key.
GLTF_TESSELLATION: dict[str | None, tuple[float, float]] = {
    None: (10.0, 0.3),
    "sketch": (200.0, 0.8),
    "rough": (100.0, 0.6),
    "low": (50.0, 0.45),
    "medium": (30.0, 0.35),
    "normal": (10.0, 0.3),
    "high": (1.0, 0.3),
}
MESH_EXPORT_FORMATS = frozenset({"glb", "gltf"})


def gltf_tessellation(quality: str | None) -> tuple[float, float]:
    return GLTF_TESSELLATION.get(quality, GLTF_TESSELLATION[None])


def _runtime_digest() -> str:
    """Digest of the Tertius runtime package, so a runtime deploy that could
    change what a component builds invalidates its entries."""
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).resolve().parent.glob("*.py")):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def cache_salt(export_format: str, quality: str | None) -> dict[str, Any]:
    import cadcache

    tessellation = (
        list(gltf_tessellation(quality))
        if export_format.lower() in MESH_EXPORT_FORMATS
        else None
    )
    return {
        "contract_schema_version": CONTRACT_SCHEMA_VERSION,
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "source_map_version": SOURCE_MAP_VERSION,
        "tertius_runtime": _runtime_digest(),
        "cadcache": cadcache.__version__,
        "export": {
            "family": "mesh" if tessellation else "exact",
            "tessellation": tessellation,
        },
    }


def _mesher(linear: float, angular: float):
    import build123d as bd

    def mesh(shape: Any) -> None:
        # Mirror build123d's export_gltf, which meshes every node this way
        # and skips any node that already carries a fine enough mesh.
        for node in bd.PreOrderIter(shape):
            if node.wrapped is not None:
                node.mesh(linear, angular)

    return mesh


def _cadcache():
    """The cadcache module, or None when it isn't installed."""
    try:
        import cadcache
    except ImportError:
        return None
    return cadcache


def configure_component_cache(
    cache_dir: str | Path | None,
    project_dir: str | Path,
    export_format: str,
    quality: str | None,
) -> bool:
    """Point cadcache at the worker's cache folder for this compile. Without
    a folder, parts still run but nothing is stored (the pre-#312 path).
    Without cadcache installed, compiles run as before #312."""
    cadcache = _cadcache()
    if cadcache is None:
        return False
    cadcache.reset()
    if not cache_dir:
        cadcache.configure(enabled=False)
        return False
    tessellation = (
        gltf_tessellation(quality)
        if export_format.lower() in MESH_EXPORT_FORMATS
        else None
    )
    cadcache.configure(
        cache_dir=cache_dir,
        root=project_dir,
        enabled=True,
        salt=cache_salt(export_format, quality),
        before_save=_mesher(*tessellation) if tessellation else (lambda shape: None),
        attributes=CACHED_SHAPE_ATTRIBUTES,
        on_hit=_replay_registrations,
        rebind=_rebind_registrations,
        validate=_validate_registrations,
    )
    return True


def record_registration(effect: dict[str, Any]) -> None:
    """A managed component or connection registered while a cached part is
    building: record it with the part's cache entry so a hit can replay it.
    Outside a cached build (or without cadcache) it does nothing."""
    import sys

    cadcache = sys.modules.get("cadcache")
    record_effect = getattr(cadcache, "record_effect", None)
    if record_effect is not None:
        record_effect(effect)


def _registration_session():
    from .session import _CURRENT_SESSION

    return _CURRENT_SESSION.get()


def _replay_registrations(shape: Any, effects: list[dict[str, Any]]) -> None:
    session = _registration_session()
    if session is None:
        raise RuntimeError("a cached part with registrations loaded outside a compile session")
    session.replay_registrations(shape, effects)


def _rebind_registrations(built: Any, loaded: Any, effects: list[dict[str, Any]]) -> None:
    session = _registration_session()
    if session is not None:
        session.rebind_registrations(loaded)


def _validate_registrations(shape: Any, effects: list[dict[str, Any]]) -> str | None:
    session = _registration_session()
    if session is None:
        return SESSION_SIDE_EFFECT if effects else None
    return session.validate_cached_registrations(shape, effects)


_STATUS = {
    "built": "built",
    "cached": "hit",
    "uncacheable": "uncacheable",
    "uncached": "uncached",
}


def component_report(cache_enabled: bool) -> dict[str, Any]:
    """Bounded summary of this run's cadcache events: one entry per distinct
    component geometry, with its part number, cache status and the instance
    count. ``entry`` is the cache entry path relative to the cache folder."""
    cadcache = _cadcache()

    components: dict[tuple[str, str], dict[str, Any]] = {}
    counts: Counter[str] = Counter()
    for event in cadcache.events() if cadcache else ():
        if event.kind == "assembly":
            counts["assembly"] += 1
            continue
        status = _STATUS.get(event.kind, "uncached")
        counts[status] += 1
        identity = (event.name, event.key or f"call-{len(components)}")
        component = components.get(identity)
        if component is None:
            if len(components) >= MAX_REPORT_COMPONENTS:
                counts["truncated"] += 1
                continue
            entry = None
            if event.entry:
                path = Path(event.entry)
                entry = f"{path.parent.name}/{path.stem}"
            component = components[identity] = {
                "component": event.name,
                "part_number": event.part_number,
                "cache_status": status,
                "entry": entry,
                "build_seconds": 0.0,
                "load_seconds": 0.0,
                "instances": 0,
                "diagnostic": event.reason,
            }
        component["instances"] += 1
        if status == "built" or status == "uncacheable":
            component["build_seconds"] += event.seconds
            if status == "built":
                component["cache_status"] = "built"
        elif status == "hit":
            component["load_seconds"] += event.seconds
    for component in components.values():
        component["build_seconds"] = round(component["build_seconds"], 4)
        component["load_seconds"] = round(component["load_seconds"], 4)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "contract_schema_version": CONTRACT_SCHEMA_VERSION,
        "cache_enabled": cache_enabled,
        "counts": {
            key: counts.get(key, 0)
            for key in ("hit", "built", "uncacheable", "uncached", "assembly", "truncated")
        },
        "components": sorted(
            components.values(),
            key=lambda item: (item["component"], item["part_number"] or ""),
        ),
    }


def write_component_report(output_dir: Path, cache_enabled: bool) -> Path:
    path = Path(output_dir) / REPORT_FILENAME
    path.write_text(
        json.dumps(component_report(cache_enabled), sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return path


def component_names_by_part_number() -> dict[str, str]:
    cadcache = _cadcache()

    return {
        event.part_number: event.name
        for event in (cadcache.events() if cadcache else ())
        if event.part_number and event.kind != "assembly"
    }
