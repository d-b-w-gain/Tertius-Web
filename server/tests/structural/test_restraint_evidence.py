from core.structural.contracts import RestraintConfigurationIdentity
from core.structural.restraint_evidence import resolve_restraint_evidence


PACK_ID = "lysaght-zc-2026-07-c10012-100ac-pb1230hs"


def test_restraint_evidence_pack_matches_exact_rendered_configuration() -> None:
    resolution = resolve_restraint_evidence(
        PACK_ID,
        RestraintConfigurationIdentity(
            primary_part_number="C10019",
            bracing_part_number="C10012",
            connector_part_numbers=["100AC-PB1230HS-M12X30-G8.8-END-JOINTS"],
        ),
    )

    assert resolution.identity_status == "pass"
    assert resolution.identity_mismatches == ()
    assert resolution.design_force_capacity_kN is None
    assert resolution.design_moment_capacity_kNm is None
    assert resolution.stiffness_status == "unverified"
    assert any("8202f3c7" in reference for reference in resolution.references)


def test_restraint_evidence_pack_fails_closed_on_generic_short_bolts() -> None:
    resolution = resolve_restraint_evidence(
        PACK_ID,
        RestraintConfigurationIdentity(
            primary_part_number="C10019",
            bracing_part_number="C10012",
            connector_part_numbers=["100AC-M12X25-END-JOINTS"],
        ),
    )

    assert resolution.identity_status == "fail"
    assert resolution.design_force_capacity_kN is None
    assert any(
        "PB1230HS-M12X30-G8.8" in mismatch and "M12X25" in mismatch
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
