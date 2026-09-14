from __future__ import annotations

import json
from pathlib import Path


FAT_REGISTER = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "structural"
    / "abcb-protocol-fat.json"
)


def test_abcb_fat_register_covers_every_protocol_control_area() -> None:
    register = json.loads(FAT_REGISTER.read_text(encoding="utf-8"))
    cases = register["cases"]

    assert register["protocol"]["edition"] == "2011.2"
    assert {case["protocol_area"] for case in cases} == {
        "scope",
        "inputs",
        "outputs",
        "validation",
        "quality_assurance",
        "software_author",
        "software_user",
        "compliance_document",
        "independent_appraisal",
        "maintenance",
    }
    ids = [case["id"] for case in cases]
    assert len(ids) == len(set(ids))
    assert ids == [f"FAT-{index:03d}" for index in range(1, len(cases) + 1)]


def test_abcb_fat_release_gates_have_objective_evidence_and_honest_status() -> None:
    register = json.loads(FAT_REGISTER.read_text(encoding="utf-8"))
    evidence_classes = set(register["evidence_classes"])

    for case in register["cases"]:
        assert case["release_gate"] is True
        assert case["method"] in evidence_classes
        assert case["evidence"]
        assert all(isinstance(path, str) and path.strip() for path in case["evidence"])
        assert case["automation"] in {
            "implemented",
            "partial",
            "planned",
            "not_applicable",
        }
        assert case["current_implementation"] in {"pass", "partial", "gap"}
        if case["automation"] == "implemented":
            assert case["current_implementation"] == "pass"
