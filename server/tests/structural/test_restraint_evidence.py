from core.structural.contracts import RestraintConfigurationIdentity
from core.structural.restraint_evidence import (
    match_restraint_evidence_pack,
    resolve_restraint_evidence,
)


PACK_ID = "lysaght-zc-2026-08-c10012-100ac-pb1230hs"


def test_restraint_evidence_pack_matches_exact_rendered_configuration() -> None:
    resolution = resolve_restraint_evidence(
        PACK_ID,
        RestraintConfigurationIdentity(
            primary_part_number="C10019",
            bracing_part_number="C10012",
            connector_part_numbers=["100AC", "PB1230HS", "PB1230HS"],
        ),
    )

    assert resolution.identity_status == "pass"
    assert resolution.identity_mismatches == ()
    assert resolution.design_force_capacity_kN is None
    assert resolution.design_moment_capacity_kNm is None
    assert resolution.stiffness_status == "unverified"
    assert resolution.restrains_lateral_translation is True
    assert resolution.restrains_twist is True
    assert resolution.demand_model == "as_nzs_4600_2005_4_3_2_flange_force"
    assert any("8202f3c7" in reference for reference in resolution.references)


def test_restraint_evidence_pack_is_selected_from_exact_rendered_parts() -> None:
    resolution = match_restraint_evidence_pack(
        RestraintConfigurationIdentity(
            primary_part_number="C10019",
            bracing_part_number="C10012",
            connector_part_numbers=["PB1230HS", "100AC", "PB1230HS"],
        )
    )

    assert resolution is not None
    assert resolution.pack_id == PACK_ID
    assert resolution.identity_status == "pass"


def test_restraint_evidence_pack_fails_closed_on_generic_short_bolts() -> None:
    resolution = resolve_restraint_evidence(
        PACK_ID,
        RestraintConfigurationIdentity(
            primary_part_number="C10019",
            bracing_part_number="C10012",
            connector_part_numbers=[
                "100AC",
                "DIN-6921-M12X25",
                "DIN-6921-M12X25",
            ],
        ),
    )

    assert resolution.identity_status == "fail"
    assert resolution.design_force_capacity_kN is None
    assert any(
        "PB1230HS" in mismatch and "M12X25" in mismatch
        for mismatch in resolution.identity_mismatches
    )


def test_unknown_restraint_evidence_pack_is_an_identity_failure() -> None:
    resolution = resolve_restraint_evidence(
        "missing-pack",
        RestraintConfigurationIdentity(),
    )

    assert resolution.identity_status == "fail"
    assert resolution.pack_version is None
    assert resolution.identity_mismatches == (
        "evidence pack 'missing-pack' is not registered",
    )
