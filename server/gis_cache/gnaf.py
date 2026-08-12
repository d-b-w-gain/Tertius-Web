from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import threading
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, Iterable

from remotezip import RemoteZip

from .models import GeocodeCandidate

GNAF_PACKAGE_API = (
    "https://data.gov.au/data/api/3/action/package_show"
    "?id=19432f89-dc3a-4ef3-b943-5326ef1dbecc"
)
GNAF_ATTRIBUTION = (
    "Incorporates or developed using G-NAF © Geoscape Australia licensed by "
    "the Commonwealth of Australia under the Open Geo-coded National Address "
    "File (G-NAF) End User Licence Agreement."
)
_NORMALIZE = re.compile(r"[^A-Z0-9]+")


def normalize_address(value: str) -> str:
    return _NORMALIZE.sub(" ", value.upper()).strip()


def _rows(handle: BinaryIO) -> Iterable[dict[str, str]]:
    text = io.TextIOWrapper(handle, encoding="utf-8-sig", newline="")
    yield from csv.DictReader(text, delimiter="|")


class GnafIndex:
    """Compact address-point index derived from the official G-NAF PSV release."""

    def __init__(self, root: Path):
        self.root = root / "address"
        self.path = self.root / "gnaf.sqlite3"
        self._state_lock = threading.Lock()
        self._job: dict[str, object] = {"status": "not_started"}

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def status(self) -> dict[str, object]:
        self.initialize()
        with self._state_lock:
            job = dict(self._job)
        if not self.path.exists():
            return {"available": False, "row_count": 0, "job": job}
        try:
            with sqlite3.connect(self.path) as connection:
                metadata = dict(connection.execute("SELECT key, value FROM metadata"))
                row_count = connection.execute(
                    "SELECT COUNT(*) FROM address"
                ).fetchone()[0]
        except sqlite3.Error:
            return {"available": False, "row_count": 0, "job": job}
        return {
            "available": True,
            "row_count": row_count,
            "dataset_version": metadata.get("dataset_version", "unknown"),
            "states": json.loads(metadata.get("states", "[]")),
            "updated_at": metadata.get("updated_at"),
            "attribution": GNAF_ATTRIBUTION,
            "job": job,
        }

    def start_sync(
        self, states: tuple[str, ...], *, force: bool = False
    ) -> dict[str, object]:
        self.initialize()
        with self._state_lock:
            if self._job.get("status") == "running":
                return dict(self._job)
            if self.path.exists() and not force:
                self._job = {"status": "ready", "detail": "index already exists"}
                return dict(self._job)
            self._job = {
                "status": "running",
                "states": list(states),
                "started_at": datetime.now(UTC).isoformat(),
            }
        threading.Thread(
            target=self._sync_worker,
            args=(states,),
            name="gnaf-sync",
            daemon=True,
        ).start()
        return self.status()["job"]  # type: ignore[return-value]

    def _sync_worker(self, states: tuple[str, ...]) -> None:
        try:
            release = self._current_release()
            self._build(release["url"], release["name"], states)
            result: dict[str, object] = {
                "status": "ready",
                "states": list(states),
                "completed_at": datetime.now(UTC).isoformat(),
            }
        except Exception as exc:  # background job exposes a bounded diagnostic
            result = {"status": "failed", "detail": str(exc)[:500]}
        with self._state_lock:
            self._job = result

    @staticmethod
    def _current_release() -> dict[str, str]:
        request = urllib.request.Request(
            GNAF_PACKAGE_API, headers={"User-Agent": "Tertius-GIS/1"}
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.load(response)
        resources = payload.get("result", {}).get("resources", [])
        candidates = [
            resource
            for resource in resources
            if "GDA2020" in str(resource.get("name", "")).upper()
            and str(resource.get("url", "")).lower().endswith(".zip")
            and str(resource.get("url", "")).startswith("https://data.gov.au/")
        ]
        if not candidates:
            raise RuntimeError("official G-NAF GDA2020 ZIP was not found")
        resource = max(candidates, key=lambda item: str(item.get("last_modified", "")))
        return {
            "url": str(resource["url"]),
            "name": str(resource.get("name", "G-NAF GDA2020")),
        }

    @staticmethod
    def _entry(names: list[str], state: str, table: str) -> str:
        suffix = f"{state}_{table}_psv.psv".upper()
        matches = [name for name in names if name.upper().endswith(suffix)]
        if len(matches) != 1:
            raise RuntimeError(f"G-NAF archive is missing {state} {table}")
        return matches[0]

    def _build(self, url: str, version: str, states: tuple[str, ...]) -> None:
        staging = self.root / "gnaf.staging.sqlite3"
        staging.unlink(missing_ok=True)
        connection = sqlite3.connect(staging)
        try:
            connection.executescript(
                """
                PRAGMA journal_mode=OFF;
                PRAGMA synchronous=OFF;
                PRAGMA temp_store=FILE;
                CREATE TABLE geocode(pid TEXT PRIMARY KEY, longitude REAL, latitude REAL, geocode_type TEXT);
                CREATE TABLE detail(
                  pid TEXT PRIMARY KEY, address TEXT, normalized TEXT, house_number TEXT,
                  street_name_norm TEXT, locality_norm TEXT, postcode TEXT, confidence INTEGER
                );
                CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                """
            )
            with RemoteZip(url) as archive:
                names = archive.namelist()
                for state in states:
                    self._load_state(connection, archive, names, state)
            connection.executescript(
                """
                CREATE TABLE address AS
                  SELECT d.pid, d.address, d.normalized, d.house_number,
                         d.street_name_norm, d.locality_norm, d.postcode, d.confidence,
                         g.longitude, g.latitude, g.geocode_type
                  FROM detail d JOIN geocode g ON g.pid = d.pid;
                CREATE UNIQUE INDEX address_pid_idx ON address(pid);
                CREATE INDEX address_house_postcode_idx ON address(house_number, postcode);
                CREATE INDEX address_house_street_idx ON address(house_number, street_name_norm);
                CREATE INDEX address_normalized_idx ON address(normalized);
                DROP TABLE detail;
                DROP TABLE geocode;
                """
            )
            metadata = {
                "dataset_version": version,
                "states": json.dumps(list(states)),
                "updated_at": datetime.now(UTC).isoformat(),
                "source_url": url,
                "attribution": GNAF_ATTRIBUTION,
            }
            connection.executemany(
                "INSERT INTO metadata VALUES (?, ?)", metadata.items()
            )
            connection.commit()
        finally:
            connection.close()
        self.path.unlink(missing_ok=True)
        staging.replace(self.path)

    def _load_state(
        self,
        connection: sqlite3.Connection,
        archive: RemoteZip,
        names: list[str],
        state: str,
    ) -> None:
        localities: dict[str, tuple[str, str]] = {}
        with archive.open(self._entry(names, state, "LOCALITY")) as handle:
            for row in _rows(handle):
                if not row["DATE_RETIRED"]:
                    localities[row["LOCALITY_PID"]] = (
                        row["LOCALITY_NAME"],
                        row["PRIMARY_POSTCODE"],
                    )

        streets: dict[str, tuple[str, str, str, str]] = {}
        with archive.open(self._entry(names, state, "STREET_LOCALITY")) as handle:
            for row in _rows(handle):
                if not row["DATE_RETIRED"]:
                    streets[row["STREET_LOCALITY_PID"]] = (
                        row["STREET_NAME"],
                        row["STREET_TYPE_CODE"],
                        row["STREET_SUFFIX_CODE"],
                        row["LOCALITY_PID"],
                    )

        batch: list[tuple[object, ...]] = []
        with archive.open(
            self._entry(names, state, "ADDRESS_DEFAULT_GEOCODE")
        ) as handle:
            for row in _rows(handle):
                if row["DATE_RETIRED"]:
                    continue
                batch.append(
                    (
                        row["ADDRESS_DETAIL_PID"],
                        float(row["LONGITUDE"]),
                        float(row["LATITUDE"]),
                        row["GEOCODE_TYPE_CODE"],
                    )
                )
                if len(batch) >= 10_000:
                    connection.executemany(
                        "INSERT OR REPLACE INTO geocode VALUES (?, ?, ?, ?)", batch
                    )
                    batch.clear()
        if batch:
            connection.executemany(
                "INSERT OR REPLACE INTO geocode VALUES (?, ?, ?, ?)", batch
            )

        batch.clear()
        with archive.open(self._entry(names, state, "ADDRESS_DETAIL")) as handle:
            for row in _rows(handle):
                if row["DATE_RETIRED"] or row["ALIAS_PRINCIPAL"] == "A":
                    continue
                street = streets.get(row["STREET_LOCALITY_PID"])
                locality = localities.get(row["LOCALITY_PID"])
                if street is None or locality is None:
                    continue
                house_number = self._house_number(row)
                if not house_number:
                    continue
                street_label = " ".join(part for part in street[:3] if part)
                postcode = row["POSTCODE"] or locality[1]
                address = f"{house_number} {street_label}, {locality[0]} {state} {postcode}".strip()
                batch.append(
                    (
                        row["ADDRESS_DETAIL_PID"],
                        address,
                        normalize_address(address),
                        normalize_address(house_number),
                        normalize_address(street[0]),
                        normalize_address(locality[0]),
                        postcode,
                        int(row["CONFIDENCE"] or 0),
                    )
                )
                if len(batch) >= 10_000:
                    connection.executemany(
                        "INSERT OR REPLACE INTO detail VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        batch,
                    )
                    batch.clear()
        if batch:
            connection.executemany(
                "INSERT OR REPLACE INTO detail VALUES (?, ?, ?, ?, ?, ?, ?, ?)", batch
            )
        connection.commit()

    @staticmethod
    def _house_number(row: dict[str, str]) -> str:
        first = "".join(
            (
                row["NUMBER_FIRST_PREFIX"],
                row["NUMBER_FIRST"],
                row["NUMBER_FIRST_SUFFIX"],
            )
        )
        last = "".join(
            (row["NUMBER_LAST_PREFIX"], row["NUMBER_LAST"], row["NUMBER_LAST_SUFFIX"])
        )
        return f"{first}-{last}" if first and last else first

    def search(self, query: str, limit: int = 5) -> list[GeocodeCandidate]:
        normalized = normalize_address(query)
        if not self.path.exists() or not normalized:
            return []
        tokens = normalized.split()
        house = (
            tokens[0] if tokens and any(char.isdigit() for char in tokens[0]) else ""
        )
        postcode = next(
            (
                token
                for token in reversed(tokens)
                if len(token) == 4 and token.isdigit()
            ),
            "",
        )
        street = next(
            (
                token
                for token in tokens[1:]
                if token.isalpha()
                and token not in {"NSW", "VIC", "QLD", "TAS", "SA", "WA", "ACT", "NT"}
            ),
            "",
        )
        with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as connection:
            version_row = connection.execute(
                "SELECT value FROM metadata WHERE key='dataset_version'"
            ).fetchone()
            version = version_row[0] if version_row else "unknown"
            if house and postcode and street:
                rows = connection.execute(
                    "SELECT * FROM address WHERE house_number=? AND postcode=? "
                    "AND street_name_norm LIKE ? LIMIT 100",
                    (house, postcode, f"{street}%"),
                ).fetchall()
            elif house and postcode:
                rows = connection.execute(
                    "SELECT * FROM address WHERE house_number=? AND postcode=? LIMIT 100",
                    (house, postcode),
                ).fetchall()
            elif house and street:
                rows = connection.execute(
                    "SELECT * FROM address WHERE house_number=? AND street_name_norm LIKE ? LIMIT 100",
                    (house, f"{street}%"),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM address WHERE normalized LIKE ? LIMIT 100",
                    (f"{normalized}%",),
                ).fetchall()
        query_tokens = set(tokens)
        scored = sorted(
            rows,
            key=lambda row: (
                -len(query_tokens & set(row[2].split())),
                -int(row[7] or 0),
                row[1],
            ),
        )
        return [
            GeocodeCandidate(
                address=row[1],
                address_pid=row[0],
                longitude=row[8],
                latitude=row[9],
                geocode_type=row[10],
                confidence=row[7],
                dataset_version=version,
                attribution=GNAF_ATTRIBUTION,
            )
            for row in scored[:limit]
        ]
