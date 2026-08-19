from __future__ import annotations

from dataclasses import dataclass
from math import pi, sqrt
from typing import Literal, cast

from .contracts import SectionProperties, StructuralMaterial


class CapacityPackError(ValueError):
    """Raised when a section lacks the traceable data required by a capacity pack."""


AS_NZS_4600_2005_A1_REFERENCE = "AS/NZS 4600:2005 incorporating Amendment No. 1"
AS_NZS_4600_2005_A1_SHA256 = (
    "9af589ce2bfee5a156c7f6600a738fcc158683bd27bea9d22adb559f172fc2d1"
)
AS_NZS_4600_DEVELOPMENTS_SUPPLEMENT_SHA256 = (
    "fb0aed54e371ae798a6bd436bca6008f0a32b88ac8989c90ac2b880c51041145"
)
ACCEPTED_STANDARD_STATUS = (
    "accepted_project_basis_2005_a1_with_developments_supplement"
)


@dataclass(frozen=True)
class CrossSectionCapacity:
    pack_id: str
    section_record_sha256: str
    design_compression_capacity_kN: float
    design_major_bending_capacity_kNm: float
    design_minor_bending_capacity_kNm: float
    design_web_shear_capacity_kN: float
    design_off_axis_shear_capacity_kN: float
    design_st_venant_torsion_capacity_kNm: float
    effective_minor_modulus_mm3: float
    phi_c: float
    phi_b: float
    phi_v: float
    web_slenderness: float
    web_yield_boundary: float
    web_elastic_boundary: float
    shear_regime: Literal["stocky", "inelastic_buckling", "elastic_buckling"]
    standard_reference: str
    standard_status: str
    standard_source_sha256: str
    developments_supplement_sha256: str
    basis: str


@dataclass(frozen=True)
class TensionMemberCapacity:
    pack_id: str
    design_tension_capacity_kN: float
    gross_yield_capacity_kN: float
    net_fracture_capacity_kN: float
    gross_area_mm2: float
    net_area_mm2: float
    phi_t: float
    force_distribution_factor: float
    standard_reference: str
    standard_status: str
    standard_source_sha256: str
    developments_supplement_sha256: str
    basis: str


@dataclass(frozen=True)
class ScrewShearQualification:
    pack_id: str
    tested_single_shear_strength_kN: float
    nominal_bearing_capacity_kN: float
    required_single_shear_strength_kN: float
    status: Literal["pass", "fail"]
    standard_reference: str
    standard_status: str
    standard_source_sha256: str
    developments_supplement_sha256: str
    basis: str


@dataclass(frozen=True)
class ManufacturerWorkingLoadAnchorGroupResistance:
    pack_id: str
    pack_version: str
    anchor_count: int
    effective_anchor_count: float
    design_tension_capacity_kN: float
    design_shear_capacity_kN: float
    interaction_utilisation: float
    embedment_status: Literal["pass", "fail"]
    edge_distance_status: Literal["pass", "fail"]
    spacing_status: Literal["pass", "fail", "not_required"]
    status: Literal["pass", "fail"]
    basis: str


@dataclass(frozen=True)
class BoltedSheetInterfaceResistance:
    pack_id: str
    pack_version: str
    bolt_count: int
    design_bolt_shear_capacity_kN: float
    design_sheet_bearing_capacity_kN: float
    design_sheet_tearout_capacity_kN: float
    governing_capacity_kN: float
    governing_utilisation: float
    required_spacing_mm: float
    required_edge_distance_mm: float
    bolt_shear_status: Literal["pass", "fail"]
    sheet_bearing_status: Literal["pass", "fail"]
    sheet_tearout_status: Literal["pass", "fail"]
    hole_status: Literal["pass", "fail"]
    spacing_status: Literal["pass", "fail"]
    edge_distance_status: Literal["pass", "fail"]
    status: Literal["pass", "fail"]
    standard_reference: str
    standard_status: str
    standard_source_sha256: str
    developments_supplement_sha256: str
    basis: str


@dataclass(frozen=True)
class MemberCompressionCapacity:
    pack_id: str
    section_record_sha256: str
    elastic_flexural_buckling_stress_MPa: float
    elastic_torsional_buckling_stress_MPa: float
    elastic_flexural_torsional_buckling_stress_MPa: float
    elastic_distortional_compression_stress_MPa: float
    elastic_distortional_bending_stress_MPa: float
    elastic_lateral_torsional_buckling_moment_kNm: float
    elastic_minor_lateral_torsional_buckling_moment_kNm: float
    elastic_major_axis_flexural_buckling_load_kN: float
    elastic_minor_axis_flexural_buckling_load_kN: float
    nominal_global_buckling_stress_MPa: float
    nominal_global_compression_capacity_kN: float
    nominal_distortional_compression_capacity_kN: float
    nominal_lateral_torsional_bending_capacity_kNm: float
    nominal_distortional_bending_capacity_kNm: float
    nominal_minor_lateral_torsional_bending_capacity_kNm: float
    design_member_compression_capacity_kN: float
    design_major_bending_capacity_kNm: float
    design_minor_bending_capacity_kNm: float
    design_global_compression_capacity_kN: float
    design_distortional_compression_capacity_kN: float
    design_lateral_torsional_bending_capacity_kNm: float
    design_distortional_bending_capacity_kNm: float
    design_section_minor_bending_capacity_kNm: float
    design_minor_lateral_torsional_bending_capacity_kNm: float
    design_web_shear_capacity_kN: float
    design_off_axis_shear_capacity_kN: float
    design_st_venant_torsion_capacity_kNm: float
    governing_compression_mode: Literal["section", "global", "distortional"]
    governing_bending_mode: Literal[
        "section",
        "lateral_torsional",
        "distortional",
    ]
    governing_minor_bending_mode: Literal["section", "lateral_torsional"]
    slenderness: float
    phi_c: float
    phi_b: float
    standard_reference: str
    standard_status: str
    standard_source_sha256: str
    developments_supplement_sha256: str
    basis: str


@dataclass(frozen=True)
class _DistortionalBucklingStress:
    stress_MPa: float
    wavelength_mm: float
    rotational_stiffness_Nmm_per_rad: float


