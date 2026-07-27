from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from .contracts import (
    CapabilityState,
    EquilibriumDiagnostic,
    LoadCase,
    MemberCheck,
    MemberResult,
    NodalLoad,
    NodeReaction,
    Restraints,
    SectionProperties,
    SnapshotSource,
    SolverMetadata,
    StructuralMaterial,
    StructuralMember,
    StructuralNode,
    StructuralSnapshot,
    Vector3,
)

BASE_NODE_ID = "fixture-node-base"
FREE_NODE_ID = "fixture-node-free"
MEMBER_ID = "fixture-member-cantilever"
SECTION_ID = "fixture-section-100x100"
MATERIAL_ID = "fixture-material-steel"
LOAD_CASE_ID = "fixture-lateral"
COMBINATION_ID = "fixture-sls"


@dataclass(frozen=True)
class CantileverSolution:
    reaction_fx_kN: float
    reaction_my_kNm: float
    tip_dx_mm: float
    base_my_kNm: float
    base_shear_kN: float


def _clean(value: float, tolerance: float = 1e-12) -> float:
    return 0.0 if abs(value) < tolerance else float(value)


def solve_cantilever() -> CantileverSolution:
    from Pynite import FEModel3D

    model = FEModel3D()
    model.add_material(
        MATERIAL_ID,
        E=200_000_000.0,
        G=76_923_000.0,
        nu=0.3,
        rho=7850.0,
    )
    model.add_section(
        SECTION_ID,
        A=0.01,
        Iy=8.333e-6,
        Iz=8.333e-6,
        J=1.667e-5,
    )
    model.add_node(BASE_NODE_ID, 0.0, 0.0, 0.0)
    model.add_node(FREE_NODE_ID, 0.0, 0.0, 2.0)
    model.def_support(
        BASE_NODE_ID,
        support_DX=True,
        support_DY=True,
        support_DZ=True,
        support_RX=True,
        support_RY=True,
        support_RZ=True,
    )
    model.add_member(
        MEMBER_ID,
        BASE_NODE_ID,
        FREE_NODE_ID,
        MATERIAL_ID,
        SECTION_ID,
    )
    model.add_node_load(FREE_NODE_ID, "FX", 1.0, case=LOAD_CASE_ID)
    model.add_load_combo(COMBINATION_ID, {LOAD_CASE_ID: 1.0})
    model.analyze(check_statics=False, log=False)

    base_node = model.nodes[BASE_NODE_ID]
    free_node = model.nodes[FREE_NODE_ID]
    member = model.members[MEMBER_ID]
    return CantileverSolution(
        reaction_fx_kN=_clean(base_node.RxnFX[COMBINATION_ID]),
        reaction_my_kNm=_clean(base_node.RxnMY[COMBINATION_ID]),
        tip_dx_mm=_clean(free_node.DX[COMBINATION_ID] * 1000.0),
        base_my_kNm=_clean(member.moment("My", 0.0, COMBINATION_ID)),
        base_shear_kN=_clean(member.shear("Fz", 0.0, COMBINATION_ID)),
    )


