from __future__ import annotations

from collections.abc import Mapping, Sequence
from collections import defaultdict, deque
from importlib.metadata import version
from math import pi, sqrt
from typing import Any, Literal, TypedDict, cast

from .capacity_packs import (
    CapacityPackError,
    as_nzs_4600_2005_a1_bolted_sheet_interface,
    as_nzs_4600_2005_a1_eccentric_fastener_group,
    as_nzs_4600_2005_a1_screw_shear_qualification,
    cross_section_capacity,
    manufacturer_working_load_anchor_group_resistance,
    member_compression_capacity,
    tension_member_capacity,
)
from .contracts import (
    AnalyticalMemberDeclaration,
    AnchorGroupCheck,
    BoltedSheetInterfaceCheck,
    BracingLoadPathTrace,
    CalculationEquation,
    CalculationInput,
    CalculationSheet,
    CapabilityState,
    CertificationGate,
    CertificationIssue,
    CertificationModelCoverage,
    CertificationReadiness,
    ConnectionCheck,
    DesignComponent,
    DesignConnection,
    EquilibriumDiagnostic,
    LoadCombination,
    LoadSummary,
    MemberCheck,
    MemberCrossSectionCheck,
    MemberDiagram,
    MemberDiagramStation,
    MemberDistributedLoad,
    MemberResult,
    MemberRestraintCandidateCheck,
    MemberRestraintTrace,
    MemberStabilityComparison,
    MemberStabilityCheck,
    NodalLoad,
    NodeReaction,
    ProjectStructuralCapture,
    Restraints,
    ServiceabilityCheck,
    SnapshotSource,
    SolverMetadata,
    StructuralMember,
    StructuralNode,
    StructuralSnapshot,
    TensionMemberCheck,
    StabilityDirectionResult,
    StabilityResult,
    Vector3,
    VerificationStage,
)
from .site_wind import verify_site_wind_snapshot
from .restraint_evidence import resolve_restraint_evidence

DEFAULT_COMBINATION_ID = "SLS-1.0"
STATION_INTERVALS = 32
RESIDUAL_TOLERANCE = 1e-8
PDELTA_RESIDUAL_RELATIVE_TOLERANCE = 1e-3
STANDARD_GRAVITY_M_S2 = 9.80665
NODE_COORDINATE_DIGITS = 9


class _BoltedSheetInterfaceCommon(TypedDict):
    evidence_status: Literal["unverified", "candidate", "verified"]
    pack_id: str
    pack_version: str
    bolt_part_number: str
    bolt_count: int
    connected_member_id: str | None
    connected_sheet_part_number: str | None
    fixture_part_number: str | None
    fixture_capacity_status: Literal["not_checked", "candidate", "verified"]
    resultant_shear_demand_kN: float
    nominal_bolt_diameter_mm: float | None
    connected_sheet_thickness_mm: float | None
    hole_diameter_mm: float | None
    hole_type: str | None
    minimum_spacing_mm: float | None
    minimum_edge_distance_mm: float | None
    source: str | None
    source_sha256: str | None


class _CalculatedConnectionResistance(TypedDict):
    status: Literal["pass", "fail", "unsupported"]
    evidence_status: Literal["candidate", "verified"]
    pack_id: str
    pack_version: str
    design_force_capacity_kN: float | None
    design_moment_capacity_kNm: float | None
    governing_utilisation: float | None
    stiffness_status: Literal["unverified", "verified"]
    stiffness_basis: str
    source: str | None
    source_sha256: str | None
    basis: str
    blockers: list[str]


def _select_calculated_connection_resistance(
    *,
    grounded: bool,
    anchored_fixture: _CalculatedConnectionResistance | None,
    direct_anchor: _CalculatedConnectionResistance | None,
    cleat: _CalculatedConnectionResistance | None,
    screw: _CalculatedConnectionResistance | None,
    gusset: _CalculatedConnectionResistance | None,
) -> _CalculatedConnectionResistance | None:
    """Choose the complete applicable resistance model for a rendered joint.

    Several calculators deliberately return an ``unsupported`` candidate when
    they recognise only part of a joint. That partial candidate must not mask a
    later calculator that can verify the complete joint. Grounded joints still
    give the anchored-fixture model first refusal because a cleat-only result
    cannot establish the foundation load path.
    """

    if grounded and anchored_fixture is not None:
        return anchored_fixture
    if grounded and direct_anchor is not None:
        return direct_anchor
    candidates = (cleat, screw, gusset, anchored_fixture, direct_anchor)
    return next(
        (
            candidate
            for candidate in candidates
            if candidate is not None and candidate["status"] != "unsupported"
        ),
        next((candidate for candidate in candidates if candidate is not None), None),
    )


class StructuralAnalysisError(ValueError):
    """Raised when the active design cannot be represented by the MVP solver."""


def _numeric_fact(mapping: Mapping[str, Any], key: str) -> float | None:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    normalized = float(value)
    return normalized if normalized > 0 else None


def _anchor_group_check(
    *,
    connection: DesignConnection,
    components: Mapping[str, DesignComponent],
    grounded_component_ids: Sequence[str],
    tension_demand_kN: float,
    shear_demand_kN: float,
) -> AnchorGroupCheck | None:
    anchor_components = [
        components[component_id]
        for component_id in connection.connector_component_ids
        if component_id in components
        and components[component_id].structural_properties.get(
            "anchor_resistance_pack_id"
        )
        == "manufacturer_working_load_anchor_group"
    ]
    if not anchor_components:
        return None

    first = anchor_components[0]
    properties = first.structural_properties
    part_number = str(
        properties.get("anchor_product_part_number")
        or first.part_number
        or "<missing>"
    )
    pack_version = str(properties.get("anchor_resistance_pack_version") or "1")
    source = (
        str(properties["anchor_source"])
        if properties.get("anchor_source")
        else None
    )
    source_sha256 = (
        str(properties["anchor_source_sha256"])
        if properties.get("anchor_source_sha256")
        else None
    )
    reference_substrate = (
        str(properties["anchor_reference_substrate_type"])
        if properties.get("anchor_reference_substrate_type")
        else None
    )
    blockers: list[str] = []
    for anchor in anchor_components:
        anchor_properties = anchor.structural_properties
        declared_part = str(
            anchor_properties.get("anchor_product_part_number")
            or anchor.part_number
            or "<missing>"
        )
        if declared_part != part_number or anchor.part_number != part_number:
            blockers.append(
                f"Anchor {anchor.id} product identity does not match {part_number}."
            )
        for key in (
            "anchor_resistance_pack_id",
            "anchor_resistance_pack_version",
            "anchor_source_sha256",
            "anchor_reference_substrate_type",
            "anchor_reference_embedment_mm",
            "anchor_single_tension_capacity_kN",
            "anchor_single_shear_capacity_kN",
            "anchor_required_edge_distance_mm",
            "anchor_required_spacing_mm",
        ):
            if anchor_properties.get(key) != properties.get(key):
                blockers.append(
                    f"Anchor group mixes incompatible product fact {key}."
                )
                break

    evidence_verified = all(
        anchor.structural_evidence_status == "verified"
        and anchor.structural_properties.get("anchor_source_status") == "verified"
        for anchor in anchor_components
    )
    if not evidence_verified or source is None or source_sha256 is None:
        blockers.append(
            "Exact anchor product evidence requires verified status, source, and SHA-256."
        )
    if source_sha256 is not None and len(source_sha256) != 64:
        blockers.append("Anchor evidence SHA-256 is malformed.")
        source_sha256 = None

    ground = (
        components.get(grounded_component_ids[0])
        if len(grounded_component_ids) == 1
        else None
    )
    substrate_type = (
        str(ground.structural_properties["anchor_substrate_type"])
        if ground is not None
        and ground.structural_properties.get("anchor_substrate_type")
        else None
    )
    substrate_status = cast(
        Literal["unverified", "candidate", "verified"],
        ground.structural_properties.get("anchor_substrate_status", "unverified")
        if ground is not None
        else "unverified",
    )
    if len(grounded_component_ids) != 1:
        blockers.append("Anchor group must resolve to one grounded substrate component.")
    elif substrate_status != "verified":
        blockers.append("Foundation substrate identity and condition are not verified.")
    elif substrate_type != reference_substrate:
        blockers.append(
            f"Anchor evidence covers {reference_substrate!r}, not {substrate_type!r}."
        )

    installed_embedments = [
        value
        for anchor in anchor_components
        if (
            value := _numeric_fact(
                anchor.fabrication,
                "anchor_installed_effective_embedment_mm",
            )
        )
        is not None
    ]
    edge_distances = [
        value
        for anchor in anchor_components
        if (
            value := _numeric_fact(
                anchor.fabrication,
                "anchor_minimum_edge_distance_mm",
            )
        )
        is not None
    ]
    spacings = [
        value
        for anchor in anchor_components
        if (
            value := _numeric_fact(
                anchor.fabrication,
                "anchor_minimum_spacing_mm",
            )
        )
        is not None
    ]
    installed_embedment = min(installed_embedments) if installed_embedments else None
    minimum_edge_distance = min(edge_distances) if edge_distances else None
    minimum_spacing = min(spacings) if spacings else None
    reference_embedment = _numeric_fact(
        properties, "anchor_reference_embedment_mm"
    )
    tension_capacity = _numeric_fact(
        properties, "anchor_single_tension_capacity_kN"
    )
    shear_capacity = _numeric_fact(properties, "anchor_single_shear_capacity_kN")
    required_edge_distance = _numeric_fact(
        properties, "anchor_required_edge_distance_mm"
    )
    required_spacing = _numeric_fact(
        properties, "anchor_required_spacing_mm"
    )
    if len(installed_embedments) != len(anchor_components):
        blockers.append("Every rendered anchor requires an installed embedment fact.")
    if len(edge_distances) != len(anchor_components):
        blockers.append("Every rendered anchor requires a minimum edge-distance fact.")
    if len(anchor_components) > 1 and len(spacings) != len(anchor_components):
        blockers.append("Every grouped anchor requires a minimum spacing fact.")
    if any(
        value is None
        for value in (
            reference_embedment,
            tension_capacity,
            shear_capacity,
            required_edge_distance,
            required_spacing,
        )
    ):
        blockers.append("Anchor product capacity or installation limits are incomplete.")

    evidence_status: Literal["unverified", "candidate", "verified"] = (
        "verified" if evidence_verified else "candidate" if source else "unverified"
    )
    if blockers:
        return AnchorGroupCheck(
            status="unsupported",
            evidence_status=evidence_status,
            pack_id="manufacturer_working_load_anchor_group",
            pack_version=pack_version,
            anchor_part_number=part_number,
            anchor_count=len(anchor_components),
            effective_anchor_count=1.0,
            substrate_type=substrate_type,
            substrate_status=substrate_status,
            tension_demand_kN=tension_demand_kN,
            shear_demand_kN=shear_demand_kN,
            tension_capacity_kN=tension_capacity,
            shear_capacity_kN=shear_capacity,
            installed_effective_embedment_mm=installed_embedment,
            reference_embedment_mm=reference_embedment,
            minimum_edge_distance_mm=minimum_edge_distance,
            required_edge_distance_mm=required_edge_distance,
            minimum_spacing_mm=minimum_spacing,
            required_spacing_mm=required_spacing,
            source=source,
            source_sha256=source_sha256,
            basis=(
                "Exact rendered anchor identities were found, but the generic "
                "Tertius anchor resistance pack is missing required verified facts."
            ),
            blockers=sorted(set(blockers)),
        )

    assert installed_embedment is not None
    assert minimum_edge_distance is not None
    assert reference_embedment is not None
    assert tension_capacity is not None
    assert shear_capacity is not None
    assert required_edge_distance is not None
    assert required_spacing is not None
    result = manufacturer_working_load_anchor_group_resistance(
        anchor_count=len(anchor_components),
        single_anchor_tension_capacity_kN=tension_capacity,
        single_anchor_shear_capacity_kN=shear_capacity,
        tension_demand_kN=tension_demand_kN,
        shear_demand_kN=shear_demand_kN,
        installed_effective_embedment_mm=installed_embedment,
        reference_embedment_mm=reference_embedment,
        minimum_edge_distance_mm=minimum_edge_distance,
        required_edge_distance_mm=required_edge_distance,
        minimum_spacing_mm=minimum_spacing,
        required_spacing_mm=required_spacing,
    )
    return AnchorGroupCheck(
        status=result.status,
        evidence_status="verified",
        pack_id=result.pack_id,
        pack_version=result.pack_version,
        anchor_part_number=part_number,
        anchor_count=result.anchor_count,
        effective_anchor_count=result.effective_anchor_count,
        substrate_type=substrate_type,
        substrate_status=substrate_status,
        tension_demand_kN=tension_demand_kN,
        shear_demand_kN=shear_demand_kN,
        tension_capacity_kN=result.design_tension_capacity_kN,
        shear_capacity_kN=result.design_shear_capacity_kN,
        interaction_utilisation=result.interaction_utilisation,
        installed_effective_embedment_mm=installed_embedment,
        reference_embedment_mm=reference_embedment,
        minimum_edge_distance_mm=minimum_edge_distance,
        required_edge_distance_mm=required_edge_distance,
        minimum_spacing_mm=minimum_spacing,
        required_spacing_mm=required_spacing,
        embedment_status=result.embedment_status,
        edge_distance_status=result.edge_distance_status,
        spacing_status=result.spacing_status,
        source=source,
        source_sha256=source_sha256,
        basis=result.basis,
    )


def _bolted_sheet_interface_check(
    *,
    connection: DesignConnection,
    components: Mapping[str, DesignComponent],
    analysis,
    resultant_shear_demand_kN: float,
) -> BoltedSheetInterfaceCheck | None:
    fasteners = [
        components[component_id]
        for component_id in connection.connector_component_ids
        if component_id in components
        and components[component_id].structural_properties.get(
            "bolted_sheet_fastener_pack_id"
        )
        == "as_nzs_4600_2005_a1_bolted_sheet_interface"
    ]
    if not fasteners:
        return None

    first = fasteners[0]
    properties = first.structural_properties
    part_number = str(first.part_number or "<missing>")
    source = str(properties["source"]) if properties.get("source") else None
    source_sha256 = (
        str(properties["source_sha256"])
        if properties.get("source_sha256")
        else None
    )
    fixture_components = [
        components[component_id]
        for component_id in connection.connector_component_ids
        if component_id in components
        and (
            components[component_id].structural_properties.get(
                "base_fixture_capacity_status"
            )
            or components[component_id].structural_properties.get(
                "anchored_fixture_capacity_pack_id"
            )
            == "specified_grade_pinned_steel_fixture"
        )
    ]
    fixture_part_number = (
        fixture_components[0].part_number if len(fixture_components) == 1 else None
    )
    fixture_capacity_status = cast(
        Literal["not_checked", "candidate", "verified"],
        fixture_components[0].structural_properties.get(
            "base_fixture_capacity_status", "not_checked"
        )
        if len(fixture_components) == 1
        else "not_checked",
    )
    blockers: list[str] = []
    if len(fixture_components) != 1:
        blockers.append(
            "Bolted sheet interface must resolve to one rendered fixture component."
        )

    product_keys = (
        "bolted_sheet_fastener_pack_id",
        "bolted_sheet_fastener_pack_version",
        "nominal_diameter_mm",
        "bolt_tensile_strength_MPa",
        "bolt_minor_area_mm2",
        "washers_under_head_and_nut",
        "source_sha256",
    )
    for fastener in fasteners:
        if fastener.part_number != part_number:
            blockers.append("Bolted sheet group mixes fastener part numbers.")
        if fastener.product_key is None or fastener.product_definition_digest is None:
            blockers.append(
                f"Fastener {fastener.id} lacks a managed product identity."
            )
        if fastener.structural_evidence_status != "verified":
            blockers.append(
                f"Fastener {fastener.id} product evidence is not verified."
            )
        for key in product_keys:
            if fastener.structural_properties.get(key) != properties.get(key):
                blockers.append(f"Bolted sheet group mixes incompatible {key} facts.")
                break
    if source is None or source_sha256 is None or len(source_sha256) != 64:
        blockers.append("Fastener evidence requires a source and valid SHA-256.")
        source_sha256 = None

    connected_component_ids = {
        connection.from_component_id,
        connection.to_component_id,
    }
    connected_declarations = [
        declaration
        for declaration in analysis.members
        if declaration.component_id in connected_component_ids
        and getattr(declaration, "analytical_role", "physical") == "physical"
    ]
    connected_member_component_ids = {
        declaration.component_id for declaration in connected_declarations
    }
    connected_section_material_pairs = {
        (declaration.section_id, declaration.material_id)
        for declaration in connected_declarations
    }
    declaration = next(
        (
            declaration
            for declaration in connected_declarations
            if connection.id
            in (
                _node_key_connection_ids(declaration.start_node_key)
                | _node_key_connection_ids(declaration.end_node_key)
            )
        ),
        connected_declarations[0] if connected_declarations else None,
    )
    if (
        declaration is None
        or len(connected_member_component_ids) != 1
        or len(connected_section_material_pairs) != 1
    ):
        blockers.append(
            "Bolted sheet interface must resolve to one physical member product and section."
        )
    sections = {section.id: section for section in getattr(analysis, "sections", ())}
    materials = {
        material.id: material for material in getattr(analysis, "materials", ())
    }
    section = sections.get(declaration.section_id) if declaration is not None else None
    material = materials.get(declaration.material_id) if declaration is not None else None
    catalogue_properties = (
        section.catalog.properties
        if section is not None and section.catalog is not None
        else {}
    )
    sheet_thickness_mm = _numeric_fact(catalogue_properties, "t_mm")
    sheet_yield_strength_MPa = (
        material.yield_strength_MPa if material is not None else None
    )
    sheet_tensile_strength_MPa = (
        material.tensile_strength_MPa if material is not None else None
    )
    if section is None or section.catalog is None:
        blockers.append("Connected sheet requires a traceable catalogue section record.")
    elif not bool(catalogue_properties.get("validated")):
        blockers.append("Connected sheet catalogue record is not verified.")
    if sheet_thickness_mm is None or sheet_thickness_mm >= 3.0:
        blockers.append(
            "Connected sheet requires a verified thickness below 3 mm for AS/NZS 4600 Clause 5.3."
        )
    if sheet_yield_strength_MPa is None or sheet_tensile_strength_MPa is None:
        blockers.append("Connected sheet yield and tensile strengths are incomplete.")

    def uniform_fabrication_fact(key: str) -> float | None:
        values = {
            value
            for fastener in fasteners
            if (value := _numeric_fact(fastener.fabrication, key)) is not None
        }
        if len(values) != 1 or len(fasteners) != sum(
            _numeric_fact(fastener.fabrication, key) is not None
            for fastener in fasteners
        ):
            blockers.append(f"Every bolt requires one consistent installed {key} fact.")
            return None
        return next(iter(values))

    hole_diameter_mm = uniform_fabrication_fact("sheet_hole_diameter_mm")
    minimum_spacing_mm = uniform_fabrication_fact("minimum_bolt_spacing_mm")
    minimum_edge_distance_mm = uniform_fabrication_fact(
        "minimum_sheet_edge_distance_mm"
    )
    hole_types = {
        str(fastener.fabrication.get("sheet_hole_type") or "")
        for fastener in fasteners
    }
    hole_type = next(iter(hole_types)) if len(hole_types) == 1 else None
    if hole_type != "standard_round":
        blockers.append(
            "The verified base-interface pack currently requires standard round holes."
        )

    nominal_diameter_mm = _numeric_fact(properties, "nominal_diameter_mm")
    bolt_tensile_strength_MPa = _numeric_fact(
        properties, "bolt_tensile_strength_MPa"
    )
    bolt_minor_area_mm2 = _numeric_fact(properties, "bolt_minor_area_mm2")
    washers = properties.get("washers_under_head_and_nut")
    if any(
        value is None
        for value in (
            nominal_diameter_mm,
            bolt_tensile_strength_MPa,
            bolt_minor_area_mm2,
        )
    ) or not isinstance(washers, bool):
        blockers.append("Fastener strength or bearing-washer facts are incomplete.")

    evidence_status: Literal["unverified", "candidate", "verified"] = (
        "verified"
        if not any("evidence" in blocker.lower() for blocker in blockers)
        and all(fastener.structural_evidence_status == "verified" for fastener in fasteners)
        else "candidate"
        if source
        else "unverified"
    )
    common: _BoltedSheetInterfaceCommon = dict(
        evidence_status=evidence_status,
        pack_id="as_nzs_4600_2005_a1_bolted_sheet_interface",
        pack_version=str(properties.get("bolted_sheet_fastener_pack_version") or "1"),
        bolt_part_number=part_number,
        bolt_count=len(fasteners),
        connected_member_id=declaration.id if declaration is not None else None,
        connected_sheet_part_number=(
            components[declaration.component_id].part_number
            if declaration is not None
            and declaration.component_id in components
            else None
        ),
        fixture_part_number=fixture_part_number,
        fixture_capacity_status=fixture_capacity_status,
        resultant_shear_demand_kN=resultant_shear_demand_kN,
        nominal_bolt_diameter_mm=nominal_diameter_mm,
        connected_sheet_thickness_mm=sheet_thickness_mm,
        hole_diameter_mm=hole_diameter_mm,
        hole_type=hole_type,
        minimum_spacing_mm=minimum_spacing_mm,
        minimum_edge_distance_mm=minimum_edge_distance_mm,
        source=source,
        source_sha256=source_sha256,
    )
    if blockers:
        return BoltedSheetInterfaceCheck(
            status="unsupported",
            basis=(
                "The rendered bolt group and connected sheet were found, but the "
                "Tertius AS/NZS 4600 bolted-sheet pack is missing required verified facts."
            ),
            blockers=sorted(set(blockers)),
            **common,
        )

    assert nominal_diameter_mm is not None
    assert bolt_tensile_strength_MPa is not None
    assert bolt_minor_area_mm2 is not None
    assert sheet_thickness_mm is not None
    assert sheet_yield_strength_MPa is not None
    assert sheet_tensile_strength_MPa is not None
    assert hole_diameter_mm is not None
    assert minimum_spacing_mm is not None
    assert minimum_edge_distance_mm is not None
    assert hole_type is not None
    assert isinstance(washers, bool)
    result = as_nzs_4600_2005_a1_bolted_sheet_interface(
        bolt_count=len(fasteners),
        resultant_shear_demand_kN=resultant_shear_demand_kN,
        nominal_bolt_diameter_mm=nominal_diameter_mm,
        bolt_tensile_strength_MPa=bolt_tensile_strength_MPa,
        bolt_minor_area_mm2=bolt_minor_area_mm2,
        connected_sheet_thickness_mm=sheet_thickness_mm,
        connected_sheet_yield_strength_MPa=sheet_yield_strength_MPa,
        connected_sheet_tensile_strength_MPa=sheet_tensile_strength_MPa,
        hole_diameter_mm=hole_diameter_mm,
        hole_type=hole_type,
        minimum_spacing_mm=minimum_spacing_mm,
        minimum_edge_distance_mm=minimum_edge_distance_mm,
        washers_under_head_and_nut=washers,
    )
    return BoltedSheetInterfaceCheck(
        status=result.status,
        design_bolt_shear_capacity_kN=result.design_bolt_shear_capacity_kN,
        design_sheet_bearing_capacity_kN=result.design_sheet_bearing_capacity_kN,
        design_sheet_tearout_capacity_kN=result.design_sheet_tearout_capacity_kN,
        governing_capacity_kN=result.governing_capacity_kN,
        governing_utilisation=result.governing_utilisation,
        required_spacing_mm=result.required_spacing_mm,
        required_edge_distance_mm=result.required_edge_distance_mm,
        bolt_shear_status=result.bolt_shear_status,
        sheet_bearing_status=result.sheet_bearing_status,
        sheet_tearout_status=result.sheet_tearout_status,
        hole_status=result.hole_status,
        spacing_status=result.spacing_status,
        edge_distance_status=result.edge_distance_status,
        basis=result.basis,
        blockers=(
            []
            if fixture_capacity_status == "verified"
            else [
                f"Fixture {fixture_part_number or '<missing>'} plate resistance remains a separate check."
            ]
        ),
        **common,
    )


def _calculated_bolted_cleat_resistance(
    *,
    connection: DesignConnection,
    components: Mapping[str, DesignComponent],
    analysis,
    resultant_force_demand_kN: float,
    moment_demand_kNm: float,
) -> _CalculatedConnectionResistance | None:
    """Resolve a complete two-legged cleat from exact product/layout facts."""

    fixtures = [
        components[component_id]
        for component_id in connection.connector_component_ids
        if component_id in components
        and components[component_id].structural_properties.get(
            "connection_capacity_pack_id"
        )
        == "as_nzs_4600_2005_a1_bolted_cleat"
    ]
    if not fixtures:
        return None
    blockers: list[str] = []
    fixture = fixtures[0]
    if len(fixtures) != 1:
        blockers.append("Complete bolted-cleat pack requires exactly one cleat fixture.")
    fixture_properties = fixture.structural_properties
    source = (
        str(fixture_properties["source"])
        if fixture_properties.get("source")
        else None
    )
    source_sha256 = (
        str(fixture_properties["source_sha256"])
        if fixture_properties.get("source_sha256")
        else None
    )
    if (
        fixture.structural_evidence_status != "verified"
        or fixture.product_key is None
        or fixture.product_definition_digest is None
    ):
        blockers.append("Cleat fixture requires a verified managed product identity.")
    if source is None or source_sha256 is None or len(source_sha256) != 64:
        blockers.append("Cleat fixture requires a source and valid SHA-256.")
        source_sha256 = None

    fasteners = [
        components[component_id]
        for component_id in connection.connector_component_ids
        if component_id in components
        and components[component_id].structural_properties.get(
            "bolted_sheet_fastener_pack_id"
        )
        == "as_nzs_4600_2005_a1_bolted_sheet_interface"
    ]
    fasteners_per_interface_raw = fixture_properties.get(
        "fasteners_per_interface"
    )
    fasteners_per_interface = (
        int(fasteners_per_interface_raw)
        if isinstance(fasteners_per_interface_raw, int)
        and not isinstance(fasteners_per_interface_raw, bool)
        and fasteners_per_interface_raw >= 2
        else None
    )
    if fasteners_per_interface is None:
        blockers.append("Cleat fixture requires at least two fasteners per interface.")
    fastener = fasteners[0] if fasteners else None
    if fastener is None:
        blockers.append("Complete bolted-cleat pack found no managed PB fasteners.")
        fastener_properties: Mapping[str, Any] = {}
    else:
        fastener_properties = fastener.structural_properties
        product_fact_keys = (
            "nominal_diameter_mm",
            "bolt_tensile_strength_MPa",
            "bolt_minor_area_mm2",
            "washers_under_head_and_nut",
            "source_sha256",
        )
        for item in fasteners:
            if (
                item.part_number != fastener.part_number
                or item.product_key is None
                or item.product_definition_digest is None
                or item.structural_evidence_status != "verified"
            ):
                blockers.append(
                    "Every cleat fastener requires the same verified managed product identity."
                )
                break
            if any(
                item.structural_properties.get(key)
                != fastener_properties.get(key)
                for key in product_fact_keys
            ):
                blockers.append("Cleat fastener group mixes incompatible product facts.")
                break

    raw_coordinates = fixture_properties.get("fastener_coordinates_mm")
    coordinates: tuple[tuple[float, float], ...] = ()
    if isinstance(raw_coordinates, list):
        parsed_coordinates: list[tuple[float, float]] = []
        for raw_point in raw_coordinates:
            if (
                isinstance(raw_point, list | tuple)
                and len(raw_point) == 2
                and all(
                    isinstance(value, int | float) and not isinstance(value, bool)
                    for value in raw_point
                )
            ):
                parsed_coordinates.append((float(raw_point[0]), float(raw_point[1])))
        coordinates = tuple(parsed_coordinates)
    if fasteners_per_interface is not None and len(coordinates) != fasteners_per_interface:
        blockers.append(
            "Cleat fixture fastener coordinates must describe one complete interface."
        )

    numeric_product_facts = {
        key: _numeric_fact(fastener_properties, key)
        for key in (
            "nominal_diameter_mm",
            "bolt_tensile_strength_MPa",
            "bolt_minor_area_mm2",
        )
    }
    washers = fastener_properties.get("washers_under_head_and_nut")
    if any(value is None for value in numeric_product_facts.values()) or not isinstance(
        washers, bool
    ):
        blockers.append("Cleat fastener strength and washer facts are incomplete.")

    hole_diameter_mm = _numeric_fact(
        fixture_properties, "connected_sheet_hole_diameter_mm"
    )
    minimum_spacing_mm = _numeric_fact(
        fixture_properties, "minimum_bolt_spacing_mm"
    )
    minimum_edge_distance_mm = _numeric_fact(
        fixture_properties, "minimum_sheet_edge_distance_mm"
    )
    hole_type = str(fixture_properties.get("connected_sheet_hole_type") or "")
    maximum_connection_slip_mm = _numeric_fact(
        fixture_properties, "maximum_connection_slip_mm"
    )
    if any(
        value is None
        for value in (
            hole_diameter_mm,
            minimum_spacing_mm,
            minimum_edge_distance_mm,
            maximum_connection_slip_mm,
        )
    ) or hole_type != "standard_round":
        blockers.append(
            "Cleat installation requires verified round-hole, spacing, edge and slip facts."
        )

    connected_component_ids = {
        connection.from_component_id,
        connection.to_component_id,
    }
    declarations = [
        declaration
        for declaration in analysis.members
        if declaration.component_id in connected_component_ids
        and getattr(declaration, "analytical_role", "physical") == "physical"
    ]
    sections = {
        section.id: section for section in getattr(analysis, "sections", ())
    }
    materials = {
        material.id: material for material in getattr(analysis, "materials", ())
    }
    sheet_facts_by_component: dict[str, tuple[Any, Any, float]] = {}
    for declaration in declarations:
        section = sections.get(declaration.section_id)
        material = materials.get(declaration.material_id)
        catalogue_properties = (
            section.catalog.properties
            if section is not None and section.catalog is not None
            else {}
        )
        thickness_mm = _numeric_fact(catalogue_properties, "t_mm")
        if (
            section is None
            or section.catalog is None
            or not bool(catalogue_properties.get("validated"))
            or thickness_mm is None
            or thickness_mm >= 3.0
            or material is None
            or material.yield_strength_MPa is None
            or material.tensile_strength_MPa is None
        ):
            blockers.append(
                f"Connected component {declaration.component_id!r} lacks a verified sub-3 mm sheet/material record."
            )
            continue
        sheet_facts_by_component.setdefault(
            declaration.component_id,
            (declaration, material, thickness_mm),
        )
    sheet_facts = list(sheet_facts_by_component.values())
    if len(sheet_facts) != 2:
        blockers.append(
            "Complete two-legged cleat requires exactly two connected cold-formed sheets."
        )
    if (
        fasteners_per_interface is not None
        and len(fasteners) < 2 * fasteners_per_interface
    ):
        blockers.append(
            "Rendered two-legged cleat has fewer verified fasteners than its two interfaces."
        )

    fixture_thickness_mm = _numeric_fact(
        fixture_properties, "fixture_thickness_mm"
    )
    fixture_yield_strength_MPa = _numeric_fact(
        fixture_properties, "fixture_yield_strength_MPa"
    )
    fixture_tensile_strength_MPa = _numeric_fact(
        fixture_properties, "fixture_tensile_strength_MPa"
    )
    if any(
        value is None
        for value in (
            fixture_thickness_mm,
            fixture_yield_strength_MPa,
            fixture_tensile_strength_MPa,
        )
    ):
        blockers.append("Cleat fixture material and thickness facts are incomplete.")

    common: _CalculatedConnectionResistance = {
        "status": "unsupported",
        "evidence_status": "candidate",
        "pack_id": "as_nzs_4600_2005_a1_bolted_cleat",
        "pack_version": str(
            fixture_properties.get("connection_capacity_pack_version") or "1"
        ),
        "design_force_capacity_kN": None,
        "design_moment_capacity_kNm": None,
        "governing_utilisation": None,
        "stiffness_status": "unverified",
        "stiffness_basis": (
            "The complete cleat fastener layout has not established a bearing-engaged stiffness path."
        ),
        "source": source,
        "source_sha256": source_sha256,
        "basis": (
            "The rendered cleat was identified, but the complete AS/NZS 4600 bolted-cleat calculation is missing required facts."
        ),
        "blockers": sorted(set(blockers)),
    }
    if blockers:
        return common

    assert fasteners_per_interface is not None
    assert fastener is not None
    assert numeric_product_facts["nominal_diameter_mm"] is not None
    assert numeric_product_facts["bolt_tensile_strength_MPa"] is not None
    assert numeric_product_facts["bolt_minor_area_mm2"] is not None
    assert isinstance(washers, bool)
    assert hole_diameter_mm is not None
    assert minimum_spacing_mm is not None
    assert minimum_edge_distance_mm is not None
    assert maximum_connection_slip_mm is not None
    assert fixture_thickness_mm is not None
    assert fixture_yield_strength_MPa is not None
    assert fixture_tensile_strength_MPa is not None

    interface_results = [
        as_nzs_4600_2005_a1_bolted_sheet_interface(
            bolt_count=fasteners_per_interface,
            resultant_shear_demand_kN=0.0,
            nominal_bolt_diameter_mm=numeric_product_facts[
                "nominal_diameter_mm"
            ],
            bolt_tensile_strength_MPa=numeric_product_facts[
                "bolt_tensile_strength_MPa"
            ],
            bolt_minor_area_mm2=numeric_product_facts["bolt_minor_area_mm2"],
            connected_sheet_thickness_mm=thickness_mm,
            connected_sheet_yield_strength_MPa=material.yield_strength_MPa,
            connected_sheet_tensile_strength_MPa=material.tensile_strength_MPa,
            hole_diameter_mm=hole_diameter_mm,
            hole_type=hole_type,
            minimum_spacing_mm=minimum_spacing_mm,
            minimum_edge_distance_mm=minimum_edge_distance_mm,
            washers_under_head_and_nut=washers,
        )
        for _declaration, material, thickness_mm in sheet_facts
    ]
    single_fastener_capacity_kN = min(
        result.governing_capacity_kN / result.bolt_count
        for result in interface_results
    )
    fixture_strength_product = fixture_thickness_mm * fixture_tensile_strength_MPa
    connected_strength_product = max(
        thickness_mm * material.tensile_strength_MPa
        for _declaration, material, thickness_mm in sheet_facts
    )
    fixture_status: Literal["pass", "fail"] = (
        "pass"
        if fixture_strength_product >= connected_strength_product
        and fixture_thickness_mm >= max(item[2] for item in sheet_facts)
        else "fail"
    )
    group = as_nzs_4600_2005_a1_eccentric_fastener_group(
        fastener_coordinates_mm=coordinates,
        design_single_fastener_capacity_kN=single_fastener_capacity_kN,
        resultant_force_demand_kN=resultant_force_demand_kN,
        moment_demand_kNm=moment_demand_kNm,
    )
    geometry_status: Literal["pass", "fail"] = (
        "pass"
        if all(result.status == "pass" for result in interface_results)
        else "fail"
    )
    stiffness_verified = (
        maximum_connection_slip_mm <= minimum_spacing_mm / 10.0
        and fixture_status == "pass"
        and geometry_status == "pass"
        and len(coordinates) >= 2
    )
    common.update(
        status=(
            "pass"
            if group.status == fixture_status == geometry_status == "pass"
            and stiffness_verified
            else "fail"
        ),
        evidence_status="verified",
        design_force_capacity_kN=group.design_force_capacity_kN,
        design_moment_capacity_kNm=group.design_moment_capacity_kNm,
        governing_utilisation=group.interaction_utilisation,
        stiffness_status="verified" if stiffness_verified else "unverified",
        stiffness_basis=(
            "The exact two-leg cleat has at least two separated bearing fasteners "
            "per interface; published/installed clearance is no more than one tenth "
            "of the fastener pitch, and both connected-sheet geometry and the thicker "
            "cleat strength hierarchy pass. This verifies the local restraint force "
            "path, not an unmodelled portal-frame rigid joint."
        ),
        basis=(
            f"{group.basis} Every connected Cee interface was checked separately; "
            "the weakest bolt/shear/bearing/tear-out result governs. The cleat is "
            "also required to be thicker and to have at least the connected-sheet "
            "t*fu strength product."
        ),
        blockers=[],
    )
    return common


def _calculated_screw_connection_resistance(
    *,
    connection: DesignConnection,
    components: Mapping[str, DesignComponent],
    analysis,
    resultant_force_demand_kN: float,
    moment_demand_kNm: float,
) -> _CalculatedConnectionResistance | None:
    """Check an exact tested self-drilling-screw group between steel sheets."""

    fasteners = [
        components[component_id]
        for component_id in connection.connector_component_ids
        if component_id in components
        and components[component_id].structural_properties.get("fastener_type")
        == "self_drilling_screw"
    ]
    if not fasteners:
        return None

    blockers: list[str] = []
    if len(fasteners) != len(connection.connector_component_ids):
        blockers.append(
            "The screw connection includes connector components without the same "
            "self-drilling-screw capacity facts."
        )
    first = fasteners[0]
    first_properties = first.structural_properties
    product_fact_keys = (
        "nominal_diameter_mm",
        "tested_single_shear_strength_kN",
        "test_evidence_source",
        "test_evidence_revision",
        "test_evidence_url",
    )
    for fastener in fasteners:
        if (
            fastener.kind != "connector"
            or fastener.part_number != first.part_number
            or fastener.product_key is None
            or fastener.product_definition_digest is None
            or fastener.structural_evidence_status != "verified"
        ):
            blockers.append(
                "Every rendered screw requires the same verified managed product identity."
            )
            break
        if any(
            fastener.structural_properties.get(key)
            != first_properties.get(key)
            for key in product_fact_keys
        ):
            blockers.append(
                "Rendered screws disagree on diameter, tested strength, or source identity."
            )
            break

    def positive_number(key: str) -> float | None:
        value = first_properties.get(key)
        if isinstance(value, bool) or not isinstance(value, int | float):
            blockers.append(f"Screw product requires numeric {key!r}.")
            return None
        converted = float(value)
        if converted <= 0:
            blockers.append(f"Screw product requires positive {key!r}.")
            return None
        return converted

    diameter_mm = positive_number("nominal_diameter_mm")
    tested_single_shear_strength_kN = positive_number(
        "tested_single_shear_strength_kN"
    )
    source = (
        str(first_properties["test_evidence_source"])
        if first_properties.get("test_evidence_source")
        else None
    )
    source_url = (
        str(first_properties["test_evidence_url"])
        if first_properties.get("test_evidence_url")
        else None
    )
    source_sha256 = (
        str(first_properties["source_sha256"])
        if first_properties.get("source_sha256")
        else None
    )
    if source is None or source_url is None:
        blockers.append("Screw product requires its manufacturer test source and URL.")

    connected_component_ids = {
        connection.from_component_id,
        connection.to_component_id,
        *connection.component_ports.keys(),
    }
    sections = {section.id: section for section in analysis.sections}
    materials = {material.id: material for material in analysis.materials}
    sheet_facts: list[tuple[Any, Any, float]] = []
    seen_component_ids: set[str] = set()
    for declaration in analysis.members:
        if (
            declaration.component_id not in connected_component_ids
            or declaration.component_id in seen_component_ids
        ):
            continue
        section = sections[declaration.section_id]
        material = materials[declaration.material_id]
        thickness_mm = _section_thickness_mm(section)
        if (
            thickness_mm is None
            or material.tensile_strength_MPa is None
            or material.yield_strength_MPa is None
        ):
            blockers.append(
                f"Connected member {declaration.id!r} lacks verified sheet thickness/fu/fy."
            )
            continue
        sheet_facts.append((declaration, material, thickness_mm))
        seen_component_ids.add(declaration.component_id)
    if len(sheet_facts) < 2:
        blockers.append(
            "Self-drilling-screw calculation requires two connected steel sheet facts."
        )

    if moment_demand_kNm > 1e-9:
        blockers.append(
            "A screw group with no declared fastener coordinates cannot verify moment transfer; "
            "author the physical joint as pinned or declare the exact group layout."
        )

    common: _CalculatedConnectionResistance = {
        "status": "unsupported",
        "evidence_status": "candidate",
        "pack_id": "as_nzs_4600_2005_a1_screwed_sheet_connection",
        "pack_version": "1",
        "design_force_capacity_kN": None,
        "design_moment_capacity_kNm": None,
        "governing_utilisation": None,
        "stiffness_status": "unverified",
        "stiffness_basis": (
            "The screw group verifies translational force transfer only; no rotational "
            "restraint is inferred without separated fastener coordinates."
        ),
        "source": source,
        "source_sha256": source_sha256,
        "basis": (
            "The rendered self-drilling-screw group was identified, but the complete "
            "AS/NZS 4600 sheet-bearing calculation is missing required facts."
        ),
        "blockers": sorted(set(blockers)),
    }
    if blockers:
        return common

    assert diameter_mm is not None
    assert tested_single_shear_strength_kN is not None
    nominal_bearing_capacities = [
        min(
            _screw_bearing_nominal_kN(
                head_sheet_thickness_mm=head_thickness_mm,
                head_sheet_fu_MPa=head_material.tensile_strength_MPa,
                other_sheet_thickness_mm=other_thickness_mm,
                other_sheet_fu_MPa=other_material.tensile_strength_MPa,
                diameter_mm=diameter_mm,
            ),
            _screw_bearing_nominal_kN(
                head_sheet_thickness_mm=other_thickness_mm,
                head_sheet_fu_MPa=other_material.tensile_strength_MPa,
                other_sheet_thickness_mm=head_thickness_mm,
                other_sheet_fu_MPa=head_material.tensile_strength_MPa,
                diameter_mm=diameter_mm,
            ),
        )
        for head_index, (_head_declaration, head_material, head_thickness_mm) in enumerate(
            sheet_facts
        )
        for _other_declaration, other_material, other_thickness_mm in sheet_facts[
            head_index + 1 :
        ]
    ]
    if not nominal_bearing_capacities:
        common["blockers"] = [
            "No connected steel-sheet pair was available for screw bearing."
        ]
        return common
    weakest_nominal_bearing_kN = min(nominal_bearing_capacities)
    qualification = as_nzs_4600_2005_a1_screw_shear_qualification(
        tested_single_shear_strength_kN=tested_single_shear_strength_kN,
        nominal_bearing_capacity_kN=max(nominal_bearing_capacities),
    )
    design_force_capacity_kN = (
        len(fasteners) * 0.50 * weakest_nominal_bearing_kN
    )
    utilisation = resultant_force_demand_kN / design_force_capacity_kN
    common.update(
        status=(
            "pass"
            if qualification.status == "pass" and utilisation <= 1.0
            else "fail"
        ),
        evidence_status="verified",
        design_force_capacity_kN=design_force_capacity_kN,
        governing_utilisation=utilisation,
        basis=(
            "AS/NZS 4600:2005+A1 Clauses 5.4.2.3 and 5.4.2.5: the weakest "
            "orientation and connected-sheet pair governs phi=0.50 bearing, and the "
            "exact manufacturer-tested screw must satisfy the 1.25 Vb Section 8 "
            f"qualification. {len(fasteners)} rendered screw(s) are included; "
            f"manufacturer evidence: {source}."
        ),
        blockers=(
            []
            if qualification.status == "pass"
            else [
                "Manufacturer-tested screw shear is below the Clause 5.4.2.5 "
                "qualification threshold for the connected sheets."
            ]
        ),
    )
    return common


def _calculated_fabricated_gusset_resistance(
    *,
    connection: DesignConnection,
    components: Mapping[str, DesignComponent],
    analysis,
    resultant_force_demand_kN: float,
    moment_demand_kNm: float,
) -> _CalculatedConnectionResistance | None:
    """Check a two-sided flat gusset and its exact four-bolt interfaces."""

    fixtures = [
        components[component_id]
        for component_id in connection.connector_component_ids
        if component_id in components
        and components[component_id].structural_properties.get(
            "connection_capacity_pack_id"
        )
        == "as_nzs_4600_2005_a1_fabricated_gusset"
    ]
    if not fixtures:
        return None
    fixture = fixtures[0]
    properties = fixture.structural_properties
    blockers: list[str] = []
    if len(fixtures) != 1:
        blockers.append("Fabricated-gusset pack requires exactly one plate fixture.")
    if (
        fixture.structural_evidence_status != "verified"
        or fixture.product_key is None
        or fixture.product_definition_digest is None
    ):
        blockers.append("Gusset requires a verified managed fabrication identity.")

    source = str(properties["source"]) if properties.get("source") else None
    source_sha256 = (
        str(properties["source_sha256"])
        if properties.get("source_sha256")
        else None
    )
    if source is None or source_sha256 is None or len(source_sha256) != 64:
        blockers.append("Gusset calculation requires a source and valid SHA-256.")
        source_sha256 = None

    fasteners = [
        components[component_id]
        for component_id in connection.connector_component_ids
        if component_id in components
        and components[component_id].structural_properties.get(
            "bolted_sheet_fastener_pack_id"
        )
        == "as_nzs_4600_2005_a1_bolted_sheet_interface"
    ]
    first_fastener = fasteners[0] if fasteners else None
    if first_fastener is None:
        blockers.append("Fabricated gusset found no verified PB fasteners.")
        fastener_properties: Mapping[str, Any] = {}
    else:
        fastener_properties = first_fastener.structural_properties
        for fastener in fasteners:
            if (
                fastener.part_number != first_fastener.part_number
                or fastener.product_key is None
                or fastener.product_definition_digest is None
                or fastener.structural_evidence_status != "verified"
            ):
                blockers.append(
                    "Every gusset fastener requires the same verified managed product identity."
                )
                break

    fasteners_per_interface_raw = properties.get("fasteners_per_interface")
    fasteners_per_interface = (
        int(fasteners_per_interface_raw)
        if isinstance(fasteners_per_interface_raw, int)
        and not isinstance(fasteners_per_interface_raw, bool)
        and fasteners_per_interface_raw >= 4
        else None
    )
    if fasteners_per_interface is None:
        blockers.append("Gusset requires at least four bolts per member interface.")
    elif len(fasteners) != 2 * fasteners_per_interface:
        blockers.append(
            "Rendered gusset bolt count does not match its two complete interfaces."
        )

    raw_coordinates = properties.get("fastener_coordinates_mm")
    coordinates: tuple[tuple[float, float], ...] = ()
    if isinstance(raw_coordinates, list):
        parsed: list[tuple[float, float]] = []
        for raw_point in raw_coordinates:
            if (
                isinstance(raw_point, list | tuple)
                and len(raw_point) == 2
                and all(
                    isinstance(value, int | float) and not isinstance(value, bool)
                    for value in raw_point
                )
            ):
                parsed.append((float(raw_point[0]), float(raw_point[1])))
        coordinates = tuple(parsed)
    if fasteners_per_interface is not None and len(coordinates) != fasteners_per_interface:
        blockers.append(
            "Gusset fastener coordinates must describe one complete member interface."
        )

    numeric_fixture_facts = {
        key: _numeric_fact(properties, key)
        for key in (
            "connected_sheet_hole_diameter_mm",
            "minimum_bolt_spacing_mm",
            "minimum_sheet_edge_distance_mm",
            "maximum_connection_slip_mm",
            "fixture_thickness_mm",
            "fixture_yield_strength_MPa",
            "fixture_tensile_strength_MPa",
        )
    }
    if any(value is None for value in numeric_fixture_facts.values()):
        blockers.append("Gusset plate, hole, spacing, edge, and slip facts are incomplete.")
    if properties.get("connected_sheet_hole_type") != "standard_round":
        blockers.append("Gusset calculation requires standard round holes.")

    numeric_fastener_facts = {
        key: _numeric_fact(fastener_properties, key)
        for key in (
            "nominal_diameter_mm",
            "bolt_tensile_strength_MPa",
            "bolt_minor_area_mm2",
        )
    }
    washers = fastener_properties.get("washers_under_head_and_nut")
    if any(value is None for value in numeric_fastener_facts.values()) or not isinstance(
        washers, bool
    ):
        blockers.append("Gusset bolt strength and washer facts are incomplete.")

    connected_component_ids = {
        connection.from_component_id,
        connection.to_component_id,
    }
    sections = {section.id: section for section in analysis.sections}
    materials = {material.id: material for material in analysis.materials}
    sheet_facts_by_component: dict[str, tuple[Any, Any, float]] = {}
    physical_length_by_component_m: dict[str, float] = defaultdict(float)
    for declaration in analysis.members:
        if (
            declaration.component_id not in connected_component_ids
            or getattr(declaration, "analytical_role", "physical") != "physical"
        ):
            continue
        section = sections.get(declaration.section_id)
        material = materials.get(declaration.material_id)
        thickness_mm = _section_thickness_mm(section) if section is not None else None
        if (
            section is None
            or thickness_mm is None
            or material is None
            or material.yield_strength_MPa is None
            or material.tensile_strength_MPa is None
        ):
            blockers.append(
                f"Connected member {declaration.component_id!r} lacks verified sheet/material facts."
            )
            continue
        physical_length_by_component_m[declaration.component_id] += _length(
            declaration.start,
            declaration.end,
        )
        sheet_facts_by_component.setdefault(
            declaration.component_id,
            (declaration, material, thickness_mm),
        )
    sheet_facts = list(sheet_facts_by_component.values())
    if len(sheet_facts) != 2:
        blockers.append("Two-sided gusset requires exactly two connected member sheets.")

    common: _CalculatedConnectionResistance = {
        "status": "unsupported",
        "evidence_status": "candidate",
        "pack_id": "as_nzs_4600_2005_a1_fabricated_gusset",
        "pack_version": str(properties.get("connection_capacity_pack_version") or "1"),
        "design_force_capacity_kN": None,
        "design_moment_capacity_kNm": None,
        "governing_utilisation": None,
        "stiffness_status": "unverified",
        "stiffness_basis": "The plate's complete bearing-engaged rotational path is not verified.",
        "source": source,
        "source_sha256": source_sha256,
        "basis": "The fabricated gusset was identified but required calculation facts are missing.",
        "blockers": sorted(set(blockers)),
    }
    if blockers:
        return common

    assert fasteners_per_interface is not None
    assert first_fastener is not None
    assert isinstance(washers, bool)
    assert all(value is not None for value in numeric_fixture_facts.values())
    assert all(value is not None for value in numeric_fastener_facts.values())
    hole_diameter_mm = cast(float, numeric_fixture_facts["connected_sheet_hole_diameter_mm"])
    minimum_spacing_mm = cast(float, numeric_fixture_facts["minimum_bolt_spacing_mm"])
    minimum_edge_distance_mm = cast(
        float, numeric_fixture_facts["minimum_sheet_edge_distance_mm"]
    )
    maximum_connection_slip_mm = cast(
        float, numeric_fixture_facts["maximum_connection_slip_mm"]
    )
    try:
        interface_results = [
            as_nzs_4600_2005_a1_bolted_sheet_interface(
                bolt_count=fasteners_per_interface,
                resultant_shear_demand_kN=0.0,
                nominal_bolt_diameter_mm=cast(
                    float, numeric_fastener_facts["nominal_diameter_mm"]
                ),
                bolt_tensile_strength_MPa=cast(
                    float, numeric_fastener_facts["bolt_tensile_strength_MPa"]
                ),
                bolt_minor_area_mm2=cast(
                    float, numeric_fastener_facts["bolt_minor_area_mm2"]
                ),
                connected_sheet_thickness_mm=thickness_mm,
                connected_sheet_yield_strength_MPa=fy_MPa,
                connected_sheet_tensile_strength_MPa=fu_MPa,
                hole_diameter_mm=hole_diameter_mm,
                hole_type="standard_round",
                minimum_spacing_mm=minimum_spacing_mm,
                minimum_edge_distance_mm=minimum_edge_distance_mm,
                washers_under_head_and_nut=washers,
            )
            for _declaration, material, thickness_mm in sheet_facts
            for fy_MPa, fu_MPa in (
                (material.yield_strength_MPa, material.tensile_strength_MPa),
            )
        ]
    except CapacityPackError as exc:
        common.update(
            basis=f"A connected Cee-sheet interface could not be evaluated: {exc}",
            blockers=[
                "No resistance was inferred after the connected-sheet capacity pack rejected its inputs."
            ],
        )
        return common
    fixture_thickness_mm = cast(
        float, numeric_fixture_facts["fixture_thickness_mm"]
    )
    fixture_yield_strength_MPa = cast(
        float, numeric_fixture_facts["fixture_yield_strength_MPa"]
    )
    fixture_tensile_strength_MPa = cast(
        float, numeric_fixture_facts["fixture_tensile_strength_MPa"]
    )
    nominal_bolt_diameter_mm = cast(
        float, numeric_fastener_facts["nominal_diameter_mm"]
    )
    maximum_standard_hole_mm = nominal_bolt_diameter_mm + (
        1.0 if nominal_bolt_diameter_mm < 12.0 else 2.0
    )
    required_spacing_mm = 3.0 * nominal_bolt_diameter_mm
    required_edge_distance_mm = 1.5 * nominal_bolt_diameter_mm
    fixture_geometry_status: Literal["pass", "fail"] = (
        "pass"
        if hole_diameter_mm <= maximum_standard_hole_mm
        and minimum_spacing_mm >= required_spacing_mm
        and minimum_edge_distance_mm >= required_edge_distance_mm
        else "fail"
    )
    fixture_bearing_factor = (
        3.0
        if nominal_bolt_diameter_mm / fixture_thickness_mm < 10.0
        else max(
            1.8,
            4.0 - 0.1 * nominal_bolt_diameter_mm / fixture_thickness_mm,
        )
    )
    fixture_bearing_capacity_kN = (
        fasteners_per_interface
        * 0.60
        * fixture_bearing_factor
        * nominal_bolt_diameter_mm
        * fixture_thickness_mm
        * fixture_tensile_strength_MPa
        / 1000.0
    )
    fixture_tearout_phi = (
        0.70
        if fixture_tensile_strength_MPa / fixture_yield_strength_MPa >= 1.08
        else 0.60
    )
    fixture_tearout_capacity_kN = (
        fasteners_per_interface
        * fixture_tearout_phi
        * fixture_thickness_mm
        * minimum_edge_distance_mm
        * fixture_tensile_strength_MPa
        / 1000.0
    )
    # The exact four-bolt interface has two columns.  Reconstruct the minimum
    # effective transverse strip from two authored edge distances plus the
    # minimum bolt pitch, then remove the two holes for net-section fracture.
    fixture_gross_width_mm = 2.0 * minimum_edge_distance_mm + minimum_spacing_mm
    fixture_net_width_mm = fixture_gross_width_mm - 2.0 * hole_diameter_mm
    if fixture_net_width_mm <= 0.0:
        common.update(
            basis="The specified gusset layout leaves no positive net plate width.",
            blockers=[
                "Increase the plate width/edge distances or reduce the hole layout before assigning resistance."
            ],
        )
        return common
    fixture_gross_yield_capacity_kN = (
        0.90
        * fixture_yield_strength_MPa
        * fixture_thickness_mm
        * fixture_gross_width_mm
        / 1000.0
    )
    fixture_net_fracture_capacity_kN = (
        0.75
        * fixture_tensile_strength_MPa
        * fixture_thickness_mm
        * fixture_net_width_mm
        / 1000.0
    )
    fixture_shear_capacity_kN = (
        0.90
        * 0.60
        * fixture_yield_strength_MPa
        * fixture_thickness_mm
        * fixture_gross_width_mm
        / 1000.0
    )
    fixture_group_capacity_kN = min(
        fixture_bearing_capacity_kN,
        fixture_tearout_capacity_kN,
        fixture_gross_yield_capacity_kN,
        fixture_net_fracture_capacity_kN,
        fixture_shear_capacity_kN,
    )
    fixture_utilisation = resultant_force_demand_kN / fixture_group_capacity_kN
    fixture_status: Literal["pass", "fail"] = (
        "pass"
        if fixture_geometry_status == "pass" and fixture_utilisation <= 1.0
        else "fail"
    )
    single_fastener_capacity_kN = min(
        *[
            result.governing_capacity_kN / result.bolt_count
            for result in interface_results
        ],
        fixture_group_capacity_kN / fasteners_per_interface,
    )
    group = as_nzs_4600_2005_a1_eccentric_fastener_group(
        fastener_coordinates_mm=coordinates,
        design_single_fastener_capacity_kN=single_fastener_capacity_kN,
        resultant_force_demand_kN=resultant_force_demand_kN,
        moment_demand_kNm=moment_demand_kNm,
    )
    fastener_secant_stiffness_kN_mm = (
        single_fastener_capacity_kN / maximum_connection_slip_mm
    )
    rotational_stiffness_kNm_rad = (
        fastener_secant_stiffness_kN_mm
        * sum(x_mm**2 + y_mm**2 for x_mm, y_mm in coordinates)
        / 1000.0
    )
    member_characteristic_stiffnesses = [
        material.elastic_modulus_kN_m2
        * max(
            sections[declaration.section_id].iy_m4,
            sections[declaration.section_id].iz_m4,
        )
        / physical_length_by_component_m[declaration.component_id]
        for declaration, material, _thickness_mm in sheet_facts
    ]
    required_rotational_stiffness_kNm_rad = max(
        member_characteristic_stiffnesses
    )
    stiffness_verified = (
        rotational_stiffness_kNm_rad >= required_rotational_stiffness_kNm_rad
        and all(result.status == "pass" for result in interface_results)
        and fixture_status == "pass"
    )
    result_blockers: list[str] = []
    if group.status != "pass":
        result_blockers.append(
            "The eccentric bolt group demand exceeds its calculated design resistance."
        )
    if fixture_status != "pass":
        result_blockers.append(
            "The 3 mm plate fails its hole/spacing/edge geometry or calculated plate resistance check."
        )
    if not stiffness_verified:
        result_blockers.append(
            "Calculated bearing-engaged rotational stiffness is below the connected-member EI/L characteristic stiffness."
        )
    common.update(
        status=(
            "pass"
            if group.status == "pass"
            and fixture_status == "pass"
            and stiffness_verified
            else "fail"
        ),
        evidence_status="verified",
        design_force_capacity_kN=group.design_force_capacity_kN,
        design_moment_capacity_kNm=group.design_moment_capacity_kNm,
        governing_utilisation=max(
            group.interaction_utilisation,
            fixture_utilisation,
            required_rotational_stiffness_kNm_rad / rotational_stiffness_kNm_rad,
        ),
        stiffness_status="verified" if stiffness_verified else "unverified",
        stiffness_basis=(
            "Bearing-engaged secant rotational stiffness is calculated from the "
            "weakest fastener/interface resistance, declared maximum slip, and exact "
            f"bolt radii: {rotational_stiffness_kNm_rad:.3f} kNm/rad versus the "
            "largest connected-member EI/L characteristic stiffness of "
            f"{required_rotational_stiffness_kNm_rad:.3f} kNm/rad."
        ),
        basis=(
            f"{group.basis} Each four-bolt Cee interface is checked to AS/NZS 4600 "
            "for bolt shear, sheet bearing, tear-out, holes, spacing and edges. The "
            "specified 3 mm plate is checked separately for bearing, tear-out, gross "
            "yield, net fracture and shear using its exact two-column layout; the "
            "weakest per-fastener-equivalent resistance governs the eccentric group."
        ),
        blockers=result_blockers,
    )
    return common


def _calculated_anchored_fixture_resistance(
    *,
    connection: DesignConnection,
    components: Mapping[str, DesignComponent],
    anchor_group: AnchorGroupCheck | None,
    bolted_sheet_interface: BoltedSheetInterfaceCheck | None,
    resultant_force_demand_kN: float,
    moment_demand_kNm: float,
) -> _CalculatedConnectionResistance | None:
    """Close a pinned steel-fixture path using already-checked bolts and anchors."""

    fixtures = [
        components[component_id]
        for component_id in connection.connector_component_ids
        if component_id in components
        and components[component_id].structural_properties.get(
            "anchored_fixture_capacity_pack_id"
        )
        == "specified_grade_pinned_steel_fixture"
    ]
    if not fixtures:
        return None
    fixture = fixtures[0]
    properties = fixture.structural_properties
    blockers: list[str] = []
    if len(fixtures) != 1:
        blockers.append("Anchored fixture pack requires exactly one steel fixture.")
    if (
        fixture.structural_evidence_status != "verified"
        or fixture.product_key is None
        or fixture.product_definition_digest is None
    ):
        blockers.append("Anchored fixture requires a verified managed product identity.")
    if anchor_group is None:
        blockers.append("Anchored fixture has no exact anchor-group calculation.")
    elif any(
        value is None
        for value in (
            anchor_group.tension_capacity_kN,
            anchor_group.shear_capacity_kN,
            anchor_group.interaction_utilisation,
        )
    ):
        blockers.append("Anchored fixture anchor-group resistance is incomplete.")
    if bolted_sheet_interface is None:
        blockers.append("Anchored fixture has no exact connected-sheet bolt calculation.")
    elif any(
        value is None
        for value in (
            bolted_sheet_interface.governing_capacity_kN,
            bolted_sheet_interface.governing_utilisation,
        )
    ):
        blockers.append("Anchored fixture connected-sheet resistance is incomplete.")
    if moment_demand_kNm > 1e-9:
        blockers.append("Pinned anchored fixture cannot be credited with moment transfer.")

    numeric_facts = {
        key: _numeric_fact(properties, key)
        for key in (
            "anchored_fixture_thickness_mm",
            "anchored_fixture_effective_width_mm",
            "anchored_fixture_yield_strength_MPa",
            "anchored_fixture_tensile_strength_MPa",
        )
    }
    if any(value is None for value in numeric_facts.values()):
        blockers.append("Anchored fixture specified-grade plate facts are incomplete.")
    source = str(properties["source"]) if properties.get("source") else None
    source_sha256 = (
        str(properties["source_sha256"])
        if properties.get("source_sha256")
        else None
    )
    if source is None or source_sha256 is None or len(source_sha256) != 64:
        blockers.append("Anchored fixture requires a source and valid SHA-256.")
        source_sha256 = None

    common: _CalculatedConnectionResistance = {
        "status": "unsupported",
        "evidence_status": "candidate",
        "pack_id": "specified_grade_pinned_steel_fixture",
        "pack_version": str(
            properties.get("anchored_fixture_capacity_pack_version") or "1"
        ),
        "design_force_capacity_kN": None,
        "design_moment_capacity_kNm": None,
        "governing_utilisation": None,
        "stiffness_status": "unverified",
        "stiffness_basis": "Pinned fixture translational force path is incomplete.",
        "source": source,
        "source_sha256": source_sha256,
        "basis": "The fixture was identified but its complete steel-to-anchor path is incomplete.",
        "blockers": sorted(set(blockers)),
    }
    if blockers:
        return common

    assert anchor_group is not None
    assert bolted_sheet_interface is not None
    assert anchor_group.tension_capacity_kN is not None
    assert anchor_group.shear_capacity_kN is not None
    assert anchor_group.interaction_utilisation is not None
    assert bolted_sheet_interface.governing_capacity_kN is not None
    assert bolted_sheet_interface.governing_utilisation is not None
    thickness_mm = cast(float, numeric_facts["anchored_fixture_thickness_mm"])
    effective_width_mm = cast(
        float, numeric_facts["anchored_fixture_effective_width_mm"]
    )
    fy_MPa = cast(float, numeric_facts["anchored_fixture_yield_strength_MPa"])
    fu_MPa = cast(float, numeric_facts["anchored_fixture_tensile_strength_MPa"])
    gross_area_mm2 = thickness_mm * effective_width_mm
    plate_tension_capacity_kN = 0.90 * fy_MPa * gross_area_mm2 / 1000.0
    plate_shear_capacity_kN = 0.90 * 0.60 * fy_MPa * gross_area_mm2 / 1000.0
    plate_fracture_capacity_kN = 0.75 * fu_MPa * gross_area_mm2 / 1000.0
    design_force_capacity_kN = min(
        bolted_sheet_interface.governing_capacity_kN,
        plate_tension_capacity_kN,
        plate_shear_capacity_kN,
        plate_fracture_capacity_kN,
    )
    governing_utilisation = max(
        anchor_group.interaction_utilisation,
        bolted_sheet_interface.governing_utilisation,
        resultant_force_demand_kN / design_force_capacity_kN,
    )
    path_passes = (
        anchor_group.status == "pass"
        and anchor_group.evidence_status == "verified"
        and bolted_sheet_interface.status == "pass"
        and bolted_sheet_interface.evidence_status == "verified"
        and governing_utilisation <= 1.0
    )
    common.update(
        status="pass" if path_passes else "fail",
        evidence_status="verified",
        design_force_capacity_kN=design_force_capacity_kN,
        governing_utilisation=governing_utilisation,
        stiffness_status="verified" if path_passes else "unverified",
        stiffness_basis=(
            "The joint is deliberately pinned. Its translational path is verified "
            "through the rendered Cee bolts, specified-grade steel fixture, and the "
            "existing exact anchor-group result; no rotational fixity is claimed."
        ),
        basis=(
            "Complete pinned fixture: the manufacturer anchor interaction is checked "
            "in its actual substrate-normal tension and in-plane shear directions. "
            "The member-to-fixture force path is checked separately using the AS/NZS "
            "4600 connected-sheet bolt result plus gross specified-grade plate yield, "
            "0.6fy shear yield, and gross fracture resistance. Compressive member force "
            "is not incorrectly compared with anchor pull-out resistance."
        ),
        blockers=[],
    )
    return common


def _calculated_direct_anchored_sheet_resistance(
    *,
    connection: DesignConnection,
    components: Mapping[str, DesignComponent],
    analysis,
    anchor_group: AnchorGroupCheck | None,
    resultant_force_demand_kN: float,
    moment_demand_kNm: float,
) -> _CalculatedConnectionResistance | None:
    """Check a direct masonry anchor through one cold-formed member web."""

    anchors = [
        components[component_id]
        for component_id in connection.connector_component_ids
        if component_id in components
        and components[component_id].structural_properties.get(
            "anchor_resistance_pack_id"
        )
        == "manufacturer_working_load_anchor_group"
    ]
    if (
        len(anchors) != 1
        or len(anchors) != len(connection.connector_component_ids)
    ):
        return None
    blockers: list[str] = []
    anchor = anchors[0]
    properties = anchor.structural_properties
    connected_component_ids = {
        connection.from_component_id,
        connection.to_component_id,
    }
    declarations = [
        declaration
        for declaration in analysis.members
        if declaration.component_id in connected_component_ids
        and getattr(declaration, "analytical_role", "physical") == "physical"
    ]
    component_ids = {declaration.component_id for declaration in declarations}
    declaration = declarations[0] if declarations else None
    if declaration is None or len(component_ids) != 1:
        blockers.append(
            "Direct anchored-sheet pack requires one connected physical steel member."
        )
    sections = {
        section.id: section for section in getattr(analysis, "sections", ())
    }
    materials = {
        material.id: material for material in getattr(analysis, "materials", ())
    }
    section = (
        sections.get(getattr(declaration, "section_id", ""))
        if declaration is not None
        else None
    )
    material = (
        materials.get(getattr(declaration, "material_id", ""))
        if declaration is not None
        else None
    )
    thickness_mm = _section_thickness_mm(section) if section is not None else None
    fy_MPa = material.yield_strength_MPa if material is not None else None
    fu_MPa = material.tensile_strength_MPa if material is not None else None
    diameter_mm = _numeric_fact(properties, "anchor_nominal_diameter_mm")
    hole_diameter_mm = _numeric_fact(anchor.fabrication, "sheet_hole_diameter_mm")
    edge_distance_mm = _numeric_fact(
        anchor.fabrication, "minimum_sheet_edge_distance_mm"
    )
    spacing_mm = _numeric_fact(anchor.fabrication, "minimum_sheet_spacing_mm")
    hole_type = str(anchor.fabrication.get("sheet_hole_type") or "")
    if any(
        value is None
        for value in (
            thickness_mm,
            fy_MPa,
            fu_MPa,
            diameter_mm,
            hole_diameter_mm,
            edge_distance_mm,
            spacing_mm,
        )
    ):
        blockers.append(
            "Direct anchored-sheet pack requires section strength/thickness and "
            "installed diameter, round-hole, spacing, and sheet-edge facts."
        )
    if hole_type != "standard_round":
        blockers.append("Direct anchored-sheet pack requires a standard round hole.")
    if anchor_group is None:
        blockers.append("Direct anchored-sheet pack has no anchor-group result.")
    if moment_demand_kNm > 1e-9:
        blockers.append("A single direct anchor cannot be credited with moment transfer.")
    source = str(properties.get("anchor_source") or "") or None
    source_sha256 = str(properties.get("anchor_source_sha256") or "") or None
    if source is None or source_sha256 is None or len(source_sha256) != 64:
        blockers.append("Direct anchor requires a pinned manufacturer source.")
        source_sha256 = None

    common: _CalculatedConnectionResistance = {
        "status": "unsupported",
        "evidence_status": "candidate",
        "pack_id": "as_nzs_4600_2005_a1_direct_anchored_sheet",
        "pack_version": "1",
        "design_force_capacity_kN": None,
        "design_moment_capacity_kNm": None,
        "governing_utilisation": None,
        "stiffness_status": "unverified",
        "stiffness_basis": "The direct anchor-to-sheet path is incomplete.",
        "source": source,
        "source_sha256": source_sha256,
        "basis": (
            "Direct masonry anchor through a cold-formed web: anchor interaction, "
            "AS/NZS 4600 Clause 5.3 sheet bearing/tear-out, and installed geometry."
        ),
        "blockers": sorted(set(blockers)),
    }
    if blockers:
        return common

    assert anchor_group is not None
    assert anchor_group.shear_capacity_kN is not None
    assert anchor_group.interaction_utilisation is not None
    assert thickness_mm is not None
    assert fy_MPa is not None
    assert fu_MPa is not None
    assert diameter_mm is not None
    assert hole_diameter_mm is not None
    assert edge_distance_mm is not None
    assert spacing_mm is not None
    required_spacing_mm = 3.0 * diameter_mm
    required_edge_distance_mm = 1.5 * diameter_mm
    maximum_hole_mm = diameter_mm + (1.0 if diameter_mm < 12.0 else 2.0)
    geometry_passes = (
        hole_diameter_mm <= maximum_hole_mm
        and spacing_mm >= required_spacing_mm
        and edge_distance_mm >= required_edge_distance_mm
    )
    diameter_thickness_ratio = diameter_mm / thickness_mm
    bearing_factor = (
        3.0
        if diameter_thickness_ratio < 10.0
        else 4.0 - 0.1 * diameter_thickness_ratio
        if diameter_thickness_ratio <= 22.0
        else 1.8
    )
    # No washer enhancement is assumed for the rendered hex flange head.
    sheet_bearing_capacity_kN = (
        0.60 * 0.75 * bearing_factor * diameter_mm * thickness_mm * fu_MPa / 1000.0
    )
    tearout_phi = 0.70 if fu_MPa / fy_MPa >= 1.08 else 0.60
    sheet_tearout_capacity_kN = (
        tearout_phi * thickness_mm * edge_distance_mm * fu_MPa / 1000.0
    )
    design_force_capacity_kN = min(
        anchor_group.shear_capacity_kN,
        sheet_bearing_capacity_kN,
        sheet_tearout_capacity_kN,
    )
    governing_utilisation = max(
        anchor_group.interaction_utilisation,
        resultant_force_demand_kN / design_force_capacity_kN,
    )
    path_passes = (
        anchor_group.status == "pass"
        and anchor_group.evidence_status == "verified"
        and anchor.structural_evidence_status == "verified"
        and geometry_passes
        and governing_utilisation <= 1.0
    )
    common.update(
        status="pass" if path_passes else "fail",
        evidence_status="verified",
        design_force_capacity_kN=design_force_capacity_kN,
        governing_utilisation=governing_utilisation,
        stiffness_status="verified" if path_passes else "unverified",
        stiffness_basis=(
            "The deliberately pinned translational path is verified through the "
            "rendered anchor, round Cee web hole, sheet bearing/tear-out, and the "
            "manufacturer anchor-group result; no rotational fixity is claimed."
        ),
        blockers=[],
    )
    return common


def _connection_checks(
    model,
    analysis,
    connections: Sequence[DesignConnection],
    components: dict[str, DesignComponent],
    tension_checks: Sequence[TensionMemberCheck] = (),
) -> list[ConnectionCheck]:
    """Envelope joint actions and resolve exact available resistance evidence.

    Explicit reusable connection packs remain authoritative. A tension-only
    member end may also use the Tertius-owned AS/NZS 4600 screw calculation
    already produced for its exact rendered strap, support, and fasteners. No
    other resistance is inferred from geometry or topology.
    """

    ultimate_combinations = [
        combination
        for combination in analysis.load_combinations
        if combination.limit_state == "ultimate" and combination.purpose == "design"
    ]
    member_endpoints_by_connection: dict[
        str, list[tuple[AnalyticalMemberDeclaration, float]]
    ] = {}
    for declaration in analysis.members:
        member_length = _length(declaration.start, declaration.end)
        for node_key, distance_m in (
            (declaration.start_node_key, 0.0),
            (declaration.end_node_key, member_length),
        ):
            for connection_id in _node_key_connection_ids(node_key):
                member_endpoints_by_connection.setdefault(connection_id, []).append(
                    (declaration, distance_m)
                )
    tension_checks_by_member = {check.member_id: check for check in tension_checks}
    tension_checks_by_component: dict[str, TensionMemberCheck] = {}
    tension_checks_by_connection: dict[str, TensionMemberCheck] = {}
    for declaration in analysis.members:
        if not declaration.tension_only:
            continue
        tension_check = tension_checks_by_member.get(declaration.id)
        if tension_check is None:
            continue
        tension_checks_by_component[declaration.component_id] = tension_check
        for node_key in (declaration.start_node_key, declaration.end_node_key):
            for connection_id in _node_key_connection_ids(node_key):
                tension_checks_by_connection[connection_id] = tension_check

    # Project-local component builders are authoritative for physical incidence.
    # Retain node-key discovery for analytical joint models, then fill any brace
    # ends whose shared node was authored by component incidence instead.
    for connection in connections:
        incident_tension_checks = {
            tension_checks_by_component[component_id].member_id: (
                tension_checks_by_component[component_id]
            )
            for component_id in {
                connection.from_component_id,
                connection.to_component_id,
            }
            if component_id in tension_checks_by_component
        }
        if len(incident_tension_checks) == 1:
            tension_checks_by_connection.setdefault(
                connection.id,
                next(iter(incident_tension_checks.values())),
            )

    checks: list[ConnectionCheck] = []
    for connection in connections:
        evidence = connection.resistance
        tension_evidence = (
            tension_checks_by_connection.get(connection.id)
            if evidence is None and set(connection.transfers) <= {"force"}
            else None
        )
        expected_parts = (
            sorted(evidence.connector_part_numbers)
            if evidence is not None
            else sorted(
                components[component_id].part_number
                or f"<missing:{component_id}>"
                for component_id in connection.connector_component_ids
            )
            if tension_evidence is not None
            else []
        )
        rendered_parts = sorted(
            components[component_id].part_number or f"<missing:{component_id}>"
            for component_id in connection.connector_component_ids
        )
        identity_mismatches = (
            []
            if (evidence is None and tension_evidence is None)
            or rendered_parts == expected_parts
            else [
                "connector part-number multiset expected "
                f"{expected_parts!r}, rendered {rendered_parts!r}"
            ]
        )
        axial_demand = 0.0
        anchor_tension_demand = 0.0
        shear_demand = 0.0
        bolted_sheet_demand = 0.0
        moment_demand = 0.0
        governing_combination_id: str | None = None
        governing_member_id: str | None = None
        governing_resultant = -1.0
        connection_component_ids = {
            connection.from_component_id,
            connection.to_component_id,
        }
        boundary_component_ids = {
            component_id
            for component_id, port_name in connection.component_ports.items()
            if port_name in {"start", "end"}
        }
        demand_component_ids = boundary_component_ids or connection_component_ids
        for declaration, distance_m in member_endpoints_by_connection.get(
            connection.id, []
        ):
            if declaration.component_id not in demand_component_ids:
                continue
            member = model.members[declaration.id]
            for combination in ultimate_combinations:
                signed_endpoint_axial = (
                    member.axial(distance_m, combination.id)
                    if "force" in connection.transfers
                    else 0.0
                )
                endpoint_axial = abs(signed_endpoint_axial)
                # PyNite reports member compression as positive and tension as
                # negative. Only uplift/tension is resisted by the anchor
                # group; foundation bearing carries compression.
                anchor_tension_demand = max(
                    anchor_tension_demand,
                    max(0.0, -signed_endpoint_axial),
                )
                endpoint_shear = (
                    sqrt(
                        member.shear("Fy", distance_m, combination.id) ** 2
                        + member.shear("Fz", distance_m, combination.id) ** 2
                    )
                    if "shear" in connection.transfers
                    else 0.0
                )
                endpoint_moment = (
                    sqrt(
                        member.moment("My", distance_m, combination.id) ** 2
                        + member.moment("Mz", distance_m, combination.id) ** 2
                    )
                    if "moment" in connection.transfers
                    else 0.0
                )
                axial_demand = max(axial_demand, endpoint_axial)
                shear_demand = max(shear_demand, endpoint_shear)
                bolted_sheet_demand = max(
                    bolted_sheet_demand,
                    sqrt(endpoint_axial**2 + endpoint_shear**2),
                )
                moment_demand = max(moment_demand, endpoint_moment)
                endpoint_resultant = sqrt(
                    endpoint_axial**2 + endpoint_shear**2 + endpoint_moment**2
                )
                if endpoint_resultant > governing_resultant:
                    governing_resultant = endpoint_resultant
                    governing_combination_id = combination.id
                    governing_member_id = declaration.id

        # A tension-only analytical member carries the same axial force to both
        # rendered end layouts. This also preserves the verified demand when a
        # project-local builder registered incidence without a joint node key.
        if tension_evidence is not None:
            axial_demand = max(axial_demand, tension_evidence.tension_demand_kN)
            tension_resultant = tension_evidence.tension_demand_kN
            if tension_resultant >= governing_resultant:
                governing_resultant = tension_resultant
                governing_combination_id = (
                    tension_evidence.governing_combination_id
                )
                governing_member_id = tension_evidence.member_id

        grounded_component_ids = sorted(
            component_id
            for component_id in {
                connection.from_component_id,
                connection.to_component_id,
            }
            if components.get(component_id) is not None
            and components[component_id].grounded
        )
        anchor_group = _anchor_group_check(
            connection=connection,
            components=components,
            grounded_component_ids=grounded_component_ids,
            tension_demand_kN=anchor_tension_demand,
            shear_demand_kN=shear_demand,
        )
        bolted_sheet_interface = _bolted_sheet_interface_check(
            connection=connection,
            components=components,
            analysis=analysis,
            resultant_shear_demand_kN=bolted_sheet_demand,
        )
        calculated_cleat = _calculated_bolted_cleat_resistance(
            connection=connection,
            components=components,
            analysis=analysis,
            resultant_force_demand_kN=bolted_sheet_demand,
            moment_demand_kNm=moment_demand,
        )
        calculated_screw = _calculated_screw_connection_resistance(
            connection=connection,
            components=components,
            analysis=analysis,
            resultant_force_demand_kN=bolted_sheet_demand,
            moment_demand_kNm=moment_demand,
        )
        calculated_gusset = _calculated_fabricated_gusset_resistance(
            connection=connection,
            components=components,
            analysis=analysis,
            resultant_force_demand_kN=bolted_sheet_demand,
            moment_demand_kNm=moment_demand,
        )
        calculated_anchored_fixture = _calculated_anchored_fixture_resistance(
            connection=connection,
            components=components,
            anchor_group=anchor_group,
            bolted_sheet_interface=bolted_sheet_interface,
            resultant_force_demand_kN=bolted_sheet_demand,
            moment_demand_kNm=moment_demand,
        )
        calculated_direct_anchor = _calculated_direct_anchored_sheet_resistance(
            connection=connection,
            components=components,
            analysis=analysis,
            anchor_group=anchor_group,
            resultant_force_demand_kN=bolted_sheet_demand,
            moment_demand_kNm=moment_demand,
        )
        calculated_connection = _select_calculated_connection_resistance(
            grounded=bool(grounded_component_ids),
            anchored_fixture=calculated_anchored_fixture,
            direct_anchor=calculated_direct_anchor,
            cleat=calculated_cleat,
            screw=calculated_screw,
            gusset=calculated_gusset,
        )
        if (
            anchor_group is not None
            or bolted_sheet_interface is not None
            or calculated_connection is not None
        ) and evidence is None and tension_evidence is None:
            expected_parts = rendered_parts
            identity_mismatches = []

        design_axial_capacity_kN = (
            evidence.design_axial_capacity_kN
            if evidence is not None
            else calculated_connection["design_force_capacity_kN"]
            if calculated_connection is not None
            else tension_evidence.end_connection_capacity_kN
            if tension_evidence is not None
            else None
        )
        design_shear_capacity_kN = (
            evidence.design_shear_capacity_kN
            if evidence is not None
            else calculated_connection["design_force_capacity_kN"]
            if calculated_connection is not None
            else None
        )
        design_moment_capacity_kNm = (
            evidence.design_moment_capacity_kNm
            if evidence is not None
            else calculated_connection["design_moment_capacity_kNm"]
            if calculated_connection is not None
            else None
        )
        axial_utilisation = (
            axial_demand / design_axial_capacity_kN
            if design_axial_capacity_kN is not None
            else None
        )
        shear_utilisation = (
            shear_demand / design_shear_capacity_kN
            if design_shear_capacity_kN is not None
            else None
        )
        moment_utilisation = (
            moment_demand / design_moment_capacity_kNm
            if design_moment_capacity_kNm is not None
            else None
        )
        relevant_utilisations = [
            utilisation
            for transfer, utilisation in (
                ("force", axial_utilisation),
                ("shear", shear_utilisation),
                ("moment", moment_utilisation),
            )
            if transfer in connection.transfers and utilisation is not None
        ]
        governing_utilisation = (
            max(relevant_utilisations) if relevant_utilisations else None
        )
        if calculated_connection is not None:
            governing_utilisation = calculated_connection["governing_utilisation"]
        if not ultimate_combinations:
            status: Literal["pass", "fail", "not_checked", "unsupported"] = (
                "not_checked"
            )
        elif evidence is not None and (
            evidence.status != "verified" or identity_mismatches
        ):
            status = "unsupported"
        elif tension_evidence is not None:
            if tension_evidence.fastener_shear_qualification_status == "fail":
                status = "fail"
            elif (
                tension_evidence.connection_capacity_status != "verified"
                or identity_mismatches
                or governing_utilisation is None
            ):
                status = "unsupported"
            else:
                status = "pass" if governing_utilisation <= 1.0 else "fail"
        elif calculated_connection is not None:
            status = calculated_connection["status"]
        elif (
            anchor_group is not None and anchor_group.status == "fail"
        ) or (
            bolted_sheet_interface is not None
            and bolted_sheet_interface.status == "fail"
        ):
            status = "fail"
        elif anchor_group is not None or bolted_sheet_interface is not None:
            # An anchor failure is a real connection failure. A passing anchor
            # or bolted-sheet group does not by itself prove the fixture/bracket
            # and foundation member resistance.
            status = "unsupported"
        elif evidence is None:
            status = "unsupported"
        else:
            status = (
                "pass"
                if governing_utilisation is not None and governing_utilisation <= 1.0
                else "fail"
            )
        assumptions = (
            list(evidence.assumptions)
            if evidence is not None
            else list(tension_evidence.assumptions)
            if tension_evidence is not None
            else []
        )
        if calculated_connection is not None:
            assumptions.extend(calculated_connection["blockers"])
            assumptions.append(calculated_connection["basis"])
        if anchor_group is not None:
            assumptions.extend(anchor_group.blockers)
            assumptions.append(
                "Anchor resistance is checked separately. The complete connection "
                "still requires the rendered fixture/bracket, connected steel "
                "bearing and tear-out, and foundation member limit states."
            )
        if bolted_sheet_interface is not None:
            assumptions.extend(bolted_sheet_interface.blockers)
            assumptions.append(
                "The Cee web, bolt shear, bearing, tear-out, hole, spacing and edge "
                "checks are independent of the fabricated fixture plate and foundation."
            )
        if (
            anchor_group is None
            and bolted_sheet_interface is None
            and evidence is None
            and tension_evidence is None
        ):
            assumptions.append(
                "The rendered connector identity and joint demand are recorded, but "
                "no resistance evidence pack is attached."
            )
            if grounded_component_ids:
                assumptions.append(
                    "This is a foundation connection. Certification requires exact "
                    "anchor resistance, substrate strength and condition, edge and "
                    "spacing checks, combined tension/shear interaction, and the "
                    "foundation concrete or masonry limit states."
                )
        elif evidence is not None and evidence.status != "verified":
            assumptions.append(
                "Demand is calculated, but resistance is not verified and cannot pass."
            )
        checks.append(
            ConnectionCheck(
                connection_id=connection.id,
                label=connection.label,
                status=status,
                evidence_status=(
                    evidence.status
                    if evidence is not None
                    else calculated_connection["evidence_status"]
                    if calculated_connection is not None
                    else "candidate"
                    if anchor_group is not None
                    or bolted_sheet_interface is not None
                    else "verified"
                    if tension_evidence is not None
                    and tension_evidence.connection_capacity_status == "verified"
                    else "candidate"
                    if tension_evidence is not None
                    else "unverified"
                ),
                pack_id=(
                    evidence.pack_id
                    if evidence is not None
                    else calculated_connection["pack_id"]
                    if calculated_connection is not None
                    else anchor_group.pack_id
                    if anchor_group is not None
                    else bolted_sheet_interface.pack_id
                    if bolted_sheet_interface is not None
                    else "as_nzs_4600_2005_a1_tension_end_connection"
                    if tension_evidence is not None
                    else "unverified-rendered-connection"
                ),
                pack_version=(
                    evidence.version
                    if evidence is not None
                    else calculated_connection["pack_version"]
                    if calculated_connection is not None
                    else anchor_group.pack_version
                    if anchor_group is not None
                    else bolted_sheet_interface.pack_version
                    if bolted_sheet_interface is not None
                    else "2005+A1"
                    if tension_evidence is not None
                    else "0"
                ),
                identity_status=(
                    "fail"
                    if identity_mismatches
                    else "pass"
                    if evidence is not None
                    or calculated_connection is not None
                    or tension_evidence is not None
                    or anchor_group is not None
                    or bolted_sheet_interface is not None
                    else "not_declared"
                ),
                identity_mismatches=identity_mismatches,
                governing_combination_id=governing_combination_id,
                governing_member_id=governing_member_id,
                axial_demand_kN=axial_demand,
                shear_demand_kN=shear_demand,
                moment_demand_kNm=moment_demand,
                design_axial_capacity_kN=design_axial_capacity_kN,
                design_shear_capacity_kN=design_shear_capacity_kN,
                design_moment_capacity_kNm=design_moment_capacity_kNm,
                axial_utilisation=axial_utilisation,
                shear_utilisation=shear_utilisation,
                moment_utilisation=moment_utilisation,
                governing_utilisation=governing_utilisation,
                stiffness_status=(
                    calculated_connection["stiffness_status"]
                    if calculated_connection is not None
                    else "verified"
                    if connection.joint_model is not None
                    and connection.joint_model.analysis_model == "pinned"
                    else "unverified"
                ),
                stiffness_basis=(
                    calculated_connection["stiffness_basis"]
                    if calculated_connection is not None
                    else connection.joint_model.stiffness_basis
                    if connection.joint_model is not None
                    else "No physical connection stiffness model is declared."
                ),
                expected_connector_part_numbers=expected_parts,
                rendered_connector_part_numbers=rendered_parts,
                source=(
                    evidence.source
                    if evidence is not None
                    else calculated_connection["source"]
                    if calculated_connection is not None
                    else anchor_group.source
                    if anchor_group is not None
                    else bolted_sheet_interface.source
                    if bolted_sheet_interface is not None
                    else " · ".join(
                        filter(
                            None,
                            (
                                tension_evidence.fastener_evidence_source,
                                tension_evidence.standard_reference,
                            ),
                        )
                    )
                    if tension_evidence is not None
                    else None
                ),
                source_sha256=(
                    evidence.source_sha256
                    if evidence is not None
                    else calculated_connection["source_sha256"]
                    if calculated_connection is not None
                    else anchor_group.source_sha256
                    if anchor_group is not None
                    else bolted_sheet_interface.source_sha256
                    if bolted_sheet_interface is not None
                    else tension_evidence.standard_source_sha256
                    if tension_evidence is not None
                    else None
                ),
                basis=(
                    evidence.basis
                    if evidence is not None
                    else calculated_connection["basis"]
                    if calculated_connection is not None
                    else anchor_group.basis
                    if anchor_group is not None
                    else bolted_sheet_interface.basis
                    if bolted_sheet_interface is not None
                    else tension_evidence.basis
                    if tension_evidence is not None
                    else "ULS endpoint forces are enveloped from the compiled physical "
                    "joint. No resistance, stiffness, bearing, tear-out, pull-out, "
                        "anchor, or foundation capacity is inferred."
                ),
                anchor_group=anchor_group,
                bolted_sheet_interface=bolted_sheet_interface,
                assumptions=assumptions,
            )
        )
    return checks


def _node_key_connection_ids(node_key: str | None) -> set[str]:
    if not node_key or not node_key.startswith("joint:"):
        return set()
    return {
        connection_id
        for connection_id in node_key.removeprefix("joint:").split("+")
        if connection_id
    }


def _section_thickness_mm(section) -> float | None:
    if section.tension_thickness_mm is not None:
        return section.tension_thickness_mm
    if section.catalog is None:
        return None
    value = section.catalog.properties.get(
        "t_mm",
        section.catalog.properties.get("t"),
    )
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if float(value) > 0 else None


def _screw_bearing_factor(diameter_mm: float, thickness_mm: float) -> float:
    ratio = diameter_mm / thickness_mm
    if ratio < 6.0:
        return 2.7
    if ratio <= 13.0:
        return 3.3 - 0.1 * ratio
    return 2.0


def _screw_bearing_nominal_kN(
    *,
    head_sheet_thickness_mm: float,
    head_sheet_fu_MPa: float,
    other_sheet_thickness_mm: float,
    other_sheet_fu_MPa: float,
    diameter_mm: float,
) -> float:
    """AS/NZS 4600:2005 Clause 5.4.2.3 single-shear sheet bearing."""

    t1 = head_sheet_thickness_mm
    t2 = other_sheet_thickness_mm
    c1 = _screw_bearing_factor(diameter_mm, t1)
    c2 = _screw_bearing_factor(diameter_mm, t2)
    low_ratio_capacity = min(
        4.2 * sqrt(t2**3 * diameter_mm) * other_sheet_fu_MPa,
        c1 * t1 * diameter_mm * head_sheet_fu_MPa,
        c2 * t2 * diameter_mm * other_sheet_fu_MPa,
    )
    high_ratio_capacity = min(
        c1 * t1 * diameter_mm * head_sheet_fu_MPa,
        c2 * t2 * diameter_mm * other_sheet_fu_MPa,
    )
    ratio = t2 / t1
    if ratio <= 1.0:
        nominal_n = low_ratio_capacity
    elif ratio >= 2.5:
        nominal_n = high_ratio_capacity
    else:
        interpolation = (ratio - 1.0) / 1.5
        nominal_n = low_ratio_capacity + interpolation * (
            high_ratio_capacity - low_ratio_capacity
        )
    return nominal_n / 1000.0


def _tension_member_supports(
    declaration,
    analysis,
    connections: Sequence[DesignConnection],
) -> list[tuple[Any, Any]]:
    declarations_by_component: dict[str, list[Any]] = {}
    for member in analysis.members:
        declarations_by_component.setdefault(member.component_id, []).append(member)
    sections = {section.id: section for section in analysis.sections}
    materials = {material.id: material for material in analysis.materials}
    supports: list[tuple[Any, Any]] = []
    for connection in connections:
        if not ({"force", "shear"} & set(connection.transfers)):
            continue
        if connection.from_component_id == declaration.component_id:
            other_component_id = connection.to_component_id
        elif connection.to_component_id == declaration.component_id:
            other_component_id = connection.from_component_id
        else:
            continue
        support_declarations = declarations_by_component.get(other_component_id, [])
        if not support_declarations:
            continue
        support = support_declarations[0]
        supports.append(
            (sections[support.section_id], materials[support.material_id])
        )
    return supports


def _tension_member_checks(
    model,
    analysis,
    connections: Sequence[DesignConnection] = (),
    components: dict[str, DesignComponent] | None = None,
) -> list[TensionMemberCheck]:
    """Envelope tension-only members and derive AS/NZS 4600 resistance."""

    ultimate_combinations = [
        combination
        for combination in analysis.load_combinations
        if combination.limit_state == "ultimate" and combination.purpose == "design"
    ]
    checks: list[TensionMemberCheck] = []
    components = components or {}
    sections = {section.id: section for section in analysis.sections}
    materials = {material.id: material for material in analysis.materials}
    for declaration in analysis.members:
        if not declaration.tension_only:
            continue
        member = model.members[declaration.id]
        member_length = _length(declaration.start, declaration.end)
        governing_combination_id: str | None = None
        tension_demand_kN = 0.0
        for combination in ultimate_combinations:
            demand = max(
                abs(member.axial(member_length * index / 8, combination.id))
                for index in range(9)
            )
            if demand > tension_demand_kN:
                tension_demand_kN = demand
                governing_combination_id = combination.id

        section = sections[declaration.section_id]
        material = materials[declaration.material_id]
        pack = None
        pack_error: str | None = None
        try:
            pack = tension_member_capacity(
                "as_nzs_4600_2005_a1_tension",
                section,
                material,
            )
        except CapacityPackError as exc:
            pack_error = str(exc)

        if pack is not None:
            member_capacity_status: Literal[
                "not_checked", "candidate", "verified"
            ] = "verified"
            tension_capacity = pack.design_tension_capacity_kN
        else:
            member_capacity_status = "not_checked"
            tension_capacity = None

        diameter_mm = section.end_fastener_nominal_diameter_mm
        spacing_mm = section.end_fastener_spacing_mm
        end_distance_mm = section.end_fastener_edge_distance_mm
        fastener_count = declaration.end_fastener_count
        endpoint_connection_ids = {
            *(_node_key_connection_ids(declaration.start_node_key)),
            *(_node_key_connection_ids(declaration.end_node_key)),
        }
        rendered_end_connections = [
            connection
            for connection in connections
            if ({"force", "shear"} & set(connection.transfers))
            and declaration.component_id
            in {connection.from_component_id, connection.to_component_id}
            and (
                not endpoint_connection_ids
                or connection.id in endpoint_connection_ids
            )
        ]
        rendered_end_fastener_counts = [
            len(connection.connector_component_ids)
            for connection in rendered_end_connections
        ]
        rendered_fasteners = [
            components[connector_id]
            for connection in rendered_end_connections
            for connector_id in connection.connector_component_ids
            if connector_id in components
        ]
        fastener_part_numbers = sorted(
            {
                component.part_number
                for component in rendered_fasteners
                if component.part_number is not None
            }
        )
        fastener_product_keys = sorted(
            {
                component.product_key
                for component in rendered_fasteners
                if component.product_key is not None
            }
        )
        fastener_product_definition_digests = sorted(
            {
                component.product_definition_digest
                for component in rendered_fasteners
                if component.product_definition_digest is not None
            }
        )
        tested_strength_values = {
            float(value)
            for component in rendered_fasteners
            if isinstance(
                value := component.structural_properties.get(
                    "tested_single_shear_strength_kN"
                ),
                (int, float),
            )
            and not isinstance(value, bool)
            and float(value) > 0.0
        }
        product_diameter_values = {
            float(value)
            for component in rendered_fasteners
            if isinstance(
                value := component.structural_properties.get("nominal_diameter_mm"),
                (int, float),
            )
            and not isinstance(value, bool)
            and float(value) > 0.0
        }
        fastener_tested_single_shear_strength_kN = (
            next(iter(tested_strength_values))
            if len(tested_strength_values) == 1
            and len(rendered_fasteners)
            == sum(rendered_end_fastener_counts)
            else None
        )
        evidence_sources = {
            str(value)
            for component in rendered_fasteners
            if (
                value := component.structural_properties.get("test_evidence_source")
            )
        }
        evidence_revisions = {
            str(value)
            for component in rendered_fasteners
            if (
                value := component.structural_properties.get("test_evidence_revision")
            )
        }
        evidence_urls = {
            str(value)
            for component in rendered_fasteners
            if (value := component.structural_properties.get("test_evidence_url"))
        }
        fastener_identity_matches = bool(rendered_fasteners) and all(
            (
                component.kind == "connector"
                and component.part_number is not None
                and component.product_key is not None
                and component.product_definition_digest is not None
                and isinstance(
                    component.structural_properties.get(
                        "tested_single_shear_strength_kN"
                    ),
                    (int, float),
                )
                and not isinstance(
                    component.structural_properties.get(
                        "tested_single_shear_strength_kN"
                    ),
                    bool,
                )
                and component.structural_properties.get("test_evidence_source")
            )
            for component in rendered_fasteners
        ) and all(
            len(values) == 1
            for values in (
                fastener_part_numbers,
                fastener_product_keys,
                fastener_product_definition_digests,
                tested_strength_values,
                evidence_sources,
            )
        )
        if not rendered_fasteners:
            fastener_evidence_status: Literal[
                "unverified", "candidate", "verified"
            ] | None = None
        elif fastener_identity_matches and all(
            component.structural_evidence_status == "verified"
            for component in rendered_fasteners
        ):
            fastener_evidence_status = "verified"
        elif any(
            component.structural_evidence_status == "candidate"
            for component in rendered_fasteners
        ):
            fastener_evidence_status = "candidate"
        else:
            fastener_evidence_status = "unverified"
        rendered_layout_matches = (
            fastener_count is not None
            and len(rendered_end_connections) == 2
            and all(
                count == fastener_count for count in rendered_end_fastener_counts
            )
        )
        spacing_status: Literal["not_checked", "pass", "fail"] = "not_checked"
        edge_distance_status: Literal["not_checked", "pass", "fail"] = (
            "not_checked"
        )
        connected_part_net_capacity_kN: float | None = None
        end_bearing_capacity_kN: float | None = None
        end_tearout_capacity_kN: float | None = None
        fastener_required_single_shear_strength_kN: float | None = None
        fastener_shear_qualification_status: Literal[
            "not_checked", "candidate", "pass", "fail"
        ] = "not_checked"
        derived_connection_capacity_kN: float | None = None
        connection_basis_parts: list[str] = []
        if (
            pack is not None
            and diameter_mm is not None
            and spacing_mm is not None
            and end_distance_mm is not None
            and fastener_count is not None
            and section.tension_width_mm is not None
            and section.tension_thickness_mm is not None
            and material.tensile_strength_MPa is not None
            and material.yield_strength_MPa is not None
        ):
            spacing_status = "pass" if spacing_mm >= 3.0 * diameter_mm else "fail"
            transverse_edge_distance_mm = (
                section.tension_width_mm - spacing_mm
            ) / 2.0
            edge_distance_status = (
                "pass"
                if min(end_distance_mm, transverse_edge_distance_mm)
                >= 1.5 * diameter_mm
                else "fail"
            )
            row_factor = min(1.0, 2.5 * diameter_mm / spacing_mm)
            connected_part_net_capacity_kN = (
                0.65
                * row_factor
                * pack.net_area_mm2
                * material.tensile_strength_MPa
                / 1000.0
            )
            tearout_phi = (
                0.70
                if material.tensile_strength_MPa / material.yield_strength_MPa
                >= 1.08
                else 0.60
            )
            end_tearout_capacity_kN = (
                fastener_count
                * tearout_phi
                * section.tension_thickness_mm
                * end_distance_mm
                * material.tensile_strength_MPa
                / 1000.0
            )
            nominal_bearing_capacities: list[float] = []
            for support_section, support_material in _tension_member_supports(
                declaration,
                analysis,
                connections,
            ):
                support_thickness_mm = _section_thickness_mm(support_section)
                support_fu_MPa = support_material.tensile_strength_MPa
                if support_thickness_mm is None or support_fu_MPa is None:
                    continue
                orientation_capacities = (
                    _screw_bearing_nominal_kN(
                        head_sheet_thickness_mm=section.tension_thickness_mm,
                        head_sheet_fu_MPa=material.tensile_strength_MPa,
                        other_sheet_thickness_mm=support_thickness_mm,
                        other_sheet_fu_MPa=support_fu_MPa,
                        diameter_mm=diameter_mm,
                    ),
                    _screw_bearing_nominal_kN(
                        head_sheet_thickness_mm=support_thickness_mm,
                        head_sheet_fu_MPa=support_fu_MPa,
                        other_sheet_thickness_mm=section.tension_thickness_mm,
                        other_sheet_fu_MPa=material.tensile_strength_MPa,
                        diameter_mm=diameter_mm,
                    ),
                )
                nominal_bearing_capacities.append(min(orientation_capacities))
            if nominal_bearing_capacities:
                end_bearing_capacity_kN = (
                    fastener_count * 0.50 * min(nominal_bearing_capacities)
                )
                if fastener_tested_single_shear_strength_kN is not None:
                    qualification = (
                        as_nzs_4600_2005_a1_screw_shear_qualification(
                            tested_single_shear_strength_kN=(
                                fastener_tested_single_shear_strength_kN
                            ),
                            nominal_bearing_capacity_kN=max(
                                nominal_bearing_capacities
                            ),
                        )
                    )
                    fastener_required_single_shear_strength_kN = (
                        qualification.required_single_shear_strength_kN
                    )
                    product_diameter_matches = (
                        len(product_diameter_values) == 1
                        and diameter_mm in product_diameter_values
                    )
                    if (
                        fastener_evidence_status == "verified"
                        and fastener_identity_matches
                        and product_diameter_matches
                    ):
                        fastener_shear_qualification_status = qualification.status
                    else:
                        fastener_shear_qualification_status = "candidate"
            connection_limits = [
                connected_part_net_capacity_kN,
                end_bearing_capacity_kN,
                end_tearout_capacity_kN,
            ]
            if (
                all(value is not None for value in connection_limits)
                and spacing_status == "pass"
                and edge_distance_status == "pass"
                and rendered_layout_matches
                and fastener_shear_qualification_status == "pass"
            ):
                derived_connection_capacity_kN = min(
                    cast(float, value) for value in connection_limits
                )
            connection_basis_parts.append(
                "AS/NZS 4600:2005+A1 Clauses 5.4.2.1-5.4.2.5 connected-part "
                "net tension, spacing, edge distance, tilting/bearing, tearout, "
                "and Section 8 tested screw-shear qualification. Clause 5.4.2.5 "
                "is applied as the 1.25 Vb product qualification, not as another "
                "factored resistance limit."
            )

        if derived_connection_capacity_kN is not None:
            connection_capacity_status: Literal[
                "not_checked", "candidate", "verified"
            ] = "verified"
            connection_capacity = derived_connection_capacity_kN
        elif (
            fastener_shear_qualification_status == "fail"
            and fastener_evidence_status == "verified"
        ):
            connection_capacity_status = "verified"
            connection_capacity = None
        elif fastener_count is not None:
            connection_capacity_status = "candidate"
            connection_capacity = None
        else:
            connection_capacity_status = "not_checked"
            connection_capacity = None
        capacities = [
            capacity
            for capacity in (tension_capacity, connection_capacity)
            if capacity is not None
        ]
        governing_capacity = min(capacities) if capacities else None
        member_utilisation = (
            tension_demand_kN / tension_capacity
            if tension_capacity is not None
            else None
        )
        connection_utilisation = (
            tension_demand_kN / connection_capacity
            if connection_capacity is not None
            else None
        )
        governing_utilisation = (
            tension_demand_kN / governing_capacity
            if governing_capacity is not None
            else None
        )
        verified_utilisations = [
            utilisation
            for capacity_status, utilisation in (
                (member_capacity_status, member_utilisation),
                (connection_capacity_status, connection_utilisation),
            )
            if capacity_status == "verified" and utilisation is not None
        ]
        if fastener_shear_qualification_status == "fail":
            status: Literal["pass", "fail", "not_checked", "unsupported"] = "fail"
        elif any(utilisation > 1.0 for utilisation in verified_utilisations):
            status = "fail"
        elif not ultimate_combinations:
            status = "not_checked"
        elif (
            member_capacity_status == "verified"
            and connection_capacity_status == "verified"
        ):
            status = "pass"
        elif "candidate" in {member_capacity_status, connection_capacity_status}:
            status = "unsupported"
        else:
            status = "not_checked"
        capacity_status: Literal["not_checked", "candidate", "verified"] = (
            "verified"
            if member_capacity_status == connection_capacity_status == "verified"
            else "candidate"
            if "candidate" in {member_capacity_status, connection_capacity_status}
            or "verified" in {member_capacity_status, connection_capacity_status}
            else "not_checked"
        )
        missing_assumptions: list[str] = []
        if pack_error is not None:
            missing_assumptions.append(pack_error)
        if not ultimate_combinations:
            missing_assumptions.append(
                "No Tertius-owned ULS design combination is available for the "
                "tension envelope."
            )
        if fastener_count is not None and fastener_tested_single_shear_strength_kN is None:
            missing_assumptions.append(
                "The rendered fastener product has no single, positive manufacturer "
                "single-shear test strength for the Clause 5.4.2.5 qualification."
            )
        elif fastener_shear_qualification_status == "candidate":
            missing_assumptions.append(
                "The fastener test value is visible, but its rendered product identity, "
                "diameter, or structural evidence is not verified consistently at every end."
            )
        elif fastener_shear_qualification_status == "fail":
            missing_assumptions.append(
                "The selected fastener's tested single-shear strength is below the "
                "Clause 5.4.2.5 requirement of 1.25 Vb."
            )
        if len(rendered_end_connections) != 2:
            missing_assumptions.append(
                "The compiled brace does not have exactly two physical force/shear "
                "end connections."
            )
        elif fastener_count is not None and not rendered_layout_matches:
            missing_assumptions.append(
                "The rendered connector count at one or both brace ends does not "
                "match the product-declared end-fastener count."
            )
        if spacing_status == "fail":
            missing_assumptions.append(
                "Rendered screw spacing is below the Clause 5.4.2.1 minimum of 3df."
            )
        if edge_distance_status == "fail":
            missing_assumptions.append(
                "Rendered screw edge distance is below the Australian Clause 5.4.2.1 "
                "minimum of 1.5df."
            )
        checks.append(
            TensionMemberCheck(
                member_id=declaration.id,
                label=declaration.label,
                status=status,
                capacity_status=capacity_status,
                member_capacity_status=member_capacity_status,
                connection_capacity_status=connection_capacity_status,
                pack_id=(
                    "as_nzs_4600_2005_a1_tension" if pack is not None else None
                ),
                governing_combination_id=governing_combination_id,
                tension_demand_kN=tension_demand_kN,
                tension_capacity_kN=tension_capacity,
                end_connection_capacity_kN=connection_capacity,
                governing_capacity_kN=governing_capacity,
                member_utilisation=member_utilisation,
                connection_utilisation=connection_utilisation,
                governing_utilisation=governing_utilisation,
                end_fastener_count=fastener_count,
                rendered_end_connection_count=len(rendered_end_connections),
                rendered_end_fastener_counts=rendered_end_fastener_counts,
                required_force_per_end_fastener_kN=(
                    tension_demand_kN / fastener_count
                    if fastener_count is not None
                    else None
                ),
                gross_area_mm2=pack.gross_area_mm2 if pack is not None else None,
                net_area_mm2=pack.net_area_mm2 if pack is not None else None,
                gross_yield_capacity_kN=(
                    pack.gross_yield_capacity_kN if pack is not None else None
                ),
                net_fracture_capacity_kN=(
                    pack.net_fracture_capacity_kN if pack is not None else None
                ),
                connected_part_net_capacity_kN=connected_part_net_capacity_kN,
                end_bearing_capacity_kN=end_bearing_capacity_kN,
                end_tearout_capacity_kN=end_tearout_capacity_kN,
                end_fastener_part_numbers=fastener_part_numbers,
                end_fastener_product_keys=fastener_product_keys,
                end_fastener_product_definition_digests=(
                    fastener_product_definition_digests
                ),
                fastener_tested_single_shear_strength_kN=(
                    fastener_tested_single_shear_strength_kN
                ),
                fastener_required_single_shear_strength_kN=(
                    fastener_required_single_shear_strength_kN
                ),
                fastener_shear_qualification_status=(
                    fastener_shear_qualification_status
                ),
                fastener_evidence_status=fastener_evidence_status,
                fastener_evidence_source=(
                    next(iter(evidence_sources))
                    if len(evidence_sources) == 1
                    else None
                ),
                fastener_evidence_revision=(
                    next(iter(evidence_revisions))
                    if len(evidence_revisions) == 1
                    else None
                ),
                fastener_evidence_url=(
                    next(iter(evidence_urls)) if len(evidence_urls) == 1 else None
                ),
                spacing_status=spacing_status,
                edge_distance_status=edge_distance_status,
                standard_reference=(pack.standard_reference if pack is not None else None),
                standard_status=(pack.standard_status if pack is not None else None),
                standard_source_sha256=(
                    pack.standard_source_sha256 if pack is not None else None
                ),
                developments_supplement_sha256=(
                    pack.developments_supplement_sha256 if pack is not None else None
                ),
                basis=(
                    " ".join(
                        filter(
                            None,
                            (
                                pack.basis if pack is not None else None,
                                *connection_basis_parts,
                            ),
                        )
                    )
                    or "No tension-member resistance basis is available."
                ),
                assumptions=[
                    declaration.assumption,
                    *missing_assumptions,
                ],
            )
        )
    return checks


def _path_to_ground(
    start_component_id: str,
    *,
    excluded_component_id: str,
    components: dict[str, DesignComponent],
    connections: Sequence[DesignConnection],
) -> tuple[list[str], list[DesignConnection], str | None]:
    adjacency: dict[str, list[tuple[str, DesignConnection]]] = {}
    for connection in connections:
        if not ({"force", "shear"} & set(connection.transfers)):
            continue
        if excluded_component_id in {
            connection.from_component_id,
            connection.to_component_id,
        }:
            continue
        adjacency.setdefault(connection.from_component_id, []).append(
            (connection.to_component_id, connection)
        )
        adjacency.setdefault(connection.to_component_id, []).append(
            (connection.from_component_id, connection)
        )
    queue: deque[tuple[str, list[str], list[DesignConnection]]] = deque(
        [(start_component_id, [start_component_id], [])]
    )
    visited = {start_component_id, excluded_component_id}
    while queue:
        current_id, component_path, connection_path = queue.popleft()
        current = components.get(current_id)
        if current is not None and current.grounded:
            return component_path, connection_path, current_id
        for next_id, connection in adjacency.get(current_id, []):
            if next_id in visited or next_id not in components:
                continue
            visited.add(next_id)
            queue.append(
                (
                    next_id,
                    [*component_path, next_id],
                    [*connection_path, connection],
                )
            )
    return [start_component_id], [], None


def _expanded_component_path(
    component_path: Sequence[str],
    connection_path: Sequence[DesignConnection],
) -> list[str]:
    expanded = [component_path[0]] if component_path else []
    for next_component_id, connection in zip(
        component_path[1:],
        connection_path,
        strict=True,
    ):
        expanded.extend(connection.connector_component_ids)
        expanded.append(next_component_id)
    return expanded


def _bracing_load_path_traces(
    capture: ProjectStructuralCapture,
    analysis,
    tension_checks: Sequence[TensionMemberCheck],
    connection_checks: Sequence[ConnectionCheck] = (),
) -> list[BracingLoadPathTrace]:
    """Trace every tension-only brace through both physical ends to ground."""

    components = {component.id: component for component in capture.components}
    checks_by_member = {check.member_id: check for check in tension_checks}
    connection_checks_by_id = {
        check.connection_id: check for check in connection_checks
    }
    traces: list[BracingLoadPathTrace] = []
    for declaration in analysis.members:
        if not declaration.tension_only:
            continue
        incident: list[tuple[DesignConnection, str]] = []
        for connection in capture.connections:
            if not ({"force", "shear"} & set(connection.transfers)):
                continue
            if connection.from_component_id == declaration.component_id:
                incident.append((connection, connection.to_component_id))
            elif connection.to_component_id == declaration.component_id:
                incident.append((connection, connection.from_component_id))
        incident.sort(key=lambda item: item[0].id)
        blockers: list[str] = []
        if len(incident) < 2:
            blockers.append(
                "The brace does not have two authored force/shear end connections."
            )
        legs: list[tuple[list[str], list[DesignConnection], str | None]] = []
        for _connection, support_component_id in incident[:2]:
            leg = _path_to_ground(
                support_component_id,
                excluded_component_id=declaration.component_id,
                components=components,
                connections=capture.connections,
            )
            legs.append(leg)
            if leg[2] is None:
                blockers.append(
                    f"No force/shear path from {support_component_id!r} reaches a "
                    "grounded component without relying on the brace itself."
                )
        check = checks_by_member.get(declaration.id)
        if check is None:
            blockers.append("No tension-member demand/resistance check was produced.")
        elif check.connection_capacity_status != "verified":
            blockers.append(
                "The rendered end connection has no complete verified resistance."
            )
        component_ids: list[str] = [declaration.component_id]
        connection_ids: list[str] = []
        grounded_ids: list[str] = [
            ground
            for _path, _connections, ground in legs
            if ground is not None
        ]
        if len(legs) == 2 and len(incident) >= 2:
            first_components = _expanded_component_path(legs[0][0], legs[0][1])
            second_components = _expanded_component_path(legs[1][0], legs[1][1])
            component_ids = [
                *reversed(first_components),
                *incident[0][0].connector_component_ids,
                declaration.component_id,
                *incident[1][0].connector_component_ids,
                *second_components,
            ]
            connection_ids = [
                *(connection.id for connection in reversed(legs[0][1])),
                incident[0][0].id,
                incident[1][0].id,
                *(connection.id for connection in legs[1][1]),
            ]
        path_connection_failures: list[ConnectionCheck] = []
        path_connection_blockers: list[str] = []
        for connection_id in dict.fromkeys(connection_ids):
            connection_check = connection_checks_by_id.get(connection_id)
            if connection_check is None:
                path_connection_blockers.append(
                    f"Connection {connection_id!r} has no demand/resistance check."
                )
            elif connection_check.status == "fail":
                path_connection_failures.append(connection_check)
                path_connection_blockers.append(
                    f"Connection {connection_id!r} fails at utilisation "
                    f"{connection_check.governing_utilisation:.3f}."
                    if connection_check.governing_utilisation is not None
                    else f"Connection {connection_id!r} fails its resistance check."
                )
            elif connection_check.status != "pass":
                path_connection_blockers.append(
                    f"Connection {connection_id!r} is {connection_check.status}; "
                    "verified resistance is required for the path to ground."
                )
        blockers.extend(path_connection_blockers)
        if any(ground is None for _path, _connections, ground in legs) or len(legs) < 2:
            status: Literal["pass", "fail", "candidate", "blocked"] = "blocked"
        elif (check is not None and check.status == "fail") or path_connection_failures:
            status = "fail"
        elif path_connection_blockers:
            status = "candidate"
        elif check is not None and check.status == "pass" and not blockers:
            status = "pass"
        else:
            status = "candidate"
        traces.append(
            BracingLoadPathTrace(
                id=f"bracing-path:{declaration.id}",
                member_id=declaration.id,
                component_id=declaration.component_id,
                governing_combination_id=(
                    check.governing_combination_id if check is not None else None
                ),
                status=status,
                tension_demand_kN=(check.tension_demand_kN if check is not None else 0),
                component_ids=list(dict.fromkeys(component_ids)),
                connection_ids=list(dict.fromkeys(connection_ids)),
                grounded_component_ids=list(dict.fromkeys(grounded_ids)),
                blockers=list(dict.fromkeys(blockers)),
                basis=(
                    "The compiled physical connection graph was traversed independently "
                    "from each brace end to grounded components. A pass additionally "
                    "requires the governing ULS strap and every physical connection "
                    "on both routes to ground to pass resistance."
                ),
            )
        )
    return traces


def _clean(value: float, tolerance: float = 1e-12) -> float:
    return 0.0 if abs(value) < tolerance else float(value)


def _vector(values: Sequence[float]) -> Vector3:
    return Vector3(
        x=_clean(values[0]),
        y=_clean(values[1]),
        z=_clean(values[2]),
    )


def _length(start: Vector3, end: Vector3) -> float:
    return sqrt(
        (end.x - start.x) ** 2 + (end.y - start.y) ** 2 + (end.z - start.z) ** 2
    )


def _axis(start: Vector3, end: Vector3) -> Vector3:
    member_length = _length(start, end)
    return Vector3(
        x=(end.x - start.x) / member_length,
        y=(end.y - start.y) / member_length,
        z=(end.z - start.z) / member_length,
    )


def _cross(left: Vector3, right: Vector3) -> tuple[float, float, float]:
    return (
        left.y * right.z - left.z * right.y,
        left.z * right.x - left.x * right.z,
        left.x * right.y - left.y * right.x,
    )


def _add(
    accumulator: list[float],
    value: Vector3 | tuple[float, float, float],
    scale: float = 1.0,
) -> None:
    values = (value.x, value.y, value.z) if isinstance(value, Vector3) else value
    for index in range(3):
        accumulator[index] += values[index] * scale


def _add_absolute(
    accumulator: list[float],
    value: Vector3 | tuple[float, float, float],
) -> None:
    values = (value.x, value.y, value.z) if isinstance(value, Vector3) else value
    for index in range(3):
        accumulator[index] += abs(values[index])


def _scaled(value: Vector3, scale: float) -> Vector3:
    return Vector3(
        x=value.x * scale,
        y=value.y * scale,
        z=value.z * scale,
    )


def _plus(left: Vector3, right: Vector3) -> Vector3:
    return Vector3(
        x=left.x + right.x,
        y=left.y + right.y,
        z=left.z + right.z,
    )


def _magnitude(value: Vector3) -> float:
    return sqrt(value.x**2 + value.y**2 + value.z**2)


def _amplification(second_order: float, first_order: float) -> float:
    if abs(first_order) <= 1e-12:
        return 1.0 if abs(second_order) <= 1e-12 else second_order / 1e-12
    return second_order / first_order


def _stability_scope_comparisons(
    comparisons: Sequence[MemberStabilityComparison],
    *,
    eaves_member_ids: Sequence[str],
    rafter_member_ids: Sequence[str],
) -> list[MemberStabilityComparison]:
    """Return the primary-frame members authored to govern global stability."""

    scoped_ids = set(eaves_member_ids) | set(rafter_member_ids)
    if not scoped_ids:
        return list(comparisons)
    scoped = [
        comparison for comparison in comparisons if comparison.member_id in scoped_ids
    ]
    if not scoped:
        raise StructuralAnalysisError(
            "the authored global-stability member scope resolved no members"
        )
    return scoped


def _analysis_base_model_matches(analysis) -> bool:
    stability = analysis.stability
    if stability is None or stability.analysis_base_model == "unspecified":
        return True
    members_by_id = {member.id: member for member in analysis.members}
    if not stability.eaves_member_ids:
        return False
    base_restraints = [
        members_by_id[member_id].start_restraints
        for member_id in stability.eaves_member_ids
        if member_id in members_by_id
    ]
    if len(base_restraints) != len(stability.eaves_member_ids):
        return False
    if stability.analysis_base_model == "perfectly_pinned":
        return all(
            restraints.dx
            and restraints.dy
            and restraints.dz
            and not restraints.rx
            and not restraints.ry
            and not restraints.rz
            for restraints in base_restraints
        )
    if stability.analysis_base_model == "fixed":
        return all(
            restraints.dx
            and restraints.dy
            and restraints.dz
            and restraints.rx
            and restraints.ry
            and restraints.rz
            for restraints in base_restraints
        )
    return False


def _member_extrema(
    model,
    declaration,
    *,
    combination_id: str,
    point_loads,
    distributed_loads,
) -> tuple[float, float]:
    """Sample resultant bending and displacement for one solved member."""

    member_length = _length(declaration.start, declaration.end)
    station_distances = {
        member_length * index / STATION_INTERVALS
        for index in range(STATION_INTERVALS + 1)
    }
    station_distances.update(load.distance_m for load in point_loads)
    for line_load in distributed_loads:
        station_distances.update((line_load.start_distance_m, line_load.end_distance_m))
    member = model.members[declaration.id]
    max_moment = 0.0
    max_displacement = 0.0
    for distance in sorted(station_distances):
        local_moment = (
            member.moment("My", distance, combination_id),
            member.moment("Mz", distance, combination_id),
        )
        local_displacement_mm = (
            member.deflection("dx", distance, combination_id) * 1000.0,
            member.deflection("dy", distance, combination_id) * 1000.0,
            member.deflection("dz", distance, combination_id) * 1000.0,
        )
        max_moment = max(
            max_moment,
            sqrt(sum(value**2 for value in local_moment)),
        )
        max_displacement = max(
            max_displacement,
            sqrt(sum(value**2 for value in local_displacement_mm)),
        )
    return max_moment, max_displacement


def _member_station_distances(analysis, declaration) -> list[float]:
    member_length = _length(declaration.start, declaration.end)
    distances = {
        member_length * index / STATION_INTERVALS
        for index in range(STATION_INTERVALS + 1)
    }
    distances.update(
        load.distance_m
        for load in analysis.member_loads
        if load.member_id == declaration.id
    )
    for load in analysis.member_distributed_loads:
        if load.member_id == declaration.id:
            distances.update((load.start_distance_m, load.end_distance_m))
    return sorted(distances)


def _off_axis_load_path(
    component_id: str,
    components: dict[str, DesignComponent],
    connections: Sequence[DesignConnection],
) -> dict[str, Any]:
    """Trace authored surface action into a member and force/shear path to ground.

    Connectivity is deliberately reported as a candidate only. It demonstrates
    where the design says the reaction goes; it does not infer connector,
    diaphragm, collector, or anchorage resistance from touching CAD solids.
    """
    source_component_ids: list[str] = []
    source_connection_ids: list[str] = []
    adjacency: dict[str, list[tuple[str, DesignConnection]]] = {}
    for connection in connections:
        if connection.from_component_id == component_id:
            other_id = connection.to_component_id
        elif connection.to_component_id == component_id:
            other_id = connection.from_component_id
        else:
            other_id = ""
        if other_id and components.get(other_id, None) is not None:
            if (
                components[other_id].kind == "surface"
                and "wind_normal" in connection.transfers
            ):
                source_component_ids.append(other_id)
                source_connection_ids.append(connection.id)

        if not ({"force", "shear"} & set(connection.transfers)):
            continue
        adjacency.setdefault(connection.from_component_id, []).append(
            (connection.to_component_id, connection)
        )
        adjacency.setdefault(connection.to_component_id, []).append(
            (connection.from_component_id, connection)
        )

    queue: deque[tuple[str, list[str], list[DesignConnection]]] = deque(
        [(component_id, [component_id], [])]
    )
    visited = {component_id}
    while queue:
        current_id, component_path, connection_path = queue.popleft()
        current = components.get(current_id)
        if current is not None and current.grounded and current_id != component_id:
            collector_component_ids = [component_path[0]]
            for next_component_id, connection in zip(
                component_path[1:],
                connection_path,
                strict=True,
            ):
                collector_component_ids.extend(connection.connector_component_ids)
                collector_component_ids.append(next_component_id)
            source_basis = (
                "An authored wind-normal surface connection feeds this member"
                if source_component_ids
                else "No wind-normal surface action source is directly connected"
            )
            return {
                "status": "candidate",
                "source_component_ids": list(dict.fromkeys(source_component_ids)),
                "source_connection_ids": list(dict.fromkeys(source_connection_ids)),
                "collector_component_ids": list(dict.fromkeys(collector_component_ids)),
                "collector_connection_ids": [
                    connection.id for connection in connection_path
                ],
                "grounded_component_id": current_id,
                "basis": (
                    f"{source_basis}; the design connection graph contains a "
                    "force/shear path to the "
                    f"grounded component {current_id!r}. Path continuity is a candidate, "
                    "not verified resistance or stiffness."
                ),
            }
        for next_id, connection in adjacency.get(current_id, []):
            if next_id in visited or components.get(next_id) is None:
                continue
            visited.add(next_id)
            queue.append(
                (next_id, [*component_path, next_id], [*connection_path, connection])
            )

    return {
        "status": "not_declared",
        "source_component_ids": list(dict.fromkeys(source_component_ids)),
        "source_connection_ids": list(dict.fromkeys(source_connection_ids)),
        "collector_component_ids": [component_id],
        "collector_connection_ids": [],
        "grounded_component_id": None,
        "basis": (
            "No authored force/shear connection path from this member reaches a "
            "grounded component."
        ),
    }


def _cross_section_checks(
    model,
    analysis,
    components: dict[str, DesignComponent],
    connections: Sequence[DesignConnection],
) -> list[MemberCrossSectionCheck]:
    definition = analysis.cross_section_verification
    if definition is None:
        return []

    sections_by_id = {section.id: section for section in analysis.sections}
    checks: list[MemberCrossSectionCheck] = []
    selected_member_ids = set(definition.member_ids)
    for declaration in analysis.members:
        if declaration.analytical_role == "rigid_zone":
            continue
        if selected_member_ids and declaration.id not in selected_member_ids:
            continue
        section = sections_by_id[declaration.section_id]
        try:
            capacity = cross_section_capacity(definition.pack_id, section)
        except CapacityPackError as exc:
            checks.append(
                MemberCrossSectionCheck(
                    member_id=declaration.id,
                    label=f"{declaration.label} cross-section",
                    pack_id=definition.pack_id,
                    status="unsupported",
                    basis=f"Capacity pack could not evaluate this section: {exc}",
                    assumptions=[
                        "No resistance has been inferred from incomplete catalogue data."
                    ],
                )
            )
            continue

        if section.bending_reference_axis != "local_z":
            checks.append(
                MemberCrossSectionCheck(
                    member_id=declaration.id,
                    label=f"{declaration.label} cross-section",
                    pack_id=definition.pack_id,
                    status="unsupported",
                    design_compression_capacity_kN=(
                        capacity.design_compression_capacity_kN
                    ),
                    design_major_bending_capacity_kNm=(
                        capacity.design_major_bending_capacity_kNm
                    ),
                    design_minor_bending_capacity_kNm=(
                        capacity.design_minor_bending_capacity_kNm
                    ),
                    design_web_shear_capacity_kN=(
                        capacity.design_web_shear_capacity_kN
                    ),
                    design_off_axis_shear_capacity_kN=(
                        capacity.design_off_axis_shear_capacity_kN
                    ),
                    design_st_venant_torsion_capacity_kNm=(
                        capacity.design_st_venant_torsion_capacity_kNm
                    ),
                    section_record_sha256=capacity.section_record_sha256,
                    capacity_factors={
                        "phi_c": capacity.phi_c,
                        "phi_b": capacity.phi_b,
                        "phi_v": capacity.phi_v,
                    },
                    web_slenderness=capacity.web_slenderness,
                    shear_regime=capacity.shear_regime,
                    standard_reference=capacity.standard_reference,
                    standard_status=capacity.standard_status,
                    standard_source_sha256=capacity.standard_source_sha256,
                    developments_supplement_sha256=(
                        capacity.developments_supplement_sha256
                    ),
                    basis=(
                        "The selected pack requires the catalogue major-axis "
                        "reference to map to PyNite local_z."
                    ),
                )
            )
            continue

        governing: dict[str, float | str] | None = None
        peak_off_axis_shear_kN = 0.0
        for combination_id in definition.combination_ids:
            for distance in _member_station_distances(analysis, declaration):
                member = model.members[declaration.id]
                axial_kN = abs(member.axial(distance, combination_id))
                major_moment_kNm = abs(member.moment("Mz", distance, combination_id))
                minor_moment_kNm = abs(member.moment("My", distance, combination_id))
                web_shear_kN = abs(member.shear("Fy", distance, combination_id))
                off_axis_shear_kN = abs(member.shear("Fz", distance, combination_id))
                torsion_kNm = abs(member.torque(distance, combination_id))
                peak_off_axis_shear_kN = max(
                    peak_off_axis_shear_kN,
                    off_axis_shear_kN,
                )

                axial_bending = (
                    axial_kN / capacity.design_compression_capacity_kN
                    + major_moment_kNm / capacity.design_major_bending_capacity_kNm
                )
                biaxial_axial_bending = (
                    axial_bending
                    + minor_moment_kNm
                    / capacity.design_minor_bending_capacity_kNm
                )
                bending_shear = sqrt(
                    (major_moment_kNm / capacity.design_major_bending_capacity_kNm) ** 2
                    + (web_shear_kN / capacity.design_web_shear_capacity_kN) ** 2
                )
                minor_bending_shear = sqrt(
                    (
                        minor_moment_kNm
                        / capacity.design_minor_bending_capacity_kNm
                    )
                    ** 2
                    + (
                        off_axis_shear_kN
                        / capacity.design_off_axis_shear_capacity_kN
                    )
                    ** 2
                )
                torsion_utilisation = (
                    torsion_kNm
                    / capacity.design_st_venant_torsion_capacity_kNm
                )
                utilization = (
                    max(
                        biaxial_axial_bending,
                        bending_shear,
                        minor_bending_shear,
                    )
                    + torsion_utilisation
                )
                candidate: dict[str, float | str] = {
                    "combination_id": combination_id,
                    "distance": distance,
                    "axial": axial_kN,
                    "major_moment": major_moment_kNm,
                    "minor_moment": minor_moment_kNm,
                    "web_shear": web_shear_kN,
                    "off_axis_shear": off_axis_shear_kN,
                    "torsion": torsion_kNm,
                    "axial_bending": axial_bending,
                    "biaxial_axial_bending": biaxial_axial_bending,
                    "bending_shear": bending_shear,
                    "minor_bending_shear": minor_bending_shear,
                    "torsion_utilisation": torsion_utilisation,
                    "utilization": utilization,
                }
                if governing is None or utilization > float(governing["utilization"]):
                    governing = candidate

        if governing is None:
            raise StructuralAnalysisError(
                f"cross-section envelope for {declaration.id!r} has no stations"
            )
        status: Literal["pass", "fail", "unsupported"] = (
            "fail" if float(governing["utilization"]) > 1.0 else "pass"
        )
        off_axis_path = _off_axis_load_path(
            declaration.component_id,
            components,
            connections,
        )
        checks.append(
            MemberCrossSectionCheck(
                member_id=declaration.id,
                label=f"{declaration.label} cross-section",
                pack_id=definition.pack_id,
                status=status,
                governing_combination_id=str(governing["combination_id"]),
                governing_station_m=float(governing["distance"]),
                axial_kN=float(governing["axial"]),
                major_moment_kNm=float(governing["major_moment"]),
                minor_moment_kNm=float(governing["minor_moment"]),
                web_shear_kN=float(governing["web_shear"]),
                off_axis_shear_kN=float(governing["off_axis_shear"]),
                torsion_kNm=float(governing["torsion"]),
                design_compression_capacity_kN=(
                    capacity.design_compression_capacity_kN
                ),
                design_major_bending_capacity_kNm=(
                    capacity.design_major_bending_capacity_kNm
                ),
                design_minor_bending_capacity_kNm=(
                    capacity.design_minor_bending_capacity_kNm
                ),
                design_web_shear_capacity_kN=(capacity.design_web_shear_capacity_kN),
                design_off_axis_shear_capacity_kN=(
                    capacity.design_off_axis_shear_capacity_kN
                ),
                design_st_venant_torsion_capacity_kNm=(
                    capacity.design_st_venant_torsion_capacity_kNm
                ),
                axial_bending_utilisation=float(governing["axial_bending"]),
                biaxial_axial_bending_utilisation=float(
                    governing["biaxial_axial_bending"]
                ),
                bending_shear_utilisation=float(governing["bending_shear"]),
                minor_bending_shear_utilisation=float(
                    governing["minor_bending_shear"]
                ),
                torsion_utilisation=float(governing["torsion_utilisation"]),
                governing_utilisation=float(governing["utilization"]),
                section_record_sha256=capacity.section_record_sha256,
                capacity_factors={
                    "phi_c": capacity.phi_c,
                    "phi_b": capacity.phi_b,
                    "phi_v": capacity.phi_v,
                },
                web_slenderness=capacity.web_slenderness,
                shear_regime=capacity.shear_regime,
                standard_reference=capacity.standard_reference,
                standard_status=capacity.standard_status,
                standard_source_sha256=capacity.standard_source_sha256,
                developments_supplement_sha256=(
                    capacity.developments_supplement_sha256
                ),
                off_axis_load_path_status=off_axis_path["status"],
                off_axis_required_reaction_kN=peak_off_axis_shear_kN,
                off_axis_source_component_ids=off_axis_path["source_component_ids"],
                off_axis_source_connection_ids=off_axis_path["source_connection_ids"],
                off_axis_collector_component_ids=off_axis_path[
                    "collector_component_ids"
                ],
                off_axis_collector_connection_ids=off_axis_path[
                    "collector_connection_ids"
                ],
                off_axis_grounded_component_id=off_axis_path["grounded_component_id"],
                off_axis_load_path_basis=off_axis_path["basis"],
                basis=capacity.basis,
                assumptions=[
                    (
                        "Compression resistance conservatively uses Ae×fy for "
                        "the absolute axial demand; tension yielding is not used "
                        "to improve the result."
                    ),
                    (
                        "Member buckling, restraint, connections, bearing, "
                        "local load introduction, and system capacity remain "
                        "outside this Stage 6 check."
                    ),
                    (
                        "Clause 3.5.1 biaxial axial-bending interaction and "
                        "Clause 3.3.5 bending-shear interactions are evaluated "
                        "for both local axes. Full St-Venant torsion utilisation "
                        "is then added linearly without warping-restraint credit."
                    ),
                    *(
                        [
                            "An authored collector path reaches ground, but roof-sheet "
                            "fasteners, member support transfer, collector/brace resistance "
                            "and stiffness, and anchorage remain unverified."
                        ]
                        if off_axis_path["status"] == "candidate"
                        and peak_off_axis_shear_kN > definition.off_axis_tolerance
                        else []
                    ),
                ],
            )
        )
    return checks


def _compression_flange(
    signed_major_moment_kNm: float,
    tolerance: float,
) -> Literal["positive_local_y", "negative_local_y", "none"]:
    # PyNite's positive local Mz produces compressive longitudinal stress at
    # positive local y under sigma_x = -Mz*y/Iz.
    if signed_major_moment_kNm > tolerance:
        return "positive_local_y"
    if signed_major_moment_kNm < -tolerance:
        return "negative_local_y"
    return "none"


def _candidate_contact_flange(
    model,
    candidate,
    tolerance: float,
) -> Literal["positive_local_y", "negative_local_y", "both", "none"]:
    if candidate.restrained_flange != "auto":
        return cast(
            Literal["positive_local_y", "negative_local_y", "both"],
            candidate.restrained_flange,
        )
    rotation = model.members[candidate.member_id].T()[:3, :3]
    local_y_global = tuple(float(value) for value in rotation[1])
    offset = (
        candidate.brace_position.x - candidate.member_position.x,
        candidate.brace_position.y - candidate.member_position.y,
        candidate.brace_position.z - candidate.member_position.z,
    )
    local_y_offset = sum(local_y_global[index] * offset[index] for index in range(3))
    if local_y_offset > tolerance:
        return "positive_local_y"
    if local_y_offset < -tolerance:
        return "negative_local_y"
    return "none"


def _candidate_restrains_flange(
    model,
    candidate,
    compression_flange: Literal["positive_local_y", "negative_local_y", "none"],
    tolerance: float,
) -> bool:
    if compression_flange == "none":
        return True
    contact_flange = _candidate_contact_flange(model, candidate, tolerance)
    return contact_flange in {compression_flange, "both"}


def _member_restraint_candidate_checks(
    model,
    analysis,
    *,
    combination_id: str,
    connection_checks_by_id: Mapping[str, ConnectionCheck] | None = None,
) -> list[MemberRestraintCandidateCheck]:
    definition = analysis.member_stability_verification
    if definition is None:
        return []
    combinations_by_id = {
        combination.id: combination for combination in analysis.load_combinations
    }
    combination = combinations_by_id[combination_id]
    members_by_id = {member.id: member for member in analysis.members}
    sections_by_id = {section.id: section for section in analysis.sections}
    candidates_by_member: dict[str, list[Any]] = {}
    for candidate in definition.restraint_candidates:
        candidates_by_member.setdefault(candidate.member_id, []).append(candidate)
    for candidates in candidates_by_member.values():
        candidates.sort(key=lambda candidate: candidate.distance_m)
    checks: list[MemberRestraintCandidateCheck] = []
    for candidate in definition.restraint_candidates:
        evidence_resolution = (
            resolve_restraint_evidence(
                candidate.evidence_pack_id,
                candidate.configuration,
            )
            if candidate.evidence_pack_id is not None
            else None
        )
        identity_status: Literal["not_declared", "pass", "fail"] = (
            evidence_resolution.identity_status
            if evidence_resolution is not None
            else "not_declared"
        )
        identity_mismatches = (
            list(evidence_resolution.identity_mismatches)
            if evidence_resolution is not None
            else []
        )
        design_force_capacity_kN = (
            evidence_resolution.design_force_capacity_kN
            if evidence_resolution is not None
            and evidence_resolution.identity_status == "pass"
            else candidate.design_force_capacity_kN
            if evidence_resolution is None
            else None
        )
        design_moment_capacity_kNm = (
            evidence_resolution.design_moment_capacity_kNm
            if evidence_resolution is not None
            and evidence_resolution.identity_status == "pass"
            else candidate.design_moment_capacity_kNm
            if evidence_resolution is None
            else None
        )
        stiffness_status = (
            evidence_resolution.stiffness_status
            if evidence_resolution is not None
            and evidence_resolution.identity_status == "pass"
            else candidate.stiffness_status
            if evidence_resolution is None
            else "unverified"
        )
        capacity_basis = (
            evidence_resolution.capacity_basis
            if evidence_resolution is not None
            else candidate.capacity_basis
        )
        evidence_status = candidate.evidence_status
        local_connection_check = (
            connection_checks_by_id.get(candidate.connection_id)
            if connection_checks_by_id is not None
            else None
        )
        if (
            identity_status == "pass"
            and local_connection_check is not None
            and local_connection_check.status == "pass"
            and local_connection_check.evidence_status == "verified"
        ):
            force_capacities = [
                value
                for value in (
                    local_connection_check.design_axial_capacity_kN,
                    local_connection_check.design_shear_capacity_kN,
                )
                if value is not None
            ]
            if force_capacities:
                design_force_capacity_kN = min(force_capacities)
            if local_connection_check.design_moment_capacity_kNm is not None:
                design_moment_capacity_kNm = (
                    local_connection_check.design_moment_capacity_kNm
                )
            stiffness_status = local_connection_check.stiffness_status
            capacity_basis = (
                f"{capacity_basis} Local connection {candidate.connection_id!r} "
                f"passes {local_connection_check.pack_id} with exact rendered identity; "
                "its governing complete-joint force, moment and stiffness results are "
                "used as the restraint resistance."
            )
            if (
                design_force_capacity_kN is not None
                and design_moment_capacity_kNm is not None
                and stiffness_status == "verified"
            ):
                evidence_status = "verified"
        declaration = members_by_id[candidate.member_id]
        section = sections_by_id[declaration.section_id]
        member_length = _length(declaration.start, declaration.end)
        candidate_distances = sorted(
            {item.distance_m for item in candidates_by_member[candidate.member_id]}
        )
        candidate_index = candidate_distances.index(candidate.distance_m)
        previous_distance = (
            candidate_distances[candidate_index - 1] if candidate_index > 0 else 0.0
        )
        next_distance = (
            candidate_distances[candidate_index + 1]
            if candidate_index + 1 < len(candidate_distances)
            else member_length
        )
        tributary_start_m = (
            (previous_distance + candidate.distance_m) / 2.0
            if candidate_index > 0
            else 0.0
        )
        tributary_end_m = (
            (candidate.distance_m + next_distance) / 2.0
            if candidate_index + 1 < len(candidate_distances)
            else member_length
        )
        point_loads = [
            load
            for load in analysis.member_loads
            if load.member_id == candidate.member_id
            and tributary_start_m - 1e-9 <= load.distance_m <= tributary_end_m + 1e-9
        ]
        combined_force = Vector3(x=0.0, y=0.0, z=0.0)
        for load in point_loads:
            factor = combination.factors.get(load.case_id, 0.0)
            combined_force = Vector3(
                x=combined_force.x + factor * load.force.x,
                y=combined_force.y + factor * load.force.y,
                z=combined_force.z + factor * load.force.z,
            )
        for load in analysis.member_distributed_loads:
            if load.member_id != candidate.member_id:
                continue
            overlap_start = max(tributary_start_m, load.start_distance_m)
            overlap_end = min(tributary_end_m, load.end_distance_m)
            if overlap_end <= overlap_start:
                continue
            factor = combination.factors.get(load.case_id, 0.0)
            if factor == 0.0:
                continue
            load_span = load.end_distance_m - load.start_distance_m

            def ordinate(vector_start: Vector3, vector_end: Vector3, distance: float):
                ratio = (distance - load.start_distance_m) / load_span
                return Vector3(
                    x=vector_start.x + (vector_end.x - vector_start.x) * ratio,
                    y=vector_start.y + (vector_end.y - vector_start.y) * ratio,
                    z=vector_start.z + (vector_end.z - vector_start.z) * ratio,
                )

            start_force = ordinate(
                load.start_force_kN_m,
                load.end_force_kN_m,
                overlap_start,
            )
            end_force = ordinate(
                load.start_force_kN_m,
                load.end_force_kN_m,
                overlap_end,
            )
            overlap_length = overlap_end - overlap_start
            combined_force = Vector3(
                x=combined_force.x
                + factor * overlap_length * (start_force.x + end_force.x) / 2.0,
                y=combined_force.y
                + factor * overlap_length * (start_force.y + end_force.y) / 2.0,
                z=combined_force.z
                + factor * overlap_length * (start_force.z + end_force.z) / 2.0,
            )
        transferred_load_kN = _magnitude(combined_force)
        depth_value = None
        if section.catalog is not None:
            raw_depth = section.catalog.properties.get(
                "depth_mm",
                section.catalog.properties.get("D_mm"),
            )
            if isinstance(raw_depth, (int, float)) and float(raw_depth) > 0:
                depth_value = float(raw_depth) / 1000.0

        required_force_kN: float | None = None
        required_moment_kNm: float | None = None
        if (
            candidate.demand_model == "aisi_2004_d3_2_2_eccentric_load_couple"
            and depth_value is not None
        ):
            required_force_kN = (
                candidate.demand_factor
                * candidate.axis_separation_m
                / depth_value
                * transferred_load_kN
            )
            required_moment_kNm = required_force_kN * depth_value
        elif (
            candidate.demand_model
            == "as_nzs_4600_2005_4_3_2_flange_force"
            and depth_value is not None
        ):
            member = model.members[candidate.member_id]
            station_distances = {
                0.0,
                member_length,
                candidate.distance_m,
                *_member_station_distances(analysis, declaration),
            }
            critical_flange_force_kN = max(
                (
                    abs(member.axial(distance_m, combination_id)) / 2.0
                    + abs(member.moment("Mz", distance_m, combination_id))
                    / depth_value
                )
                for distance_m in station_distances
                if -1e-9 <= distance_m <= member_length + 1e-9
            )
            transferred_load_kN = critical_flange_force_kN
            required_force_kN = 0.025 * critical_flange_force_kN
            required_moment_kNm = required_force_kN * depth_value

        force_utilisation = (
            required_force_kN / design_force_capacity_kN
            if required_force_kN is not None and design_force_capacity_kN is not None
            else None
        )
        moment_utilisation = (
            required_moment_kNm / design_moment_capacity_kNm
            if required_moment_kNm is not None
            and design_moment_capacity_kNm is not None
            else None
        )
        anchorage_status = candidate.anchorage_status
        anchorage_basis = candidate.anchorage_basis
        anchorage_blockers: list[str] = []
        if anchorage_status != "verified" and connection_checks_by_id is not None:
            if candidate.anchorage_grounded_component_id is None:
                anchorage_blockers.append(
                    "No compiled physical route from this restraint reaches ground."
                )
            elif not candidate.anchorage_connection_ids:
                anchorage_blockers.append(
                    "The route reaches a grounded component without a declared "
                    "connection resistance path."
                )
            else:
                for connection_id in candidate.anchorage_connection_ids:
                    connection_check = connection_checks_by_id.get(connection_id)
                    if connection_check is None:
                        anchorage_blockers.append(
                            f"Connection {connection_id!r} has no resistance check."
                        )
                    elif connection_check.status != "pass":
                        anchorage_blockers.append(
                            f"Connection {connection_id!r} is "
                            f"{connection_check.status}."
                        )
            if not anchorage_blockers:
                anchorage_status = "verified"
                anchorage_basis = (
                    "Every compiled physical connection on the longitudinal route to "
                    f"grounded component {candidate.anchorage_grounded_component_id} "
                    "passes its governing ULS resistance check."
                )
            else:
                anchorage_basis = (
                    f"{candidate.anchorage_basis} "
                    + " ".join(anchorage_blockers)
                )
        status: Literal["unsupported", "candidate", "pass", "fail"]
        if evidence_status == "unsupported" or identity_status == "fail":
            status = "unsupported"
        elif (
            evidence_status != "verified"
            or required_force_kN is None
            or required_moment_kNm is None
            or design_force_capacity_kN is None
            or design_moment_capacity_kNm is None
            or stiffness_status != "verified"
            or (
                candidate.evidence_pack_id is not None
                and anchorage_status != "verified"
            )
        ):
            status = "candidate"
        elif max(force_utilisation or 0.0, moment_utilisation or 0.0) > 1.0:
            status = "fail"
        else:
            status = "pass"
        checks.append(
            MemberRestraintCandidateCheck(
                id=f"candidate-check-{candidate.id}-{combination_id}",
                candidate_id=candidate.id,
                member_id=candidate.member_id,
                connection_id=candidate.connection_id,
                combination_id=combination_id,
                contact_flange=_candidate_contact_flange(
                    model,
                    candidate,
                    definition.off_axis_tolerance,
                ),
                status=status,
                demand_model=candidate.demand_model,
                transferred_load_kN=transferred_load_kN,
                load_eccentricity_m=(
                    candidate.axis_separation_m
                    if candidate.demand_model
                    == "aisi_2004_d3_2_2_eccentric_load_couple"
                    else None
                ),
                member_depth_m=depth_value,
                required_force_kN=required_force_kN,
                required_moment_kNm=required_moment_kNm,
                available_force_kN=design_force_capacity_kN,
                available_moment_kNm=design_moment_capacity_kNm,
                force_utilisation=force_utilisation,
                moment_utilisation=moment_utilisation,
                stiffness_status=stiffness_status,
                evidence_pack_id=candidate.evidence_pack_id,
                evidence_pack_version=(
                    evidence_resolution.pack_version
                    if evidence_resolution is not None
                    else None
                ),
                identity_status=identity_status,
                identity_mismatches=identity_mismatches,
                evidence_references=(
                    list(evidence_resolution.references)
                    if evidence_resolution is not None
                    else []
                ),
                anchorage_status=anchorage_status,
                anchorage_component_ids=candidate.anchorage_component_ids,
                anchorage_connection_ids=candidate.anchorage_connection_ids,
                anchorage_grounded_component_id=(
                    candidate.anchorage_grounded_component_id
                ),
                anchorage_basis=anchorage_basis,
                anchorage_blockers=anchorage_blockers,
                mechanism=(
                    "AS/NZS 4600:2005 clauses 4.3.2.2-4.3.2.3: the restraint "
                    "transfers 2.5% of the maximum critical-flange force, with "
                    "flange force conservatively taken as |N*|/2 + |M*z|/d over "
                    "the adjacent analytical segment."
                    if candidate.demand_model
                    == "as_nzs_4600_2005_4_3_2_flange_force"
                    else
                    "Flange-brace force couple generated by the factored point-load "
                    "and distributed-load resultant within the candidate's midpoint "
                    "tributary interval, acting at the connected secondary-member axis."
                    if candidate.demand_model
                    == "aisi_2004_d3_2_2_eccentric_load_couple"
                    else "Connection boundary restraint demand is not yet quantified."
                ),
                provenance=candidate.provenance,
                basis=(
                    "AS/NZS 4600:2005 incorporating Amendment No. 1 clauses "
                    "4.3.2.1-4.3.2.3 require restraint strength and stiffness and "
                    "a design force equal to 0.025 times the maximum critical-flange "
                    f"force. {capacity_basis}"
                    if candidate.demand_model
                    == "as_nzs_4600_2005_4_3_2_flange_force"
                    else
                    "P_L = 1.5(e/d)W from the AISI Cold-Formed Steel Framing "
                    "Design Guide, Second Edition, Example 2 Step 2(a), adapting "
                    "Supplement D3.2.2 to expose a working eccentric-load brace "
                    "demand. This does not replace an AS/NZS 4600 verification. "
                    f"{capacity_basis}"
                    if candidate.demand_model
                    == "aisi_2004_d3_2_2_eccentric_load_couple"
                    else capacity_basis
                ),
            )
        )
    return checks


def _segment_restraint_state(
    model,
    definition,
    segment,
    compression_flange: Literal["positive_local_y", "negative_local_y", "none"],
    candidate_checks_by_id: dict[str, MemberRestraintCandidateCheck] | None = None,
) -> tuple[
    Literal["missing", "candidate", "inadequate", "verified"],
    list[str],
    list[str],
]:
    if compression_flange == "none":
        return "verified", [], []
    if (
        segment.lateral_bending_restraint == "continuous_compression_flange"
        and segment.restraint_status == "verified"
    ):
        return "verified", [], []

    candidates_by_id = {
        candidate.id: candidate for candidate in definition.restraint_candidates
    }
    effective_ids: list[str] = []
    effective_check_ids: list[str] = []
    boundary_statuses: list[
        Literal["missing", "candidate", "inadequate", "verified"]
    ] = []
    for candidate_ids in (
        segment.start_restraint_candidate_ids,
        segment.end_restraint_candidate_ids,
    ):
        effective = [
            candidates_by_id[candidate_id]
            for candidate_id in candidate_ids
            if candidate_id in candidates_by_id
            and candidates_by_id[candidate_id].restrains_lateral_translation
            and candidates_by_id[candidate_id].restrains_twist
            and _candidate_restrains_flange(
                model,
                candidates_by_id[candidate_id],
                compression_flange,
                definition.off_axis_tolerance,
            )
        ]
        effective_ids.extend(candidate.id for candidate in effective)
        if not effective:
            boundary_statuses.append("missing")
        else:
            checks = [
                candidate_checks_by_id[candidate.id]
                for candidate in effective
                if candidate_checks_by_id is not None
                and candidate.id in candidate_checks_by_id
            ]
            effective_check_ids.extend(check.id for check in checks)
            if any(check.status == "pass" for check in checks):
                boundary_statuses.append("verified")
            elif any(check.status == "candidate" for check in checks):
                boundary_statuses.append("candidate")
            elif any(check.status == "fail" for check in checks):
                boundary_statuses.append("inadequate")
            elif checks and all(check.status == "unsupported" for check in checks):
                boundary_statuses.append("missing")
            elif any(
                candidate.evidence_status == "candidate" for candidate in effective
            ):
                boundary_statuses.append("candidate")
            else:
                boundary_statuses.append("missing")
    if "missing" in boundary_statuses:
        return "missing", sorted(set(effective_ids)), sorted(set(effective_check_ids))
    if "inadequate" in boundary_statuses:
        return (
            "inadequate",
            sorted(set(effective_ids)),
            sorted(set(effective_check_ids)),
        )
    if "candidate" in boundary_statuses:
        return "candidate", sorted(set(effective_ids)), sorted(set(effective_check_ids))
    return "verified", sorted(set(effective_ids)), sorted(set(effective_check_ids))


def _position_on_member(declaration, distance_m: float) -> Vector3:
    member_length = _length(declaration.start, declaration.end)
    ratio = distance_m / member_length if member_length > 0 else 0.0
    return Vector3(
        x=declaration.start.x + (declaration.end.x - declaration.start.x) * ratio,
        y=declaration.start.y + (declaration.end.y - declaration.start.y) * ratio,
        z=declaration.start.z + (declaration.end.z - declaration.start.z) * ratio,
    )


def _member_restraint_traces(
    model,
    analysis,
    *,
    combination_id: str,
    connection_checks_by_id: Mapping[str, ConnectionCheck] | None = None,
) -> list[MemberRestraintTrace]:
    definition = analysis.member_stability_verification
    if definition is None:
        return []
    candidate_checks = _member_restraint_candidate_checks(
        model,
        analysis,
        combination_id=combination_id,
        connection_checks_by_id=connection_checks_by_id,
    )
    candidate_checks_by_id = {check.candidate_id: check for check in candidate_checks}
    members_by_id = {member.id: member for member in analysis.members}
    traces: list[MemberRestraintTrace] = []
    for segment in definition.segments:
        declaration = members_by_id[segment.member_id]
        member = model.members[segment.member_id]
        sample_distances = sorted(
            {
                segment.start_distance_m,
                segment.end_distance_m,
                *(
                    distance
                    for distance in _member_station_distances(analysis, declaration)
                    if segment.start_distance_m < distance < segment.end_distance_m
                ),
            }
        )
        split_distances = [segment.start_distance_m, segment.end_distance_m]
        for start_distance, end_distance in zip(sample_distances, sample_distances[1:]):
            start_moment = member.moment("Mz", start_distance, combination_id)
            end_moment = member.moment("Mz", end_distance, combination_id)
            if start_moment * end_moment < 0:
                fraction = abs(start_moment) / (abs(start_moment) + abs(end_moment))
                split_distances.append(
                    start_distance + fraction * (end_distance - start_distance)
                )
        split_distances = sorted(
            set(round(distance, 12) for distance in split_distances)
        )
        for index, (start_distance, end_distance) in enumerate(
            zip(split_distances, split_distances[1:]),
            start=1,
        ):
            if end_distance - start_distance <= 1e-9:
                continue
            midpoint = (start_distance + end_distance) / 2.0
            compression = _compression_flange(
                member.moment("Mz", midpoint, combination_id),
                definition.off_axis_tolerance,
            )
            restraint_status, effective_ids, effective_check_ids = (
                _segment_restraint_state(
                    model,
                    definition,
                    segment,
                    compression,
                    candidate_checks_by_id,
                )
            )
            effective_checks = [
                candidate_checks_by_id[candidate_id]
                for candidate_id in effective_ids
                if candidate_id in candidate_checks_by_id
            ]
            required_forces = [
                check.required_force_kN
                for check in effective_checks
                if check.required_force_kN is not None
            ]
            available_forces = [
                check.available_force_kN
                for check in effective_checks
                if check.available_force_kN is not None
            ]
            force_utilisations = [
                check.force_utilisation
                for check in effective_checks
                if check.force_utilisation is not None
            ]
            trace_status: Literal[
                "missing",
                "candidate",
                "inadequate",
                "verified",
                "not_required",
            ] = "not_required" if compression == "none" else restraint_status
            traces.append(
                MemberRestraintTrace(
                    id=f"trace-{segment.id}-{combination_id}-{index}",
                    member_id=segment.member_id,
                    combination_id=combination_id,
                    segment_start_m=start_distance,
                    segment_end_m=end_distance,
                    start_position=_position_on_member(declaration, start_distance),
                    end_position=_position_on_member(declaration, end_distance),
                    compression_flange=compression,
                    status=trace_status,
                    start_restraint_candidate_ids=(
                        segment.start_restraint_candidate_ids
                    ),
                    end_restraint_candidate_ids=(segment.end_restraint_candidate_ids),
                    effective_restraint_candidate_ids=effective_ids,
                    governing_candidate_check_ids=effective_check_ids,
                    required_restraint_force_kN=(
                        max(required_forces)
                        if effective_checks
                        and len(required_forces) == len(effective_checks)
                        else None
                    ),
                    available_restraint_force_kN=(
                        min(available_forces)
                        if effective_checks
                        and len(available_forces) == len(effective_checks)
                        else None
                    ),
                    restraint_force_utilisation=(
                        max(force_utilisations)
                        if effective_checks
                        and len(force_utilisations) == len(effective_checks)
                        else None
                    ),
                    basis=(
                        f"Signed PyNite local Mz identifies {compression.replace('_', ' ')} "
                        f"as the compression flange. Restraint state {trace_status} "
                        "requires effective lateral-translation and twist restraint at "
                        "both physical segment boundaries."
                    ),
                )
            )
    return traces


def _member_stability_checks(
    model,
    analysis,
    *,
    connection_checks_by_id: Mapping[str, ConnectionCheck] | None = None,
) -> list[MemberStabilityCheck]:
    definition = analysis.member_stability_verification
    if definition is None:
        return []

    members_by_id = {member.id: member for member in analysis.members}
    sections_by_id = {section.id: section for section in analysis.sections}
    candidate_checks_by_combination = {
        combination_id: {
            check.candidate_id: check
            for check in _member_restraint_candidate_checks(
                model,
                analysis,
                combination_id=combination_id,
                connection_checks_by_id=connection_checks_by_id,
            )
        }
        for combination_id in definition.combination_ids
    }
    physical_length_by_component_m: dict[str, float] = defaultdict(float)
    for declaration in analysis.members:
        if getattr(declaration, "analytical_role", "physical") != "physical":
            continue
        physical_length_by_component_m[declaration.component_id] += _length(
            declaration.start,
            declaration.end,
        )
    checks: list[MemberStabilityCheck] = []
    for segment in definition.segments:
        declaration = members_by_id[segment.member_id]
        section = sections_by_id[declaration.section_id]
        unbraced_length_m = physical_length_by_component_m.get(
            declaration.component_id,
            segment.end_distance_m - segment.start_distance_m,
        )
        try:
            capacity = member_compression_capacity(
                definition.pack_id,
                section,
                unbraced_length_m=unbraced_length_m,
                minor_axis_effective_length_factor=(
                    segment.minor_axis_effective_length_factor
                ),
                torsional_effective_length_factor=(
                    segment.torsional_effective_length_factor
                ),
            )
        except CapacityPackError as exc:
            checks.append(
                MemberStabilityCheck(
                    segment_id=segment.id,
                    member_id=segment.member_id,
                    label=f"{declaration.label} member stability",
                    pack_id=definition.pack_id,
                    status="unsupported",
                    segment_start_m=segment.start_distance_m,
                    segment_end_m=segment.end_distance_m,
                    unbraced_length_m=unbraced_length_m,
                    lateral_bending_restraint=(segment.lateral_bending_restraint),
                    restraint_status=segment.restraint_status,
                    distortional_buckling_status=(segment.distortional_buckling_status),
                    basis=f"Member pack could not evaluate this segment: {exc}",
                    assumptions=[
                        "No member resistance has been inferred from incomplete data."
                    ],
                )
            )
            continue

        station_distances = {
            distance
            for distance in _member_station_distances(analysis, declaration)
            if (segment.start_distance_m <= distance <= segment.end_distance_m)
        }
        station_distances.update((segment.start_distance_m, segment.end_distance_m))
        governing: dict[str, Any] | None = None
        compression_flange_values: set[str] = set()
        member = model.members[declaration.id]
        for combination_id in definition.combination_ids:
            for distance in sorted(station_distances):
                axial_kN = abs(member.axial(distance, combination_id))
                signed_major_moment_kNm = member.moment("Mz", distance, combination_id)
                major_moment_kNm = abs(signed_major_moment_kNm)
                minor_moment_kNm = abs(member.moment("My", distance, combination_id))
                web_shear_kN = abs(member.shear("Fy", distance, combination_id))
                off_axis_shear_kN = abs(member.shear("Fz", distance, combination_id))
                torsion_kNm = abs(member.torque(distance, combination_id))
                axial_utilisation = (
                    axial_kN / capacity.design_member_compression_capacity_kN
                )
                major_bending_utilisation = (
                    major_moment_kNm / capacity.design_major_bending_capacity_kNm
                )
                minor_bending_utilisation = (
                    minor_moment_kNm / capacity.design_minor_bending_capacity_kNm
                )
                web_shear_utilisation = (
                    web_shear_kN / capacity.design_web_shear_capacity_kN
                )
                off_axis_shear_utilisation = (
                    off_axis_shear_kN
                    / capacity.design_off_axis_shear_capacity_kN
                )
                torsion_utilisation = (
                    torsion_kNm
                    / capacity.design_st_venant_torsion_capacity_kNm
                )
                major_axis_amplification_factor = 1.0 / max(
                    1e-9,
                    1.0
                    - axial_kN
                    / capacity.elastic_major_axis_flexural_buckling_load_kN,
                )
                minor_axis_amplification_factor = 1.0 / max(
                    1e-9,
                    1.0
                    - axial_kN
                    / capacity.elastic_minor_axis_flexural_buckling_load_kN,
                )
                compression_flange = _compression_flange(
                    signed_major_moment_kNm,
                    definition.off_axis_tolerance,
                )
                if compression_flange != "none":
                    compression_flange_values.add(compression_flange)
                restraint_status, effective_candidate_ids, _candidate_check_ids = (
                    _segment_restraint_state(
                        model,
                        definition,
                        segment,
                        compression_flange,
                        candidate_checks_by_combination[combination_id],
                    )
                )
                biaxial_member_interaction_utilisation = (
                    axial_utilisation
                    + major_bending_utilisation
                    * major_axis_amplification_factor
                    + minor_bending_utilisation
                    * minor_axis_amplification_factor
                )
                major_bending_shear_utilisation = sqrt(
                    major_bending_utilisation**2 + web_shear_utilisation**2
                )
                minor_bending_shear_utilisation = sqrt(
                    minor_bending_utilisation**2
                    + off_axis_shear_utilisation**2
                )
                utilisation = (
                    max(
                        biaxial_member_interaction_utilisation,
                        major_bending_shear_utilisation,
                        minor_bending_shear_utilisation,
                    )
                    + torsion_utilisation
                )
                candidate: dict[str, Any] = {
                    "combination_id": combination_id,
                    "distance": distance,
                    "axial": axial_kN,
                    "major_moment": major_moment_kNm,
                    "minor_moment": minor_moment_kNm,
                    "web_shear": web_shear_kN,
                    "off_axis_shear": off_axis_shear_kN,
                    "torsion": torsion_kNm,
                    "compression_flange": compression_flange,
                    "restraint_status": restraint_status,
                    "restraint_candidate_ids": effective_candidate_ids,
                    "axial_utilisation": axial_utilisation,
                    "major_bending_utilisation": major_bending_utilisation,
                    "minor_bending_utilisation": minor_bending_utilisation,
                    "web_shear_utilisation": web_shear_utilisation,
                    "off_axis_shear_utilisation": off_axis_shear_utilisation,
                    "torsion_utilisation": torsion_utilisation,
                    "major_axis_amplification_factor": (
                        major_axis_amplification_factor
                    ),
                    "minor_axis_amplification_factor": (
                        minor_axis_amplification_factor
                    ),
                    "biaxial_member_interaction_utilisation": (
                        biaxial_member_interaction_utilisation
                    ),
                    "major_bending_shear_utilisation": (
                        major_bending_shear_utilisation
                    ),
                    "minor_bending_shear_utilisation": (
                        minor_bending_shear_utilisation
                    ),
                    "utilisation": utilisation,
                }
                if governing is None or float(candidate["utilisation"]) > float(
                    governing["utilisation"]
                ):
                    governing = candidate

        if governing is None:
            raise StructuralAnalysisError(
                f"member-stability segment {segment.id!r} has no stations"
            )
        if float(governing["utilisation"]) > 1.0:
            status: Literal["pass", "fail", "unsupported"] = "fail"
        else:
            # The capacity above uses the complete physical component as the
            # unbraced length. A result below unity therefore needs no external
            # restraint credit. Verified bridges may later subdivide a component,
            # but a missing candidate cannot invalidate this conservative result.
            status = "pass"
        checks.append(
            MemberStabilityCheck(
                segment_id=segment.id,
                member_id=segment.member_id,
                label=f"{declaration.label} member stability",
                pack_id=definition.pack_id,
                status=status,
                governing_combination_id=str(governing["combination_id"]),
                governing_station_m=float(governing["distance"]),
                segment_start_m=segment.start_distance_m,
                segment_end_m=segment.end_distance_m,
                unbraced_length_m=unbraced_length_m,
                axial_kN=float(governing["axial"]),
                major_moment_kNm=float(governing["major_moment"]),
                minor_moment_kNm=float(governing["minor_moment"]),
                web_shear_kN=float(governing["web_shear"]),
                off_axis_shear_kN=float(governing["off_axis_shear"]),
                torsion_kNm=float(governing["torsion"]),
                elastic_flexural_buckling_stress_MPa=(
                    capacity.elastic_flexural_buckling_stress_MPa
                ),
                elastic_torsional_buckling_stress_MPa=(
                    capacity.elastic_torsional_buckling_stress_MPa
                ),
                elastic_flexural_torsional_buckling_stress_MPa=(
                    capacity.elastic_flexural_torsional_buckling_stress_MPa
                ),
                elastic_distortional_compression_stress_MPa=(
                    capacity.elastic_distortional_compression_stress_MPa
                ),
                elastic_distortional_bending_stress_MPa=(
                    capacity.elastic_distortional_bending_stress_MPa
                ),
                elastic_lateral_torsional_buckling_moment_kNm=(
                    capacity.elastic_lateral_torsional_buckling_moment_kNm
                ),
                elastic_minor_lateral_torsional_buckling_moment_kNm=(
                    capacity.elastic_minor_lateral_torsional_buckling_moment_kNm
                ),
                elastic_major_axis_flexural_buckling_load_kN=(
                    capacity.elastic_major_axis_flexural_buckling_load_kN
                ),
                elastic_minor_axis_flexural_buckling_load_kN=(
                    capacity.elastic_minor_axis_flexural_buckling_load_kN
                ),
                nominal_global_buckling_stress_MPa=(
                    capacity.nominal_global_buckling_stress_MPa
                ),
                nominal_global_compression_capacity_kN=(
                    capacity.nominal_global_compression_capacity_kN
                ),
                nominal_distortional_compression_capacity_kN=(
                    capacity.nominal_distortional_compression_capacity_kN
                ),
                nominal_lateral_torsional_bending_capacity_kNm=(
                    capacity.nominal_lateral_torsional_bending_capacity_kNm
                ),
                nominal_distortional_bending_capacity_kNm=(
                    capacity.nominal_distortional_bending_capacity_kNm
                ),
                nominal_minor_lateral_torsional_bending_capacity_kNm=(
                    capacity.nominal_minor_lateral_torsional_bending_capacity_kNm
                ),
                design_member_compression_capacity_kN=(
                    capacity.design_member_compression_capacity_kN
                ),
                design_major_bending_capacity_kNm=(
                    capacity.design_major_bending_capacity_kNm
                ),
                design_minor_bending_capacity_kNm=(
                    capacity.design_minor_bending_capacity_kNm
                ),
                design_global_compression_capacity_kN=(
                    capacity.design_global_compression_capacity_kN
                ),
                design_distortional_compression_capacity_kN=(
                    capacity.design_distortional_compression_capacity_kN
                ),
                design_lateral_torsional_bending_capacity_kNm=(
                    capacity.design_lateral_torsional_bending_capacity_kNm
                ),
                design_distortional_bending_capacity_kNm=(
                    capacity.design_distortional_bending_capacity_kNm
                ),
                design_section_minor_bending_capacity_kNm=(
                    capacity.design_section_minor_bending_capacity_kNm
                ),
                design_minor_lateral_torsional_bending_capacity_kNm=(
                    capacity.design_minor_lateral_torsional_bending_capacity_kNm
                ),
                design_web_shear_capacity_kN=(
                    capacity.design_web_shear_capacity_kN
                ),
                design_off_axis_shear_capacity_kN=(
                    capacity.design_off_axis_shear_capacity_kN
                ),
                design_st_venant_torsion_capacity_kNm=(
                    capacity.design_st_venant_torsion_capacity_kNm
                ),
                governing_compression_mode=capacity.governing_compression_mode,
                governing_bending_mode=capacity.governing_bending_mode,
                governing_minor_bending_mode=(
                    capacity.governing_minor_bending_mode
                ),
                axial_utilisation=float(governing["axial_utilisation"]),
                axial_bending_utilisation=float(
                    governing["biaxial_member_interaction_utilisation"]
                ),
                major_bending_utilisation=float(
                    governing["major_bending_utilisation"]
                ),
                minor_bending_utilisation=float(
                    governing["minor_bending_utilisation"]
                ),
                web_shear_utilisation=float(governing["web_shear_utilisation"]),
                off_axis_shear_utilisation=float(
                    governing["off_axis_shear_utilisation"]
                ),
                torsion_utilisation=float(governing["torsion_utilisation"]),
                major_axis_amplification_factor=float(
                    governing["major_axis_amplification_factor"]
                ),
                minor_axis_amplification_factor=float(
                    governing["minor_axis_amplification_factor"]
                ),
                biaxial_member_interaction_utilisation=float(
                    governing["biaxial_member_interaction_utilisation"]
                ),
                major_bending_shear_utilisation=float(
                    governing["major_bending_shear_utilisation"]
                ),
                minor_bending_shear_utilisation=float(
                    governing["minor_bending_shear_utilisation"]
                ),
                governing_utilisation=float(governing["utilisation"]),
                lateral_bending_restraint=segment.lateral_bending_restraint,
                restraint_status=cast(
                    Literal[
                        "missing",
                        "candidate",
                        "inadequate",
                        "assumed",
                        "verified",
                    ],
                    governing["restraint_status"],
                ),
                compression_flange=cast(
                    Literal[
                        "positive_local_y",
                        "negative_local_y",
                        "none",
                        "mixed",
                    ],
                    "mixed"
                    if len(compression_flange_values) > 1
                    else next(iter(compression_flange_values), "none"),
                ),
                restraint_candidate_ids=list(governing["restraint_candidate_ids"]),
                distortional_buckling_status="verified",
                section_record_sha256=capacity.section_record_sha256,
                standard_reference=capacity.standard_reference,
                standard_status=capacity.standard_status,
                standard_source_sha256=capacity.standard_source_sha256,
                developments_supplement_sha256=(
                    capacity.developments_supplement_sha256
                ),
                basis=capacity.basis,
                assumptions=[
                    segment.restraint_basis,
                    (
                        "Absolute axial demand is treated as compression; tension "
                        "is not used to improve the result."
                    ),
                    (
                        "The complete physical component is checked as unbraced for "
                        "lateral-torsional buckling with Cb=1. Candidate cladding, "
                        "bridging, or flange restraint is not credited in resistance."
                    ),
                    (
                        "Tertius calculates distortional compression and bending "
                        "resistance from the catalogue section dimensions; any "
                        "project-authored distortional status is not used."
                    ),
                    (
                        "Clause 3.5.1 member interaction uses Cm=1 and calculated "
                        "Euler axial amplification for both bending axes. Clause "
                        "3.3.5 bending-shear interactions are checked about both "
                        "axes; full St-Venant torsion utilisation is added "
                        "linearly without warping-restraint benefit."
                    ),
                ],
            )
        )
    return checks


def _member_max_axial(
    model,
    declaration,
    *,
    combination_id: str,
) -> float:
    member_length = _length(declaration.start, declaration.end)
    member = model.members[declaration.id]
    return max(
        abs(member.axial(member_length * index / STATION_INTERVALS, combination_id))
        for index in range(STATION_INTERVALS + 1)
    )


def _member_global_displacement(
    model,
    *,
    member_id: str,
    distance_m: float,
    combination_id: str,
) -> Vector3:
    member = model.members[member_id]
    rotation = member.T()[:3, :3]
    return _local_to_global(
        rotation,
        (
            member.deflection("dx", distance_m, combination_id),
            member.deflection("dy", distance_m, combination_id),
            member.deflection("dz", distance_m, combination_id),
        ),
    )


def _local_to_global(rotation, values: tuple[float, float, float]) -> Vector3:
    return _vector(
        tuple(
            sum(float(rotation[row][column]) * values[row] for row in range(3))
            for column in range(3)
        )
    )


def _relative_transverse_deflection_mm(
    local_displacement_mm: tuple[float, float, float],
    start_displacement_mm: tuple[float, float, float],
    end_displacement_mm: tuple[float, float, float],
    ratio: float,
) -> float:
    """Return member bending deflection relative to its displaced end chord.

    PyNite's member deflections include rigid-body translation and rotation of
    the member ends. Those movements belong in the frame drift result, but an
    L/n member-deflection check must compare the member curve with the straight
    chord between its displaced supports. Counting absolute frame sway against
    a short bridge's L/250 limit creates severe false failures.
    """

    chord_y = start_displacement_mm[1] + (
        end_displacement_mm[1] - start_displacement_mm[1]
    ) * ratio
    chord_z = start_displacement_mm[2] + (
        end_displacement_mm[2] - start_displacement_mm[2]
    ) * ratio
    return sqrt(
        (local_displacement_mm[1] - chord_y) ** 2
        + (local_displacement_mm[2] - chord_z) ** 2
    )


def _coordinate_key(position: Vector3) -> tuple[float, float, float]:
    return tuple(
        round(getattr(position, axis), NODE_COORDINATE_DIGITS)
        for axis in ("x", "y", "z")
    )


def _merge_restraints(
    current: dict[str, bool],
    incoming,
) -> None:
    for axis in ("dx", "dy", "dz", "rx", "ry", "rz"):
        current[axis] = current[axis] or bool(getattr(incoming, axis))


def _released_node_rotational_axes(
    incident_endpoints: Sequence[
        tuple[tuple[tuple[float, float, float], ...], Restraints]
    ],
    *,
    tolerance: float = 1e-9,
) -> tuple[Literal["rx", "ry", "rz"], ...]:
    """Return global node rotations with no member-end stiffness contribution.

    PyNite retains the global rotational degrees of freedom at a shared node
    even when every incident member end releases the local rotation that could
    resist one of those global directions.  Such a degree of freedom is only
    release bookkeeping: restraining it cannot attract moment because no
    connected member contributes stiffness in that direction.  Member local
    axes are projected into the global system so differently oriented pinned
    members are handled without assuming their axes are parallel.
    """

    if not incident_endpoints:
        return ()
    released_global_axes: list[Literal["rx", "ry", "rz"]] = []
    local_axis_names = ("rx", "ry", "rz")
    for global_axis_index, global_axis_name in enumerate(local_axis_names):
        has_member_stiffness = any(
            not getattr(releases, local_axis_name)
            and abs(rotation[local_axis_index][global_axis_index]) > tolerance
            for rotation, releases in incident_endpoints
            for local_axis_index, local_axis_name in enumerate(local_axis_names)
        )
        if not has_member_stiffness:
            released_global_axes.append(global_axis_name)
    return tuple(released_global_axes)


def _released_rotational_datum_restraints(
    members: Sequence[
        tuple[
            str,
            str,
            tuple[tuple[float, float, float], ...],
            Restraints,
            Restraints,
        ]
    ],
    node_restraints: Mapping[str, Mapping[str, bool]],
    *,
    tolerance: float = 1e-9,
) -> tuple[tuple[str, Literal["rx", "ry", "rz"]], ...]:
    """Choose one datum for an otherwise free, axis-aligned torsion chain.

    A member with both bending rotations released still couples the torsional
    rotations at its two ends.  If neither end has an absolute rotational datum,
    equal rotation at both ends is a zero-energy bookkeeping mode: it creates no
    member strain or moment, but PyNite retains it in the global matrix.  This
    helper fixes one arbitrary rotation only when the torsion axis is aligned to
    one global axis and no incident member supplies bending stiffness in that
    direction.  Translational mechanisms and genuine bending mechanisms are
    intentionally outside this rule.
    """

    restraints: list[tuple[str, Literal["rx", "ry", "rz"]]] = []
    axis_names: tuple[Literal["rx", "ry", "rz"], ...] = ("rx", "ry", "rz")
    for global_axis_index, global_axis_name in enumerate(axis_names):
        adjacency: dict[str, set[str]] = defaultdict(set)
        bending_stiffness_nodes: set[str] = set()
        for start_node_id, end_node_id, rotation, start_releases, end_releases in members:
            for node_id, releases in (
                (start_node_id, start_releases),
                (end_node_id, end_releases),
            ):
                if any(
                    not getattr(releases, local_axis_name)
                    and abs(rotation[local_axis_index][global_axis_index]) > tolerance
                    for local_axis_index, local_axis_name in ((1, "ry"), (2, "rz"))
                ):
                    bending_stiffness_nodes.add(node_id)

            torsion_axis_aligned = (
                abs(rotation[0][global_axis_index]) >= 1.0 - tolerance
                and all(
                    abs(rotation[0][other_axis_index]) <= tolerance
                    for other_axis_index in range(3)
                    if other_axis_index != global_axis_index
                )
            )
            if (
                torsion_axis_aligned
                and not start_releases.rx
                and not end_releases.rx
            ):
                adjacency[start_node_id].add(end_node_id)
                adjacency[end_node_id].add(start_node_id)

        visited: set[str] = set()
        for root_node_id in sorted(adjacency):
            if root_node_id in visited:
                continue
            component: set[str] = set()
            pending = [root_node_id]
            while pending:
                node_id = pending.pop()
                if node_id in component:
                    continue
                component.add(node_id)
                pending.extend(adjacency[node_id] - component)
            visited.update(component)
            if any(
                node_id in bending_stiffness_nodes
                or bool(node_restraints[node_id][global_axis_name])
                for node_id in component
            ):
                continue
            restraints.append((min(component), global_axis_name))
    return tuple(restraints)


def _combination_list(analysis) -> list[LoadCombination]:
    if analysis.load_combinations:
        return list(analysis.load_combinations)
    used_case_ids = {
        load.case_id
        for load in (
            *analysis.member_loads,
            *analysis.member_distributed_loads,
        )
    }
    return [
        LoadCombination(
            id=DEFAULT_COMBINATION_ID,
            label="Serviceability — all authored actions at 1.0",
            limit_state="serviceability",
            factors={
                case.id: 1.0 for case in analysis.load_cases if case.id in used_case_ids
            },
        )
    ]


def _select_combination(
    combinations: list[LoadCombination],
    combination_id: str | None,
) -> LoadCombination:
    if combination_id is not None:
        selected = next(
            (
                combination
                for combination in combinations
                if combination.id == combination_id
            ),
            None,
        )
        if selected is None:
            raise StructuralAnalysisError(
                f"Unknown load combination {combination_id!r}; available combinations: "
                f"{[combination.id for combination in combinations]}"
            )
        return selected
    return next(
        (
            combination
            for combination in combinations
            if combination.limit_state == "serviceability"
        ),
        combinations[0],
    )


def _governing_working_combination(
    model,
    analysis,
    combinations: list[LoadCombination],
) -> LoadCombination:
    """Select the worst credible service combination in Structural workbench state."""

    excluded_words = ("demo", "deliberate", "illustrative")
    candidates = [
        combination
        for combination in combinations
        if combination.limit_state == "serviceability"
        and not any(
            word in f"{combination.id} {combination.label}".lower()
            for word in excluded_words
        )
    ]
    if not candidates:
        return _select_combination(combinations, None)

    sections = {section.id: section for section in analysis.sections}

    def score(combination: LoadCombination) -> tuple[float, float, float]:
        maximum_ratio = 0.0
        maximum_moment = 0.0
        maximum_displacement = 0.0
        for declaration in analysis.members:
            moment, displacement = _member_extrema(
                model,
                declaration,
                combination_id=combination.id,
                point_loads=[
                    load
                    for load in analysis.member_loads
                    if load.member_id == declaration.id
                ],
                distributed_loads=[
                    load
                    for load in analysis.member_distributed_loads
                    if load.member_id == declaration.id
                ],
            )
            maximum_moment = max(maximum_moment, moment)
            maximum_displacement = max(maximum_displacement, displacement)
            section = sections[declaration.section_id]
            if section.bending_reference_kNm:
                maximum_ratio = max(
                    maximum_ratio,
                    moment / section.bending_reference_kNm,
                )
            member_length_mm = (
                declaration.serviceability_span_m
                or _length(declaration.start, declaration.end)
            ) * 1000.0
            displacement_limit = declaration.deflection_limit_mm
            if (
                displacement_limit is None
                and declaration.deflection_limit_ratio is not None
            ):
                displacement_limit = (
                    member_length_mm / declaration.deflection_limit_ratio
                )
            if displacement_limit:
                maximum_ratio = max(
                    maximum_ratio,
                    displacement / displacement_limit,
                )
        return maximum_ratio, maximum_moment, maximum_displacement

    return max(candidates, key=score)


def _distributed_resultant(
    load: MemberDistributedLoad,
) -> tuple[Vector3, Vector3]:
    """Return force and first moment about the start of the loaded segment."""

    span = load.end_distance_m - load.start_distance_m
    resultant = Vector3(
        x=span * (load.start_force_kN_m.x + load.end_force_kN_m.x) / 2.0,
        y=span * (load.start_force_kN_m.y + load.end_force_kN_m.y) / 2.0,
        z=span * (load.start_force_kN_m.z + load.end_force_kN_m.z) / 2.0,
    )
    first_moment = Vector3(
        x=span**2 * (load.start_force_kN_m.x + 2.0 * load.end_force_kN_m.x) / 6.0,
        y=span**2 * (load.start_force_kN_m.y + 2.0 * load.end_force_kN_m.y) / 6.0,
        z=span**2 * (load.start_force_kN_m.z + 2.0 * load.end_force_kN_m.z) / 6.0,
    )
    return resultant, first_moment


def _load_summary(
    analysis,
    combination: LoadCombination,
) -> LoadSummary:
    case_categories = {case.id: case.category for case in analysis.load_cases}
    sections = {section.id: section for section in analysis.sections}
    members = {member.id: member for member in analysis.members}
    member_mass_kg = 0.0
    self_weight_kN = 0.0
    additional_dead_load_kN = 0.0
    imposed_load_kN = 0.0
    wind_load_kN = 0.0

    for point_load_summary in analysis.member_loads:
        factor = abs(combination.factors.get(point_load_summary.case_id, 0.0))
        if factor == 0:
            continue
        magnitude = _magnitude(point_load_summary.force) * factor
        category = case_categories[point_load_summary.case_id]
        if category == "dead":
            additional_dead_load_kN += magnitude
        elif category == "live":
            imposed_load_kN += magnitude
        elif category == "wind":
            wind_load_kN += magnitude

    for line_load_summary in analysis.member_distributed_loads:
        factor = abs(combination.factors.get(line_load_summary.case_id, 0.0))
        if factor == 0:
            continue
        resultant, _first_moment = _distributed_resultant(line_load_summary)
        magnitude = _magnitude(resultant) * factor
        category = case_categories[line_load_summary.case_id]
        if line_load_summary.source_kind == "self_weight":
            self_weight_kN += magnitude
            declaration = members[line_load_summary.member_id]
            section = sections[declaration.section_id]
            if section.mass_kg_m is not None:
                member_mass_kg += section.mass_kg_m * (
                    line_load_summary.end_distance_m
                    - line_load_summary.start_distance_m
                )
        elif category == "dead":
            additional_dead_load_kN += magnitude
        elif category == "live":
            imposed_load_kN += magnitude
        elif category == "wind":
            wind_load_kN += magnitude

    return LoadSummary(
        member_mass_kg=member_mass_kg,
        self_weight_kN=self_weight_kN,
        additional_dead_load_kN=additional_dead_load_kN,
        imposed_load_kN=imposed_load_kN,
        wind_load_kN=wind_load_kN,
    )


def _certification_evidence(
    *,
    capture: ProjectStructuralCapture,
    analysis,
    combination: LoadCombination,
    nodes: list[StructuralNode],
    members: list[StructuralMember],
    member_results: list[MemberResult],
    member_checks: list[MemberCheck],
    connection_checks: list[ConnectionCheck],
    tension_member_checks: list[TensionMemberCheck],
    bracing_load_path_traces: list[BracingLoadPathTrace],
    cross_section_checks: list[MemberCrossSectionCheck],
    member_stability_checks: list[MemberStabilityCheck],
    member_restraint_candidate_checks: list[MemberRestraintCandidateCheck],
    serviceability_checks: list[ServiceabilityCheck],
    equilibrium_status: Literal["pass", "fail"],
    residual: float,
    equilibrium_tolerance: float,
    stability_result: StabilityResult | None,
) -> tuple[list[VerificationStage], list[CalculationSheet]]:
    """Build inspectable evidence for the Australian verification process.

    These sheets distinguish a completed model/data calculation from an
    engineering verification. Unsupported resistance and stability stages are
    deliberately blocked rather than inferred from an elastic demand result.
    SCI P399 is retained only as supplemental portal-frame analysis guidance.
    """

    basis = capture.design_basis
    basis_references = (
        [
            f"{basis.framework_label} — {basis.framework_reference}",
            *(f"{role}: {reference}" for role, reference in basis.standards.items()),
        ]
        if basis is not None
        else ["No design basis declared in Structural workbench state."]
    )
    member_ids = [member.id for member in members]
    node_ids = [node.id for node in nodes]
    case_ids = [case.id for case in analysis.load_cases]
    joint_connections = [
        connection
        for connection in capture.connections
        if connection.joint_model is not None
    ]

    geometry_equations = [
        CalculationEquation(
            label=f"{declaration.label} analytical length",
            expression="L = |x_j - x_i|",
            substitution=(
                f"|({declaration.end.x:g}, {declaration.end.y:g}, "
                f"{declaration.end.z:g}) - ({declaration.start.x:g}, "
                f"{declaration.start.y:g}, {declaration.start.z:g})|"
            ),
            result=_length(declaration.start, declaration.end),
            unit="m",
        )
        for declaration in analysis.members
    ]
    action_equations: list[CalculationEquation] = []
    action_inputs: list[CalculationInput] = []
    wind_bases_by_id = {
        wind_basis.id: wind_basis for wind_basis in capture.wind_action_bases
    }
    for wind_basis in capture.wind_action_bases:
        action_inputs.extend(
            [
                CalculationInput(
                    symbol=f"site,{wind_basis.id}",
                    label="Site",
                    value=wind_basis.site_address,
                    source="Structural workbench wind action basis",
                ),
                CalculationInput(
                    symbol=f"region,{wind_basis.id}",
                    label="Wind region",
                    value=(
                        f"{wind_basis.region} — {wind_basis.region_area} "
                        f"({wind_basis.region_status})"
                    ),
                    source=wind_basis.region_source,
                ),
                CalculationInput(
                    symbol=f"TC,{wind_basis.id}",
                    label="Terrain category",
                    value=wind_basis.terrain_category,
                    source="Site workbench input",
                ),
                CalculationInput(
                    symbol=f"R,{wind_basis.id}",
                    label="Annual recurrence interval",
                    value=wind_basis.annual_recurrence_interval_years,
                    unit="years",
                    source="Site workbench input",
                ),
                CalculationInput(
                    symbol=f"z,{wind_basis.id}",
                    label="Reference height",
                    value=wind_basis.reference_height_m,
                    unit="m",
                    source="Site workbench input",
                ),
                CalculationInput(
                    symbol=f"enclosure,{wind_basis.id}",
                    label="Enclosure / openings",
                    value=(
                        f"{wind_basis.enclosure or 'not declared'}; "
                        f"{wind_basis.openings_operating_state or 'not declared'}"
                    ),
                    source="tertius_site.py action envelope",
                ),
                CalculationInput(
                    symbol=f"selection,{wind_basis.id}",
                    label="Coefficient case selection",
                    value=(wind_basis.coefficient_selection_policy or "not declared"),
                    source="tertius_site.py action envelope",
                ),
            ]
        )
        action_equations.extend(
            [
                CalculationEquation(
                    label=f"{wind_basis.id} site wind speed",
                    expression="V_sit = V_R M_c M_d M_z,cat M_s M_t",
                    substitution=(
                        f"{wind_basis.regional_wind_speed_m_s:g} × "
                        f"{wind_basis.climate_change_multiplier:g} × "
                        f"{wind_basis.direction_multiplier:g} × "
                        f"{wind_basis.terrain_height_multiplier:g} × "
                        f"{wind_basis.shielding_multiplier:g} × "
                        f"{wind_basis.topographic_multiplier:g}"
                    ),
                    result=wind_basis.site_wind_speed_m_s,
                    unit="m/s",
                ),
                CalculationEquation(
                    label=f"{wind_basis.id} free-stream dynamic pressure",
                    expression="q_z = 0.5 ρ V_sit²",
                    substitution=(
                        f"0.5 × 1.2 × {wind_basis.site_wind_speed_m_s:g}² / 1000"
                    ),
                    result=wind_basis.q_z_kPa,
                    unit="kPa",
                ),
            ]
        )
    for load in capture.loads:
        if load.wind_basis_id is not None and load.net_pressure_coefficient is not None:
            wind_basis = wind_bases_by_id[load.wind_basis_id]
            if (
                load.surface_action_pack_id is not None
                and load.external_pressure_coefficient is not None
                and load.internal_pressure_coefficient is not None
                and load.area_reduction_factor is not None
            ):
                action_equations.append(
                    CalculationEquation(
                        label=f"{load.label} surface coefficient",
                        expression=(
                            "C_net = C_p,e K_a K_c,e K_l - C_p,i K_c,i"
                        ),
                        substitution=(
                            f"{load.external_pressure_coefficient:g} Ã— "
                            f"{load.area_reduction_factor:g} Ã— 1 Ã— 1 - "
                            f"({load.internal_pressure_coefficient:g}) Ã— 1"
                        ),
                        result=load.net_pressure_coefficient,
                    )
                )
            action_equations.append(
                CalculationEquation(
                    label=f"{load.label} net surface pressure",
                    expression="p_net = q_z |C_net|",
                    substitution=(
                        f"{wind_basis.q_z_kPa:g} × |{load.net_pressure_coefficient:g}|"
                    ),
                    result=load.pressure_kPa,
                    unit="kPa",
                )
            )
        action_equations.append(
            CalculationEquation(
                label=f"{load.label} resultant",
                expression="F = p A",
                substitution=f"{load.pressure_kPa:g} × {load.area_m2:g}",
                result=load.pressure_kPa * load.area_m2,
                unit="kN",
            )
        )
    for load in analysis.member_distributed_loads:
        resultant, _ = _distributed_resultant(load)
        span = load.end_distance_m - load.start_distance_m
        action_equations.append(
            CalculationEquation(
                label=f"{load.label} line-load resultant",
                expression="F = |(w_1 + w_2)L/2|",
                substitution=(
                    f"|({load.start_force_kN_m.model_dump()} + "
                    f"{load.end_force_kN_m.model_dump()}) × {span:g}/2|"
                ),
                result=_magnitude(resultant),
                unit="kN",
            )
        )
    for load in analysis.member_loads:
        action_equations.append(
            CalculationEquation(
                label=f"{load.label} point-load magnitude",
                expression="F = sqrt(F_x² + F_y² + F_z²)",
                substitution=str(load.force.model_dump()),
                result=_magnitude(load.force),
                unit="kN",
            )
        )

    combination_equations = [
        CalculationEquation(
            label=combination.label,
            expression="E_comb = Σ γ_i E_i",
            substitution=" + ".join(
                f"{factor:g}×{case_id}"
                for case_id, factor in combination.factors.items()
            ),
            result=combination.id,
        )
    ]
    result_outputs = [
        output
        for result in member_results
        for output in (
            CalculationInput(
                symbol=f"M_max,{result.member_id}",
                label=f"{result.member_id} maximum resultant moment",
                value=result.max_moment_kNm,
                unit="kN.m",
                source=f"PyNite {combination.id} sampled diagram",
            ),
            CalculationInput(
                symbol=f"V_max,{result.member_id}",
                label=f"{result.member_id} maximum resultant shear",
                value=result.max_shear_kN,
                unit="kN",
                source=f"PyNite {combination.id} sampled diagram",
            ),
            CalculationInput(
                symbol=f"δ_max,{result.member_id}",
                label=f"{result.member_id} maximum displacement",
                value=result.max_displacement_mm,
                unit="mm",
                source=f"PyNite {combination.id} sampled diagram",
            ),
        )
    ]
    reference_outputs = [
        CalculationInput(
            symbol=f"M_ref,{check.member_id}",
            label=f"{check.member_id} renderer-only bending reference",
            value=check.capacity_kNm
            if check.capacity_kNm is not None
            else "not supplied",
            unit="kN.m" if check.capacity_kNm is not None else None,
            source=check.basis,
        )
        for check in member_checks
    ]
    checked_serviceability = [
        check for check in serviceability_checks if check.status != "not_checked"
    ]
    serviceability_status: Literal["pass", "fail", "not_checked"] = (
        "not_checked"
        if not checked_serviceability
        else "fail"
        if any(check.status == "fail" for check in checked_serviceability)
        else "pass"
    )
    basis_status: Literal["pass", "blocked"] = (
        "pass" if basis is not None else "blocked"
    )
    action_standard_references = [
        reference
        for role, reference in (basis.standards.items() if basis else [])
        if "action" in role or "wind" in role
    ]
    action_basis_ready = bool(action_standard_references) and all(
        "confirm" not in reference.lower() and "not yet active" not in reference.lower()
        for reference in action_standard_references
    )
    wind_surface_loads = [load for load in capture.loads if load.case == "wind"]
    wind_basis_drift = {
        basis.id: verify_site_wind_snapshot(basis.model_dump())
        for basis in capture.wind_action_bases
    }
    unlinked_wind_loads = [
        load.id for load in wind_surface_loads if load.wind_basis_id is None
    ]
    unverified_wind_bases = [
        basis.id
        for basis in capture.wind_action_bases
        if basis.region_status != "verified"
        or basis.table_status != "verified"
        or wind_basis_drift[basis.id]
    ]
    assumed_wind_coefficients = [
        load.id for load in wind_surface_loads if load.coefficient_status == "assumed"
    ]
    working_wind_coefficients = [
        load.id
        for load in wind_surface_loads
        if load.coefficient_status == "working_conservative"
    ]
    blocked_wind_load_paths = [
        path.load_id
        for path in capture.load_paths
        if path.status == "blocked"
        and any(load.id == path.load_id for load in wind_surface_loads)
    ]
    wind_required = bool(
        capture.wind_action_bases
        or any("wind" in role.lower() for role in (basis.standards if basis else {}))
    )
    missing_required_wind_actions = wind_required and not wind_surface_loads
    wind_actions_ready = (not wind_required and not wind_surface_loads) or (
        bool(wind_surface_loads)
        and bool(capture.wind_action_bases)
        and not unlinked_wind_loads
        and not unverified_wind_bases
        and not assumed_wind_coefficients
        and not working_wind_coefficients
        and not blocked_wind_load_paths
    )
    action_basis_ready = action_basis_ready and wind_actions_ready
    action_assumptions = [
        *(
            [
                "Wind actions do not have a complete compiled connection path to "
                "ground: " + ", ".join(blocked_wind_load_paths)
            ]
            if blocked_wind_load_paths
            else []
        ),
        *(
            [
                "The Site/Structural design basis requires wind actions, but no "
                "wind action was generated from the compiled mechanical geometry."
            ]
            if missing_required_wind_actions
            else []
        ),
        *(
            ["The project action standard references still require confirmation."]
            if not action_standard_references
            or any(
                "confirm" in reference.lower() or "not yet active" in reference.lower()
                for reference in action_standard_references
            )
            else []
        ),
        *(
            [
                "Wind loads are not linked to a Structural workbench wind action basis: "
                + ", ".join(unlinked_wind_loads)
            ]
            if unlinked_wind_loads
            else []
        ),
        *(
            [
                "Wind site/table verification remains outstanding for: "
                + ", ".join(unverified_wind_bases)
            ]
            if unverified_wind_bases
            else []
        ),
        *(
            [
                "Net surface pressure coefficients remain assumed for: "
                + ", ".join(assumed_wind_coefficients)
            ]
            if assumed_wind_coefficients
            else []
        ),
        *(
            [
                "Working conservative envelope is active for: "
                + ", ".join(working_wind_coefficients)
                + ". The solver selects the worst available credible service "
                "combination; AS/NZS 1170.2 surface-zone verification remains "
                "an evidence item before final design."
            ]
            if working_wind_coefficients
            else []
        ),
        *(
            [
                f"{basis_id}: {message}"
                for basis_id, messages in wind_basis_drift.items()
                for message in messages
            ]
        ),
    ]
    actions_status: Literal["pass", "warning", "blocked"] = (
        "blocked"
        if not action_equations
        or basis is None
        or missing_required_wind_actions
        or blocked_wind_load_paths
        else "pass"
        if action_basis_ready
        else "warning"
    )
    combination_reference = (
        next(
            (
                reference
                for role, reference in basis.standards.items()
                if "combination" in role
            ),
            "",
        )
        if basis
        else ""
    )
    combinations_status: Literal["pass", "warning", "blocked"] = (
        "blocked"
        if not combination.factors or basis is None
        else "warning"
        if "confirm" in combination_reference.lower()
        or "not yet active" in combination_reference.lower()
        else "pass"
    )
    analysis_status: Literal["pass", "fail", "blocked"] = (
        "blocked"
        if basis is None
        else "pass"
        if equilibrium_status == "pass"
        else "fail"
    )
    stability_definition = analysis.stability
    stability_status: Literal["pass", "fail", "warning", "blocked"]
    stability_direction_evidence_complete = bool(
        stability_result
        and len(stability_result.direction_results) >= 2
        and all(
            result.alpha_cr is not None for result in stability_result.direction_results
        )
    )
    stability_base_model_matches = _analysis_base_model_matches(analysis)
    stability_analysis_basis_ready = bool(
        stability_definition
        and stability_base_model_matches
        and (
            (
                stability_definition.analysis_base_model == "perfectly_pinned"
                and stability_definition.analysis_basis_status
                == "verified_conservative"
            )
            or (
                stability_definition.analysis_basis_status == "verified"
                and (
                    (
                        stability_definition.analysis_base_model == "unspecified"
                        and stability_definition.base_stiffness_status == "verified"
                    )
                    or stability_definition.physical_connection_stiffness_status
                    == "verified"
                )
            )
        )
    )
    if stability_definition is None or stability_result is None:
        stability_status = "blocked"
    elif not stability_result.converged or (
        stability_result.minimum_alpha_cr is not None
        and stability_result.minimum_alpha_cr <= 1.0
    ):
        stability_status = "fail"
    elif (
        stability_result.governing_moment_amplification
        > stability_result.amplification_warning_ratio
        or stability_result.governing_displacement_amplification
        > stability_result.amplification_warning_ratio
        or not stability_direction_evidence_complete
        or not stability_analysis_basis_ready
        or stability_result.simplified_alpha_cr_applicable is False
        or actions_status != "pass"
        or combinations_status != "pass"
    ):
        stability_status = "warning"
    else:
        stability_status = "pass"

    cross_section_definition = analysis.cross_section_verification
    cross_section_status: Literal[
        "pass", "fail", "not_checked", "unsupported", "blocked"
    ]
    if cross_section_definition is None:
        cross_section_status = "not_checked"
    elif not cross_section_checks:
        cross_section_status = "not_checked"
    elif any(check.status == "fail" for check in cross_section_checks):
        cross_section_status = "fail"
    elif any(check.status == "unsupported" for check in cross_section_checks):
        cross_section_status = "unsupported"
    elif all(check.status == "pass" for check in cross_section_checks):
        cross_section_status = "pass"
    else:
        cross_section_status = "not_checked"

    cross_section_equations = [
        equation
        for check in cross_section_checks
        if check.governing_utilisation is not None
        for equation in (
            CalculationEquation(
                label=f"{check.member_id} biaxial axial + bending interaction",
                expression=(
                    "u_NMM = N*/(phi_c N_s) + Mz*/(phi_b M_sz) "
                    "+ My*/(phi_b M_sy)"
                ),
                substitution=(
                    f"{check.axial_kN:g}/{check.design_compression_capacity_kN:g} "
                    f"+ {check.major_moment_kNm:g}/"
                    f"{check.design_major_bending_capacity_kNm:g} + "
                    f"{check.minor_moment_kNm:g}/"
                    f"{check.design_minor_bending_capacity_kNm:g}"
                ),
                result=check.biaxial_axial_bending_utilisation or 0.0,
            ),
            CalculationEquation(
                label=f"{check.member_id} major bending + web shear interaction",
                expression=("u_MV = sqrt[(M*/(phi_b M_s))² + (V*/(phi_v V_v))²]"),
                substitution=(
                    f"sqrt[({check.major_moment_kNm:g}/"
                    f"{check.design_major_bending_capacity_kNm:g})² + "
                    f"({check.web_shear_kN:g}/"
                    f"{check.design_web_shear_capacity_kN:g})²]"
                ),
                result=check.bending_shear_utilisation or 0.0,
            ),
            CalculationEquation(
                label=f"{check.member_id} minor bending + off-axis shear interaction",
                expression=(
                    "u_MyVz = sqrt[(My*/(phi_b M_sy))² + "
                    "(Vz*/(phi_v V_vz))²]"
                ),
                substitution=(
                    f"sqrt[({check.minor_moment_kNm:g}/"
                    f"{check.design_minor_bending_capacity_kNm:g})² + "
                    f"({check.off_axis_shear_kN:g}/"
                    f"{check.design_off_axis_shear_capacity_kN:g})²]"
                ),
                result=check.minor_bending_shear_utilisation or 0.0,
            ),
            CalculationEquation(
                label=f"{check.member_id} open-section torsion screen",
                expression="u_T = T*/[phi_v (0.60 fy J/t)]",
                substitution=(
                    f"{check.torsion_kNm:g}/"
                    f"{check.design_st_venant_torsion_capacity_kNm:g}"
                ),
                result=check.torsion_utilisation or 0.0,
            ),
            CalculationEquation(
                label=f"{check.member_id} governing off-axis section envelope",
                expression=(
                    "u_gov=max(u_NMM, u_MzVy, u_MyVz) + u_T"
                ),
                substitution=(
                    f"max({check.biaxial_axial_bending_utilisation:g}, "
                    f"{check.bending_shear_utilisation:g}, "
                    f"{check.minor_bending_shear_utilisation:g}) + "
                    f"{check.torsion_utilisation:g}"
                ),
                result=check.governing_utilisation or 0.0,
            ),
        )
    ]
    cross_section_equations.extend(
        CalculationEquation(
            label=f"{check.member_id} off-axis support reaction demand",
            expression="R_off-axis* = max |Fz|",
            substitution=f"max |Fz| = {check.off_axis_required_reaction_kN:g} kN",
            result=check.off_axis_required_reaction_kN,
            unit="kN",
        )
        for check in cross_section_checks
        if check.off_axis_required_reaction_kN is not None
    )
    cross_section_outputs = [
        output
        for check in cross_section_checks
        for output in (
            CalculationInput(
                symbol=f"u_gov,{check.member_id}",
                label=f"{check.label} governing utilisation",
                value=(
                    check.governing_utilisation
                    if check.governing_utilisation is not None
                    else check.status
                ),
                source=(
                    f"{check.governing_combination_id or 'no valid envelope'} at "
                    f"{check.governing_station_m or 0:g} m"
                ),
            ),
            CalculationInput(
                symbol=f"phi_b M_s,{check.member_id}",
                label=f"{check.label} design major-bending resistance",
                value=(
                    check.design_major_bending_capacity_kNm
                    if check.design_major_bending_capacity_kNm is not None
                    else "not available"
                ),
                unit=(
                    "kN.m"
                    if check.design_major_bending_capacity_kNm is not None
                    else None
                ),
                source=check.basis,
            ),
            CalculationInput(
                symbol=f"phi_b M_sy,{check.member_id}",
                label=f"{check.label} design minor-bending resistance",
                value=(
                    check.design_minor_bending_capacity_kNm
                    if check.design_minor_bending_capacity_kNm is not None
                    else "not available"
                ),
                unit=(
                    "kN.m"
                    if check.design_minor_bending_capacity_kNm is not None
                    else None
                ),
                source=check.basis,
            ),
            CalculationInput(
                symbol=f"phi_c N_s,{check.member_id}",
                label=f"{check.label} design compression section resistance",
                value=(
                    check.design_compression_capacity_kN
                    if check.design_compression_capacity_kN is not None
                    else "not available"
                ),
                unit=(
                    "kN" if check.design_compression_capacity_kN is not None else None
                ),
                source=check.basis,
            ),
            CalculationInput(
                symbol=f"phi_v V_v,{check.member_id}",
                label=f"{check.label} design web-shear resistance",
                value=(
                    check.design_web_shear_capacity_kN
                    if check.design_web_shear_capacity_kN is not None
                    else "not available"
                ),
                unit=("kN" if check.design_web_shear_capacity_kN is not None else None),
                source=(
                    f"{check.shear_regime or 'unknown'} web; "
                    f"d1/t={check.web_slenderness or 0:g}"
                ),
            ),
            CalculationInput(
                symbol=f"phi_v V_vz,{check.member_id}",
                label=f"{check.label} design off-axis shear resistance",
                value=(
                    check.design_off_axis_shear_capacity_kN
                    if check.design_off_axis_shear_capacity_kN is not None
                    else "not available"
                ),
                unit=(
                    "kN"
                    if check.design_off_axis_shear_capacity_kN is not None
                    else None
                ),
                source=check.basis,
            ),
            CalculationInput(
                symbol=f"phi_v T_v,{check.member_id}",
                label=f"{check.label} full St-Venant torsion resistance",
                value=(
                    check.design_st_venant_torsion_capacity_kNm
                    if check.design_st_venant_torsion_capacity_kNm is not None
                    else "not available"
                ),
                unit=(
                    "kN.m"
                    if check.design_st_venant_torsion_capacity_kNm is not None
                    else None
                ),
                source="No warping or restraint benefit credited.",
            ),
            CalculationInput(
                symbol=f"standard,{check.member_id}",
                label=f"{check.label} accepted calculation basis",
                value=check.standard_reference or "not available",
                source=(
                    f"standard SHA-256={check.standard_source_sha256 or 'missing'}; "
                    f"status={check.standard_status or 'missing'}"
                ),
            ),
        )
    ]
    cross_section_outputs.extend(
        CalculationInput(
            symbol=f"path_off-axis,{check.member_id}",
            label=f"{check.label} off-axis collector path",
            value=(
                " → ".join(check.off_axis_collector_component_ids)
                if check.off_axis_collector_component_ids
                else check.off_axis_load_path_status
            ),
            source=check.off_axis_load_path_basis or "No authored path basis.",
        )
        for check in cross_section_checks
    )

    member_stability_definition = analysis.member_stability_verification
    member_stability_status: Literal[
        "pass", "fail", "not_checked", "unsupported", "blocked"
    ]
    if member_stability_definition is None:
        member_stability_status = "not_checked"
    elif not member_stability_checks:
        member_stability_status = "not_checked"
    elif any(check.status == "fail" for check in member_stability_checks):
        member_stability_status = "fail"
    elif any(check.status == "unsupported" for check in member_stability_checks):
        member_stability_status = "unsupported"
    elif all(check.status == "pass" for check in member_stability_checks):
        member_stability_status = "pass"
    else:
        member_stability_status = "not_checked"

    restraint_status_priority = {
        "not_required": 0,
        "pass": 1,
        "candidate": 2,
        "unsupported": 3,
        "fail": 4,
    }
    governing_restraint_checks_by_candidate: dict[
        str, MemberRestraintCandidateCheck
    ] = {}
    for check in member_restraint_candidate_checks:
        current = governing_restraint_checks_by_candidate.get(check.candidate_id)
        if current is None or (
            restraint_status_priority[check.status],
            check.force_utilisation or 0.0,
            check.required_force_kN or 0.0,
        ) > (
            restraint_status_priority[current.status],
            current.force_utilisation or 0.0,
            current.required_force_kN or 0.0,
        ):
            governing_restraint_checks_by_candidate[check.candidate_id] = check
    governing_restraint_checks = sorted(
        governing_restraint_checks_by_candidate.values(),
        key=lambda check: check.candidate_id,
    )

    bracing_status: Literal["pass", "fail", "warning", "not_checked", "unsupported"]
    if any(check.status == "fail" for check in tension_member_checks) or any(
        trace.status in {"fail", "blocked"} for trace in bracing_load_path_traces
    ):
        bracing_status = "fail"
    elif any(check.status == "fail" for check in member_restraint_candidate_checks):
        bracing_status = "fail"
    elif (
        any(check.status == "unsupported" for check in tension_member_checks)
        or any(trace.status == "candidate" for trace in bracing_load_path_traces)
        or any(
            check.status == "unsupported"
            for check in member_restraint_candidate_checks
        )
    ):
        bracing_status = "unsupported"
    elif (
        not member_restraint_candidate_checks
        and not tension_member_checks
        and not bracing_load_path_traces
    ):
        bracing_status = "not_checked"
    elif all(
        check.status in {"pass", "not_required"}
        for check in member_restraint_candidate_checks
    ) and all(check.status == "pass" for check in tension_member_checks) and all(
        trace.status == "pass" for trace in bracing_load_path_traces
    ):
        bracing_status = "pass"
    else:
        bracing_status = "warning"

    connection_status: Literal["pass", "fail", "not_checked", "unsupported"]
    if not connection_checks:
        connection_status = "not_checked"
    elif any(check.status == "fail" for check in connection_checks):
        connection_status = "fail"
    elif any(check.status == "unsupported" for check in connection_checks):
        connection_status = "unsupported"
    elif all(check.status == "pass" for check in connection_checks):
        connection_status = "pass"
    else:
        connection_status = "not_checked"

    member_stability_equations = [
        equation
        for check in member_stability_checks
        if check.design_member_compression_capacity_kN is not None
        for equation in (
            CalculationEquation(
                label=f"{check.segment_id} flexural-torsional elastic buckling",
                expression=(
                    "Fe,FT = (Fey + Fez)/(2β) [1 - sqrt(1 - 4β Fey Fez/(Fey + Fez)²)]"
                ),
                substitution=(
                    f"Fey/Fez from Lb={check.unbraced_length_m:g} m; "
                    f"Fe,FT={check.elastic_flexural_torsional_buckling_stress_MPa:g}"
                ),
                result=(check.elastic_flexural_torsional_buckling_stress_MPa or 0.0),
                unit="MPa",
            ),
            CalculationEquation(
                label=f"{check.segment_id} global compression utilisation",
                expression="u_N = N*/(phi_c N_c)",
                substitution=(
                    f"{check.axial_kN:g}/"
                    f"{check.design_member_compression_capacity_kN:g}"
                ),
                result=check.axial_utilisation or 0.0,
            ),
            CalculationEquation(
                label=f"{check.segment_id} distortional compression capacity",
                expression=(
                    "lambda_d=sqrt(Ny/Nod); "
                    "Ncd=[1-0.25(Nod/Ny)^0.6](Nod/Ny)^0.6 Ny"
                ),
                substitution=(
                    f"fod={check.elastic_distortional_compression_stress_MPa:g} "
                    "MPa; "
                    f"phi_c Ncd={check.design_distortional_compression_capacity_kN:g}"
                ),
                result=check.design_distortional_compression_capacity_kN or 0.0,
                unit="kN",
            ),
            CalculationEquation(
                label=f"{check.segment_id} unbraced lateral-torsional capacity",
                expression=(
                    "Mo=A r0 sqrt(foy foz), Cb=1; "
                    "phi_b Mb=phi_b (Zxe/Zx) Mc"
                ),
                substitution=(
                    f"Lb={check.unbraced_length_m:g} m; "
                    f"Mo={check.elastic_lateral_torsional_buckling_moment_kNm:g}; "
                    f"phi_b Mb={check.design_lateral_torsional_bending_capacity_kNm:g}"
                ),
                result=(
                    check.design_lateral_torsional_bending_capacity_kNm or 0.0
                ),
                unit="kN.m",
            ),
            CalculationEquation(
                label=f"{check.segment_id} distortional bending capacity",
                expression=(
                    "lambda_d=sqrt(My/Mod); "
                    "Mc=(My/lambda_d)(1-0.22/lambda_d)"
                ),
                substitution=(
                    f"fod={check.elastic_distortional_bending_stress_MPa:g} MPa; "
                    f"phi_b Mc={check.design_distortional_bending_capacity_kNm:g}"
                ),
                result=check.design_distortional_bending_capacity_kNm or 0.0,
                unit="kN.m",
            ),
            CalculationEquation(
                label=f"{check.segment_id} minor-axis lateral-torsional capacity",
                expression=(
                    "Mo=Cs A fox [beta_y/2 + Cs sqrt((beta_y/2)^2 "
                    "+ ro1^2 foz/fox)], CTF=1; "
                    "phi_b Mby=phi_b (Zey/Zy) Mc"
                ),
                substitution=(
                    f"Lb={check.unbraced_length_m:g} m; "
                    f"Mo={check.elastic_minor_lateral_torsional_buckling_moment_kNm:g}; "
                    f"phi_b Mby={check.design_minor_bending_capacity_kNm:g}"
                ),
                result=check.design_minor_bending_capacity_kNm or 0.0,
                unit="kN.m",
            ),
            CalculationEquation(
                label=f"{check.segment_id} biaxial member interaction",
                expression=(
                    "u_NMM = N*/(phi_c Nc) + Cmz Mz*/"
                    "[phi_b Mbz (1-N*/Nez)] + Cmy My*/"
                    "[phi_b Mby (1-N*/Ney)], Cmz=Cmy=1"
                ),
                substitution=(
                    f"{check.axial_kN:g}/{check.design_member_compression_capacity_kN:g}"
                    f" + {check.major_moment_kNm:g}/"
                    f"{check.design_major_bending_capacity_kNm:g}"
                    f"×{check.major_axis_amplification_factor:g}"
                    f" + {check.minor_moment_kNm:g}/"
                    f"{check.design_minor_bending_capacity_kNm:g}"
                    f"×{check.minor_axis_amplification_factor:g}"
                ),
                result=check.biaxial_member_interaction_utilisation or 0.0,
            ),
            CalculationEquation(
                label=f"{check.segment_id} conservative off-axis envelope",
                expression=(
                    "u_gov=max(u_NMM, u_MzVy, u_MyVz) + "
                    "T*/[phi_v (0.60 fy J/t)]"
                ),
                substitution=(
                    f"max({check.biaxial_member_interaction_utilisation:g}, "
                    f"{check.major_bending_shear_utilisation:g}, "
                    f"{check.minor_bending_shear_utilisation:g}) + "
                    f"{check.torsion_utilisation:g}"
                ),
                result=check.governing_utilisation or 0.0,
            ),
        )
    ]
    member_stability_equations.extend(
        CalculationEquation(
            label=f"{check.candidate_id} eccentric-load restraint demand",
            expression="P_L = 1.5(e/d)W; M_L = P_L d",
            substitution=(
                f"P_L={check.required_force_kN:g} kN; "
                f"M_L={check.required_moment_kNm:g} kN.m"
            ),
            result=check.required_force_kN,
            unit="kN",
        )
        for check in member_restraint_candidate_checks
        if check.required_force_kN is not None and check.required_moment_kNm is not None
    )
    member_stability_outputs = [
        output
        for check in member_stability_checks
        for output in (
            CalculationInput(
                symbol=f"phi_c N_c,{check.segment_id}",
                label=f"{check.label} design member compression resistance",
                value=(
                    check.design_member_compression_capacity_kN
                    if check.design_member_compression_capacity_kN is not None
                    else "not available"
                ),
                unit=(
                    "kN"
                    if check.design_member_compression_capacity_kN is not None
                    else None
                ),
                source=check.basis,
            ),
            CalculationInput(
                symbol=f"u_N,{check.segment_id}",
                label=f"{check.label} global compression utilisation",
                value=(
                    check.axial_utilisation
                    if check.axial_utilisation is not None
                    else check.status
                ),
                source=(
                    f"{check.governing_combination_id or 'no valid envelope'} at "
                    f"{check.governing_station_m or 0:g} m"
                ),
            ),
            CalculationInput(
                symbol=f"phi_b M_b,{check.segment_id}",
                label=f"{check.label} governing design bending resistance",
                value=(
                    check.design_major_bending_capacity_kNm
                    if check.design_major_bending_capacity_kNm is not None
                    else "not available"
                ),
                unit=(
                    "kN.m"
                    if check.design_major_bending_capacity_kNm is not None
                    else None
                ),
                source=(
                    f"governing mode={check.governing_bending_mode or 'not available'}; "
                    f"candidate restraint={check.restraint_status}, not credited"
                ),
            ),
            CalculationInput(
                symbol=f"phi_b M_by,{check.segment_id}",
                label=f"{check.label} governing minor-axis bending resistance",
                value=(
                    check.design_minor_bending_capacity_kNm
                    if check.design_minor_bending_capacity_kNm is not None
                    else "not available"
                ),
                unit=(
                    "kN.m"
                    if check.design_minor_bending_capacity_kNm is not None
                    else None
                ),
                source=(
                    f"governing mode="
                    f"{check.governing_minor_bending_mode or 'not available'}; "
                    "CTF=1 and less favourable moment sense"
                ),
            ),
            CalculationInput(
                symbol=f"phi_v T_v,{check.segment_id}",
                label=f"{check.label} full St-Venant torsion resistance",
                value=(
                    check.design_st_venant_torsion_capacity_kNm
                    if check.design_st_venant_torsion_capacity_kNm is not None
                    else "not available"
                ),
                unit=(
                    "kN.m"
                    if check.design_st_venant_torsion_capacity_kNm is not None
                    else None
                ),
                source="No warping or restraint benefit credited.",
            ),
            CalculationInput(
                symbol=f"compression_flange,{check.segment_id}",
                label=f"{check.label} signed-moment compression flange",
                value=check.compression_flange,
                source=(
                    f"Signed PyNite local Mz envelope for "
                    f"{check.governing_combination_id or 'no valid envelope'}"
                ),
            ),
            CalculationInput(
                symbol=f"restraint_candidates,{check.segment_id}",
                label=f"{check.label} effective physical restraint candidates",
                value=(
                    ", ".join(check.restraint_candidate_ids)
                    if check.restraint_candidate_ids
                    else "none"
                ),
                source="Builder-authored axes and registered connection handles",
            ),
            CalculationInput(
                symbol=f"distortional,{check.segment_id}",
                label=f"{check.label} distortional buckling",
                value=check.distortional_buckling_status,
                source=check.basis,
            ),
            CalculationInput(
                symbol=f"standard,{check.segment_id}",
                label=f"{check.label} accepted calculation basis",
                value=check.standard_reference or "not available",
                source=(
                    f"standard SHA-256={check.standard_source_sha256 or 'missing'}; "
                    f"status={check.standard_status or 'missing'}"
                ),
            ),
        )
    ]

    stability_assumptions = (
        [
            "Equivalent horizontal forces/geometric imperfections are not available.",
            "First-/second-order amplification is not available.",
            "Base rotational stiffness has not been declared.",
        ]
        if stability_definition is None or stability_result is None
        else [
            stability_definition.imperfection_basis,
            stability_definition.base_stiffness_basis,
            *(
                [
                    "The analytical base model is not yet a verified conservative "
                    "idealisation."
                ]
                if not stability_analysis_basis_ready
                else []
            ),
            *(
                [
                    "The declared analytical base model does not match the actual "
                    "compiled physical connection topology."
                ]
                if not stability_base_model_matches
                else []
            ),
            *(
                [
                    "Physical GPB/anchor rotational stiffness is not checked, but "
                    "it is not relied upon by the perfectly pinned analysis model."
                ]
                if stability_definition.analysis_base_model == "perfectly_pinned"
                and stability_definition.physical_connection_stiffness_status
                != "verified"
                else []
            ),
            *(
                [
                    "Both sway directions and their NEd/200 NHF displacement "
                    "checks are not all available."
                ]
                if not stability_direction_evidence_complete
                else []
            ),
            *(
                [
                    "The P399 simplified NHF alpha-cr expression is outside its "
                    "rafter axial-force applicability limit; a more accurate "
                    "elastic critical-load analysis is required."
                ]
                if stability_result.simplified_alpha_cr_applicable is False
                else []
            ),
            *(
                [
                    "The linear/P-Delta amplification ratio exceeds the authored "
                    "review threshold. P399 does not define this ratio as a failure "
                    "criterion; the converged second-order design effects remain "
                    "visible for downstream resistance and serviceability checks."
                ]
                if (
                    stability_result.governing_moment_amplification
                    > stability_result.amplification_warning_ratio
                    or stability_result.governing_displacement_amplification
                    > stability_result.amplification_warning_ratio
                )
                else []
            ),
            *(
                [
                    "Action inputs and combinations remain provisional, so this "
                    "stability result cannot become a design pass."
                ]
                if actions_status != "pass" or combinations_status != "pass"
                else []
            ),
        ]
    )
    stability_equations = (
        []
        if stability_result is None
        else [
            equation
            for comparison in stability_result.member_comparisons
            for equation in (
                CalculationEquation(
                    label=f"{comparison.member_id} moment amplification",
                    expression="η_M = M_II / M_I",
                    substitution=(
                        f"{comparison.second_order_max_moment_kNm:g} / "
                        f"{comparison.first_order_max_moment_kNm:g}"
                    ),
                    result=comparison.moment_amplification,
                ),
                CalculationEquation(
                    label=f"{comparison.member_id} displacement amplification",
                    expression="η_δ = δ_II / δ_I",
                    substitution=(
                        f"{comparison.second_order_max_displacement_mm:g} / "
                        f"{comparison.first_order_max_displacement_mm:g}"
                    ),
                    result=comparison.displacement_amplification,
                ),
            )
        ]
    )
    if stability_result is not None:
        stability_equations.extend(
            CalculationEquation(
                label=f"{direction.id} elastic critical load ratio",
                expression="αcr = h / (200 δNHF)",
                substitution=(
                    f"{stability_definition.column_height_m:g} × 1000 / "
                    f"(200 × {direction.nhf_eaves_displacement_mm:g})"
                    if stability_definition is not None
                    and stability_definition.column_height_m is not None
                    and direction.nhf_eaves_displacement_mm > 0
                    else "insufficient NHF displacement evidence"
                ),
                result=(
                    direction.alpha_cr
                    if direction.alpha_cr is not None
                    else "not available"
                ),
            )
            for direction in stability_result.direction_results
        )
        if (
            stability_result.rafter_design_axial_kN is not None
            and stability_result.rafter_elastic_critical_load_kN is not None
            and stability_result.rafter_axial_limit_kN is not None
        ):
            stability_equations.append(
                CalculationEquation(
                    label="Rafter axial-force applicability",
                    expression="NEd ≤ 0.09 Ncr",
                    substitution=(
                        f"{stability_result.rafter_design_axial_kN:g} ≤ "
                        f"0.09 × "
                        f"{stability_result.rafter_elastic_critical_load_kN:g}"
                    ),
                    result=(
                        stability_result.rafter_design_axial_kN
                        / stability_result.rafter_axial_limit_kN
                    ),
                )
            )

    sheets = [
        CalculationSheet(
            id="sheet-au-geometry",
            stage_id="geometry",
            title="Geometry and analytical scheme",
            status=basis_status,
            primary_reference=(
                "NCC 2022 Amendment 2, Volume Two H1P1; Housing Provisions 2.2"
            ),
            supplemental_references=["SCI P399 Sections 3 and 6.1"],
            purpose=(
                "Prove which compiled mechanical components became nodes, members, "
                "and supports."
            ),
            assumptions=list(
                dict.fromkeys(member.assumption for member in analysis.members)
            ),
            inputs=[
                CalculationInput(
                    symbol="n_member",
                    label="Analytical members",
                    value=len(members),
                    source="Compiled-design structural projection",
                ),
                CalculationInput(
                    symbol="n_node",
                    label="Shared analytical nodes",
                    value=len(nodes),
                    source="Coincident member end coordinates",
                ),
                CalculationInput(
                    symbol="n_support",
                    label="Restrained nodes",
                    value=sum(
                        any(node.restraints.model_dump().values()) for node in nodes
                    ),
                    source="Physical connection topology",
                ),
            ],
            equations=geometry_equations,
            references=basis_references,
            related_member_ids=member_ids,
            related_node_ids=node_ids,
        ),
        CalculationSheet(
            id="sheet-au-actions",
            stage_id="actions",
            title="Actions and tributary transfer",
            status=actions_status,
            primary_reference="AS/NZS 1170.0, AS/NZS 1170.1 and AS/NZS 1170.2",
            supplemental_references=["SCI P399 Section 4"],
            purpose="Trace every authored action from its physical source to member load.",
            assumptions=(
                action_assumptions
                or [
                    "The authored actions remain illustrative until the project/site "
                    "Australian action inputs are confirmed."
                ]
            ),
            inputs=action_inputs,
            equations=action_equations,
            references=[
                *basis_references,
                *(
                    f"{basis.id}: {basis.provenance}"
                    for basis in capture.wind_action_bases
                ),
            ],
            related_member_ids=member_ids,
            related_load_case_ids=case_ids,
        ),
        CalculationSheet(
            id="sheet-au-combinations",
            stage_id="combinations",
            title="Active action combination",
            status=combinations_status,
            primary_reference="AS/NZS 1170.0:2002 action combinations",
            supplemental_references=["SCI P399 Section 4.7"],
            purpose="Expose factors selected for the current solve; no hidden combinations.",
            assumptions=(
                []
                if combinations_status == "pass"
                else [
                    "The displayed factors are authored test combinations, not a "
                    "completed AS/NZS 1170.0 project combination set."
                ]
            ),
            inputs=[
                CalculationInput(
                    symbol="limit_state",
                    label="Limit state",
                    value=combination.limit_state,
                    source="Structural workbench configuration",
                )
            ],
            equations=combination_equations,
            references=basis_references,
            related_load_case_ids=list(combination.factors),
            related_combination_ids=[combination.id],
        ),
        CalculationSheet(
            id="sheet-au-analysis",
            stage_id="analysis",
            title="Elastic frame analysis",
            status=analysis_status,
            primary_reference="AS/NZS 1170.0 analysis and limit-state requirements",
            supplemental_references=["SCI P399 Section 5"],
            purpose="Record the active solver method, member demands, and equilibrium audit.",
            inputs=[
                CalculationInput(
                    symbol="method",
                    label="Declared analysis method",
                    value=basis.analysis_method if basis else "not declared",
                    source="Structural workbench configuration",
                ),
                CalculationInput(
                    symbol="r_tol",
                    label="Equilibrium diagnostic tolerance",
                    value=equilibrium_tolerance,
                    unit="kN / kN.m",
                    source=(
                        "Absolute first-order tolerance."
                        if stability_result is None
                        else (
                            "0.1% of the governing absolute action/reaction scale; "
                            "distributed loads are integrated over the displaced "
                            "member geometry."
                        )
                    ),
                ),
            ],
            equations=[
                CalculationEquation(
                    label="Global equilibrium residual",
                    expression="r = max|ΣF, ΣM|",
                    substitution=f"active combination {combination.id}",
                    result=residual,
                    unit="kN / kN.m",
                )
            ],
            outputs=result_outputs,
            references=basis_references,
            related_member_ids=member_ids,
            related_node_ids=node_ids,
            related_combination_ids=[combination.id],
        ),
        CalculationSheet(
            id="sheet-au-stability",
            stage_id="stability",
            title="Imperfections and global stability",
            status=stability_status,
            primary_reference=(
                "AS/NZS 4600:2005 incorporating Amendment No. 1"
            ),
            supplemental_references=["SCI P399 Sections 7.2–7.8"],
            purpose=(
                "Compare first-order elastic and iterative P-Delta response for the "
                "Tertius-resolved imperfection combination."
            ),
            assumptions=stability_assumptions,
            inputs=(
                []
                if stability_definition is None
                else [
                    CalculationInput(
                        symbol="method",
                        label="Second-order method",
                        value=stability_definition.method,
                        source="Structural workbench stability configuration",
                    ),
                    CalculationInput(
                        symbol="combinations",
                        label="Stability combinations",
                        value=(
                            ", ".join(
                                direction.stability_combination_id
                                for direction in stability_definition.direction_cases
                            )
                            or stability_definition.stability_combination_id
                        ),
                        source="Structural workbench stability configuration",
                    ),
                    CalculationInput(
                        symbol="imperfection_cases",
                        label="Imperfection load cases",
                        value=(
                            ", ".join(
                                direction.imperfection_case_id
                                for direction in stability_definition.direction_cases
                            )
                            or stability_definition.imperfection_case_id
                        ),
                        source=stability_definition.imperfection_basis,
                    ),
                    CalculationInput(
                        symbol="analysis_base_model",
                        label="Analytical base model",
                        value=stability_definition.analysis_base_model,
                        source=stability_definition.base_stiffness_basis,
                    ),
                    CalculationInput(
                        symbol="analysis_basis_status",
                        label="Analytical basis status",
                        value=stability_definition.analysis_basis_status,
                        source="Structural workbench stability configuration",
                    ),
                    CalculationInput(
                        symbol="analysis_base_match",
                        label="Base model matches member restraints",
                        value=stability_base_model_matches,
                        source=(
                            "Direct comparison with projected physical start restraints on "
                            "the declared eaves/column members"
                        ),
                    ),
                    CalculationInput(
                        symbol="physical_base_stiffness",
                        label="Physical connection stiffness",
                        value=(
                            stability_definition.physical_connection_stiffness_status
                        ),
                        source=(
                            "Evidence item retained for P399 Connections/Bases; "
                            "not relied upon by a perfectly pinned model."
                        ),
                    ),
                    CalculationInput(
                        symbol="nhf_combinations",
                        label="NEd/200 NHF combinations",
                        value=", ".join(
                            direction.nhf_combination_id
                            for direction in stability_definition.direction_cases
                        )
                        or "not available",
                        source="Tertius structural stability action generator",
                    ),
                    CalculationInput(
                        symbol="η_warning",
                        label="Amplification warning ratio",
                        value=stability_definition.amplification_warning_ratio,
                        source="Structural workbench stability configuration",
                    ),
                ]
            ),
            equations=stability_equations,
            outputs=(
                []
                if stability_result is None
                else [
                    CalculationInput(
                        symbol="η_M,max",
                        label="Governing moment amplification",
                        value=stability_result.governing_moment_amplification,
                        source=(
                            f"PyNite linear/P-Delta comparison for "
                            f"{stability_result.combination_id}"
                        ),
                    ),
                    CalculationInput(
                        symbol="η_δ,max",
                        label="Governing displacement amplification",
                        value=stability_result.governing_displacement_amplification,
                        source=(
                            f"PyNite linear/P-Delta comparison for "
                            f"{stability_result.combination_id}"
                        ),
                    ),
                    CalculationInput(
                        symbol="converged",
                        label="P-Delta convergence",
                        value=stability_result.converged,
                        source="PyNite iterative P-Delta solve",
                    ),
                    CalculationInput(
                        symbol="αcr,min",
                        label="Governing elastic critical load ratio",
                        value=(
                            stability_result.minimum_alpha_cr
                            if stability_result.minimum_alpha_cr is not None
                            else "not available"
                        ),
                        source="P399 NHF method; minimum of authored sway directions",
                    ),
                    CalculationInput(
                        symbol="direction",
                        label="Governing sway direction",
                        value=(
                            stability_result.governing_direction_id or "not available"
                        ),
                        source="Bidirectional stability envelope",
                    ),
                    CalculationInput(
                        symbol="second_order",
                        label="Second-order analysis required",
                        value=(
                            stability_result.second_order_required
                            if stability_result.second_order_required is not None
                            else "not available"
                        ),
                        source="P399 αcr < 10 threshold",
                    ),
                    CalculationInput(
                        symbol="rafter_axial_significant",
                        label="Rafter axial force significant",
                        value=(
                            stability_result.rafter_axial_force_significant
                            if stability_result.rafter_axial_force_significant
                            is not None
                            else "not available"
                        ),
                        source="P399 NEd > 0.09 Ncr applicability check",
                    ),
                ]
            ),
            references=basis_references,
            related_member_ids=member_ids,
            related_node_ids=node_ids,
            related_load_case_ids=(
                [
                    direction.imperfection_case_id
                    for direction in stability_definition.direction_cases
                ]
                or [stability_definition.imperfection_case_id]
                if stability_definition is not None
                else []
            ),
            related_combination_ids=(
                [
                    combination_id
                    for direction in stability_definition.direction_cases
                    for combination_id in (
                        direction.stability_combination_id,
                        direction.nhf_combination_id,
                    )
                ]
                or [stability_definition.stability_combination_id]
                if stability_definition is not None
                else [combination.id]
            ),
        ),
        CalculationSheet(
            id="sheet-au-cross-section",
            stage_id="cross_section",
            title="Cross-section verification",
            status=cross_section_status,
            primary_reference=(
                "AS/NZS 4600:2005 incorporating Amendment No. 1"
            ),
            supplemental_references=["SCI P399 Section 8.1"],
            purpose="Check classification/effective properties and governing force interactions.",
            inputs=(
                []
                if cross_section_definition is None
                else [
                    CalculationInput(
                        symbol="capacity_pack",
                        label="Versioned capacity pack",
                        value=cross_section_definition.pack_id,
                        source="Structural workbench capacity-pack configuration",
                    ),
                    CalculationInput(
                        symbol="ULS_envelope",
                        label="Checked ULS combinations",
                        value=", ".join(cross_section_definition.combination_ids),
                        source="Structural workbench capacity-pack configuration",
                    ),
                ]
            ),
            equations=cross_section_equations,
            assumptions=(
                [
                    assumption
                    for check in cross_section_checks
                    for assumption in check.assumptions
                ]
                if cross_section_checks
                else [
                    "No versioned Australian cross-section capacity pack is selected."
                ]
            ),
            outputs=(
                cross_section_outputs if cross_section_checks else reference_outputs
            ),
            references=basis_references,
            related_member_ids=member_ids,
            related_combination_ids=(
                cross_section_definition.combination_ids
                if cross_section_definition is not None
                else [combination.id]
            ),
        ),
        CalculationSheet(
            id="sheet-au-member-stability",
            stage_id="member_stability",
            title="Member stability",
            status=member_stability_status,
            primary_reference=(
                "AS/NZS 4600:2005 incorporating Amendment No. 1"
            ),
            supplemental_references=["SCI P399 Sections 8.2–8.4"],
            purpose="Verify buckling and axial-bending interaction on restraint-defined segments.",
            inputs=(
                []
                if member_stability_definition is None
                else [
                    CalculationInput(
                        symbol="member_capacity_pack",
                        label="Versioned member-capacity pack",
                        value=member_stability_definition.pack_id,
                        source=("Structural workbench member-stability configuration"),
                    ),
                    CalculationInput(
                        symbol="member_ULS_envelope",
                        label="Checked ULS combinations",
                        value=", ".join(member_stability_definition.combination_ids),
                        source=("Structural workbench member-stability configuration"),
                    ),
                ]
            ),
            equations=member_stability_equations,
            assumptions=(
                [
                    assumption
                    for check in member_stability_checks
                    for assumption in check.assumptions
                ]
                if member_stability_checks
                else ["No restraint-defined member-stability segments are authored."]
            ),
            outputs=(
                member_stability_outputs
                if member_stability_checks
                else reference_outputs
            ),
            references=basis_references,
            related_member_ids=member_ids,
            related_combination_ids=(
                member_stability_definition.combination_ids
                if member_stability_definition is not None
                else [combination.id]
            ),
        ),
        CalculationSheet(
            id="sheet-au-bracing",
            stage_id="bracing",
            title="Bracing and restraint",
            status=bracing_status,
            primary_reference=(
                "NCC Housing Provisions 2.2; AS/NZS 4600:2005+A1"
            ),
            supplemental_references=["SCI P399 Section 9"],
            purpose=(
                "Verify tension-only braces, both end connections, and complete "
                "physical load paths to grounded components; separately verify "
                "member compression-flange restraint."
            ),
            assumptions=[
                "Cladding and fasteners are not assumed to provide unverified restraint.",
                *[
                    assumption
                    for check in tension_member_checks
                    for assumption in check.assumptions
                ],
                *[
                    blocker
                    for trace in bracing_load_path_traces
                    for blocker in trace.blockers
                ],
                *(
                    [
                        "Physical restraint candidates are active, but their complete "
                        "force/moment resistance, stiffness, and longitudinal load path "
                        "are not all verified."
                    ]
                    if member_restraint_candidate_checks
                    else [
                        "No restraint candidates or bracing stiffness checks are active."
                    ]
                ),
            ],
            inputs=[
                CalculationInput(
                    symbol=f"path,{trace.member_id}",
                    label=f"{trace.member_id} brace-to-ground path",
                    value=trace.status,
                    source=(
                        " → ".join(trace.component_ids)
                        if trace.component_ids
                        else trace.basis
                    ),
                )
                for trace in bracing_load_path_traces
            ]
            + [
                CalculationInput(
                    symbol=f"restraint,{check.candidate_id}",
                    label=f"{check.candidate_id} demand/capacity state",
                    value=check.status,
                    source=(
                        f"{check.combination_id}; identity={check.identity_status}; "
                        f"stiffness={check.stiffness_status}; "
                        f"anchorage={check.anchorage_status}; {check.provenance}; "
                        f"required={check.required_force_kN if check.required_force_kN is not None else 'not defined'} kN; "
                        f"available={check.available_force_kN if check.available_force_kN is not None else 'not verified'} kN"
                    ),
                )
                for check in governing_restraint_checks
            ]
            + [
                CalculationInput(
                    symbol=f"strap,{check.member_id}",
                    label=f"{check.label} ULS tension envelope",
                    value=check.status,
                    source=(
                        f"governing={check.governing_combination_id or 'none'}; "
                        f"demand={check.tension_demand_kN:g} kN; "
                        f"member evidence={check.member_capacity_status}; "
                        f"connection evidence={check.connection_capacity_status}; "
                        f"strap capacity={check.tension_capacity_kN if check.tension_capacity_kN is not None else 'not declared'} kN; "
                        f"end capacity={check.end_connection_capacity_kN if check.end_connection_capacity_kN is not None else 'unverified'} kN; "
                        f"per end fastener={check.required_force_per_end_fastener_kN if check.required_force_per_end_fastener_kN is not None else 'not declared'} kN"
                    ),
                )
                for check in tension_member_checks
            ],
            references=list(
                dict.fromkeys(
                    [
                        *basis_references,
                        *(
                            reference
                            for check in tension_member_checks
                            for reference in (
                                check.standard_reference,
                                (
                                    f"AS/NZS 4600 source SHA-256 "
                                    f"{check.standard_source_sha256}"
                                    if check.standard_source_sha256
                                    else None
                                ),
                            )
                            if reference is not None
                        ),
                        *(
                            reference
                            for check in member_restraint_candidate_checks
                            for reference in check.evidence_references
                        ),
                    ]
                )
            ),
            equations=[
                CalculationEquation(
                    label=f"{check.label} gross-section yielding",
                    expression="phi Nt,gross = 0.90 Ag fy",
                    substitution=(
                        f"Ag={check.gross_area_mm2} mm2"
                    ),
                    result=check.gross_yield_capacity_kN or 0.0,
                    unit="kN",
                )
                for check in tension_member_checks
                if check.gross_yield_capacity_kN is not None
            ]
            + [
                CalculationEquation(
                    label=f"{check.label} net-section fracture",
                    expression="phi Nt,net = 0.90(0.85 kt An fu)",
                    substitution=(
                        f"An={check.net_area_mm2} mm2"
                    ),
                    result=check.net_fracture_capacity_kN or 0.0,
                    unit="kN",
                )
                for check in tension_member_checks
                if check.net_fracture_capacity_kN is not None
            ]
            + [
                CalculationEquation(
                    label=f"{check.label} governing strap utilisation",
                    expression="u = N* / min(phi Nt, phi Nc,end)",
                    substitution=(
                        f"{check.tension_demand_kN:g} / "
                        f"{check.governing_capacity_kN if check.governing_capacity_kN is not None else 'unverified'}"
                    ),
                    result=check.governing_utilisation or 0.0,
                )
                for check in tension_member_checks
                if check.governing_capacity_kN is not None
            ]
            + [
                CalculationEquation(
                    label=f"{check.label} required force per end fastener",
                    expression="F*,fastener = N* / n_end",
                    substitution=(
                        f"{check.tension_demand_kN:g} / {check.end_fastener_count}"
                    ),
                    result=check.required_force_per_end_fastener_kN or 0.0,
                    unit="kN",
                )
                for check in tension_member_checks
                if check.end_fastener_count is not None
            ],
            outputs=[
                CalculationInput(
                    symbol=f"N*,{check.member_id}",
                    label=f"{check.label} governing ULS tension",
                    value=check.tension_demand_kN,
                    unit="kN",
                    source=check.governing_combination_id or "No active ULS tension",
                )
                for check in tension_member_checks
            ],
            related_member_ids=member_ids,
            related_combination_ids=list(
                dict.fromkeys(
                    [
                        combination.id,
                        *(
                            check.governing_combination_id
                            for check in tension_member_checks
                            if check.governing_combination_id is not None
                        ),
                    ]
                )
            ),
        ),
        CalculationSheet(
            id="sheet-au-connections",
            stage_id="connections",
            title="Connections and bases",
            status=connection_status,
            primary_reference=(
                "NCC Housing Provisions 2.2; AS/NZS 4600:2005+A1"
            ),
            supplemental_references=["SCI P399 Section 11"],
            purpose="Verify brackets, fasteners, anchors, concrete, and base behaviour.",
            assumptions=[
                "Rendered screws, bolts, bracket, anchors, and concrete establish identity and geometry, not resistance by themselves.",
                *(
                    [
                        "Finite connection zones terminate the flexible member axes at the outer rendered bolt lines; the remaining centreline arms are deliberately idealised as rigid.",
                        "The finite-zone model is candidate stiffness evidence only. Bracket, bolt, Cee local web/flange, slip, and connection resistance remain unverified.",
                    ]
                    if joint_connections
                    else ["Connection stiffness and resistance are not yet calculated."]
                ),
                *(
                    assumption
                    for check in connection_checks
                    for assumption in check.assumptions
                ),
            ],
            inputs=[
                CalculationInput(
                    symbol=f"joint model {connection.id}",
                    label=connection.label,
                    value=connection.joint_model.analysis_model,
                    source=connection.joint_model.stiffness_basis,
                )
                for connection in joint_connections
                if connection.joint_model is not None
            ]
            + [
                CalculationInput(
                    symbol=f"identity,{check.connection_id}",
                    label=f"{check.label} rendered connector identity",
                    value=check.identity_status,
                    source=(
                        f"expected={check.expected_connector_part_numbers!r}; "
                        f"rendered={check.rendered_connector_part_numbers!r}"
                    ),
                )
                for check in connection_checks
            ],
            equations=[
                CalculationEquation(
                    label=(
                        f"{connection.label} — {engagement.role} flexible-axis "
                        "termination"
                    ),
                    expression="L_rigid = max(s_bolt)",
                    substitution="max("
                    + ", ".join(
                        f"{distance:g}" for distance in engagement.bolt_line_distances_m
                    )
                    + ")",
                    result=engagement.engagement_length_m,
                    unit="m",
                )
                for connection in joint_connections
                if connection.joint_model is not None
                for engagement in connection.joint_model.member_engagements
            ]
            + [
                CalculationEquation(
                    label=f"{check.label} {action} utilisation",
                    expression=f"u_{symbol} = {demand_symbol}*/{capacity_symbol}",
                    substitution=f"{demand:g} / {capacity:g}",
                    result=utilisation,
                )
                for check in connection_checks
                for action, symbol, demand_symbol, capacity_symbol, demand, capacity, utilisation in (
                    (
                        "axial",
                        "N",
                        "N",
                        "phi N_c",
                        check.axial_demand_kN,
                        check.design_axial_capacity_kN,
                        check.axial_utilisation,
                    ),
                    (
                        "shear",
                        "V",
                        "V",
                        "phi V_c",
                        check.shear_demand_kN,
                        check.design_shear_capacity_kN,
                        check.shear_utilisation,
                    ),
                    (
                        "moment",
                        "M",
                        "M",
                        "phi M_c",
                        check.moment_demand_kNm,
                        check.design_moment_capacity_kNm,
                        check.moment_utilisation,
                    ),
                )
                if capacity is not None and utilisation is not None
            ],
            outputs=[
                CalculationInput(
                    symbol=f"Lrigid,{connection.id},{engagement.role}",
                    label=f"{connection.label} — {engagement.role}",
                    value=engagement.engagement_length_m,
                    unit="m",
                    source=(
                        "Outermost bolt line from the rendered component-builder "
                        "joint port"
                    ),
                )
                for connection in joint_connections
                if connection.joint_model is not None
                for engagement in connection.joint_model.member_engagements
            ]
            + [
                CalculationInput(
                    symbol=f"demand,{check.connection_id}",
                    label=f"{check.label} ULS end-action envelope",
                    value=check.status,
                    source=(
                        f"{check.governing_combination_id or 'no ULS'}; "
                        f"N={check.axial_demand_kN:g} kN; "
                        f"V={check.shear_demand_kN:g} kN; "
                        f"M={check.moment_demand_kNm:g} kN.m; "
                        f"resistance pack={check.pack_id} v{check.pack_version} "
                        f"({check.evidence_status})"
                    ),
                )
                for check in connection_checks
            ],
            references=list(
                dict.fromkeys(
                    [
                        *basis_references,
                        *(
                            reference
                            for check in connection_checks
                            for reference in (
                                check.source,
                                (
                                    f"SHA-256 {check.source_sha256}"
                                    if check.source_sha256 is not None
                                    else None
                                ),
                            )
                            if reference is not None
                        ),
                    ]
                )
            ),
            related_node_ids=[
                node.id for node in nodes if any(node.restraints.model_dump().values())
            ],
            related_combination_ids=[combination.id],
        ),
        CalculationSheet(
            id="sheet-au-serviceability",
            stage_id="serviceability",
            title="Serviceability",
            status=serviceability_status,
            primary_reference="AS/NZS 1170.0 serviceability limit state",
            supplemental_references=["SCI P399 Section 12"],
            purpose="Compare elastic SLS movement with explicitly authored project criteria.",
            outputs=[
                CalculationInput(
                    symbol=f"δ/{check.member_id}",
                    label=check.label,
                    value=check.displacement_mm,
                    unit="mm",
                    source=check.basis,
                )
                for check in serviceability_checks
            ],
            references=basis_references,
            related_member_ids=member_ids,
            related_combination_ids=[combination.id],
        ),
        CalculationSheet(
            id="sheet-au-decision",
            stage_id="decision",
            title="Evidence and order decision",
            status="blocked",
            primary_reference=(
                "NCC 2022 Amendment 2, A5G3 evidence of suitability and H1P1"
            ),
            supplemental_references=["SCI P399 complete verification process"],
            purpose="Prevent a green design/order decision while required stages are incomplete.",
            assumptions=[
                "Current result is analysis evidence only and is not suitable for ordering.",
                "Impact, robustness, progressive collapse, and controlled failure are separate studies.",
            ],
            references=basis_references,
            related_member_ids=member_ids,
            related_node_ids=node_ids,
            related_combination_ids=[combination.id],
        ),
    ]
    physical_restraint_candidate_ids = {
        check.candidate_id for check in member_restraint_candidate_checks
    }
    restraint_combination_ids = {
        check.combination_id for check in member_restraint_candidate_checks
    }
    restraint_identity_pass_ids = {
        check.candidate_id
        for check in member_restraint_candidate_checks
        if check.identity_status == "pass"
    }
    restraint_stiffness_verified_ids = {
        check.candidate_id
        for check in member_restraint_candidate_checks
        if check.stiffness_status == "verified"
    }
    restraint_anchored_ids = {
        check.candidate_id
        for check in member_restraint_candidate_checks
        if check.anchorage_status == "verified"
    }
    stages = [
        VerificationStage(
            id="geometry",
            order=1,
            label="Geometry",
            primary_reference="NCC H1P1; Housing Provisions 2.2",
            supplemental_references=["SCI P399 §§3, 6.1"],
            status=basis_status,
            summary=(
                f"{len(members)} members, {len(nodes)} nodes, "
                f"{sum(any(node.restraints.model_dump().values()) for node in nodes)} supports."
            ),
            sheet_ids=["sheet-au-geometry"],
        ),
        VerificationStage(
            id="actions",
            order=2,
            label="Actions",
            primary_reference="AS/NZS 1170.0/.1/.2",
            supplemental_references=["SCI P399 §4"],
            status=actions_status,
            summary=(
                f"{len(capture.wind_action_bases)} site basis/bases, "
                f"{len(wind_surface_loads)} wind surface action(s), "
                f"{len(action_equations)} trace equation(s)."
            ),
            sheet_ids=["sheet-au-actions"],
            blocking_stage_ids=[] if basis_status == "pass" else ["geometry"],
        ),
        VerificationStage(
            id="combinations",
            order=3,
            label="Combinations",
            primary_reference="AS/NZS 1170.0",
            supplemental_references=["SCI P399 §4.7"],
            status=combinations_status,
            summary=f"{combination.id}: {len(combination.factors)} explicit factors.",
            sheet_ids=["sheet-au-combinations"],
            blocking_stage_ids=[]
            if actions_status in {"pass", "warning"}
            else ["actions"],
        ),
        VerificationStage(
            id="analysis",
            order=4,
            label="Analysis",
            primary_reference="AS/NZS 1170.0 analysis requirements",
            supplemental_references=["SCI P399 §5"],
            status=analysis_status,
            summary=f"PyNite elastic solve; equilibrium residual {residual:.3e}.",
            sheet_ids=["sheet-au-analysis"],
            blocking_stage_ids=(
                [] if combinations_status in {"pass", "warning"} else ["combinations"]
            ),
        ),
        VerificationStage(
            id="stability",
            order=5,
            label="Global stability",
            primary_reference="AS/NZS 4600:2005+A1 stability",
            supplemental_references=["SCI P399 §§7.2–7.8"],
            status=stability_status,
            summary=(
                "Imperfection load and P-Delta comparison are missing."
                if stability_result is None
                else (
                    f"{len(stability_result.direction_results)} sway directions; "
                    f"ηM={stability_result.governing_moment_amplification:.3f}, "
                    f"ηδ={stability_result.governing_displacement_amplification:.3f}, "
                    f"αcr,min="
                    f"{stability_result.minimum_alpha_cr:.2f}; "
                    f"{stability_definition.analysis_base_model} bases."
                    if stability_result.minimum_alpha_cr is not None
                    else (
                        f"{stability_result.combination_id}: "
                        f"ηM={stability_result.governing_moment_amplification:.3f}, "
                        f"ηδ="
                        f"{stability_result.governing_displacement_amplification:.3f}; "
                        "NHF αcr evidence incomplete."
                    )
                )
            ),
            sheet_ids=["sheet-au-stability"],
            blocking_stage_ids=[] if analysis_status == "pass" else ["analysis"],
        ),
        VerificationStage(
            id="cross_section",
            order=6,
            label="Cross-section",
            primary_reference="AS/NZS 4600:2005+A1 cross-section resistance",
            supplemental_references=["SCI P399 §8.1"],
            status=cross_section_status,
            summary=(
                "No versioned Australian capacity pack is selected."
                if cross_section_definition is None
                else (
                    f"{len(cross_section_checks)} members checked across "
                    f"{len(cross_section_definition.combination_ids)} ULS "
                    f"combinations; governing utilisation "
                    f"{max((check.governing_utilisation or 0.0) for check in cross_section_checks):.3f}. "
                    "Cross-section only."
                    if cross_section_checks
                    else "Cross-section envelope produced no member results."
                )
            ),
            sheet_ids=["sheet-au-cross-section"],
            blocking_stage_ids=["stability"],
        ),
        VerificationStage(
            id="member_stability",
            order=7,
            label="Member stability",
            primary_reference="AS/NZS 4600:2005+A1 member stability",
            supplemental_references=["SCI P399 §§8.2–8.4"],
            status=member_stability_status,
            summary=(
                "No restraint-defined member-capacity pack is selected."
                if member_stability_definition is None
                else (
                    f"{len(member_stability_checks)} restraint segment(s); "
                    f"{sum(check.status == 'pass' for check in member_stability_checks)} "
                    "pass, "
                    f"{sum(check.status == 'fail' for check in member_stability_checks)} "
                    "fail, "
                    f"{sum(check.status == 'unsupported' for check in member_stability_checks)} "
                    "have unsupported off-axis actions."
                )
            ),
            sheet_ids=["sheet-au-member-stability"],
            blocking_stage_ids=["stability", "cross_section"],
        ),
        VerificationStage(
            id="bracing",
            order=8,
            label="Bracing/restraint",
            primary_reference=(
                "NCC Housing Provisions 2.2; AS/NZS 4600:2005+A1"
            ),
            supplemental_references=["SCI P399 §9"],
            status=bracing_status,
            summary=(
                f"{len(physical_restraint_candidate_ids)} physical restraint location(s) "
                f"evaluated across {len(restraint_combination_ids)} combination(s): "
                f"{len(restraint_identity_pass_ids)} exact product matches, "
                f"{len(restraint_stiffness_verified_ids)} stiffness verified, "
                f"{len(restraint_anchored_ids)} anchored."
                if member_restraint_candidate_checks
                else "No verified restraint or bracing load path is active."
            ),
            sheet_ids=["sheet-au-bracing"],
            blocking_stage_ids=["member_stability"],
        ),
        VerificationStage(
            id="connections",
            order=9,
            label="Connections/bases",
            primary_reference=(
                "NCC Housing Provisions 2.2; AS/NZS 4600:2005+A1"
            ),
            supplemental_references=["SCI P399 §11"],
            status=connection_status,
            summary=(
                f"{len(connection_checks)} physical connection demand/resistance "
                f"check(s): {sum(check.status == 'pass' for check in connection_checks)} "
                f"pass, {sum(check.status == 'fail' for check in connection_checks)} "
                f"fail, {sum(check.status == 'unsupported' for check in connection_checks)} "
                "without verified resistance."
                if connection_checks
                else "Rendered detail exists; no resistance evidence pack is connected."
            ),
            sheet_ids=["sheet-au-connections"],
            blocking_stage_ids=["analysis"],
        ),
        VerificationStage(
            id="serviceability",
            order=10,
            label="Serviceability",
            primary_reference="AS/NZS 1170.0 serviceability",
            supplemental_references=["SCI P399 §12"],
            status=serviceability_status,
            summary=(
                f"{len(checked_serviceability)} authored SLS criteria evaluated."
                if checked_serviceability
                else "Select an SLS combination with an authored deflection criterion."
            ),
            sheet_ids=["sheet-au-serviceability"],
            blocking_stage_ids=[] if analysis_status == "pass" else ["analysis"],
        ),
        VerificationStage(
            id="decision",
            order=11,
            label="Evidence/decision",
            primary_reference="NCC A5G3 and H1P1",
            supplemental_references=["SCI P399 complete process"],
            status="blocked",
            summary="NOT READY TO CERTIFY OR ORDER: Australian verification gates remain incomplete.",
            sheet_ids=["sheet-au-decision"],
            blocking_stage_ids=[
                "stability",
                "cross_section",
                "member_stability",
                "bracing",
                "connections",
            ],
        ),
    ]
    return stages, sheets


def _australian_certification_readiness(
    *,
    capture: ProjectStructuralCapture,
    analysis,
    stages: list[VerificationStage],
    sheets: list[CalculationSheet],
    equilibrium_status: Literal["pass", "fail"],
    tension_member_checks: list[TensionMemberCheck],
    member_results: list[MemberResult],
    cross_section_checks: list[MemberCrossSectionCheck],
    member_stability_checks: list[MemberStabilityCheck],
    member_restraint_candidate_checks: list[MemberRestraintCandidateCheck],
    connection_checks: list[ConnectionCheck],
    bracing_load_path_traces: list[BracingLoadPathTrace],
) -> tuple[CertificationReadiness, list[VerificationStage], list[CalculationSheet]]:
    """Convert detailed calculations into conservative Australian release gates."""

    stages_by_id = {stage.id: stage for stage in stages}

    def combined_status(stage_ids: list[str]):
        statuses = [stages_by_id[stage_id].status for stage_id in stage_ids]
        if all(status == "pass" for status in statuses):
            return "pass"
        if "fail" in statuses:
            return "fail"
        if "warning" in statuses and all(
            status in {"pass", "warning"} for status in statuses
        ):
            return "warning"
        return "blocked"

    basis = capture.design_basis
    basis_missing: list[str] = []
    if basis is None or basis.framework_id != "AU-NCC-2022":
        basis_missing.append("NCC 2022 Amendment 2 Australian primary framework")
    if basis is None or not basis.building_classification:
        basis_missing.append("NCC building classification")
    if basis is None or not basis.importance_level:
        basis_missing.append("importance level")
    if basis is None or basis.design_life_years is None:
        basis_missing.append("design life")
    required_standard_roles = {
        "action_combinations",
        "permanent_and_imposed_actions",
        "wind_actions",
        "members",
    }
    if basis is None or not required_standard_roles.issubset(basis.standards):
        basis_missing.append("complete Australian standard register")
    if basis is None or any(
        "unconfirmed" in reference.lower() for reference in basis.standards.values()
    ):
        basis_missing.append("confirmed project standard editions")
    if capture.wind_action_bases and any(
        wind.region_status != "verified" or wind.table_status != "verified"
        for wind in capture.wind_action_bases
    ):
        basis_missing.append("verified wind region and standard-table evidence")

    basis_gate = CertificationGate(
        id="project_basis",
        order=1,
        label="Project and NCC basis",
        status="pass" if not basis_missing else "blocked",
        primary_reference=(
            "NCC 2022 Amendment 2, A6, H1 and Housing Provisions 2.2"
        ),
        summary=(
            "NCC classification, importance, design life and project standards are recorded."
            if not basis_missing
            else "Missing or unverified: " + ", ".join(basis_missing) + "."
        ),
        stage_ids=["geometry"],
    )

    action_pack = analysis.action_standard_pack
    actions_calculated = combined_status(["actions", "combinations"]) == "pass"
    action_evidence_ready = bool(
        actions_calculated
        and action_pack is not None
        and action_pack.status != "working"
    )
    action_gate = CertificationGate(
        id="actions",
        order=2,
        label="Actions and combinations",
        status="pass" if action_evidence_ready else "blocked",
        primary_reference="AS/NZS 1170.0, AS/NZS 1170.1 and AS/NZS 1170.2",
        summary=(
            "Applicable actions and combinations use certification evidence."
            if action_evidence_ready
            else (
                "Actions are calculated for engineering review, but the selected "
                "action pack is working evidence and cannot support certification."
                if actions_calculated
                else "Required action calculations or combinations are incomplete."
            )
        ),
        stage_ids=["actions", "combinations"],
    )

    analysis_stage_status = combined_status(["geometry", "analysis"])
    analysis_gate = CertificationGate(
        id="analysis",
        order=3,
        label="Structural analysis",
        status=(
            "pass"
            if analysis_stage_status == "pass" and equilibrium_status == "pass"
            else "fail"
            if equilibrium_status == "fail"
            else "blocked"
        ),
        primary_reference="AS/NZS 1170.0 analysis and limit-state requirements",
        summary=(
            "The compiled mechanical model solves and satisfies global equilibrium."
            if analysis_stage_status == "pass" and equilibrium_status == "pass"
            else "The analytical model or its equilibrium check is incomplete."
        ),
        stage_ids=["geometry", "analysis"],
    )

    stability_gate = CertificationGate(
        id="stability",
        order=4,
        label="System stability",
        status=stages_by_id["stability"].status,
        primary_reference="AS/NZS 4600:2005+A1 stability requirements",
        summary=stages_by_id["stability"].summary,
        stage_ids=["stability"],
    )
    capacity_gate = CertificationGate(
        id="member_capacity",
        order=5,
        label="Member resistance and stability",
        status=combined_status(["cross_section", "member_stability"]),
        primary_reference="AS/NZS 4600:2005+A1 cold-formed steel design",
        summary=(
            f"Cross-section: {stages_by_id['cross_section'].status}; member stability: "
            f"{stages_by_id['member_stability'].status}."
        ),
        stage_ids=["cross_section", "member_stability"],
    )
    tension_ready = all(check.status == "pass" for check in tension_member_checks)
    load_path_status = combined_status(["bracing", "connections"])
    load_path_gate = CertificationGate(
        id="load_path",
        order=6,
        label="Bracing, connections and foundations",
        status=(
            "pass"
            if load_path_status == "pass" and tension_ready
            else "fail"
            if load_path_status == "fail"
            else "blocked"
        ),
        primary_reference=(
            "NCC Housing Provisions 2.2; AS/NZS 4600:2005+A1"
        ),
        summary=(
            "Bracing and connection resistance form a verified load path to ground."
            if load_path_status == "pass" and tension_ready
            else (
                f"Bracing: {stages_by_id['bracing'].status}; connections: "
                f"{stages_by_id['connections'].status}; verified tension components: "
                f"{sum(check.status == 'pass' for check in tension_member_checks)}/"
                f"{len(tension_member_checks)}."
            )
        ),
        stage_ids=["bracing", "connections"],
    )
    serviceability_gate = CertificationGate(
        id="serviceability",
        order=7,
        label="Serviceability",
        status=stages_by_id["serviceability"].status,
        primary_reference="AS/NZS 1170.0 serviceability limit state",
        summary=stages_by_id["serviceability"].summary,
        stage_ids=["serviceability"],
    )

    technical_gates = [
        basis_gate,
        action_gate,
        analysis_gate,
        stability_gate,
        capacity_gate,
        load_path_gate,
        serviceability_gate,
    ]
    technical_ready = all(gate.status == "pass" for gate in technical_gates)
    documentation_gate = CertificationGate(
        id="documentation",
        order=8,
        label="Evidence and engineering decision",
        status="pass" if technical_ready else "blocked",
        primary_reference=(
            "NCC 2022 Amendment 2, A5G3 evidence of suitability"
        ),
        summary=(
            "The technical gates support preparation of a certificate for engineer sign-off."
            if technical_ready
            else "A positive certificate cannot be prepared while technical gates remain open."
        ),
        stage_ids=["decision"],
    )
    gates = [*technical_gates, documentation_gate]
    blocking_gates = [gate for gate in gates if gate.status != "pass"]
    ready_for_engineering_review = analysis_gate.status == "pass"
    ready_for_certificate = not blocking_gates
    compiled_member_ids = {member.id for member in analysis.members}
    solved_member_ids = {result.member_id for result in member_results}
    missing_result_member_ids = sorted(compiled_member_ids - solved_member_ids)
    model_coverage = CertificationModelCoverage(
        status="incomplete" if missing_result_member_ids else "complete",
        compiled_member_count=len(compiled_member_ids),
        solved_member_count=len(compiled_member_ids & solved_member_ids),
        missing_result_member_ids=missing_result_member_ids,
        summary=(
            f"PyNite results cover all {len(compiled_member_ids)} compiled analytical "
            "members; the open certification gates are not missing member definitions."
            if not missing_result_member_ids
            else (
                f"PyNite results are missing for {len(missing_result_member_ids)} of "
                f"{len(compiled_member_ids)} compiled analytical members."
            )
        ),
    )

    def affected(values: Sequence[str]) -> list[str]:
        return sorted(set(values))[:12]

    issues: list[CertificationIssue] = []
    provisional_wind_loads = [
        load
        for load in capture.loads
        if load.case == "wind"
        and load.coefficient_status in {"assumed", "working_conservative"}
    ]
    if provisional_wind_loads:
        issues.append(
            CertificationIssue(
                id="provisional-wind-coefficients",
                stage_id="actions",
                kind="provisional_input",
                owner="tertius",
                count=len(provisional_wind_loads),
                title="Wind surface coefficients remain provisional",
                detail=(
                    "The Site wind speed and directional cases are calculated, but "
                    "the AS/NZS 1170.2 surface/opening coefficient envelope is still "
                    "the working conservative model."
                ),
                next_action=(
                    "Complete the Tertius-owned AS/NZS 1170.2 surface-zone and "
                    "internal-pressure action pack; do not add formulas to design.py."
                ),
                affected_ids=affected([load.id for load in provisional_wind_loads]),
            )
        )

    failed_cross_sections = [
        check for check in cross_section_checks if check.status == "fail"
    ]
    if failed_cross_sections:
        issues.append(
            CertificationIssue(
                id="cross-section-design-failures",
                stage_id="cross_section",
                kind="design_failure",
                owner="design",
                count=len(failed_cross_sections),
                title="Member cross-sections exceed calculated resistance",
                detail=(
                    "These are numerical demand/capacity failures in the current shed, "
                    "not missing PyNite definitions."
                ),
                next_action=(
                    "Revise the affected member size, span, support, or load path and "
                    "rerun the unchanged Tertius capacity pack."
                ),
                affected_ids=affected(
                    [check.member_id for check in failed_cross_sections]
                ),
            )
        )
    unsupported_cross_sections = [
        check for check in cross_section_checks if check.status == "unsupported"
    ]
    if unsupported_cross_sections:
        issues.append(
            CertificationIssue(
                id="cross-section-evidence-gaps",
                stage_id="cross_section",
                kind="evidence_gap",
                owner="evidence",
                count=len(unsupported_cross_sections),
                title="Cross-section resistance evidence is incomplete",
                detail="The solver has members, but the capacity pack cannot verify their section records.",
                next_action="Add traceable section properties or a supported resistance pack in Tertius.",
                affected_ids=affected(
                    [check.member_id for check in unsupported_cross_sections]
                ),
            )
        )

    failed_stability = [
        check for check in member_stability_checks if check.status == "fail"
    ]
    if failed_stability:
        issues.append(
            CertificationIssue(
                id="member-stability-design-failures",
                stage_id="member_stability",
                kind="design_failure",
                owner="mixed",
                count=len(failed_stability),
                title="Members fail the current unrestrained stability model",
                detail=(
                    "The conservative full-segment calculation exceeds resistance. "
                    "Verified restraint may reduce the unbraced demand; otherwise the "
                    "member design must change."
                ),
                next_action=(
                    "Finish restraint verification first, then resize or support only "
                    "the members that still fail."
                ),
                affected_ids=affected([check.member_id for check in failed_stability]),
            )
        )
    unsupported_stability = [
        check for check in member_stability_checks if check.status == "unsupported"
    ]
    if unsupported_stability:
        issues.append(
            CertificationIssue(
                id="member-stability-evidence-gaps",
                stage_id="member_stability",
                kind="evidence_gap",
                owner="evidence",
                count=len(unsupported_stability),
                title="Member stability waits on restraint evidence",
                detail=(
                    "Demand and unrestrained resistance are calculated below unity, "
                    "but effective lateral/torsional restraint at the segment boundaries "
                    "has not been verified."
                ),
                next_action=(
                    "Attach tested restraint force, stiffness, connection, and anchorage "
                    "evidence to the rendered cladding/brace configurations."
                ),
                affected_ids=affected(
                    [check.member_id for check in unsupported_stability]
                ),
            )
        )

    open_restraint_candidates = {
        check.candidate_id
        for check in member_restraint_candidate_checks
        if check.status in {"unsupported", "candidate", "fail"}
    }
    if open_restraint_candidates:
        issues.append(
            CertificationIssue(
                id="restraint-candidate-evidence-gaps",
                stage_id="bracing",
                kind="evidence_gap",
                owner="evidence",
                count=len(open_restraint_candidates),
                title="Rendered restraint candidates lack qualifying evidence",
                detail=(
                    "Tertius found the physical contacts, but geometry alone cannot "
                    "prove restraint force, stiffness, twist control, or anchorage."
                ),
                next_action=(
                    "Add product/test evidence packs for the exact cladding, fastener, "
                    "brace, and connection identities already present in the design."
                ),
                affected_ids=affected(list(open_restraint_candidates)),
            )
        )

    failed_connections = [
        check for check in connection_checks if check.status == "fail"
    ]
    if failed_connections:
        issues.append(
            CertificationIssue(
                id="connection-design-failures",
                stage_id="connections",
                kind="design_failure",
                owner="mixed",
                count=len(failed_connections),
                title="Connections exceed currently verified resistance",
                detail=(
                    "These checks have demand and resistance values; some may need a "
                    "connection change while others need verified group behaviour."
                ),
                next_action=(
                    "Review each governing interaction and either revise the connection "
                    "or attach evidence that justifies additional resistance."
                ),
                affected_ids=affected(
                    [check.connection_id for check in failed_connections]
                ),
            )
        )
    unsupported_connections = [
        check for check in connection_checks if check.status == "unsupported"
    ]
    if unsupported_connections:
        issues.append(
            CertificationIssue(
                id="connection-evidence-gaps",
                stage_id="connections",
                kind="evidence_gap",
                owner="evidence",
                count=len(unsupported_connections),
                title="Connection resistance packs are missing",
                detail=(
                    "The connections and their PyNite forces exist, but Tertius has no "
                    "verified resistance model for the rendered connector identities."
                ),
                next_action=(
                    "Implement or attach resistance packs for the exact angle, cleat, "
                    "knee, apex, screw, bolt, and fixture combinations."
                ),
                affected_ids=affected(
                    [check.connection_id for check in unsupported_connections]
                ),
            )
        )

    failed_bracing_paths = [
        trace
        for trace in bracing_load_path_traces
        if trace.status in {"fail", "blocked"}
    ]
    if failed_bracing_paths:
        issues.append(
            CertificationIssue(
                id="dependent-bracing-path-blockers",
                stage_id="bracing",
                kind="dependent_blocker",
                owner="mixed",
                count=len(failed_bracing_paths),
                title="Bracing paths inherit downstream connection blockers",
                detail=(
                    "Brace members are present and analysed; their path cannot pass "
                    "until every connection and foundation link on the route passes."
                ),
                next_action=(
                    "Resolve the listed connection/foundation checks before changing "
                    "brace geometry solely to clear this dependent status."
                ),
                affected_ids=affected(
                    [trace.component_id for trace in failed_bracing_paths]
                ),
            )
        )

    if stages_by_id["stability"].status == "warning":
        issues.append(
            CertificationIssue(
                id="system-stability-warning",
                stage_id="stability",
                kind="engineering_warning",
                owner="mixed",
                count=1,
                title="Global stability still requires engineering review",
                detail=stages_by_id["stability"].summary,
                next_action=(
                    "Review the reported amplification and assumptions after the "
                    "action and restraint evidence is final."
                ),
            )
        )
    document_status: Literal[
        "analysis_incomplete", "engineering_review_draft", "certificate_ready"
    ] = (
        "certificate_ready"
        if ready_for_certificate
        else "engineering_review_draft"
        if ready_for_engineering_review
        else "analysis_incomplete"
    )
    readiness = CertificationReadiness(
        document_status=document_status,
        draft_document_label=(
            "DRAFT STRUCTURAL CERTIFICATE — ENGINEER REVIEW AND SIGNATURE REQUIRED"
            if ready_for_certificate
            else "DRAFT ENGINEERING REVIEW REPORT — NOT A STRUCTURAL CERTIFICATE"
        ),
        ready_for_engineering_review=ready_for_engineering_review,
        ready_for_certificate=ready_for_certificate,
        ready_for_order=ready_for_certificate,
        conclusion=(
            "Australian technical gates pass; prepare the controlled certificate draft for engineer review."
            if ready_for_certificate
            else "Analysis evidence is available for engineering review, but certification and ordering remain blocked."
            if ready_for_engineering_review
            else "The structural analysis is incomplete and no engineering review document should be issued."
        ),
        blocking_gate_ids=[gate.id for gate in blocking_gates],
        blocking_reasons=[f"{gate.label}: {gate.summary}" for gate in blocking_gates],
        gates=gates,
        model_coverage=model_coverage,
        issues=issues,
    )

    decision_status = "pass" if ready_for_certificate else "blocked"
    decision_summary = (
        "READY FOR CONTROLLED CERTIFICATE DRAFT AND ENGINEER SIGN-OFF."
        if ready_for_certificate
        else "NOT READY TO CERTIFY OR ORDER: Australian verification gates remain incomplete."
    )
    stages = [
        stage.model_copy(
            update={"status": decision_status, "summary": decision_summary}
        )
        if stage.id == "decision"
        else stage
        for stage in stages
    ]
    sheets = [
        sheet.model_copy(
            update={
                "status": decision_status,
                "assumptions": [
                    readiness.draft_document_label,
                    *readiness.blocking_reasons,
                ],
            }
        )
        if sheet.stage_id == "decision"
        else sheet
        for sheet in sheets
    ]
    return readiness, stages, sheets


def _generate_p399_nodal_loads(
    model,
    *,
    analysis,
    member_node_ids: dict[str, tuple[str, str]],
    nodes_by_topology: dict[tuple[object, ...], dict[str, Any]],
) -> list[NodalLoad]:
    """Solve vertical reactions, then add Tertius-generated P399 EHF/NHF."""

    stability = analysis.stability
    if stability is None or not stability.column_component_ids:
        return []
    generated_directions = [
        direction
        for direction in stability.direction_cases
        if direction.base_combination_id is not None
    ]
    if not generated_directions:
        return []

    model.analyze_linear(check_statics=False, log=False)
    topology_nodes = {str(node["id"]): node for node in nodes_by_topology.values()}
    members_by_component: dict[str, list[Any]] = {}
    for declaration in analysis.members:
        members_by_component.setdefault(declaration.component_id, []).append(
            declaration
        )
    combinations_by_id = {
        combination.id: combination for combination in analysis.load_combinations
    }
    generated_loads: list[NodalLoad] = []
    for direction in generated_directions:
        base_combination_id = direction.base_combination_id
        assert base_combination_id is not None
        nhf_combination = combinations_by_id[direction.nhf_combination_id]
        nhf_case_ids = list(nhf_combination.factors)
        if len(nhf_case_ids) != 1:
            raise StructuralAnalysisError(
                f"P399 NHF combination {direction.nhf_combination_id!r} must "
                "contain exactly one generated action case"
            )
        nhf_case_id = nhf_case_ids[0]
        pynite_direction = "FX" if direction.horizontal_axis == "x" else "FY"
        for component_id in stability.column_component_ids:
            component_members = members_by_component.get(component_id, [])
            if not component_members:
                raise StructuralAnalysisError(
                    f"P399 column component {component_id!r} has no analytical member"
                )
            endpoints: list[tuple[float, str]] = []
            for declaration in component_members:
                start_node_id, end_node_id = member_node_ids[declaration.id]
                endpoints.extend(
                    (
                        (declaration.start.z, start_node_id),
                        (declaration.end.z, end_node_id),
                    )
                )
            base_node_id = min(endpoints, key=lambda item: item[0])[1]
            eaves_node_id = max(endpoints, key=lambda item: item[0])[1]
            base_node = model.nodes[base_node_id]
            vertical_reaction_kN = max(
                0.0,
                float(base_node.RxnFZ[base_combination_id]),
            )
            horizontal_force_kN = (
                vertical_reaction_kN / 200.0 * direction.direction_sign
            )
            if abs(horizontal_force_kN) <= 1e-12:
                continue
            eaves_node = topology_nodes[eaves_node_id]
            for case_id, action_label in (
                (direction.imperfection_case_id, "equivalent horizontal force"),
                (nhf_case_id, "notional horizontal force"),
            ):
                model.add_node_load(
                    eaves_node_id,
                    pynite_direction,
                    horizontal_force_kN,
                    case=case_id,
                )
                force = Vector3(
                    x=(horizontal_force_kN if direction.horizontal_axis == "x" else 0),
                    y=(horizontal_force_kN if direction.horizontal_axis == "y" else 0),
                    z=0,
                )
                generated_loads.append(
                    NodalLoad(
                        id=(f"p399:{case_id}:{component_id}:{eaves_node_id}"),
                        label=(f"P399 {action_label} at {component_id} column top"),
                        node_id=eaves_node_id,
                        case_id=case_id,
                        force=force,
                        visual_node_id=str(eaves_node["visual_node_id"]),
                        provenance=(
                            f"SCI P399 working method: 1/200 of the solved vertical "
                            f"base reaction ({vertical_reaction_kN:.6g} kN) under "
                            f"{base_combination_id}."
                        ),
                    )
                )
    return generated_loads


def solve_project_structural(
    capture: ProjectStructuralCapture,
    *,
    combination_id: str | None = None,
) -> StructuralSnapshot:
    analysis = capture.analysis
    if analysis is None or not analysis.members:
        raise StructuralAnalysisError("Active design has no analytical member axes")
    if not analysis.member_loads and not analysis.member_distributed_loads:
        raise StructuralAnalysisError("Active design has no analytical member loads")

    from Pynite import FEModel3D
    from Pynite.PhysMember import PhysMember

    class ExplicitTopologyPhysMember(PhysMember):
        """Prevent PyNite from inferring joints from unrelated spatial nodes."""

        def descritize(self) -> None:
            all_nodes = self.model.nodes
            self.model.nodes = {
                self.i_node.name: self.i_node,
                self.j_node.name: self.j_node,
            }
            try:
                super().descritize()
            finally:
                self.model.nodes = all_nodes

    combinations = _combination_list(analysis)
    active_combination = _select_combination(combinations, combination_id)
    if analysis.cross_section_verification is not None:
        combinations_by_id = {
            combination.id: combination for combination in combinations
        }
        for (
            envelope_combination_id
        ) in analysis.cross_section_verification.combination_ids:
            envelope_combination = combinations_by_id.get(envelope_combination_id)
            if envelope_combination is None:
                raise StructuralAnalysisError(
                    "Cross-section verification references missing combination "
                    f"{envelope_combination_id!r}"
                )
            if envelope_combination.limit_state != "ultimate":
                raise StructuralAnalysisError(
                    "Cross-section verification requires ULS combinations; "
                    f"{envelope_combination_id!r} is "
                    f"{envelope_combination.limit_state!r}"
                )
    combination_selection: Literal[
        "requested", "default", "governing_working_envelope"
    ] = "requested" if combination_id is not None else "default"
    components = {component.id: component for component in capture.components}

    model = FEModel3D()
    for material in analysis.materials:
        model.add_material(
            material.id,
            E=material.elastic_modulus_kN_m2,
            G=material.shear_modulus_kN_m2,
            nu=material.poisson_ratio,
            rho=material.density_kg_m3,
        )
    for section in analysis.sections:
        model.add_section(
            section.id,
            A=section.area_m2,
            Iy=section.iy_m4,
            Iz=section.iz_m4,
            J=section.torsion_j_m4,
        )

    nodes_by_topology: dict[tuple[object, ...], dict[str, Any]] = {}
    member_node_ids: dict[str, tuple[str, str]] = {}
    for declaration in analysis.members:
        component = components[declaration.component_id]
        member_nodes: list[str] = []
        for endpoint, position, node_key, restraints in (
            (
                "start",
                declaration.start,
                declaration.start_node_key,
                declaration.start_restraints,
            ),
            (
                "end",
                declaration.end,
                declaration.end_node_key,
                declaration.end_restraints,
            ),
        ):
            coordinate = _coordinate_key(position)
            key: tuple[object, ...] = (
                ("explicit", node_key)
                if node_key is not None
                else ("coordinate", *coordinate)
            )
            node = nodes_by_topology.get(key)
            if node is None:
                node = {
                    "id": f"node-{len(nodes_by_topology) + 1}",
                    "position": position,
                    "restraints": {
                        "dx": False,
                        "dy": False,
                        "dz": False,
                        "rx": False,
                        "ry": False,
                        "rz": False,
                    },
                    "visual_node_id": component.visual_node_id,
                    "labels": [],
                }
                nodes_by_topology[key] = node
            elif _coordinate_key(node["position"]) != coordinate:
                raise StructuralAnalysisError(
                    f"analytical node key {node_key!r} joins different coordinates; "
                    f"check the physical connection at {declaration.id}.{endpoint}"
                )
            _merge_restraints(node["restraints"], restraints)
            node["labels"].append(declaration.label)
            member_nodes.append(node["id"])
        member_node_ids[declaration.id] = (member_nodes[0], member_nodes[1])

    # A released degree of freedom at a node touched by only one analytical
    # member has no stiffness contribution at all.  PyNite correctly identifies
    # that bookkeeping DOF as singular even though it cannot transfer force or
    # moment into the structure.  Restrain the orphaned rotational bookkeeping
    # DOFs while retaining the authored member-end release.
    endpoint_releases_by_node: dict[
        str, list[tuple[str, Restraints]]
    ] = defaultdict(list)
    for declaration in analysis.members:
        start_node_id, end_node_id = member_node_ids[declaration.id]
        endpoint_releases_by_node[start_node_id].append(
            (declaration.id, declaration.start_releases)
        )
        endpoint_releases_by_node[end_node_id].append(
            (declaration.id, declaration.end_releases)
        )
    nodes_by_id = {node["id"]: node for node in nodes_by_topology.values()}
    for node_id, endpoint_details in endpoint_releases_by_node.items():
        if len(endpoint_details) != 1:
            continue
        _member_id, release = endpoint_details[0]
        node_restraints = nodes_by_id[node_id]["restraints"]
        for axis in ("dx", "dy", "dz"):
            if getattr(release, axis):
                node_restraints[axis] = True
        if any(getattr(release, axis) for axis in ("rx", "ry", "rz")):
            node_restraints.update({"rx": True, "ry": True, "rz": True})

    # The secondary window frame and floor ledgers are supported subassemblies,
    # not free 3D mechanisms.  Their compile projection supplies translational
    # ground restraints but an idealized pin model omits the out-of-plane/torsion
    # restraint supplied by the installed brackets and sheeting.  Add only the
    # missing secondary-axis support at already-grounded nodes.  Primary portal
    # bases and the knee/apex joint model are intentionally untouched.
    for declaration in analysis.members:
        component_role = (components[declaration.component_id].role or "").lower()
        start_node_id, end_node_id = member_node_ids[declaration.id]
        for node_id, endpoint_restraints in (
            (start_node_id, declaration.start_restraints),
            (end_node_id, declaration.end_restraints),
        ):
            if not (
                endpoint_restraints.dx
                and endpoint_restraints.dy
                and endpoint_restraints.dz
            ):
                continue
            node_restraints = nodes_by_id[node_id]["restraints"]
            if component_role.startswith("window "):
                node_restraints["rx"] = True
            elif component_role == "floor ledger":
                node_restraints["ry"] = True

    for node in nodes_by_topology.values():
        position = node["position"]
        model.add_node(node["id"], position.x, position.y, position.z)
        restraints = node["restraints"]
        model.def_support(
            node["id"],
            support_DX=restraints["dx"],
            support_DY=restraints["dy"],
            support_DZ=restraints["dz"],
            support_RX=restraints["rx"],
            support_RY=restraints["ry"],
            support_RZ=restraints["rz"],
        )

    for declaration in analysis.members:
        start_node_id, end_node_id = member_node_ids[declaration.id]
        model.members[declaration.id] = ExplicitTopologyPhysMember(
            model,
            declaration.id,
            model.nodes[start_node_id],
            model.nodes[end_node_id],
            declaration.material_id,
            declaration.section_id,
            rotation=declaration.rotation_deg,
            tension_only=declaration.tension_only,
            comp_only=declaration.compression_only,
        )
        model.solution = None
        start_releases = declaration.start_releases
        end_releases = declaration.end_releases
        if any(
            getattr(releases, axis)
            for releases in (start_releases, end_releases)
            for axis in ("dx", "dy", "dz", "rx", "ry", "rz")
        ):
            model.def_releases(
                declaration.id,
                Dxi=start_releases.dx,
                Dyi=start_releases.dy,
                Dzi=start_releases.dz,
                Rxi=start_releases.rx,
                Ryi=start_releases.ry,
                Rzi=start_releases.rz,
                Dxj=end_releases.dx,
                Dyj=end_releases.dy,
                Dzj=end_releases.dz,
                Rxj=end_releases.rx,
                Ryj=end_releases.ry,
                Rzj=end_releases.rz,
            )

    # A pin shared by multiple members can leave one global node rotation with
    # exactly zero stiffness when all incident local member axes release that
    # direction.  PyNite reports the unused bookkeeping DOF as an unstable
    # mechanism.  Restrain only directions proven to receive no member-end
    # stiffness; the authored releases remain in place, so this cannot create
    # moment transfer or turn the physical pin into a fixed joint.
    supports_changed = False
    for node_id, endpoint_details in endpoint_releases_by_node.items():
        incident_endpoints = []
        for member_id, releases in endpoint_details:
            rotation = model.members[member_id].T()[:3, :3]
            incident_endpoints.append(
                (
                    tuple(
                        tuple(float(rotation[row, column]) for column in range(3))
                        for row in range(3)
                    ),
                    releases,
                )
            )
        node_restraints = nodes_by_id[node_id]["restraints"]
        for axis in _released_node_rotational_axes(incident_endpoints):
            if not node_restraints[axis]:
                node_restraints[axis] = True
                supports_changed = True
    rotational_member_details = []
    for declaration in analysis.members:
        start_node_id, end_node_id = member_node_ids[declaration.id]
        rotation = model.members[declaration.id].T()[:3, :3]
        rotational_member_details.append(
            (
                start_node_id,
                end_node_id,
                tuple(
                    tuple(float(rotation[row, column]) for column in range(3))
                    for row in range(3)
                ),
                declaration.start_releases,
                declaration.end_releases,
            )
        )
    for node_id, axis in _released_rotational_datum_restraints(
        rotational_member_details,
        {
            node_id: node["restraints"]
            for node_id, node in nodes_by_id.items()
        },
    ):
        node_restraints = nodes_by_id[node_id]["restraints"]
        if not node_restraints[axis]:
            node_restraints[axis] = True
            supports_changed = True
    if supports_changed:
        for node in nodes_by_topology.values():
            restraints = node["restraints"]
            model.def_support(
                node["id"],
                support_DX=restraints["dx"],
                support_DY=restraints["dy"],
                support_DZ=restraints["dz"],
                support_RX=restraints["rx"],
                support_RY=restraints["ry"],
                support_RZ=restraints["rz"],
            )

    direction_names = (
        ("FX", "x"),
        ("FY", "y"),
        ("FZ", "z"),
        ("MX", "x"),
        ("MY", "y"),
        ("MZ", "z"),
    )
    for point_load in analysis.member_loads:
        for direction, axis_name in direction_names[:3]:
            magnitude = getattr(point_load.force, axis_name)
            if magnitude:
                model.add_member_pt_load(
                    point_load.member_id,
                    direction,
                    magnitude,
                    point_load.distance_m,
                    case=point_load.case_id,
                )
        for direction, axis_name in direction_names[3:]:
            magnitude = getattr(point_load.moment, axis_name)
            if magnitude:
                model.add_member_pt_load(
                    point_load.member_id,
                    direction,
                    magnitude,
                    point_load.distance_m,
                    case=point_load.case_id,
                )

    for line_load in analysis.member_distributed_loads:
        for direction, axis_name in direction_names[:3]:
            start_magnitude = getattr(line_load.start_force_kN_m, axis_name)
            end_magnitude = getattr(line_load.end_force_kN_m, axis_name)
            if start_magnitude or end_magnitude:
                model.add_member_dist_load(
                    line_load.member_id,
                    direction,
                    start_magnitude,
                    end_magnitude,
                    x1=line_load.start_distance_m,
                    x2=line_load.end_distance_m,
                    case=line_load.case_id,
                )

    for combination in combinations:
        model.add_load_combo(combination.id, dict(combination.factors))
    stability_directions: list[dict[str, Any]] = []
    if analysis.stability is not None:
        stability_directions = [
            direction.model_dump() for direction in analysis.stability.direction_cases
        ] or [
            {
                "id": "authored",
                "stability_combination_id": (
                    analysis.stability.stability_combination_id
                ),
                "imperfection_case_id": analysis.stability.imperfection_case_id,
                "nhf_combination_id": "",
                "horizontal_axis": "x",
            }
        ]
    first_order_stability: dict[tuple[str, str], tuple[float, float]] = {}
    first_order_nhf_eaves_displacement_mm: dict[str, float] = {}
    first_order_rafter_axial_kN: dict[str, float] = {}
    stability_result: StabilityResult | None = None
    generated_nodal_loads: list[NodalLoad] = []
    try:
        if analysis.stability is None:
            model.analyze(check_statics=False, log=False)
        else:
            generated_nodal_loads = _generate_p399_nodal_loads(
                model,
                analysis=analysis,
                member_node_ids=member_node_ids,
                nodes_by_topology=nodes_by_topology,
            )
            model.analyze_linear(check_statics=False, log=False)
            members_by_id = {
                declaration.id: declaration for declaration in analysis.members
            }
            for stability_direction in stability_directions:
                stability_combination_id = stability_direction[
                    "stability_combination_id"
                ]
                for declaration in analysis.members:
                    first_order_stability[
                        (stability_direction["id"], declaration.id)
                    ] = _member_extrema(
                        model,
                        declaration,
                        combination_id=stability_combination_id,
                        point_loads=[
                            load
                            for load in analysis.member_loads
                            if load.member_id == declaration.id
                        ],
                        distributed_loads=[
                            load
                            for load in analysis.member_distributed_loads
                            if load.member_id == declaration.id
                        ],
                    )
                first_order_rafter_axial_kN[stability_direction["id"]] = max(
                    (
                        _member_max_axial(
                            model,
                            members_by_id[member_id],
                            combination_id=stability_combination_id,
                        )
                        for member_id in analysis.stability.rafter_member_ids
                    ),
                    default=0.0,
                )
                nhf_combination_id = stability_direction["nhf_combination_id"]
                if nhf_combination_id and analysis.stability.eaves_member_ids:
                    axis_name = stability_direction["horizontal_axis"]
                    first_order_nhf_eaves_displacement_mm[stability_direction["id"]] = (
                        max(
                            abs(
                                getattr(
                                    _member_global_displacement(
                                        model,
                                        member_id=member_id,
                                        distance_m=_length(
                                            members_by_id[member_id].start,
                                            members_by_id[member_id].end,
                                        ),
                                        combination_id=nhf_combination_id,
                                    ),
                                    axis_name,
                                )
                            )
                            * 1000.0
                            for member_id in analysis.stability.eaves_member_ids
                        )
                    )
            model.analyze_PDelta(log=False)
    except Exception as exc:
        raise StructuralAnalysisError(
            f"PyNite could not solve the active design: {exc}"
        ) from exc

    if combination_id is None and any(
        basis.coefficient_selection_policy == "worst_available_credible"
        for basis in capture.wind_action_bases
    ):
        active_combination = _governing_working_combination(
            model,
            analysis,
            combinations,
        )
        combination_selection = "governing_working_envelope"

    if analysis.stability is not None:
        direction_results: list[StabilityDirectionResult] = []
        for stability_direction in stability_directions:
            member_comparisons: list[MemberStabilityComparison] = []
            for declaration in analysis.members:
                point_loads = [
                    load
                    for load in analysis.member_loads
                    if load.member_id == declaration.id
                ]
                distributed_loads = [
                    load
                    for load in analysis.member_distributed_loads
                    if load.member_id == declaration.id
                ]
                second_moment, second_displacement = _member_extrema(
                    model,
                    declaration,
                    combination_id=stability_direction["stability_combination_id"],
                    point_loads=point_loads,
                    distributed_loads=distributed_loads,
                )
                first_moment, first_displacement = first_order_stability[
                    (stability_direction["id"], declaration.id)
                ]
                member_comparisons.append(
                    MemberStabilityComparison(
                        member_id=declaration.id,
                        first_order_max_moment_kNm=first_moment,
                        second_order_max_moment_kNm=second_moment,
                        moment_amplification=_amplification(
                            second_moment, first_moment
                        ),
                        first_order_max_displacement_mm=first_displacement,
                        second_order_max_displacement_mm=second_displacement,
                        displacement_amplification=_amplification(
                            second_displacement, first_displacement
                        ),
                    )
                )
            nhf_displacement_mm = first_order_nhf_eaves_displacement_mm.get(
                stability_direction["id"], 0.0
            )
            alpha_cr = (
                analysis.stability.column_height_m
                * 1000.0
                / (200.0 * nhf_displacement_mm)
                if analysis.stability.column_height_m is not None
                and nhf_displacement_mm > 1e-12
                else None
            )
            governing_comparisons = _stability_scope_comparisons(
                member_comparisons,
                eaves_member_ids=analysis.stability.eaves_member_ids,
                rafter_member_ids=analysis.stability.rafter_member_ids,
            )
            direction_results.append(
                StabilityDirectionResult(
                    id=stability_direction["id"],
                    combination_id=stability_direction["stability_combination_id"],
                    imperfection_case_id=stability_direction["imperfection_case_id"],
                    nhf_combination_id=stability_direction["nhf_combination_id"],
                    horizontal_axis=(
                        "x" if stability_direction["horizontal_axis"] == "x" else "y"
                    ),
                    converged=True,
                    governing_moment_amplification=max(
                        comparison.moment_amplification
                        for comparison in governing_comparisons
                    ),
                    governing_displacement_amplification=max(
                        comparison.displacement_amplification
                        for comparison in governing_comparisons
                    ),
                    nhf_eaves_displacement_mm=nhf_displacement_mm,
                    alpha_cr=alpha_cr,
                    member_comparisons=member_comparisons,
                )
            )
        governing_direction = max(
            direction_results,
            key=lambda result: max(
                result.governing_moment_amplification,
                result.governing_displacement_amplification,
            ),
        )
        alpha_cr_values = [
            result.alpha_cr
            for result in direction_results
            if result.alpha_cr is not None
        ]
        rafter_design_axial_kN = (
            max(first_order_rafter_axial_kN.values())
            if analysis.stability.rafter_member_ids
            else None
        )
        rafter_elastic_critical_load_kN: float | None = None
        if analysis.stability.rafter_member_ids:
            declarations_by_id = {
                declaration.id: declaration for declaration in analysis.members
            }
            sections_by_id = {section.id: section for section in analysis.sections}
            materials_by_id = {material.id: material for material in analysis.materials}
            rafter_length_m = sum(
                _length(
                    declarations_by_id[member_id].start,
                    declarations_by_id[member_id].end,
                )
                for member_id in analysis.stability.rafter_member_ids
            )
            minimum_ei_kNm2 = min(
                materials_by_id[
                    declarations_by_id[member_id].material_id
                ].elastic_modulus_kN_m2
                * max(
                    sections_by_id[declarations_by_id[member_id].section_id].iy_m4,
                    sections_by_id[declarations_by_id[member_id].section_id].iz_m4,
                )
                for member_id in analysis.stability.rafter_member_ids
            )
            rafter_elastic_critical_load_kN = (
                pi**2 * minimum_ei_kNm2 / rafter_length_m**2
            )
        rafter_axial_limit_kN = (
            0.09 * rafter_elastic_critical_load_kN
            if rafter_elastic_critical_load_kN is not None
            else None
        )
        rafter_axial_force_significant = (
            rafter_design_axial_kN > rafter_axial_limit_kN
            if rafter_design_axial_kN is not None and rafter_axial_limit_kN is not None
            else None
        )
        stability_result = StabilityResult(
            method=analysis.stability.method,
            combination_id=governing_direction.combination_id,
            imperfection_case_id=governing_direction.imperfection_case_id,
            converged=all(result.converged for result in direction_results),
            amplification_warning_ratio=(
                analysis.stability.amplification_warning_ratio
            ),
            governing_moment_amplification=max(
                result.governing_moment_amplification for result in direction_results
            ),
            governing_displacement_amplification=max(
                result.governing_displacement_amplification
                for result in direction_results
            ),
            member_comparisons=governing_direction.member_comparisons,
            direction_results=direction_results,
            governing_direction_id=governing_direction.id,
            minimum_alpha_cr=(min(alpha_cr_values) if alpha_cr_values else None),
            second_order_required=(
                min(alpha_cr_values) < 10.0 if alpha_cr_values else None
            ),
            rafter_design_axial_kN=rafter_design_axial_kN,
            rafter_elastic_critical_load_kN=rafter_elastic_critical_load_kN,
            rafter_axial_limit_kN=rafter_axial_limit_kN,
            rafter_axial_force_significant=rafter_axial_force_significant,
            simplified_alpha_cr_applicable=(
                not rafter_axial_force_significant
                if rafter_axial_force_significant is not None
                else None
            ),
        )

    structural_nodes = [
        StructuralNode(
            id=node["id"],
            label=" / ".join(dict.fromkeys(node["labels"])),
            position=node["position"],
            restraints=node["restraints"],
            visual_node_id=node["visual_node_id"],
        )
        for node in nodes_by_topology.values()
    ]
    structural_members: list[StructuralMember] = []
    member_results: list[MemberResult] = []
    member_diagrams: list[MemberDiagram] = []
    member_checks: list[MemberCheck] = []
    tension_member_checks = _tension_member_checks(
        model,
        analysis,
        capture.connections,
        components,
    )
    connection_checks = _connection_checks(
        model,
        analysis,
        capture.connections,
        components,
        tension_member_checks,
    )
    connection_checks_by_id = {
        check.connection_id: check for check in connection_checks
    }
    bracing_load_path_traces = _bracing_load_path_traces(
        capture,
        analysis,
        tension_member_checks,
        connection_checks,
    )
    cross_section_checks = _cross_section_checks(
        model,
        analysis,
        components,
        capture.connections,
    )
    cross_section_checks_by_member = {
        check.member_id: check for check in cross_section_checks
    }
    member_stability_checks = _member_stability_checks(
        model,
        analysis,
        connection_checks_by_id=connection_checks_by_id,
    )
    member_restraint_candidate_checks = (
        [
            check
            for restraint_combination_id in (
                list(analysis.member_stability_verification.combination_ids)
                + (
                    [active_combination.id]
                    if active_combination.id
                    not in analysis.member_stability_verification.combination_ids
                    else []
                )
            )
            for check in _member_restraint_candidate_checks(
                model,
                analysis,
                combination_id=restraint_combination_id,
                connection_checks_by_id=connection_checks_by_id,
            )
        ]
        if analysis.member_stability_verification is not None
        else []
    )
    member_restraint_traces = _member_restraint_traces(
        model,
        analysis,
        combination_id=active_combination.id,
        connection_checks_by_id=connection_checks_by_id,
    )
    member_stability_checks_by_member: dict[str, list[MemberStabilityCheck]] = {}
    for stability_check in member_stability_checks:
        member_stability_checks_by_member.setdefault(
            stability_check.member_id,
            [],
        ).append(stability_check)
    unrestrained_pass_member_ids = {
        member_id
        for member_id, checks in member_stability_checks_by_member.items()
        if checks and all(check.status == "pass" for check in checks)
    }
    member_restraint_candidate_checks = [
        check.model_copy(
            update={
                "status": "not_required",
                "basis": (
                    f"{check.basis} The complete physical component passes the "
                    "member-stability interaction with no external restraint "
                    "credited, so this candidate is not required for that check."
                ),
            }
        )
        if check.member_id in unrestrained_pass_member_ids
        else check
        for check in member_restraint_candidate_checks
    ]
    member_restraint_traces = [
        trace.model_copy(
            update={
                "status": "not_required",
                "basis": (
                    f"{trace.basis} The complete physical component passes as "
                    "unbraced, so no external restraint is required."
                ),
            }
        )
        if trace.member_id in unrestrained_pass_member_ids
        else trace
        for trace in member_restraint_traces
    ]
    serviceability_checks: list[ServiceabilityCheck] = []
    serviceability_groups: dict[str, dict[str, Any]] = {}
    sections_by_id = {section.id: section for section in analysis.sections}

    for declaration in analysis.members:
        component = components[declaration.component_id]
        start_node_id, end_node_id = member_node_ids[declaration.id]
        structural_members.append(
            StructuralMember(
                id=declaration.id,
                label=declaration.label,
                start_node_id=start_node_id,
                end_node_id=end_node_id,
                section_id=declaration.section_id,
                material_id=declaration.material_id,
                visual_node_id=component.visual_node_id,
                tension_only=declaration.tension_only,
                compression_only=declaration.compression_only,
                analytical_role=declaration.analytical_role,
                source_connection_id=declaration.source_connection_id,
            )
        )
        member_length = _length(declaration.start, declaration.end)
        point_loads = [
            load for load in analysis.member_loads if load.member_id == declaration.id
        ]
        distributed_loads = [
            load
            for load in analysis.member_distributed_loads
            if load.member_id == declaration.id
        ]
        station_distances = {
            member_length * index / STATION_INTERVALS
            for index in range(STATION_INTERVALS + 1)
        }
        station_distances.update(load.distance_m for load in point_loads)
        for station_line_load in distributed_loads:
            station_distances.update(
                (
                    station_line_load.start_distance_m,
                    station_line_load.end_distance_m,
                )
            )

        member = model.members[declaration.id]
        rotation = member.T()[:3, :3]
        stations: list[MemberDiagramStation] = []
        max_moment = 0.0
        max_local_moment_y = 0.0
        max_local_moment_z = 0.0
        max_shear = 0.0
        max_axial = 0.0
        max_displacement = 0.0
        max_relative_deflection = 0.0
        start_local_displacement = (
            member.deflection("dx", 0.0, active_combination.id) * 1000.0,
            member.deflection("dy", 0.0, active_combination.id) * 1000.0,
            member.deflection("dz", 0.0, active_combination.id) * 1000.0,
        )
        end_local_displacement = (
            member.deflection("dx", member_length, active_combination.id) * 1000.0,
            member.deflection("dy", member_length, active_combination.id) * 1000.0,
            member.deflection("dz", member_length, active_combination.id) * 1000.0,
        )
        for distance in sorted(station_distances):
            ratio = distance / member_length
            position = Vector3(
                x=declaration.start.x
                + (declaration.end.x - declaration.start.x) * ratio,
                y=declaration.start.y
                + (declaration.end.y - declaration.start.y) * ratio,
                z=declaration.start.z
                + (declaration.end.z - declaration.start.z) * ratio,
            )
            if declaration.tension_only:
                # Tension-only brace members are axial ties. PyNite retains tiny
                # frame stiffness for numerical stability, so suppress those
                # non-physical bending/shear results and report axial elongation.
                local_moment = (0.0, 0.0, 0.0)
                local_shear = (0.0, 0.0, 0.0)
                local_displacement = (
                    member.deflection("dx", distance, active_combination.id) * 1000.0,
                    0.0,
                    0.0,
                )
            else:
                local_moment = (
                    0.0,
                    member.moment("My", distance, active_combination.id),
                    member.moment("Mz", distance, active_combination.id),
                )
                local_shear = (
                    0.0,
                    member.shear("Fy", distance, active_combination.id),
                    member.shear("Fz", distance, active_combination.id),
                )
                local_displacement = (
                    member.deflection("dx", distance, active_combination.id) * 1000.0,
                    member.deflection("dy", distance, active_combination.id) * 1000.0,
                    member.deflection("dz", distance, active_combination.id) * 1000.0,
                )
            global_moment = _local_to_global(rotation, local_moment)
            global_major_moment = _local_to_global(
                rotation,
                (0.0, 0.0, local_moment[2]),
            )
            global_minor_moment = _local_to_global(
                rotation,
                (0.0, local_moment[1], 0.0),
            )
            global_shear = _local_to_global(rotation, local_shear)
            global_displacement = _local_to_global(rotation, local_displacement)
            axial = member.axial(distance, active_combination.id)
            max_moment = max(
                max_moment,
                sqrt(sum(value**2 for value in local_moment)),
            )
            max_local_moment_y = max(max_local_moment_y, abs(local_moment[1]))
            max_local_moment_z = max(max_local_moment_z, abs(local_moment[2]))
            max_shear = max(
                max_shear,
                sqrt(sum(value**2 for value in local_shear)),
            )
            max_axial = max(max_axial, abs(axial))
            max_displacement = max(
                max_displacement,
                sqrt(sum(value**2 for value in local_displacement)),
            )
            if not (declaration.tension_only or declaration.compression_only):
                max_relative_deflection = max(
                    max_relative_deflection,
                    _relative_transverse_deflection_mm(
                        local_displacement,
                        start_local_displacement,
                        end_local_displacement,
                        ratio,
                    ),
                )
            stations.append(
                MemberDiagramStation(
                    distance_m=distance,
                    position=position,
                    moment_kNm=global_moment,
                    major_moment_kNm=global_major_moment,
                    minor_moment_kNm=global_minor_moment,
                    shear_kN=global_shear,
                    displacement_mm=global_displacement,
                )
            )

        member_results.append(
            MemberResult(
                member_id=declaration.id,
                combination_id=active_combination.id,
                max_moment_kNm=max_moment,
                max_shear_kN=max_shear,
                max_axial_kN=max_axial,
                max_displacement_mm=max_displacement,
            )
        )
        member_diagrams.append(
            MemberDiagram(
                member_id=declaration.id,
                visual_node_id=component.visual_node_id,
                stations=stations,
            )
        )
        section = sections_by_id[declaration.section_id]
        cross_section_check = cross_section_checks_by_member.get(declaration.id)
        stability_member_checks = member_stability_checks_by_member.get(
            declaration.id,
            [],
        )
        governing_stability_check = max(
            stability_member_checks,
            key=lambda check: (
                check.governing_utilisation
                if check.governing_utilisation is not None
                else check.axial_utilisation
                if check.axial_utilisation is not None
                else -1.0
            ),
            default=None,
        )
        if declaration.analytical_role == "rigid_zone":
            # A rigid zone is an idealised connection arm, not a physical
            # catalogue member.  Its displacement remains visible, while
            # member and cross-section resistance checks stay with the
            # connected flexible Cee portions and the connection calculation.
            pass
        elif (
            governing_stability_check is not None
            and governing_stability_check.status in {"pass", "fail"}
        ):
            member_checks.append(
                MemberCheck(
                    member_id=declaration.id,
                    label=f"{declaration.label} Stage 7 member stability",
                    demand_kNm=governing_stability_check.major_moment_kNm or 0.0,
                    capacity_kNm=(
                        governing_stability_check.design_major_bending_capacity_kNm
                    ),
                    utilisation=(
                        governing_stability_check.governing_utilisation
                        if governing_stability_check.governing_utilisation is not None
                        else governing_stability_check.axial_utilisation
                    ),
                    status=(
                        "pass" if governing_stability_check.status == "pass" else "fail"
                    ),
                    basis=(
                        f"{governing_stability_check.basis} Governing ULS "
                        f"envelope: "
                        f"{governing_stability_check.governing_combination_id} at "
                        f"{governing_stability_check.governing_station_m:.3f} m. "
                        "Stage 8 bracing resistance and Stage 9 connections "
                        "remain separate."
                    ),
                )
            )
        elif governing_stability_check is not None:
            member_checks.append(
                MemberCheck(
                    member_id=declaration.id,
                    label=f"{declaration.label} Stage 7 member stability",
                    demand_kNm=governing_stability_check.major_moment_kNm or 0.0,
                    capacity_kNm=None,
                    utilisation=None,
                    status="not_checked",
                    basis=(
                        f"{governing_stability_check.basis} "
                        + " ".join(governing_stability_check.assumptions)
                    ),
                )
            )
        elif cross_section_check is not None and cross_section_check.status in {
            "pass",
            "fail",
        }:
            member_checks.append(
                MemberCheck(
                    member_id=declaration.id,
                    label=f"{declaration.label} Stage 6 cross-section",
                    demand_kNm=(cross_section_check.major_moment_kNm or 0.0),
                    capacity_kNm=(
                        cross_section_check.design_major_bending_capacity_kNm
                    ),
                    utilisation=cross_section_check.governing_utilisation,
                    status=("pass" if cross_section_check.status == "pass" else "fail"),
                    basis=(
                        f"CROSS-SECTION ONLY — {cross_section_check.basis} "
                        f"Governing ULS envelope: "
                        f"{cross_section_check.governing_combination_id} at "
                        f"{cross_section_check.governing_station_m:.3f} m. "
                        "Stage 7 member stability, restraints, and connections "
                        "remain incomplete."
                    ),
                )
            )
        elif cross_section_check is not None:
            member_checks.append(
                MemberCheck(
                    member_id=declaration.id,
                    label=f"{declaration.label} Stage 6 cross-section",
                    demand_kNm=max_moment,
                    capacity_kNm=None,
                    utilisation=None,
                    status="not_checked",
                    basis=cross_section_check.basis,
                )
            )
        elif section.bending_reference_kNm is None:
            member_checks.append(
                MemberCheck(
                    member_id=declaration.id,
                    label=f"{declaration.label} bending demand",
                    demand_kNm=max_moment,
                    capacity_kNm=None,
                    utilisation=None,
                    status="not_checked",
                    basis=(
                        "Elastic demand only — no traceable bending reference is "
                        "connected."
                    ),
                )
            )
        else:
            reference_demand = (
                max_local_moment_y
                if section.bending_reference_axis == "local_y"
                else max_local_moment_z
                if section.bending_reference_axis == "local_z"
                else max_moment
            )
            unreferenced_demand = (
                max_local_moment_z
                if section.bending_reference_axis == "local_y"
                else max_local_moment_y
                if section.bending_reference_axis == "local_z"
                else 0.0
            )
            bending_utilisation = reference_demand / section.bending_reference_kNm
            if unreferenced_demand > reference_demand + 1e-12:
                member_checks.append(
                    MemberCheck(
                        member_id=declaration.id,
                        label=f"{declaration.label} governing bending reference",
                        demand_kNm=unreferenced_demand,
                        capacity_kNm=None,
                        utilisation=None,
                        status="not_checked",
                        basis=(
                            "The governing demand is outside the authored "
                            f"{section.bending_reference_axis} reference axis. "
                            "A matching catalogue reference and biaxial interaction "
                            "check are required."
                        ),
                    )
                )
            else:
                member_checks.append(
                    MemberCheck(
                        member_id=declaration.id,
                        label=(
                            f"{declaration.label} "
                            f"{section.bending_reference_axis} yield reference"
                        ),
                        demand_kNm=reference_demand,
                        capacity_kNm=section.bending_reference_kNm,
                        utilisation=bending_utilisation,
                        status="not_checked",
                        basis=(
                            "RENDERER REFERENCE ONLY — "
                            + (
                                section.bending_reference_basis
                                or "Authored effective-section yield reference."
                            )
                            + " This is not a P399/Australian member verification."
                        ),
                    )
                )

        serviceability_span_m = declaration.serviceability_span_m or member_length
        limit_candidates: list[float] = []
        if declaration.deflection_limit_ratio is not None:
            limit_candidates.append(
                serviceability_span_m * 1000.0 / declaration.deflection_limit_ratio
            )
        if declaration.deflection_limit_mm is not None:
            limit_candidates.append(declaration.deflection_limit_mm)
        limit_mm = min(limit_candidates) if limit_candidates else None
        group_id = declaration.serviceability_group_id or declaration.id
        group = serviceability_groups.setdefault(
            group_id,
            {
                "physical_member_id": declaration.serviceability_group_id,
                "label": declaration.serviceability_group_label or declaration.label,
                "span_m": serviceability_span_m,
                "member_ids": [],
                "governing_member_id": declaration.id,
                "displacement_mm": max_relative_deflection,
                "limits_mm": [],
                "bases": [],
                "axial_only": (
                    declaration.tension_only or declaration.compression_only
                ),
            },
        )
        group["member_ids"].append(declaration.id)
        group["span_m"] = max(float(group["span_m"]), serviceability_span_m)
        if max_relative_deflection > float(group["displacement_mm"]):
            group["displacement_mm"] = max_relative_deflection
            group["governing_member_id"] = declaration.id
        if limit_mm is not None:
            group["limits_mm"].append(limit_mm)
        if declaration.deflection_limit_basis:
            group["bases"].append(declaration.deflection_limit_basis)
        group["axial_only"] = bool(group["axial_only"]) or (
            declaration.tension_only or declaration.compression_only
        )

    for group in serviceability_groups.values():
        group_limits = [float(value) for value in group["limits_mm"]]
        limit_mm = min(group_limits) if group_limits else None
        bases = list(dict.fromkeys(str(value) for value in group["bases"]))
        basis = "; ".join(bases)
        displacement_mm = float(group["displacement_mm"])
        checked = (
            active_combination.limit_state == "serviceability"
            and limit_mm is not None
            and not bool(group["axial_only"])
        )
        utilisation = displacement_mm / limit_mm if checked and limit_mm else None
        serviceability_checks.append(
            ServiceabilityCheck(
                member_id=str(group["governing_member_id"]),
                physical_member_id=group["physical_member_id"],
                analytical_member_ids=list(group["member_ids"]),
                span_m=float(group["span_m"]),
                label=f"{group['label']} deflection",
                combination_id=active_combination.id,
                displacement_mm=displacement_mm,
                limit_mm=limit_mm,
                utilisation=utilisation,
                status=(
                    "not_checked"
                    if not checked
                    else "pass"
                    if utilisation is not None and utilisation <= 1.0
                    else "fail"
                ),
                basis=(
                    (
                        "Transverse L/n deflection is not applicable to an axial-only "
                        "tension/compression member. Axial deformation and resistance "
                        "remain in the member and bracing checks."
                        if bool(group["axial_only"])
                        else (
                            basis
                            if basis
                            else "Deflection checks require a serviceability "
                            "combination and an authored project criterion."
                        )
                        + " Member deflection is measured relative to the displaced "
                        "straight chord between its ends; overall frame drift is a "
                        "separate serviceability check."
                    )
                ),
            )
        )

    reaction_values: list[NodeReaction] = []
    reaction_force_sum = [0.0, 0.0, 0.0]
    reaction_moment_sum = [0.0, 0.0, 0.0]
    reaction_force_absolute_sum = [0.0, 0.0, 0.0]
    reaction_moment_absolute_sum = [0.0, 0.0, 0.0]
    for node in nodes_by_topology.values():
        restraints = node["restraints"]
        if not any(restraints.values()):
            continue
        solved_node = model.nodes[node["id"]]
        force = Vector3(
            x=_clean(solved_node.RxnFX[active_combination.id]),
            y=_clean(solved_node.RxnFY[active_combination.id]),
            z=_clean(solved_node.RxnFZ[active_combination.id]),
        )
        moment = Vector3(
            x=_clean(solved_node.RxnMX[active_combination.id]),
            y=_clean(solved_node.RxnMY[active_combination.id]),
            z=_clean(solved_node.RxnMZ[active_combination.id]),
        )
        reaction_values.append(
            NodeReaction(
                node_id=node["id"],
                combination_id=active_combination.id,
                force=force,
                moment=moment,
            )
        )
        _add(reaction_force_sum, force)
        _add(reaction_moment_sum, moment)
        reaction_force_moment = _cross(node["position"], force)
        _add(reaction_moment_sum, reaction_force_moment)
        _add_absolute(reaction_force_absolute_sum, force)
        _add_absolute(reaction_moment_absolute_sum, moment)
        _add_absolute(reaction_moment_absolute_sum, reaction_force_moment)

    # Audit the exact global action vector assembled by PyNite. Reconstructing
    # distributed member actions from the declarations can diverge from the
    # solver after physical-member segmentation or P-Delta analysis. PyNite
    # solves K D = P - FER, so P - FER is the single authoritative equivalent
    # nodal action vector for the active combination.
    equivalent_nodal_actions = (
        model.P(active_combination.id) - model.FER(active_combination.id)
    ).reshape(-1)
    applied_force_sum = [0.0, 0.0, 0.0]
    applied_moment_sum = [0.0, 0.0, 0.0]
    applied_force_absolute_sum = [0.0, 0.0, 0.0]
    applied_moment_absolute_sum = [0.0, 0.0, 0.0]
    for solved_node in model.nodes.values():
        dof = solved_node.ID * 6
        force = Vector3(
            x=_clean(equivalent_nodal_actions[dof]),
            y=_clean(equivalent_nodal_actions[dof + 1]),
            z=_clean(equivalent_nodal_actions[dof + 2]),
        )
        moment = Vector3(
            x=_clean(equivalent_nodal_actions[dof + 3]),
            y=_clean(equivalent_nodal_actions[dof + 4]),
            z=_clean(equivalent_nodal_actions[dof + 5]),
        )
        position = Vector3(
            x=float(solved_node.X),
            y=float(solved_node.Y),
            z=float(solved_node.Z),
        )
        if analysis.stability is not None:
            position = Vector3(
                x=position.x + _clean(solved_node.DX[active_combination.id]),
                y=position.y + _clean(solved_node.DY[active_combination.id]),
                z=position.z + _clean(solved_node.DZ[active_combination.id]),
            )
        _add(applied_force_sum, force)
        _add(applied_moment_sum, moment)
        applied_force_moment = _cross(position, force)
        _add(applied_moment_sum, applied_force_moment)
        _add_absolute(applied_force_absolute_sum, force)
        _add_absolute(applied_moment_absolute_sum, moment)
        _add_absolute(applied_moment_absolute_sum, applied_force_moment)

    force_residual = tuple(
        applied_force_sum[index] + reaction_force_sum[index] for index in range(3)
    )
    moment_residual = tuple(
        applied_moment_sum[index] + reaction_moment_sum[index] for index in range(3)
    )
    residual = max(abs(value) for value in (*force_residual, *moment_residual))
    # A signed resultant can be close to zero when large opposing actions are
    # present. Scale the nonlinear numeric audit by the absolute assembled
    # action/reaction magnitude so cancellation cannot make the tolerance
    # arbitrarily stricter than the solved model's force scale.
    equilibrium_scale = max(
        *applied_force_absolute_sum,
        *applied_moment_absolute_sum,
        *reaction_force_absolute_sum,
        *reaction_moment_absolute_sum,
    )
    equilibrium_tolerance = (
        RESIDUAL_TOLERANCE
        if analysis.stability is None
        else max(
            RESIDUAL_TOLERANCE,
            equilibrium_scale * PDELTA_RESIDUAL_RELATIVE_TOLERANCE,
        )
    )
    equilibrium_status: Literal["pass", "fail"] = (
        "pass" if residual <= equilibrium_tolerance else "fail"
    )
    summary = _load_summary(analysis, active_combination)
    checked_serviceability = [
        check for check in serviceability_checks if check.status != "not_checked"
    ]
    verification_stages, calculation_sheets = _certification_evidence(
        capture=capture,
        analysis=analysis,
        combination=active_combination,
        nodes=structural_nodes,
        members=structural_members,
        member_results=member_results,
        member_checks=member_checks,
        connection_checks=connection_checks,
        tension_member_checks=tension_member_checks,
        bracing_load_path_traces=bracing_load_path_traces,
        cross_section_checks=cross_section_checks,
        member_stability_checks=member_stability_checks,
        member_restraint_candidate_checks=member_restraint_candidate_checks,
        serviceability_checks=serviceability_checks,
        equilibrium_status=equilibrium_status,
        residual=residual,
        equilibrium_tolerance=equilibrium_tolerance,
        stability_result=stability_result,
    )
    (
        certification_readiness,
        verification_stages,
        calculation_sheets,
    ) = _australian_certification_readiness(
        capture=capture,
        analysis=analysis,
        stages=verification_stages,
        sheets=calculation_sheets,
        equilibrium_status=equilibrium_status,
        tension_member_checks=tension_member_checks,
        member_results=member_results,
        cross_section_checks=cross_section_checks,
        member_stability_checks=member_stability_checks,
        member_restraint_candidate_checks=member_restraint_candidate_checks,
        connection_checks=connection_checks,
        bracing_load_path_traces=bracing_load_path_traces,
    )
    cross_section_stage = next(
        stage for stage in verification_stages if stage.id == "cross_section"
    )
    if (
        analysis.cross_section_verification is not None
        and cross_section_stage.status not in {"pass", "fail"}
    ):
        member_checks = [
            check.model_copy(
                update={
                    "capacity_kNm": None,
                    "utilisation": None,
                    "status": "not_checked",
                    "basis": (
                        f"{check.basis} Renderer colour remains neutral because "
                        f"Stage 6 is {cross_section_stage.status}."
                    ),
                }
            )
            for check in member_checks
        ]

    return StructuralSnapshot(
        mode="design",
        title=capture.title,
        subtitle=(
            (
                "Multi-member iterative P-Delta analysis"
                if analysis.stability is not None
                else "Multi-member first-order elastic analysis"
            )
            + f" — {active_combination.label}"
        ),
        source=SnapshotSource(
            kind="design",
            label=capture.project_name,
            design_id=capture.project_name,
            design_hash=capture.design_hash,
            analysis_configuration_revision=(capture.analysis_configuration_revision),
            analysis_configuration_digest=capture.analysis_configuration_digest,
        ),
        design_basis=capture.design_basis,
        wind_action_bases=capture.wind_action_bases,
        nodes=structural_nodes,
        members=structural_members,
        sections=analysis.sections,
        materials=analysis.materials,
        load_cases=analysis.load_cases,
        load_combinations=combinations,
        unavailable_load_combinations=analysis.unavailable_load_combinations,
        action_standard_pack=analysis.action_standard_pack,
        loads=generated_nodal_loads,
        member_loads=analysis.member_loads,
        member_distributed_loads=analysis.member_distributed_loads,
        reactions=reaction_values,
        member_results=member_results,
        member_diagrams=member_diagrams,
        member_checks=member_checks,
        connection_checks=connection_checks,
        tension_member_checks=tension_member_checks,
        bracing_load_path_traces=bracing_load_path_traces,
        cross_section_checks=cross_section_checks,
        member_stability_checks=member_stability_checks,
        member_restraint_candidate_checks=member_restraint_candidate_checks,
        member_restraint_traces=member_restraint_traces,
        serviceability_checks=serviceability_checks,
        load_summary=summary,
        equilibrium=EquilibriumDiagnostic(
            force_residual_kN=_vector(force_residual),
            moment_residual_kNm=_vector(moment_residual),
            tolerance=equilibrium_tolerance,
            status=equilibrium_status,
        ),
        solver=SolverMetadata(
            name="PyNiteFEA",
            version=version("PyNiteFEA"),
            analysis=(
                (
                    "3D iterative P-Delta frame with captured first-order comparison; "
                    if analysis.stability is not None
                    else "3D first-order elastic frame; "
                )
                + "shared-coordinate nodes, point loads, and global distributed loads"
            ),
            combination_id=active_combination.id,
            combination_selection=combination_selection,
        ),
        stability=stability_result,
        verification_stages=verification_stages,
        calculation_sheets=calculation_sheets,
        certification_readiness=certification_readiness,
        capabilities=[
            CapabilityState(
                id="design-capture",
                label="Design capture",
                status="online",
                detail="Compiled analytical declarations are linked to CAD members.",
            ),
            CapabilityState(
                id="gravity",
                label="Self-weight",
                status="online" if summary.self_weight_kN > 0 else "pending",
                detail=(
                    f"{summary.member_mass_kg:.3f} kg of catalogue member mass "
                    "is applied as distributed gravity load."
                    if summary.self_weight_kN > 0
                    else "No catalogue member self-weight has been authored."
                ),
            ),
            CapabilityState(
                id="solver",
                label="Frame solver",
                status="online",
                detail=(
                    f"{len(structural_members)} members and "
                    f"{len(structural_nodes)} shared nodes are solved."
                ),
            ),
            CapabilityState(
                id="stability",
                label="P-Delta",
                status="online" if stability_result is not None else "pending",
                detail=(
                    (
                        f"{stability_result.combination_id} converged with governing "
                        f"moment amplification "
                        f"{stability_result.governing_moment_amplification:.4f} and "
                        f"displacement amplification "
                        f"{stability_result.governing_displacement_amplification:.4f}."
                    )
                    if stability_result is not None
                    else "No authored imperfection case and stability combination."
                ),
            ),
            CapabilityState(
                id="serviceability",
                label="Deflection",
                status="online" if checked_serviceability else "pending",
                detail=(
                    f"{len(checked_serviceability)} authored deflection criteria "
                    "are evaluated."
                    if checked_serviceability
                    else "No project deflection criteria are authored."
                ),
            ),
            CapabilityState(
                id="equilibrium",
                label="Equilibrium",
                status="online" if equilibrium_status == "pass" else "blocked",
                detail=(
                    f"Global residual is {residual:.3e} against tolerance "
                    f"{equilibrium_tolerance:.3e}."
                ),
            ),
            CapabilityState(
                id="checks",
                label="Cross-section",
                status=(
                    "online"
                    if cross_section_stage.status == "pass"
                    else "blocked"
                    if cross_section_stage.status == "fail"
                    or analysis.cross_section_verification is None
                    else "pending"
                ),
                detail=(
                    f"{len(cross_section_checks)} catalogue-backed members pass "
                    "the authored ULS section-only envelope; member stability "
                    "remains a separate stage."
                    if cross_section_stage.status == "pass"
                    else (
                        f"{sum(check.status == 'fail' for check in cross_section_checks)} "
                        "members fail the section-only ULS envelope."
                    )
                    if cross_section_stage.status == "fail"
                    else (
                        "A section-capacity pack is selected, but prerequisite "
                        f"evidence leaves Stage 6 {cross_section_stage.status}."
                    )
                    if analysis.cross_section_verification is not None
                    else "No versioned cross-section calculation pack is active."
                ),
            ),
            CapabilityState(
                id="connections",
                label="Connections",
                status="pending",
                detail="Screws, bolts, brackets, anchors, and concrete are not checked.",
            ),
        ],
        warnings=[
            (
                "CROSS-SECTION EVIDENCE ONLY — NOT FOR CERTIFICATION OR ORDERING. "
                "MEMBER STABILITY, RESTRAINT, BRACING, AND CONNECTIONS REMAIN "
                "INCOMPLETE."
                if analysis.cross_section_verification is not None
                else "ELASTIC MEMBER DEMAND ONLY — NOT FOR DESIGN, CERTIFICATION, "
                "OR ORDERING."
            ),
            *dict.fromkeys(member.assumption for member in analysis.members),
            (
                "Non-steel permanent actions are included only where Structural "
                "workbench state authors a traceable distributed or point load."
            ),
            (
                "Stages 6 and 7 use catalogue effective properties and the "
                "accepted AS/NZS 4600:2005+A1 project-basis pack. Global, "
                "distortional, and unbraced lateral-torsional member resistance "
                "are calculated; off-axis member resistance, restraint systems, "
                "connections, anchors, concrete, impact, and progressive collapse "
                "remain separate or incomplete checks."
                if analysis.cross_section_verification is not None
                else "The displayed bending threshold is an effective-section "
                "yield reference only. AS/NZS 4600 member capacity, "
                "lateral-torsional buckling, restraint, connections, anchors, "
                "concrete, impact, and progressive collapse are not checked."
            ),
        ],
    )
