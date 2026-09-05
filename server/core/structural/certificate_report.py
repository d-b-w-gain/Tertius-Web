from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from hashlib import sha256
import io
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from fpdf import FPDF

from .analysis_cache import canonical_digest
from .contracts import StructuralSnapshot


REPORT_SCHEMA_VERSION = "3"
EVIDENCE_SCHEMA_VERSION = "tertius.structural.evidence.v2"
MANIFEST_SCHEMA_VERSION = "tertius.structural.report-manifest.v2"
DOCUMENT_KIND = "structural_certificate_draft"
MAX_REPORT_BYTES = 32 * 1024 * 1024

ABCB_PROTOCOL_DISCLOSURE = {
    "schema_version": "tertius.structural.abcb-protocol-disclosure.v1",
    "protocol_id": "ABCB Protocol for Structural Software",
    "protocol_edition": "2011.2",
    # This must remain fail-closed until an independent structural appraisal,
    # Compliance Document and controlled user-training scheme are in force.
    "claim_status": "not_appraised",
    "workflow_status": "engineer_review_required",
}


def _text(value: object, *, limit: int = 4_000) -> str:
    replacements = {
        "\u00b7": " - ",
        "\u00d7": " x ",
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2026": "...",
        "\u2264": "<=",
        "\u2265": ">=",
        "\u03b4": "delta",
        "\u03b3": "gamma",
        "\u03b1": "alpha",
    }
    clean = str(value).replace("\r", " ").replace("\n", " ").strip()
    clean = re.sub(r"\s+", " ", clean)
    for source, target in replacements.items():
        clean = clean.replace(source, target)
    if len(clean) > limit:
        clean = clean[: max(0, limit - 3)].rstrip() + "..."
    return clean.encode("latin-1", errors="replace").decode("latin-1")


def safe_filename_part(value: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip()).strip("-")
    return (clean[:80] or "tertius").lower()


def certificate_filename(project_name: str) -> str:
    return f"{safe_filename_part(project_name)}-structural-certificate-draft.pdf"


def review_pack_filename(project_name: str) -> str:
    return f"{safe_filename_part(project_name)}-structural-review-pack.zip"


def evidence_filename(project_name: str) -> str:
    return f"{safe_filename_part(project_name)}-structural-evidence.json"


def report_identity(*, analysis_key_digest: str) -> str:
    return canonical_digest(
        {
            "analysis_key_digest": analysis_key_digest,
            "document_kind": DOCUMENT_KIND,
            "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
            "report_schema_version": REPORT_SCHEMA_VERSION,
        }
    )


