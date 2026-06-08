import build123d as bd
from fpdf import FPDF
from pathlib import Path

# Use simple box instead of missing shed design
b1 = bd.Box(10, 10, 10)
b2 = bd.Cylinder(3, 15)
c = b1 + b2
bbox = c.bounding_box()
look_at = bbox.center()

pdf = FPDF(orientation="landscape", unit="mm", format="A4")
pdf.add_page()

def projected_bounds(edges):
    points = []
    for edge in edges:
        g_type = edge_geom_name(edge)
        n_samples = 2 if g_type == "LINE" else 30
        points.extend(edge.position_at(t / (n_samples - 1)) for t in range(n_samples))
    if not points:
        return look_at.X, look_at.Y
    min_x = min(point.X for point in points)
    max_x = max(point.X for point in points)
    min_y = min(point.Y for point in points)
    max_y = max(point.Y for point in points)
    return (min_x + max_x) / 2, (min_y + max_y) / 2

def projected_edges(shape_list):
    return [shape for shape in shape_list if hasattr(shape, "position_at")]

def edge_geom_name(edge):
    geom_type = edge.geom_type() if callable(edge.geom_type) else edge.geom_type
    return getattr(geom_type, "name", str(geom_type))

def draw_view(comp, view_name, ox, oy, w, h, scale):
    if view_name == "top":
        origin = (look_at.X, look_at.Y, bbox.max.Z + 1000)
        up = (0, 1, 0)
    elif view_name == "front":
        origin = (look_at.X, bbox.min.Y - 1000, look_at.Z)
        up = (0, 0, 1)
    else:
        origin = (bbox.max.X + 1000, look_at.Y, look_at.Z)
        up = (0, 0, 1)
        
    visible, hidden = comp.project_to_viewport(origin, up, look_at)
    
    # We must compute a combined bounding box of the projected visible shape
    # to center it in our PDF rectangle.
    visible_edges = projected_edges(visible)
    hidden_edges = projected_edges(hidden)
    cx, cy = projected_bounds(visible_edges)
    
    screen_cx = ox + w/2
    screen_cy = oy + h/2
    
    # Visible edges
    pdf.set_line_width(0.3)
    pdf.set_draw_color(15, 15, 17)
    for edge in visible_edges:
        g_type = edge_geom_name(edge)
        n_samples = 2 if g_type == "LINE" else 30
        pts = [edge.position_at(t/(n_samples-1)) for t in range(n_samples)]
        for i in range(len(pts)-1):
            p1 = pts[i]
            p2 = pts[i+1]
            x1 = screen_cx + (p1.X - cx) * scale
            y1 = screen_cy - (p1.Y - cy) * scale
            x2 = screen_cx + (p2.X - cx) * scale
            y2 = screen_cy - (p2.Y - cy) * scale
            pdf.line(x1, y1, x2, y2)
            
    # Hidden edges
    if hidden_edges:
        pdf.set_line_width(0.15)
        pdf.set_draw_color(150, 150, 150)
        for edge in hidden_edges:
            g_type = edge_geom_name(edge)
            n_samples = 2 if g_type == "LINE" else 30
            pts = [edge.position_at(t/(n_samples-1)) for t in range(n_samples)]
            for i in range(len(pts)-1):
                p1 = pts[i]
                p2 = pts[i+1]
                x1 = screen_cx + (p1.X - cx) * scale
                y1 = screen_cy - (p1.Y - cy) * scale
                x2 = screen_cx + (p2.X - cx) * scale
                y2 = screen_cy - (p2.Y - cy) * scale
                pdf.line(x1, y1, x2, y2)

draw_view(c, "top", 20, 30, 100, 75, 1.0)
draw_view(c, "front", 20, 120, 100, 75, 1.0)

output_path = Path("cache/test_draw.pdf")
output_path.parent.mkdir(parents=True, exist_ok=True)
pdf.output(output_path)
print("SUCCESS!")
