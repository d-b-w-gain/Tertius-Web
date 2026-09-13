from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
import json
from math import isfinite
from types import MappingProxyType
from typing import Any


JsonPrimitive = str | int | float | bool | None
FrozenJson = JsonPrimitive | tuple["FrozenJson", ...] | Mapping[str, "FrozenJson"]


def required_text(label: str, value: object) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{label} must not be empty")
    return text


def freeze_json(value: Any, *, label: str = "value") -> FrozenJson:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError(f"{label} must contain finite numbers")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, FrozenJson] = {}
        for key, item in value.items():
            text_key = required_text(f"{label} key", key)
            if text_key in frozen:
                raise ValueError(f"{label} contains duplicate key {text_key!r}")
            frozen[text_key] = freeze_json(item, label=f"{label}.{text_key}")
        return MappingProxyType(frozen)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(
            freeze_json(item, label=f"{label}[{index}]")
            for index, item in enumerate(value)
        )
    raise TypeError(f"{label} must be JSON-like, got {type(value).__name__}")


def thaw_json(value: FrozenJson | Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(item) for item in value]
    return value


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        thaw_json(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_digest(value: Any) -> str:
    return sha256(canonical_json_bytes(value)).hexdigest()
