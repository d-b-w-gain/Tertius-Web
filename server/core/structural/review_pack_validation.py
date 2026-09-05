from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import io
import json
from typing import Any, Literal, Mapping
from zipfile import BadZipFile, ZipFile

from .certificate_report import MAX_REPORT_BYTES


ValidationProfile = Literal["technical", "abcb_claim"]


@dataclass(frozen=True)
class ReviewPackFinding:
    code: str
    message: str
    path: str


@dataclass(frozen=True)
class ReviewPackValidation:
    profile: ValidationProfile
    findings: tuple[ReviewPackFinding, ...]
    report_identity: str | None = None

    @property
    def ok(self) -> bool:
        return not self.findings

    def model_dump(self) -> dict[str, Any]:
        return {
            "schema_version": "tertius.structural.review-pack-validation.v1",
            "profile": self.profile,
            "status": "pass" if self.ok else "fail",
            "report_identity": self.report_identity,
            "findings": [asdict(finding) for finding in self.findings],
        }


def _mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def validate_structural_review_pack(
    content: bytes,
    *,
    profile: ValidationProfile = "technical",
) -> ReviewPackValidation:
    """Independently verify a Structural Workbench review-pack artifact.

    The technical profile checks artifact integrity and the fail-closed
    calculation evidence. The ``abcb_claim`` profile additionally requires the
    controlled-release declarations that may only be populated after external
    appraisal, Compliance Document approval and trained-user controls exist.
    """

    findings: list[ReviewPackFinding] = []

    def fail(code: str, message: str, path: str) -> None:
        findings.append(ReviewPackFinding(code=code, message=message, path=path))

    if len(content) > MAX_REPORT_BYTES:
        fail("PACK_TOO_LARGE", "Review pack exceeds the size limit.", "$")
        return ReviewPackValidation(profile, tuple(findings))

    try:
        with ZipFile(io.BytesIO(content)) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                fail("PACK_DUPLICATE_NAME", "Archive contains duplicate names.", "$")
            for info in infos:
                path_parts = info.filename.replace("\\", "/").split("/")
                if (
                    info.is_dir()
                    or len(path_parts) != 1
                    or any(part in {"", ".", ".."} for part in path_parts)
                ):
                    fail(
                        "PACK_UNSAFE_PATH",
                        f"Archive entry {info.filename!r} is not a flat safe filename.",
                        "$",
                    )
                if info.file_size > MAX_REPORT_BYTES:
                    fail(
                        "PACK_ENTRY_TOO_LARGE",
                        f"Archive entry {info.filename!r} exceeds the size limit.",
                        "$",
                    )
            if "manifest.json" not in names:
                fail("PACK_MANIFEST_MISSING", "manifest.json is missing.", "$")
                return ReviewPackValidation(profile, tuple(findings))
            try:
                manifest = json.loads(archive.read("manifest.json"))
            except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                fail("PACK_MANIFEST_INVALID", f"Manifest cannot be read: {exc}.", "$")
                return ReviewPackValidation(profile, tuple(findings))

            manifest_mapping = _mapping(manifest)
            if manifest_mapping is None:
                fail("PACK_MANIFEST_INVALID", "Manifest must be a JSON object.", "$")
                return ReviewPackValidation(profile, tuple(findings))
            files = manifest_mapping.get("files")
            if not isinstance(files, list) or len(files) != 2:
                fail(
                    "PACK_FILE_REGISTER_INVALID",
                    "Manifest must register exactly the PDF and evidence JSON.",
                    "$.files",
                )
                files = []

            evidence: Mapping[str, Any] | None = None
            pdf_content: bytes | None = None
            for index, entry_value in enumerate(files):
                entry = _mapping(entry_value)
                path = f"$.files[{index}]"
                if entry is None:
                    fail(
                        "PACK_FILE_REGISTER_INVALID",
                        "File entry must be an object.",
                        path,
                    )
                    continue
                name = entry.get("name")
                if not isinstance(name, str) or name not in names:
                    fail(
                        "PACK_FILE_MISSING",
                        f"Registered file {name!r} is missing.",
                        path,
                    )
                    continue
                artifact = archive.read(name)
                if entry.get("byte_size") != len(artifact):
                    fail(
                        "PACK_SIZE_MISMATCH",
                        f"Byte size does not match {name!r}.",
                        path,
                    )
                if entry.get("sha256") != sha256(artifact).hexdigest():
                    fail(
                        "PACK_HASH_MISMATCH", f"SHA-256 does not match {name!r}.", path
                    )
                content_type = entry.get("content_type")
                if content_type == "application/pdf":
                    pdf_content = artifact
                elif content_type == "application/json":
                    try:
                        parsed = json.loads(artifact)
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        fail(
                            "EVIDENCE_INVALID",
                            f"Evidence JSON cannot be read: {exc}.",
                            path,
                        )
                    else:
                        evidence = _mapping(parsed)

            registered_names = {
                entry.get("name")
                for value in files
                if (entry := _mapping(value)) is not None
                and isinstance(entry.get("name"), str)
            }
            if set(names) != registered_names | {"manifest.json"}:
                fail(
                    "PACK_UNREGISTERED_FILE",
                    "Archive contains a file not covered by the manifest.",
                    "$",
                )
    except BadZipFile as exc:
        fail("PACK_INVALID_ZIP", f"Review pack is not a valid ZIP: {exc}.", "$")
        return ReviewPackValidation(profile, tuple(findings))

    report_identity = (
        manifest_mapping.get("report_identity")
        if isinstance(manifest_mapping.get("report_identity"), str)
        else None
    )
    if manifest_mapping.get("document_status") != "controlled_unsigned_draft":
        fail(
            "DOCUMENT_STATUS_INVALID",
            "Manifest must identify a controlled unsigned draft.",
            "$.document_status",
        )
    if pdf_content is None or not pdf_content.startswith(b"%PDF-"):
        fail("PDF_INVALID", "Registered PDF is missing or invalid.", "$.files")
    elif b"CONTROLLED DRAFT" not in pdf_content:
        fail(
            "PDF_CONTROL_MARK_MISSING",
            "PDF does not contain the controlled-draft marking.",
            "$.files",
        )
    if evidence is None:
        fail(
            "EVIDENCE_INVALID", "Registered evidence must be a JSON object.", "$.files"
        )
        return ReviewPackValidation(profile, tuple(findings), report_identity)
    if evidence.get("report_identity") != report_identity:
        fail(
            "REPORT_IDENTITY_MISMATCH",
            "Manifest and evidence report identities differ.",
            "$.report_identity",
        )
    if evidence.get("analysis") != manifest_mapping.get("analysis"):
        fail(
            "ANALYSIS_IDENTITY_MISMATCH",
            "Manifest and evidence analysis identities differ.",
            "$.analysis",
        )

    snapshot = _mapping(evidence.get("snapshot"))
    if snapshot is None:
        fail("SNAPSHOT_MISSING", "Evidence snapshot is missing.", "$.snapshot")
    else:
        _validate_snapshot(snapshot, fail)

    if profile == "abcb_claim":
        _validate_abcb_claim(manifest_mapping, evidence, fail)
    return ReviewPackValidation(profile, tuple(findings), report_identity)


