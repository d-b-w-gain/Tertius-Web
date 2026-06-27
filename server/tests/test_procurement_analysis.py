from __future__ import annotations

import math
from copy import deepcopy

from core.procurement_analysis import (
    analyze_design_sources,
    analyze_gltf_tree,
    build_procurement_analysis,
)


def component_tree(label: str = "Member_A") -> dict:
    return {
        "name": "Scene",
        "children": [
            {
                "name": "Portal",
                "type": "Object3D",
                "children": [
                    {
                        "name": label,
                        "type": "Object3D",
                        "children": [{"name": "mesh_1", "type": "Mesh", "isMesh": True}],
                    }
                ],
            }
        ],
    }


def first_requirement(files: dict[str, str], tree: dict | None = None) -> dict:
    source = analyze_design_sources(files)
    gltf = analyze_gltf_tree(tree or component_tree())
    analysis = build_procurement_analysis(source, gltf)
    assert analysis["requirements"]
    return analysis["requirements"][0]


def test_literal_arg_part_number_is_resolved_without_product_hardcoding():
    requirement = first_requirement({
        "design.py": """
def make_member(length, part_number):
    return None

left = make_member(1200, part_number="TEST-A")
""",
    })

    assert requirement["part_number"] == "TEST-A"
    assert requirement["dimensions"] == {"length_mm": 1200}
    assert requirement["resolution_trace"]["part_number"]["resolution"] == "literal"


def test_local_constant_part_number_and_length_are_resolved():
    requirement = first_requirement({
        "design.py": """
PURLIN_PART_NUMBER = "TEST-B"
column_height = 2400

def make_member(length, part_number):
    return None

left = make_member(column_height, part_number=PURLIN_PART_NUMBER)
""",
    })

    assert requirement["part_number"] == "TEST-B"
    assert requirement["dimensions"] == {"length_mm": 2400}
    assert requirement["resolution_trace"]["part_number"]["resolution"] == "literal_assignment"


def test_imported_constant_part_number_is_resolved_from_local_module():
    requirement = first_requirement({
        "design.py": """
from products import MEMBER_PART

def make_member(length, part_number):
    return None

left = make_member(1800, part_number=MEMBER_PART)
""",
        "products.py": """
MEMBER_PART = "TEST-IMPORTED"
""",
    })

    assert requirement["part_number"] == "TEST-IMPORTED"
    assert requirement["resolution_trace"]["part_number"]["resolution"] == "imported_constant"
    assert requirement["resolution_trace"]["part_number"]["source_file"] == "products.py"


def test_function_default_part_number_is_used_when_argument_is_omitted():
    requirement = first_requirement({
        "design.py": """
def make_member(length, part_number="TEST-DEFAULT"):
    return None

left = make_member(900)
""",
    })

    assert requirement["part_number"] == "TEST-DEFAULT"
    assert requirement["dimensions"] == {"length_mm": 900}
    assert requirement["resolution_trace"]["part_number"]["resolution"] == "function_default"


def test_keyword_only_default_part_number_is_used_when_argument_is_omitted():
    requirement = first_requirement({
        "design.py": """
def make_member(length, *, part_number="TEST-KW-DEFAULT", quantity=3):
    return None

left = make_member(900)
""",
    })

    assert requirement["part_number"] == "TEST-KW-DEFAULT"
    assert requirement["quantity"] == 1
    assert requirement["quantity_source"] == "visual_instances"
    assert requirement["orderable"] is True
    assert requirement["count_trace"]["explicit_quantity"] == 3
    assert requirement["dimensions"] == {"length_mm": 900}
    assert requirement["resolution_trace"]["part_number"]["resolution"] == "function_default"


def test_supplied_argument_overrides_function_default():
    requirement = first_requirement({
        "design.py": """
def make_member(length, part_number="WRONG-DEFAULT"):
    return None

left = make_member(900, part_number="TEST-OVERRIDE")
""",
    })

    assert requirement["part_number"] == "TEST-OVERRIDE"
    assert requirement["resolution_trace"]["part_number"]["resolution"] == "literal"


def test_custom_apex_bracket_gets_stable_generated_key_that_changes_with_pitch():
    files = {
        "design.py": """
def make_apex_bracket(roof_pitch, material="G250"):
    return None

bracket = make_apex_bracket(roof_pitch=20, material="G250")
""",
    }
    tree = component_tree("Apex_Bracket")

    requirement = first_requirement(files, tree)
    repeat = first_requirement(files, deepcopy(tree))
    changed = first_requirement({
        "design.py": files["design.py"].replace("roof_pitch=20", "roof_pitch=25"),
    }, deepcopy(tree))

    assert requirement["part_number"] == "AB-20"
    assert repeat["part_number"] == requirement["part_number"]
    assert changed["part_number"] != requirement["part_number"]
    assert requirement["resolution_trace"]["part_number"]["resolution"] == "generated_compact_identity"


def test_mesh_only_noise_uses_source_components_without_mesh_requirements():
    source = analyze_design_sources({
        "design.py": """
def make_member(length, part_number):
    return None

left = make_member(1200, part_number="TEST-NOISE")
""",
    })
    tree = analyze_gltf_tree({
        "name": "Scene",
        "children": [
            {"name": "mesh_1", "type": "Mesh", "isMesh": True},
            {"name": "node_123", "type": "Object3D", "children": []},
        ],
    })
    analysis = build_procurement_analysis(source, tree)

    assert tree["assemblies"] == []
    assert tree["components"] == []
    assert [item["part_number"] for item in analysis["requirements"]] == ["TEST-NOISE"]
    assert {item["orderable"] for item in analysis["requirements"]} == {False}
    assert {item["quantity_source"] for item in analysis["requirements"]} == {"diagnostic_placeholder"}
    assert analysis["analysis_mode"] == "source_diagnostic"
    assert analysis["quantity_authority"] == "diagnostic_only"
    assert analysis["components"][0]["visual_node_ids"] == []
    assert any(diagnostic["code"] == "source_only_components_no_visual_tree" for diagnostic in analysis["diagnostics"])


def test_imported_opaque_helper_expands_internal_bom_component_calls():
    source = analyze_design_sources({
        "design.py": """
from foundation import make_foundation

foundation = make_foundation()
""",
        "foundation.py": """
def make_blocks(*, part_number="BLOCK-A", quantity=80, unit="each"):
    return None

def make_concrete(*, part_number="CONCRETE-A", quantity=0.288, unit="m3"):
    return None

def make_foundation():
    blocks = make_blocks()
    concrete = make_concrete()
    return [blocks, concrete]
""",
    })
    analysis = build_procurement_analysis(source, {"assemblies": [], "components": [], "diagnostics": []}, explicit_manifest={})

    requirements = {item["part_number"]: item for item in analysis["requirements"]}
    assert set(requirements) == {"BLOCK-A", "CONCRETE-A"}
    assert requirements["BLOCK-A"]["quantity"] == 1
    assert requirements["CONCRETE-A"]["quantity"] == 1
    assert {item["quantity_source"] for item in requirements.values()} == {"diagnostic_placeholder"}
    assert {item["orderable"] for item in requirements.values()} == {False}
    assert requirements["BLOCK-A"]["count_trace"]["explicit_quantity"] == 80
    assert requirements["CONCRETE-A"]["count_trace"]["explicit_quantity"] == 0.288
    assert [assembly["label"] for assembly in analysis["assemblies"]] == ["Foundation"]


def test_partial_gltf_labels_are_supplemented_with_unmatched_source_components():
    source = analyze_design_sources({
        "design.py": """
def make_member(length, part_number):
    return None

left = make_member(1200, part_number="LEFT")
right = make_member(1200, part_number="RIGHT")
""",
    })
    tree = analyze_gltf_tree(component_tree("Left"))

    analysis = build_procurement_analysis(source, tree)

    assert [item["part_number"] for item in analysis["requirements"]] == ["LEFT"]
    assert analysis["components"][0]["visual_node_ids"]
    assert any(diagnostic["code"] == "source_metadata_without_visual_component" for diagnostic in analysis["diagnostics"])


def test_parent_named_group_is_assembly_and_leaf_named_group_is_component():
    tree = analyze_gltf_tree(component_tree("Left_Column"))

    assert [assembly["label"] for assembly in tree["assemblies"]] == ["Portal"]
    assert [component["label"] for component in tree["components"]] == ["Left_Column"]
    assert tree["components"][0]["assembly_id"] == tree["assemblies"][0]["id"]


