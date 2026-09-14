#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = REPOSITORY_ROOT / "server"
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from core.structural import build_source_inventory


def _pretty_print(inventory: dict) -> None:
    print(f"Entrypoint: {inventory['entrypoint']}")
    print(f"Closure digest: {inventory['closure_digest']}")
    print("Source closure:")
    for source in inventory["source_files"]:
        syntax = "ok" if source["syntax_valid"] else "syntax error"
        print(f"  {source['path']} ({source['bytes']} bytes, {syntax})")
    print(f"External imports: {', '.join(inventory['external_imports']) or '-'}")
    print(
        "Standard-library imports: "
        f"{', '.join(inventory['standard_library_imports']) or '-'}"
    )
    print("Python files outside the closure:")
    for path in inventory["out_of_closure_python_files"]:
        print(f"  {path}")
    if inventory["literal_file_references"]:
        print("Literal file references:")
        for reference in inventory["literal_file_references"]:
            state = (
                "present"
                if reference["exists"]
                else "missing"
                if reference["exists"] is False
                else "external/unknown"
            )
            print(
                f"  {reference['source_file']}:{reference['line']} "
                f"{reference['literal']} ({state})"
            )
    if inventory["diagnostics"]:
        print("Diagnostics:")
        for diagnostic in inventory["diagnostics"]:
            location = diagnostic.get("source_file", "<project>")
            if diagnostic.get("line"):
                location += f":{diagnostic['line']}"
            print(f"  {diagnostic['severity'].upper()} {location} {diagnostic['code']}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Inventory design.py and its transitive local imports without importing "
            "or executing project code."
        ),
    )
    parser.add_argument(
        "project",
        type=Path,
        help="Project directory, or a direct path to the Python entrypoint.",
    )
    parser.add_argument(
        "--entrypoint",
        default="design.py",
        help="Entrypoint relative to the project directory (default: design.py).",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Print a compact human-readable inventory instead of JSON.",
    )
    args = parser.parse_args()

    if args.project.is_file():
        project_dir = args.project.parent
        entrypoint = args.project.name
    else:
        project_dir = args.project
        entrypoint = args.entrypoint

    try:
        inventory = build_source_inventory(project_dir, entrypoint)
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        parser.error(str(exc))

    if args.pretty:
        _pretty_print(inventory)
    else:
        print(json.dumps(inventory, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
