from __future__ import annotations

import json
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha256
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from .models import CardinalMultiplierValues, DirectionalWindMultiplierEvidence
from .settings import GisCacheSettings


class WindMultiplierUnavailable(RuntimeError):
    pass


class GaWindMultiplierProvider:
    """Cache point samples from GA's national eight-direction multiplier grids."""

    _DIRECTIONS = ("n", "ne", "e", "se", "s", "sw", "w", "nw")
    _PRODUCTS = {
        "terrain_height_multipliers": ("terrain", "mz", "Mz"),
        "shielding_multipliers": ("shielding", "ms", "Ms"),
        "topographic_multipliers": ("topographic", "mt", "Mt"),
    }
    _NUMBER = re.compile(
        r"\[\d+\],\s*(-?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)"
    )

    def __init__(self, settings: GisCacheSettings):
        self.settings = settings
        self.requests_dir = settings.root / "wind-multipliers" / "requests"

    def initialize(self) -> None:
        self.requests_dir.mkdir(parents=True, exist_ok=True)

    def fetch(
        self, latitude: float, longitude: float
    ) -> DirectionalWindMultiplierEvidence:
        if not self.settings.ga_wind_multipliers_enabled:
            raise WindMultiplierUnavailable(
                "Geoscience Australia wind multiplier evidence is disabled"
            )
        if not (-44.5 <= latitude <= -9.0 and 112.0 <= longitude <= 154.0):
            raise WindMultiplierUnavailable(
                "coordinates are outside the configured Australian coverage"
            )
        self.initialize()
        request_key = sha256(
            json.dumps(
                {
                    "dataset": "ga-national-wind-multipliers",
                    "latitude": round(latitude, 6),
                    "longitude": round(longitude, 6),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        request_path = self.requests_dir / f"{request_key}.json"
        if request_path.exists():
            try:
                return DirectionalWindMultiplierEvidence.model_validate_json(
                    request_path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                request_path.unlink(missing_ok=True)

        evidence = self._fetch_remote(latitude, longitude)
        temporary_path = request_path.with_suffix(".tmp")
        temporary_path.write_text(
            evidence.model_dump_json(indent=2), encoding="utf-8"
        )
        temporary_path.replace(request_path)
        return evidence

    def _fetch_remote(
        self, latitude: float, longitude: float
    ) -> DirectionalWindMultiplierEvidence:
        tile_id = self._tile_id(latitude, longitude)
        metadata_url = self._dataset_xml_url(tile_id)
        lat_index, lon_index = self._grid_indices(
            self._read_text(metadata_url), latitude, longitude
        )

        jobs: dict[object, tuple[str, str]] = {}
        product_values: dict[str, dict[str, float]] = {
            name: {} for name in self._PRODUCTS
        }
        with ThreadPoolExecutor(
            max_workers=self.settings.ga_wind_multiplier_workers
        ) as executor:
            for product_name, (directory, code, variable) in self._PRODUCTS.items():
                for direction in self._DIRECTIONS:
                    future = executor.submit(
                        self._sample,
                        tile_id,
                        directory,
                        code,
                        variable,
                        direction,
                        lat_index,
                        lon_index,
                    )
                    jobs[future] = (product_name, direction)
            for future in as_completed(jobs):
                product_name, direction = jobs[future]
                product_values[product_name][direction] = future.result()

        evidence_payload = {
            "latitude": latitude,
            "longitude": longitude,
            "tile_id": tile_id,
            "terrain_reference_height_m": 10.0,
            "terrain_height_multipliers": product_values[
                "terrain_height_multipliers"
            ],
            "shielding_multipliers": product_values["shielding_multipliers"],
            "topographic_multipliers": product_values[
                "topographic_multipliers"
            ],
            "dataset_version": "Wind Multiplier Software 2.0 output (January 2016)",
        }
        evidence_id = "windv1-" + sha256(
            json.dumps(
                evidence_payload, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()[:32]
        return DirectionalWindMultiplierEvidence(
            evidence_id=evidence_id,
            latitude=latitude,
            longitude=longitude,
            tile_id=tile_id,
            terrain_reference_height_m=10.0,
            terrain_height_multipliers=CardinalMultiplierValues(
                **product_values["terrain_height_multipliers"]
            ),
            shielding_multipliers=CardinalMultiplierValues(
                **product_values["shielding_multipliers"]
            ),
            topographic_multipliers=CardinalMultiplierValues(
                **product_values["topographic_multipliers"]
            ),
        )

    def _sample(
        self,
        tile_id: str,
        directory: str,
        code: str,
        variable: str,
        direction: str,
        lat_index: int,
        lon_index: int,
    ) -> float:
        dataset = f"{tile_id}_{code}_{direction}.nc"
        query = f"{variable}%5B{lat_index}%5D%5B{lon_index}%5D"
        url = (
            f"{self.settings.ga_wind_multipliers_base_url.rstrip('/')}"
            f"/dodsC/fj6/multipliers/{directory}/{dataset}.ascii?{query}"
        )
        body = self._read_text(url)
        match = self._NUMBER.search(body)
        if match is None:
            raise WindMultiplierUnavailable(
                f"GA {variable} response did not contain a point value"
            )
        value = float(match.group(1))
        if not math.isfinite(value) or value <= 0 or value > 5:
            raise WindMultiplierUnavailable(
                f"GA {variable} response contained an invalid multiplier"
            )
        return round(value, 8)

    def _dataset_xml_url(self, tile_id: str) -> str:
        dataset = f"{tile_id}_mz_n.nc"
        return (
            f"{self.settings.ga_wind_multipliers_base_url.rstrip('/')}"
            f"/ncss/grid/fj6/multipliers/terrain/{dataset}/dataset.xml"
        )

    @staticmethod
    def _tile_id(latitude: float, longitude: float) -> str:
        west = math.floor(longitude - 0.3512) + 0.3512
        north_south_magnitude = math.floor(abs(latitude)) - 0.0054
        return f"e{west:.4f}s{north_south_magnitude:.4f}"

    @staticmethod
    def _grid_indices(
        body: str, latitude: float, longitude: float
    ) -> tuple[int, int]:
        try:
            root = ElementTree.fromstring(body)
            axes = {
                axis.attrib["name"]: axis.find("values")
                for axis in root.findall("axis")
            }
            lat_values = axes["lat"]
            lon_values = axes["lon"]
            if lat_values is None or lon_values is None:
                raise KeyError("axis values")

            def index(values: ElementTree.Element, coordinate: float) -> int:
                start = float(values.attrib["start"])
                resolution = float(values.attrib["resolution"])
                count = int(values.attrib["npts"])
                result = round((coordinate - start) / resolution)
                if not 0 <= result < count:
                    raise WindMultiplierUnavailable(
                        "coordinates are outside the selected GA multiplier tile"
                    )
                return result

            return index(lat_values, latitude), index(lon_values, longitude)
        except (ElementTree.ParseError, KeyError, TypeError, ValueError) as exc:
            raise WindMultiplierUnavailable(
                "GA multiplier grid metadata could not be interpreted"
            ) from exc

    def _read_text(self, url: str) -> str:
        request = Request(
            url,
            headers={"User-Agent": "Tertius-GIS-Cache/0.1 (+https://github.com/d-b-w-gain)"},
        )
        try:
            with urlopen(
                request, timeout=self.settings.ga_wind_multiplier_timeout_seconds
            ) as response:
                return response.read().decode("utf-8", errors="replace")
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise WindMultiplierUnavailable(
                "Geoscience Australia wind multiplier service is unavailable"
            ) from exc
