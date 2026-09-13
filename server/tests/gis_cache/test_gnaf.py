from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from server.gis_cache.gnaf import GNAF_ATTRIBUTION, GnafIndex, normalize_address


def test_search_returns_address_point_not_street_centroid(tmp_path: Path):
    index = GnafIndex(tmp_path)
    index.initialize()
    with sqlite3.connect(index.path) as connection:
        connection.executescript(
            """
            CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE address(
              pid TEXT, address TEXT, normalized TEXT, house_number TEXT,
              street_name_norm TEXT, locality_norm TEXT, postcode TEXT,
              confidence INTEGER, longitude REAL, latitude REAL, geocode_type TEXT
            );
            CREATE INDEX address_house_postcode_idx ON address(house_number, postcode);
            """
        )
        connection.execute(
            "INSERT INTO metadata VALUES ('dataset_version', 'G-NAF test release')"
        )
        connection.execute(
            "INSERT INTO metadata VALUES ('states', ?)", (json.dumps(["NSW"]),)
        )
        address = "14 PORTER ST, NORTH WOLLONGONG NSW 2500"
        connection.execute(
            "INSERT INTO address VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "GANSW123",
                address,
                normalize_address(address),
                "14",
                "PORTER",
                "NORTH WOLLONGONG",
                "2500",
                2,
                150.8886,
                -34.4125,
                "PROPERTY CENTROID",
            ),
        )

    result = index.search("14 Porter St, North Wollongong NSW 2500")

    assert len(result) == 1
    assert result[0].address_pid == "GANSW123"
    assert result[0].quality == "address_point"
    assert result[0].latitude == -34.4125
    assert result[0].attribution == GNAF_ATTRIBUTION
