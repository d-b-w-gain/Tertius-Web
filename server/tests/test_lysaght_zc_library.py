from __future__ import annotations

import importlib.util
from pathlib import Path

import build123d as bd
import pytest

from tertius import all_workbench_projections
from tertius.runner import execute_design
from tertius.session import compile_session
from core.project_templates import DEFAULT_PROJECT_TEMPLATE_DIR, default_project_files


_library_spec = importlib.util.spec_from_file_location(
    "test_project_lysaght_zc",
    DEFAULT_PROJECT_TEMPLATE_DIR / "lysaght_zc.py",
)
assert _library_spec is not None and _library_spec.loader is not None
_library = importlib.util.module_from_spec(_library_spec)
_library_spec.loader.exec_module(_library)

CONNECTION_FAMILY = _library.CONNECTION_FAMILY
catalogue_part_numbers = _library.catalogue_part_numbers
cee_member = _library.cee_member
lysaght_zc_product = _library.lysaght_zc_product
zed_member = _library.zed_member


def test_full_catalogue_resolves_exact_primary_rows_and_immutable_facets() -> None:
    assert len(catalogue_part_numbers(family="C")) == 16
    assert len(catalogue_part_numbers(family="Z")) == 16

    product = lysaght_zc_product("C10019")
    assert product.key == "lysaght-zc-v2:C10019"
    assert product.catalogue_revision == "2.0"
    assert product.catalogue_row["key"] == "C10019 (100x1.9)"
    assert product.geometry == {
        "profile": "C",
        "member_axis": "local_z",
        "depth_mm": 102.0,
        "thickness_mm": 1.9,
        "lip_mm": 14.5,
        "inside_radius_mm": 2.85,
        "flange_width_mm": 51.0,
    }
    assert product.procurement is not None
    assert product.procurement.part_number == "C10019"
    assert product.structural is not None
    assert product.structural.section["area_m2"] == pytest.approx(409e-6)
    assert product.structural.section["iy_m4"] == pytest.approx(142000e-12)
    assert product.structural.section["iz_m4"] == pytest.approx(673000e-12)
    with pytest.raises(TypeError):
        product.catalogue_row["t_mm"] = 2.4  # type: ignore[index]

    # These aliases are incorrect in the supplied source catalogue. Exact row
    # keys must win so an alias can never substitute another physical section.
    assert lysaght_zc_product("Z10019").catalogue_row["key"] == "Z10019 (100x1.9)"
    assert lysaght_zc_product("Z35030").catalogue_row["key"] == "Z35030 (350x3.0)"


def test_cee_member_call_registers_geometry_and_every_workbench_facet() -> None:
    with compile_session() as session:
        member = cee_member(
            "C10019",
            start_mm=(10, 20, 30),
            end_mm=(10, 20, 1030),
            ordered_length_mm=1200,
            mark="C1",
            role="portal column",
        )
        model = bd.Compound(  # type: ignore[call-overload]
            children=[member], label="single managed column"
        )
        graph = session.finalize(model)
        projections = all_workbench_projections(graph, model=model)

    assert member.ports.start.point_mm == (10.0, 20.0, 30.0)
    assert member.ports.end.point_mm == (10.0, 20.0, 1030.0)
    assert member.ports.start.compatible_families == (CONNECTION_FAMILY,)
    assert member.ports.start.x_direction == (1.0, 0.0, 0.0)
    assert member.ports.start.engagement_length_mm == 75.0
    assert graph["components"] == [
        {
            "id": "C1",
            "mark": "C1",
            "role": "portal column",
            "product_key": "lysaght-zc-v2:C10019",
            "product_definition_digest": lysaght_zc_product("C10019").definition_digest,
            "fabrication": {
                "cut_length_mm": 1000.0,
                "ordered_length_mm": 1200.0,
                "end_treatment": "square_cut",
                "rotation_deg": 0.0,
            },
            "ports": [
                {
                    "name": "end",
                    "point_mm": [10.0, 20.0, 1030.0],
                    "direction": [0.0, 0.0, 1.0],
                    "x_direction": [1.0, 0.0, 0.0],
                    "compatible_families": [CONNECTION_FAMILY],
                    "engagement_length_mm": 75.0,
                },
                {
                    "name": "start",
                    "point_mm": [10.0, 20.0, 30.0],
                    "direction": [-0.0, -0.0, -1.0],
                    "x_direction": [1.0, 0.0, 0.0],
                    "compatible_families": [CONNECTION_FAMILY],
                    "engagement_length_mm": 75.0,
                },
            ],
            "visual": {"label": "C1 · C10019 · L=1000mm"},
        }
    ]
    procurement = projections["procurement"]["requirements"][0]
    assert procurement["part_number"] == "C10019"
    assert procurement["dimensions"]["cut_length_mm"] == 1000.0
    assert procurement["dimensions"]["ordered_length_mm"] == 1200.0
    structural = projections["structural"]["analytical_members"][0]
    assert structural["start_m"] == [0.01, 0.02, 0.03]
    assert structural["end_m"] == [0.01, 0.02, 1.03]
    assert structural["section"]["mass_kg_m"] == 3.29
    assert projections["drawing"]["items"][0]["name"] == "C100x1.9 (Lysaght)"
    assert {
        projection["compiled_design_digest"] for projection in projections.values()
    } == {graph["compiled_design_digest"]}


