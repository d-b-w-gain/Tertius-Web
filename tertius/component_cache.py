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
    "tertius_refs",
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
        # New entries are written by flush_component_cache() after the
        # compile's outputs, so the result doesn't wait for them.
        defer_saves=True,
    )
    return True


def flush_component_cache() -> dict[str, Any]:
    """Write the entries this compile built; returns how many and how long."""
    from time import perf_counter

    cadcache = _cadcache()
    if cadcache is None or not hasattr(cadcache, "flush"):
        return {"written": 0, "seconds": 0.0}
    started = perf_counter()
    written = cadcache.flush()
    return {"written": written, "seconds": round(perf_counter() - started, 3)}


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


def _replay_registrations(shape: Any, effects: list[dict[str, Any]]) -> bool:
    from .refs import restore_refs

    session = _registration_session()
    if session is None:
        return False
    if not session.replay_registrations(shape, effects):
        return False
    restore_refs(shape)
    return True


def _rebind_registrations(built: Any, loaded: Any, effects: list[dict[str, Any]]) -> None:
    from .refs import restore_refs

    session = _registration_session()
    if session is not None:
        session.rebind_registrations(loaded)
        restore_refs(loaded)


LOST_ATTRIBUTES = "component_replay_ineligible_live_attribute"

# Attributes a replayed registration sets again, and provenance's own tags
# (set on the result after the part returns).
_RECREATED_ATTRIBUTES = frozenset(
    {
        "ports",
        "tertius_component_id",
        "tertius_product_key",
        "tertius_product_definition_digest",
        "tertius_connection_id",
        "tertius_connection_definition_digest",
        "tertius_source_call_ids",
    }
)


# build123d's own bookkeeping; the entry's sidecar keeps what matters of it
# (label, colour, material, joints) and topo_parent is rebuilt on load.
_BUILD123D_ATTRIBUTES = frozenset({"topo_parent", "joints", "label", "material", "for_construction"})


def _holds_shape(value: Any, depth: int = 0) -> bool:
    import build123d as bd

    if isinstance(value, bd.Shape):
        return True
    if depth >= 4:
        return False
    if isinstance(value, dict):
        return any(_holds_shape(item, depth + 1) for item in value.values())
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_holds_shape(item, depth + 1) for item in value)
    return False


def _lost_attribute(shape: Any) -> str | None:
    """The name of an attribute on the result that a cache hit would not
    give back, or None. The caller gets the copy read back from the entry,
    which keeps only CACHED_SHAPE_ATTRIBUTES and what replay sets again, so
    handles to other shapes (``portal.tertius_members``) would be lost."""
    import build123d as bd

    from .refs import reference_names

    for node in bd.PreOrderIter(shape):
        recreated = reference_names(node)
        for name, value in vars(node).items():
            if (
                name.startswith("_")
                or name in CACHED_SHAPE_ATTRIBUTES
                or name in _BUILD123D_ATTRIBUTES
                or name in recreated
            ):
                continue
            if name in _RECREATED_ATTRIBUTES and (
                name == "tertius_source_call_ids"
                or getattr(node, "tertius_component_token", None)
                or getattr(node, "tertius_connection_token", None)
            ):
                continue
            if name == "ports" or name.startswith("tertius_") or _holds_shape(value):
                return name
    return None


def _validate_registrations(shape: Any, effects: list[dict[str, Any]]) -> str | None:
    if _lost_attribute(shape) is not None:
        return LOST_ATTRIBUTES
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
