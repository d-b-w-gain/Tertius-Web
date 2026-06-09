import time
import build123d as bd
from OCP.HLRBRep import HLRBRep_PolyAlgo, HLRBRep_PolyHLRToShape
from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir
from OCP.BRepMesh import BRepMesh_IncrementalMesh

def create_complex_assembly():
    columns = []
    for x in range(10):
        for y in range(10):
            with bd.BuildPart() as p:
                with bd.Locations((x * 100, y * 100, 0)):
                    bd.Box(10, 10, 500)
            columns.append(p.part)
            
    for x in range(10):
        with bd.BuildPart() as p:
            with bd.Locations((x * 100, 450, 450)):
                bd.Box(10, 1000, 10)
        columns.append(p.part)
        
    compound = bd.Compound(columns)
    return compound

def benchmark_hlr_poly(compound):
    bbox = compound.bounding_box()
    look_at = bbox.center()
    max_dim = max(bbox.max.X - bbox.min.X, bbox.max.Y - bbox.min.Y, bbox.max.Z - bbox.min.Z)
    
    origin = gp_Pnt(look_at.X, look_at.Y, bbox.max.Z + max_dim)
    up_dir = gp_Dir(0, 1, 0)
    look_dir = gp_Dir(look_at.X - origin.X(), look_at.Y - origin.Y(), look_at.Z - origin.Z())
    
    print("Meshing shape...")
    st = time.time()
    BRepMesh_IncrementalMesh(compound.wrapped, 0.1)
    print(f"Meshing took {time.time() - st:.2f}s")
    
    print("Starting PolyAlgo...")
    st = time.time()
    
    projector = gp_Ax2(origin, look_dir, up_dir)
    from OCP.HLRAlgo import HLRAlgo_Projector
    hlr_projector = HLRAlgo_Projector(projector)
    
    algo = HLRBRep_PolyAlgo()
    algo.Projector(hlr_projector)
    algo.Load(compound.wrapped)
    algo.Update()
    
    extractor = HLRBRep_PolyHLRToShape()
    extractor.Update(algo)
    
    # Extract visible sharp edges
    visible_edges = extractor.VCompound()
    hidden_edges = extractor.HCompound()
    
    print(f"PolyAlgo took {time.time() - st:.2f}s")
    
    vis = bd.Compound(visible_edges) if visible_edges else None
    hid = bd.Compound(hidden_edges) if hidden_edges else None
    
    print(f"Visible edges: {len(vis.edges()) if vis else 0}")
    print(f"Hidden edges: {len(hid.edges()) if hid else 0}")

if __name__ == "__main__":
    comp = create_complex_assembly()
    benchmark_hlr_poly(comp)
