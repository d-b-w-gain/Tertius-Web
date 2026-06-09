import sys
import time
from pathlib import Path

sys.path.insert(0, "/app/server")

from core.db import SessionLocal
from core.models import Project, ProjectFile
from core.compile_runtime import hydrate_project_files

def main():
    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.name == "shed").first()
        files_query = db.query(ProjectFile).filter(ProjectFile.project_id == project.id).all()
        files = {f.filename: f.content for f in files_query}

        with hydrate_project_files(files) as project_dir:
            sandbox_script = """
import sys
import time
import build123d as bd
from design import design
from OCP.HLRBRep import HLRBRep_PolyAlgo, HLRBRep_PolyHLRToShape
from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopoDS import TopoDS_Compound
from OCP.BRep import BRep_Builder
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box

print("Building design...")
st = time.time()
compound = bd.Compound(design())
print(f"Design built in {time.time() - st:.2f}s")

# 1. Output parameters
scale = 0.02 # 1:50
min_printable_mm = 0.1 # 0.1mm on paper
min_model_size = min_printable_mm / scale # 5.0mm in model space
deflection = min_model_size / 2.0 # 2.5mm deflection for mesh

print(f"Scale: {scale}, Min Feature: {min_model_size}mm, Deflection: {deflection}mm")

# 2. Cull tiny geometry
print("Culling geometry...")
st = time.time()
builder = BRep_Builder()
culled_compound = TopoDS_Compound()
builder.MakeCompound(culled_compound)

explorer = TopExp_Explorer(compound.wrapped, TopAbs_SOLID)
solid_count = 0
culled_count = 0
while explorer.More():
    solid = explorer.Current()
    bbox = Bnd_Box()
    BRepBndLib.Add_s(solid, bbox)
    xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
    max_dim = max(xmax - xmin, ymax - ymin, zmax - zmin)
    
    if max_dim >= min_model_size:
        builder.Add(culled_compound, solid)
    else:
        culled_count += 1
    
    solid_count += 1
    explorer.Next()
    
print(f"Culling took {time.time() - st:.2f}s. Kept {solid_count - culled_count}/{solid_count} solids.")

# 3. Dynamic Tessellation
print("Meshing shape with dynamic deflection...")
st = time.time()
BRepMesh_IncrementalMesh(culled_compound, deflection)
print(f"Meshing took {time.time() - st:.2f}s")

# 4. HLR PolyAlgo
print("Starting PolyAlgo...")
st = time.time()

bbox = bd.Compound(culled_compound).bounding_box()
look_at = bbox.center()
max_dim = max(bbox.max.X - bbox.min.X, bbox.max.Y - bbox.min.Y, bbox.max.Z - bbox.min.Z)

origin = gp_Pnt(look_at.X, look_at.Y, bbox.max.Z + max_dim)
up_dir = gp_Dir(0, 1, 0)
look_dir = gp_Dir(look_at.X - origin.X(), look_at.Y - origin.Y(), look_at.Z - origin.Z())
projector = gp_Ax2(origin, look_dir, up_dir)

from OCP.HLRAlgo import HLRAlgo_Projector
hlr_projector = HLRAlgo_Projector(projector)

algo = HLRBRep_PolyAlgo()
algo.Projector(hlr_projector)
algo.Load(culled_compound)
algo.Update()

extractor = HLRBRep_PolyHLRToShape()
extractor.Update(algo)

visible_edges = extractor.VCompound()
hidden_edges = extractor.HCompound()

print(f"PolyAlgo took {time.time() - st:.2f}s")

vis = bd.Compound(visible_edges) if visible_edges else None
print(f"Visible edges generated: {len(vis.edges()) if vis else 0}")
"""
            with open(project_dir / "timing_adaptive.py", "w") as f:
                f.write(sandbox_script)
                
            import subprocess
            res = subprocess.run(["python", "timing_adaptive.py"], cwd=project_dir, capture_output=True, text=True)
            print("STDOUT:")
            print(res.stdout)
            print("STDERR:")
            print(res.stderr)

    finally:
        db.close()

if __name__ == "__main__":
    main()
