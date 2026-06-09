import sys
from pathlib import Path
import build123d as bd

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
import json

doc = _create_xde(compound, bd.Unit.MM)
shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
tag_to_name = {}

for node in bd.PreOrderIter(compound):
    if node.label:
        inst_label = shape_tool.FindShape(node.wrapped, findInstance=True)
        if inst_label.IsNull():
            inst_label = shape_tool.FindShape(node.wrapped, findInstance=False)
            
        if not inst_label.IsNull():
            entry = TCollection_AsciiString()
            TDF_Tool.Entry_s(inst_label, entry)
            tag_to_name[f"=>[{entry.ToCString()}]"] = node.label

print(json.dumps(tag_to_name, indent=2))