def test_golden_procurement_analysis_uses_resolved_design_values_not_magic_products():
    files = {
        "design.py": """
PURLIN_PART_NUMBER = "TEST-GOLDEN"
column_height = 2100

def make_member(length, part_number):
    return None

left = make_member(column_height, part_number=PURLIN_PART_NUMBER)
""",
    }

    analysis = build_procurement_analysis(
        analyze_design_sources(files),
        analyze_gltf_tree(component_tree("Left_Column")),
    )

    assert analysis["source"] == "deterministic_analysis"
    assert analysis["assemblies"] == [
        {
            "id": "portal",
            "label": "Portal",
            "path": "Portal",
            "parent_id": None,
        }
    ]
    assert analysis["components"][0]["label"] == "Left_Column"
    requirement = analysis["requirements"][0]
    assert requirement["part_number"] == "TEST-GOLDEN"
    assert requirement["stock_number"] == "TEST-GOLDEN-21"
    assert requirement["quantity"] == 1
    assert requirement["rolled_up_quantity"] == 1
    assert requirement["quantity_source"] == "visual_instances"
    assert requirement["orderable"] is True
    assert requirement["dimensions"] == {"length_mm": 2100}
    assert requirement["resolution_trace"]["part_number"]["resolution"] == "literal_assignment"


def test_varied_part_numbers_prove_identity_is_data_driven():
    for part_number in ["ALPHA-001", "BETA-999", "GAMMA-42"]:
        requirement = first_requirement({
            "design.py": f'''
def make_member(length, part_number):
    return None

left = make_member(1000, part_number="{part_number}")
''',
        })

        assert requirement["part_number"] == part_number


def test_visual_components_provide_verified_quantity_without_extra_source_calls():
    source = analyze_design_sources({
        "design.py": """
def make_member(part_number):
    return None

prototype = make_member("VISUAL-COUNT")
""",
    })
    tree = {
        "assemblies": [{"id": "assembly", "label": "Assembly", "path": "Assembly", "parent_id": None}],
        "components": [
            {
                "id": f"assembly-member-{index}",
                "label": "Member",
                "path": "Assembly/Member",
                "assembly_id": "assembly",
                "visual_node_ids": [f"node-{index}"],
            }
            for index in range(3)
        ],
        "diagnostics": [],
    }

    analysis = build_procurement_analysis(source, tree)
    requirements = analysis["requirements"]

    assert [requirement["part_number"] for requirement in requirements] == ["VISUAL-COUNT"] * 3
    assert sum(requirement["quantity"] for requirement in requirements) == 3
    assert sum(requirement["rolled_up_quantity"] for requirement in requirements) == 3
    assert {requirement["quantity_source"] for requirement in requirements} == {"visual_instances"}
    assert {requirement["quantity_confidence"] for requirement in requirements} == {"verified"}
    assert {requirement["visual_instance_count"] for requirement in requirements} == {1}
    assert {requirement["source_call_count"] for requirement in requirements} == {1}


def test_countable_visual_quantity_is_not_multiplied_by_source_assembly_count():
    source = analyze_design_sources({
        "design.py": """
positions = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17]

def make_screw(*, part_number="6-310-3117-5C4", quantity=1, unit="each"):
    return None

def roof_battens():
    screw = make_screw()
    return screw

def building():
    proto = roof_battens()
    placed = []
    for position in positions:
        placed.append(proto)
    return placed

model = building()
""",
    })
    tree = {
        "assemblies": [],
        "components": [
            {
                "id": f"roof-batten-tek-screw-{index}",
                "label": "Roof Batten Tek Screw",
                "path": "Roof Battens/Roof Batten Tek Screws/6-310-3117-5C4",
                "assembly_id": None,
                "visual_node_ids": [f"node-{index}"],
            }
            for index in range(42)
        ],
        "diagnostics": [],
    }

    analysis = build_procurement_analysis(source, tree)
    requirements = analysis["requirements"]

    assert {requirement["part_number"] for requirement in requirements} == {"6-310-3117-5C4"}
    assert sum(requirement["quantity"] for requirement in requirements) == 42
    assert sum(requirement["rolled_up_quantity"] for requirement in requirements) == 42
    assert {requirement["quantity_source"] for requirement in requirements} == {"visual_instances"}
    assert {requirement["quantity_confidence"] for requirement in requirements} == {"verified"}
    assert {requirement["count_trace"]["assembly_instance_multiplier"] for requirement in requirements} == {1}
    assert {requirement["count_trace"]["visual_instance_count"] for requirement in requirements} == {1}
    assert {requirement["count_trace"]["source_call_count"] for requirement in requirements} == {1}


def test_sheet_visual_quantity_is_not_multiplied_by_explicit_source_count():
    source = analyze_design_sources({
        "design.py": """
def make_sheet(*, part_number="CUSTOM-ORB", quantity=14, unit="sheet"):
    return None

sheet_proto = make_sheet()
""",
    })
    tree = {
        "assemblies": [{"id": "roof-cladding", "label": "Roof Cladding", "path": "Roof Cladding", "parent_id": None}],
        "components": [
            {
                "id": f"roof-sheet-{index}",
                "label": f"Roof Sheet {index}",
                "path": f"Roof Cladding/Roof Sheet {index}",
                "assembly_id": "roof-cladding",
                "visual_node_ids": [f"node-{index}"],
            }
            for index in range(4)
        ],
        "diagnostics": [],
    }

    analysis = build_procurement_analysis(source, tree)
    requirements = analysis["requirements"]

    assert {requirement["part_number"] for requirement in requirements} == {"CUSTOM-ORB"}
    assert sum(requirement["quantity"] for requirement in requirements) == 4
    assert sum(requirement["rolled_up_quantity"] for requirement in requirements) == 4
    assert {requirement["unit"] for requirement in requirements} == {"sheet"}
    assert {requirement["quantity_source"] for requirement in requirements} == {"visual_instances"}
    assert {requirement["count_trace"]["visual_count_is_quantity"] for requirement in requirements} == {True}


def test_length_visual_quantity_is_not_multiplied_by_source_placements():
    source = analyze_design_sources({
        "design.py": """
def make_gutter(*, part_number="QUAD-GUTTER-115", quantity=2, unit="length"):
    return None

gutter = make_gutter()
""",
    })
    tree = {
        "assemblies": [{"id": "gutters", "label": "Gutters", "path": "Gutters", "parent_id": None}],
        "components": [
            {
                "id": f"gutter-{index}",
                "label": f"Long Wall Gutter {index}",
                "path": f"Gutters/Long Wall Gutter {index}",
                "assembly_id": "gutters",
                "visual_node_ids": [f"node-{index}"],
            }
            for index in range(2)
        ],
        "diagnostics": [],
    }

    analysis = build_procurement_analysis(source, tree)
    requirements = analysis["requirements"]

    assert {requirement["part_number"] for requirement in requirements} == {"QUAD-GUTTER-115"}
    assert sum(requirement["quantity"] for requirement in requirements) == 2
    assert sum(requirement["rolled_up_quantity"] for requirement in requirements) == 2
    assert {requirement["unit"] for requirement in requirements} == {"length"}
    assert {requirement["quantity_source"] for requirement in requirements} == {"visual_instances"}
    assert {requirement["count_trace"]["visual_count_is_quantity"] for requirement in requirements} == {True}


def test_visual_source_match_prefers_scope_tokens_over_generic_sheet_overlap():
    source = analyze_design_sources({
        "design.py": """
def make_sheet(length, *, part_number="CUSTOM-ORB", quantity=14, unit="sheet"):
    return None

def long_wall_cladding():
    return make_sheet(2400)

def roof_cladding():
    return make_sheet(1800)

wall = long_wall_cladding()
roof = roof_cladding()
""",
    })
    tree = {
        "assemblies": [{"id": "roof-cladding", "label": "Roof Cladding", "path": "Roof Cladding", "parent_id": None}],
        "components": [{
            "id": "roof-sheet",
            "label": "Left Roof Sheet 1",
            "path": "Roof Cladding/Left Roof Sheet 1",
            "assembly_id": "roof-cladding",
            "visual_node_ids": ["node-a"],
        }],
        "diagnostics": [],
    }

    analysis = build_procurement_analysis(source, tree)
    requirement = analysis["requirements"][0]

    assert requirement["source_trace"]["source_scope"] == "roof_cladding"
    assert requirement["dimensions"] == {"length_mm": 1800}
    assert requirement["quantity"] == 1
    assert requirement["unit"] == "sheet"


