import build123d as bd
from fpdf import FPDF
import tempfile
import os
import traceback

try:
    b = bd.Box(10, 10, 10)
    exporter = bd.ExportSVG(scale=1.0)
    exporter.add_shape(b)
    
    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
        tmp_path = f.name
    exporter.write(tmp_path)
    
    with open(tmp_path, "r", encoding="utf-8") as f:
        svg_content = f.read()
    print("ORIGINAL SVG:")
    print(svg_content[:200])
    os.remove(tmp_path)
    
    import re
    # Remove width and height
    svg_content2 = re.sub(r'(<svg[^>]*?)\s+width="[^"]*"', r'\1', svg_content)
    svg_content2 = re.sub(r'(<svg[^>]*?)\s+height="[^"]*"', r'\1', svg_content2)
    
    print("\nMODIFIED SVG:")
    print(svg_content2[:200])
    
    pdf = FPDF()
    pdf.add_page()
    
    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
        f.write(svg_content2.encode("utf-8"))
        mod_path = f.name
        
    try:
        pdf.image(mod_path, w=100, h=100)
        print("\nSuccessfully parsed modified SVG!")
    except Exception as e:
        print(f"\nFailed to parse modified SVG: {e}")
        traceback.print_exc()
        
    os.remove(mod_path)
    
    # Let's try original SVG
    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
        f.write(svg_content.encode("utf-8"))
        orig_path = f.name
        
    try:
        pdf.image(orig_path, w=100, h=100)
        print("\nSuccessfully parsed original SVG!")
    except Exception as e:
        print(f"\nFailed to parse original SVG: {e}")
        traceback.print_exc()
        
    os.remove(orig_path)
    
except Exception as e:
    traceback.print_exc()
