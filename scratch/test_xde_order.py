import sys
from pathlib import Path
import build123d as bd
import json
import struct

proj_dir = Path("c:/Users/ben/Documents/Projects/Tertius-Web/cache/tertius/intus/3x5shed")
sys.path.insert(0, str(proj_dir))

env = {"bd": bd}
design_code = (proj_dir / "design.py").read_text(encoding="utf-8")
exec(design_code, env)

shapes = []
for val in env.values():
    if isinstance(val, bd.Shape) and hasattr(val, "volume"):
        g_type = val.geom_type() if callable(val.geom_type) else val.geom_type
        geom_name = getattr(g_type, "name", str(g_type)).upper()
        if geom_name in ("SOLID", "COMPOUND", "OTHER"):
            shapes.append(val)

compound = bd.Compound(children=shapes)

from build123d.exporters3d import _create_xde
from OCP.XCAFDoc import XCAFDoc_DocumentTool
from OCP.TCollection import TCollection_AsciiString
from OCP.TDF import TDF_Tool

# 1. Export GLTF FIRST
out_glb = Path("scratch/test_order.glb")
bd.export_gltf(compound, str(out_glb), binary=True)

# 2. Extract tags SECOND
doc1 = _create_xde(compound, bd.Unit.MM)
shape_tool1 = XCAFDoc_DocumentTool.ShapeTool_s(doc1.Main())

t1 = []
for node in bd.PreOrderIter(compound):
    if node.label:
        inst_label = shape_tool1.FindShape(node.wrapped, findInstance=True)
        if inst_label.IsNull():
            inst_label = shape_tool1.FindShape(node.wrapped, findInstance=False)
        if not inst_label.IsNull():
            entry = TCollection_AsciiString()
            TDF_Tool.Entry_s(inst_label, entry)
            t1.append(f"=>[{entry.ToCString()}]")

# 3. Read GLTF tags
with open(out_glb, "rb") as f:
    data = f.read()
magic, version, length = struct.unpack("<4sII", data[:12])
chunk_len, chunk_type = struct.unpack("<II", data[12:20])
json_data = data[20:20+chunk_len].decode("utf-8")
gltf_json = json.loads(json_data)

glb_names = [n["name"] for n in gltf_json["nodes"] if "name" in n]

print("Extracted tags:")
print(t1[:5])
print("\nGLB tags:")
print(glb_names[:5])
print("\nAre they the exact same?", set(t1).issubset(set(glb_names)))
