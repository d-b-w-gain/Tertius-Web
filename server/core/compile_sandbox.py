from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic, sleep
from typing import Any

from . import glb_dedup, glb_export
from . import scene_export as scene_export_module
from .model_geometry_runtime import TERTIUS_MODEL_GEOMETRY_HELPER_SOURCE
from .provenance_runtime import TERTIUS_PROVENANCE_HELPER_SOURCE

SUPPORTED_EXPORT_FORMATS = {"stl", "step", "gltf", "glb", "timus_views", "timus_bounds"}
COMPILED_DESIGN_FILENAME = "tertius-compiled-design.json"
WORKBENCH_ARTIFACT_FILENAMES = {
    "compiled_design": COMPILED_DESIGN_FILENAME,
    "procurement": "tertius-procurement.json",
    "structural": "tertius-structural.json",
    "drawing": "tertius-drawing.json",
    "bounds": "tertius-bounds.json",
}
COMPONENT_REPORT_FILENAME = "tertius-components.json"
PHASES_FILENAME = "tertius-compile-phases.json"
# Written once every output is in place; the sandbox then writes the
# compile's new component cache entries before it exits.
RESULT_READY_FILENAME = "tertius-result-ready"
CACHE_FLUSH_FILENAME = "tertius-cache-flush.json"
COMPONENT_CACHE_DIR_ENV = "TERTIUS_COMPONENT_CACHE_DIR"


