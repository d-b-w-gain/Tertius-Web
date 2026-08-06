from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
from typing import Any


DATASET_PATH = (
    Path(__file__).parent
    / "data"
    / "as_nzs_1170_2_2021_key_changes_tables.json"
)

AUSTRALIAN_REGIONS = (
    "A0",
    "A1",
    "A2",
    "A3",
    "A4",
    "A5",
    "B1",
    "B2",
    "C",
    "D",
)


class WindStandardTableError(ValueError):
    """Raised when the digitised evidence cannot provide a requested value."""


@lru_cache(maxsize=1)
def load_wind_standard_dataset() -> dict[str, Any]:
    try:
        payload = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WindStandardTableError(
            "AS/NZS 1170.2 digitised table evidence is unavailable"
        ) from exc
    if payload.get("schema_version") != "1.0" or not isinstance(
        payload.get("tables"), dict
    ):
        raise WindStandardTableError("digitised wind-table evidence has an invalid schema")
    return payload


def _australian_region(region: str) -> str:
    code = str(region).strip().upper()
    if code not in AUSTRALIAN_REGIONS:
        raise WindStandardTableError(
            f"wind region {region!r} is not one of {list(AUSTRALIAN_REGIONS)}"
        )
    return code


def lookup_direction_multipliers(region: str) -> dict[str, float]:
    code = _australian_region(region)
    column = "B2/C/D" if code in {"B2", "C", "D"} else code
    table = load_wind_standard_dataset()["tables"][
        "wind_direction_multiplier_australia"
    ]
    values = {
        str(row["direction"]).lower(): float(row[column]) for row in table["rows"]
    }
    expected = {"n", "ne", "e", "se", "s", "sw", "w", "nw"}
    if set(values) != expected:
        raise WindStandardTableError("Australian Md table is incomplete")
    return values


def lookup_climate_change_multiplier(region: str) -> float:
    code = _australian_region(region)
    if code.startswith("A") or code == "B1":
        return 1.0
    return 1.05


def site_table_evidence(region: str) -> dict[str, Any]:
    code = _australian_region(region)
    dataset = load_wind_standard_dataset()
    tables = dataset["tables"]
    relevant_table_ids = (
        "wind_direction_multiplier_australia",
        "climate_change_multiplier",
    )
    return {
        "dataset_version": dataset["dataset_version"],
        "standard_reference": dataset["standard_reference"],
        "source": dataset["source"],
        "verification": dataset["verification"],
        "region": code,
        "direction_multipliers": lookup_direction_multipliers(code),
        "climate_change_multiplier": lookup_climate_change_multiplier(code),
        "applied_tables": [
            {
                "id": table_id,
                "table_number": tables[table_id]["table_number"],
                "title": tables[table_id]["title"],
                "source_page": tables[table_id]["source_page"],
                "applicability": tables[table_id]["applicability"],
            }
            for table_id in relevant_table_ids
        ],
        "report_table_index": [
            {
                "id": table_id,
                "table_number": table["table_number"],
                "title": table["title"],
                "source_page": table["source_page"],
            }
            for table_id, table in tables.items()
        ],
    }


def site_report_evidence(
    site: dict[str, Any], calculation: dict[str, Any]
) -> dict[str, Any]:
    region = str(site["wind"]["region"])
    dataset = load_wind_standard_dataset()
    return {
        "schema_version": "1.0",
        "report_type": "tertius_site_wind_evidence",
        "site": site,
        "calculation": calculation,
        "site_table_evidence": site_table_evidence(region),
        "digitised_tables": dataset["tables"],
        "digitised_rules": dataset["rules"],
    }
