from __future__ import annotations

import ast
import hashlib
import json
import os
import sys
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any

IGNORED_DIRECTORIES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "venv",
    }
)

FILE_REFERENCE_CALLS = frozenset(
    {
        "Path",
        "open",
        "pandas.read_csv",
        "pandas.read_excel",
        "pandas.read_json",
        "pd.read_csv",
        "pd.read_excel",
        "pd.read_json",
        "read_csv",
        "read_excel",
        "read_json",
    }
)

DYNAMIC_IMPORT_CALLS = frozenset(
    {
        "__import__",
        "import_module",
        "importlib.import_module",
    }
)


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _relative_path(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _is_within(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def _source_metadata(
    path: Path, root: Path, source: str, syntax_valid: bool
) -> dict[str, Any]:
    content = path.read_bytes()
    return {
        "path": _relative_path(path, root),
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "syntax_valid": syntax_valid,
        "line_count": len(source.splitlines()),
    }


def _project_python_files(root: Path) -> Iterable[Path]:
    for directory, child_directories, filenames in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        child_directories[:] = sorted(
            name
            for name in child_directories
            if name not in IGNORED_DIRECTORIES
            and not (directory_path / name).is_symlink()
        )
        for filename in sorted(filenames):
            if not filename.endswith(".py"):
                continue
            path = directory_path / filename
            if path.is_symlink():
                continue
            resolved = path.resolve()
            if _is_within(resolved, root):
                yield resolved


def _module_context(source_file: str) -> tuple[str, ...]:
    parts = PurePosixPath(source_file).with_suffix("").parts
    if parts and parts[-1] == "__init__":
        return tuple(parts[:-1])
    return tuple(parts[:-1])


def _module_parts(
    source_file: str,
    module: str | None,
    level: int,
) -> tuple[str, ...] | None:
    absolute_parts = tuple(part for part in (module or "").split(".") if part)
    if level == 0:
        return absolute_parts

    package = _module_context(source_file)
    if not package:
        return None
    parents_to_drop = level - 1
    if parents_to_drop > len(package):
        return None
    return (*package[: len(package) - parents_to_drop], *absolute_parts)


def _safe_existing_file(path: Path, root: Path) -> Path | None:
    if not path.is_file():
        return None
    resolved = path.resolve()
    if not _is_within(resolved, root):
        return None
    return resolved


def _resolve_local_module(root: Path, module_parts: tuple[str, ...]) -> list[Path]:
    if not module_parts:
        return []

    module_base = root.joinpath(*module_parts)
    module_file = _safe_existing_file(module_base.with_suffix(".py"), root)
    package_init = _safe_existing_file(module_base / "__init__.py", root)
    target = module_file or package_init
    if target is None:
        return []

    resolved: list[Path] = []
    prefix_limit = len(module_parts) if package_init else len(module_parts) - 1
    for index in range(1, prefix_limit + 1):
        parent_init = _safe_existing_file(
            root.joinpath(*module_parts[:index]) / "__init__.py",
            root,
        )
        if parent_init is not None and parent_init not in resolved:
            resolved.append(parent_init)
    if target not in resolved:
        resolved.append(target)
    return resolved


def _literal_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


class _ModuleCallVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node.func)
        if name:
            self.calls.append((name, node.lineno))
        self.generic_visit(node)


def _module_level_calls(tree: ast.Module, source_file: str) -> list[dict[str, Any]]:
    visitor = _ModuleCallVisitor()
    for statement in tree.body:
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        visitor.visit(statement)
    return [
        {"source_file": source_file, "line": line, "call": name}
        for name, line in sorted(visitor.calls, key=lambda item: (item[1], item[0]))
    ]


def _literal_file_references(
    tree: ast.Module,
    source_file: str,
    root: Path,
) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        call = _call_name(node.func)
        if not call:
            continue
        short_call = call.rsplit(".", 1)[-1]
        if call not in FILE_REFERENCE_CALLS and short_call not in FILE_REFERENCE_CALLS:
            continue
        value = _literal_string(node.args[0])
        if value is None or not value.strip():
            continue

        literal_path = Path(value)
        within_project = False
        exists: bool | None = None
        if not literal_path.is_absolute():
            candidate = (root / literal_path).resolve()
            within_project = _is_within(candidate, root)
            if within_project:
                exists = candidate.exists()

        references.append(
            {
                "source_file": source_file,
                "line": node.lineno,
                "call": call,
                "literal": value,
                "within_project": within_project,
                "exists": exists,
            }
        )
    return sorted(
        references,
        key=lambda item: (
            item["source_file"],
            item["line"],
            item["call"],
            item["literal"],
        ),
    )


def _import_nodes(tree: ast.Module) -> list[ast.Import | ast.ImportFrom | ast.Call]:
    nodes: list[ast.Import | ast.ImportFrom | ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            nodes.append(node)
            continue
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if _call_name(node.func) in DYNAMIC_IMPORT_CALLS and _literal_string(
            node.args[0]
        ):
            nodes.append(node)
    return sorted(nodes, key=lambda node: (node.lineno, node.col_offset))


def _record_import(
    *,
    root: Path,
    source_file: str,
    line: int,
    kind: str,
    module: str,
    module_parts: tuple[str, ...] | None,
    local_paths: list[Path],
    imports: list[dict[str, Any]],
    standard_library_imports: set[str],
    external_imports: set[str],
    diagnostics: list[dict[str, Any]],
) -> None:
    root_name = module.lstrip(".").split(".", 1)[0] if module.lstrip(".") else ""
    if local_paths:
        classification = "local"
    elif module_parts is None:
        classification = "unresolved_relative"
        diagnostics.append(
            {
                "code": "unresolved_relative_import",
                "severity": "error",
                "source_file": source_file,
                "line": line,
                "module": module,
            }
        )
    elif root_name in sys.stdlib_module_names:
        classification = "standard_library"
        standard_library_imports.add(root_name)
    else:
        classification = "external"
        if root_name:
            external_imports.add(root_name)

    imports.append(
        {
            "source_file": source_file,
            "line": line,
            "kind": kind,
            "module": module,
            "classification": classification,
            "local_files": [_relative_path(path, root) for path in local_paths],
        }
    )


def _read_source(path: Path) -> tuple[str, str | None]:
    try:
        return path.read_text(encoding="utf-8-sig"), None
    except UnicodeDecodeError as exc:
        return path.read_text(encoding="utf-8-sig", errors="replace"), str(exc)


def build_source_inventory(
    project_dir: Path,
    entrypoint: str = "design.py",
) -> dict[str, Any]:
    """Inventory a design.py import closure without importing or executing it."""

    root = project_dir.resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Project directory does not exist: {project_dir}")

    entrypoint_path = (root / entrypoint).resolve()
    if not _is_within(entrypoint_path, root):
        raise ValueError(
            f"Entrypoint must stay inside the project directory: {entrypoint}"
        )
    if not entrypoint_path.is_file():
        raise FileNotFoundError(f"Entrypoint does not exist: {entrypoint_path}")

    queue = [entrypoint_path]
    queued = {entrypoint_path}
    visited: set[Path] = set()
    source_files: list[dict[str, Any]] = []
    imports: list[dict[str, Any]] = []
    standard_library_imports: set[str] = set()
    external_imports: set[str] = set()
    module_calls: list[dict[str, Any]] = []
    file_references: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []

    while queue:
        path = queue.pop(0)
        if path in visited:
            continue
        visited.add(path)
        source_file = _relative_path(path, root)
        source, decode_error = _read_source(path)
        if decode_error:
            diagnostics.append(
                {
                    "code": "source_decode_error",
                    "severity": "error",
                    "source_file": source_file,
                    "message": decode_error,
                }
            )

        try:
            tree = ast.parse(source, filename=source_file)
            syntax_valid = True
        except SyntaxError as exc:
            syntax_valid = False
            diagnostics.append(
                {
                    "code": "syntax_error",
                    "severity": "error",
                    "source_file": source_file,
                    "line": exc.lineno,
                    "column": exc.offset,
                    "message": exc.msg,
                }
            )
            source_files.append(_source_metadata(path, root, source, syntax_valid))
            continue

        source_files.append(_source_metadata(path, root, source, syntax_valid))
        module_calls.extend(_module_level_calls(tree, source_file))
        file_references.extend(_literal_file_references(tree, source_file, root))

        for node in _import_nodes(tree):
            import_specs: list[tuple[str, str, tuple[str, ...] | None, list[Path]]] = []
            if isinstance(node, ast.Import):
                for alias in node.names:
                    parts = _module_parts(source_file, alias.name, 0)
                    local_paths = _resolve_local_module(root, parts or ())
                    import_specs.append(("import", alias.name, parts, local_paths))
            elif isinstance(node, ast.ImportFrom):
                dotted_module = f"{'.' * node.level}{node.module or ''}"
                parts = _module_parts(source_file, node.module, node.level)
                local_paths = (
                    _resolve_local_module(root, parts or ())
                    if parts is not None
                    else []
                )
                for alias in node.names:
                    if alias.name == "*" or parts is None:
                        continue
                    child_paths = _resolve_local_module(
                        root, (*parts, *alias.name.split("."))
                    )
                    for child_path in child_paths:
                        if child_path not in local_paths:
                            local_paths.append(child_path)
                import_specs.append(("from", dotted_module, parts, local_paths))
            else:
                module = _literal_string(node.args[0]) or ""
                parts = _module_parts(source_file, module, 0)
                local_paths = _resolve_local_module(root, parts or ())
                import_specs.append(("dynamic", module, parts, local_paths))

            for kind, module, parts, local_paths in import_specs:
                _record_import(
                    root=root,
                    source_file=source_file,
                    line=node.lineno,
                    kind=kind,
                    module=module,
                    module_parts=parts,
                    local_paths=local_paths,
                    imports=imports,
                    standard_library_imports=standard_library_imports,
                    external_imports=external_imports,
                    diagnostics=diagnostics,
                )
                for local_path in local_paths:
                    if local_path not in visited and local_path not in queued:
                        queued.add(local_path)
                        queue.append(local_path)

    source_files.sort(key=lambda item: item["path"])
    imports.sort(key=lambda item: (item["source_file"], item["line"], item["module"]))
    module_calls.sort(
        key=lambda item: (item["source_file"], item["line"], item["call"])
    )
    file_references.sort(
        key=lambda item: (
            item["source_file"],
            item["line"],
            item["call"],
            item["literal"],
        ),
    )
    diagnostics.sort(
        key=lambda item: (
            item.get("source_file", ""),
            item.get("line") or 0,
            item["code"],
        ),
    )

    closure_paths = {item["path"] for item in source_files}
    all_python_paths = {
        _relative_path(path, root) for path in _project_python_files(root)
    }
    out_of_closure = sorted(all_python_paths - closure_paths)
    digest_payload = [(item["path"], item["sha256"]) for item in source_files]
    closure_digest = hashlib.sha256(
        json.dumps(digest_payload, separators=(",", ":"), ensure_ascii=True).encode(),
    ).hexdigest()

    return {
        "schema_version": 1,
        "entrypoint": _relative_path(entrypoint_path, root),
        "closure_digest": closure_digest,
        "source_files": source_files,
        "imports": imports,
        "standard_library_imports": sorted(standard_library_imports),
        "external_imports": sorted(external_imports),
        "out_of_closure_python_files": out_of_closure,
        "literal_file_references": file_references,
        "module_level_calls": module_calls,
        "diagnostics": diagnostics,
    }
