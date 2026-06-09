import sys
from pathlib import Path

# Add project root to sys.path so we can import build123d normally if needed, or we just run the user's design.py
proj_dir = Path("c:/Users/ben/Documents/Projects/Tertius-Web/cache/tertius/intus/3x5shed")
sys.path.insert(0, str(proj_dir))

from OCP.XCAFDoc import XCAFDoc_DocumentTool
from OCP.TCollection import TCollection_AsciiString
from OCP.TDF import TDF_Tool
from build123d.exporters3d import _create_xde
import build123d as bd

# Load the design
import design

compound = design.show_object.final_shape if hasattr(design, "show_object") else None
if not compound:
    print("Could not find compound in design.py")
    sys.exit(1)

# Mimic the post-processing logic
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
            print(f"Found node with label '{node.label}', mapped to =>[{entry.ToCString()}]")
        else:
            print(f"Node with label '{node.label}' has NO XDE inst_label!")

print("\nFinal tag_to_name mapping:")
print(tag_to_name)