def test_bulk_quantity_uses_explicit_source_quantity_not_visual_instances():
    source = analyze_design_sources({
        "design.py": """
def make_concrete(*, part_number="CONCRETE-A", quantity=0.288, unit="m3"):
    return None

footing = make_concrete()
""",
    })
    tree = {
        "assemblies": [{"id": "foundation", "label": "Foundation", "path": "Foundation", "parent_id": None}],
        "components": [{
            "id": "foundation-concrete",
            "label": "Concrete",
            "path": "Foundation/Concrete",
            "assembly_id": "foundation",
            "visual_node_ids": ["node-a", "node-b", "node-c"],
            "visual_instance_count": 3,
        }],
        "diagnostics": [],
    }

    analysis = build_procurement_analysis(source, tree)
    requirement = analysis["requirements"][0]

    assert requirement["part_number"] == "CONCRETE-A"
    assert requirement["quantity"] == 0.288
    assert requirement["rolled_up_quantity"] == 0.288
    assert requirement["quantity_source"] == "metadata_quantity_non_discrete"
    assert requirement["quantity_confidence"] == "verified"
    assert requirement["orderable"] is True
    assert requirement["visual_instance_count"] == 1
    assert requirement["count_trace"]["visual_count_not_quantity"] is True
    assert requirement["count_trace"]["quantity_unit"] == "m3"
    assert not any(diagnostic["code"] == "quantity_evidence_mismatch" for diagnostic in analysis["diagnostics"])


def test_linear_unit_does_not_use_visual_instance_count_as_quantity():
    source = analyze_design_sources({
        "design.py": """
def make_drain(*, part_number="AGPIPE-100-SOCKED", unit="m"):
    return None

drain = make_drain()
""",
    })
    tree = {
        "assemblies": [{"id": "drainage", "label": "Drainage", "path": "Drainage", "parent_id": None}],
        "components": [{
            "id": "drainage-agpipe",
            "label": "Drain",
            "path": "Drainage/Drain",
            "assembly_id": "drainage",
            "visual_node_ids": ["node-a", "node-b", "node-c"],
            "visual_instance_count": 3,
        }],
        "diagnostics": [],
    }

    analysis = build_procurement_analysis(source, tree)
    requirement = analysis["requirements"][0]

    assert requirement["part_number"] == "AGPIPE-100-SOCKED"
    assert requirement["quantity"] == 1
    assert requirement["quantity_source"] == "diagnostic_placeholder"
    assert requirement["quantity_confidence"] == "diagnostic"
    assert requirement["orderable"] is False
    assert requirement["visual_instance_count"] == 1
    assert requirement["count_trace"]["visual_count_not_quantity"] is True
    assert requirement["count_trace"]["quantity_unit"] == "m"


def test_generated_render_children_are_visual_components_not_parent_quantity():
    source = analyze_design_sources({
        "design.py": """
def make_blocks(*, part_number="BLOCK-A", unit="each"):
    return None

blocks = make_blocks()
""",
    })
    tree = analyze_gltf_tree({
        "name": "Scene",
        "children": [
            {
                "name": "Wall",
                "type": "Object3D",
                "children": [
                    {
                        "name": "Blocks",
                        "type": "Object3D",
                        "children": [
                            {
                                "name": "=>[0:1]",
                                "type": "Object3D",
                                "children": [{"name": "mesh_1", "type": "Mesh", "isMesh": True}],
                            },
                            {
                                "name": "=>[0:2]",
                                "type": "Object3D",
                                "children": [{"name": "mesh_2", "type": "Mesh", "isMesh": True}],
                            },
                            {
                                "name": "=>[0:3]",
                                "type": "Object3D",
                                "children": [{"name": "mesh_3", "type": "Mesh", "isMesh": True}],
                            },
                        ],
                    }
                ],
            }
        ],
    })
    analysis = build_procurement_analysis(source, tree)

    assert [component["label"] for component in analysis["components"]] == ["=>[0:1]", "=>[0:2]", "=>[0:3]"]
    assert {requirement["part_number"] for requirement in analysis["requirements"]} == {"BLOCK-A"}
    assert [requirement["quantity"] for requirement in analysis["requirements"]] == [1, 1, 1]
    assert {requirement["quantity_source"] for requirement in analysis["requirements"]} == {"visual_instances"}
    assert {requirement["visual_instance_count"] for requirement in analysis["requirements"]} == {1}
    assert not any(diagnostic["code"] == "requirement_missing_part_number" for diagnostic in analysis["diagnostics"])


def test_named_mesh_leaves_are_visual_components_under_bucket_assembly():
    source = analyze_design_sources({
        "design.py": """
def make_anchor(*, part_number="RAMSET-ANKASCREW-M12X100-GAL", unit="each"):
    return None

anchors = make_anchor()
""",
    })
    tree = analyze_gltf_tree({
        "name": "Scene",
        "children": [
            {
                "name": "Portal Frame",
                "type": "Object3D",
                "children": [
                    {
                        "name": "Masonry Anchors",
                        "type": "Object3D",
                        "children": [
                            {
                                "name": "Ramset M12x100 Galvanised AnkaScrew Masonry Anchor",
                                "type": "Mesh",
                                "isMesh": True,
                            },
                            {
                                "name": "Ramset M12x100 Galvanised AnkaScrew Masonry Anchor",
                                "type": "Mesh",
                                "isMesh": True,
                            },
                            {
                                "name": "Ramset M12x100 Galvanised AnkaScrew Masonry Anchor",
                                "type": "Mesh",
                                "isMesh": True,
                            },
                        ],
                    }
                ],
            }
        ],
    })
    analysis = build_procurement_analysis(source, tree)

    assert [component["label"] for component in analysis["components"]] == [
        "Ramset M12x100 Galvanised AnkaScrew Masonry Anchor",
        "Ramset M12x100 Galvanised AnkaScrew Masonry Anchor",
        "Ramset M12x100 Galvanised AnkaScrew Masonry Anchor",
    ]
    assert {requirement["part_number"] for requirement in analysis["requirements"]} == {
        "RAMSET-ANKASCREW-M12X100-GAL",
    }
    assert sum(requirement["quantity"] for requirement in analysis["requirements"]) == 3
    assert {requirement["quantity_source"] for requirement in analysis["requirements"]} == {"visual_instances"}
    assert {requirement["visual_instance_count"] for requirement in analysis["requirements"]} == {1}


def test_named_mesh_leaf_part_number_is_counted_without_parent_name_hack():
    source = analyze_design_sources({
        "design.py": """
def lysaght_zc_cp(part_number):
    return None

cp = lysaght_zc_cp("100CP")
""",
    })
    tree = analyze_gltf_tree({
        "name": "Scene",
        "children": [
            {
                "name": "Fascia Bracket Assembly FBA01-51",
                "type": "Object3D",
                "children": [
                    {
                        "name": "=>011332",
                        "type": "Object3D",
                        "children": [
                            {"name": "100CP", "type": "Mesh", "isMesh": True},
                            {"name": "100CP", "type": "Mesh", "isMesh": True},
                        ],
                    }
                ],
            }
        ],
    })
    analysis = build_procurement_analysis(source, tree)

    requirements = [item for item in analysis["requirements"] if item["part_number"] == "100CP"]
    assert len(requirements) == 2
    assert sum(item["quantity"] for item in requirements) == 2
    assert {item["quantity_source"] for item in requirements} == {"visual_instances"}


