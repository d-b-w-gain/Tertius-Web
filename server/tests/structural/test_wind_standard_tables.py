from __future__ import annotations

import pytest

from core.structural.wind_standard_tables import (
    WindStandardTableError,
    load_wind_standard_dataset,
    lookup_climate_change_multiplier,
    lookup_direction_multipliers,
    site_report_evidence,
    site_table_evidence,
)


def test_digitised_dataset_carries_source_hash_and_all_extracted_tables():
    dataset = load_wind_standard_dataset()

    assert dataset["dataset_version"] == "key-changes-2021-v1"
    assert dataset["source"]["source_type"] == "secondary_summary_presentation"
    assert dataset["source"]["sha256"] == (
        "c866da386d04013d1dc9027765bd7268e99c4486b286ac54a69b45a2c940c6a4"
    )
    assert len(dataset["tables"]) == 8
    assert dataset["verification"]["status"] == "requires_licensed_standard_check"


def test_a2_direction_and_climate_values_match_tables_3_2a_and_3_3():
    assert lookup_direction_multipliers("A2") == {
        "n": 0.85,
        "ne": 0.75,
        "e": 0.85,
        "se": 0.95,
        "s": 0.95,
        "sw": 0.95,
        "w": 1.0,
        "nw": 0.95,
    }
    assert lookup_climate_change_multiplier("A2") == 1.0
    assert lookup_climate_change_multiplier("B2") == 1.05
    assert lookup_direction_multipliers("C") == {
        direction: 0.9
        for direction in ("n", "ne", "e", "se", "s", "sw", "w", "nw")
    }


def test_site_evidence_is_report_ready_but_not_claimed_as_verified():
    evidence = site_table_evidence("a2")
    report = site_report_evidence(
        {"wind": {"region": "A2"}},
        {"q_z_kPa": 0.5},
    )

    assert evidence["region"] == "A2"
    assert [table["table_number"] for table in evidence["applied_tables"]] == [
        "3.2(A)",
        "3.3",
    ]
    assert len(evidence["report_table_index"]) == 8
    assert len(report["digitised_tables"]) == 8
    assert report["site_table_evidence"]["verification"]["status"] == (
        "requires_licensed_standard_check"
    )


def test_unsupported_region_fails_closed():
    with pytest.raises(WindStandardTableError):
        lookup_direction_multipliers("NZ1")
