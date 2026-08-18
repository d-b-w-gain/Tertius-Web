from __future__ import annotations

import pytest

from server.core.structural.capacity_packs import (
    AS_NZS_4600_2005_A1_SHA256,
    CapacityPackError,
    as_nzs_4600_2005_a1_member_capacity,
)
from server.core.structural.contracts import SectionCatalogReference, SectionProperties


def _c10012_section(**property_overrides: object) -> SectionProperties:
    properties: dict[str, object] = {
        "validated": True,
        "type": "C",
        "depth_mm": 102,
        "t_mm": 1.2,
        "flange_mm": 51,
        "lip_mm": 12.5,
        "d1_mm": 96.0,
        "fy_MPa": 500,
        "E_MPa": 200000,
        "G_MPa": 80000,
        "A_mm2": 258,
        "Ix_mm4": 432000,
        "Iy_mm4": 89200,
        "Zx_mm3": 8480,
        "Zy_mm3": 2590,
        "rx_mm": 41.0,
        "ry_mm": 18.6,
        "x0_mm": 39.7,
        "J_mm4": 124,
        "Iw_mm6": 188000000,
        "ro2_mm2": 3603,
        "beta_y_mm": 123,
        "Zxe_mm3": 6740,
        "Ae_mm2": 153,
    }
    properties.update(property_overrides)
    return SectionProperties(
        id="section-c10012",
        label="C100x1.2 (Lysaght)",
        area_m2=0.000258,
        iy_m4=4.32e-7,
        iz_m4=8.92e-8,
        torsion_j_m4=1.24e-10,
        catalog=SectionCatalogReference(
            catalog_id="lysaght-zc-v2",
            catalog_version="2.0",
            section_key="C10012 (100x1.2)",
            source="Lysaght Purlins & Girts D&I Guide BLY0111_A (05/2025)",
            record_sha256="a" * 64,
            axis_mapping={"local_y_inertia": "Iy", "local_z_inertia": "Ix"},
            properties=properties,
        ),
    )


def test_member_pack_calculates_unbraced_and_distortional_modes() -> None:
    capacity = as_nzs_4600_2005_a1_member_capacity(
        _c10012_section(),
        unbraced_length_m=3.0,
        minor_axis_effective_length_factor=1.0,
        torsional_effective_length_factor=1.0,
    )

    assert capacity.standard_reference == (
        "AS/NZS 4600:2005 incorporating Amendment No. 1"
    )
    assert capacity.standard_source_sha256 == AS_NZS_4600_2005_A1_SHA256
    assert capacity.standard_status == (
        "accepted_project_basis_2005_a1_with_developments_supplement"
    )
    assert capacity.elastic_distortional_compression_stress_MPa == pytest.approx(
        245.7394361
    )
    assert capacity.elastic_distortional_bending_stress_MPa == pytest.approx(
        370.3912046
    )
    assert capacity.design_member_compression_capacity_kN == pytest.approx(
        4.351315736
    )
    assert capacity.design_major_bending_capacity_kNm == pytest.approx(0.715827833)
    assert capacity.governing_compression_mode == "global"
    assert capacity.governing_bending_mode == "lateral_torsional"
    assert capacity.design_member_compression_capacity_kN == pytest.approx(
        capacity.design_global_compression_capacity_kN
    )
    assert capacity.design_major_bending_capacity_kNm == pytest.approx(
        capacity.design_lateral_torsional_bending_capacity_kNm
    )
    assert capacity.design_minor_bending_capacity_kNm == pytest.approx(
        0.2145886048
    )
    assert capacity.governing_minor_bending_mode == "lateral_torsional"
    assert capacity.design_off_axis_shear_capacity_kN == pytest.approx(15.657948)
    assert capacity.design_st_venant_torsion_capacity_kNm == pytest.approx(0.0279)
    assert "Cb=1" in capacity.basis
    assert "no cladding or bridging restraint is credited" in capacity.basis


def test_member_pack_does_not_require_project_restraint_assertions() -> None:
    short_capacity = as_nzs_4600_2005_a1_member_capacity(
        _c10012_section(),
        unbraced_length_m=1.5,
        minor_axis_effective_length_factor=1.0,
        torsional_effective_length_factor=1.0,
    )
    long_capacity = as_nzs_4600_2005_a1_member_capacity(
        _c10012_section(),
        unbraced_length_m=3.0,
        minor_axis_effective_length_factor=1.0,
        torsional_effective_length_factor=1.0,
    )

    assert short_capacity.design_member_compression_capacity_kN > (
        long_capacity.design_member_compression_capacity_kN
    )
    assert short_capacity.design_major_bending_capacity_kNm > (
        long_capacity.design_major_bending_capacity_kNm
    )


def test_member_pack_rejects_channel_outside_prequalified_bounds() -> None:
    with pytest.raises(CapacityPackError, match="prequalification bounds"):
        as_nzs_4600_2005_a1_member_capacity(
            _c10012_section(d1_mm=600.0),
            unbraced_length_m=3.0,
            minor_axis_effective_length_factor=1.0,
            torsional_effective_length_factor=1.0,
        )
