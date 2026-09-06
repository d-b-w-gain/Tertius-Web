from datetime import UTC, datetime
from hashlib import sha256
import io
import json
from types import SimpleNamespace
from typing import cast
from zipfile import ZipFile

from core.structural.certificate_report import (
    build_manifest,
    build_review_pack,
    build_structural_certificate_pdf,
    build_structural_evidence_json,
    certificate_filename,
    evidence_filename,
    report_identity,
)
from core.structural.contracts import StructuralSnapshot
from core.structural.report_exports import certificate_export_blockers


def _item(**values):
    return SimpleNamespace(**values)


def _snapshot(
    *,
    serviceability_status: str = "not_applicable",
    protocol_scope_status: str | None = None,
):
    pass_check = _item(status="pass")
    readiness = _item(
        ready_for_certificate_draft=True,
        blocking_reasons=[],
        issues=[],
        model_coverage=_item(status="complete", summary="All members solved."),
        gates=[
            _item(
                order=1,
                label="Project and NCC basis",
                primary_reference="NCC 2022",
                status="pass",
                summary="Project basis is recorded.",
            ),
            _item(
                order=2,
                label="Actions and combinations",
                primary_reference="AS/NZS 1170",
                status="pass",
                summary="Actions are verified.",
            ),
        ],
    )
    snapshot = _item(
        source=_item(
            design_hash="d" * 64,
            analysis_configuration_revision=7,
            analysis_configuration_digest="c" * 64,
        ),
        certification_readiness=readiness,
        design_basis=_item(
            framework_id="AU-NCC-2022",
            framework_reference="NCC 2022 Amendment 2",
            jurisdiction="Australia / New South Wales",
            building_classification="10a",
            importance_level="2",
            design_life_years=50,
            analysis_method="Second-order frame analysis",
            compliance_pathway="Deemed-to-Satisfy",
            standards={
                "actions": "AS/NZS 1170",
                "members": "AS/NZS 4600:2005+A1",
            },
        ),
        abcb_protocol_scope=(
            _item(
                status=protocol_scope_status,
                compliance_pathway="Deemed-to-Satisfy",
                geometry=_item(
                    eaves_height_m=2.4,
                    roof_height_m=3.0,
                    building_width_m=3.0,
                    length_width_ratio=5.0 / 3.0,
                    roof_pitch_degrees=21.8,
                ),
                blocking_reasons=[],
            )
            if protocol_scope_status is not None
            else None
        ),
        cross_section_checks=[pass_check],
        member_stability_checks=[pass_check],
        connection_checks=[pass_check],
        tension_member_checks=[pass_check],
        bracing_load_path_traces=[pass_check],
        serviceability_checks=[
            _item(
                status="pass",
                member_id="R1",
                physical_member_id="R1",
                basis="L/250",
            ),
            _item(
                status=serviceability_status,
                member_id="B1",
                physical_member_id="B1",
                basis=(
                    "Transverse L/n deflection is not applicable to an axial-only "
                    "tension/compression member."
                ),
            ),
        ],
        members=[_item(id="R1"), _item(id="B1")],
        nodes=[_item(id="N1"), _item(id="N2")],
        reactions=[_item(node_id="N1")],
        solver=_item(
            name="PyNite",
            version="2.4.1",
            analysis="P-Delta",
            combination_id="SLS-G+W",
        ),
        equilibrium=_item(
            status="pass",
            force_residual_kN=_item(x=0.0, y=0.0, z=0.0),
        ),
        load_summary=_item(
            member_mass_kg=100.0,
            self_weight_kN=0.981,
            additional_dead_load_kN=1.0,
            imposed_load_kN=1.4,
            wind_load_kN=2.5,
        ),
        load_combinations=[_item(id="SLS-G+W")],
        unavailable_load_combinations=[],
        wind_action_bases=[],
        stability=_item(
            combination_id="ULS-W+X",
            converged=True,
            governing_moment_amplification=1.0015,
            governing_displacement_amplification=1.0012,
            second_order_required=False,
            direction_results=[_item(id="+X")],
        ),
        calculation_sheets=[
            _item(
                id="sheet-au-decision",
                stage_id="decision",
                title="Engineering decision",
                status="pass",
                primary_reference="NCC A5G3",
                assumptions=["Fabrication and installation must match the model."],
                inputs=[],
                equations=[],
                outputs=[],
            )
        ],
    )
    snapshot.model_dump = lambda mode="json": {
        "schema_version": "2.0",
        "source": {"design_hash": "d" * 64},
        "serviceability_checks": [
            {"member_id": item.member_id, "status": item.status, "basis": item.basis}
            for item in snapshot.serviceability_checks
        ],
    }
    return snapshot


