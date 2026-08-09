from __future__ import annotations

import pytest

from core.structural.site_wind import (
    TABLE_VERSION,
    compute_site_wind,
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
    assert result["terrain_height_multiplier"] == pytest.approx(0.75)
    assert result["site_wind_speed_m_s"] == pytest.approx(33.75)
    assert result["q_z_kPa"] == pytest.approx(0.683438)
    assert len(result["verifier_hash"]) == 12


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
