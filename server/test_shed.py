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

compound = bd.Compound(design())
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

visible_edges = extractor.VCompound()
hidden_edges = extractor.HCompound()

print(f"PolyAlgo took {time.time() - st:.2f}s")

vis = bd.Compound(visible_edges) if visible_edges else None
hid = bd.Compound(hidden_edges) if hidden_edges else None

print(f"Visible edges: {len(vis.edges()) if vis else 0}")
print(f"Hidden edges: {len(hid.edges()) if hid else 0}")
"""
            with open(project_dir / "timing_poly.py", "w") as f:
                f.write(sandbox_script)
                
            import subprocess
            res = subprocess.run(["python", "timing_poly.py"], cwd=project_dir, capture_output=True, text=True)
            print("STDOUT:")
            print(res.stdout)
            print("STDERR:")
            print(res.stderr)

    finally:
        db.close()

if __name__ == "__main__":
    main()
