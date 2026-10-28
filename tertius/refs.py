"""References from one shape to managed components, that survive caching.

Builders often hand their caller handles to components they registered:
``portal.tertius_members["left_column"]``. A live handle can't be stored in a
cached part's entry, so a cache hit would lose it. ``refer`` stores the
handle as the components' marks instead (plain data, kept with the entry as
``tertius_refs``) and sets the attribute to a read-only view that looks each
mark up in the current compile session when it is read. Reads work the same
whether the part was just built or loaded from the cache.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from typing import Any

import build123d as bd

REFS_ATTRIBUTE = "tertius_refs"


def _encode(value: Any, label: str) -> Any:
    if isinstance(value, bd.Shape):
        token = getattr(value, "tertius_component_token", None)
        mark = getattr(value, "tertius_component_id", None)
        if token is None or mark is None:
            raise TypeError(f"{label} refers to a shape that isn't a managed component")
        from .session import current_session

        registration = current_session()._components_by_token.get(str(token))
        if registration is None or registration.mark is None:
            # Without a mark a component's id depends on registration order,
            # so a cached reference to it could point at the wrong one.
            raise ValueError(f"{label} refers to component {mark!r}, which has no mark")
        return {"$component": registration.mark}
    if isinstance(value, Mapping):
        return {"$items": [[json.loads(json.dumps(key)), _encode(item, label)] for key, item in value.items()]}
    if isinstance(value, (list, tuple)):
        return {"$tuple" if isinstance(value, tuple) else "$list": [_encode(item, label) for item in value]}
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError(f"{label} holds {type(value).__name__}; refer to components, or plain data")


def _decode(data: Any) -> Any:
    if isinstance(data, dict):
        if "$component" in data:
            return _ComponentRef(data["$component"])
        if "$items" in data:
            return ComponentRefs(data)
        if "$tuple" in data:
            return tuple(_resolve(_decode(item)) for item in data["$tuple"])
        if "$list" in data:
            return [_resolve(_decode(item)) for item in data["$list"]]
    return data


class _ComponentRef:
    __slots__ = ("mark",)

    def __init__(self, mark: str) -> None:
        self.mark = mark


def _resolve(value: Any) -> Any:
    if isinstance(value, _ComponentRef):
        from .session import current_session

        return current_session().component(value.mark)
    return value


class ComponentRefs(Mapping[Any, Any]):
    """A read-only mapping whose values are looked up in the compile session
    on access. Keys may be any JSON-able value (tuples compare as lists)."""

    def __init__(self, data: Mapping[str, Any]) -> None:
        self._items = {json.dumps(key): (key, item) for key, item in data["$items"]}

    @staticmethod
    def _key(key: Any) -> str:
        return json.dumps(json.loads(json.dumps(key)))

    def __getitem__(self, key: Any) -> Any:
        return _resolve(_decode(self._items[self._key(key)][1]))

    def __iter__(self) -> Iterator[Any]:
        for key, _ in self._items.values():
            yield tuple(key) if isinstance(key, list) else key

    def __len__(self) -> int:
        return len(self._items)

    def __repr__(self) -> str:
        return f"ComponentRefs({[key for key, _ in self._items.values()]!r})"


def refer(shape: bd.Shape, name: str, value: Any) -> None:
    """Set ``shape.<name>`` to ``value``, a component or a dict, list or
    tuple of components (and plain data), stored by mark so the reference
    survives the shape being cached."""
    if not name.isidentifier() or name.startswith("_"):
        raise ValueError(f"reference name {name!r} must be a public attribute name")
    refs = dict(getattr(shape, REFS_ATTRIBUTE, None) or {})
    refs[name] = _encode(value, f"{name} reference")
    setattr(shape, REFS_ATTRIBUTE, refs)
    setattr(shape, name, _resolve(_decode(refs[name])))
    from .component_cache import record_registration

    # Makes a cache hit call back, so restore_refs runs on the loaded copy.
    record_registration({"kind": "refs"})


def restore_refs(shape: bd.Shape) -> None:
    """Recreate the reference attributes of every node of a shape loaded
    from a cache entry (they are stored as ``tertius_refs``)."""
    for node in bd.PreOrderIter(shape):
        refs = getattr(node, REFS_ATTRIBUTE, None)
        if refs:
            for name, data in refs.items():
                setattr(node, name, _resolve(_decode(data)))


def reference_names(node: bd.Shape) -> frozenset[str]:
    """The attributes of a node that refer set (restore_refs recreates them)."""
    return frozenset(getattr(node, REFS_ATTRIBUTE, None) or ())