def test_endpoints_control_length_and_placement_for_horizontal_zed() -> None:
    with compile_session() as session:
        member = zed_member(
            "Z10019",
            start_mm=(-500, 100, 200),
            end_mm=(1500, 100, 200),
            mark="Z1",
            rotation_deg=90,
        )
        graph = session.finalize(
            bd.Compound(children=[member])  # type: ignore[call-overload]
        )

    assert member.ports.start.direction == (-1.0, -0.0, -0.0)
    assert member.ports.end.direction == (1.0, 0.0, 0.0)
    assert graph["components"][0]["fabrication"]["cut_length_mm"] == 2000.0
    assert graph["components"][0]["product_key"] == "lysaght-zc-v2:Z10019"


def test_catalogue_member_rejects_profile_overrides_and_short_order_length() -> None:
    with pytest.raises(TypeError, match="unexpected keyword"):
        cee_member(  # type: ignore[call-arg]
            "C10019",
            start_mm=(0, 0, 0),
            end_mm=(0, 0, 1000),
            mark="C1",
            thickness_mm=2.4,
        )

    with compile_session():
        with pytest.raises(ValueError, match="at least the endpoint cut length"):
            cee_member(
                "C10019",
                start_mm=(0, 0, 0),
                end_mm=(0, 0, 1000),
                ordered_length_mm=999,
                mark="C1",
            )


def test_product_switch_changes_every_projection_from_the_same_call() -> None:
    projection_sets: list[dict[str, dict]] = []
    for part_number in ("C10015", "C10019"):
        with compile_session() as session:
            member = cee_member(
                part_number,
                start_mm=(0, 0, 0),
                end_mm=(0, 0, 1000),
                mark="M1",
            )
            model = bd.Compound(children=[member])  # type: ignore[call-overload]
            graph = session.finalize(model)
            projection_sets.append(all_workbench_projections(graph, model=model))

    first, second = projection_sets
    assert first["procurement"]["requirements"][0]["part_number"] == "C10015"
    assert second["procurement"]["requirements"][0]["part_number"] == "C10019"
    assert (
        first["structural"]["analytical_members"][0]["section"]
        != second["structural"]["analytical_members"][0]["section"]
    )
    for projection_name in ("procurement", "structural", "drawing", "bounds"):
        assert (
            first[projection_name]["projection_digest"]
            != second[projection_name]["projection_digest"]
        )


def test_design_only_import_is_finalized_by_the_runner(tmp_path: Path) -> None:
    for filename, content in default_project_files().items():
        if filename != "design.py":
            (tmp_path / filename).write_text(content, encoding="utf-8")
    (tmp_path / "design.py").write_text(
        "from lysaght_zc import cee_member\n"
        "model = cee_member(\n"
        "    'C10019', start_mm=(0, 0, 0), end_mm=(0, 0, 2400), mark='P1'\n"
        ")\n",
        encoding="utf-8",
    )

    execution = execute_design(tmp_path)

    assert execution.compiled_design["components"][0]["id"] == "P1"
    assert (
        execution.projections["procurement"]["requirements"][0]["part_number"]
        == "C10019"
    )
    assert "TERTIUS_STRUCTURAL" not in execution.namespace