def test_visual_fastener_assembly_uses_visual_instances_for_decomposed_bolt_and_nut():
    source = analyze_design_sources({
        "design.py": """
def make_fastener_assembly(size, length, grip_length):
    return None

assembly = make_fastener_assembly("M12", 25.0, 4.9)
""",
    })
    tree = analyze_gltf_tree({
        "name": "Scene",
        "children": [
            {
                "name": "Fastener Assembly",
                "type": "Object3D",
                "children": [
                    {"name": "mesh_1", "type": "Mesh", "isMesh": True},
                    {"name": "mesh_2", "type": "Mesh", "isMesh": True},
                ],
            }
        ],
    })
    analysis = build_procurement_analysis(source, tree)
    requirements = {item["source_trace"]["procurement_item"]: item for item in analysis["requirements"]}

    assert set(requirements) == {"bolt", "nut"}
    assert requirements["bolt"]["part_number"] == "DIN-6921-M12X25"
    assert requirements["nut"]["part_number"] == "DIN-6923-M12"
    assert {item["quantity"] for item in requirements.values()} == {1}
    assert {item["quantity_source"] for item in requirements.values()} == {"visual_instances"}
    assert {item["count_trace"]["visual_instance_count"] for item in requirements.values()} == {1}
    assert not any(
        diagnostic["code"] == "visual_container_without_procurement_identity"
        for diagnostic in analysis["diagnostics"]
    )


def test_generated_component_identity_groups_repeated_visual_instances_by_source_function():
    source = analyze_design_sources({
        "design.py": """
def make_floor_joist(y):
    return None

joists = [
    make_floor_joist(1185.6),
    make_floor_joist(1214.4),
    make_floor_joist(14.4),
]
""",
    })
    tree = {
        "assemblies": [{"id": "floor", "label": "Floor", "path": "Floor", "parent_id": None}],
        "components": [
            {
                "id": "floor-joist-y-1185-6",
                "label": "Floor_Joist_Y_1185.6",
                "path": "Floor/Floor_Joist_Y_1185.6",
                "assembly_id": "floor",
                "visual_node_ids": ["node-a"],
            },
            {
                "id": "floor-joist-y-1214-4",
                "label": "Floor_Joist_Y_1214.4",
                "path": "Floor/Floor_Joist_Y_1214.4",
                "assembly_id": "floor",
                "visual_node_ids": ["node-b"],
            },
            {
                "id": "floor-joist-y-14-4",
                "label": "Floor_Joist_Y_14.4",
                "path": "Floor/Floor_Joist_Y_14.4",
                "assembly_id": "floor",
                "visual_node_ids": ["node-c"],
            },
        ],
        "diagnostics": [],
    }

    analysis = build_procurement_analysis(source, tree)
    part_numbers = [requirement["part_number"] for requirement in analysis["requirements"]]

    assert len(set(part_numbers)) == 1
    assert part_numbers[0].startswith("COMPONENT-FLOOR-JOIST-")
    assert "1185" not in part_numbers[0]
    assert "1214" not in part_numbers[0]
    assert "14-4" not in part_numbers[0]


def test_visual_assembly_container_without_identity_is_diagnostic_not_bom_line():
    source = analyze_design_sources({
        "design.py": """
def make_floor_assembly():
    return None

floor = make_floor_assembly()
""",
    })
    tree = {
        "assemblies": [],
        "components": [{
            "id": "floor-assembly",
            "label": "Floor_Assembly",
            "path": "Floor_Assembly",
            "assembly_id": None,
            "visual_node_ids": [f"node-{index}" for index in range(69)],
            "visual_instance_count": 69,
        }],
        "diagnostics": [],
    }

    analysis = build_procurement_analysis(source, tree)

    assert analysis["requirements"] == []
    diagnostic = next(
        item
        for item in analysis["diagnostics"]
        if item["code"] == "visual_container_without_procurement_identity"
    )
    assert diagnostic["component_id"] == "floor-assembly"
    assert diagnostic["source_line"] == 5


def test_foundation_semantic_visual_aggregates_remain_visible_as_missing_identity_rows():
    source = analyze_design_sources({
        "design.py": """
def make_foundation():
    return None

foundation = make_foundation()
""",
    })
    tree = {
        "assemblies": [{"id": "foundation", "label": "Foundation Assembly", "path": "Foundation Assembly", "parent_id": None}],
        "components": [
            {
                "id": "foundation-concrete-pads",
                "label": "Concrete Pads",
                "path": "Foundation Assembly/Concrete Pads",
                "assembly_id": "foundation",
                "visual_node_ids": ["pad-a", "pad-b", "pad-c", "pad-d"],
                "visual_instance_count": 4,
            },
            {
                "id": "foundation-rebar",
                "label": "Rebar",
                "path": "Foundation Assembly/Rebar",
                "assembly_id": "foundation",
                "visual_node_ids": [f"bar-{index}" for index in range(12)],
                "visual_instance_count": 12,
            },
        ],
        "diagnostics": [],
    }

    analysis = build_procurement_analysis(source, tree)
    rows_by_label = {
        item["dimensions"].get("component_label"): item
        for item in analysis["requirements"]
    }

    assert set(rows_by_label) == {"Concrete Pads", "Rebar"}
    assert rows_by_label["Concrete Pads"]["part_number"] is None
    assert rows_by_label["Rebar"]["part_number"] is None
    assert not any(diagnostic["code"] == "visual_container_without_procurement_identity" for diagnostic in analysis["diagnostics"])


def test_dimensioned_visual_aggregate_mark_is_not_generated_bom_line():
    source = analyze_design_sources({
        "design.py": """
def portal_frame(mark, width_mm, height_mm, angle_deg):
    return None

single_portal = portal_frame(mark="PF01", width_mm=3100, height_mm=2400, angle_deg=20)
""",
    })
    tree = {
        "assemblies": [],
        "components": [{
            "id": "pf01",
            "label": "PF01-31-24-20",
            "path": "Shed/PF01-31-24-20",
            "assembly_id": None,
            "visual_node_ids": [f"node-{index}" for index in range(12)],
            "visual_instance_count": 12,
        }],
        "diagnostics": [],
    }

    analysis = build_procurement_analysis(source, tree)

    assert analysis["requirements"] == []
    diagnostic = next(
        item
        for item in analysis["diagnostics"]
        if item["code"] == "visual_container_without_procurement_identity"
    )
    assert diagnostic["component_id"] == "pf01"
    assert diagnostic["message"]


def test_source_call_only_analysis_marks_quantity_probable_and_counts_grouped_calls():
    source = analyze_design_sources({
        "design.py": """
def make_member(part_number):
    return None

left = make_member("SOURCE-COUNT")
right = make_member("SOURCE-COUNT")
""",
    })

    analysis = build_procurement_analysis(source, {"assemblies": [], "components": [], "diagnostics": []}, explicit_manifest={})
    requirements = [item for item in analysis["requirements"] if item["part_number"] == "SOURCE-COUNT"]

    assert len(requirements) == 2
    assert {item["quantity"] for item in requirements} == {1}
    assert {item["rolled_up_quantity"] for item in requirements} == {1}
    assert {item["quantity_source"] for item in requirements} == {"diagnostic_placeholder"}
    assert {item["quantity_confidence"] for item in requirements} == {"diagnostic"}
    assert {item["orderable"] for item in requirements} == {False}
    assert {item["source_call_count"] for item in requirements} == {2}


def test_explicit_and_visual_quantity_mismatch_emits_diagnostic():
    source = analyze_design_sources({
        "design.py": """
def make_member(part_number, quantity):
    return None

prototype = make_member("COUNT-MISMATCH", quantity=2)
""",
    })
    tree = {
        "assemblies": [{"id": "assembly", "label": "Assembly", "path": "Assembly", "parent_id": None}],
        "components": [{
            "id": "assembly-member",
            "label": "Member",
            "path": "Assembly/Member",
            "assembly_id": "assembly",
            "visual_node_ids": ["node-a", "node-b", "node-c"],
            "visual_instance_count": 3,
        }],
        "diagnostics": [],
    }

    analysis = build_procurement_analysis(source, tree)
    requirement = analysis["requirements"][0]

    assert requirement["quantity"] == 1
    assert requirement["rolled_up_quantity"] == 1
    assert requirement["quantity_source"] == "visual_instances"
    assert requirement["quantity_confidence"] == "diagnostic"
    assert any(diagnostic["code"] == "quantity_evidence_mismatch" for diagnostic in analysis["diagnostics"])


