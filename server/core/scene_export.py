"""Scene manifests and per-component GLB assets (#314).

A monolithic GLB export re-meshes, patches and deduplicates the whole model
on every compile. With the component cache most of the model is unchanged,
so this exports one GLB asset per *unit* instead and a manifest that says how
to put them back together:

- a unit is the model root or a cached part (``@part``) in the model tree;
- a unit's asset holds its subtree minus the units nested in it, so each
  piece of geometry is in exactly one asset;
- a cached part's asset is named from its cache key, the tessellation and a
  digest of its names, colours and Tertius metadata, so an unchanged part
  reuses the asset stored by an earlier compile without exporting it;
- the manifest mirrors the node tree of the monolithic GLB (names, Tertius
  extras, local transforms), and records which asset each unit comes from.

Assets carry geometry, materials and names, but no source call ids: those
are numbered by execution order, so an unrelated edit renumbers them. They
live in the manifest. The sandbox copies this file into the project as
``tertius_scene_export.py``.
"""

import hashlib
import json
import shutil
from pathlib import Path

SCENE_SCHEMA_VERSION = "tertius.scene.v1"
# Bump to retire every stored asset (export or patch changes).
ASSET_FORMAT = 1
_GLTF_Y_UP = (-90.0, (1, 0, 0))


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


# The exporter writes metres for a model in millimetres.
_METRES_PER_UNIT = 0.001


def _matrix(bd, location):
    """Column-major 4x4 in glTF's metres, as glTF stores node matrices."""
    transform = location.wrapped.Transformation()
    rows = [
        [transform.Value(row, col) * (_METRES_PER_UNIT if col == 4 else 1.0) for col in range(1, 5)]
        for row in range(1, 4)
    ]
    rows.append([0.0, 0.0, 0.0, 1.0])
    return [rows[row][col] for col in range(4) for row in range(4)]


def _color(node):
    color = getattr(node, "color", None)
    return None if color is None else [round(float(c), 6) for c in tuple(color)]


def _shape_children(bd, node):
    return [child for child in (getattr(node, "children", None) or ()) if isinstance(child, bd.Shape)]


def _is_unit(node):
    return getattr(node, "_cadcache_key", None) is not None


def _asset_visual(visual_node):
    """The visual metadata an asset keeps: everything but source call ids."""
    return {key: value for key, value in visual_node.items() if key not in ("source_call_ids", "children")}


class _Unit:
    def __init__(self, node, visual_node, key):
        self.node = node
        self.visual_node = visual_node
        self.key = key
        self.asset = None


def _copy_for_export(bd, node, visual_node, units, at_root):
    """A copy of node's subtree for export, without the units nested in it,
    and the visual metadata to patch it with. Leaves share their OCCT shape
    with the model; nothing in the model is changed."""
    kids, visual_kids = [], []
    for child, visual_child in zip(_shape_children(bd, node), visual_node.get("children") or []):
        if _is_unit(child):
            units.append(_Unit(child, visual_child, child._cadcache_key))
            continue
        copied, copied_visual = _copy_for_export(bd, child, visual_child, units, False)
        kids.append(copied)
        visual_kids.append(copied_visual)
    if kids or _shape_children(bd, node):
        copy = bd.Compound(children=kids) if kids else bd.Compound([])
        copy.location = bd.Location() if at_root else node.location
    else:
        # A new OCCT shape sharing the leaf's geometry: casting node.wrapped
        # itself would share the shape object, and setting the copy's
        # location (or the exporter's Y-up turn) would move the model's node.
        from OCP.TopLoc import TopLoc_Location

        location = TopLoc_Location() if at_root else node.wrapped.Location()
        copy = bd.Compound.cast(node.wrapped.Located(location))
    copy.label = node.label
    if getattr(node, "color", None) is not None:
        copy.color = node.color
    visual = dict(_asset_visual(visual_node), children=visual_kids)
    return copy, visual


