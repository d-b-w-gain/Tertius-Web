#!/usr/bin/env python3
from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.auth import get_auth_context
from core.auth_types import AuthContext
from core.db import get_db
from core.models import Project, ProjectFile, UserWorkspaceState
from core.repositories import ProjectRepository

app = FastAPI(title="Artus Feature Tree Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_active_design_code(db: Session, ctx: AuthContext) -> tuple[str, str] | None:
    state = db.scalar(
        select(UserWorkspaceState).where(
            UserWorkspaceState.user_id == ctx.user_id,
            UserWorkspaceState.tenant_id == ctx.tenant_id,
        )
    )
    if state is None or state.active_project_id is None:
        return None

    project = db.scalar(
        select(Project).where(
            Project.tenant_id == ctx.tenant_id,
            Project.id == state.active_project_id,
        )
    )
    if project is None:
        return None

    file_row = db.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == ctx.tenant_id,
            ProjectFile.project_id == project.id,
            ProjectFile.filename == "design.py",
        )
    )
    if file_row is None:
        return None
    return project.name, file_row.content

@app.get("/health")
def health():
    return {"status": "ok"}

def unparse_expr(node):
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except:
        return "<?>"

def extract_dependencies(node):
    deps = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            deps.add(child.id)
    return [d for d in deps if d not in ('bd', 'build123d', 'Align', 'Mode', 'MIN', 'CENTER', 'MAX', 'SUBTRACT', 'ADD', 'INTERSECT')]


STANDARD_BOM_FIELDS = {
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
    "bracket_type",
}


@dataclass
class FunctionSignature:
    name: str
    params: list[str]
    filename: str
    line: int


def compact_expr(node: ast.AST) -> dict[str, Any]:
    if isinstance(node, ast.Constant):
        return {"kind": "literal", "value": node.value}
    if isinstance(node, ast.Name):
        return {"kind": "reference", "name": node.id}
    try:
        return {"kind": "expression", "source": ast.unparse(node)}
    except Exception:
        return {"kind": "expression", "source": ast.dump(node)}


def call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def infer_bom_kind(function_name: str) -> str:
    name = function_name.lower()
    if "fastener" in name:
        return "fastener_assembly"
    if "purlin" in name or "fascia" in name or "column" in name or "rafter" in name:
        return "structural_member"
    if "bracket" in name or "gpb" in name or "cp" in name:
        return "bracket"
    if "block" in name:
        return "block"
    if "rebar" in name:
        return "rebar"
    if "foundation" in name:
        return "foundation"
    return "component"


def standardize_bom_inputs(parameters: dict[str, Any]) -> dict[str, Any]:
    standard: dict[str, Any] = {}
    for key, value in parameters.items():
        out_key = key
        if key == "length":
            out_key = "length_mm"
        elif key == "grip_length":
            out_key = "grip_length_mm"
        elif key == "bracket_type":
            out_key = "part_number"
        if out_key in STANDARD_BOM_FIELDS:
            standard[out_key] = value
    return standard


def bom_readiness(function_name: str, parameters: dict[str, Any]) -> dict[str, Any]:
    kind = infer_bom_kind(function_name)
    standard_inputs = standardize_bom_inputs(parameters)
    keys = set(standard_inputs)
    missing = []
    if kind == "fastener_assembly":
        if "size" not in keys:
            missing.append("size")
        if "length_mm" not in keys:
            missing.append("length_mm")
    elif kind == "structural_member":
        if "part_number" not in keys and "product_key" not in keys:
            missing.append("part_number")
        if "length_mm" not in keys:
            missing.append("length_mm")
    elif kind == "bracket":
        if "part_number" not in keys and "product_key" not in keys:
            missing.append("part_number")

    if not missing and standard_inputs:
        level = "ok"
    elif standard_inputs:
        level = "warning"
    else:
        level = "info"

    return {
        "level": level,
        "kind": kind,
        "missing": missing,
        "standard_inputs": standard_inputs,
    }


def should_record_bom_call(function_name: str, parameters: dict[str, Any], signature: FunctionSignature | None) -> bool:
    if function_name.startswith("make_"):
        return True
    if infer_bom_kind(function_name) != "component":
        return True
    if signature and set(parameters) & STANDARD_BOM_FIELDS:
        return True
    if standardize_bom_inputs(parameters):
        return True
    return False


