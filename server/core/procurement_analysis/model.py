from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass
from typing import Any, Callable

from typing import Any, Callable, Iterable, Iterator, Sequence


STANDARD_BOM_FIELDS = {
    "mark",
    "part_number",
    "product_key",
    "manufacturer",
    "standard",
    "size",
    "length",
    "length_mm",
    "width",
    "width_mm",
    "height",
    "height_mm",
    "thickness",
    "thickness_mm",
    "diameter",
    "diameter_mm",
    "grip_length",
    "grip_length_mm",
    "quantity",
    "unit",
    "role",
    "material",
    "colour",
    "color",
    "finish",
    "grade",
    "source_library",
    "bracket_type",
    "roof_pitch",
    "roof_pitch_deg",
    "angle",
    "angle_deg",
    "span",
    "span_mm",
    "model_length",
    "stock_length_mm",
    "stock_width_mm",
    "cut_length_mm",
    "cut_width_mm",
    "drawing_number",
}

PROCUREMENT_HELPER_FUNCTIONS = {
    "assembly_key",
    "assembly_label",
}

GENERATED_NAME_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"^$",
        r"^mesh$",
        r"^component$",
        r"^solid$",
        r"^=>\d+(?:_\d+)?$",
        r"^node[_\s-]?\d+$",
        r"^mesh[_\s-]?\d+$",
        r"^object[_\s-]?3?d?[_\s-]?\d+$",
        r"^shape[_\s-]?\d+$",
        r"^face[_\s-]?\d+$",
        r"^edge[_\s-]?\d+$",
        r"^compound[_\s-]?\d+$",
        r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$",
        r"^=>\[[0-9:_\-\s]+\]$",
    ]
]


@dataclass(frozen=True)
class FunctionSignature:
    name: str
    params: list[str]
    defaults: dict[str, ast.AST]
    filename: str
    line: int
    return_annotation: str | None


@dataclass(frozen=True)
class ConstantValue:
    name: str
    value: Any
    filename: str
    line: int
    resolution: str = "literal_assignment"


@dataclass(frozen=True)
class StaticObject:
    attrs: dict[str, Any]


@dataclass(frozen=True)
class StaticResolution:
    value: Any
    resolution: str
    dependencies: tuple[str, ...] = ()


SAFE_BUILTIN_NUMERIC_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
    "sum": sum,
    "ceil": math.ceil,
    "floor": math.floor,
}

SAFE_MATH_NUMERIC_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "radians": math.radians,
    "degrees": math.degrees,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "atan": math.atan,
    "ceil": math.ceil,
    "floor": math.floor,
    "sqrt": math.sqrt,
}


def positive_number(value: Any) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)) and math.isfinite(value) and value > 0:
        return value
    return None