SANDBOX_SCRIPT = r"""
import os
import sys
import json
import traceback
from pathlib import Path
from time import perf_counter

phase_started = perf_counter()
phases = {}


def end_phase(name):
    global phase_started
    now = perf_counter()
    phases[name] = round(now - phase_started, 4)
    phase_started = now

import build123d as bd
project_dir = Path.cwd()
export_format = sys.argv[1].lower()
quality_arg = sys.argv[2].lower() if len(sys.argv) > 2 else None
output_path = project_dir / f"output.{export_format}"

project_dir_str = str(project_dir.resolve())
if project_dir_str not in sys.path:
    sys.path.insert(0, project_dir_str)

from tertius_provenance import install as install_tertius_provenance
from tertius_provenance import source_call_ids as tertius_source_call_ids
from tertius_provenance import source_map as tertius_source_map
from tertius_provenance import uninstall as uninstall_tertius_provenance
from tertius_model_geometry import model_geometry_metadata, visual_metadata_tree
from tertius_glb_export import export_patched_gltf
from tertius.runner import execute_design, write_design_bundle
from tertius.component_cache import (
    component_names_by_part_number,
    configure_component_cache,
    flush_component_cache,
    gltf_tessellation,
    write_component_report,
)
end_phase("startup")

try:
    design_file = project_dir / "design.py"
    if not design_file.exists():
        raise RuntimeError("design.py not found in project. Cannot compile.")

    component_cache_enabled = configure_component_cache(
        os.environ.get("TERTIUS_COMPONENT_CACHE_DIR"),
        project_dir,
        export_format,
        quality_arg,
    )
    install_tertius_provenance(project_dir)
    try:
        execution = execute_design(project_dir)
    finally:
        uninstall_tertius_provenance()
    end_phase("design")

    write_design_bundle(project_dir, execution)
    write_component_report(project_dir, component_cache_enabled)
    compound = execution.model
    part_component_names = component_names_by_part_number(compound)
    visual_tree = visual_metadata_tree(
        compound,
        bd=bd,
        source_call_ids=tertius_source_call_ids,
        root=True,
    )
    model_geometry = model_geometry_metadata(visual_tree)
    visual_source_map = tertius_source_map()
    end_phase("metadata")
    if export_format == "timus_bounds":
        bbox = compound.bounding_box()
        max_dim = max(bbox.max.X - bbox.min.X, bbox.max.Y - bbox.min.Y, bbox.max.Z - bbox.min.Z)
        if max_dim == 0:
            max_dim = 100
        output_path.write_text(json.dumps({"max_dim": max_dim}), encoding="utf-8")
    elif export_format == "stl":
        bd.export_stl(compound, str(output_path))
    elif export_format == "step":
        bd.export_step(compound, str(output_path))
    elif export_format in ("gltf", "glb") and hasattr(bd, "export_gltf"):
        # The UI omits quality for its ordinary GLB preview, which uses the
        # established "normal" preview quality rather than an ultra-fine mesh.
        # Cached components were meshed with these same values before they
        # were stored, so the exporter skips re-meshing them.
        deflection, angular_deflection = gltf_tessellation(quality_arg)
        export_patched_gltf(
            compound,
            output_path,
            export_format,
            bd=bd,
            deflection=deflection,
            angular_deflection=angular_deflection,
            visual_tree=visual_tree,
            model_geometry=model_geometry,
            visual_source_map=visual_source_map,
            part_component_names=part_component_names,
            end_phase=end_phase,
        )
        if export_format == "glb" and os.environ.get("TERTIUS_SCENE_EXPORT") == "1":
            # #314: per-component assets and a manifest, beside the
            # monolithic GLB until the viewer loads manifests.
            from tertius_scene_export import export_scene

            cache_root = os.environ.get("TERTIUS_COMPONENT_CACHE_DIR")
            scene_manifest, scene_counts = export_scene(
                compound,
                project_dir / "tertius-scene",
                bd=bd,
                export_patched_gltf=export_patched_gltf,
                deflection=deflection,
                angular_deflection=angular_deflection,
                visual_tree=visual_tree,
                model_geometry=model_geometry,
                visual_source_map=visual_source_map,
                part_component_names=part_component_names,
                asset_cache_dir=Path(cache_root) / "scene-assets" if cache_root else None,
            )
            end_phase("scene")
            (project_dir / "tertius-scene" / "counts.json").write_text(json.dumps(scene_counts), encoding="utf-8")
    elif export_format == "timus_views":
        import json
        from OCP.HLRBRep import HLRBRep_PolyAlgo, HLRBRep_PolyHLRToShape
        from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir, gp_Pnt2d
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopAbs import TopAbs_SOLID
        from OCP.TopoDS import TopoDS_Compound
        from OCP.BRep import BRep_Builder
        from OCP.BRepBndLib import BRepBndLib
        from OCP.Bnd import Bnd_Box
        from OCP.HLRAlgo import HLRAlgo_Projector

        # 1. Output parameters
        scale = 0.02
        settings_path = project_dir / "settings.json"
        if settings_path.exists():
            try:
                with open(settings_path) as f:
                    settings = json.load(f)
                    scale = settings.get("scale", 0.02)
            except Exception:
                pass
                
        line_weight_mm = 0.2
        min_model_size = line_weight_mm / scale
        deflection = min_model_size / 2.0

        # 2. Cull tiny geometry
        builder = BRep_Builder()
        culled_compound = TopoDS_Compound()
        builder.MakeCompound(culled_compound)

        explorer = TopExp_Explorer(compound.wrapped, TopAbs_SOLID)
        while explorer.More():
            solid = explorer.Current()
            bbox_obj = Bnd_Box()
            BRepBndLib.Add_s(solid, bbox_obj)
            xmin, ymin, zmin, xmax, ymax, zmax = bbox_obj.Get()
            max_dim = max(xmax - xmin, ymax - ymin, zmax - zmin)
            
            if max_dim >= min_model_size:
                builder.Add(culled_compound, solid)
            explorer.Next()
            
        # 3. Analytic HLR projection on culled model
        culled_bd = bd.Compound(culled_compound)
        bbox = culled_bd.bounding_box()
        look_at = bbox.center()
        max_dim = max(bbox.max.X - bbox.min.X, bbox.max.Y - bbox.min.Y, bbox.max.Z - bbox.min.Z)
        if max_dim == 0:
            max_dim = 100
            
        views = {}
        for view_name in ["top", "front", "side", "iso"]:
            if view_name == "top":
                origin = (look_at.X, look_at.Y, bbox.max.Z + max_dim)
                up_dir = (0, 1, 0)
            elif view_name == "front":
                origin = (look_at.X, bbox.min.Y - max_dim, look_at.Z)
                up_dir = (0, 0, 1)
            elif view_name == "side":
                origin = (bbox.max.X + max_dim, look_at.Y, look_at.Z)
                up_dir = (0, 0, 1)
            else:
                origin = (look_at.X + max_dim, look_at.Y - max_dim, look_at.Z + max_dim)
                up_dir = (0, 0, 1)
                
            try:
                visible, hidden = culled_bd.project_to_viewport(
                    origin, 
                    viewport_up=up_dir, 
                    look_at=(look_at.X, look_at.Y, look_at.Z)
                )
                
                segments = []
                
                def extract_edges(shape_list, is_hidden):
                    if not shape_list: return
                    for shape in shape_list:
                        for edge in shape.edges():
                            try:
                                if edge.geom_type.name == 'LINE':
                                    p1 = edge.position_at(0)
                                    p2 = edge.position_at(1)
                                    segments.append(((p1.X, p1.Y), (p2.X, p2.Y), is_hidden))
                                else:
                                    num_samples = max(8, min(64, int(edge.length / 2.0)))
                                    pts = [edge.position_at(i / num_samples) for i in range(num_samples + 1)]
                                    for i in range(len(pts)-1):
                                        p1, p2 = pts[i], pts[i+1]
                                        segments.append(((p1.X, p1.Y), (p2.X, p2.Y), is_hidden))
                            except Exception:
                                pass

                extract_edges(visible, False)
                extract_edges(hidden, True)
                            
                views[view_name] = segments
            except Exception as e:
                print(f"Failed to project {view_name}: {e}")
                views[view_name] = []

        with open(str(output_path), "w") as f:
            json.dump(views, f)
    else:
        raise RuntimeError(f"Unsupported export format: {export_format}")
    end_phase("finish" if "export" in phases else "export")
    (project_dir / "tertius-compile-phases.json").write_text(json.dumps(phases), encoding="utf-8")
    ready = project_dir / "tertius-result-ready.tmp"
    ready.write_text("ready", encoding="utf-8")
    ready.replace(project_dir / "tertius-result-ready")
    if component_cache_enabled:
        (project_dir / "tertius-cache-flush.json").write_text(
            json.dumps(flush_component_cache()), encoding="utf-8"
        )
except Exception:
    traceback.print_exc()
    sys.exit(1)
"""


