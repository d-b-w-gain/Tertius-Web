import build123d as bd

# Parametric variables for Lysaght Cee Purlin (Datasheet Z/C10019)
length = 2400.0
t = 1.9         # Material thickness
D = 102.0       # Section depth
B = 51.0        # Flange width
L = 14.5        # Lip length
R5 = 5.0        # Inner bend radius

# Hole detailing (Datasheet)
G = 40.0              # Gauge line spacing (Web)
edge_distance = 35.0  # From ends
slot_length = 22.0    # Elongated hole length
slot_height = 18.0    # Elongated hole height (Dh)
flange_gauge = B / 2  # Flange gauge line (center of flange)

with bd.BuildPart() as purlin:
    with bd.BuildSketch() as profile:
        # 1. Main outer boundary of the Cee section
        bd.Rectangle(B, D, align=(bd.Align.MIN, bd.Align.CENTER))
        
        # 2. Subtract the inner hollow section (leaves a fully closed box of thickness t)
        with bd.Locations((t, 0)):
            bd.Rectangle(B - 2*t, D - 2*t, align=(bd.Align.MIN, bd.Align.CENTER), mode=bd.Mode.SUBTRACT)
            
        # 3. Cut the front opening to create the lips
        with bd.Locations((B, 0)):
            bd.Rectangle(t, D - L*2, align=(bd.Align.MAX, bd.Align.CENTER), mode=bd.Mode.SUBTRACT)
            
        # 4. Fillet the outer corners (Radius = R5 + t)
        v_out = [v for v in profile.vertices() if (abs(v.X) < 1e-3 or abs(v.X - B) < 1e-3) and abs(abs(v.Y) - D/2) < 1e-3]
        if v_out: bd.fillet(v_out, radius=R5 + t)
        
        # 5. Fillet the inner corners (Radius = R5)
        v_in = [v for v in profile.vertices() if (abs(v.X - t) < 1e-3 or abs(v.X - (B-t)) < 1e-3) and abs(abs(v.Y) - (D/2-t)) < 1e-3]
        if v_in: bd.fillet(v_in, radius=R5)
        
    # Extrude the 2D sketch into a 3D beam
    bd.extrude(amount=length)

    # ---------------------------------------------------------
    # Add slotted bolt holes through the Web (Y-Z plane)
    # ---------------------------------------------------------
    with bd.BuildSketch(bd.Plane.YZ):
        # 4 holes total: 2 at each end, spaced vertically by G
        with bd.Locations(
            (G/2, edge_distance), 
            (-G/2, edge_distance), 
            (G/2, length - edge_distance), 
            (-G/2, length - edge_distance)
        ):
            # Elongate along the purlin length (which is local Y on the YZ plane)
            bd.SlotOverall(slot_length, slot_height, rotation=90)
            
    # Extrude the 2D slots along X axis to cut through the web
    bd.extrude(amount=t*4, both=True, mode=bd.Mode.SUBTRACT)

    # ---------------------------------------------------------
    # Add slotted bolt holes through the Flanges (X-Z plane)
    # ---------------------------------------------------------
    with bd.BuildSketch(bd.Plane.XZ):
        # 2 locations: one at each end. 
        # Extruding along Y will cut through BOTH top and bottom flanges simultaneously
        with bd.Locations(
            (flange_gauge, edge_distance), 
            (flange_gauge, length - edge_distance)
        ):
            # Elongate along the purlin length (which is local Y on the XZ plane)
            bd.SlotOverall(slot_length, slot_height, rotation=90)
            
    # Extrude the 2D slots along Y axis to cut through both flanges
    bd.extrude(amount=D, both=True, mode=bd.Mode.SUBTRACT)