def _metadata() -> dict[str, object]:
    return {
        "key_digest": "a" * 64,
        "design_digest": "d" * 64,
        "configuration_digest": "c" * 64,
        "site_digest": "s" * 64,
        "engine_version": "test-engine",
        "snapshot_schema_version": "2",
        "combination_id": "__governing_default__",
        "calculation_duration_seconds": 149.27,
        "calculated_at": "2026-09-03T01:20:22Z",
    }


def test_certificate_report_is_deterministic_and_discloses_applicability():
    snapshot = cast(
        StructuralSnapshot,
        _snapshot(protocol_scope_status="within_scope"),
    )
    generated_at = datetime(2026, 9, 3, 2, 0, tzinfo=UTC)
    identity = report_identity(analysis_key_digest="a" * 64)
    evidence = build_structural_evidence_json(
        project_name="shed",
        snapshot=snapshot,
        analysis_metadata=_metadata(),
        report_id=identity,
        generated_at=generated_at,
    )
    evidence_digest = sha256(evidence).hexdigest()

    first = build_structural_certificate_pdf(
        project_name="shed",
        snapshot=snapshot,
        analysis_metadata=_metadata(),
        report_id=identity,
        generated_at=generated_at,
        evidence_sha256=evidence_digest,
    )
    second = build_structural_certificate_pdf(
        project_name="shed",
        snapshot=snapshot,
        analysis_metadata=_metadata(),
        report_id=identity,
        generated_at=generated_at,
        evidence_sha256=evidence_digest,
    )

    assert first == second
    assert first.startswith(b"%PDF-")
    assert first.rstrip().endswith(b"%%EOF")
    assert b"/CreationDate (D:20260903020000Z)" in first
    assert b"CONTROLLED DRAFT" in first
    assert b"ABCB Protocol status" in first
    assert b"Protocol job scope" in first
    assert b"WITHIN SCOPE" in first
    assert b"2.400 m / 6.000 m" in first
    assert b"Reviewing engineer declaration" in first
    assert b"1 NOT APPLICABLE / 0 NOT CHECKED" in first
    assert b"B1" in first


def test_review_pack_manifest_hashes_exact_artifacts():
    snapshot = cast(StructuralSnapshot, _snapshot())
    generated_at = datetime(2026, 9, 3, 2, 0, tzinfo=UTC)
    identity = report_identity(analysis_key_digest="a" * 64)
    evidence = build_structural_evidence_json(
        project_name="shed",
        snapshot=snapshot,
        analysis_metadata=_metadata(),
        report_id=identity,
        generated_at=generated_at,
    )
    pdf = build_structural_certificate_pdf(
        project_name="shed",
        snapshot=snapshot,
        analysis_metadata=_metadata(),
        report_id=identity,
        generated_at=generated_at,
        evidence_sha256=sha256(evidence).hexdigest(),
    )
    manifest = build_manifest(
        project_name="shed",
        report_id=identity,
        analysis_metadata=_metadata(),
        generated_at=generated_at,
        pdf_content=pdf,
        evidence_content=evidence,
    )
    pack = build_review_pack(
        project_name="shed",
        pdf_content=pdf,
        evidence_content=evidence,
        manifest=manifest,
        generated_at=generated_at,
    )

    with ZipFile(io.BytesIO(pack)) as archive:
        assert archive.namelist() == [
            certificate_filename("shed"),
            evidence_filename("shed"),
            "manifest.json",
        ]
        assert archive.read(certificate_filename("shed")) == pdf
        assert archive.read(evidence_filename("shed")) == evidence
        stored_manifest = json.loads(archive.read("manifest.json"))
    assert stored_manifest["files"][0]["sha256"] == sha256(pdf).hexdigest()
    assert stored_manifest["files"][1]["sha256"] == sha256(evidence).hexdigest()
    assert stored_manifest["abcb_protocol"]["claim_status"] == "not_appraised"
    assert json.loads(evidence)["abcb_protocol"]["workflow_status"] == (
        "engineer_review_required"
    )


def test_genuine_not_checked_serviceability_blocks_export():
    snapshot = cast(StructuralSnapshot, _snapshot(serviceability_status="not_checked"))

    blockers = certificate_export_blockers(snapshot)

    assert any("Serviceability" in blocker and "not checked" in blocker for blocker in blockers)


def test_reasoned_not_applicable_serviceability_does_not_block_export():
    snapshot = cast(StructuralSnapshot, _snapshot())

    assert certificate_export_blockers(snapshot) == []


def test_unresolved_selected_connection_subcheck_blocks_export():
    snapshot = cast(StructuralSnapshot, _snapshot())
    snapshot.connection_checks[0].anchor_group = _item(status="unsupported")

    blockers = certificate_export_blockers(snapshot)

    assert any(
        "selected anchor group check is unsupported" in blocker
        for blocker in blockers
    )
