from __future__ import annotations

from pathlib import Path


DEFAULT_PROJECT_TEMPLATE_DIR = (
    Path(__file__).resolve().parent.parent / "workflows" / "intus" / "templates"
)
DEFAULT_PROJECT_FILENAMES = (
    "design.py",
    "lysaght_zc.py",
    "structural_connections.py",
    "lysaght_zc_v2.part00.json",
    "lysaght_zc_v2.part01.json",
    "lysaght_zc_v2.part02.json",
    "lysaght_zc_v2.part03.json",
    "lysaght_zc_v2.part04.json",
    "lysaght_zc_v2.part05.json",
    "lysaght_zc_v2.part06.json",
)


def default_project_files() -> dict[str, str]:
    """Return the complete, project-owned default mechanical source bundle."""

    return {
        filename: (
            DEFAULT_PROJECT_TEMPLATE_DIR
            / ("default_purlin.py" if filename == "design.py" else filename)
        ).read_text(encoding="utf-8")
        for filename in DEFAULT_PROJECT_FILENAMES
    }


def default_structural_configuration() -> dict[str, object]:
    """Return the Structural workbench state paired with the starter model."""

    return {
        "schema_version": "1.0",
        "title": "Lysaght Cee knee-frame draft analysis",
        "design_basis": {
            "framework_id": "SCI-P399",
            "framework_label": "SCI P399 verification process",
            "framework_reference": "Table 3.1 and Sections 4-12",
            "jurisdiction": "Australia",
            "analysis_method": "3D first-order elastic frame analysis",
            "standards": {
                "actions": "AS/NZS 1170 project inputs",
                "members": "AS/NZS 4600 verification required",
            },
        },
        "load_cases": [
            {"id": "dead", "label": "Permanent actions", "category": "dead"},
            {"id": "live", "label": "Demonstration imposed action", "category": "live"},
        ],
        "load_combinations": [
            {
                "id": "SLS-G+Q",
                "label": "Permanent plus imposed actions",
                "limit_state": "serviceability",
                "factors": {"dead": 1.0, "live": 1.0},
            },
            {
                "id": "SLS-G",
                "label": "Permanent actions",
                "limit_state": "serviceability",
                "factors": {"dead": 1.0},
            },
        ],
        "include_self_weight": True,
        "member_distributed_loads": [
            {
                "id": "P1-demo-imposed",
                "label": "P1 demonstration lateral action",
                "component_id": "P1",
                "case_id": "live",
                "start_distance_m": 0.0,
                "end_distance_m": None,
                "start_force_kN_m": {"x": 0.0, "y": 0.0, "z": -0.2},
                "end_force_kN_m": None,
                "provenance": (
                    "Draft workbench demonstration action; replace with project "
                    "actions before verification."
                ),
            }
        ],
        "member_criteria": [
            {
                "component_id": "P1",
                "deflection_limit_ratio": 250.0,
                "deflection_limit_mm": None,
                "deflection_limit_basis": "Draft project criterion L/250.",
            }
        ],
        "approval_policy": "draft_analysis",
    }
