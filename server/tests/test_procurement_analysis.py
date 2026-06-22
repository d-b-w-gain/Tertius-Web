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

    assert requirement["part_number"].startswith("BRACKET-APEX-BRACKET-20DEG-")
    assert repeat["part_number"] == requirement["part_number"]
    assert changed["part_number"] != requirement["part_number"]
    assert requirement["resolution_trace"]["part_number"]["resolution"] == "generated_deterministic"


def test_mesh_only_noise_never_becomes_component_or_requirement():
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
    assert analysis["requirements"] == []


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
    assert analysis["requirements"] == [
        {
            "id": "portal-left-column.requirement",
            "component_id": "portal-left-column",
            "assembly_id": "portal",
            "part_number": "TEST-GOLDEN",
            "stock_number": "TEST-GOLDEN-21",
            "quantity": 1,
            "rolled_up_quantity": 1,
            "quantity_source": "source_calls",
            "quantity_confidence": "probable",
            "visual_instance_count": None,
            "assembly_instance_multiplier": 1,
            "source_call_count": 1,
            "count_trace": {
                "explicit_quantity": None,
                "visual_instance_count": None,
                "assembly_instance_multiplier": 1,
                "source_call_count": 1,
            },
            "unit": "each",
            "dimensions": {"length_mm": 2100},
            "material": None,
            "finish": None,
            "source_trace": {
                "function": "make_member",
                "source_file": "design.py",
                "source_scope": "<module>",
                "source_line": 8,
                "match_reason": "single candidate source call",
            },
            "resolution_trace": {
                "part_number": {
                    "raw": {"kind": "reference", "name": "PURLIN_PART_NUMBER"},
                    "resolved": "TEST-GOLDEN",
                    "resolution": "literal_assignment",
                    "source_file": "design.py",
                    "source_line": 2,
                },
            },
        }
    ]


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


def test_visual_instance_count_provides_verified_quantity_without_extra_source_calls():
    source = analyze_design_sources({
        "design.py": """
def make_member(part_number):
    return None

prototype = make_member("VISUAL-COUNT")
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

    assert requirement["part_number"] == "VISUAL-COUNT"
    assert requirement["quantity"] == 3
    assert requirement["rolled_up_quantity"] == 3
    assert requirement["quantity_source"] == "visual_instances"
    assert requirement["quantity_confidence"] == "verified"
    assert requirement["visual_instance_count"] == 3
    assert requirement["source_call_count"] == 1


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
    assert {item["quantity_source"] for item in requirements} == {"source_calls"}
    assert {item["quantity_confidence"] for item in requirements} == {"probable"}
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

    assert requirement["quantity"] == 2
    assert requirement["rolled_up_quantity"] == 2
    assert requirement["quantity_source"] == "explicit"
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
    assert requirement["quantity_source"] == "explicit"
    assert requirement["quantity_confidence"] == "verified"
    assert requirement["source_call_count"] == 1
    assert requirement["count_trace"]["explicit_quantity"] == 4


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
    assert {item["rolled_up_quantity"] for item in requirements} == {3}
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
    assert {item["quantity"] for item in fasteners} == {28}
    assert {item["rolled_up_quantity"] for item in fasteners} == {84}
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

    assert requirement["quantity"] == 14
    assert requirement["rolled_up_quantity"] == 14
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
    assert requirements["CUSTOM-ORB"]["quantity"] == 14
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
