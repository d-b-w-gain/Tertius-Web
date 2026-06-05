import build123d as bd
from fpdf import FPDF

# Use simple box instead of missing shed design
b1 = bd.Box(10, 10, 10)
b2 = bd.Cylinder(3, 15)
c = b1 + b2
bbox = c.bounding_box()
look_at = bbox.center()

pdf = FPDF(orientation="landscape", unit="mm", format="A4")
pdf.add_page()

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
    v_bbox = visible.bounding_box()
    cx = v_bbox.center().X
    cy = v_bbox.center().Y
    
    screen_cx = ox + w/2
    screen_cy = oy + h/2
    
    # Visible edges
    pdf.set_line_width(0.3)
    pdf.set_draw_color(15, 15, 17)
    for edge in visible.edges():
        g_type = getattr(edge.geom_type(), "name", str(edge.geom_type()))
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
    if hidden:
        pdf.set_line_width(0.15)
        pdf.set_draw_color(150, 150, 150)
        for edge in hidden.edges():
            g_type = getattr(edge.geom_type(), "name", str(edge.geom_type()))
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

pdf.output("/app/cache/test_draw.pdf")
print("SUCCESS!")
