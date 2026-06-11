#!/usr/bin/env python3
"""Probe Intus design scripts for BoM-readable metadata signals.

This is a spike utility for #73. It intentionally uses Python AST only, so it
can inspect project scripts without importing build123d or executing design.py.
"""

from __future__ import annotations

import argparse
import ast
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


STANDARD_FIELDS = {
    "part_number",
    "product_key",
    "manufacturer",
    "standard",
    "size",
    "length",
    "length_mm",
    "width",
    "width_mm",
    "height",
    "height_mm",
    "thickness",
    "thickness_mm",
    "diameter",
    "diameter_mm",
    "grip_length",
    "grip_length_mm",
    "quantity",
    "unit",
    "role",
    "material",
    "finish",
    "source_library",
}

FUNCTION_KIND_KEYWORDS = {
    "fastener": "fastener_assembly",
    "bolt": "fastener",
    "nut": "fastener",
    "purlin": "structural_member",
    "bracket": "bracket",
    "gpb": "bracket",
    "cp": "bracket",
    "block": "block",
    "rebar": "rebar",
    "foundation": "foundation",
    "fascia": "structural_member",
    "column": "structural_member",
    "rafter": "structural_member",
}


@dataclass
class FunctionSignature:
    name: str
    params: list[str]
    path: str
    line: int


