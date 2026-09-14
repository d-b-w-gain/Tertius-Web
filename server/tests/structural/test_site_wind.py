from __future__ import annotations

import pytest

from core.structural.site_wind import (
    TABLE_VERSION,
    compute_site_wind,
    lookup_regional_wind_speed,
    lookup_terrain_height_multiplier,
    lookup_wind_region,
    verify_site_wind_snapshot,
    wind_region_geojson,
)


def test_porter_street_lookup_returns_a2_suggestion_with_dataset_provenance():
    result = lookup_wind_region(
        latitude=-34.4125046,
        longitude=150.8885637,
    )

    assert result is not None
    assert result["region"] == "A2"
    assert result["area"] == "NSW"
    assert result["approximate"] is True
    assert "Geoscience Australia" in result["source"]
    assert "Fig. 3.1(A)" in result["verify_against"]


def test_site_wind_preserves_fbd_starter_table_derivation():
    result = compute_site_wind(
        region="A2",
        terrain_category="3",
        importance_level="2",
        annual_probability_uls="1/500",
        reference_height_m=1.6,
        direction_multiplier=1.0,
        shielding_multiplier=1.0,
        topographic_multiplier=1.0,
    )

    assert result["table_version"] == TABLE_VERSION
    assert result["regional_wind_speed_m_s"] == pytest.approx(45.0)
    assert result["terrain_height_multiplier"] == pytest.approx(0.83)
    assert result["site_wind_speed_m_s"] == pytest.approx(37.35)
    assert result["q_z_kPa"] == pytest.approx(0.8370135)
    assert len(result["verifier_hash"]) == 12


@pytest.mark.parametrize(
    ("region", "ari", "expected"),
    (
        ("A2", 1000, 46.0),
        ("A5", 2500, 48.0),
        ("B1", 100, 48.0),
        ("B2", 2000, 63.0),
        ("D", 2000, 90.0),
    ),
)
def test_2021_regional_wind_speed_rows(region, ari, expected):
    assert lookup_regional_wind_speed(region, ari) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("category", "expected_at_3m", "expected_at_30m"),
    (
        ("1", 0.97, 1.18),
        ("2", 0.91, 1.12),
        ("2.5", 0.87, 1.06),
        ("3", 0.83, 1.00),
        ("4", 0.75, 0.80),
    ),
)
def test_2021_terrain_height_table_rows(
    category,
    expected_at_3m,
    expected_at_30m,
):
    assert lookup_terrain_height_multiplier(category, 3.0) == pytest.approx(
        expected_at_3m
    )
    assert lookup_terrain_height_multiplier(category, 30.0) == pytest.approx(
        expected_at_30m
    )


def test_terrain_height_table_interpolates_between_source_rows():
    assert lookup_terrain_height_multiplier("3", 12.5) == pytest.approx(0.86)


def test_site_wind_accepts_an_auditable_directional_terrain_override():
    result = compute_site_wind(
        region="A2",
        terrain_category="3",
        importance_level="2",
        reference_height_m=3.0,
        direction_multiplier=0.9,
        terrain_height_multiplier=0.82,
        shielding_multiplier=0.95,
        topographic_multiplier=1.1,
    )

    expected_speed = 45.0 * 0.9 * 0.82 * 0.95 * 1.1
    assert result["terrain_height_multiplier"] == pytest.approx(0.82)
    assert result["terrain_height_source"] == "authored directional multiplier"
    assert result["site_wind_speed_m_s"] == pytest.approx(expected_speed)
    assert verify_site_wind_snapshot(result) == []


def test_snapshot_drift_is_field_specific_and_fails_closed():
    result = compute_site_wind(
        region="C",
        terrain_category="3",
        importance_level="2",
        annual_probability_uls="1/500",
        reference_height_m=1.6,
    )
    assert verify_site_wind_snapshot(result) == []

    result["q_z_kPa"] = float(result["q_z_kPa"]) + 0.1
    messages = verify_site_wind_snapshot(result)

    assert any("q_z_kPa" in message for message in messages)


def test_region_overlay_is_the_vendored_fbd_feature_collection():
    overlay = wind_region_geojson()

    assert overlay["type"] == "FeatureCollection"
    assert len(overlay["features"]) >= 10