def _positive(
    properties: dict[str, object],
    key: str,
    *aliases: str,
) -> float:
    matched_key = next(
        (candidate for candidate in (key, *aliases) if candidate in properties),
        key,
    )
    value = properties.get(matched_key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CapacityPackError(f"catalogue property {key!r} must be numeric")
    normalized = float(value)
    if normalized <= 0:
        raise CapacityPackError(f"catalogue property {key!r} must be positive")
    return normalized


def as_nzs_4600_2005_a1_tension_capacity(
    section: SectionProperties,
    material: StructuralMaterial,
) -> TensionMemberCapacity:
    """Return the Clause 3.2 design tension resistance for a flat strap.

    Product geometry supplies the critical fastener-hole deduction and force
    distribution factor. Tertius owns the standard equations and capacity
    reduction factor; a design import does not provide a pre-calculated result.
    """

    width_mm = section.tension_width_mm
    thickness_mm = section.tension_thickness_mm
    hole_diameter_mm = section.tension_hole_diameter_mm
    hole_count = section.tension_holes_in_critical_section
    force_distribution_factor = section.tension_force_distribution_factor
    fy_mpa = material.yield_strength_MPa
    fu_mpa = material.tensile_strength_MPa
    required = {
        "tension_width_mm": width_mm,
        "tension_thickness_mm": thickness_mm,
        "tension_hole_diameter_mm": hole_diameter_mm,
        "tension_holes_in_critical_section": hole_count,
        "tension_force_distribution_factor": force_distribution_factor,
        "yield_strength_MPa": fy_mpa,
        "tensile_strength_MPa": fu_mpa,
    }
    missing = [key for key, value in required.items() if value is None]
    if missing:
        raise CapacityPackError(
            "tension member is missing product facts: " + ", ".join(missing)
        )
    assert width_mm is not None
    assert thickness_mm is not None
    assert hole_diameter_mm is not None
    assert hole_count is not None
    assert force_distribution_factor is not None
    assert fy_mpa is not None
    assert fu_mpa is not None
    gross_area_mm2 = width_mm * thickness_mm
    net_width_mm = width_mm - hole_count * hole_diameter_mm
    if net_width_mm <= 0:
        raise CapacityPackError(
            "tension fastener holes remove the complete critical section"
        )
    if abs(gross_area_mm2 - section.area_m2 * 1_000_000.0) > max(
        1e-6,
        gross_area_mm2 * 0.01,
    ):
        raise CapacityPackError(
            "tension width and thickness do not reproduce the analytical gross area"
        )
    net_area_mm2 = net_width_mm * thickness_mm
    phi_t = 0.90
    gross_yield_capacity_kN = phi_t * gross_area_mm2 * fy_mpa / 1000.0
    net_fracture_capacity_kN = (
        phi_t
        * 0.85
        * force_distribution_factor
        * net_area_mm2
        * fu_mpa
        / 1000.0
    )
    return TensionMemberCapacity(
        pack_id="as_nzs_4600_2005_a1_tension",
        design_tension_capacity_kN=min(
            gross_yield_capacity_kN,
            net_fracture_capacity_kN,
        ),
        gross_yield_capacity_kN=gross_yield_capacity_kN,
        net_fracture_capacity_kN=net_fracture_capacity_kN,
        gross_area_mm2=gross_area_mm2,
        net_area_mm2=net_area_mm2,
        phi_t=phi_t,
        force_distribution_factor=force_distribution_factor,
        standard_reference=AS_NZS_4600_2005_A1_REFERENCE,
        standard_status=ACCEPTED_STANDARD_STATUS,
        standard_source_sha256=AS_NZS_4600_2005_A1_SHA256,
        developments_supplement_sha256=(
            AS_NZS_4600_DEVELOPMENTS_SUPPLEMENT_SHA256
        ),
        basis=(
            "AS/NZS 4600:2005 incorporating Amendment No. 1 Clauses 3.2.1 "
            "and 3.2.2: phi_t=0.90 and Nt=min(Ag fy, 0.85 kt An fu). "
            "Gross and net areas come from the compiled product width, "
            "thickness, critical fastener-hole layout, and material strengths."
        ),
    )


def tension_member_capacity(
    pack_id: str,
    section: SectionProperties,
    material: StructuralMaterial,
) -> TensionMemberCapacity:
    if pack_id == "as_nzs_4600_2005_a1_tension":
        return as_nzs_4600_2005_a1_tension_capacity(section, material)
    raise CapacityPackError(f"unsupported tension capacity pack {pack_id!r}")


def as_nzs_4600_2005_a1_screw_shear_qualification(
    *,
    tested_single_shear_strength_kN: float,
    nominal_bearing_capacity_kN: float,
) -> ScrewShearQualification:
    """Qualify a screw for the Clause 5.4.2.3 bearing resistance.

    Clause 5.4.2.5 does not define a separate factored screw-shear design
    resistance. It requires the nominal screw strength established by Section 8
    testing to be at least 1.25 times the Clause 5.4.2.3 nominal bearing value.
    """

    if tested_single_shear_strength_kN <= 0:
        raise CapacityPackError("tested single-shear strength must be positive")
    if nominal_bearing_capacity_kN <= 0:
        raise CapacityPackError("nominal screw bearing capacity must be positive")
    required_strength_kN = 1.25 * nominal_bearing_capacity_kN
    return ScrewShearQualification(
        pack_id="as_nzs_4600_2005_a1_screw_shear_qualification",
        tested_single_shear_strength_kN=tested_single_shear_strength_kN,
        nominal_bearing_capacity_kN=nominal_bearing_capacity_kN,
        required_single_shear_strength_kN=required_strength_kN,
        status=(
            "pass"
            if tested_single_shear_strength_kN >= required_strength_kN
            else "fail"
        ),
        standard_reference=AS_NZS_4600_2005_A1_REFERENCE,
        standard_status=ACCEPTED_STANDARD_STATUS,
        standard_source_sha256=AS_NZS_4600_2005_A1_SHA256,
        developments_supplement_sha256=(
            AS_NZS_4600_DEVELOPMENTS_SUPPLEMENT_SHA256
        ),
        basis=(
            "AS/NZS 4600:2005 incorporating Amendment No. 1 Clause 5.4.2.5: "
            "the Section 8 tested nominal screw shear strength must be not less "
            "than 1.25 Vb, where Vb is the Clause 5.4.2.3 nominal single-screw "
            "bearing resistance. Screw shear is a qualification gate, not an "
            "additional factored connection-capacity limit."
        ),
    )


def as_nzs_4600_2005_a1_bolted_sheet_interface(
    *,
    bolt_count: int,
    resultant_shear_demand_kN: float,
    nominal_bolt_diameter_mm: float,
    bolt_tensile_strength_MPa: float,
    bolt_minor_area_mm2: float,
    connected_sheet_thickness_mm: float,
    connected_sheet_yield_strength_MPa: float,
    connected_sheet_tensile_strength_MPa: float,
    hole_diameter_mm: float,
    hole_type: str,
    minimum_spacing_mm: float,
    minimum_edge_distance_mm: float,
    washers_under_head_and_nut: bool,
) -> BoltedSheetInterfaceResistance:
    """Check a single-shear bolt group through a sub-3 mm connected sheet.

    Product imports declare only physical product and installed-layout facts.
    Tertius owns the AS/NZS 4600:2005 Clause 5.3 bolt shear, sheet bearing,
    sheet tearout, hole-size, spacing, and edge-distance calculations.
    """

    positive_values = {
        "nominal bolt diameter": nominal_bolt_diameter_mm,
        "bolt tensile strength": bolt_tensile_strength_MPa,
        "bolt minor area": bolt_minor_area_mm2,
        "connected sheet thickness": connected_sheet_thickness_mm,
        "connected sheet yield strength": connected_sheet_yield_strength_MPa,
        "connected sheet tensile strength": connected_sheet_tensile_strength_MPa,
        "hole diameter": hole_diameter_mm,
        "minimum spacing": minimum_spacing_mm,
        "minimum edge distance": minimum_edge_distance_mm,
    }
    if bolt_count <= 0:
        raise CapacityPackError("bolt count must be positive")
    if resultant_shear_demand_kN < 0:
        raise CapacityPackError("bolted-sheet demand cannot be negative")
    for label, value in positive_values.items():
        if value <= 0:
            raise CapacityPackError(f"{label} must be positive")
    if connected_sheet_thickness_mm >= 3.0:
        raise CapacityPackError(
            "AS/NZS 4600 Clause 5.3 requires at least one connected part below 3 mm"
        )
    normalized_hole_type = hole_type.strip().lower()
    if normalized_hole_type != "standard_round":
        raise CapacityPackError(
            "this verified pack currently requires standard round bolt holes"
        )

    required_spacing_mm = 3.0 * nominal_bolt_diameter_mm
    required_edge_distance_mm = 1.5 * nominal_bolt_diameter_mm
    maximum_standard_hole_mm = nominal_bolt_diameter_mm + (
        1.0 if nominal_bolt_diameter_mm < 12.0 else 2.0
    )
    hole_status: Literal["pass", "fail"] = (
        "pass" if hole_diameter_mm <= maximum_standard_hole_mm else "fail"
    )
    spacing_status: Literal["pass", "fail"] = (
        "pass" if minimum_spacing_mm >= required_spacing_mm else "fail"
    )
    edge_distance_status: Literal["pass", "fail"] = (
        "pass" if minimum_edge_distance_mm >= required_edge_distance_mm else "fail"
    )

    phi_bolt_shear = 0.80
    nominal_bolt_shear_per_bolt_N = (
        0.62 * bolt_tensile_strength_MPa * bolt_minor_area_mm2
    )
    design_bolt_shear_capacity_kN = (
        bolt_count * phi_bolt_shear * nominal_bolt_shear_per_bolt_N / 1000.0
    )

    diameter_thickness_ratio = (
        nominal_bolt_diameter_mm / connected_sheet_thickness_mm
    )
    if diameter_thickness_ratio < 10.0:
        bearing_factor = 3.0
    elif diameter_thickness_ratio <= 22.0:
        bearing_factor = 4.0 - 0.1 * diameter_thickness_ratio
    else:
        bearing_factor = 1.8
    bearing_modification = 1.0 if washers_under_head_and_nut else 0.75
    phi_bearing = 0.60
    nominal_bearing_per_bolt_N = (
        bearing_modification
        * bearing_factor
        * nominal_bolt_diameter_mm
        * connected_sheet_thickness_mm
        * connected_sheet_tensile_strength_MPa
    )
    design_sheet_bearing_capacity_kN = (
        bolt_count * phi_bearing * nominal_bearing_per_bolt_N / 1000.0
    )

    tearout_phi = (
        0.70
        if connected_sheet_tensile_strength_MPa
        / connected_sheet_yield_strength_MPa
        >= 1.08
        else 0.60
    )
    nominal_tearout_per_bolt_N = (
        connected_sheet_thickness_mm
        * minimum_edge_distance_mm
        * connected_sheet_tensile_strength_MPa
    )
    design_sheet_tearout_capacity_kN = (
        bolt_count * tearout_phi * nominal_tearout_per_bolt_N / 1000.0
    )
    governing_capacity_kN = min(
        design_bolt_shear_capacity_kN,
        design_sheet_bearing_capacity_kN,
        design_sheet_tearout_capacity_kN,
    )
    governing_utilisation = resultant_shear_demand_kN / governing_capacity_kN
    bolt_shear_status: Literal["pass", "fail"] = (
        "pass"
        if resultant_shear_demand_kN <= design_bolt_shear_capacity_kN
        else "fail"
    )
    sheet_bearing_status: Literal["pass", "fail"] = (
        "pass"
        if resultant_shear_demand_kN <= design_sheet_bearing_capacity_kN
        else "fail"
    )
    sheet_tearout_status: Literal["pass", "fail"] = (
        "pass"
        if resultant_shear_demand_kN <= design_sheet_tearout_capacity_kN
        else "fail"
    )
    checks = (
        bolt_shear_status,
        sheet_bearing_status,
        sheet_tearout_status,
        hole_status,
        spacing_status,
        edge_distance_status,
    )
    status: Literal["pass", "fail"] = (
        "pass" if all(value == "pass" for value in checks) else "fail"
    )
    return BoltedSheetInterfaceResistance(
        pack_id="as_nzs_4600_2005_a1_bolted_sheet_interface",
        pack_version="1",
        bolt_count=bolt_count,
        design_bolt_shear_capacity_kN=design_bolt_shear_capacity_kN,
        design_sheet_bearing_capacity_kN=design_sheet_bearing_capacity_kN,
        design_sheet_tearout_capacity_kN=design_sheet_tearout_capacity_kN,
        governing_capacity_kN=governing_capacity_kN,
        governing_utilisation=governing_utilisation,
        required_spacing_mm=required_spacing_mm,
        required_edge_distance_mm=required_edge_distance_mm,
        bolt_shear_status=bolt_shear_status,
        sheet_bearing_status=sheet_bearing_status,
        sheet_tearout_status=sheet_tearout_status,
        hole_status=hole_status,
        spacing_status=spacing_status,
        edge_distance_status=edge_distance_status,
        status=status,
        standard_reference=AS_NZS_4600_2005_A1_REFERENCE,
        standard_status=ACCEPTED_STANDARD_STATUS,
        standard_source_sha256=AS_NZS_4600_2005_A1_SHA256,
        developments_supplement_sha256=(
            AS_NZS_4600_DEVELOPMENTS_SUPPLEMENT_SHA256
        ),
        basis=(
            "AS/NZS 4600:2005 incorporating Amendment No. 1 Clauses 5.3.1, "
            "5.3.2, 5.3.4.2 and 5.3.5.1: standard-hole geometry, 3df spacing, "
            "1.5df edge distance, connected-sheet tearout and bearing, and "
            "Grade-bolt single-shear resistance. Demand is shared equally by "
            "the declared identical bolt group; fixture-plate and connected-part "
            "net-section resistance remain separate limit states."
        ),
    )


def manufacturer_working_load_anchor_group_resistance(
    *,
    anchor_count: int,
    single_anchor_tension_capacity_kN: float,
    single_anchor_shear_capacity_kN: float,
    tension_demand_kN: float,
    shear_demand_kN: float,
    installed_effective_embedment_mm: float,
    reference_embedment_mm: float,
    minimum_edge_distance_mm: float,
    required_edge_distance_mm: float,
    minimum_spacing_mm: float | None,
    required_spacing_mm: float,
) -> ManufacturerWorkingLoadAnchorGroupResistance:
    """Evaluate a manufacturer WLL anchor pack without inventing group strength.

    Product imports provide immutable catalogue and installation facts. Tertius
    owns the geometry gates and the conservative linear tension/shear
    interaction. Published single-anchor working loads are deliberately not
    multiplied for a close group unless a separate verified group pack exists.
    """

    positive_values = {
        "single-anchor tension capacity": single_anchor_tension_capacity_kN,
        "single-anchor shear capacity": single_anchor_shear_capacity_kN,
        "installed effective embedment": installed_effective_embedment_mm,
        "reference embedment": reference_embedment_mm,
        "minimum edge distance": minimum_edge_distance_mm,
        "required edge distance": required_edge_distance_mm,
        "required spacing": required_spacing_mm,
    }
    if anchor_count <= 0:
        raise CapacityPackError("anchor count must be positive")
    if tension_demand_kN < 0 or shear_demand_kN < 0:
        raise CapacityPackError("anchor demands cannot be negative")
    for label, value in positive_values.items():
        if value <= 0:
            raise CapacityPackError(f"{label} must be positive")
    if minimum_spacing_mm is not None and minimum_spacing_mm <= 0:
        raise CapacityPackError("minimum anchor spacing must be positive")

    # A single-anchor lower bound is valid for a group because no beneficial
    # load sharing is claimed. A later evidence pack can replace this with a
    # manufacturer/standard group reduction calculation.
    effective_anchor_count = 1.0
    tension_capacity_kN = single_anchor_tension_capacity_kN
    shear_capacity_kN = single_anchor_shear_capacity_kN
    interaction_utilisation = (
        tension_demand_kN / tension_capacity_kN
        + shear_demand_kN / shear_capacity_kN
    )
    embedment_status: Literal["pass", "fail"] = (
        "pass"
        if installed_effective_embedment_mm >= reference_embedment_mm
        else "fail"
    )
    edge_distance_status: Literal["pass", "fail"] = (
        "pass"
        if minimum_edge_distance_mm >= required_edge_distance_mm
        else "fail"
    )
    spacing_status: Literal["pass", "fail", "not_required"] = (
        "not_required"
        if anchor_count == 1
        else "pass"
        if minimum_spacing_mm is not None
        and minimum_spacing_mm >= required_spacing_mm
        else "fail"
    )
    status: Literal["pass", "fail"] = (
        "pass"
        if embedment_status == "pass"
        and edge_distance_status == "pass"
        and spacing_status in {"pass", "not_required"}
        and interaction_utilisation <= 1.0
        else "fail"
    )
    return ManufacturerWorkingLoadAnchorGroupResistance(
        pack_id="manufacturer_working_load_anchor_group",
        pack_version="1",
        anchor_count=anchor_count,
        effective_anchor_count=effective_anchor_count,
        design_tension_capacity_kN=tension_capacity_kN,
        design_shear_capacity_kN=shear_capacity_kN,
        interaction_utilisation=interaction_utilisation,
        embedment_status=embedment_status,
        edge_distance_status=edge_distance_status,
        spacing_status=spacing_status,
        status=status,
        basis=(
            "Tertius checks installed embedment, edge distance, anchor spacing, "
            "and N*/Ncap + V*/Vcap <= 1.0. Published single-anchor working "
            "loads are retained as a conservative one-anchor lower bound for "
            "the complete group; no unverified group multiplication is used."
        ),
    )


def _simple_lipped_channel_distortional_stress(
    *,
    elastic_modulus_mpa: float,
    thickness_mm: float,
    flange_width_mm: float,
    lip_depth_mm: float,
    clear_web_depth_mm: float,
    bending: bool,
) -> _DistortionalBucklingStress:
    """Appendix D2/D3 elastic distortional stress for a simple lipped C.

    Catalogue straight-element dimensions are used directly. The Appendix D3
    instruction to recalculate a negative bending rotational stiffness with
    f'od = 0 is applied explicitly.
    """

    flange_lip_area_mm2 = (flange_width_mm + lip_depth_mm) * thickness_mm
    centroid_x_mm = (
        flange_width_mm**2 + 2.0 * flange_width_mm * lip_depth_mm
    ) / (2.0 * (flange_width_mm + lip_depth_mm))
    centroid_y_mm = lip_depth_mm**2 / (
        2.0 * (flange_width_mm + lip_depth_mm)
    )
    flange_lip_ix_mm4 = (
        flange_width_mm * thickness_mm**3 / 12.0
        + thickness_mm * lip_depth_mm**3 / 12.0
        + flange_width_mm * thickness_mm * centroid_y_mm**2
        + lip_depth_mm
        * thickness_mm
        * (lip_depth_mm / 2.0 - centroid_y_mm) ** 2
    )
    flange_lip_iy_mm4 = (
        thickness_mm * flange_width_mm**3 / 12.0
        + lip_depth_mm * thickness_mm**3 / 12.0
        + lip_depth_mm
        * thickness_mm
        * (flange_width_mm - centroid_x_mm) ** 2
        + flange_width_mm
        * thickness_mm
        * (centroid_x_mm - flange_width_mm / 2.0) ** 2
    )
    flange_lip_ixy_mm4 = (
        flange_width_mm
        * thickness_mm
        * (flange_width_mm / 2.0 - centroid_x_mm)
        * (-centroid_y_mm)
        + lip_depth_mm
        * thickness_mm
        * (lip_depth_mm / 2.0 - centroid_y_mm)
        * (flange_width_mm - centroid_x_mm)
    )
    flange_lip_j_mm4 = (
        thickness_mm**3 * (flange_width_mm + lip_depth_mm) / 3.0
    )
    beta_1_mm2 = centroid_x_mm**2 + (
        flange_lip_ix_mm4 + flange_lip_iy_mm4
    ) / flange_lip_area_mm2
    wavelength_denominator = 2.0 if bending else 1.0
    wavelength_mm = 4.80 * (
        flange_lip_ix_mm4
        * flange_width_mm**2
        * clear_web_depth_mm
        / (wavelength_denominator * thickness_mm**3)
    ) ** 0.25
    eta_per_mm2 = (pi / wavelength_mm) ** 2

    def elastic_stress(rotational_stiffness: float) -> float:
        a_1 = (
            eta_per_mm2
            / beta_1_mm2
            * (
                flange_lip_ix_mm4 * flange_width_mm**2
                + 0.039 * flange_lip_j_mm4 * wavelength_mm**2
            )
            + rotational_stiffness
            / (
                beta_1_mm2
                * eta_per_mm2
                * elastic_modulus_mpa
            )
        )
        a_2 = eta_per_mm2 * (
            flange_lip_iy_mm4
            + 2.0
            / beta_1_mm2
            * centroid_y_mm
            * flange_width_mm
            * flange_lip_ixy_mm4
        )
        a_3 = eta_per_mm2 * (
            a_1 * flange_lip_iy_mm4
            - eta_per_mm2
            / beta_1_mm2
            * flange_lip_ixy_mm4**2
            * flange_width_mm**2
        )
        radicand = max(0.0, (a_1 + a_2) ** 2 - 4.0 * a_3)
        return elastic_modulus_mpa / (2.0 * flange_lip_area_mm2) * (
            (a_1 + a_2) - sqrt(radicand)
        )

    prime_stress_mpa = elastic_stress(0.0)
    if bending:
        restraint_ratio = (
            clear_web_depth_mm**4 * wavelength_mm**2
            / (
                12.56 * wavelength_mm**4
                + 2.192 * clear_web_depth_mm**4
                + 13.39 * wavelength_mm**2 * clear_web_depth_mm**2
            )
        )
        base_rotational_stiffness = (
            2.0
            * elastic_modulus_mpa
            * thickness_mm**3
            / (5.46 * (clear_web_depth_mm + 0.06 * wavelength_mm))
        )
    else:
        restraint_ratio = (
            clear_web_depth_mm**2
            * wavelength_mm
            / (clear_web_depth_mm**2 + wavelength_mm**2)
        ) ** 2
        base_rotational_stiffness = (
            elastic_modulus_mpa
            * thickness_mm**3
            / (5.46 * (clear_web_depth_mm + 0.06 * wavelength_mm))
        )
    rotational_stiffness = base_rotational_stiffness * (
        1.0
        - 1.11
        * prime_stress_mpa
        / (elastic_modulus_mpa * thickness_mm**2)
        * restraint_ratio
    )
    if bending and rotational_stiffness < 0:
        rotational_stiffness = base_rotational_stiffness
    stress_mpa = elastic_stress(rotational_stiffness)
    if stress_mpa <= 0:
        raise CapacityPackError(
            "Appendix D distortional calculation did not produce positive stress"
        )
    return _DistortionalBucklingStress(
        stress_MPa=stress_mpa,
        wavelength_mm=wavelength_mm,
        rotational_stiffness_Nmm_per_rad=rotational_stiffness,
    )


def _require_prequalified_simple_lipped_channel(
    *,
    elastic_modulus_mpa: float,
    yield_stress_mpa: float,
    thickness_mm: float,
    flange_width_mm: float,
    lip_depth_mm: float,
    clear_web_depth_mm: float,
) -> None:
    """Enforce the bounded Direct Strength prequalification in Table 7.1.1."""

    checks = {
        "d/t <= 472": clear_web_depth_mm / thickness_mm <= 472.0,
        "b1/t <= 159": flange_width_mm / thickness_mm <= 159.0,
        "4 <= d1/t <= 33": 4.0
        <= lip_depth_mm / thickness_mm
        <= 33.0,
        "0.7 <= d/b1 <= 5": 0.7
        <= clear_web_depth_mm / flange_width_mm
        <= 5.0,
        "0.05 <= d1/b1 <= 0.41": 0.05
        <= lip_depth_mm / flange_width_mm
        <= 0.41,
        "E/fy > 340": elastic_modulus_mpa / yield_stress_mpa > 340.0,
    }
    failed = [label for label, passed in checks.items() if not passed]
    if failed:
        raise CapacityPackError(
            "simple lipped channel is outside AS/NZS 4600:2005 Table 7.1.1 "
            f"prequalification bounds: {', '.join(failed)}"
        )


def as_nzs_4600_2005_a1_ewm_capacity(
    section: SectionProperties,
) -> CrossSectionCapacity:
    """Return section-only C/Z capacities from the accepted project basis.

    This pack deliberately stops before member buckling, restraint, connection,
    and system checks. Inputs use the catalogue's effective area/modulus and the
    AS/NZS 4600:2005+A1 section and web-shear equations.
    """

    catalog = section.catalog
    if catalog is None:
        raise CapacityPackError("section has no immutable catalogue reference")
    properties = catalog.properties
    if properties.get("validated") is not True:
        raise CapacityPackError("catalogue property 'validated' must be true")
    section_type = str(properties.get("type", "")).strip().upper()
    lip_mm = _positive(properties, "lip", "lip_mm")
    if section_type not in {"C", "Z"} or lip_mm <= 0:
        raise CapacityPackError(
            "pack supports lipped C/Z sections with a stiffened compression flange"
        )

    fy_mpa = _positive(properties, "fy", "fy_MPa")
    elastic_modulus_mpa = _positive(properties, "E", "E_MPa")
    effective_area_mm2 = _positive(properties, "Ae", "Ae_mm2")
    effective_modulus_mm3 = _positive(properties, "Zxe", "Zxe_mm3")
    gross_area_mm2 = _positive(properties, "A", "A_mm2")
    gross_minor_modulus_mm3 = _positive(properties, "Zy", "Zy_mm3")
    torsion_constant_mm4 = _positive(properties, "J", "J_mm4")
    clear_web_depth_mm = _positive(properties, "d1", "d1_mm")
    thickness_mm = _positive(properties, "t", "t_mm")

    # AS/NZS 4600:2005+A1 Table 1.6 capacity factors for the section-only limit
    # states used here. The stored pack ID freezes this auditable implementation.
    phi_b = 0.95
    phi_c = 0.85
    phi_v = 0.90
    shear_buckling_coefficient = 5.34

    nominal_bending_kNm = effective_modulus_mm3 * fy_mpa / 1_000_000.0
    nominal_compression_kN = effective_area_mm2 * fy_mpa / 1000.0
    effective_minor_modulus_mm3 = gross_minor_modulus_mm3 * min(
        1.0,
        effective_area_mm2 / gross_area_mm2,
    )
    nominal_minor_bending_kNm = (
        effective_minor_modulus_mm3 * fy_mpa / 1_000_000.0
    )

    web_slenderness = clear_web_depth_mm / thickness_mm
    web_yield_boundary = sqrt(elastic_modulus_mpa * shear_buckling_coefficient / fy_mpa)
    web_elastic_boundary = 1.415 * web_yield_boundary
    shear_regime: Literal["stocky", "inelastic_buckling", "elastic_buckling"]
    if web_slenderness <= web_yield_boundary:
        shear_regime = "stocky"
        nominal_shear_n = 0.64 * fy_mpa * clear_web_depth_mm * thickness_mm
    elif web_slenderness <= web_elastic_boundary:
        shear_regime = "inelastic_buckling"
        nominal_shear_n = (
            0.64
            * thickness_mm**2
            * sqrt(elastic_modulus_mpa * shear_buckling_coefficient * fy_mpa)
        )
    else:
        shear_regime = "elastic_buckling"
        nominal_shear_n = (
            0.905
            * elastic_modulus_mpa
            * shear_buckling_coefficient
            * thickness_mm**3
            / clear_web_depth_mm
        )

    design_web_shear_capacity_kN = phi_v * nominal_shear_n / 1000.0
    design_off_axis_shear_capacity_kN = min(
        design_web_shear_capacity_kN,
        phi_v * 0.60 * fy_mpa * effective_area_mm2 / 1000.0,
    )
    design_st_venant_torsion_capacity_kNm = (
        phi_v
        * 0.60
        * fy_mpa
        * torsion_constant_mm4
        / thickness_mm
        / 1_000_000.0
    )

    return CrossSectionCapacity(
        pack_id="as_nzs_4600_2005_a1_ewm",
        section_record_sha256=catalog.record_sha256,
        design_compression_capacity_kN=(phi_c * nominal_compression_kN),
        design_major_bending_capacity_kNm=(phi_b * nominal_bending_kNm),
        design_minor_bending_capacity_kNm=(phi_b * nominal_minor_bending_kNm),
        design_web_shear_capacity_kN=design_web_shear_capacity_kN,
        design_off_axis_shear_capacity_kN=design_off_axis_shear_capacity_kN,
        design_st_venant_torsion_capacity_kNm=(
            design_st_venant_torsion_capacity_kNm
        ),
        effective_minor_modulus_mm3=effective_minor_modulus_mm3,
        phi_c=phi_c,
        phi_b=phi_b,
        phi_v=phi_v,
        web_slenderness=web_slenderness,
        web_yield_boundary=web_yield_boundary,
        web_elastic_boundary=web_elastic_boundary,
        shear_regime=shear_regime,
        standard_reference=AS_NZS_4600_2005_A1_REFERENCE,
        standard_status=ACCEPTED_STANDARD_STATUS,
        standard_source_sha256=AS_NZS_4600_2005_A1_SHA256,
        developments_supplement_sha256=AS_NZS_4600_DEVELOPMENTS_SUPPLEMENT_SHA256,
        basis=(
            "AS/NZS 4600:2005 incorporating Amendment No. 1 project-basis "
            "effective-width section resistance using catalogue Ae and Zxe; "
            "Clause 3.3.4 web shear uses kv=5.34; minor-axis section bending "
            "uses Clause 3.3.2 with Zey conservatively approximated by "
            "Zy(Ae/A). Off-axis shear does not exceed the lower of web shear "
            "capacity and 0.6fyAe. Open-section torque uses a rational elastic "
            "full-St-Venant yield screen, tau=Tt/J, with no warping or restraint "
            "benefit. The developments paper is "
            "the accepted project supplement for later AS/NZS 4600 changes. "
            "Cross-section only."
        ),
    )


def cross_section_capacity(
    pack_id: str,
    section: SectionProperties,
) -> CrossSectionCapacity:
    if pack_id == "as_nzs_4600_2005_a1_ewm":
        return as_nzs_4600_2005_a1_ewm_capacity(section)
    raise CapacityPackError(f"unsupported cross-section capacity pack {pack_id!r}")


def as_nzs_4600_2005_a1_member_capacity(
    section: SectionProperties,
    *,
    unbraced_length_m: float,
    minor_axis_effective_length_factor: float,
    torsional_effective_length_factor: float,
) -> MemberCompressionCapacity:
    """Return conservative unbraced lipped-channel member resistance.

    The effective area is the catalogue value at yield. Reusing it at the lower
    global buckling stress is conservative because the effective area is not
    allowed to recover as stress reduces. Full physical segment length is used
    for lateral-torsional buckling with Cb=1 and no cladding/bridging restraint
    credit. Appendix D and Section 7 provide distortional resistance, removing
    any need for design.py to assert that this limit state was checked.
    """

    section_capacity = as_nzs_4600_2005_a1_ewm_capacity(section)
    catalog = section.catalog
    if catalog is None:  # Guarded by the section-capacity call above.
        raise CapacityPackError("section has no immutable catalogue reference")
    properties = catalog.properties
    if str(properties.get("type", "")).strip().upper() != "C":
        raise CapacityPackError(
            "member pack currently supports singly symmetric lipped C sections"
        )
    if unbraced_length_m <= 0:
        raise CapacityPackError("member unbraced length must be positive")
    if (
        min(
            minor_axis_effective_length_factor,
            torsional_effective_length_factor,
        )
        <= 0
    ):
        raise CapacityPackError("member effective-length factors must be positive")

    fy_mpa = _positive(properties, "fy", "fy_MPa")
    elastic_modulus_mpa = _positive(properties, "E", "E_MPa")
    shear_modulus_mpa = _positive(properties, "G", "G_MPa")
    gross_area_mm2 = _positive(properties, "A", "A_mm2")
    effective_area_mm2 = _positive(properties, "Ae", "Ae_mm2")
    effective_modulus_mm3 = _positive(properties, "Zxe", "Zxe_mm3")
    gross_major_modulus_mm3 = _positive(properties, "Zx", "Zx_mm3")
    gross_minor_modulus_mm3 = _positive(properties, "Zy", "Zy_mm3")
    major_radius_mm = _positive(properties, "rx", "rx_mm")
    minor_radius_mm = _positive(properties, "ry", "ry_mm")
    shear_centre_offset_mm = _positive(properties, "x0", "x0_mm")
    polar_radius_squared_mm2 = _positive(properties, "ro2", "ro2_mm2")
    torsion_constant_mm4 = _positive(properties, "J", "J_mm4")
    warping_constant_mm6 = _positive(properties, "Iw", "Iw_mm6")
    flange_width_mm = _positive(properties, "flange", "flange_mm")
    lip_depth_mm = _positive(properties, "lip", "lip_mm")
    clear_web_depth_mm = _positive(properties, "d1", "d1_mm")
    thickness_mm = _positive(properties, "t", "t_mm")
    beta_y_mm = _positive(properties, "beta_y", "beta_y_mm")

    _require_prequalified_simple_lipped_channel(
        elastic_modulus_mpa=elastic_modulus_mpa,
        yield_stress_mpa=fy_mpa,
        thickness_mm=thickness_mm,
        flange_width_mm=flange_width_mm,
        lip_depth_mm=lip_depth_mm,
        clear_web_depth_mm=clear_web_depth_mm,
    )

    length_mm = unbraced_length_m * 1000.0
    minor_effective_length_mm = minor_axis_effective_length_factor * length_mm
    torsional_effective_length_mm = torsional_effective_length_factor * length_mm
    major_flexural_stress_mpa = (
        pi**2 * elastic_modulus_mpa / (length_mm / major_radius_mm) ** 2
    )
    minor_flexural_stress_mpa = (
        pi**2
        * elastic_modulus_mpa
        / (minor_effective_length_mm / minor_radius_mm) ** 2
    )
    torsional_stress_mpa = (
        shear_modulus_mpa * torsion_constant_mm4
        + pi**2 * elastic_modulus_mpa
        * warping_constant_mm6
        / torsional_effective_length_mm**2
    ) / (gross_area_mm2 * polar_radius_squared_mm2)
    elastic_major_axis_flexural_buckling_load_kN = (
        gross_area_mm2 * major_flexural_stress_mpa / 1000.0
    )
    elastic_minor_axis_flexural_buckling_load_kN = (
        gross_area_mm2 * minor_flexural_stress_mpa / 1000.0
    )
    coupling_factor = 1.0 - (shear_centre_offset_mm**2 / polar_radius_squared_mm2)
    if not 0 < coupling_factor <= 1:
        raise CapacityPackError(
            "catalogue x0 and ro2 do not define a valid channel coupling factor"
        )
    stress_sum = minor_flexural_stress_mpa + torsional_stress_mpa
    discriminant = 1.0 - (
        4.0
        * coupling_factor
        * minor_flexural_stress_mpa
        * torsional_stress_mpa
        / stress_sum**2
    )
    flexural_torsional_stress_mpa = (
        stress_sum / (2.0 * coupling_factor) * (1.0 - sqrt(max(0.0, discriminant)))
    )
    elastic_global_stress_mpa = min(
        major_flexural_stress_mpa,
        flexural_torsional_stress_mpa,
    )
    slenderness = sqrt(fy_mpa / elastic_global_stress_mpa)
    nominal_global_stress_mpa = (
        (0.658 ** (slenderness**2)) * fy_mpa
        if slenderness <= 1.5
        else (0.877 / slenderness**2) * fy_mpa
    )
    phi_c = 0.85
    nominal_global_compression_capacity_kN = (
        effective_area_mm2 * nominal_global_stress_mpa / 1000.0
    )

    distortional_compression = _simple_lipped_channel_distortional_stress(
        elastic_modulus_mpa=elastic_modulus_mpa,
        thickness_mm=thickness_mm,
        flange_width_mm=flange_width_mm,
        lip_depth_mm=lip_depth_mm,
        clear_web_depth_mm=clear_web_depth_mm,
        bending=False,
    )
    section_yield_compression_capacity_kN = gross_area_mm2 * fy_mpa / 1000.0
    elastic_distortional_compression_capacity_kN = (
        gross_area_mm2 * distortional_compression.stress_MPa / 1000.0
    )
    distortional_compression_slenderness = sqrt(
        section_yield_compression_capacity_kN
        / elastic_distortional_compression_capacity_kN
    )
    if distortional_compression_slenderness <= 0.561:
        nominal_distortional_compression_capacity_kN = (
            section_yield_compression_capacity_kN
        )
    else:
        elastic_yield_ratio = (
            elastic_distortional_compression_capacity_kN
            / section_yield_compression_capacity_kN
        )
        nominal_distortional_compression_capacity_kN = (
            1.0 - 0.25 * elastic_yield_ratio**0.6
        ) * elastic_yield_ratio**0.6 * section_yield_compression_capacity_kN

    # Clause 3.3.3.2: conservative singly-symmetric C-section LTB with Cb=1.
    elastic_lateral_torsional_buckling_moment_kNm = (
        gross_area_mm2
        * sqrt(polar_radius_squared_mm2)
        * sqrt(minor_flexural_stress_mpa * torsional_stress_mpa)
        / 1_000_000.0
    )
    section_yield_moment_kNm = gross_major_modulus_mm3 * fy_mpa / 1_000_000.0
    lateral_torsional_slenderness = sqrt(
        section_yield_moment_kNm
        / elastic_lateral_torsional_buckling_moment_kNm
    )
    if lateral_torsional_slenderness <= 0.60:
        critical_lateral_torsional_moment_kNm = section_yield_moment_kNm
    elif lateral_torsional_slenderness < 1.336:
        critical_lateral_torsional_moment_kNm = (
            1.11
            * section_yield_moment_kNm
            * (1.0 - 10.0 * lateral_torsional_slenderness**2 / 36.0)
        )
    else:
        critical_lateral_torsional_moment_kNm = (
            section_yield_moment_kNm / lateral_torsional_slenderness**2
        )
    # Zc at the reduced critical stress is not catalogued. Retaining the yield-
    # stress effective/gross modulus ratio prevents effective width recovery and
    # is conservative.
    nominal_lateral_torsional_bending_capacity_kNm = (
        effective_modulus_mm3
        / gross_major_modulus_mm3
        * critical_lateral_torsional_moment_kNm
    )

    # Clause 3.3.3.2(13): elastic LTB moment for a singly symmetric section
    # bent about the centroidal axis perpendicular to its symmetry axis. The
    # full segment remains unbraced and CTF=1. Both moment senses are evaluated;
    # the lower positive result is retained so a design cannot rely on a
    # favourable channel orientation that is absent from the mechanical model.
    beta_y_half_mm = beta_y_mm / 2.0
    minor_ltb_root_mm = sqrt(
        beta_y_half_mm**2
        + polar_radius_squared_mm2
        * torsional_stress_mpa
        / major_flexural_stress_mpa
    )
    minor_ltb_candidates_kNm = [
        moment_kNm
        for moment_kNm in (
            (
                sign
                * gross_area_mm2
                * major_flexural_stress_mpa
                * (beta_y_half_mm + sign * minor_ltb_root_mm)
                / 1_000_000.0
            )
            for sign in (-1.0, 1.0)
        )
        if moment_kNm > 0
    ]
    if not minor_ltb_candidates_kNm:
        raise CapacityPackError(
            "catalogue beta_y does not define a positive minor-axis LTB moment"
        )
    elastic_minor_lateral_torsional_buckling_moment_kNm = min(
        minor_ltb_candidates_kNm
    )
    minor_section_yield_moment_kNm = (
        gross_minor_modulus_mm3 * fy_mpa / 1_000_000.0
    )
    minor_lateral_torsional_slenderness = sqrt(
        minor_section_yield_moment_kNm
        / elastic_minor_lateral_torsional_buckling_moment_kNm
    )
    if minor_lateral_torsional_slenderness <= 0.60:
        critical_minor_lateral_torsional_moment_kNm = (
            minor_section_yield_moment_kNm
        )
    elif minor_lateral_torsional_slenderness < 1.336:
        critical_minor_lateral_torsional_moment_kNm = (
            1.11
            * minor_section_yield_moment_kNm
            * (1.0 - 10.0 * minor_lateral_torsional_slenderness**2 / 36.0)
        )
    else:
        critical_minor_lateral_torsional_moment_kNm = (
            minor_section_yield_moment_kNm
            / minor_lateral_torsional_slenderness**2
        )
    nominal_minor_lateral_torsional_bending_capacity_kNm = (
        section_capacity.effective_minor_modulus_mm3
        / gross_minor_modulus_mm3
        * critical_minor_lateral_torsional_moment_kNm
    )

    distortional_bending = _simple_lipped_channel_distortional_stress(
        elastic_modulus_mpa=elastic_modulus_mpa,
        thickness_mm=thickness_mm,
        flange_width_mm=flange_width_mm,
        lip_depth_mm=lip_depth_mm,
        clear_web_depth_mm=clear_web_depth_mm,
        bending=True,
    )
    elastic_distortional_bending_moment_kNm = (
        gross_major_modulus_mm3
        * distortional_bending.stress_MPa
        / 1_000_000.0
    )
    distortional_bending_slenderness = sqrt(
        section_yield_moment_kNm / elastic_distortional_bending_moment_kNm
    )
    if distortional_bending_slenderness <= 0.674:
        nominal_distortional_bending_capacity_kNm = section_yield_moment_kNm
    else:
        nominal_distortional_bending_capacity_kNm = (
            section_yield_moment_kNm
            / distortional_bending_slenderness
            * (1.0 - 0.22 / distortional_bending_slenderness)
        )

    phi_b = 0.90
    design_global_compression_capacity_kN = (
        phi_c * nominal_global_compression_capacity_kN
    )
    design_distortional_compression_capacity_kN = (
        phi_c * nominal_distortional_compression_capacity_kN
    )
    compression_capacities = {
        "section": section_capacity.design_compression_capacity_kN,
        "global": design_global_compression_capacity_kN,
        "distortional": design_distortional_compression_capacity_kN,
    }
    governing_compression_mode = min(
        compression_capacities,
        key=compression_capacities.__getitem__,
    )
    design_member_compression_capacity_kN = compression_capacities[
        governing_compression_mode
    ]

    design_lateral_torsional_bending_capacity_kNm = (
        phi_b * nominal_lateral_torsional_bending_capacity_kNm
    )
    design_distortional_bending_capacity_kNm = (
        phi_b * nominal_distortional_bending_capacity_kNm
    )
    design_minor_lateral_torsional_bending_capacity_kNm = (
        phi_b * nominal_minor_lateral_torsional_bending_capacity_kNm
    )
    bending_capacities = {
        "section": section_capacity.design_major_bending_capacity_kNm,
        "lateral_torsional": design_lateral_torsional_bending_capacity_kNm,
        "distortional": design_distortional_bending_capacity_kNm,
    }
    governing_bending_mode = min(
        bending_capacities,
        key=bending_capacities.__getitem__,
    )
    design_major_bending_capacity_kNm = bending_capacities[
        governing_bending_mode
    ]
    minor_bending_capacities = {
        "section": section_capacity.design_minor_bending_capacity_kNm,
        "lateral_torsional": (
            design_minor_lateral_torsional_bending_capacity_kNm
        ),
    }
    governing_minor_bending_mode = min(
        minor_bending_capacities,
        key=minor_bending_capacities.__getitem__,
    )
    design_minor_bending_capacity_kNm = minor_bending_capacities[
        governing_minor_bending_mode
    ]
    return MemberCompressionCapacity(
        pack_id="as_nzs_4600_2005_a1_member",
        section_record_sha256=catalog.record_sha256,
        elastic_flexural_buckling_stress_MPa=min(
            major_flexural_stress_mpa,
            minor_flexural_stress_mpa,
        ),
        elastic_torsional_buckling_stress_MPa=torsional_stress_mpa,
        elastic_flexural_torsional_buckling_stress_MPa=(flexural_torsional_stress_mpa),
        elastic_distortional_compression_stress_MPa=(
            distortional_compression.stress_MPa
        ),
        elastic_distortional_bending_stress_MPa=distortional_bending.stress_MPa,
        elastic_lateral_torsional_buckling_moment_kNm=(
            elastic_lateral_torsional_buckling_moment_kNm
        ),
        elastic_minor_lateral_torsional_buckling_moment_kNm=(
            elastic_minor_lateral_torsional_buckling_moment_kNm
        ),
        elastic_major_axis_flexural_buckling_load_kN=(
            elastic_major_axis_flexural_buckling_load_kN
        ),
        elastic_minor_axis_flexural_buckling_load_kN=(
            elastic_minor_axis_flexural_buckling_load_kN
        ),
        nominal_global_buckling_stress_MPa=nominal_global_stress_mpa,
        nominal_global_compression_capacity_kN=(
            nominal_global_compression_capacity_kN
        ),
        nominal_distortional_compression_capacity_kN=(
            nominal_distortional_compression_capacity_kN
        ),
        nominal_lateral_torsional_bending_capacity_kNm=(
            nominal_lateral_torsional_bending_capacity_kNm
        ),
        nominal_distortional_bending_capacity_kNm=(
            nominal_distortional_bending_capacity_kNm
        ),
        nominal_minor_lateral_torsional_bending_capacity_kNm=(
            nominal_minor_lateral_torsional_bending_capacity_kNm
        ),
        design_member_compression_capacity_kN=(design_member_compression_capacity_kN),
        design_major_bending_capacity_kNm=design_major_bending_capacity_kNm,
        design_minor_bending_capacity_kNm=design_minor_bending_capacity_kNm,
        design_global_compression_capacity_kN=(
            design_global_compression_capacity_kN
        ),
        design_distortional_compression_capacity_kN=(
            design_distortional_compression_capacity_kN
        ),
        design_lateral_torsional_bending_capacity_kNm=(
            design_lateral_torsional_bending_capacity_kNm
        ),
        design_distortional_bending_capacity_kNm=(
            design_distortional_bending_capacity_kNm
        ),
        design_section_minor_bending_capacity_kNm=(
            section_capacity.design_minor_bending_capacity_kNm
        ),
        design_minor_lateral_torsional_bending_capacity_kNm=(
            design_minor_lateral_torsional_bending_capacity_kNm
        ),
        design_web_shear_capacity_kN=section_capacity.design_web_shear_capacity_kN,
        design_off_axis_shear_capacity_kN=(
            section_capacity.design_off_axis_shear_capacity_kN
        ),
        design_st_venant_torsion_capacity_kNm=(
            section_capacity.design_st_venant_torsion_capacity_kNm
        ),
        governing_compression_mode=cast(
            Literal["section", "global", "distortional"],
            governing_compression_mode,
        ),
        governing_bending_mode=cast(
            Literal["section", "lateral_torsional", "distortional"],
            governing_bending_mode,
        ),
        governing_minor_bending_mode=cast(
            Literal["section", "lateral_torsional"],
            governing_minor_bending_mode,
        ),
        slenderness=slenderness,
        phi_c=phi_c,
        phi_b=phi_b,
        standard_reference=AS_NZS_4600_2005_A1_REFERENCE,
        standard_status=ACCEPTED_STANDARD_STATUS,
        standard_source_sha256=AS_NZS_4600_2005_A1_SHA256,
        developments_supplement_sha256=AS_NZS_4600_DEVELOPMENTS_SUPPLEMENT_SHA256,
        basis=(
            "AS/NZS 4600:2005 incorporating Amendment No. 1 project basis: global "
            "compression to Clause 3.4.1, unbraced lateral-torsional bending "
            "about both centroidal axes to Clause 3.3.3.2 with Cb=1, CTF=1, "
            "and the less favourable minor-axis moment sense, distortional "
            "bending to Clause "
            "3.3.3.3 and Appendix D3, and distortional compression to Clause "
            "7.2.1.4 and Appendix D2. Full segment length is unbraced; no "
            "cladding or bridging restraint is credited. Catalogue Ae and the "
            "yield-stress Zxe/Zx and Zey/Zy ratios are retained "
            "conservatively. Web shear, off-axis shear, and full St-Venant "
            "torsion capacities are inherited from the section pack without "
            "credit for warping restraint. The 2018 "
            "developments paper is the accepted project supplement for later "
            "AS/NZS 4600 changes."
        ),
    )


def member_compression_capacity(
    pack_id: str,
    section: SectionProperties,
    *,
    unbraced_length_m: float,
    minor_axis_effective_length_factor: float,
    torsional_effective_length_factor: float,
) -> MemberCompressionCapacity:
    if pack_id == "as_nzs_4600_2005_a1_member":
        return as_nzs_4600_2005_a1_member_capacity(
            section,
            unbraced_length_m=unbraced_length_m,
            minor_axis_effective_length_factor=(minor_axis_effective_length_factor),
            torsional_effective_length_factor=torsional_effective_length_factor,
        )
    raise CapacityPackError(f"unsupported member capacity pack {pack_id!r}")
