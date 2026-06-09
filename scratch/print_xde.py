import build123d as bd
import sys
sys.path.insert(0, 'cache/tertius/intus/3x5shed')
from design import building
from build123d.exporters3d import _create_xde
from OCP.XCAFDoc import XCAFDoc_DocumentTool
from OCP.TDF import TDF_ChildIterator, TDF_Tool
from OCP.TCollection import TCollection_AsciiString
from OCP.TDataStd import TDataStd_Name

building.location *= bd.Location((0,0,0),(1,0,0),-90)
doc = _create_xde(building, bd.Unit.MM)
st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())

def print_tree(lbl, d=0):
    entry = TCollection_AsciiString()
    TDF_Tool.Entry_s(lbl, entry)
    name = ""
    if XCAFDoc_DocumentTool.IsShape_s(lbl):
        na = TDataStd_Name()
        if lbl.FindAttribute(TDataStd_Name.GetID_s(), na):
            name = na.Get().ToExtString()
            
    print("  " * d + entry.ToCString() + " " + name)
    
    it = TDF_ChildIterator(lbl)
    while it.More():
        print_tree(it.Value(), d+1)
        it.Next()

print_tree(doc.Main())
