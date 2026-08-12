from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from .models import SiteBoundaryEvidence
from .settings import GisCacheSettings


class SiteBoundaryUnavailable(RuntimeError):
    """Raised when the authoritative property service cannot resolve a site."""


class NswPropertyBoundaryProvider:
    """Fetch and retain the NSW property polygon containing a site point."""

    def __init__(self, settings: GisCacheSettings):
        self.settings = settings
        self.root = settings.root / "cadastre"

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def _point_cache_path(self, latitude: float, longitude: float) -> Path:
        key = sha256(f"{latitude:.6f},{longitude:.6f}".encode()).hexdigest()[:24]
        return self.root / f"point-{key}.json"

    def fetch(self, latitude: float, longitude: float) -> SiteBoundaryEvidence:
        cached_path = self._point_cache_path(latitude, longitude)
        if cached_path.is_file():
            try:
                return SiteBoundaryEvidence.model_validate_json(
                    cached_path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                # A corrupt cache entry is safe to replace from the source.
                pass

        params = {
            "where": "1=1",
            "geometry": f"{longitude:.8f},{latitude:.8f}",
            "geometryType": "esriGeometryPoint",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "propid,housenumber,address,lastupdate,shapeuuid",
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "json",
        }
        try:
            url = (
                f"{self.settings.nsw_property_feature_url}?{urlencode(params)}"
            )
            with urlopen(  # noqa: S310 - fixed operator-configured HTTPS endpoint
                url,
                timeout=self.settings.nsw_property_timeout_seconds,
            ) as response:
                payload = json.load(response)
        except (OSError, URLError, ValueError) as exc:
            raise SiteBoundaryUnavailable(
                "NSW property boundary service is unavailable"
            ) from exc

        features = payload.get("features") if isinstance(payload, dict) else None
        if not isinstance(features, list) or not features:
            raise SiteBoundaryUnavailable(
                "No NSW property polygon contains the selected site point"
            )
        source_feature = features[0]
        attributes = source_feature.get("attributes", {})
        geometry = source_feature.get("geometry", {})
        rings = geometry.get("rings") if isinstance(geometry, dict) else None
        if not isinstance(attributes, dict) or not isinstance(rings, list) or not rings:
            raise SiteBoundaryUnavailable(
                "NSW property service returned an invalid polygon"
            )

        last_update = attributes.get("lastupdate")
        if isinstance(last_update, (int, float)):
            dataset_version = datetime.fromtimestamp(
                last_update / 1000.0, tz=timezone.utc
            ).date().isoformat()
        else:
            dataset_version = "current"
        feature: dict[str, Any] = {
            "type": "Feature",
            "properties": {
                key: attributes.get(key)
                for key in (
                    "propid",
                    "housenumber",
                    "address",
                    "lastupdate",
                    "shapeuuid",
                )
            },
            "geometry": {"type": "Polygon", "coordinates": rings},
        }
        identity = json.dumps(
            {
                "shapeuuid": attributes.get("shapeuuid"),
                "lastupdate": last_update,
                "geometry": rings,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        evidence = SiteBoundaryEvidence(
            evidence_id=f"parcelv1-{sha256(identity.encode()).hexdigest()[:32]}",
            fetched_at=datetime.now(timezone.utc),
            dataset_version=dataset_version,
            source_uri=self.settings.nsw_property_feature_url,
            query_point=(longitude, latitude),
            feature=feature,
        )
        temporary = cached_path.with_suffix(".tmp")
        temporary.write_text(evidence.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(cached_path)
        return evidence
