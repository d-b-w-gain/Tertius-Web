import os
import time

from core.compile_sandbox import run_compile_sandbox


def test_compile_sandbox_rejects_unsupported_export_format_before_spawn(tmp_path):
    (tmp_path / "design.py").write_text(
        """
from pathlib import Path

Path("spawned.txt").write_text("spawned", encoding="utf-8")
""",
        encoding="utf-8",
    )

    result = run_compile_sandbox(tmp_path, "../stl", timeout_seconds=5)

    assert result.success is False
    assert result.error == "Unsupported export format: ../stl"
    assert not (tmp_path / "spawned.txt").exists()


def test_compile_sandbox_does_not_expose_worker_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DB_PASSWORD", "super-secret")
    (tmp_path / "design.py").write_text(
        """
from pathlib import Path
import os

Path("leaked-secret.txt").write_text(os.environ.get("APP_DB_PASSWORD", ""), encoding="utf-8")
raise RuntimeError("stop after leak attempt")
""",
        encoding="utf-8",
    )

    run_compile_sandbox(tmp_path, "stl", timeout_seconds=5)

    assert (tmp_path / "leaked-secret.txt").read_text(encoding="utf-8") == ""


def test_compile_sandbox_timeout_kills_spawned_children(tmp_path):
    marker = tmp_path / "child-survived.txt"
    child_code = (
        "import pathlib, time; "
        "time.sleep(15); "
        f"pathlib.Path({str(marker)!r}).write_text('alive', encoding='utf-8')"
    )
    (tmp_path / "design.py").write_text(
        f"""
import subprocess
import sys
import time

print("DESIGN_STARTED", flush=True)
subprocess.Popen([sys.executable, "-c", {child_code!r}])
time.sleep(30)
""",
        encoding="utf-8",
    )

    result = run_compile_sandbox(tmp_path, "stl", timeout_seconds=10)
    time.sleep(6)

    assert result.success is False
    assert "DESIGN_STARTED" in result.stdout
    assert not marker.exists()