@lru_cache(maxsize=1)
def cantilever_snapshot() -> StructuralSnapshot:
    solution = solve_cantilever()
    moment_capacity_kNm = 2.5
    utilisation = abs(solution.base_my_kNm) / moment_capacity_kNm
    force_residual_x = 1.0 + solution.reaction_fx_kN
    moment_residual_y = 2.0 + solution.reaction_my_kNm
    residual_tolerance = 1e-8
    equilibrium_passes = max(abs(force_residual_x), abs(moment_residual_y)) <= residual_tolerance

    return StructuralSnapshot(
        mode="fixture",
        title="Structural Workbench — Cantilever",
        subtitle="Deterministic end-to-end fixture",
        source=SnapshotSource(
            kind="fixture",
            label="Known 2 m cantilever; not a shed design",
            design_id="structural-fixture/cantilever-v1",
            design_hash="fixture:cantilever-v1",
        ),
        nodes=[
            StructuralNode(
                id=BASE_NODE_ID,
                label="Fixed base",
                position=Vector3(x=0, y=0, z=0),
                restraints=Restraints(dx=True, dy=True, dz=True, rx=True, ry=True, rz=True),
                visual_node_id=BASE_NODE_ID,
            ),
            StructuralNode(
                id=FREE_NODE_ID,
                label="Loaded tip",
                position=Vector3(x=0, y=0, z=2),
                visual_node_id=FREE_NODE_ID,
            ),
        ],
        members=[
            StructuralMember(
                id=MEMBER_ID,
                label="Fixture cantilever",
                start_node_id=BASE_NODE_ID,
                end_node_id=FREE_NODE_ID,
                section_id=SECTION_ID,
                material_id=MATERIAL_ID,
                visual_node_id=MEMBER_ID,
            )
        ],
        sections=[
            SectionProperties(
                id=SECTION_ID,
                label="100 × 100 mm fixture section",
                area_m2=0.01,
                iy_m4=8.333e-6,
                iz_m4=8.333e-6,
                torsion_j_m4=1.667e-5,
            )
        ],
        materials=[
            StructuralMaterial(
                id=MATERIAL_ID,
                label="Fixture steel",
                elastic_modulus_kN_m2=200_000_000.0,
                shear_modulus_kN_m2=76_923_000.0,
                poisson_ratio=0.3,
                density_kg_m3=7850.0,
            )
        ],
        load_cases=[
            LoadCase(
                id=LOAD_CASE_ID,
                label="1 kN lateral tip load",
                category="fixture",
            )
        ],
        loads=[
            NodalLoad(
                id="fixture-load-tip-x",
                label="+X tip load",
                node_id=FREE_NODE_ID,
                case_id=LOAD_CASE_ID,
                force=Vector3(x=1, y=0, z=0),
                visual_node_id="fixture-load-tip-x",
            )
        ],
        reactions=[
            NodeReaction(
                node_id=BASE_NODE_ID,
                combination_id=COMBINATION_ID,
                force=Vector3(x=solution.reaction_fx_kN, y=0, z=0),
                moment=Vector3(x=0, y=solution.reaction_my_kNm, z=0),
            )
        ],
        member_results=[
            MemberResult(
                member_id=MEMBER_ID,
                combination_id=COMBINATION_ID,
                max_moment_kNm=abs(solution.base_my_kNm),
                max_shear_kN=abs(solution.base_shear_kN),
                max_axial_kN=0,
                max_displacement_mm=abs(solution.tip_dx_mm),
            )
        ],
        member_checks=[
            MemberCheck(
                member_id=MEMBER_ID,
                label="Illustrative bending check",
                demand_kNm=abs(solution.base_my_kNm),
                capacity_kNm=moment_capacity_kNm,
                utilisation=utilisation,
                status="pass" if utilisation <= 1 else "fail",
                basis="Fixture capacity only — no AS 4600 section check",
            )
        ],
        equilibrium=EquilibriumDiagnostic(
            force_residual_kN=Vector3(x=force_residual_x, y=0, z=0),
            moment_residual_kNm=Vector3(x=0, y=moment_residual_y, z=0),
            tolerance=residual_tolerance,
            status="pass" if equilibrium_passes else "fail",
        ),
        solver=SolverMetadata(
            name="PyNiteFEA",
            version=version("PyNiteFEA"),
            analysis="3D first-order elastic",
            combination_id=COMBINATION_ID,
        ),
        capabilities=[
            CapabilityState(
                id="design-capture",
                label="Design capture",
                status="fixture",
                detail="Static fixture; design.py capture is the next adapter.",
            ),
            CapabilityState(
                id="geometry",
                label="Build123D geometry",
                status="online",
                detail="Physical member and analytical IDs share one identity.",
            ),
            CapabilityState(
                id="graph",
                label="Structural graph",
                status="online",
                detail="Two nodes, one member, and one fixed support.",
            ),
            CapabilityState(
                id="solver",
                label="PyNite solver",
                status="online",
                detail="Reactions and displacement are generated by PyNiteFEA.",
            ),
            CapabilityState(
                id="checks",
                label="Member checks",
                status="fixture",
                detail="Illustrative capacity only; AS 4600 checks are not connected.",
            ),
            CapabilityState(
                id="reports",
                label="Calculation reports",
                status="pending",
                detail="No calculation report artifact is generated yet.",
            ),
        ],
        warnings=[
            "DEMONSTRATION FIXTURE — NOT FOR DESIGN, CERTIFICATION, OR ORDERING.",
            "The 2.5 kNm capacity is illustrative and is not an AS 4600 section capacity.",
            "No shed geometry, wind, cladding, battens, openings, doors, or connections are included.",
        ],
    )


