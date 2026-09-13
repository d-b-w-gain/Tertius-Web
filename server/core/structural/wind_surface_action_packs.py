from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence


WindSurfaceActionPackId = Literal[
    "as_nzs_1170_2_rectangular_enclosed_main_frame_v1"
]
SurfaceKind = Literal[
    "windward_wall",
    "leeward_wall",
    "roof",
]
OpeningSurface = Literal["windward", "leeward", "side", "roof"]


PACK_ID: WindSurfaceActionPackId = (
    "as_nzs_1170_2_rectangular_enclosed_main_frame_v1"
)
PACK_VERSION = "1.0.0"
STANDARD_REFERENCE = (
    "AS/NZS 1170.2:2011 Clauses 5.2-5.4 and Tables 5.1-5.5, with the "
    "AS/NZS 1170.2:2021 Table 5.1(B), Table 5.4 and Clause 5.4.3 changes "
    "identified by the project key-changes evidence"
)
BASE_STANDARD_SOURCE_SHA256 = (
    "9c8a0bc10ad50b5abdcf51af93ccb7350f225eee184bfd2880928856041379c8"
)
KEY_CHANGES_SOURCE_SHA256 = (
    "c866da386d04013d1dc9027765bd7268e99c4486b286ac54a69b45a2c940c6a4"
)


@dataclass(frozen=True)
class SurfaceCoefficientEnvelope:
    """A traceable main-frame net-pressure coefficient envelope.

    ``net_coefficient`` is signed in the normal pressure convention: positive
    acts into the surface and negative acts out of the surface.  The caller
    owns only the mechanical receiver geometry; all coefficient selection is
    contained in this pack.
    """

    net_coefficient: float
    external_coefficient: float
    internal_coefficient: float
    area_reduction_factor: float
    external_combination_factor: float
    internal_combination_factor: float
    local_pressure_factor: float
    status: Literal["verified"]
    provenance: str


def _interpolate(
    value: float,
    lower_x: float,
    upper_x: float,
    lower_y: float,
    upper_y: float,
) -> float:
    if upper_x <= lower_x:
        raise ValueError("interpolation points must increase")
    ratio = (value - lower_x) / (upper_x - lower_x)
    return lower_y + ratio * (upper_y - lower_y)


def _piecewise_linear(value: float, points: Sequence[tuple[float, float]]) -> float:
    if not points:
        raise ValueError("piecewise interpolation requires points")
    if value <= points[0][0]:
        return points[0][1]
    for (lower_x, lower_y), (upper_x, upper_y) in zip(points, points[1:]):
        if value <= upper_x:
            return _interpolate(value, lower_x, upper_x, lower_y, upper_y)
    return points[-1][1]


def area_reduction_factor(
    loaded_area_m2: float,
    *,
    surface: SurfaceKind,
    average_roof_height_m: float,
) -> float:
    """Return AS/NZS 1170.2:2021 Table 5.4 ``Ka``.

    The main-frame pack deliberately does not apply the optional ``Kc``
    reduction.  ``Kl`` is 1.0 because portal columns and rafters are not
    cladding, fixings, or the immediate cladding-support member.
    """

    if loaded_area_m2 <= 0:
        raise ValueError("loaded area must be positive")
    if average_roof_height_m <= 0 or average_roof_height_m >= 25:
        raise ValueError("the low-rise area table requires 0 < h < 25 m")
    if surface == "roof":
        points = ((10.0, 1.0), (25.0, 0.9), (100.0, 0.8))
    elif surface == "windward_wall":
        points = ((10.0, 1.0), (25.0, 0.95), (100.0, 0.9))
    elif surface == "leeward_wall":
        points = ((10.0, 1.0), (25.0, 1.0), (100.0, 0.95))
    else:  # pragma: no cover - protected by the Literal contract
        raise ValueError(f"unsupported surface {surface!r}")
    return _piecewise_linear(loaded_area_m2, points)


def transverse_leeward_wall_external_coefficient(roof_pitch_degrees: float) -> float:
    """Conservative Table 5.2(B) envelope for wind normal to a gable ridge."""

    if not 10.0 <= roof_pitch_degrees <= 25.0:
        raise ValueError(
            "the v1 main-frame pack supports gable roof pitches from 10 to 25 degrees"
        )
    return _piecewise_linear(
        roof_pitch_degrees,
        ((10.0, -0.3), (15.0, -0.3), (20.0, -0.4), (25.0, -0.5)),
    )


def longitudinal_leeward_wall_external_coefficient(
    *,
    building_depth_m: float,
    average_roof_height_m: float,
) -> float:
    """Table 5.2(B) leeward coefficient for wind parallel to the ridge."""

    if building_depth_m <= 0 or average_roof_height_m <= 0:
        raise ValueError("building depth and average roof height must be positive")
    depth_over_height = building_depth_m / average_roof_height_m
    return _piecewise_linear(
        depth_over_height,
        ((1.0, -0.5), (2.0, -0.3), (4.0, -0.2)),
    )


