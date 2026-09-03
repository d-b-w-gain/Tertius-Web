from __future__ import annotations

import os
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

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


SANDBOX_SCRIPT = r"""
import os
import sys
import json
import traceback
from pathlib import Path

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
from tertius.runner import execute_design, write_design_bundle

try:
    design_file = project_dir / "design.py"
    if not design_file.exists():
        raise RuntimeError("design.py not found in project. Cannot compile.")

    install_tertius_provenance(project_dir)
    try:
        execution = execute_design(project_dir)
    finally:
        uninstall_tertius_provenance()

    write_design_bundle(project_dir, execution)
    compound = execution.model
    visual_tree = visual_metadata_tree(
        compound,
        bd=bd,
        source_call_ids=tertius_source_call_ids,
        root=True,
    )
    model_geometry = model_geometry_metadata(visual_tree)
    visual_source_map = tertius_source_map()
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
        deflection = 0.001
        if quality_arg == "sketch":
            deflection = 200.0
        elif quality_arg == "rough":
            deflection = 100.0
        elif quality_arg == "low":
            deflection = 50.0
        elif quality_arg == "medium":
            deflection = 30.0
        elif quality_arg == "normal":
            deflection = 10.0
        elif quality_arg == "high":
            deflection = 1.0

        bd.export_gltf(
            compound,
            str(output_path),
            binary=(export_format == "glb"),
            linear_deflection=deflection,
            angular_deflection=0.1
        )

        if export_format in ("gltf", "glb"):
            try:
                from build123d.exporters3d import _create_xde
                from OCP.XCAFDoc import XCAFDoc_DocumentTool
                from OCP.TCollection import TCollection_AsciiString, TCollection_ExtendedString
                from OCP.TDF import TDF_Tool
                from OCP.TDataStd import TDataStd_Name
                import json
                import struct

                # 1. Map tags to labels
                original_location = compound.location
                compound.location *= bd.Location((0, 0, 0), (1, 0, 0), -90)
                doc = _create_xde(compound, getattr(bd, "Unit").MM)
                shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
                tag_to_name = {}
                tag_to_color = {}

                def srgb_to_linear_float32(component):
                    if component <= 0.04045:
                        linear = component / 12.92
                    else:
                        linear = ((component + 0.055) / 1.055) ** 2.4
                    return struct.unpack("<f", struct.pack("<f", linear))[0]

                def color_factor(color):
                    rgba = None
                    try:
                        if isinstance(color, bd.Color):
                            rgba = list(color)
                            rgba[:3] = [srgb_to_linear_float32(float(component)) for component in rgba[:3]]
                        elif hasattr(color, "to_tuple"):
                            rgba = list(color.to_tuple())
                        elif all(hasattr(color, attr) for attr in ("r", "g", "b")):
                            rgba = [color.r, color.g, color.b, getattr(color, "a", 1.0)]
                        elif all(hasattr(color, attr) for attr in ("red", "green", "blue")):
                            rgba = [color.red, color.green, color.blue, getattr(color, "alpha", 1.0)]
                    except Exception:
                        rgba = None
                    if not isinstance(rgba, list):
                        return None
                    if len(rgba) == 3:
                        rgba.append(1.0)
                    try:
                        return [float(component) for component in rgba[:4]]
                    except Exception:
                        return None

                for node in bd.PreOrderIter(compound):
                    if node.label or node.color is not None:
                        inst_label = shape_tool.FindShape(node.wrapped, findInstance=True)
                        if inst_label.IsNull():
                            inst_label = shape_tool.FindShape(node.wrapped, findInstance=False)

                        if not inst_label.IsNull():
                            entry = TCollection_AsciiString()
                            TDF_Tool.Entry_s(inst_label, entry)
                            tag = f"=>[{entry.ToCString()}]"
                            if node.label:
                                tag_to_name[tag] = node.label
                            if node.color is not None:
                                factor = color_factor(node.color)
                                if factor is not None:
                                    tag_to_color[tag] = factor
                            TDataStd_Name.Set_s(inst_label, TCollection_ExtendedString(tag))

                compound.location = original_location

                # 2. Patch the exported scene metadata. Depending on the
                # exporter/backend, nodes may carry either XDE tags or labels.
                def is_generated_export_name(name):
                    if not isinstance(name, str) or not name.strip():
                        return True
                    stripped = name.strip()
                    lowered = stripped.lower()
                    return (
                        stripped.startswith("=>[")
                        or lowered in {"mesh", "node", "solid", "compound", "part", "object3d"}
                        or lowered.startswith(("node_", "mesh_"))
                    )

                def patch_gltf_json(gltf_json, name_mapping, color_mapping, visual_metadata):
                    changed = False
                    extras = gltf_json.setdefault("extras", {})
                    if model_geometry is not None:
                        extras["tertiusModelGeometry"] = model_geometry
                        changed = True
                    if visual_source_map.get("source_calls"):
                        extras["tertiusSourceMap"] = visual_source_map
                        changed = True

                    def mark_authored_material(material):
                        material_changed = False
                        extras = material.setdefault("extras", {})
                        if extras.get("tertiusAuthoredColor") is not True:
                            extras["tertiusAuthoredColor"] = True
                            material_changed = True

                        base_color = material.get("pbrMetallicRoughness", {}).get("baseColorFactor")
                        if (
                            isinstance(base_color, list)
                            and len(base_color) >= 4
                            and isinstance(base_color[3], (int, float))
                            and float(base_color[3]) < 1.0
                            and material.get("alphaMode") != "BLEND"
                        ):
                            material["alphaMode"] = "BLEND"
                            material_changed = True

                        return material_changed

                    for material in gltf_json.get("materials", []):
                        pbr = material.get("pbrMetallicRoughness")
                        if isinstance(pbr, dict) and "baseColorFactor" in pbr:
                            changed = mark_authored_material(material) or changed

                    for node in gltf_json.get("nodes", []):
                        node_tag = node.get("name")
                        if node_tag in color_mapping and "mesh" in node:
                            mesh_index = node["mesh"]
                            meshes = gltf_json.get("meshes", [])
                            if isinstance(mesh_index, int) and 0 <= mesh_index < len(meshes):
                                for primitive in meshes[mesh_index].get("primitives", []):
                                    material_index = primitive.get("material")
                                    materials = gltf_json.get("materials", [])
                                    if isinstance(material_index, int) and 0 <= material_index < len(materials):
                                        material = materials[material_index]
                                        pbr = material.setdefault("pbrMetallicRoughness", {})
                                        pbr["baseColorFactor"] = color_mapping[node_tag]
                                        changed = mark_authored_material(material) or changed
                        if node_tag in name_mapping:
                            node["name"] = name_mapping[node_tag]
                            changed = True

                    nodes = gltf_json.get("nodes", [])

                    def apply_visual_metadata(node_index, visual_node):
                        nonlocal changed
                        if not isinstance(node_index, int) or not isinstance(visual_node, dict):
                            return
                        if not (0 <= node_index < len(nodes)):
                            return
                        node = nodes[node_index]
                        if not isinstance(node, dict):
                            return

                        label = str(visual_node.get("label") or "")
                        source_call_ids = [
                            str(item)
                            for item in (visual_node.get("source_call_ids") or [])
                            if item
                        ]
                        component_id = visual_node.get("component_id")
                        product_key = visual_node.get("product_key")
                        product_digest = visual_node.get("product_definition_digest")
                        connection_id = visual_node.get("connection_id")
                        connection_digest = visual_node.get("connection_definition_digest")
                        if label and is_generated_export_name(node.get("name")):
                            node["name"] = label
                            changed = True
                        if source_call_ids:
                            extras = node.setdefault("extras", {})
                            extras["tertiusSourceCallIds"] = source_call_ids
                            changed = True
                        if component_id:
                            extras = node.setdefault("extras", {})
                            extras["tertiusComponent"] = {
                                "id": str(component_id),
                                "productKey": str(product_key or ""),
                                "productDefinitionDigest": str(product_digest or ""),
                            }
                            changed = True
                        if connection_id:
                            extras = node.setdefault("extras", {})
                            extras["tertiusConnection"] = {
                                "id": str(connection_id),
                                "connectionDefinitionDigest": str(connection_digest or ""),
                            }
                            changed = True

                        gltf_children = [
                            child_index
                            for child_index in (node.get("children") or [])
                            if isinstance(child_index, int)
                        ]
                        visual_children = [
                            child
                            for child in (visual_node.get("children") or [])
                            if isinstance(child, dict)
                        ]
                        for child_index, child_visual_node in zip(gltf_children, visual_children):
                            apply_visual_metadata(child_index, child_visual_node)

                    scene_roots = []
                    scene_id = gltf_json.get("scene")
                    scenes = gltf_json.get("scenes")
                    if isinstance(scene_id, int) and isinstance(scenes, list) and 0 <= scene_id < len(scenes):
                        scene = scenes[scene_id]
                        if isinstance(scene, dict) and isinstance(scene.get("nodes"), list):
                            scene_roots = [index for index in scene["nodes"] if isinstance(index, int)]
                    if not scene_roots:
                        referenced = {
                            child_index
                            for node in nodes
                            if isinstance(node, dict)
                            for child_index in (node.get("children") or [])
                            if isinstance(child_index, int)
                        }
                        scene_roots = [index for index in range(len(nodes)) if index not in referenced]

                    if len(scene_roots) == 1:
                        apply_visual_metadata(scene_roots[0], visual_metadata)
                    else:
                        visual_children = [
                            child
                            for child in (visual_metadata.get("children") or [])
                            if isinstance(child, dict)
                        ] if isinstance(visual_metadata, dict) else []
                        for root_index, child_visual_node in zip(scene_roots, visual_children):
                            apply_visual_metadata(root_index, child_visual_node)

                    return changed

                def patch_glb_metadata(glb_path, name_mapping, color_mapping):
                    with open(glb_path, "rb") as f:
                        data = f.read()

                    magic, version, length = struct.unpack("<4sII", data[:12])
                    if magic != b"glTF":
                        return

                    chunk_len, chunk_type = struct.unpack("<I4s", data[12:20])
                    if chunk_type != b"JSON":
                        return

                    json_data = data[20:20 + chunk_len].decode("utf-8")
                    gltf_json = json.loads(json_data)

                    changed = patch_gltf_json(gltf_json, name_mapping, color_mapping, visual_tree)

                    if not changed:
                        return

                    new_json_data = json.dumps(gltf_json, separators=(',', ':')).encode("utf-8")
                    padding = (4 - len(new_json_data) % 4) % 4
                    new_json_data += b' ' * padding

                    new_chunk_len = len(new_json_data)
                    new_length = length - chunk_len + new_chunk_len

                    new_data = bytearray()
                    new_data.extend(struct.pack("<4sII", magic, version, new_length))
                    new_data.extend(struct.pack("<I4s", new_chunk_len, chunk_type))
                    new_data.extend(new_json_data)
                    new_data.extend(data[20 + chunk_len:])

                    with open(glb_path, "wb") as f:
                        f.write(new_data)

                def patch_gltf_metadata(gltf_path, name_mapping, color_mapping):
                    with open(gltf_path, "r", encoding="utf-8") as f:
                        gltf_json = json.load(f)

                    changed = patch_gltf_json(gltf_json, name_mapping, color_mapping, visual_tree)
                    if not changed:
                        return

                    with open(gltf_path, "w", encoding="utf-8") as f:
                        json.dump(gltf_json, f, separators=(",", ":"))

                if export_format == "glb":
                    patch_glb_metadata(str(output_path), tag_to_name, tag_to_color)
                else:
                    patch_gltf_metadata(str(output_path), tag_to_name, tag_to_color)
            except Exception as patch_e:
                print("Failed to patch GLTF metadata:", patch_e)
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


def _subprocess_output_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _sandbox_env(project_dir: Path) -> dict[str, str]:
    sandbox_home = str(project_dir)
    env = {
        "HOME": sandbox_home,
        "PATH": "",
        "PYTHONPATH": "",
        "PYTHONIOENCODING": "utf-8",
        "TMP": sandbox_home,
        "TEMP": sandbox_home,
    }
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
            env=_sandbox_env(project_dir),
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    else:
        process = subprocess.Popen(
            args,
            cwd=project_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=_sandbox_env(project_dir),
            start_new_session=True,
        )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        _kill_process_tree(process)
        stdout, stderr = process.communicate()
        return CompileSandboxResult(
            success=False,
            output_path=None,
            stdout=_subprocess_output_text(exc.stdout)
            or _subprocess_output_text(stdout),
            stderr=_subprocess_output_text(exc.stderr)
            or _subprocess_output_text(stderr),
            error=f"Compile timed out after {timeout_seconds} seconds",
        )

    if process.returncode != 0:
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
    )