@dataclass(frozen=True)
class CompileSandboxResult:
    success: bool
    output_path: Path | None
    stdout: str
    stderr: str
    error: str | None
    artifact_paths: dict[str, Path] = field(default_factory=dict)
    component_report_path: Path | None = None
    phases_path: Path | None = None
    # With early_result: call to wait for the sandbox to finish storing new
    # component cache entries (True if it stored them cleanly).
    finish: Callable[[], bool] | None = None


def _sandbox_env(
    project_dir: Path,
    component_cache_dir: Path | None = None,
    scene_export: bool = False,
) -> dict[str, str]:
    sandbox_home = str(project_dir)
    env = {
        "HOME": sandbox_home,
        "PATH": "",
        "PYTHONPATH": "",
        "PYTHONIOENCODING": "utf-8",
        "TMP": sandbox_home,
        "TEMP": sandbox_home,
    }
    if component_cache_dir is not None:
        env[COMPONENT_CACHE_DIR_ENV] = str(component_cache_dir)
    if scene_export:
        env["TERTIUS_SCENE_EXPORT"] = "1"
    if sys.platform == "win32":
        env["USERPROFILE"] = sandbox_home
        for name in ("SystemRoot", "WINDIR", "COMSPEC", "PATHEXT"):
            if value := os.environ.get(name):
                env[name] = value
    return env


def _kill_process_tree(process: subprocess.Popen[str]) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return

    os.killpg(process.pid, signal.SIGKILL)


