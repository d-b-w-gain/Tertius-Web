from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Literal

from .contracts import SectionProperties


class CapacityPackError(ValueError):
    """Raised when a section lacks the traceable data required by a capacity pack."""


@dataclass(frozen=True)
class CrossSectionCapacity:
    pack_id: str
    section_record_sha256: str
    design_compression_capacity_kN: float
    design_major_bending_capacity_kNm: float
    design_web_shear_capacity_kN: float
    phi_c: float
    phi_b: float
    phi_v: float
    web_slenderness: float
    web_yield_boundary: float
    web_elastic_boundary: float
    shear_regime: Literal["stocky", "inelastic_buckling", "elastic_buckling"]
    basis: str


@dataclass(frozen=True)
class MemberCompressionCapacity:
    pack_id: str
    section_record_sha256: str
    elastic_flexural_buckling_stress_MPa: float
    elastic_torsional_buckling_stress_MPa: float
    elastic_flexural_torsional_buckling_stress_MPa: float
    nominal_global_buckling_stress_MPa: float
    design_member_compression_capacity_kN: float
    design_major_bending_capacity_kNm: float
    slenderness: float
    phi_c: float
    basis: str


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


def as_nzs_4600_2018_ewm_capacity(
    section: SectionProperties,
) -> CrossSectionCapacity:
    """Return section-only C/Z capacities from a traceable catalogue record.

    This pack deliberately stops before member buckling, restraint, connection,
    and system checks. Inputs use the catalogue's effective area/modulus and the
    AS/NZS 4600 web-shear equations.
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
    clear_web_depth_mm = _positive(properties, "d1", "d1_mm")
    thickness_mm = _positive(properties, "t", "t_mm")

    # AS/NZS 4600:2018 capacity factors for the section-only limit states used
    # here. The pack ID freezes these values and equations as one auditable unit.
    phi_b = 0.95
    phi_c = 0.85
    phi_v = 0.90
    shear_buckling_coefficient = 5.34

    nominal_bending_kNm = effective_modulus_mm3 * fy_mpa / 1_000_000.0
    nominal_compression_kN = effective_area_mm2 * fy_mpa / 1000.0

    web_slenderness = clear_web_depth_mm / thickness_mm
    web_yield_boundary = sqrt(
        elastic_modulus_mpa * shear_buckling_coefficient / fy_mpa
    )
    web_elastic_boundary = 1.415 * web_yield_boundary
    shear_regime: Literal["stocky", "inelastic_buckling", "elastic_buckling"]
    if web_slenderness <= web_yield_boundary:
        shear_regime = "stocky"
        nominal_shear_n = (
            0.64 * fy_mpa * clear_web_depth_mm * thickness_mm
        )
    elif web_slenderness <= web_elastic_boundary:
        shear_regime = "inelastic_buckling"
        nominal_shear_n = (
            0.64
            * thickness_mm**2
            * sqrt(
                elastic_modulus_mpa
                * shear_buckling_coefficient
                * fy_mpa
            )
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

    return CrossSectionCapacity(
        pack_id="as_nzs_4600_2018_ewm",
        section_record_sha256=catalog.record_sha256,
        design_compression_capacity_kN=(
            phi_c * nominal_compression_kN
        ),
        design_major_bending_capacity_kNm=(
            phi_b * nominal_bending_kNm
        ),
        design_web_shear_capacity_kN=phi_v * nominal_shear_n / 1000.0,
        phi_c=phi_c,
        phi_b=phi_b,
        phi_v=phi_v,
        web_slenderness=web_slenderness,
        web_yield_boundary=web_yield_boundary,
        web_elastic_boundary=web_elastic_boundary,
        shear_regime=shear_regime,
        basis=(
            "AS/NZS 4600:2018 effective-width section resistance using catalogue "
            "Ae and Zxe; web shear uses kv=5.34. Cross-section only."
        ),
    )


def cross_section_capacity(
    pack_id: str,
    section: SectionProperties,
) -> CrossSectionCapacity:
    if pack_id == "as_nzs_4600_2018_ewm":
        return as_nzs_4600_2018_ewm_capacity(section)
    raise CapacityPackError(f"unsupported cross-section capacity pack {pack_id!r}")


def as_nzs_4600_2018_ewm_member_capacity(
    section: SectionProperties,
    *,
    unbraced_length_m: float,
    minor_axis_effective_length_factor: float,
    torsional_effective_length_factor: float,
) -> MemberCompressionCapacity:
    """Return conservative lipped-channel global compression resistance.

    The effective area is the catalogue value at yield. Reusing it at the lower
    global buckling stress is conservative because the effective area is not
    allowed to recover as stress reduces. Lateral-torsional bending remains a
    separate restraint-dependent limit state.
    """

    section_capacity = as_nzs_4600_2018_ewm_capacity(section)
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
    if min(
        minor_axis_effective_length_factor,
        torsional_effective_length_factor,
    ) <= 0:
        raise CapacityPackError("member effective-length factors must be positive")

    fy_mpa = _positive(properties, "fy", "fy_MPa")
    elastic_modulus_mpa = _positive(properties, "E", "E_MPa")
    shear_modulus_mpa = _positive(properties, "G", "G_MPa")
    gross_area_mm2 = _positive(properties, "A", "A_mm2")
    effective_area_mm2 = _positive(properties, "Ae", "Ae_mm2")
    major_radius_mm = _positive(properties, "rx", "rx_mm")
    minor_radius_mm = _positive(properties, "ry", "ry_mm")
    shear_centre_offset_mm = _positive(properties, "x0", "x0_mm")
    polar_radius_squared_mm2 = _positive(properties, "ro2", "ro2_mm2")
    torsion_constant_mm4 = _positive(properties, "J", "J_mm4")
    warping_constant_mm6 = _positive(properties, "Iw", "Iw_mm6")

    length_mm = unbraced_length_m * 1000.0
    minor_effective_length_mm = (
        minor_axis_effective_length_factor * length_mm
    )
    torsional_effective_length_mm = (
        torsional_effective_length_factor * length_mm
    )
    major_flexural_stress_mpa = (
        (3.141592653589793**2)
        * elastic_modulus_mpa
        / (length_mm / major_radius_mm) ** 2
    )
    minor_flexural_stress_mpa = (
        (3.141592653589793**2)
        * elastic_modulus_mpa
        / (minor_effective_length_mm / minor_radius_mm) ** 2
    )
    torsional_stress_mpa = (
        shear_modulus_mpa * torsion_constant_mm4
        + (3.141592653589793**2)
        * elastic_modulus_mpa
        * warping_constant_mm6
        / torsional_effective_length_mm**2
    ) / (gross_area_mm2 * polar_radius_squared_mm2)
    coupling_factor = 1.0 - (
        shear_centre_offset_mm**2 / polar_radius_squared_mm2
    )
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
        stress_sum
        / (2.0 * coupling_factor)
        * (1.0 - sqrt(max(0.0, discriminant)))
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
    design_member_compression_capacity_kN = (
        phi_c * effective_area_mm2 * nominal_global_stress_mpa / 1000.0
    )
    return MemberCompressionCapacity(
        pack_id="as_nzs_4600_2018_ewm_member",
        section_record_sha256=catalog.record_sha256,
        elastic_flexural_buckling_stress_MPa=min(
            major_flexural_stress_mpa,
            minor_flexural_stress_mpa,
        ),
        elastic_torsional_buckling_stress_MPa=torsional_stress_mpa,
        elastic_flexural_torsional_buckling_stress_MPa=(
            flexural_torsional_stress_mpa
        ),
        nominal_global_buckling_stress_MPa=nominal_global_stress_mpa,
        design_member_compression_capacity_kN=(
            design_member_compression_capacity_kN
        ),
        design_major_bending_capacity_kNm=(
            section_capacity.design_major_bending_capacity_kNm
        ),
        slenderness=slenderness,
        phi_c=phi_c,
        basis=(
            "AS/NZS 4600:2018 EWM global flexural/flexural-torsional "
            "compression curve using catalogue A, Ae, rx, ry, x0, ro2, J, "
            "and Iw. Catalogue Ae at yield is retained conservatively. "
            "Lateral-torsional bending requires verified compression-flange "
            "and twist restraint."
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
    if pack_id == "as_nzs_4600_2018_ewm_member":
        return as_nzs_4600_2018_ewm_member_capacity(
            section,
            unbraced_length_m=unbraced_length_m,
            minor_axis_effective_length_factor=(
                minor_axis_effective_length_factor
            ),
            torsional_effective_length_factor=torsional_effective_length_factor,
        )
    raise CapacityPackError(f"unsupported member capacity pack {pack_id!r}")