def _validate_snapshot(snapshot: Mapping[str, Any], fail) -> None:
    readiness = _mapping(snapshot.get("certification_readiness"))
    if readiness is None or readiness.get("ready_for_certificate_draft") is not True:
        fail(
            "TECHNICAL_READINESS_FAILED",
            "Snapshot is not ready for a controlled certificate draft.",
            "$.snapshot.certification_readiness",
        )
    else:
        coverage = _mapping(readiness.get("model_coverage"))
        if coverage is None or coverage.get("status") != "complete":
            fail(
                "MODEL_COVERAGE_INCOMPLETE",
                "Structural model coverage is not complete.",
                "$.snapshot.certification_readiness.model_coverage",
            )
        gates = readiness.get("gates")
        if not isinstance(gates, list) or not gates:
            fail(
                "READINESS_GATES_MISSING",
                "No readiness gates are recorded.",
                "$.snapshot.certification_readiness.gates",
            )
        else:
            for index, value in enumerate(gates):
                gate = _mapping(value)
                if gate is None or gate.get("status") != "pass":
                    fail(
                        "READINESS_GATE_OPEN",
                        "Every technical readiness gate must pass.",
                        f"$.snapshot.certification_readiness.gates[{index}]",
                    )

    required_families = (
        "cross_section_checks",
        "member_stability_checks",
        "connection_checks",
        "tension_member_checks",
        "bracing_load_path_traces",
    )
    for family in required_families:
        checks = snapshot.get(family)
        if not isinstance(checks, list) or not checks:
            fail(
                "CHECK_FAMILY_EMPTY", f"{family} has no checks.", f"$.snapshot.{family}"
            )
            continue
        for index, value in enumerate(checks):
            check = _mapping(value)
            if check is None or check.get("status") != "pass":
                fail(
                    "REQUIRED_CHECK_OPEN",
                    f"Every {family} result must pass.",
                    f"$.snapshot.{family}[{index}]",
                )
                continue
            if family == "connection_checks":
                for nested_name in ("anchor_group", "bolted_sheet_interface"):
                    nested = _mapping(check.get(nested_name))
                    if nested is not None and nested.get("status") != "pass":
                        fail(
                            "SELECTED_CONNECTION_SUBCHECK_OPEN",
                            f"Selected {nested_name} result must pass.",
                            f"$.snapshot.{family}[{index}].{nested_name}",
                        )

    serviceability = snapshot.get("serviceability_checks")
    if not isinstance(serviceability, list) or not serviceability:
        fail(
            "SERVICEABILITY_EMPTY",
            "No serviceability checks are recorded.",
            "$.snapshot.serviceability_checks",
        )
    else:
        for index, value in enumerate(serviceability):
            check = _mapping(value)
            path = f"$.snapshot.serviceability_checks[{index}]"
            if check is None:
                fail("SERVICEABILITY_OPEN", "Serviceability entry is invalid.", path)
            elif check.get("status") == "not_applicable":
                if (
                    not check.get("physical_member_id")
                    or not str(check.get("basis", "")).strip()
                ):
                    fail(
                        "SERVICEABILITY_NA_UNJUSTIFIED",
                        "A non-applicable check needs a member identity and reason.",
                        path,
                    )
            elif check.get("status") != "pass":
                fail(
                    "SERVICEABILITY_OPEN",
                    "Serviceability check must pass or be reasoned not applicable.",
                    path,
                )