def run_compile_sandbox(
    project_dir: Path,
    export_format: str,
    quality: str | None = None,
    timeout_seconds: int = 30,
    component_cache_dir: Path | None = None,
    early_result: bool = False,
    scene_export: bool = False,
) -> CompileSandboxResult:
    ext = export_format.lower()
    if ext not in SUPPORTED_EXPORT_FORMATS:
        return CompileSandboxResult(
            success=False,
            output_path=None,
            stdout="",
            stderr="",
            error=f"Unsupported export format: {export_format}",
        )

    output_path = project_dir / f"output.{ext}"
    provenance_helper_path = project_dir / "tertius_provenance.py"
    provenance_helper_path.write_text(
        TERTIUS_PROVENANCE_HELPER_SOURCE, encoding="utf-8"
    )
    model_geometry_helper_path = project_dir / "tertius_model_geometry.py"
    model_geometry_helper_path.write_text(
        TERTIUS_MODEL_GEOMETRY_HELPER_SOURCE,
        encoding="utf-8",
    )
    (project_dir / "tertius_glb_export.py").write_text(
        Path(glb_export.__file__).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (project_dir / "tertius_scene_export.py").write_text(
        Path(scene_export_module.__file__).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    glb_helper_path = project_dir / "tertius_glb_runtime.py"
    glb_helper_path.write_text(
        Path(glb_dedup.__file__).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    args = [sys.executable, "-c", SANDBOX_SCRIPT, ext]
    if quality:
        args.append(quality)

    if sys.platform == "win32":
        process = subprocess.Popen(
            args,
            cwd=project_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=_sandbox_env(project_dir, component_cache_dir, scene_export),
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    else:
        process = subprocess.Popen(
            args,
            cwd=project_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=_sandbox_env(project_dir, component_cache_dir, scene_export),
            start_new_session=True,
        )
    output = _DrainedOutput(process)
    deadline = monotonic() + timeout_seconds
    ready_path = project_dir / RESULT_READY_FILENAME
    finish: Callable[[], bool] | None = None
    while process.poll() is None:
        if early_result and ready_path.exists():
            # Every output is written; the sandbox is now only storing new
            # component cache entries, which the caller can wait for later.
            finish = _sandbox_finisher(process, output, deadline)
            break
        if monotonic() >= deadline:
            _kill_process_tree(process)
            output.join()
            return CompileSandboxResult(
                success=False,
                output_path=None,
                stdout=output.stdout(),
                stderr=output.stderr(),
                error=f"Compile timed out after {timeout_seconds} seconds",
            )
        sleep(_POLL_SECONDS)
    if finish is None:
        output.join()
    stdout, stderr = output.stdout(), output.stderr()

    # A failure after the ready marker only lost new cache entries: the
    # outputs were complete.
    if finish is None and process.returncode != 0 and not ready_path.exists():
        return CompileSandboxResult(
            success=False,
            output_path=None,
            stdout=stdout,
            stderr=stderr,
            error=stderr.strip() or f"Compile exited with status {process.returncode}",
        )

    if not output_path.exists():
        return CompileSandboxResult(
            success=False,
            output_path=None,
            stdout=stdout,
            stderr=stderr,
            error=f"Compile completed without creating output.{ext}",
        )

    return CompileSandboxResult(
        success=True,
        output_path=output_path,
        stdout=stdout,
        stderr=stderr,
        error=None,
        artifact_paths={
            kind: project_dir / filename
            for kind, filename in WORKBENCH_ARTIFACT_FILENAMES.items()
            if (project_dir / filename).is_file()
        },
        component_report_path=_existing_file(project_dir / COMPONENT_REPORT_FILENAME),
        phases_path=_existing_file(project_dir / PHASES_FILENAME),
        finish=finish,
    )


_POLL_SECONDS = 0.05
# Time the sandbox always gets to store its cache entries after its outputs,
# even when the compile used most of its budget.
_FLUSH_GRACE_SECONDS = 120


class _DrainedOutput:
    """Reads a process's stdout and stderr on threads, so neither pipe can
    fill and stall the process while the caller isn't waiting on it."""

    def __init__(self, process: subprocess.Popen[str]) -> None:
        self._chunks: dict[str, list[str]] = {"stdout": [], "stderr": []}
        self._threads = [
            threading.Thread(target=self._read, args=(stream, name), daemon=True)
            for name, stream in (("stdout", process.stdout), ("stderr", process.stderr))
            if stream is not None
        ]
        for thread in self._threads:
            thread.start()

    def _read(self, stream: Any, name: str) -> None:
        for chunk in iter(lambda: stream.read(65536), ""):
            self._chunks[name].append(chunk)
        stream.close()

    def join(self) -> None:
        for thread in self._threads:
            thread.join()

    def stdout(self) -> str:
        return "".join(self._chunks["stdout"])

    def stderr(self) -> str:
        return "".join(self._chunks["stderr"])


def _sandbox_finisher(
    process: subprocess.Popen[str], output: _DrainedOutput, deadline: float
) -> Callable[[], bool]:
    """Wait for a sandbox that has written its outputs to finish storing
    cache entries. Returns whether it stored them cleanly."""
    done: list[bool] = []

    def finish() -> bool:
        if done:
            return done[0]
        timeout = max(deadline - monotonic(), _FLUSH_GRACE_SECONDS)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_process_tree(process)
            process.wait()
        output.join()
        done.append(process.returncode == 0)
        return done[0]

    return finish


def _existing_file(path: Path) -> Path | None:
    return path if path.is_file() else None
