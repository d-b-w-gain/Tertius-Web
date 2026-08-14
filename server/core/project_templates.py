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
        "title": "Compiled mechanical structure draft analysis",
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
        ],
        "load_combinations": [
            {
                "id": "SLS-G",
                "label": "Permanent actions",
                "limit_state": "serviceability",
                "factors": {"dead": 1.0},
            },
            {
                "id": "ULS-1.2G",
                "label": "ULS permanent actions",
                "limit_state": "ultimate",
                "factors": {"dead": 1.2},
            },
        ],
        "include_self_weight": True,
        "member_distributed_loads": [],
        "member_criteria": [
            {
                "component_id": None,
                "deflection_limit_ratio": 250.0,
                "deflection_limit_mm": None,
                "deflection_limit_basis": (
                    "Draft all-members serviceability criterion L/250; replace "
                    "with project-specific criteria before approval."
                ),
            }
        ],
        "cross_section_verification": {
            "pack_id": "as_nzs_4600_2018_ewm",
            "combination_ids": ["ULS-1.2G"],
            "component_ids": [],
            "off_axis_tolerance": 1e-6,
        },
        "member_stability_verification": {
            "pack_id": "as_nzs_4600_2018_ewm_member",
            "combination_ids": ["ULS-1.2G"],
            "segments": [],
            "off_axis_tolerance": 1e-6,
        },
        "approval_policy": "draft_analysis",
    }
