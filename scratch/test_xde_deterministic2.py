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
for i, child in enumerate(compound.children):
    if hasattr(shapes[i], "label"):
        child.label = shapes[i].label

from build123d.exporters3d import _create_xde
from OCP.XCAFDoc import XCAFDoc_DocumentTool
from OCP.TCollection import TCollection_AsciiString
from OCP.TDF import TDF_Tool

doc1 = _create_xde(compound, bd.Unit.MM)
shape_tool1 = XCAFDoc_DocumentTool.ShapeTool_s(doc1.Main())

doc2 = _create_xde(compound, bd.Unit.MM)
shape_tool2 = XCAFDoc_DocumentTool.ShapeTool_s(doc2.Main())

t1, t2 = [], []

for node in bd.PreOrderIter(compound):
    if node.label:
        inst_label = shape_tool1.FindShape(node.wrapped, findInstance=True)
        if inst_label.IsNull():
            inst_label = shape_tool1.FindShape(node.wrapped, findInstance=False)
        if not inst_label.IsNull():
            entry = TCollection_AsciiString()
            TDF_Tool.Entry_s(inst_label, entry)
            t1.append(f"{node.label}: =>[{entry.ToCString()}]")

for node in bd.PreOrderIter(compound):
    if node.label:
        inst_label = shape_tool2.FindShape(node.wrapped, findInstance=True)
        if inst_label.IsNull():
            inst_label = shape_tool2.FindShape(node.wrapped, findInstance=False)
        if not inst_label.IsNull():
            entry = TCollection_AsciiString()
            TDF_Tool.Entry_s(inst_label, entry)
            t2.append(f"{node.label}: =>[{entry.ToCString()}]")

print("Match?", t1 == t2)
if t1 != t2:
    print("Tags doc1:")
    print(t1)
    print("Tags doc2:")
    print(t2)
