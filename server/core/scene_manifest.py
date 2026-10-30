"""The ``tertius.scene.v1`` compile artifact (#314).

A scene manifest mirrors the monolithic GLB's node tree and names, for each
unit, the per-component GLB asset it comes from (see ``scene_export``). The
compile worker uploads the assets to the compile sidecar Object Store and
adds ``asset_refs`` to the manifest; the result consumer copies any asset
the tenant doesn't hold yet into ``scene_assets`` and stores the manifest
only once every asset it names is present.
"""

from __future__ import annotations

import re
from typing import Any

from core.object_store import ObjectRef

SCENE_SCHEMA_VERSION = "tertius.scene.v1"
MAX_SCENE_NODES = 100_000
MAX_SCENE_ASSETS = 5_000
ASSET_ID = re.compile(r"^c?[0-9a-f]{64}$")


def scene_assets(document: Any) -> dict[str, ObjectRef]:
    """Validate a scene manifest and return {asset id: transport ref}.
    Raises ValueError for anything malformed or unbounded."""
    if not isinstance(document, dict) or document.get("schema_version") != SCENE_SCHEMA_VERSION:
        raise ValueError("scene manifest has an unsupported schema version")
    used: set[str] = set()
    count = 0
    stack = [document.get("root")]
    while stack:
        node = stack.pop()
        count += 1
        if count > MAX_SCENE_NODES:
            raise ValueError("scene manifest has too many nodes")
        if not isinstance(node, dict) or not isinstance(node.get("name"), str):
            raise ValueError("scene manifest node is malformed")
        matrix = node.get("matrix")
        if not (
            isinstance(matrix, list)
            and len(matrix) == 16
            and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in matrix)
        ):
            raise ValueError("scene manifest node matrix is malformed")
        asset = node.get("asset")
        if asset is not None:
            if not isinstance(asset, str) or not ASSET_ID.match(asset):
                raise ValueError("scene manifest names a malformed asset")
            used.add(asset)
        children = node.get("children")
        if not isinstance(children, list):
            raise ValueError("scene manifest node children are malformed")
        stack.extend(children)
    if document.get("root", {}).get("asset") is None:
        raise ValueError("scene manifest root has no asset")
    if len(used) > MAX_SCENE_ASSETS or set(document.get("assets") or []) != used:
        raise ValueError("scene manifest asset list doesn't match its nodes")
    refs = document.get("asset_refs")
    if not isinstance(refs, dict) or set(refs) != used:
        raise ValueError("scene manifest doesn't reference every asset it uses")
    try:
        return {asset: ObjectRef.model_validate(ref) for asset, ref in refs.items()}
    except Exception as exc:
        raise ValueError("scene manifest asset reference is malformed") from exc
