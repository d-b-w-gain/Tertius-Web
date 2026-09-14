from __future__ import annotations

from pathlib import Path

import pytest

from server.gis_cache.settings import GisCacheSettings
from server.gis_cache.wind_multipliers import GaWindMultiplierProvider


def _settings(root: Path) -> GisCacheSettings:
    return GisCacheSettings(
        root=root,
        max_upload_bytes=4_000_000,
        max_pixels=1_000_000,
    )


GRID_XML = """<?xml version="1.0" encoding="UTF-8"?>
<gridDataset>
  <axis name="lat"><values npts="3600" start="-33.99475703469757" resolution="-2.777787654413971E-4" /></axis>
  <axis name="lon"><values npts="3600" start="150.3513388888889" resolution="2.777777777777805E-4" /></axis>
</gridDataset>
"""


def test_ga_provider_samples_all_24_directional_values_and_caches_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    provider = GaWindMultiplierProvider(_settings(tmp_path))
    requests: list[str] = []

    def read_text(url: str) -> str:
        requests.append(url)
        if url.endswith("dataset.xml"):
            return GRID_XML
        variable = next(value for value in ("Mz", "Ms", "Mt") if value in url)
        offset = {"Mz": 0.70, "Ms": 0.80, "Mt": 1.00}[variable]
        return f"{variable}.{variable}[1][1]\n[0], {offset}\n"

    monkeypatch.setattr(provider, "_read_text", read_text)

    result = provider.fetch(-34.4125046, 150.8885637)

    assert result.tile_id == "e150.3512s33.9946"
    assert result.evidence_id.startswith("windv1-")
    assert result.terrain_height_multipliers.n == pytest.approx(0.70)
    assert result.shielding_multipliers.sw == pytest.approx(0.80)
    assert result.topographic_multipliers.e == pytest.approx(1.00)
    assert result.review_required is True
    assert len(requests) == 25

    requests.clear()
    cached = provider.fetch(-34.4125046, 150.8885637)
    assert cached == result
    assert requests == []


def test_ga_grid_indices_select_the_nearest_porter_street_cell():
    assert GaWindMultiplierProvider._grid_indices(
        GRID_XML, -34.4125046, 150.8885637
    ) == (1504, 1934)