def _validate_abcb_claim(
    manifest: Mapping[str, Any],
    evidence: Mapping[str, Any],
    fail,
) -> None:
    disclosure = _mapping(manifest.get("abcb_protocol"))
    if disclosure is None or disclosure != _mapping(evidence.get("abcb_protocol")):
        fail(
            "ABCB_DISCLOSURE_MISSING",
            "Matching ABCB protocol disclosure is required in manifest and evidence.",
            "$.abcb_protocol",
        )
        return
    expected = {
        "protocol_id": "ABCB Protocol for Structural Software",
        "protocol_edition": "2011.2",
        "claim_status": "independently_appraised",
        "workflow_status": "trained_user_signoff_enabled",
    }
    for key, expected_value in expected.items():
        if disclosure.get(key) != expected_value:
            fail(
                "ABCB_RELEASE_NOT_APPROVED",
                f"{key} must be {expected_value!r} for an ABCB claim.",
                f"$.abcb_protocol.{key}",
            )
    compliance_document = _mapping(disclosure.get("compliance_document"))

    def has_text(key: str) -> bool:
        if compliance_document is None:
            return False
        value = compliance_document.get(key)
        return isinstance(value, str) and bool(value.strip())

    if compliance_document is None or not all(
        has_text(key) for key in ("identifier", "version", "sha256")
    ):
        fail(
            "ABCB_COMPLIANCE_DOCUMENT_MISSING",
            "An approved Compliance Document identity and hash are required.",
            "$.abcb_protocol.compliance_document",
        )
