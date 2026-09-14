from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
import json

from core.structural.certificate_report import build_review_pack
from core.structural.review_pack_validation import validate_structural_review_pack


def _pack(
    *,
    nested_status: str | None = None,
    hash_override: str | None = None,
    approved_claim: bool = False,
    scope_status: str = "within_scope",
) -> bytes:
    protocol: dict[str, object] = {
        "schema_version": "tertius.structural.abcb-protocol-disclosure.v1",
        "protocol_id": "ABCB Protocol for Structural Software",
        "protocol_edition": "2011.2",
        "claim_status": "not_appraised",
        "workflow_status": "engineer_review_required",
    }
    if approved_claim:
        protocol.update(
            {
                "claim_status": "independently_appraised",
                "workflow_status": "trained_user_signoff_enabled",
                "compliance_document": {
                    "identifier": "TERTIUS-CD-001",
                    "version": "1",
                    "sha256": "c" * 64,
                },
            }
        )
    pass_check: dict[str, object] = {"status": "pass"}
    connection: dict[str, object] = deepcopy(pass_check)
    if nested_status is not None:
        connection["bolted_sheet_interface"] = {"status": nested_status}
    scope_checks = [
        {"id": check_id, "status": "pass"}
        for check_id in (
            "deemed_to_satisfy",
            "eaves_height",
            "roof_height",
            "building_width",
            "length_width_ratio",
            "roof_pitch",
        )
    ]
    evidence = {
        "schema_version": "tertius.structural.evidence.v2",
        "document_status": "controlled_unsigned_draft",
        "abcb_protocol": protocol,
        "report_identity": "r" * 64,
        "analysis": {"key_digest": "a" * 64},
        "snapshot": {
            "abcb_protocol_scope": {
                "status": scope_status,
                "checks": scope_checks,
            },
            "certification_readiness": {
                "ready_for_certificate_draft": True,
                "model_coverage": {"status": "complete"},
                "gates": [{"status": "pass"}],
            },
            "cross_section_checks": [pass_check],
            "member_stability_checks": [pass_check],
            "connection_checks": [connection],
            "tension_member_checks": [pass_check],
            "bracing_load_path_traces": [pass_check],
            "serviceability_checks": [pass_check],
        },
    }
    evidence_content = json.dumps(evidence, sort_keys=True).encode()
    pdf_content = b"%PDF-1.4\nCONTROLLED DRAFT\n%%EOF"
    manifest = {
        "schema_version": "tertius.structural.report-manifest.v2",
        "document_status": "controlled_unsigned_draft",
        "abcb_protocol": protocol,
        "report_identity": "r" * 64,
        "analysis": {"key_digest": "a" * 64},
        "files": [
            {
                "name": "shed-structural-certificate-draft.pdf",
                "content_type": "application/pdf",
                "byte_size": len(pdf_content),
                "sha256": hash_override or sha256(pdf_content).hexdigest(),
            },
            {
                "name": "shed-structural-evidence.json",
                "content_type": "application/json",
                "byte_size": len(evidence_content),
                "sha256": sha256(evidence_content).hexdigest(),
            },
        ],
    }
    return build_review_pack(
        project_name="shed",
        pdf_content=pdf_content,
        evidence_content=evidence_content,
        manifest=manifest,
        generated_at=datetime(2026, 9, 5, tzinfo=UTC),
    )


def test_technical_profile_verifies_integrity_and_closed_checks() -> None:
    result = validate_structural_review_pack(_pack())

    assert result.ok
    assert result.model_dump()["status"] == "pass"


def test_integrity_mismatch_fails() -> None:
    result = validate_structural_review_pack(_pack(hash_override="0" * 64))

    assert not result.ok
    assert "PACK_HASH_MISMATCH" in {finding.code for finding in result.findings}


def test_nested_selected_connection_check_fails_closed() -> None:
    result = validate_structural_review_pack(_pack(nested_status="unsupported"))

    assert not result.ok
    assert "SELECTED_CONNECTION_SUBCHECK_OPEN" in {
        finding.code for finding in result.findings
    }


def test_abcb_claim_profile_rejects_unappraised_release() -> None:
    result = validate_structural_review_pack(_pack(), profile="abcb_claim")

    assert not result.ok
    codes = {finding.code for finding in result.findings}
    assert "ABCB_RELEASE_NOT_APPROVED" in codes
    assert "ABCB_COMPLIANCE_DOCUMENT_MISSING" in codes


def test_abcb_claim_profile_accepts_controlled_approved_disclosure() -> None:
    result = validate_structural_review_pack(
        _pack(approved_claim=True),
        profile="abcb_claim",
    )

    assert result.ok


def test_abcb_claim_profile_rejects_outside_scope_job() -> None:
    result = validate_structural_review_pack(
        _pack(approved_claim=True, scope_status="outside_scope"),
        profile="abcb_claim",
    )

    assert not result.ok
    assert "ABCB_JOB_OUTSIDE_SCOPE" in {
        finding.code for finding in result.findings
    }