def _metadata_digest(bd, copy, visual):
    """Names, colours and metadata an asset is patched with."""
    def walk(shape, visual_node):
        return {
            "label": shape.label,
            "color": _color(shape),
            "visual": _asset_visual(visual_node),
            "children": [
                walk(child, visual_child)
                for child, visual_child in zip(_shape_children(bd, shape), visual_node.get("children") or [])
            ],
        }

    return _digest(walk(copy, visual))


def _manifest_node(bd, node, visual_node, unit_assets, at_root):
    location = node.location
    if at_root:
        # As the monolithic export does: model.location *= Y-up rotation.
        location = location * bd.Location((0, 0, 0), _GLTF_Y_UP[1], _GLTF_Y_UP[0])
    entry = {
        "name": node.label or "",
        "matrix": _matrix(bd, location),
        "visual": visual_node and {key: value for key, value in visual_node.items() if key != "children"},
        "children": [
            _manifest_node(bd, child, visual_child, unit_assets, False)
            for child, visual_child in zip(_shape_children(bd, node), visual_node.get("children") or [])
        ],
    }
    asset = unit_assets.get(id(node))
    if asset is not None:
        entry["asset"] = asset
    return entry


def export_scene(
    compound,
    out_dir,
    *,
    bd,
    export_patched_gltf,
    deflection,
    angular_deflection,
    visual_tree,
    model_geometry,
    visual_source_map,
    part_component_names,
    asset_cache_dir=None,
):
    """Write ``scene.json`` and ``assets/<id>.glb`` to out_dir. Assets found
    in asset_cache_dir are reused; new ones are written there too. Returns
    the manifest and counts of exported and reused assets."""
    out_dir = Path(out_dir)
    asset_dir = out_dir / "assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(asset_cache_dir) if asset_cache_dir else None
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
    tessellation = [deflection, angular_deflection]

    pending = [_Unit(compound, visual_tree, getattr(compound, "_cadcache_key", None))]
    unit_assets = {}
    exported = reused = 0
    while pending:
        unit = pending.pop()
        nested = []
        copy, visual = _copy_for_export(bd, unit.node, unit.visual_node, nested, True)
        pending.extend(nested)
        asset_id = None
        if unit.key is not None:
            asset_id = _digest(
                {
                    "format": ASSET_FORMAT,
                    "key": unit.key,
                    "tessellation": tessellation,
                    "metadata": _metadata_digest(bd, copy, visual),
                }
            )
            cached = cache_dir / f"{asset_id}.glb" if cache_dir else None
            if cached is not None and cached.is_file():
                shutil.copyfile(cached, asset_dir / f"{asset_id}.glb")
                unit_assets[id(unit.node)] = asset_id
                reused += 1
                continue
        scratch = asset_dir / "unit.glb"
        export_patched_gltf(
            copy,
            scratch,
            "glb",
            bd=bd,
            deflection=deflection,
            angular_deflection=angular_deflection,
            visual_tree=visual,
            model_geometry=None,
            visual_source_map={},
            part_component_names=part_component_names,
        )
        content = scratch.read_bytes()
        if asset_id is None:
            # Not a cached part (the model root): named by its content.
            asset_id = "c" + hashlib.sha256(content).hexdigest()
        scratch.replace(asset_dir / f"{asset_id}.glb")
        if unit.key is not None and cache_dir is not None:
            (cache_dir / f"{asset_id}.glb").write_bytes(content)
        unit_assets[id(unit.node)] = asset_id
        exported += 1

    manifest = {
        "schema_version": SCENE_SCHEMA_VERSION,
        "root": _manifest_node(bd, compound, visual_tree, unit_assets, True),
        "model_geometry": model_geometry,
        "source_map": visual_source_map if visual_source_map.get("source_calls") else None,
        "assets": sorted(set(unit_assets.values())),
    }
    (out_dir / "scene.json").write_text(json.dumps(manifest, separators=(",", ":")), encoding="utf-8")
    return manifest, {"exported": exported, "reused": reused}