def test_explicit_manifest_requirements_receive_quantity_evidence_defaults():
    analysis = build_procurement_analysis(
        {"calls": []},
        {"assemblies": [], "components": [], "diagnostics": []},
        explicit_manifest={
            "scopes": [],
            "components": [],
            "requirements": [{
                "id": "explicit.requirement",
                "component_id": "explicit",
                "part_number": "EXPLICIT-PART",
                "quantity": 4,
                "unit": "each",
                "dimensions": {},
            }],
            "diagnostics": [],
        },
    )

    requirement = analysis["requirements"][0]
    assert requirement["quantity"] == 4
    assert requirement["rolled_up_quantity"] == 4
    assert requirement["quantity_source"] == "explicit_manifest"
    assert requirement["quantity_confidence"] == "verified"
    assert requirement["orderable"] is True
    assert requirement["source_call_count"] == 1
    assert requirement["count_trace"]["explicit_quantity"] == 4


def test_visual_tree_ignores_explicit_manifest_requirement_quantities():
    source = analyze_design_sources({
        "design.py": """
def make_member(part_number):
    return None

member = make_member("VISUAL-WINS")
""",
    })
    analysis = build_procurement_analysis(
        source,
        analyze_gltf_tree(component_tree("Member")),
        explicit_manifest={
            "scopes": [],
            "components": [],
            "requirements": [{
                "id": "manifest.requirement",
                "component_id": "manifest-component",
                "part_number": "MANIFEST-SHOULD-NOT-WIN",
                "quantity": 99,
                "unit": "each",
                "dimensions": {},
            }],
            "diagnostics": [],
        },
    )

    requirement = analysis["requirements"][0]
    assert analysis["analysis_mode"] == "visual_verified"
    assert analysis["quantity_authority"] == "visual_tree"
    assert requirement["part_number"] == "VISUAL-WINS"
    assert requirement["quantity"] == 1
    assert requirement["quantity_source"] == "visual_instances"
    assert requirement["orderable"] is True
    assert any(diagnostic["code"] == "explicit_manifest_ignored_for_visual_authority" for diagnostic in analysis["diagnostics"])


def test_explicit_manifest_fallback_rows_do_not_replace_deterministic_source_requirements():
    source = analyze_design_sources({
        "design.py": """
def make_member(length, part_number):
    return None

def portal_frame():
    left = make_member(1200, part_number="TEST-A")
    return left

portal = portal_frame()
""",
    })

    analysis = build_procurement_analysis(
        source,
        {"assemblies": [], "components": [], "diagnostics": []},
        explicit_manifest={
            "scopes": [
                {"id": "portal-frame-pf01", "label": "Portal_Frame_PF01"},
                {"id": "portal-frame-pf02", "label": "Portal_Frame_PF02"},
            ],
            "components": [{
                "id": "pf01-left-column",
                "scope_id": "portal-frame-pf01",
                "label": "PF01_Left_Column",
                "role": "component",
                "visual_node_ids": ["pf01-left-column"],
            }],
            "requirements": [{
                "id": "pf01-left-column.requirement",
                "component_id": "pf01-left-column",
                "scope_id": "portal-frame-pf01",
                "part_number": None,
                "quantity": 1,
                "unit": "each",
                "dimensions": {"component_label": "PF01_Left_Column"},
            }],
            "diagnostics": [],
        },
    )

    assert analysis["source"] == "diagnostic_source_analysis"
    assert analysis["analysis_mode"] == "source_diagnostic"
    assert [assembly["label"] for assembly in analysis["assemblies"]] == ["Portal Frame"]
    assert [requirement["part_number"] for requirement in analysis["requirements"]] == ["TEST-A"]
    assert {requirement["orderable"] for requirement in analysis["requirements"]} == {False}


def test_component_label_only_explicit_manifest_rows_are_not_procurement_requirements():
    analysis = build_procurement_analysis(
        {"calls": []},
        {"assemblies": [], "components": [], "diagnostics": []},
        explicit_manifest={
            "scopes": [{"id": "portal-frame-pf01", "label": "Portal_Frame_PF01-31-24-20"}],
            "components": [{
                "id": "pf01-left-column",
                "scope_id": "portal-frame-pf01",
                "label": "PF01_Left_Column",
                "role": "component",
                "visual_node_ids": ["pf01-left-column"],
            }],
            "requirements": [{
                "id": "pf01-left-column.requirement",
                "component_id": "pf01-left-column",
                "scope_id": "portal-frame-pf01",
                "part_number": None,
                "quantity": 1,
                "unit": "each",
                "dimensions": {"component_label": "PF01_Left_Column"},
            }],
            "diagnostics": [],
        },
    )

    assert analysis["requirements"] == []
    assert any(diagnostic["code"] == "explicit_manifest_visual_rows_ignored" for diagnostic in analysis["diagnostics"])


def test_non_geometry_lookup_function_with_part_number_is_not_a_requirement():
    source = analyze_design_sources({
        "design.py": """
class ProductSpec:
    pass

def product_table(part_number: str) -> ProductSpec:
    return ProductSpec()

def make_member(part_number):
    spec = product_table(part_number)
    return None

member = make_member("LOOKUP-ITEM")
""",
    })
    analysis = build_procurement_analysis(source, analyze_gltf_tree(component_tree("Member")))

    assert [item["part_number"] for item in analysis["requirements"]] == ["LOOKUP-ITEM"]
    assert all(item["source_trace"]["function"] != "product_table" for item in analysis["requirements"])


def test_source_only_repeated_assembly_scope_multiplies_child_quantities():
    source = analyze_design_sources({
        "design.py": """
positions = [0, 1, 2]

def member(part_number):
    return None

def frame():
    left = member("REPEATED-MEMBER")
    right = member("REPEATED-MEMBER")
    return [left, right]

def building():
    prototype = frame()
    placed = []
    for position in positions:
        placed.append(prototype.children)
    return placed

model = building()
""",
    })
    analysis = build_procurement_analysis(source, {"assemblies": [], "components": [], "diagnostics": []}, explicit_manifest={})
    requirements = [item for item in analysis["requirements"] if item["part_number"] == "REPEATED-MEMBER"]

    assert len(requirements) == 2
    assert {item["quantity"] for item in requirements} == {1}
    assert {item["rolled_up_quantity"] for item in requirements} == {1}
    assert {item["quantity_source"] for item in requirements} == {"diagnostic_placeholder"}
    assert {item["orderable"] for item in requirements} == {False}
    assert {item["count_trace"]["assembly_instance_multiplier"] for item in requirements} == {3}
    assert {item["source_call_count"] for item in requirements} == {2}


def test_fastener_assembly_decomposes_into_bolt_and_nut_requirements_with_placements():
    source = analyze_design_sources({
        "design.py": """
positions = [0, 1, 2]

def apex_bracket():
    holes = []
    for dist in [1, 2]:
        for side in [-1, 1]:
            holes.append((dist, side))
            holes.append((-dist, side))
    return None, holes

def knee_bracket_pair():
    holes = []
    for dist in [1, 2]:
        for side in [-1, 1]:
            holes.append((dist, side))
            holes.append((dist, -side))
    holes_r = [(-h[0], h[1]) for h in holes]
    return None, None, holes, holes_r

def make_fastener_assembly(size, length, grip_length):
    return None

def portal_frame():
    _apex, apex_holes = apex_bracket()
    _knee_l, _knee_r, knee_holes_l, knee_holes_r = knee_bracket_pair()
    fastener_base = make_fastener_assembly("M12", 25.0, 4.9)
    fastener_base = fastener_base.moved(None)
    fasteners = []
    for h in apex_holes:
        fasteners.append(fastener_base.moved(h))
    for h in knee_holes_l + knee_holes_r:
        fasteners.append(fastener_base.moved(h))
    base_holes = []
    for bx in [-1, 1]:
        for hx in [-1, 1]:
            base_holes.append((bx, hx))
    for h in base_holes:
        fasteners.append(fastener_base.moved(h))
    return fasteners

def building():
    prototype = portal_frame()
    placed = []
    for position in positions:
        placed.append(prototype.children)
    return placed

model = building()
""",
    })

    analysis = build_procurement_analysis(source, {"assemblies": [], "components": [], "diagnostics": []}, explicit_manifest={})
    fasteners = [
        item for item in analysis["requirements"]
        if item["source_trace"].get("decomposed_from") == "fastener_assembly"
        and item["source_trace"].get("source_scope") == "portal_frame"
    ]

    assert [item["source_trace"]["procurement_item"] for item in fasteners] == ["bolt", "nut"]
    assert {item["quantity"] for item in fasteners} == {1}
    assert {item["rolled_up_quantity"] for item in fasteners} == {1}
    assert {item["quantity_source"] for item in fasteners} == {"diagnostic_placeholder"}
    assert {item["orderable"] for item in fasteners} == {False}
    assert {item["count_trace"]["source_instance_count"] for item in fasteners} == {28}
    assert {item["count_trace"]["assembly_instance_multiplier"] for item in fasteners} == {3}
    assert {item["unit"] for item in fasteners} == {"each"}
    assert {item["part_number"] for item in fasteners} == {"DIN-6921-M12X25", "DIN-6923-M12"}
    assert next(item for item in fasteners if item["source_trace"]["procurement_item"] == "bolt")["dimensions"] == {
        "size": "M12",
        "length_mm": 25.0,
        "grip_length_mm": 4.9,
    }
    assert next(item for item in fasteners if item["source_trace"]["procurement_item"] == "nut")["dimensions"] == {"size": "M12"}


