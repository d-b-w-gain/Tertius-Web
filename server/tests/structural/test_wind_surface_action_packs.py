from __future__ import annotations

import pytest

from core.structural.wind_surface_action_packs import (
    PACK_ID,
    area_reduction_factor,
    internal_pressure_candidates,
    longitudinal_leeward_wall_external_coefficient,
    longitudinal_roof_external_coefficient,
    surface_coefficient_envelope,
    transverse_leeward_wall_external_coefficient,
    transverse_roof_external_coefficients,
)


def test_area_reduction_factor_interpolates_2021_table_5_4() -> None:
    assert area_reduction_factor(
        10, surface="roof", average_roof_height_m=3
    ) == pytest.approx(1.0)
    assert area_reduction_factor(
        25, surface="windward_wall", average_roof_height_m=3
    ) == pytest.approx(0.95)
    assert area_reduction_factor(
        62.5, surface="leeward_wall", average_roof_height_m=3
    ) == pytest.approx(0.975)
    assert area_reduction_factor(
        200, surface="roof", average_roof_height_m=3
    ) == pytest.approx(0.8)


def test_transverse_gable_coefficients_interpolate_geometry() -> None:
    assert transverse_leeward_wall_external_coefficient(15) == pytest.approx(-0.3)
    assert transverse_leeward_wall_external_coefficient(22.5) == pytest.approx(-0.45)
    upwind, downwind = transverse_roof_external_coefficients(
        roof_pitch_degrees=20,
        average_roof_height_m=2.7,
        building_depth_m=3.0,
    )
    assert upwind == pytest.approx(-0.64)
    assert downwind == pytest.approx(-0.6)


def test_longitudinal_coefficients_follow_depth_and_roof_strip() -> None:
    assert longitudinal_leeward_wall_external_coefficient(
        building_depth_m=5.4,
        average_roof_height_m=2.7,
    ) == pytest.approx(-0.3)
    assert longitudinal_roof_external_coefficient(
        distance_from_windward_edge_m=0.2,
        average_roof_height_m=2.7,
        building_depth_m=5.0,
    ) == pytest.approx(-0.932)
    assert longitudinal_roof_external_coefficient(
        distance_from_windward_edge_m=9.0,
        average_roof_height_m=2.7,
        building_depth_m=5.0,
    ) == pytest.approx(-0.2)


def test_unverified_gable_openings_become_dominant_opening_candidates() -> None:
    transverse = internal_pressure_candidates(
        opening_capacity_verified=False,
        openings_normally_open=False,
        potential_opening_surfaces=("side",),
        leeward_external_coefficient=-0.4,
        roof_external_coefficient=-0.8,
    )
    assert transverse == (-0.65, -0.3, 0.0)
    longitudinal = internal_pressure_candidates(
        opening_capacity_verified=False,
        openings_normally_open=False,
        potential_opening_surfaces=("windward", "leeward"),
        leeward_external_coefficient=-0.3,
        roof_external_coefficient=-0.9,
    )
    assert longitudinal == (-0.3, 0.0, 0.7)


def test_verified_closed_openings_use_permeability_candidates() -> None:
    assert internal_pressure_candidates(
        opening_capacity_verified=True,
        openings_normally_open=False,
        potential_opening_surfaces=("windward", "roof"),
        leeward_external_coefficient=-0.4,
        roof_external_coefficient=-0.9,
    ) == (-0.3, 0.0)


def test_surface_envelope_records_formula_and_source_evidence() -> None:
    coefficient = surface_coefficient_envelope(
        external_coefficient=0.7,
        internal_candidates=(-0.65, -0.3, 0.0),
        loaded_area_m2=5.0,
        surface="windward_wall",
        average_roof_height_m=2.7,
        detail="test windward wall",
    )
    assert coefficient.net_coefficient == pytest.approx(1.35)
    assert coefficient.internal_coefficient == pytest.approx(-0.65)
    assert coefficient.status == "verified"
    assert PACK_ID in coefficient.provenance
    assert "Cnet=Cp,e*Ka*Kc,e*Kl-Cp,i*Kc,i" in coefficient.provenance
    assert "source SHA-256" in coefficient.provenance


def test_surface_pack_fails_closed_outside_supported_pitch() -> None:
    with pytest.raises(ValueError, match="10 to 25 degrees"):
        transverse_roof_external_coefficients(
            roof_pitch_degrees=8,
            average_roof_height_m=2.7,
            building_depth_m=3,
        )