def build_structural_evidence_json(
    *,
    project_name: str,
    snapshot: StructuralSnapshot,
    analysis_metadata: Mapping[str, object],
    report_id: str,
    generated_at: datetime,
) -> bytes:
    payload = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "document_status": "controlled_unsigned_draft",
        "abcb_protocol": ABCB_PROTOCOL_DISCLOSURE,
        "report_identity": report_id,
        "generated_at": generated_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "project_name": project_name,
        "analysis": dict(analysis_metadata),
        "snapshot": snapshot.model_dump(mode="json"),
    }
    content = json.dumps(
        payload,
        sort_keys=True,
        indent=2,
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    if len(content) > MAX_REPORT_BYTES:
        raise ValueError("structural evidence JSON exceeds the report size limit")
    return content


class StructuralCertificatePDF(FPDF):
    def __init__(self, *, project_name: str, report_id: str) -> None:
        super().__init__(orientation="P", unit="mm", format="A4")
        self.project_name = _text(project_name, limit=120)
        self.report_id = report_id
        self.set_auto_page_break(auto=True, margin=18)
        self.set_compression(False)
        self.alias_nb_pages()
        self.set_margins(15, 20, 15)

    def header(self) -> None:
        self.set_fill_color(127, 29, 29)
        self.rect(0, 0, 210, 11, style="F")
        self.set_xy(15, 3)
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(255, 255, 255)
        self.cell(0, 4, "CONTROLLED DRAFT - ENGINEER REVIEW AND SIGNATURE REQUIRED")
        self.set_xy(15, 12)
        self.set_font("Helvetica", "", 6.5)
        self.set_text_color(100, 116, 139)
        self.cell(120, 4, self.project_name)
        self.cell(0, 4, f"Report {self.report_id[:16]}", align="R")
        self.ln(5)

    def footer(self) -> None:
        self.set_y(-13)
        self.set_draw_color(203, 213, 225)
        self.line(15, self.get_y(), 195, self.get_y())
        self.set_y(-10)
        self.set_font("Helvetica", "", 6.5)
        self.set_text_color(100, 116, 139)
        self.cell(130, 4, "Unsigned Tertius calculation output - not an issued certificate")
        self.cell(0, 4, f"Page {self.page_no()}/{{nb}}", align="R")


def _ensure_space(pdf: StructuralCertificatePDF, height: float) -> None:
    if pdf.get_y() + height > 276:
        pdf.add_page()


def _section(pdf: StructuralCertificatePDF, title: str, subtitle: str | None = None) -> None:
    _ensure_space(pdf, 17 if subtitle else 11)
    pdf.set_fill_color(8, 145, 178)
    pdf.rect(15, pdf.get_y(), 2, 7, style="F")
    pdf.set_x(20)
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(0, 7, _text(title, limit=180))
    pdf.ln(8)
    if subtitle:
        pdf.set_x(20)
        pdf.set_font("Helvetica", "", 7.5)
        pdf.set_text_color(71, 85, 105)
        pdf.multi_cell(175, 4, _text(subtitle, limit=1_000))
        pdf.ln(1)


def _paragraph(
    pdf: StructuralCertificatePDF,
    value: object,
    *,
    bold: bool = False,
    color: tuple[int, int, int] = (51, 65, 85),
    size: float = 8.2,
    line_height: float = 4.4,
) -> None:
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", "B" if bold else "", size)
    pdf.set_text_color(*color)
    pdf.multi_cell(0, line_height, _text(value))
    pdf.ln(1)


def _status_box(pdf: StructuralCertificatePDF, label: str, value: str, *, passed: bool) -> None:
    _ensure_space(pdf, 17)
    y = pdf.get_y()
    if passed:
        fill, border, text_color = (236, 253, 245), (16, 185, 129), (6, 95, 70)
    else:
        fill, border, text_color = (254, 242, 242), (239, 68, 68), (153, 27, 27)
    pdf.set_fill_color(*fill)
    pdf.set_draw_color(*border)
    pdf.rect(15, y, 180, 14, style="DF")
    pdf.set_xy(19, y + 2)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(*text_color)
    pdf.cell(70, 4, _text(label, limit=80))
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(101, 4, _text(value, limit=120), align="R")
    pdf.set_y(y + 17)


def _key_values(
    pdf: StructuralCertificatePDF,
    rows: Iterable[tuple[object, object]],
) -> None:
    for label, value in rows:
        _ensure_space(pdf, 10)
        y = pdf.get_y()
        pdf.set_fill_color(248, 250, 252)
        pdf.rect(15, y, 180, 8, style="F")
        pdf.set_xy(18, y + 1.4)
        pdf.set_font("Helvetica", "B", 7.2)
        pdf.set_text_color(71, 85, 105)
        pdf.cell(52, 5, _text(label, limit=80))
        pdf.set_font("Helvetica", "", 7.2)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(122, 5, _text(value, limit=220), align="R")
        pdf.set_y(y + 9)


def _status_counts(values: Sequence[object]) -> Counter[str]:
    return Counter(str(value) for value in values)


def _check_rows(snapshot: StructuralSnapshot) -> list[tuple[str, Sequence[object]]]:
    return [
        ("Cross-sections", snapshot.cross_section_checks),
        ("Member stability", snapshot.member_stability_checks),
        ("Connections", snapshot.connection_checks),
        ("Tension members", snapshot.tension_member_checks),
        ("Bracing paths", snapshot.bracing_load_path_traces),
        ("Serviceability", snapshot.serviceability_checks),
    ]


def _status_table(pdf: StructuralCertificatePDF, snapshot: StructuralSnapshot) -> None:
    _key_values(
        pdf,
        (
            (
                label,
                ", ".join(
                    part
                    for part in (
                        f"{counts['pass']} pass",
                        f"{counts['fail']} fail" if counts["fail"] else "",
                        f"{counts['not_applicable']} not applicable"
                        if counts["not_applicable"]
                        else "",
                        f"{counts['not_checked']} not checked"
                        if counts["not_checked"]
                        else "",
                        f"{counts['unsupported']} unsupported"
                        if counts["unsupported"]
                        else "",
                    )
                    if part
                ),
            )
            for label, checks in _check_rows(snapshot)
            for counts in [_status_counts([getattr(check, "status", "") for check in checks])]
        ),
    )


def _float(value: object, digits: int = 3) -> str:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return f"{float(value):.{digits}f}"
    return "not recorded"


def _governing_utilisation(check: object) -> float | None:
    for attribute in (
        "governing_utilisation",
        "utilisation",
        "interaction_utilisation",
        "tension_utilisation",
        "force_utilisation",
    ):
        value = getattr(check, attribute, None)
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            return float(value)
    return None


def _check_identity(check: object) -> str:
    for attribute in (
        "physical_member_id",
        "member_id",
        "connection_id",
        "component_id",
        "segment_id",
        "id",
    ):
        value = getattr(check, attribute, None)
        if value:
            return _text(value, limit=120)
    return "recorded check"


def _governing_results(
    pdf: StructuralCertificatePDF,
    snapshot: StructuralSnapshot,
) -> None:
    for family, checks in _check_rows(snapshot):
        applicable = [
            check
            for check in checks
            if getattr(check, "status", None) != "not_applicable"
        ]
        ranked = sorted(
            applicable,
            key=lambda check: _governing_utilisation(check) or -1.0,
            reverse=True,
        )[:5]
        _ensure_space(pdf, 12 + 7 * max(1, len(ranked)))
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(0, 5, _text(family, limit=120))
        pdf.ln(6)
        for check in ranked:
            utilisation = _governing_utilisation(check)
            status = str(getattr(check, "status", "recorded")).replace("_", " ")
            value = (
                f"{utilisation:.3f} utilisation"
                if utilisation is not None
                else "no numerical utilisation"
            )
            _paragraph(
                pdf,
                f"- {_check_identity(check)}: {status}; {value}",
                size=6.8,
                line_height=3.5,
            )
        if not ranked:
            _paragraph(pdf, "- No applicable checks recorded.", size=6.8, line_height=3.5)


def build_structural_certificate_pdf(
    *,
    project_name: str,
    snapshot: StructuralSnapshot,
    analysis_metadata: Mapping[str, object],
    report_id: str,
    generated_at: datetime,
    evidence_sha256: str,
) -> bytes:
    readiness = snapshot.certification_readiness
    if readiness is None or not readiness.ready_for_certificate_draft:
        raise ValueError("structural result is not ready for a certificate draft")

    pdf = StructuralCertificatePDF(project_name=project_name, report_id=report_id)
    # Keep the document metadata inside the same immutable identity as the body.
    # fpdf2 otherwise inserts the current wall-clock time, making a later rebuild
    # of identical evidence produce different bytes and a different SHA-256.
    pdf.set_creation_date(generated_at.astimezone(UTC))
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(8, 145, 178)
    pdf.cell(0, 5, "TERTIUS / STRUCTURAL WORKBENCH")
    pdf.ln(9)
    pdf.set_font("Helvetica", "B", 23)
    pdf.set_text_color(15, 23, 42)
    pdf.multi_cell(0, 10, "Structural design certificate - controlled draft")
    pdf.ln(2)
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(71, 85, 105)
    pdf.cell(0, 7, _text(project_name, limit=120))
    pdf.ln(10)

    _status_box(
        pdf,
        "Australian technical gates",
        "PASS - ENGINEER REVIEW REQUIRED",
        passed=True,
    )
    _paragraph(
        pdf,
        "This document is an unsigned calculation-based draft prepared for review by "
        "an appropriately qualified engineer. It is not an issued structural "
        "certificate and does not itself establish authority acceptance, construction "
        "approval, or permission to order.",
        bold=True,
        color=(127, 29, 29),
        size=9,
        line_height=5,
    )

    generated_text = generated_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    source = snapshot.source
    _section(pdf, "Document control")
    _key_values(
        pdf,
        (
            ("Document status", "Controlled unsigned draft"),
            ("Report identity", report_id),
            ("Generated", generated_text),
            ("Analysis calculated", analysis_metadata.get("calculated_at", "not recorded")),
            ("Structural engine", analysis_metadata.get("engine_version", "not recorded")),
            ("Analysis key", analysis_metadata.get("key_digest", "not recorded")),
            ("Design digest", source.design_hash or "not recorded"),
            ("Configuration revision", source.analysis_configuration_revision or "not recorded"),
            ("Configuration digest", source.analysis_configuration_digest or "not recorded"),
            ("Evidence JSON SHA-256", evidence_sha256),
        ),
    )

    basis = snapshot.design_basis
    pdf.add_page()
    _section(pdf, "Proposed engineering statement")
    _paragraph(
        pdf,
        "The saved Tertius structural analysis identified by this report records all "
        "technical readiness gates as passing for the stated design, Site basis, "
        "materials, products, connections and installation assumptions. The reviewing "
        "engineer must verify the model scope, evidence applicability and constructed "
        "work before adopting or signing this statement.",
    )
    if basis is not None:
        _key_values(
            pdf,
            (
                ("Framework", f"{basis.framework_id} - {basis.framework_reference}"),
                ("Jurisdiction", basis.jurisdiction),
                ("Building classification", basis.building_classification or "not recorded"),
                ("Importance level", basis.importance_level or "not recorded"),
                ("Design life", f"{basis.design_life_years} years"),
                ("Analysis method", basis.analysis_method),
                ("Compliance pathway", basis.compliance_pathway),
            ),
        )
        _section(pdf, "Standards register")
        _key_values(pdf, sorted(basis.standards.items()))

    _section(pdf, "Scope and conditions")
    _paragraph(
        pdf,
        "Scope is limited to the compiled structural model, actions, combinations, "
        "member products, connections, supports and Site evidence identified by the "
        "digests in this document. Fabrication and construction must match those "
        "details. Substitution, relocation, omitted fasteners, changed openings, changed "
        "Site exposure, damage or corrosion requires engineering review and may require "
        "a new analysis.",
    )
    _paragraph(
        pdf,
        "The certificate draft does not verify workmanship or as-built dimensions. "
        "Foundation substrate and installation conditions must match the recorded "
        "connection evidence and product requirements.",
    )

    pdf.add_page()
    _section(pdf, "Technical gate decision")
    for gate in readiness.gates:
        _status_box(
            pdf,
            f"{gate.order}. {gate.label} - {gate.primary_reference}",
            gate.status.replace("_", " ").upper(),
            passed=gate.status == "pass",
        )
        _paragraph(pdf, gate.summary, size=7.2, line_height=3.8)

    _section(pdf, "Verification totals")
    _status_table(pdf, snapshot)

    pdf.add_page()
    _section(
        pdf,
        "Governing verification results",
        "Up to five governing applicable checks are shown for each family. The complete register is retained in the evidence JSON.",
    )
    _governing_results(pdf, snapshot)

    _section(pdf, "Model and solver")
    _key_values(
        pdf,
        (
            ("Members / nodes / reactions", f"{len(snapshot.members)} / {len(snapshot.nodes)} / {len(snapshot.reactions)}"),
            ("Solver", f"{snapshot.solver.name} {snapshot.solver.version}"),
            ("Analysis", snapshot.solver.analysis),
            ("Governing combination", snapshot.solver.combination_id),
            ("Global equilibrium", snapshot.equilibrium.status.upper()),
            ("Force residual", f"X {_float(snapshot.equilibrium.force_residual_kN.x, 6)}, Y {_float(snapshot.equilibrium.force_residual_kN.y, 6)}, Z {_float(snapshot.equilibrium.force_residual_kN.z, 6)} kN"),
            ("Calculation duration", f"{_float(analysis_metadata.get('calculation_duration_seconds'), 3)} seconds"),
        ),
    )

    pdf.add_page()
    _section(pdf, "Actions and governing response")
    _key_values(
        pdf,
        (
            ("Member mass", f"{_float(snapshot.load_summary.member_mass_kg)} kg"),
            ("Self-weight", f"{_float(snapshot.load_summary.self_weight_kN)} kN"),
            ("Additional dead load", f"{_float(snapshot.load_summary.additional_dead_load_kN)} kN"),
            ("Imposed load", f"{_float(snapshot.load_summary.imposed_load_kN)} kN"),
            ("Wind load", f"{_float(snapshot.load_summary.wind_load_kN)} kN"),
            ("Available combinations", len(snapshot.load_combinations)),
            ("Unavailable combinations", len(snapshot.unavailable_load_combinations)),
        ),
    )
    if snapshot.wind_action_bases:
        _section(pdf, "Site and wind basis")
        for wind in snapshot.wind_action_bases:
            _ensure_space(pdf, 25)
            _paragraph(
                pdf,
                f"{wind.id}: {wind.site_address}; region {wind.region}; "
                f"{wind.structural_action_direction or 'direction not recorded'}; "
                f"Vsite {_float(wind.site_wind_speed_m_s)} m/s; "
                f"qz {_float(wind.q_z_kPa, 6)} kPa; {wind.standard}.",
                size=7.2,
                line_height=3.8,
            )

    if snapshot.stability is not None:
        _section(pdf, "Global stability")
        _key_values(
            pdf,
            (
                ("Combination", snapshot.stability.combination_id),
                ("Status", "PASS" if snapshot.stability.converged else "FAIL"),
                ("Governing moment amplification", _float(snapshot.stability.governing_moment_amplification, 6)),
                ("Governing displacement amplification", _float(snapshot.stability.governing_displacement_amplification, 6)),
                ("Second-order analysis required", "yes" if snapshot.stability.second_order_required else "no"),
                ("Directions assessed", len(snapshot.stability.direction_results)),
            ),
        )

    pdf.add_page()
    _section(
        pdf,
        "Applicability, exclusions and limitations",
        "A non-applicable test is a reasoned method decision, not an unverified item.",
    )
    not_applicable = [
        check for check in snapshot.serviceability_checks if check.status == "not_applicable"
    ]
    unchecked = [
        check for check in snapshot.serviceability_checks if check.status == "not_checked"
    ]
    _status_box(
        pdf,
        "Serviceability applicability",
        f"{len(not_applicable)} NOT APPLICABLE / {len(unchecked)} NOT CHECKED",
        passed=not unchecked,
    )
    _paragraph(
        pdf,
        "Transverse span-deflection criteria are not applicable to the axial-only "
        "bracing members listed below. Axial deformation and resistance, end "
        "connections, and bracing load path are verified in the member, connection and "
        "bracing stages.",
    )
    for check in not_applicable:
        _ensure_space(pdf, 15)
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", "B", 7.2)
        pdf.set_text_color(15, 23, 42)
        pdf.multi_cell(0, 4, _text(check.physical_member_id or check.member_id, limit=180))
        _paragraph(pdf, check.basis, size=6.8, line_height=3.5, color=(71, 85, 105))
    if not not_applicable:
        _paragraph(pdf, "No non-applicable serviceability checks are recorded.")

    _section(pdf, "Recorded assumptions and review conditions")
    assumptions: list[str] = []
    for sheet in snapshot.calculation_sheets:
        assumptions.extend(sheet.assumptions)
    for assumption in dict.fromkeys(assumptions):
        _paragraph(pdf, f"- {assumption}", size=6.8, line_height=3.6)

    pdf.add_page()
    _section(pdf, "Calculation and evidence register")
    for sheet in sorted(snapshot.calculation_sheets, key=lambda value: (value.stage_id, value.id)):
        _ensure_space(pdf, 18)
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", "B", 7.4)
        pdf.set_text_color(15, 23, 42)
        pdf.multi_cell(0, 4, _text(f"{sheet.id} - {sheet.title}", limit=260))
        _paragraph(
            pdf,
            f"Stage {sheet.stage_id}; {sheet.status.replace('_', ' ')}; "
            f"{sheet.primary_reference}; inputs {len(sheet.inputs)}; equations "
            f"{len(sheet.equations)}; outputs {len(sheet.outputs)}. Full detail is in "
            f"{evidence_filename(project_name)} (SHA-256 {evidence_sha256}).",
            size=6.8,
            line_height=3.5,
            color=(71, 85, 105),
        )

    pdf.add_page()
    _section(pdf, "Reviewing engineer declaration")
    _paragraph(
        pdf,
        "To be completed only after the reviewing engineer has checked the design, "
        "calculation report, evidence pack, stated assumptions and proposed construction "
        "details. Tertius does not complete or apply this declaration.",
        bold=True,
        color=(127, 29, 29),
    )
    for label in (
        "Engineer name",
        "Qualifications",
        "Registration number and jurisdiction",
        "Organisation",
        "Engineer-added conditions",
        "Signature",
        "Date",
    ):
        _ensure_space(pdf, 23 if label == "Engineer-added conditions" else 16)
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.set_text_color(71, 85, 105)
        pdf.cell(0, 5, label)
        pdf.ln(9 if label != "Engineer-added conditions" else 16)
        pdf.set_draw_color(148, 163, 184)
        pdf.line(15, pdf.get_y(), 195, pdf.get_y())
        pdf.ln(4)

    content = bytes(pdf.output())
    if len(content) > MAX_REPORT_BYTES:
        raise ValueError("structural certificate draft exceeds the report size limit")
    return content


def build_manifest(
    *,
    project_name: str,
    report_id: str,
    analysis_metadata: Mapping[str, object],
    generated_at: datetime,
    pdf_content: bytes,
    evidence_content: bytes,
) -> dict[str, Any]:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "document_status": "controlled_unsigned_draft",
        "abcb_protocol": ABCB_PROTOCOL_DISCLOSURE,
        "report_identity": report_id,
        "generated_at": generated_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "analysis": dict(analysis_metadata),
        "files": [
            {
                "name": certificate_filename(project_name),
                "content_type": "application/pdf",
                "byte_size": len(pdf_content),
                "sha256": sha256(pdf_content).hexdigest(),
            },
            {
                "name": evidence_filename(project_name),
                "content_type": "application/json",
                "byte_size": len(evidence_content),
                "sha256": sha256(evidence_content).hexdigest(),
            },
        ],
    }


def build_review_pack(
    *,
    project_name: str,
    pdf_content: bytes,
    evidence_content: bytes,
    manifest: Mapping[str, object],
    generated_at: datetime,
) -> bytes:
    buffer = io.BytesIO()
    zip_time = generated_at.astimezone(UTC)
    date_time = (
        max(1980, zip_time.year),
        zip_time.month,
        zip_time.day,
        zip_time.hour,
        zip_time.minute,
        zip_time.second - (zip_time.second % 2),
    )

    def write(name: str, content: bytes) -> None:
        info = ZipInfo(filename=name, date_time=date_time)
        info.compress_type = ZIP_DEFLATED
        info.external_attr = 0o600 << 16
        archive.writestr(info, content)

    with ZipFile(buffer, mode="w") as archive:
        write(certificate_filename(project_name), pdf_content)
        write(evidence_filename(project_name), evidence_content)
        manifest_content = json.dumps(
            manifest,
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        write("manifest.json", manifest_content)
    content = buffer.getvalue()
    if len(content) > MAX_REPORT_BYTES:
        raise ValueError("structural review pack exceeds the report size limit")
    return content


def source_file() -> Path:
    """Expose the report implementation path for deterministic source inventories."""

    return Path(__file__)
