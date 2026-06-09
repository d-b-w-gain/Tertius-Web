import sys
from pathlib import Path
import build123d as bd

proj_dir = Path("c:/Users/ben/Documents/Projects/Tertius-Web/cache/tertius/intus/3x5shed")
sys.path.insert(0, str(proj_dir))
import design

compound = design.show_object.final_shape

from build123d.exporters3d import _create_xde
from OCP.XCAFDoc import XCAFDoc_DocumentTool
from OCP.TCollection import TCollection_AsciiString
from OCP.TDF import TDF_Tool

doc1 = _create_xde(compound, bd.Unit.MM)
shape_tool1 = XCAFDoc_DocumentTool.ShapeTool_s(doc1.Main())

doc2 = _create_xde(compound, bd.Unit.MM)
shape_tool2 = XCAFDoc_DocumentTool.ShapeTool_s(doc2.Main())

print("Tags from doc1:")
for node in bd.PreOrderIter(compound):
    if node.label:
        inst_label = shape_tool1.FindShape(node.wrapped, findInstance=True)
        if inst_label.IsNull():
            inst_label = shape_tool1.FindShape(node.wrapped, findInstance=False)
        entry = TCollection_AsciiString()
        TDF_Tool.Entry_s(inst_label, entry)
        print(f"{node.label}: =>[{entry.ToCString()}]")

print("\nTags from doc2:")
for node in bd.PreOrderIter(compound):
    if node.label:
        inst_label = shape_tool2.FindShape(node.wrapped, findInstance=True)
        if inst_label.IsNull():
            inst_label = shape_tool2.FindShape(node.wrapped, findInstance=False)
        entry = TCollection_AsciiString()
        TDF_Tool.Entry_s(inst_label, entry)
        print(f"{node.label}: =>[{entry.ToCString()}]")
