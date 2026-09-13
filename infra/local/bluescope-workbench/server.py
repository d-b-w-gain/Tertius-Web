from __future__ import annotations

import json
import mimetypes
import os
import re
import sqlite3
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


APP_DIR = Path(__file__).resolve().parent
# Kustomize's ConfigMap generator mounts file keys by basename, so the static
# assets sit beside this module inside the pod even though they are grouped in
# a local source directory.
STATIC_DIR = APP_DIR
DB_PATH = Path(os.getenv("PROCUREMENT_DB", "/procurement-data/procurement.db"))
PORT = int(os.getenv("PORT", "8080"))
API_BASE = os.getenv(
    "BLUESCOPE_API_BASE",
    "https://gateway.apiaut.bluescope.com/api/experience/gs1/orders/v1",
).rstrip("/")
TOKEN_URL = os.getenv(
    "BLUESCOPE_TOKEN_URL",
    "https://login.microsoftonline.com/bluescopeltd.onmicrosoft.com/oauth2/token",
)

SUBSCRIPTION_KEY = os.getenv("BLUESCOPE_SUBSCRIPTION_KEY", "")
CLIENT_ID = os.getenv("BLUESCOPE_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("BLUESCOPE_CLIENT_SECRET", "")
RESOURCE = os.getenv("BLUESCOPE_RESOURCE", "")

CATALOGUE_RULES = (
    (re.compile(r"^C\d{3}\d{2}$", re.I), "ZED & CEE Purlins and Girts", "Lysaght candidate"),
    (re.compile(r"^TOPHAT", re.I), "TOPSPAN / top-hat framing", "Confirm Lysaght equivalent"),
    (re.compile(r"^CUSTOM-ORB", re.I), "CUSTOM ORB roof and wall cladding", "Lysaght candidate"),
    (re.compile(r"^(QUAD-GUTTER|RIDGE-CAPPING|CORNER-CAP|TBD-FASCIA)", re.I), "Rainwater goods and flashings", "Confirm profile and girth"),
)


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def credentials_status() -> dict[str, bool]:
    return {
        "subscription_key": bool(SUBSCRIPTION_KEY),
        "oauth_client_id": bool(CLIENT_ID),
        "oauth_client_secret": bool(CLIENT_SECRET),
        "oauth_resource": bool(RESOURCE),
    }


def classify(part_number: str) -> tuple[str, str] | None:
    for pattern, family, confidence in CATALOGUE_RULES:
        if pattern.search(part_number or ""):
            return family, confidence
    return None


def load_bom() -> dict[str, Any]:
    if not DB_PATH.exists():
        return {"available": False, "reason": "The shed procurement database is not mounted.", "items": []}

    try:
        connection = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=2)
        connection.row_factory = sqlite3.Row
        snapshot = connection.execute(
            "SELECT id, filename, design_name, imported_at FROM snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not snapshot:
            return {"available": True, "reason": "No BoM snapshots exist yet.", "items": []}

        rows = connection.execute(
            """
            SELECT part_number, line_item, quantity, unit, length_mm,
                   material, colour, finish, grade
            FROM items
            WHERE snapshot_id = ?
            ORDER BY part_number, length_mm
            """,
            (snapshot["id"],),
        ).fetchall()
        connection.close()
    except (sqlite3.Error, OSError) as error:
        return {"available": False, "reason": f"Could not read the BoM: {error}", "items": []}

    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        product = classify(row["part_number"] or "")
        if not product:
            continue
        key = (row["part_number"], row["length_mm"], row["unit"], row["colour"], row["finish"])
        if key not in grouped:
            grouped[key] = {
                "part_number": row["part_number"],
                "description": row["line_item"],
                "quantity": 0,
                "unit": row["unit"],
                "length_mm": row["length_mm"],
                "material": row["material"],
                "colour": row["colour"],
                "finish": row["finish"],
                "grade": row["grade"],
                "family": product[0],
                "mapping": product[1],
            }
        grouped[key]["quantity"] += row["quantity"] or 0

    return {
        "available": True,
        "reason": None,
        "snapshot": dict(snapshot),
        "items": list(grouped.values()),
    }


def make_stock_plan(items: list[dict[str, Any]], stock_length: float = 9000, kerf: float = 3) -> list[dict[str, Any]]:
    by_profile: dict[str, list[float]] = defaultdict(list)
    for item in items:
        profile = item.get("part_number") or ""
        length = float(item.get("length_mm") or 0)
        quantity = int(round(float(item.get("quantity") or 0)))
        if not re.match(r"^C\d{3}\d{2}$", profile, re.I) or length <= 0 or length > stock_length:
            continue
        by_profile[profile].extend([length] * quantity)

    plans: list[dict[str, Any]] = []
    for profile, cuts in sorted(by_profile.items()):
        bars: list[list[float]] = []
        for cut in sorted(cuts, reverse=True):
            best_index = None
            best_remaining = None
            for index, bar in enumerate(bars):
                used = sum(bar) + max(0, len(bar) - 1) * kerf
                required = cut + (kerf if bar else 0)
                remaining = stock_length - used - required
                if remaining >= 0 and (best_remaining is None or remaining < best_remaining):
                    best_index, best_remaining = index, remaining
            if best_index is None:
                bars.append([cut])
            else:
                bars[best_index].append(cut)

        rendered = []
        for number, bar in enumerate(bars, start=1):
            used = sum(bar) + max(0, len(bar) - 1) * kerf
            rendered.append({
                "number": number,
                "cuts_mm": bar,
                "used_mm": round(used, 1),
                "offcut_mm": round(stock_length - used, 1),
            })
        plans.append({
            "profile": profile,
            "stock_length_mm": stock_length,
            "stock_bars": len(rendered),
            "cut_count": len(cuts),
            "bars": rendered,
        })
    return plans


def retrieve_token() -> str | None:
    if not all((CLIENT_ID, CLIENT_SECRET, RESOURCE)):
        return None
    body = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "resource": RESOURCE,
        }
    ).encode("ascii")
    request = urllib.request.Request(TOKEN_URL, data=body, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(request, timeout=15, context=ssl.create_default_context()) as response:
        payload = json.load(response)
    return payload.get("access_token")


def probe_bluescope() -> tuple[int, dict[str, Any]]:
    headers = {
        "Accept": "application/json",
        "Cache-Control": "no-cache",
        "User-Agent": "bluescope-procurement-workbench/0.1",
    }
    if SUBSCRIPTION_KEY:
        headers["Ocp-Apim-Subscription-Key"] = SUBSCRIPTION_KEY
    try:
        token = retrieve_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(f"{API_BASE}/isAlive", headers=headers, method="GET")
        with urllib.request.urlopen(request, timeout=15, context=ssl.create_default_context()) as response:
            return HTTPStatus.OK, {
                "ok": 200 <= response.status < 300,
                "upstream_status": response.status,
                "message": "BlueScope API gateway responded.",
            }
    except urllib.error.HTTPError as error:
        return HTTPStatus.OK, {
            "ok": False,
            "upstream_status": error.code,
            "message": (
                "BlueScope requires an active subscription key."
                if error.code == HTTPStatus.UNAUTHORIZED and not SUBSCRIPTION_KEY
                else "BlueScope rejected the health check. Check subscription approval and OAuth onboarding."
            ),
        }
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        return HTTPStatus.OK, {
            "ok": False,
            "message": f"Could not reach the BlueScope gateway: {type(error).__name__}",
        }


class Handler(BaseHTTPRequestHandler):
    server_version = "BlueScopeWorkbench/0.1"

    def send_json(self, status: int, value: Any) -> None:
        body = json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def serve_file(self, relative: str) -> None:
        path = (STATIC_DIR / relative).resolve()
        if STATIC_DIR.resolve() not in path.parents or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if path == "/healthz":
            self.send_json(HTTPStatus.OK, {"ok": True})
        elif path == "/api/status":
            creds = credentials_status()
            self.send_json(
                HTTPStatus.OK,
                {
                    "product": "BBC Purchase Orders",
                    "business_unit": "BlueScope Building Components (includes Lysaght)",
                    "api": "GS1 Orders API (BBC v2 onboarding)",
                    "portal_console_route": "GS1 Orders API v1 health endpoint",
                    "api_base": API_BASE,
                    "operations": ["GET isAlive", "GET isAliveAndWell", "PUT putOrder"],
                    "quote_supported": False,
                    "catalogue_supported": False,
                    "credentials": creds,
                    "probe_ready": True,
                    "order_ready": all(creds.values()),
                },
            )
        elif path == "/api/bom":
            bom = load_bom()
            bom["stock_plans"] = make_stock_plan(bom.get("items", []))
            self.send_json(HTTPStatus.OK, bom)
        elif path in ("/", "/index.html"):
            self.serve_file("index.html")
        elif path.startswith("/static/"):
            self.serve_file(path.removeprefix("/static/"))
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/connectivity":
            status, result = probe_bluescope()
            self.send_json(status, result)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        # Paths and status only; never log request headers, payloads, or credentials.
        super().log_message(format, *args)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"BlueScope procurement workbench listening on :{PORT}", flush=True)
    server.serve_forever()
