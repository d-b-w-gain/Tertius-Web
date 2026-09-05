from __future__ import annotations

from datetime import UTC
from hashlib import sha256
from typing import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import StructuralAnalysisResult, StructuralReportExport, now_utc

from .certificate_report import (
    DOCUMENT_KIND,
    REPORT_SCHEMA_VERSION,
    build_manifest,
    build_review_pack,
    build_structural_certificate_pdf,
    build_structural_evidence_json,
    certificate_filename,
    report_identity,
)
from .contracts import StructuralSnapshot


class StructuralReportNotReady(ValueError):
    def __init__(self, blockers: Iterable[str]) -> None:
        self.blockers = list(dict.fromkeys(blockers))
        super().__init__("; ".join(self.blockers))


def certificate_export_blockers(snapshot: StructuralSnapshot) -> list[str]:
    blockers: list[str] = []
    readiness = snapshot.certification_readiness
    if readiness is None:
        blockers.append("Certification readiness is missing from the saved analysis.")
        return blockers
    if not readiness.ready_for_certificate_draft:
        blockers.extend(readiness.blocking_reasons)
        if not readiness.blocking_reasons:
            blockers.append("Australian technical gates are not all passing.")
    if readiness.model_coverage.status != "complete":
        blockers.append(readiness.model_coverage.summary)
    if readiness.issues:
        blockers.extend(issue.title for issue in readiness.issues)
    for gate in readiness.gates:
        if gate.status != "pass":
            blockers.append(f"{gate.label}: {gate.summary}")

    required_check_families = (
        ("Cross-section", snapshot.cross_section_checks),
        ("Member stability", snapshot.member_stability_checks),
        ("Connection", snapshot.connection_checks),
        ("Tension member", snapshot.tension_member_checks),
        ("Bracing path", snapshot.bracing_load_path_traces),
    )
    for label, checks in required_check_families:
        open_count = sum(getattr(check, "status", None) != "pass" for check in checks)
        if open_count:
            blockers.append(f"{label}: {open_count} required check(s) are not passing.")

    for connection in snapshot.connection_checks:
        for label, supporting_check in (
            ("anchor group", getattr(connection, "anchor_group", None)),
            (
                "bolted-sheet interface",
                getattr(connection, "bolted_sheet_interface", None),
            ),
        ):
            if supporting_check is not None and supporting_check.status != "pass":
                blockers.append(
                    f"Connection {getattr(connection, 'connection_id', '<unknown>')}: "
                    f"selected {label} "
                    f"check is {supporting_check.status}."
                )

    if not snapshot.serviceability_checks:
        blockers.append("Serviceability: no member checks were recorded.")
    for check in snapshot.serviceability_checks:
        if check.status == "pass":
            continue
        if check.status == "not_applicable":
            if not check.physical_member_id or not check.basis.strip():
                blockers.append(
                    "Serviceability: a non-applicable check lacks physical identity or reason."
                )
            continue
        blockers.append(
            f"Serviceability: {check.physical_member_id or check.member_id} is "
            f"{check.status.replace('_', ' ')}."
        )
    return list(dict.fromkeys(blockers))


def analysis_metadata(stored: StructuralAnalysisResult) -> dict[str, object]:
    return {
        "key_digest": stored.key_digest,
        "design_digest": stored.design_digest,
        "configuration_digest": stored.configuration_digest,
        "site_digest": stored.site_digest,
        "engine_version": stored.engine_version,
        "snapshot_schema_version": stored.snapshot_schema_version,
        "combination_id": stored.combination_id,
        "calculation_duration_seconds": stored.calculation_duration_seconds,
        "calculated_at": stored.created_at.astimezone(UTC).isoformat().replace(
            "+00:00", "Z"
        ),
    }


def get_or_create_structural_report_export(
    db: Session,
    *,
    stored: StructuralAnalysisResult,
    project_name: str,
    requested_by: UUID,
) -> tuple[StructuralReportExport, bool]:
    existing = db.scalar(
        select(StructuralReportExport).where(
            StructuralReportExport.tenant_id == stored.tenant_id,
            StructuralReportExport.project_id == stored.project_id,
            StructuralReportExport.structural_analysis_result_id == stored.id,
            StructuralReportExport.document_kind == DOCUMENT_KIND,
            StructuralReportExport.report_schema_version == REPORT_SCHEMA_VERSION,
        )
    )
    if existing is not None:
        return existing, True

    snapshot = StructuralSnapshot.model_validate(stored.snapshot)
    blockers = certificate_export_blockers(snapshot)
    if blockers:
        raise StructuralReportNotReady(blockers)

    generated_at = now_utc()
    report_id = report_identity(analysis_key_digest=stored.key_digest)
    metadata = analysis_metadata(stored)
    evidence_content = build_structural_evidence_json(
        project_name=project_name,
        snapshot=snapshot,
        analysis_metadata=metadata,
        report_id=report_id,
        generated_at=generated_at,
    )
    evidence_digest = sha256(evidence_content).hexdigest()
    pdf_content = build_structural_certificate_pdf(
        project_name=project_name,
        snapshot=snapshot,
        analysis_metadata=metadata,
        report_id=report_id,
        generated_at=generated_at,
        evidence_sha256=evidence_digest,
    )
    manifest = build_manifest(
        project_name=project_name,
        report_id=report_id,
        analysis_metadata=metadata,
        generated_at=generated_at,
        pdf_content=pdf_content,
        evidence_content=evidence_content,
    )
    report = StructuralReportExport(
        tenant_id=stored.tenant_id,
        project_id=stored.project_id,
        structural_analysis_result_id=stored.id,
        requested_by=requested_by,
        document_kind=DOCUMENT_KIND,
        report_schema_version=REPORT_SCHEMA_VERSION,
        report_identity_digest=report_id,
        filename=certificate_filename(project_name),
        pdf_content=pdf_content,
        pdf_sha256=sha256(pdf_content).hexdigest(),
        pdf_byte_size=len(pdf_content),
        evidence_json_content=evidence_content,
        evidence_sha256=evidence_digest,
        evidence_byte_size=len(evidence_content),
        manifest=manifest,
        created_at=generated_at,
    )
    db.add(report)
    db.flush()
    return report, False


def report_review_pack(report: StructuralReportExport, *, project_name: str) -> bytes:
    return build_review_pack(
        project_name=project_name,
        pdf_content=report.pdf_content,
        evidence_content=report.evidence_json_content,
        manifest=report.manifest,
        generated_at=report.created_at,
    )