def collect_function_signatures(files: dict[str, str]) -> dict[str, FunctionSignature]:
    signatures: dict[str, FunctionSignature] = {}
    for filename, content in files.items():
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                signatures[node.name] = FunctionSignature(
                    name=node.name,
                    params=[arg.arg for arg in node.args.args],
                    filename=filename,
                    line=node.lineno,
                )
    return signatures


def collect_local_import_closure(files: dict[str, str], entrypoint: str = "design.py") -> list[str]:
    visited: set[str] = set()
    ordered: list[str] = []

    def visit_file(filename: str) -> None:
        if filename in visited or filename not in files:
            return
        visited.add(filename)
        ordered.append(filename)
        try:
            tree = ast.parse(files[filename])
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
                if candidate in files:
                    visit_file(candidate)

    visit_file(entrypoint)
    return ordered


def extract_bom_metadata(project_name: str, files: dict[str, str]) -> dict[str, Any]:
    signatures = collect_function_signatures(files)
    calls = []
    labels = []
    source_files = collect_local_import_closure(files)

    for source_file in source_files:
        try:
            tree = ast.parse(files.get(source_file, ""))
        except SyntaxError:
            continue

        scope: list[str] = []
        seen_calls: set[tuple[str, int, int]] = set()

        def visit(node: ast.AST):
            if isinstance(node, ast.FunctionDef):
                scope.append(node.name)
                for child in ast.iter_child_nodes(node):
                    visit(child)
                scope.pop()
                return

            if isinstance(node, ast.Call):
                key = (source_file, node.lineno, node.col_offset)
                if key not in seen_calls:
                    seen_calls.add(key)
                    name = call_name(node.func)
                    short_name = name.rsplit(".", 1)[-1] if name else ""
                    signature = signatures.get(short_name)

                    parameters: dict[str, Any] = {}
                    if signature:
                        for index, arg in enumerate(node.args):
                            param = signature.params[index] if index < len(signature.params) else f"arg_{index}"
                            parameters[param] = compact_expr(arg)
                    else:
                        for index, arg in enumerate(node.args):
                            parameters[f"arg_{index}"] = compact_expr(arg)
                    for keyword in node.keywords:
                        if keyword.arg:
                            parameters[keyword.arg] = compact_expr(keyword.value)

                    if name in {"bd.Compound", "Compound"}:
                        label = parameters.get("label")
                        if label and label.get("kind") == "literal":
                            labels.append({
                                "label": label.get("value"),
                                "sourceFile": source_file,
                                "line": node.lineno,
                                "scope": "::".join(scope) or "<module>",
                            })

                    if should_record_bom_call(short_name, parameters, signature):
                        readiness = bom_readiness(short_name, parameters)
                        calls.append({
                            "function": short_name,
                            "sourceFile": source_file,
                            "scope": "::".join(scope) or "<module>",
                            "line": node.lineno,
                            "parameters": parameters,
                            "standardInputs": readiness["standard_inputs"],
                            "bomKind": readiness["kind"],
                            "bomReadiness": readiness["level"],
                            "bomMissingFields": readiness["missing"],
                            "signatureSource": {
                                "filename": signature.filename,
                                "line": signature.line,
                            } if signature else None,
                        })

            for child in ast.iter_child_nodes(node):
                visit(child)

        visit(tree)

    return {
        "projectName": project_name,
        "source": "design.py",
        "sourceFiles": source_files,
        "calls": calls,
        "labels": labels,
        "standardFields": sorted(STANDARD_BOM_FIELDS),
    }

def parse_tree(node):
    nodes = []
    if isinstance(node, ast.With):
        for item in node.items:
            ctx = item.context_expr
            name = unparse_expr(ctx)
            if name.startswith("bd."):
                name = name[3:]
                
            as_name = None
            if item.optional_vars and isinstance(item.optional_vars, ast.Name):
                as_name = item.optional_vars.id
                
            children = []
            for stmt in node.body:
                children.extend(parse_tree(stmt))
            nodes.append({
                "type": "Context",
                "name": name,
                "as_name": as_name,
                "children": children
            })
    elif isinstance(node, ast.FunctionDef):
        children = []
        for stmt in node.body:
            children.extend(parse_tree(stmt))
        nodes.append({
            "type": "Context",
            "name": f"def {node.name}()",
            "as_name": None,
            "children": children
        })
    elif isinstance(node, ast.Expr):
        if isinstance(node.value, ast.Call):
            call = node.value
            if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name) and call.func.value.id == "bd":
                name = call.func.attr
                args = [unparse_expr(arg) for arg in call.args]
                for kw in call.keywords:
                    args.append(f"{kw.arg}={unparse_expr(kw.value)}")
                deps = extract_dependencies(call)
                nodes.append({
                    "type": "Operation",
                    "name": name,
                    "arguments": args,
                    "dependencies": deps
                })
    return nodes

