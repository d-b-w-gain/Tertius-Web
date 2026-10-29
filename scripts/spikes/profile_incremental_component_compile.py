#!/usr/bin/env python3
"""Profile incremental component compile (#312/#313) on a real design.

Copies a project, marks selected top-level component builders with cadcache's
``@part``, then runs the production compile sandbox through a sequence of
scenarios that share one component cache folder (standing in for the
worker's tenant-scoped store):

1. ``cold``: empty cache, every part builds;
2. ``unchanged``: same source, every part should be a hit;
3. ``unrelated-edit``: ``--unrelated-edit`` applied, every part a hit;
4. ``related-edit``: ``--related-edit`` applied, only the parts it reaches
   rebuild.

Edits are ``OLD=>NEW`` literal replacements in ``design.py``. Prints one JSON
document with per-phase timings and component cache counts per scenario.

Example (reference shed, default GLB quality):

    uv run python scripts/spikes/profile_incremental_component_compile.py \\
      --project-dir ../CAD/3x5shed --output-dir .tmp/incremental-shed \\
      --part roof_cladding --part long_wall_cladding --part floor_assembly \\
      --unrelated-edit 'roof_pitch = 16.3=>roof_pitch = 16.3  # comment' \\
      --related-edit 'roof_cladding_colour = "Manor Red"=>roof_cladding_colour = "Night Sky"'
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from time import perf_counter
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))

from core.compile_sandbox import run_compile_sandbox  # noqa: E402

COPY_SUFFIXES = {".py", ".json", ".yaml", ".yml", ".csv", ".step", ".stp", ".stl", ".png", ".svg"}


def stage_project(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for path in source.iterdir():
        if path.is_file() and path.suffix.lower() in COPY_SUFFIXES and not path.name.startswith("tertius-"):
            shutil.copy2(path, target / path.name)
        elif path.is_dir() and path.name == "assets":
            shutil.copytree(path, target / path.name)


def _split_file(spec: str, default: str = "design.py") -> tuple[str, str]:
    """``module.py:rest`` names a project file; a bare ``rest`` means design.py."""
    head, sep, rest = spec.partition(":")
    if sep and head.endswith((".py", ".json")) and "/" not in head and "=>" not in head:
        return head, rest
    return default, spec


def mark_parts(project: Path, parts: list[str]) -> list[str]:
    """Put ``@part`` on each named top-level ``def``: ``name`` in design.py or
    ``module.py:name`` in another project file. The import goes right above
    the def, so files starting with a docstring or ``__future__`` import stay
    valid."""
    marked = []
    for spec in parts:
        filename, name = _split_file(spec)
        path = project / filename
        source = path.read_text(encoding="utf-8")
        pattern = re.compile(rf"^def {re.escape(name)}\(", re.MULTILINE)
        if not pattern.search(source):
            raise SystemExit(f"{filename} has no top-level def {name}()")
        source = pattern.sub(
            f"from cadcache import part as _cadcache_part\n@_cadcache_part\ndef {name}(", source, count=1
        )
        path.write_text(source, encoding="utf-8")
        marked.append(spec)
    return marked


def apply_edit(project: Path, edit: str) -> None:
    filename, edit = _split_file(edit)
    old, sep, new = edit.partition("=>")
    if not sep:
        raise SystemExit(f"edit must be [file.py:]OLD=>NEW: {edit!r}")
    path = project / filename
    source = path.read_text(encoding="utf-8")
    if old not in source:
        raise SystemExit(f"edit target not found in {filename}: {old!r}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


_OBJECT_ADDRESS = re.compile(rb" at 0x[0-9a-f]+")


def output_fingerprint(path: Path) -> str:
    """Digest of an export with the Python object addresses that the source
    map's parameter reprs carry removed: those differ on every run."""
    return hashlib.sha256(_OBJECT_ADDRESS.sub(b"", path.read_bytes())).hexdigest()