def test_catalog_backed_factory_wrapper_resolves_procurement_identity():
    source = analyze_design_sources({
        "design.py": """
from roofing_fasteners import make_wall_cladding_screw

def wall_fasteners():
    screw = make_wall_cladding_screw()
    return screw

model = wall_fasteners()
""",
        "roofing_fasteners.py": """
from dataclasses import dataclass

@dataclass(frozen=True)
class FastenerSpec:
    part_number: str
    length_max: float
    standard: str = "AS 3566.1"
    finish: str = "Climaseal 4"

FASTENERS = {
    "wall": FastenerSpec(
        part_number="6-310-3117-5C4",
        length_max=15.5,
    ),
}

def make_roofing_fastener(key="wall", length=None):
    return None

def make_wall_cladding_screw(length=None):
    return make_roofing_fastener("wall", length=length)
""",
    })

    analysis = build_procurement_analysis(source, {"assemblies": [], "components": [], "diagnostics": []}, explicit_manifest={})
    requirements = [item for item in analysis["requirements"] if item["part_number"] == "6-310-3117-5C4"]

    assert len(requirements) == 1
    assert requirements[0]["dimensions"] == {"length_mm": 15.5}
    assert requirements[0]["finish"] == "Climaseal 4"
    assert requirements[0]["resolution_trace"]["part_number"]["resolution"] == "static_product_table"
    assert all(item["source_trace"]["source_file"] == "design.py" for item in analysis["requirements"])


def test_source_placement_counts_generic_make_prototypes_with_range_and_continue():
    source = analyze_design_sources({
        "design.py": """
sheet_count = 3
offsets = [0, 10]

def make_screw(part_number):
    return None

def fasteners():
    screw = make_screw("SCR-001").moved(None)
    placed = []
    for i in range(sheet_count):
        for offset in offsets:
            y = i * 10 + offset
            if not (0 <= y <= 20):
                continue
            placed.append(screw.moved(y))
    return placed

model = fasteners()
""",
    })

    analysis = build_procurement_analysis(source, {"assemblies": [], "components": [], "diagnostics": []}, explicit_manifest={})
    requirements = [item for item in analysis["requirements"] if item["part_number"] == "SCR-001"]

    assert len(requirements) == 1
    assert requirements[0]["quantity"] == 1
    assert requirements[0]["rolled_up_quantity"] == 1
    assert requirements[0]["quantity_source"] == "diagnostic_placeholder"
    assert requirements[0]["orderable"] is False
    assert requirements[0]["count_trace"]["source_instance_count"] == 5


def test_explicit_quantity_one_inside_static_loop_counts_each_source_instance():
    source = analyze_design_sources({
        "design.py": """
positions = [0, 1, 2]

def make_batten(part_number, quantity=1):
    return None

def battens():
    rows = []
    for position in positions:
        proto = make_batten("BATTEN", quantity=1)
        rows.append(proto.moved(position))
    return rows

model = battens()
""",
    })

    analysis = build_procurement_analysis(source, {"assemblies": [], "components": [], "diagnostics": []}, explicit_manifest={})
    battens = [item for item in analysis["requirements"] if item["part_number"] == "BATTEN"]

    assert len(battens) == 1
    assert battens[0]["quantity"] == 1
    assert battens[0]["quantity_source"] == "diagnostic_placeholder"
    assert battens[0]["orderable"] is False
    assert battens[0]["rolled_up_quantity"] == 1
    assert battens[0]["count_trace"]["source_instance_count"] == 3


def test_source_placement_counts_nested_compound_prototypes():
    source = analyze_design_sources({
        "design.py": """
positions = [0, 1, 2]

class bd:
    class Compound:
        pass

def lysaght_zc_cp(part_number):
    return None

def make_fastener_assembly(size, length, grip_length):
    return None

def fascia_brackets():
    cp_base = lysaght_zc_cp("100CP").moved(None)
    fastener_base = make_fastener_assembly("M12", 25.0, 4.9)
    fastener_base = fastener_base.moved(None)
    cp_assembly = bd.Compound(children=[
        cp_base,
        fastener_base.moved(15),
        fastener_base.moved(-15),
    ])
    brackets = []
    for y in positions:
        brackets.append(cp_assembly.moved(("left", y)))
        brackets.append(cp_assembly.moved(("right", y)))
    return bd.Compound(children=brackets)

model = fascia_brackets()
""",
    })

    analysis = build_procurement_analysis(source, {"assemblies": [], "components": [], "diagnostics": []}, explicit_manifest={})
    cp = [item for item in analysis["requirements"] if item["part_number"] == "100CP"]
    fasteners = [
        item for item in analysis["requirements"]
        if item["source_trace"].get("decomposed_from") == "fastener_assembly"
    ]

    assert len(cp) == 1
    assert cp[0]["quantity"] == 1
    assert cp[0]["quantity_source"] == "diagnostic_placeholder"
    assert cp[0]["orderable"] is False
    assert cp[0]["count_trace"]["source_instance_count"] == 6
    assert {item["quantity"] for item in fasteners} == {1}
    assert {item["quantity_source"] for item in fasteners} == {"diagnostic_placeholder"}


def test_visual_quantity_does_not_fall_back_to_larger_source_instance_count():
    source = analyze_design_sources({
        "design.py": """
positions = [0, 1, 2]

def lysaght_zc_cp(part_number):
    return None

def fascia_brackets():
    cp_base = lysaght_zc_cp("100CP").moved(None)
    brackets = []
    for y in positions:
        brackets.append(cp_base.moved(("left", y)))
        brackets.append(cp_base.moved(("right", y)))
    return brackets

model = fascia_brackets()
""",
    })
    tree = {
        "assemblies": [],
        "components": [{
            "id": "fascia-bracket-assembly",
            "label": "Fascia Bracket Assembly FBA01-51",
            "path": "Shed Building/Fascia Bracket Assembly FBA01-51",
            "assembly_id": None,
            "visual_node_ids": [f"node-{index}" for index in range(30)],
            "visual_instance_count": 30,
        }],
        "diagnostics": [],
    }

    analysis = build_procurement_analysis(source, tree)
    requirement = next(item for item in analysis["requirements"] if item["part_number"] == "100CP")

    assert requirement["quantity"] == 1
    assert requirement["rolled_up_quantity"] == 1
    assert requirement["quantity_source"] == "visual_instances"
    assert requirement["visual_instance_count"] == 1
    assert requirement["count_trace"]["source_instance_count"] == 6


def test_tuple_returned_bracket_pair_counts_leading_products_before_metadata():
    source = analyze_design_sources({
        "design.py": """
def knee_bracket_pair(mark="KB01"):
    left = object()
    right = object()
    holes = []
    holes_r = []
    return left, right, holes, holes_r

def portal_frame():
    knee_l, knee_r, knee_holes_l, knee_holes_r = knee_bracket_pair(mark="KB01")
    return knee_l, knee_r

model = portal_frame()
""",
    })

    analysis = build_procurement_analysis(source, {"assemblies": [], "components": [], "diagnostics": []}, explicit_manifest={})
    knees = [item for item in analysis["requirements"] if item["source_trace"]["function"] == "knee_bracket_pair"]

    assert len(knees) == 1
    assert knees[0]["quantity"] == 1
    assert knees[0]["quantity_source"] == "diagnostic_placeholder"
    assert knees[0]["orderable"] is False
    assert knees[0]["count_trace"]["source_instance_count"] == 2


