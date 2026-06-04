#!/usr/bin/env python3
import ast
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(title="Artus Feature Tree Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CACHE_ROOT = Path(__file__).parent.parent.parent.parent / 'cache' / 'tertius'
ACTIVE_PROJECT = CACHE_ROOT / 'active_project.txt'

def get_active_script() -> Path:
    if ACTIVE_PROJECT.exists():
        script_path = Path(ACTIVE_PROJECT.read_text(encoding="utf-8").strip())
        if script_path.exists():
            return script_path
    return None

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

@app.get("/features")
def get_features():
    script_file = get_active_script()
    if not script_file:
        return JSONResponse(status_code=404, content={"error": "No active project found. Compile something in Intus first!"})
    
    project_name = script_file.parent.name
    
    try:
        content = script_file.read_text(encoding="utf-8")
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

class UpdateRequest(BaseModel):
    updates: dict

@app.post("/update_features")
def update_features(req: UpdateRequest):
    script_file = get_active_script()
    if not script_file:
        return JSONResponse(status_code=404, content={"error": "No active project found."})
        
    try:
        content = script_file.read_text(encoding="utf-8")
        lines = content.splitlines()
        tree = ast.parse(content)
        
        assignments = {}
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
                old_val = node.value.value
                
                start_col = getattr(node.value, "col_offset", None)
                end_col = getattr(node.value, "end_col_offset", None)
                
                if start_col is not None and end_col is not None:
                    # preserve type for string vs float
                    if isinstance(old_val, str):
                        new_str = repr(str(new_val))
                    elif isinstance(old_val, float):
                        new_str = str(float(new_val))
                    elif isinstance(old_val, int):
                        new_str = str(int(new_val))
                    elif isinstance(old_val, bool):
                        # Convert JS boolean string to python bool string if needed
                        b_val = str(new_val).lower() == 'true'
                        new_str = "True" if b_val else "False"
                    else:
                        new_str = str(new_val)
                        
                    lines[line_idx] = raw_line[:start_col] + new_str + raw_line[end_col:]
                    
        script_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return {"success": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

class AIRequest(BaseModel):
    prompt: str

@app.post("/ai_modify")
def ai_modify(req: AIRequest):
    # Skeleton endpoint for Phase 2
    return {"success": True, "message": "AI modification is not yet implemented"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8893)