@lru_cache(maxsize=1)
def cantilever_glb() -> bytes:
    import build123d as bd
    from build123d.exporters3d import _create_xde
    from OCP.TCollection import TCollection_AsciiString
    from OCP.TDF import TDF_Tool
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    member = bd.Box(
        100,
        100,
        2000,
        align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN),
    )
    member.label = MEMBER_ID
    member.color = bd.Color(0.95, 0.52, 0.12)

    base_plate = bd.Box(
        500,
        500,
        80,
        align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MAX),
    )
    base_plate.label = "fixture-support-base"
    base_plate.color = bd.Color(0.25, 0.32, 0.43)

    base_node = bd.Sphere(85)
    base_node.label = BASE_NODE_ID
    base_node.color = bd.Color(0.22, 0.78, 0.99)

    free_node = bd.Sphere(85).translate((0, 0, 2000))
    free_node.label = FREE_NODE_ID
    free_node.color = bd.Color(0.22, 0.78, 0.99)

    load_shaft = (
        bd.Cylinder(
            25,
            400,
            align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN),
        )
        .rotate(bd.Axis.Y, 90)
        .translate((-550, 0, 2000))
    )
    load_tip = (
        bd.Cone(
            70,
            0,
            150,
            align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN),
        )
        .rotate(bd.Axis.Y, 90)
        .translate((-150, 0, 2000))
    )
    load_arrow = bd.Compound(
        None,
        children=cast(list[bd.Shape], [load_shaft, load_tip]),
    )
    load_arrow.label = "fixture-load-tip-x"
    load_arrow.color = bd.Color(0.94, 0.27, 0.27)

    assembly = bd.Compound(
        None,
        children=cast(
            list[bd.Shape],
            [base_plate, member, base_node, free_node, load_arrow],
        ),
    )
    assembly.label = "fixture-cantilever-assembly"

    original_location = assembly.location
    assembly.location *= bd.Location((0, 0, 0), (1, 0, 0), -90)
    document = _create_xde(assembly, bd.Unit.MM)
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
    tag_to_name: dict[str, str] = {}
    for node in bd.PreOrderIter(assembly):
        if not node.label:
            continue
        instance_label = shape_tool.FindShape(node.wrapped, findInstance=True)
        if instance_label.IsNull():
            instance_label = shape_tool.FindShape(node.wrapped, findInstance=False)
        if instance_label.IsNull():
            continue
        entry = TCollection_AsciiString()
        TDF_Tool.Entry_s(instance_label, entry)
        tag_to_name[f"=>[{entry.ToCString()}]"] = node.label
    assembly.location = original_location

    with TemporaryDirectory(prefix="tertius-structural-fixture-") as temp_dir:
        output = Path(temp_dir) / "cantilever.glb"
        bd.export_gltf(assembly, output, binary=True)
        return _patch_glb_node_names(output.read_bytes(), tag_to_name)


def _patch_glb_node_names(content: bytes, tag_to_name: dict[str, str]) -> bytes:
    import json
    import struct

    magic, gltf_version, total_length = struct.unpack("<4sII", content[:12])
    if magic != b"glTF":
        raise ValueError("Build123D fixture export did not produce a GLB file")
    json_chunk_length, json_chunk_type = struct.unpack("<I4s", content[12:20])
    if json_chunk_type != b"JSON":
        raise ValueError("Build123D fixture GLB has no leading JSON chunk")

    json_end = 20 + json_chunk_length
    document = json.loads(content[20:json_end].decode("utf-8"))
    for node in document.get("nodes", []):
        name = node.get("name")
        if name in tag_to_name:
            node["name"] = tag_to_name[name]

    json_content = json.dumps(document, separators=(",", ":")).encode("utf-8")
    json_content += b" " * ((4 - len(json_content) % 4) % 4)
    new_total_length = total_length - json_chunk_length + len(json_content)
    return b"".join(
        [
            struct.pack("<4sII", magic, gltf_version, new_total_length),
            struct.pack("<I4s", len(json_content), json_chunk_type),
            json_content,
            content[json_end:],
        ]
    )