def test_structural_member_section_identity_and_static_loop_count():
    source = analyze_design_sources({
        "design.py": """
def make_member(p1, p2, section, pitch=50.0):
    return None

def tower(n_panels=3, belt="90x90x8"):
    levels = n_panels + 1
    rows = []
    for i in range(1, levels):
        for side in range(4):
            rows.append(make_member((0, 0, 0), (0, 0, 1000), belt))
    return rows

model = tower()
""",
    })

    analysis = build_procurement_analysis(source, {"assemblies": [], "components": [], "diagnostics": []}, explicit_manifest={})
    belts = [item for item in analysis["requirements"] if item["part_number"] == "90x90x8"]

    assert len(belts) == 1
    assert belts[0]["quantity"] == 1
    assert belts[0]["quantity_source"] == "diagnostic_placeholder"
    assert belts[0]["orderable"] is False
    assert belts[0]["count_trace"]["source_instance_count"] == 12
    assert belts[0]["dimensions"] == {"length_mm": 1000.0}
    assert "angle_deg" not in belts[0]["dimensions"]


def test_plate_factory_dimensions_and_static_loop_count_are_source_derived():
    source = analyze_design_sources({
        "design.py": """
def make_plate(ctr, normal, w, h, t, label="plate"):
    return None

def plates():
    made = []
    for side in range(4):
        made.append(make_plate((0, 0, 0), (0, 0, 1), 250.0, 250.0, 10.0))
    return made

model = plates()
""",
    })

    analysis = build_procurement_analysis(source, {"assemblies": [], "components": [], "diagnostics": []}, explicit_manifest={})
    plates = [item for item in analysis["requirements"] if item["source_trace"]["function"] == "make_plate"]

    assert len(plates) == 1
    assert plates[0]["quantity"] == 1
    assert plates[0]["quantity_source"] == "diagnostic_placeholder"
    assert plates[0]["orderable"] is False
    assert plates[0]["count_trace"]["source_instance_count"] == 4
    assert plates[0]["dimensions"] == {"width_mm": 250.0, "height_mm": 250.0, "thickness_mm": 10.0}
    assert plates[0]["part_number"] == "P-2P5-2P5-0P1"


def test_fastener_assembly_counts_hole_bearing_member_calls_in_same_scope():
    source = analyze_design_sources({
        "design.py": """
def make_fastener_assembly(size, length, grip_length):
    return None

def make_member(section, holes_start=(0, 0), holes_end=(0, 0)):
    return None

def frame():
    fb = make_fastener_assembly("M16", 50.0, 30.0)
    members = []
    for side in range(4):
        members.append(make_member("ANGLE", holes_start=(0, 1), holes_end=(0, 2)))
    return members

model = frame()
""",
    })

    analysis = build_procurement_analysis(source, {"assemblies": [], "components": [], "diagnostics": []}, explicit_manifest={})
    fasteners = [
        item for item in analysis["requirements"]
        if item["source_trace"].get("decomposed_from") == "fastener_assembly"
    ]

    assert [item["source_trace"]["procurement_item"] for item in fasteners] == ["bolt", "nut"]
    assert {item["quantity"] for item in fasteners} == {1}
    assert {item["quantity_source"] for item in fasteners} == {"diagnostic_placeholder"}
    assert {item["orderable"] for item in fasteners} == {False}
    assert {item["count_trace"]["source_instance_count"] for item in fasteners} == {12}


def test_catalog_class_attributes_and_local_quantity_are_resolved_generically():
    source = analyze_design_sources({
        "design.py": """
import math
from products import SheetProduct

portal_y_positions = [0, 5100]
column_height = 2400
finish = "zincalume"

def make_sheet(length, quantity=1, part_number=None, material=None, unit="each", finish=None):
    return None

def wall_cladding():
    wall_height = column_height
    y_min = portal_y_positions[0]
    y_max = portal_y_positions[-1] + 100
    wall_length = y_max - y_min
    sheet_spacing = SheetProduct.cover_width
    sheet_count = math.ceil(wall_length / sheet_spacing)
    sheet = make_sheet(
        length=wall_height,
        quantity=sheet_count * 2,
        part_number=SheetProduct.part_number,
        material=SheetProduct.material,
        unit=SheetProduct.unit,
        finish=finish,
    )
    return sheet

model = wall_cladding()
""",
        "products.py": """
class SheetProduct:
    part_number = "SHEET-001"
    material = "steel"
    unit = "sheet"
    cover_width = 762.0
""",
    })
    analysis = build_procurement_analysis(source, {"assemblies": [], "components": [], "diagnostics": []}, explicit_manifest={})
    requirement = next(item for item in analysis["requirements"] if item["part_number"] == "SHEET-001")

    assert requirement["quantity"] == 1
    assert requirement["rolled_up_quantity"] == 1
    assert requirement["quantity_source"] == "diagnostic_placeholder"
    assert requirement["orderable"] is False
    assert requirement["count_trace"]["explicit_quantity"] == 14
    assert requirement["unit"] == "sheet"
    assert requirement["dimensions"] == {"length_mm": 2400}
    assert requirement["material"] == "steel"
    assert requirement["finish"] == "zincalume"
    assert requirement["resolution_trace"]["part_number"]["resolution"] == "imported_class_attribute"
    assert all(item["source_trace"]["function"] != "build" for item in analysis["requirements"])


def test_catalog_objects_trig_formulas_and_pure_helpers_resolve_generically():
    files = {
        "design.py": """
import math
from sections import section_tables
from products import TopHat50, SheetProduct

PURLIN_PART_NUMBER = "C10019"
portal_span = 3100.0
column_height = 2400.0
roof_pitch = 20.0
roof_sheet_order_increment = 100.0
roof_sheet_min_eave_overhang = 30.0
roof_sheet_apex_gap_half = 30.0

spec = section_tables(PURLIN_PART_NUMBER)
D = float(spec.D)
B = float(spec.B)
roof_batten_depth = TopHat50.height
span_center = portal_span - D
col_inner_x = (span_center / 2) - (D / 2)
apex_gap_half = 5.0
_roof_pitch_rad = math.radians(roof_pitch)
_roof_cos = math.cos(_roof_pitch_rad)
_roof_sin = math.sin(_roof_pitch_rad)
roof_stack_top_offset = D / 2 + roof_batten_depth
_ROOF_M = math.tan(math.radians(roof_pitch))
eave_outer_x = portal_span / 2 + B
_roof_support_c = column_height + _ROOF_M * eave_outer_x
_rafter_ref_c = _roof_support_c - roof_stack_top_offset / _roof_cos
_left_column_inner_top = (-col_inner_x, column_height)
left_rafter_start_x = _roof_cos * (
    _roof_cos * _left_column_inner_top[0]
    + _roof_sin * _left_column_inner_top[1]
    - _roof_sin * _rafter_ref_c
)
rafter_start_x = -left_rafter_start_x
rafter_length = (rafter_start_x - apex_gap_half) / _roof_cos

def round_up_to_increment(value, increment):
    return math.ceil(value / increment) * increment

def roof_sheet_length():
    eave_to_apex = (eave_outer_x - roof_sheet_apex_gap_half) / _roof_cos
    return round_up_to_increment(
        eave_to_apex + roof_sheet_min_eave_overhang,
        roof_sheet_order_increment,
    )

def make_member(length, part_number):
    return None

def make_sheet(length, quantity=1, part_number=None, material=None, unit="sheet"):
    return None

left_rafter = make_member(rafter_length, part_number=PURLIN_PART_NUMBER)
roof_sheet = make_sheet(
    length=roof_sheet_length(),
    quantity=14,
    part_number=SheetProduct.part_number,
    material=SheetProduct.material,
    unit=SheetProduct.unit,
)
""",
        "sections.py": """
ATTR_ALIAS = {"D": "depth_mm", "B": "flange_mm"}

def section_tables(part_number):
    return None
""",
        "products.py": """
class TopHat50:
    height = 50.0

class SheetProduct:
    part_number = "CUSTOM-ORB"
    material = "steel"
    unit = "sheet"
""",
        "catalog.json.py": """
{
  "sections": [
    {"key": "C10015 (100x1.5)", "part_number_alias": "C10019", "depth_mm": 102, "flange_mm": 51}
  ]
}
""",
    }
    source = analyze_design_sources(files)
    analysis = build_procurement_analysis(source, {"assemblies": [], "components": [], "diagnostics": []}, explicit_manifest={})
    requirements = {item["part_number"]: item for item in analysis["requirements"]}

    d = 102
    b = 51
    roof_cos = math.cos(math.radians(20.0))
    roof_sin = math.sin(math.radians(20.0))
    roof_m = math.tan(math.radians(20.0))
    span_center = 3100.0 - d
    col_inner_x = (span_center / 2) - (d / 2)
    roof_stack_top_offset = d / 2 + 50.0
    eave_outer_x = 3100.0 / 2 + b
    roof_support_c = 2400.0 + roof_m * eave_outer_x
    rafter_ref_c = roof_support_c - roof_stack_top_offset / roof_cos
    left_rafter_start_x = roof_cos * (
        roof_cos * (-col_inner_x)
        + roof_sin * 2400.0
        - roof_sin * rafter_ref_c
    )
    expected_rafter_length = (-left_rafter_start_x - 5.0) / roof_cos
    expected_sheet_length = math.ceil(((eave_outer_x - 30.0) / roof_cos + 30.0) / 100.0) * 100.0

    assert requirements["C10019"]["dimensions"]["length_mm"] == expected_rafter_length
    assert requirements["CUSTOM-ORB"]["dimensions"]["length_mm"] == expected_sheet_length
    assert requirements["CUSTOM-ORB"]["quantity"] == 1
    assert requirements["CUSTOM-ORB"]["quantity_source"] == "diagnostic_placeholder"
    assert requirements["CUSTOM-ORB"]["orderable"] is False
    assert requirements["CUSTOM-ORB"]["count_trace"]["explicit_quantity"] == 14
    assert requirements["CUSTOM-ORB"]["unit"] == "sheet"
    assert requirements["CUSTOM-ORB"]["material"] == "steel"


