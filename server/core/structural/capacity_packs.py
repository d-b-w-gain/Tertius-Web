from __future__ import annotations

from dataclasses import dataclass
from math import pi, sqrt
from typing import Literal, cast

from .contracts import SectionProperties


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
