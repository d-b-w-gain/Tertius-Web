#!/usr/bin/env python3
"""Local-only helper for the static procurement analysis viewer.

The static HTML page cannot start Python directly. Run this helper locally, then
the file:// viewer can POST a design path to http://127.0.0.1:8765/run.
"""

from __future__ import annotations

import json
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


HOST = "127.0.0.1"
PORT = 8765
REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYGROUND = REPO_ROOT / "scripts" / "spikes" / "procurement_analysis_playground.py"


def _read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("content-length") or 0)
    if length <= 0:
        return {}
    return json.loads(handler.rfile.read(length).decode("utf-8"))


def _send_json(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("content-type", "application/json; charset=utf-8")
    handler.send_header("access-control-allow-origin", "*")
    handler.send_header("access-control-allow-methods", "GET, POST, OPTIONS")
    handler.send_header("access-control-allow-headers", "content-type")
    handler.send_header("content-length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _string(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _build_command(payload: dict[str, Any]) -> tuple[list[str], Path, int]:
    design_py = Path(_string(payload.get("design")))
    if not design_py.is_file():
        raise FileNotFoundError(f"design.py does not exist: {design_py}")

    output_path = Path(_string(payload.get("output"), str(REPO_ROOT / ".tmp" / "procurement-analysis-viewer-run.json")))
    quality = _string(payload.get("quality"), "sketch")
    export_format = _string(payload.get("format"), "glb")
    compile_timeout = int(payload.get("timeout") or 300)

    command = [
        sys.executable,
        str(PLAYGROUND),
        "--design-py",
        str(design_py),
    ]
    if export_format == "source-only":
        command.append("--source-only")
    else:
        command.extend([
            "--export-format",
            export_format,
            "--quality",
            quality,
            "--compile-timeout",
            str(compile_timeout),
        ])
    if payload.get("compat"):
        command.append("--compat-build123d-compound")
    command.extend(["--out", str(output_path)])
    return command, output_path, compile_timeout


class RunnerHandler(BaseHTTPRequestHandler):
    server_version = "TertiusProcurementRunner/1.0"

    def do_OPTIONS(self) -> None:
        _send_json(self, 200, {"ok": True})

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/health":
            _send_json(self, 200, {"ok": True, "repo_root": str(REPO_ROOT)})
            return
        _send_json(self, 404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/run":
            _send_json(self, 404, {"ok": False, "error": "not found"})
            return
        try:
            payload = _read_json_body(self)
            command, output_path, compile_timeout = _build_command(payload)
            result = subprocess.run(
                command,
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                timeout=compile_timeout + 90,
                check=False,
            )
            analysis = None
            if output_path.is_file():
                analysis = json.loads(output_path.read_text(encoding="utf-8-sig"))
            _send_json(self, 200 if result.returncode == 0 else 500, {
                "ok": result.returncode == 0,
                "returncode": result.returncode,
                "command": command,
                "output": str(output_path),
                "stdout": result.stdout,
                "stderr": result.stderr,
                "analysis": analysis,
            })
        except Exception as exc:  # pragma: no cover - local diagnostic helper
            _send_json(self, 500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))


def main() -> int:
    server = ThreadingHTTPServer((HOST, PORT), RunnerHandler)
    print(f"Procurement analysis runner listening on http://{HOST}:{PORT}")
    print("Open docs/bom/procurement-analysis-viewer.html and use Run with local runner.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping runner.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