@app.post("/projects/{name}/activate")
def activate_project(name: str, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    from core.repositories import ProjectRepository
    from fastapi.responses import JSONResponse
    repo = ProjectRepository(db, ctx.tenant_id)
    project = repo.get_project(name)
    if not project:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    repo.set_active_project(ctx.user_id, project.id)
    db.commit()
    return {"success": True}

@app.get("/features")
def get_features(ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    active_design = get_active_design_code(db, ctx)
    if active_design is None:
        return JSONResponse(status_code=404, content={"error": "No active project found. Compile something in Intus first!"})

    project_name, content = active_design
    
    try:
        lines = content.splitlines()
        tree = ast.parse(content)
        
        variables = []
        operations = []
        
        for node in tree.body:
            # 1. Extract parametric variables
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        if isinstance(node.value, ast.Constant):
                            val = node.value.value
                            t = type(val).__name__
                            if t in ("int", "float", "str", "bool"):
                                line_idx = node.lineno - 1
                                raw_line = lines[line_idx]
                                comment = ""
                                if "#" in raw_line:
                                    comment = raw_line.split("#", 1)[1].strip()
                                variables.append({
                                    "name": target.id,
                                    "value": val,
                                    "type": t,
                                    "description": comment
                                })
            
            # 2. Extract Geometric Operations (Contexts and Top-level Exprs)
            operations.extend(parse_tree(node))
            
        return {"project_name": project_name, "features": variables, "operations": operations}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/bom_metadata")
def get_bom_metadata(ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    active_design = get_active_design_code(db, ctx)
    if active_design is None:
        return JSONResponse(status_code=404, content={"error": "No active project found."})

    project_name, _content = active_design
    files = ProjectRepository(db, ctx.tenant_id).files_for_runtime(project_name)
    if files is None:
        return JSONResponse(status_code=404, content={"error": "Project not found"})

    try:
        return extract_bom_metadata(project_name, files)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

class UpdateRequest(BaseModel):
    updates: dict

@app.post("/update_features")
def update_features(
    req: UpdateRequest,
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    active_design = get_active_design_code(db, ctx)
    if active_design is None:
        return JSONResponse(status_code=404, content={"error": "No active project found."})

    project_name, content = active_design
        
    try:
        lines = content.splitlines()
        tree = ast.parse(content)
        
        assignments: dict[str, ast.Assign] = {}
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        if isinstance(node.value, ast.Constant):
                            assignments[target.id] = node

        for key, new_val in req.updates.items():
            if key in assignments:
                node = assignments[key]
                line_idx = node.lineno - 1
                raw_line = lines[line_idx]
                assert isinstance(node.value, ast.Constant)
                old_val = node.value.value
                
                start_col = getattr(node.value, "col_offset", None)
                end_col = getattr(node.value, "end_col_offset", None)
                
                if start_col is not None and end_col is not None:
                    # preserve type for string vs float
                    if isinstance(old_val, str):
                        new_str = repr(str(new_val))
                    elif isinstance(old_val, bool):
                        # Convert JS boolean string to python bool string if needed
                        b_val = str(new_val).lower() == 'true'
                        new_str = "True" if b_val else "False"
                    elif isinstance(old_val, float):
                        new_str = str(float(new_val))
                    elif isinstance(old_val, int):
                        new_str = str(int(new_val))
                    else:
                        new_str = str(new_val)
                        
                    lines[line_idx] = raw_line[:start_col] + new_str + raw_line[end_col:]
                    
        saved = ProjectRepository(db, ctx.tenant_id).save_code(
            project_name,
            "design.py",
            "\n".join(lines) + "\n",
            ctx.user_id,
            "Updated features via Artus",
        )
        if not saved:
            return JSONResponse(status_code=404, content={"error": "Project not found"})
        return {"success": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8893)
