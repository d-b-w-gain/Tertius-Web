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
