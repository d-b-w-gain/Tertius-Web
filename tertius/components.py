from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt
from typing import Any, Iterable, Mapping

from ._build123d_compat import install_build123d_compatibility

install_build123d_compatibility()

import build123d as bd  # noqa: E402 - OCP compatibility must be installed first.

from ._canonical import freeze_json, required_text  # noqa: E402
from .products import ProductDefinition  # noqa: E402


def _vector3(label: str, value: Iterable[float]) -> tuple[float, float, float]:
    values = tuple(float(item) for item in value)
    if len(values) != 3 or not all(isfinite(item) for item in values):
        raise ValueError(f"{label} requires three finite coordinates")
    return values


def _unit_vector3(
    label: str,
    value: Iterable[float],
) -> tuple[float, float, float]:
    values = _vector3(label, value)
    length = sqrt(sum(item * item for item in values))
    if length == 0.0:
        raise ValueError(f"{label} must not be zero")
    return (
        values[0] / length,
        values[1] / length,
        values[2] / length,
    )


def _default_x_direction(
    direction: tuple[float, float, float],
) -> tuple[float, float, float]:
    reference = (1.0, 0.0, 0.0) if abs(direction[0]) < 0.9 else (0.0, 1.0, 0.0)
    projection = sum(direction[index] * reference[index] for index in range(3))
    return _unit_vector3(
        "port x direction",
        tuple(
            reference[index] - projection * direction[index] for index in range(3)
        ),
    )


@dataclass(frozen=True)
class PortPlacement:
    point_mm: tuple[float, float, float]
    direction: tuple[float, float, float]
    x_direction: tuple[float, float, float]
    compatible_families: tuple[str, ...] = ()
    engagement_length_mm: float = 0.0

    def __init__(
        self,
        point_mm: Iterable[float],
        direction: Iterable[float],
        compatible_families: Iterable[str] = (),
        *,
        x_direction: Iterable[float] | None = None,
        engagement_length_mm: float = 0.0,
    ) -> None:
        object.__setattr__(self, "point_mm", _vector3("port point", point_mm))
        direction_values = _unit_vector3("port direction", direction)
        object.__setattr__(self, "direction", direction_values)
        x_values = (
            _default_x_direction(direction_values)
            if x_direction is None
            else _unit_vector3("port x direction", x_direction)
        )
        if abs(sum(direction_values[index] * x_values[index] for index in range(3))) > 1e-6:
            raise ValueError("port x direction must be perpendicular to port direction")
        object.__setattr__(self, "x_direction", x_values)
        object.__setattr__(
            self,
            "compatible_families",
            tuple(
                required_text("connection family", item) for item in compatible_families
            ),
        )
        engagement = float(engagement_length_mm)
        if not isfinite(engagement) or engagement < 0.0:
            raise ValueError("port engagement length must be finite and non-negative")
        object.__setattr__(self, "engagement_length_mm", engagement)


@dataclass(frozen=True)
class ComponentPort:
    component_token: str
    name: str
    point_mm: tuple[float, float, float]
    direction: tuple[float, float, float]
    x_direction: tuple[float, float, float]
    compatible_families: tuple[str, ...]
    engagement_length_mm: float


class PortSet:
    def __init__(self, ports: Mapping[str, ComponentPort]) -> None:
        self._ports = dict(ports)

    def __getattr__(self, name: str) -> ComponentPort:
        try:
            return self._ports[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __getitem__(self, name: str) -> ComponentPort:
        return self._ports[name]

    def __iter__(self):
        return iter(self._ports)

    def items(self):
        return self._ports.items()


@dataclass(frozen=True)
class ComponentRegistration:
    token: str
    instance_id: str
    shape: bd.Shape
    product: ProductDefinition
    mark: str | None
    role: str | None
    fabrication: Any
    ports: Mapping[str, ComponentPort]


def managed_component(
    shape: bd.Shape,
    *,
    product: ProductDefinition,
    mark: str | None = None,
    role: str | None = None,
    fabrication: Mapping[str, Any] | None = None,
    ports: Mapping[str, PortPlacement] | None = None,
) -> bd.Shape:
    """Register one installed mechanical component and return its CAD shape."""

    from .session import current_session

    if not isinstance(shape, bd.Shape):
        raise TypeError("managed_component requires a Build123D Shape")
    if not isinstance(product, ProductDefinition):
        raise TypeError("managed_component requires a ProductDefinition")
    return current_session().register_component(
        shape,
        product=product,
        mark=mark,
        role=role,
        fabrication=freeze_json(fabrication or {}, label="component fabrication"),
        ports=ports or {},
    )
