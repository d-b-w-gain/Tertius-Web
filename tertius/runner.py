from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any

import build123d as bd

from ._canonical import canonical_json_bytes
from .projections import all_workbench_projections
from .session import TertiusRuntimeError, compile_session


COMPILED_DESIGN_FILENAME = "tertius-compiled-design.json"
WORKBENCH_FILENAMES = {
    "compiled_design": COMPILED_DESIGN_FILENAME,
    "procurement": "tertius-procurement.json",
    "structural": "tertius-structural.json",
    "drawing": "tertius-drawing.json",
    "bounds": "tertius-bounds.json",
}


@dataclass(frozen=True)
class DesignExecution:
    model: bd.Shape
    compiled_design: dict[str, Any]
    projections: dict[str, dict[str, Any]]
    namespace: dict[str, Any]


def _reject_reserved_project_runtime(project_dir: Path) -> None:
    reserved = [
        project_dir / "tertius.py",
        project_dir / "tertius",
        project_dir / "tertius_bom.py",
        project_dir / "tertius_structural.py",
    ]
    present = [path.name for path in reserved if path.exists()]
    if present:
        raise TertiusRuntimeError(
            "project source may not define the reserved Tertius runtime namespace: "
            + ", ".join(sorted(present))
        )


def execute_design(project_dir: Path) -> DesignExecution:
    project_dir = Path(project_dir).resolve()
    _reject_reserved_project_runtime(project_dir)
    design_file = project_dir / "design.py"
    if not design_file.is_file():
        raise TertiusRuntimeError("design.py not found in project")

    namespace: dict[str, Any] = {
        "__name__": "__tertius_design__",
        "__file__": str(design_file),
        "bd": bd,
        "build123d": bd,
    }
    added_path = str(project_dir) not in sys.path
    if added_path:
        sys.path.insert(1, str(project_dir))
    try:
        with compile_session() as session:
            source = compile(design_file.read_text(encoding="utf-8"), str(design_file), "exec")
            exec(source, namespace)
            removed_exports = sorted(
                name for name in namespace if str(name).startswith("TERTIUS_")
            )
            if removed_exports:
                raise TertiusRuntimeError(
                    "design.py uses removed Tertius manifest exports: "
                    + ", ".join(removed_exports)
                    + ". Tertius now finalizes workbench data automatically from model."
                )
            model = namespace.get("model")
            if not isinstance(model, bd.Shape):
                raise TertiusRuntimeError(
                    "design.py must assign the final Build123D Shape to `model`"
                )
            compiled_design = session.finalize(model)
            projections = all_workbench_projections(compiled_design, model=model)
    finally:
        if added_path:
            sys.path.remove(str(project_dir))
    return DesignExecution(
        model=model,
        compiled_design=compiled_design,
        projections=projections,
        namespace=namespace,
    )


def write_design_bundle(
    output_dir: Path,
    execution: DesignExecution,
) -> dict[str, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    documents = {
        "compiled_design": execution.compiled_design,
        **execution.projections,
    }
    paths: dict[str, Path] = {}
    for kind, document in documents.items():
        path = output_dir / WORKBENCH_FILENAMES[kind]
        path.write_bytes(canonical_json_bytes(document))
        paths[kind] = path
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Execute one Tertius mechanical design")
    parser.add_argument("project", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        execution = execute_design(args.project)
        outputs = write_design_bundle(args.output_dir or args.project, execution)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "artifacts": {kind: str(path) for kind, path in outputs.items()},
                "digest": execution.compiled_design["compiled_design_digest"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