def test_static_numeric_resolves_chained_trigonometry_length():
    requirement = first_requirement({
        "design.py": """
import math

pitch_deg = 20.0
span = 3100.0
offset = 5.0
pitch_rad = math.radians(pitch_deg)
cos_pitch = math.cos(pitch_rad)
half_span = span / 2
rafter_length = (half_span - offset) / cos_pitch

def make_member(length, part_number):
    return None

member = make_member(rafter_length, part_number="TRIG-MEMBER")
""",
    })

    expected = (3100.0 / 2 - 5.0) / math.cos(math.radians(20.0))
    assert requirement["part_number"] == "TRIG-MEMBER"
    assert requirement["dimensions"]["length_mm"] == expected
    assert requirement["resolution_trace"]["part_number"]["resolution"] == "literal"


def test_static_numeric_resolves_round_up_to_increment_helper():
    requirement = first_requirement({
        "design.py": """
import math

raw_length = 1832.0

def round_up_to_increment(value, increment):
    return math.ceil(value / increment) * increment

def make_member(length, part_number):
    return None

member = make_member(round_up_to_increment(raw_length, 100.0), part_number="ROUND-MEMBER")
""",
    })

    assert requirement["dimensions"] == {"length_mm": 1900.0}


def test_static_numeric_resolves_safe_builtins_and_math_ceil():
    requirement = first_requirement({
        "design.py": """
import math

length = max(abs(-1200), min(1800, math.ceil(1200.2)))

def make_member(length, part_number):
    return None

member = make_member(length, part_number="SAFE-BUILTIN")
""",
    })

    assert requirement["dimensions"] == {"length_mm": 1201}


def test_static_numeric_refuses_unknown_helpers_and_methods():
    source = analyze_design_sources({
        "design.py": """
def unknown(value):
    return external(value)

class Wrapper:
    def value(self):
        return 1200

bad_helper_length = unknown(1200)
bad_method_length = Wrapper().value()

def make_member(length, part_number):
    return None

first = make_member(bad_helper_length, part_number="BAD-HELPER")
second = make_member(bad_method_length, part_number="BAD-METHOD")
""",
    })
    analysis = build_procurement_analysis(source, {"assemblies": [], "components": [], "diagnostics": []}, explicit_manifest={})
    requirements = {item["part_number"]: item for item in analysis["requirements"]}

    assert requirements["BAD-HELPER"]["dimensions"] == {}
    assert requirements["BAD-METHOD"]["dimensions"] == {}
    assert any(diagnostic["code"] == "unresolved_formula_dependency" for diagnostic in analysis["diagnostics"])


def test_direct_labelled_geometry_without_bom_inputs_emits_repair_diagnostic():
    source = analyze_design_sources({
        "design.py": """
import build123d as bd

def make_foundation():
    pads = []
    with bd.BuildPart() as pad:
        bd.Box(400, 600, 300)
    pads.append(pad.part)

    rebar = []
    with bd.BuildPart() as bar:
        bd.Cylinder(radius=8, height=3200)
    rebar.append(bar.part)

    return bd.Compound(children=[
        bd.Compound(children=pads, label="Concrete Pads"),
        bd.Compound(children=rebar, label="Rebar"),
    ], label="Foundation Assembly")

foundation = make_foundation()
""",
    })
    analysis = build_procurement_analysis(source, {"assemblies": [], "components": [], "diagnostics": []}, explicit_manifest={})

    diagnostic = next(item for item in analysis["diagnostics"] if item["code"] == "unrepresented_geometry_source")
    assert diagnostic["function"] == "make_foundation"
    assert diagnostic["source_file"] == "design.py"
    assert diagnostic["labels"] == ["Concrete Pads", "Foundation Assembly", "Rebar"]
    assert diagnostic["primitive_counts"] == {"Box": 1, "Cylinder": 1}
    assert analysis["requirements"] == []


def test_direct_geometry_with_bom_arguments_does_not_emit_unrepresented_warning():
    source = analyze_design_sources({
        "design.py": """
import build123d as bd

def make_rebar(length, part_number):
    with bd.BuildPart() as bar:
        bd.Cylinder(radius=8, height=length)
    return bar.part

bar = make_rebar(3200, part_number="REBAR-D16")
""",
    })
    analysis = build_procurement_analysis(source, {"assemblies": [], "components": [], "diagnostics": []}, explicit_manifest={})

    assert [requirement["part_number"] for requirement in analysis["requirements"]] == ["REBAR-D16"]
    assert not any(diagnostic["code"] == "unrepresented_geometry_source" for diagnostic in analysis["diagnostics"])


def test_ocp_compatibility_helper_is_not_misclassified_as_cp_bracket():
    source = analyze_design_sources({
        "design.py": """
from ocp_compat import ensure_ocp_hashcode

ensure_ocp_hashcode()
""",
        "ocp_compat.py": """
def ensure_ocp_hashcode():
    return None
""",
    })
    analysis = build_procurement_analysis(source, {"assemblies": [], "components": [], "diagnostics": []}, explicit_manifest={})

    assert analysis["components"] == []
    assert analysis["requirements"] == []


def test_visual_placeholder_rows_keep_label_identity_and_do_not_borrow_generic_source_dimensions():
    source = analyze_design_sources({
        "design.py": """
def fascia_bracket_assembly(mark="FBA01", length_mm=5100):
    return None

cp_brackets = fascia_bracket_assembly(mark="FBA01", length_mm=5100)
""",
    })
    tree = analyze_gltf_tree({
        "name": "Shed Building SH01-51-31-24-20",
        "children": [
            {
                "name": "Foundation Assembly",
                "children": [
                    {
                        "name": "Versaloc Blocks",
                        "children": [
                            {
                                "name": "Versaloc Block Envelope",
                                "children": [{"name": "mesh_1", "type": "Mesh", "isMesh": True}],
                            },
                        ],
                    },
                ],
            },
        ],
    })
    analysis = build_procurement_analysis(source, tree)

    requirement = analysis["requirements"][0]
    assert requirement["part_number"] is None
    assert requirement["source_trace"]["function"] is None
    assert requirement["dimensions"] == {"component_label": "Versaloc Block Envelope"}