def transverse_roof_external_coefficients(
    *,
    roof_pitch_degrees: float,
    average_roof_height_m: float,
    building_depth_m: float,
) -> tuple[float, float]:
    """Return conservative upwind/downwind roof ``Cp,e`` from Tables 5.3(B/C)."""

    if not 10.0 <= roof_pitch_degrees <= 25.0:
        raise ValueError(
            "the v1 main-frame pack supports gable roof pitches from 10 to 25 degrees"
        )
    if average_roof_height_m <= 0 or building_depth_m <= 0:
        raise ValueError("average roof height and building depth must be positive")

    ratio = max(0.25, min(1.0, average_roof_height_m / building_depth_m))
    ratio_rows = (0.25, 0.5, 1.0)
    pitch_columns = (10.0, 15.0, 20.0, 25.0)
    upwind_grid = (
        (-0.7, -0.5, -0.3, -0.2),
        (-0.9, -0.7, -0.4, -0.3),
        (-1.3, -1.0, -0.7, -0.5),
    )
    downwind_grid = (
        (-0.3, -0.5, -0.6, -0.6),
        (-0.5, -0.5, -0.6, -0.6),
        (-0.7, -0.6, -0.6, -0.6),
    )

    def at_pitch(row: Sequence[float]) -> float:
        return _piecewise_linear(
            roof_pitch_degrees,
            tuple(zip(pitch_columns, row)),
        )

    upwind_by_ratio = tuple(at_pitch(row) for row in upwind_grid)
    downwind_by_ratio = tuple(at_pitch(row) for row in downwind_grid)
    upwind = _piecewise_linear(ratio, tuple(zip(ratio_rows, upwind_by_ratio)))
    downwind = _piecewise_linear(ratio, tuple(zip(ratio_rows, downwind_by_ratio)))
    return upwind, downwind


def longitudinal_roof_external_coefficient(
    *,
    distance_from_windward_edge_m: float,
    average_roof_height_m: float,
    building_depth_m: float,
) -> float:
    """Conservative Table 5.3(A) crosswind-slope coefficient by roof strip."""

    if distance_from_windward_edge_m < 0:
        raise ValueError("distance from the windward edge cannot be negative")
    if average_roof_height_m <= 0 or building_depth_m <= 0:
        raise ValueError("average roof height and building depth must be positive")
    distance_ratio = distance_from_windward_edge_m / average_roof_height_m
    height_depth_ratio = max(
        0.5, min(1.0, average_roof_height_m / building_depth_m)
    )
    if distance_ratio <= 0.5:
        return _interpolate(height_depth_ratio, 0.5, 1.0, -0.9, -1.3)
    if distance_ratio <= 1.0:
        return -0.9
    if distance_ratio <= 2.0:
        return -0.7
    if distance_ratio <= 3.0:
        return -0.3
    return -0.2


def internal_pressure_candidates(
    *,
    opening_capacity_verified: bool,
    openings_normally_open: bool,
    potential_opening_surfaces: Sequence[OpeningSurface],
    leeward_external_coefficient: float,
    roof_external_coefficient: float,
) -> tuple[float, ...]:
    """Return the coherent ``Cp,i`` candidates that the surface envelope bounds.

    Verified normally-closed doors/windows use the all-walls-permeable case.
    Otherwise each compiled potential opening is conservatively treated as a
    possible ratio >= 6 dominant opening.  That deliberately removes any need
    for a hand-maintained opening-area ratio in ``design.py``.
    """

    candidates = {-0.3, 0.0}
    if opening_capacity_verified and not openings_normally_open:
        return tuple(sorted(candidates))
    for surface in potential_opening_surfaces:
        if surface == "windward":
            candidates.add(0.7)
        elif surface == "leeward":
            candidates.add(leeward_external_coefficient)
        elif surface == "side":
            candidates.add(-0.65)
        elif surface == "roof":
            candidates.add(roof_external_coefficient)
        else:  # pragma: no cover - protected by the Literal contract
            raise ValueError(f"unsupported opening surface {surface!r}")
    return tuple(sorted(candidates))


def surface_coefficient_envelope(
    *,
    external_coefficient: float,
    internal_candidates: Sequence[float],
    loaded_area_m2: float,
    surface: SurfaceKind,
    average_roof_height_m: float,
    detail: str,
) -> SurfaceCoefficientEnvelope:
    """Select a conservative signed net coefficient for one main-frame receiver."""

    if not internal_candidates:
        raise ValueError("at least one internal-pressure candidate is required")
    ka = area_reduction_factor(
        loaded_area_m2,
        surface=surface,
        average_roof_height_m=average_roof_height_m,
    )
    # Kc is an optional peak non-simultaneity reduction. Keeping it at 1.0 is
    # conservative and avoids pretending a global frame effect is known while
    # loads are being projected one mechanical receiver at a time.
    kc_external = 1.0
    kc_internal = 1.0
    kl = 1.0
    net_candidates = [
        (external_coefficient * ka * kc_external * kl - cpi * kc_internal, cpi)
        for cpi in internal_candidates
    ]
    net, internal = max(net_candidates, key=lambda value: abs(value[0]))
    provenance = (
        f"Tertius wind surface pack {PACK_ID} v{PACK_VERSION}; {detail}; "
        f"Cnet=Cp,e*Ka*Kc,e*Kl-Cp,i*Kc,i="
        f"{external_coefficient:.6g}*{ka:.6g}*1*1-({internal:.6g})*1="
        f"{net:.6g}; Cp,i candidates={','.join(f'{value:.6g}' for value in internal_candidates)}; "
        "Kc reductions not taken; Kl=1 for the main portal frame; "
        f"{STANDARD_REFERENCE}; source SHA-256 "
        f"{BASE_STANDARD_SOURCE_SHA256} and {KEY_CHANGES_SOURCE_SHA256}"
    )
    return SurfaceCoefficientEnvelope(
        net_coefficient=net,
        external_coefficient=external_coefficient,
        internal_coefficient=internal,
        area_reduction_factor=ka,
        external_combination_factor=kc_external,
        internal_combination_factor=kc_internal,
        local_pressure_factor=kl,
        status="verified",
        provenance=provenance,
    )