class SignatureCollector(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.signatures: dict[str, FunctionSignature] = {}

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        params = [arg.arg for arg in node.args.args]
        self.signatures[node.name] = FunctionSignature(
            name=node.name,
            params=params,
            path=str(self.path),
            line=node.lineno,
        )
        self.generic_visit(node)


def collect_signatures(project_dir: Path) -> dict[str, FunctionSignature]:
    signatures: dict[str, FunctionSignature] = {}
    for path in sorted(project_dir.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        collector = SignatureCollector(path)
        collector.visit(tree)
        signatures.update(collector.signatures)
    return signatures


def collect_local_import_closure(project_dir: Path, entrypoint: str = "design.py") -> list[Path]:
    visited: set[str] = set()
    ordered: list[Path] = []

    def visit_file(filename: str) -> None:
        path = project_dir / filename
        if filename in visited or not path.exists():
            return
        visited.add(filename)
        ordered.append(path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            return

        for node in ast.walk(tree):
            module_names: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                module_names.append(node.module.split(".", 1)[0])
            elif isinstance(node, ast.Import):
                module_names.extend(alias.name.split(".", 1)[0] for alias in node.names)

            for module_name in module_names:
                candidate = f"{module_name}.py"
                if (project_dir / candidate).exists():
                    visit_file(candidate)

    visit_file(entrypoint)
    return ordered


def call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def compact_expr(node: ast.AST) -> dict[str, Any]:
    if isinstance(node, ast.Constant):
        return {"kind": "literal", "value": node.value}
    if isinstance(node, ast.Name):
        return {"kind": "reference", "name": node.id}
    if isinstance(node, ast.List):
        return {"kind": "list", "items": [compact_expr(item) for item in node.elts]}
    if isinstance(node, ast.Tuple):
        return {"kind": "tuple", "items": [compact_expr(item) for item in node.elts]}
    try:
        return {"kind": "expression", "source": ast.unparse(node)}
    except Exception:
        return {"kind": "expression", "source": ast.dump(node)}


def infer_kind(name: str) -> str:
    lower = name.lower()
    for keyword, kind in FUNCTION_KIND_KEYWORDS.items():
        if keyword in lower:
            return kind
    return "component"


def readiness_for(function_name: str, params: dict[str, Any]) -> dict[str, Any]:
    keys = set(params)
    standard_keys = sorted(keys & STANDARD_FIELDS)
    kind = infer_kind(function_name)
    missing: list[str] = []

    if kind == "fastener_assembly":
        if "size" not in keys:
            missing.append("size")
        if "length_mm" not in keys and "length" not in keys:
            missing.append("length_mm")
    elif kind == "structural_member":
        if "part_number" not in keys and "product_key" not in keys:
            missing.append("part_number")
        if "length_mm" not in keys and "length" not in keys:
            missing.append("length_mm")
    elif kind == "bracket":
        if "part_number" not in keys and "product_key" not in keys and "bracket_type" not in keys:
            missing.append("part_number")

    if not missing and standard_keys:
        level = "ok"
    elif standard_keys:
        level = "warning"
    else:
        level = "info"

    return {
        "level": level,
        "kind": kind,
        "standard_fields": standard_keys,
        "missing_for_bom": missing,
    }


class DesignProbe(ast.NodeVisitor):
    def __init__(self, path: Path, signatures: dict[str, FunctionSignature]) -> None:
        self.path = path
        self.signatures = signatures
        self.scope: list[str] = []
        self.calls: list[dict[str, Any]] = []
        self.labels: list[dict[str, Any]] = []
        self.seen_calls: set[tuple[int, int]] = set()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_Assign(self, node: ast.Assign) -> None:
        if isinstance(node.value, ast.Call):
            self._record_call(node.value, self._targets(node.targets))
        self.generic_visit(node)

    def visit_Expr(self, node: ast.Expr) -> None:
        if isinstance(node.value, ast.Call):
            self._record_call(node.value, [])
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        self._record_call(node, [])
        self.generic_visit(node)

    def _targets(self, targets: list[ast.expr]) -> list[str]:
        names: list[str] = []
        for target in targets:
            if isinstance(target, ast.Name):
                names.append(target.id)
            elif isinstance(target, ast.Tuple):
                names.extend(item.id for item in target.elts if isinstance(item, ast.Name))
        return names

    def _record_call(self, node: ast.Call, targets: list[str]) -> None:
        call_key = (node.lineno, node.col_offset)
        if call_key in self.seen_calls:
            return
        self.seen_calls.add(call_key)

        name = call_name(node.func)
        if not name:
            return

        short_name = name.rsplit(".", 1)[-1]
        signature = self.signatures.get(short_name)
        params: dict[str, Any] = {}
        if signature:
            for index, arg in enumerate(node.args):
                if index < len(signature.params):
                    params[signature.params[index]] = compact_expr(arg)
                else:
                    params[f"arg_{index}"] = compact_expr(arg)
        else:
            for index, arg in enumerate(node.args):
                params[f"arg_{index}"] = compact_expr(arg)

        for keyword in node.keywords:
            if keyword.arg:
                params[keyword.arg] = compact_expr(keyword.value)

        label = None
        for keyword in node.keywords:
            if keyword.arg == "label" and isinstance(keyword.value, ast.Constant):
                label = keyword.value.value

        if name in {"bd.Compound", "Compound"} and label:
            self.labels.append(
                {
                    "label": label,
                    "line": node.lineno,
                    "scope": "::".join(self.scope) or "<module>",
                    "targets": targets,
                }
            )

        if not short_name.startswith("make_"):
            return

        readiness = readiness_for(short_name, params)
        self.calls.append(
            {
                "function": short_name,
                "sourceFile": self.path.name,
                "line": node.lineno,
                "scope": "::".join(self.scope) or "<module>",
                "targets": targets,
                "signature_source": {
                    "path": signature.path,
                    "line": signature.line,
                }
                if signature
                else None,
                "parameters": params,
                "bom_readiness": readiness,
            }
        )


def probe_design(path: Path) -> dict[str, Any]:
    project_dir = path.parent
    signatures = collect_signatures(project_dir)
    source_files = collect_local_import_closure(project_dir, path.name)
    calls: list[dict[str, Any]] = []
    labels: list[dict[str, Any]] = []

    for source_file in source_files:
        tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
        probe = DesignProbe(source_file, signatures)
        probe.visit(tree)
        calls.extend(probe.calls)
        labels.extend(probe.labels)

    summary: dict[str, int] = {}
    for call in calls:
        level = call["bom_readiness"]["level"]
        summary[level] = summary.get(level, 0) + 1

    return {
        "schemaVersion": 1,
        "source": str(path),
        "sourceFiles": [str(source_file) for source_file in source_files],
        "functionCallCount": len(calls),
        "labelCount": len(labels),
        "readinessSummary": summary,
        "standardFields": sorted(STANDARD_FIELDS),
        "calls": calls,
        "labels": labels,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe an Intus design.py for semantic BoM metadata signals.")
    parser.add_argument("design", type=Path, help="Path to design.py")
    parser.add_argument("--pretty", action="store_true", help="Print a readable summary instead of JSON")
    args = parser.parse_args()

    result = probe_design(args.design)
    if not args.pretty:
        print(json.dumps(result, indent=2))
        return 0

    print(f"Source: {result['source']}")
    print("Source files:")
    for source_file in result["sourceFiles"]:
        print(f"  {source_file}")
    print(f"Function calls: {result['functionCallCount']}")
    print(f"Labels: {result['labelCount']}")
    print(f"Readiness: {result['readinessSummary']}")
    print()
    for call in result["calls"]:
        readiness = call["bom_readiness"]
        targets = ", ".join(call["targets"]) or "-"
        params = ", ".join(call["parameters"].keys()) or "-"
        missing = ", ".join(readiness["missing_for_bom"]) or "-"
        print(
            f"{readiness['level'].upper():7} line {call['line']:>3} "
            f"{call['sourceFile']}::{call['scope']} -> {targets}: {call['function']}({params}) "
            f"kind={readiness['kind']} missing={missing}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