def run_scenario(
    name: str,
    staged: Path,
    work: Path,
    cache_dir: Path | None,
    export_format: str,
    quality: str | None,
    timeout_seconds: int,
) -> dict[str, Any]:
    # Like production's hydrate_project_files, every compile gets a fresh,
    # randomly named project folder, so absolute paths never repeat.
    work.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="tertius-project-", dir=work)) / "project"
    shutil.copytree(staged, work)
    started = perf_counter()
    result = run_compile_sandbox(
        work,
        export_format,
        quality=quality,
        timeout_seconds=timeout_seconds,
        component_cache_dir=cache_dir,
        early_result=True,
        scene_export=cache_dir is not None,
    )
    # What the user waits for: the result, before new cache entries are written.
    wall = perf_counter() - started
    if result.finish is not None:
        result.finish()
    cache_write = perf_counter() - started - wall
    if not result.success:
        raise SystemExit(f"{name} compile failed:\n{(result.error or result.stderr)[-4000:]}")
    report = (
        json.loads(result.component_report_path.read_text(encoding="utf-8"))
        if result.component_report_path
        else {"counts": {}, "components": []}
    )
    phases = json.loads(result.phases_path.read_text(encoding="utf-8")) if result.phases_path else {}
    components = {
        item["component"]: item["cache_status"] for item in report["components"]
    }
    return {
        "scenario": name,
        "wall_seconds": round(wall, 2),
        "cache_write_seconds": round(cache_write, 2),
        "phases": phases,
        "counts": report["counts"],
        "rebuilt": sorted(name for name, status in components.items() if status == "built"),
        "uncacheable": sorted(name for name, status in components.items() if status == "uncacheable"),
        "part_numbers": {
            item["component"]: item["part_number"] for item in report["components"] if item["part_number"]
        },
        # A cached compile must produce the same workbench artifacts as a
        # full build; compare these digests across scenarios.
        "artifact_sha256": {
            kind: hashlib.sha256(path.read_bytes()).hexdigest()
            for kind, path in sorted(result.artifact_paths.items())
        },
        "output_sha256": hashlib.sha256(result.output_path.read_bytes()).hexdigest() if result.output_path else None,
        "output_fingerprint": output_fingerprint(result.output_path) if result.output_path else None,
        "output_bytes": result.output_path.stat().st_size if result.output_path else 0,
        "cache_bytes": sum(path.stat().st_size for path in cache_dir.rglob("*") if path.is_file()) if cache_dir else 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--part", action="append", default=[], help="top-level builder to mark with @part: name or module.py:name"
    )
    parser.add_argument("--unrelated-edit", action="append", default=[])
    parser.add_argument("--related-edit", action="append", default=[])
    parser.add_argument("--export-format", default="glb")
    parser.add_argument("--quality", default=None)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--skip-cold", action="store_true", help="reuse the existing cache folder")
    parser.add_argument("--baseline", action="store_true", help="first compile the unmarked project without a cache")
    args = parser.parse_args(argv)

    output = args.output_dir.resolve()
    staged = output / "staged"
    cache_dir = output / "component-cache"
    stage_project(args.project_dir.resolve(), staged)
    marked = mark_parts(staged, args.part)
    if not args.skip_cold and cache_dir.exists():
        shutil.rmtree(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    def run(name: str, source: Path = staged, cache: Path | None = cache_dir) -> dict[str, Any]:
        outcome = run_scenario(
            name, source, output / "work", cache, args.export_format, args.quality, args.timeout_seconds
        )
        print(
            json.dumps(
                {
                    key: outcome[key]
                    for key in (
                        "scenario", "wall_seconds", "cache_write_seconds", "phases", "counts",
                        "rebuilt", "uncacheable", "artifact_sha256", "output_fingerprint",
                    )
                }
            ),
            file=sys.stderr,
        )
        return outcome

    scenarios = []
    if args.baseline:
        unmarked = output / "unmarked"
        stage_project(args.project_dir.resolve(), unmarked)
        scenarios.append(run("baseline", unmarked, None))
    if not args.skip_cold:
        scenarios.append(run("cold"))
    scenarios.append(run("unchanged"))
    if args.unrelated_edit:
        for edit in args.unrelated_edit:
            apply_edit(staged, edit)
        scenarios.append(run("unrelated-edit"))
    if args.related_edit:
        for edit in args.related_edit:
            apply_edit(staged, edit)
        scenarios.append(run("related-edit"))
    print(json.dumps({"marked_parts": marked, "scenarios": scenarios}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
