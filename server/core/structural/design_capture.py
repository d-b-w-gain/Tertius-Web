from __future__ import annotations

import ast
from collections import deque
from dataclasses import dataclass, field
from hashlib import sha256
import operator
from typing import Any, Callable

from pydantic import ValidationError

from .contracts import (
    CapabilityState,
    DesignAnalysisDefinition,
    DesignComponent,
    DesignConnection,
    DesignLoadPath,
    DesignSurfaceLoad,
    ProjectStructuralCapture,
    StructuralDesignBasis,
)

DECLARATION_NAME = "TERTIUS_STRUCTURAL"
_COMPONENT_METHOD_KINDS = {
    "ground": "ground",
    "member": "member",
    "surface": "surface",
    "connector": "connector",
    "support": "support",
}
_MISSING = object()


class StructuralDeclarationError(ValueError):
    """Raised when a design's static structural declaration cannot be captured."""


def _load_case_id(value: str) -> str:
    case_id = str(value).strip()
    if not case_id:
        raise StructuralDeclarationError("load case ID must not be empty")
    return case_id if case_id.startswith("case-") else f"case-{case_id}"


_BINARY_OPERATORS: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPERATORS: dict[type[ast.unaryop], Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
    ast.Not: operator.not_,
}


@dataclass(frozen=True)
class _ComponentHandle:
    model_name: str
    component_id: str


@dataclass(frozen=True)
class _SpecHandle:
    model_name: str
    id: str


@dataclass
class _GeneratedModel:
    name: str
    title: str
    design_basis: dict[str, Any] | None = None
    stability: dict[str, Any] | None = None
    components: list[dict[str, Any]] = field(default_factory=list)
    connections: list[dict[str, Any]] = field(default_factory=list)
    loads: list[dict[str, Any]] = field(default_factory=list)
    materials: list[dict[str, Any]] = field(default_factory=list)
    sections: list[dict[str, Any]] = field(default_factory=list)
    analytical_members: list[dict[str, Any]] = field(default_factory=list)
    member_loads: list[dict[str, Any]] = field(default_factory=list)
    member_distributed_loads: list[dict[str, Any]] = field(default_factory=list)
    load_combinations: list[dict[str, Any]] = field(default_factory=list)
    load_case_categories: dict[str, str] = field(default_factory=dict)
    load_case_labels: dict[str, str] = field(default_factory=dict)
    assembled_component_ids: list[str] | None = None

    def declaration(self) -> dict[str, Any]:
        if self.assembled_component_ids is None:
            raise StructuralDeclarationError(
                "call StructuralModel.assembly(...) before assigning "
                f"{DECLARATION_NAME}"
            )
        component_ids = [component["id"] for component in self.components]
        if len(self.assembled_component_ids) != len(set(self.assembled_component_ids)):
            raise StructuralDeclarationError(
                "StructuralModel.assembly(...) contains a component more than once"
            )
        missing = sorted(set(component_ids) - set(self.assembled_component_ids))
        extra = sorted(set(self.assembled_component_ids) - set(component_ids))
        if missing or extra:
            raise StructuralDeclarationError(
                "StructuralModel.assembly(...) must contain every registered component "
                f"exactly once; missing={missing}, unregistered={extra}"
            )
        return {
            "title": self.title,
            "design_basis": self.design_basis,
            "authoring": {
                "mode": "generated",
                "assembly_component_ids": list(self.assembled_component_ids),
            },
            "components": self.components,
            "connections": self.connections,
            "loads": self.loads,
            "analysis": {
                "materials": self.materials,
                "sections": self.sections,
                "members": self.analytical_members,
                "load_cases": [
                    {
                        "id": case_id,
                        "label": self.load_case_labels.get(
                            case_id,
                            f"{category.title()} load",
                        ),
                        "category": category,
                    }
                    for case_id, category in self.load_case_categories.items()
                ],
                "load_combinations": (
                    self.load_combinations
                    if self.load_combinations
                    else [
                        {
                            "id": "SLS-1.0",
                            "label": "Serviceability — all authored actions at 1.0",
                            "limit_state": "serviceability",
                            "factors": {
                                case_id: 1.0 for case_id in self.load_case_categories
                            },
                        }
                    ]
                ),
                "member_loads": self.member_loads,
                "member_distributed_loads": self.member_distributed_loads,
                "stability": self.stability,
            },
        }


def _static_value(node: ast.AST, names: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in names:
            raise StructuralDeclarationError(f"unknown static name {node.id!r}")
        return names[node.id]
    if isinstance(node, ast.List):
        return [_static_value(item, names) for item in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_static_value(item, names) for item in node.elts)
    if isinstance(node, ast.Set):
        return {_static_value(item, names) for item in node.elts}
    if isinstance(node, ast.Dict):
        result = {}
        for key, value in zip(node.keys, node.values, strict=True):
            if key is None:
                raise StructuralDeclarationError(
                    "dictionary unpacking is not supported"
                )
            result[_static_value(key, names)] = _static_value(value, names)
        return result
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        left = _static_value(node.left, names)
        right = _static_value(node.right, names)
        return _BINARY_OPERATORS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
        return _UNARY_OPERATORS[type(node.op)](_static_value(node.operand, names))
    raise StructuralDeclarationError(
        f"unsupported expression {type(node).__name__}; "
        "structural declarations may use literals, static names, and arithmetic only"
    )


def _call_keywords(call: ast.Call) -> dict[str, ast.AST]:
    keywords: dict[str, ast.AST] = {}
    for keyword in call.keywords:
        if keyword.arg is None:
            raise StructuralDeclarationError(
                "structural authoring calls do not support keyword unpacking"
            )
        if keyword.arg in keywords:
            raise StructuralDeclarationError(
                f"structural authoring call repeats keyword {keyword.arg!r}"
            )
        keywords[keyword.arg] = keyword.value
    return keywords


def _keyword_value(
    keywords: dict[str, ast.AST],
    name: str,
    names: dict[str, Any],
    *,
    default: Any = _MISSING,
) -> Any:
    node = keywords.get(name)
    if node is None:
        if default is _MISSING:
            raise StructuralDeclarationError(
                f"structural authoring call requires keyword {name!r}"
            )
        return default
    return _static_value(node, names)


def _component_handle(
    node: ast.AST,
    handles: dict[str, _ComponentHandle],
    *,
    model_name: str,
    context: str,
) -> _ComponentHandle:
    if not isinstance(node, ast.Name) or node.id not in handles:
        name = node.id if isinstance(node, ast.Name) else type(node).__name__
        raise StructuralDeclarationError(
            f"{context} references unregistered structural handle {name!r}"
        )
    handle = handles[node.id]
    if handle.model_name != model_name:
        raise StructuralDeclarationError(
            f"{context} references component {handle.component_id!r} from another model"
        )
    return handle


def _component_handle_list(
    node: ast.AST,
    handles: dict[str, _ComponentHandle],
    *,
    model_name: str,
    context: str,
) -> list[_ComponentHandle]:
    if not isinstance(node, (ast.List, ast.Tuple)):
        raise StructuralDeclarationError(f"{context} must be a literal list of handles")
    return [
        _component_handle(
            item,
            handles,
            model_name=model_name,
            context=context,
        )
        for item in node.elts
    ]


def _direction_value(value: Any) -> dict[str, float]:
    if isinstance(value, dict) and set(value) == {"x", "y", "z"}:
        return {axis: float(value[axis]) for axis in ("x", "y", "z")}
    if (
        isinstance(value, (list, tuple))
        and len(value) == 3
        and not any(isinstance(item, (list, tuple, dict, set)) for item in value)
    ):
        return {"x": float(value[0]), "y": float(value[1]), "z": float(value[2])}
    raise StructuralDeclarationError("structural vector must contain x, y, and z")


def _restraints_value(value: Any) -> dict[str, bool]:
    axes = ("dx", "dy", "dz", "rx", "ry", "rz")
    if isinstance(value, dict) and set(value) == set(axes):
        return {axis: bool(value[axis]) for axis in axes}
    if isinstance(value, (list, tuple)) and len(value) == len(axes):
        return {axis: bool(value[index]) for index, axis in enumerate(axes)}
    if value in ((), []):
        return {axis: False for axis in axes}
    raise StructuralDeclarationError(
        "StructuralModel.member_axis(...) restraints must contain "
        "dx, dy, dz, rx, ry, and rz"
    )


def _spec_handle(
    node: ast.AST,
    handles: dict[str, _SpecHandle],
    *,
    model_name: str,
    context: str,
) -> _SpecHandle:
    if not isinstance(node, ast.Name) or node.id not in handles:
        name = node.id if isinstance(node, ast.Name) else type(node).__name__
        raise StructuralDeclarationError(
            f"{context} references unregistered handle {name!r}"
        )
    handle = handles[node.id]
    if handle.model_name != model_name:
        raise StructuralDeclarationError(
            f"{context} references {handle.id!r} from another model"
        )
    return handle


def _generated_structural_declaration(tree: ast.Module) -> dict[str, Any] | None:
    names: dict[str, Any] = {}
    models: dict[str, _GeneratedModel] = {}
    handles: dict[str, _ComponentHandle] = {}
    material_handles: dict[str, _SpecHandle] = {}
    section_handles: dict[str, _SpecHandle] = {}
    surface_load_handles: dict[str, _SpecHandle] = {}
    legacy_declaration: dict[str, Any] | None = None

    for statement in tree.body:
        target_name: str | None = None
        value_node: ast.AST | None = None
        if (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
        ):
            target_name = statement.targets[0].id
            value_node = statement.value
        elif isinstance(statement, ast.AnnAssign) and isinstance(
            statement.target, ast.Name
        ):
            target_name = statement.target.id
            value_node = statement.value

        if target_name is not None and isinstance(value_node, ast.Call):
            call = value_node
            if isinstance(call.func, ast.Name) and call.func.id == "StructuralModel":
                if call.args:
                    raise StructuralDeclarationError(
                        "StructuralModel(...) accepts the title as a keyword only"
                    )
                keywords = _call_keywords(call)
                unexpected = sorted(set(keywords) - {"title"})
                if unexpected:
                    raise StructuralDeclarationError(
                        f"StructuralModel(...) has unsupported keywords {unexpected}"
                    )
                title = str(_keyword_value(keywords, "title", names))
                models[target_name] = _GeneratedModel(name=target_name, title=title)
                continue

            if (
                isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id in models
            ):
                model_name = call.func.value.id
                model = models[model_name]
                method = call.func.attr
                keywords = _call_keywords(call)
                if method == "design_basis":
                    if call.args:
                        raise StructuralDeclarationError(
                            "StructuralModel.design_basis(...) accepts keywords only"
                        )
                    allowed = {
                        "framework_id",
                        "framework_label",
                        "framework_reference",
                        "jurisdiction",
                        "analysis_method",
                        "standards",
                    }
                    unexpected = sorted(set(keywords) - allowed)
                    if unexpected:
                        raise StructuralDeclarationError(
                            "StructuralModel.design_basis(...) has unsupported "
                            f"keywords {unexpected}"
                        )
                    if model.design_basis is not None:
                        raise StructuralDeclarationError(
                            "StructuralModel.design_basis(...) may only be called once"
                        )
                    standards = _keyword_value(keywords, "standards", names)
                    if not isinstance(standards, dict) or not standards:
                        raise StructuralDeclarationError(
                            "StructuralModel.design_basis(...) requires a non-empty "
                            "standards mapping"
                        )
                    model.design_basis = {
                        "framework_id": str(
                            _keyword_value(keywords, "framework_id", names)
                        ),
                        "framework_label": str(
                            _keyword_value(keywords, "framework_label", names)
                        ),
                        "framework_reference": str(
                            _keyword_value(keywords, "framework_reference", names)
                        ),
                        "jurisdiction": str(
                            _keyword_value(keywords, "jurisdiction", names)
                        ),
                        "analysis_method": str(
                            _keyword_value(keywords, "analysis_method", names)
                        ),
                        "standards": {
                            str(role): str(reference)
                            for role, reference in standards.items()
                        },
                    }
                    continue
                if method == "material":
                    if call.args:
                        raise StructuralDeclarationError(
                            "StructuralModel.material(...) accepts keywords only"
                        )
                    allowed = {
                        "id",
                        "label",
                        "elastic_modulus_kN_m2",
                        "shear_modulus_kN_m2",
                        "poisson_ratio",
                        "density_kg_m3",
                    }
                    unexpected = sorted(set(keywords) - allowed)
                    if unexpected:
                        raise StructuralDeclarationError(
                            "StructuralModel.material(...) has unsupported keywords "
                            f"{unexpected}"
                        )
                    material_id = str(_keyword_value(keywords, "id", names))
                    model.materials.append(
                        {
                            "id": material_id,
                            "label": str(_keyword_value(keywords, "label", names)),
                            "elastic_modulus_kN_m2": float(
                                _keyword_value(
                                    keywords,
                                    "elastic_modulus_kN_m2",
                                    names,
                                )
                            ),
                            "shear_modulus_kN_m2": float(
                                _keyword_value(
                                    keywords,
                                    "shear_modulus_kN_m2",
                                    names,
                                )
                            ),
                            "poisson_ratio": float(
                                _keyword_value(keywords, "poisson_ratio", names)
                            ),
                            "density_kg_m3": float(
                                _keyword_value(keywords, "density_kg_m3", names)
                            ),
                        }
                    )
                    material_handles[target_name] = _SpecHandle(
                        model_name=model_name,
                        id=material_id,
                    )
                    continue
                if method == "section":
                    if call.args:
                        raise StructuralDeclarationError(
                            "StructuralModel.section(...) accepts keywords only"
                        )
                    allowed = {
                        "id",
                        "label",
                        "area_m2",
                        "iy_m4",
                        "iz_m4",
                        "torsion_j_m4",
                        "mass_kg_m",
                        "bending_reference_kNm",
                        "bending_reference_axis",
                        "bending_reference_basis",
                    }
                    unexpected = sorted(set(keywords) - allowed)
                    if unexpected:
                        raise StructuralDeclarationError(
                            "StructuralModel.section(...) has unsupported keywords "
                            f"{unexpected}"
                        )
                    section_id = str(_keyword_value(keywords, "id", names))
                    section_value = {
                        "id": section_id,
                        "label": str(_keyword_value(keywords, "label", names)),
                        "area_m2": float(_keyword_value(keywords, "area_m2", names)),
                        "iy_m4": float(_keyword_value(keywords, "iy_m4", names)),
                        "iz_m4": float(_keyword_value(keywords, "iz_m4", names)),
                        "torsion_j_m4": float(
                            _keyword_value(keywords, "torsion_j_m4", names)
                        ),
                    }
                    mass_kg_m = _keyword_value(
                        keywords,
                        "mass_kg_m",
                        names,
                        default=None,
                    )
                    if mass_kg_m is not None:
                        section_value["mass_kg_m"] = float(mass_kg_m)
                    bending_reference = _keyword_value(
                        keywords,
                        "bending_reference_kNm",
                        names,
                        default=None,
                    )
                    bending_reference_basis = _keyword_value(
                        keywords,
                        "bending_reference_basis",
                        names,
                        default=None,
                    )
                    bending_reference_axis = _keyword_value(
                        keywords,
                        "bending_reference_axis",
                        names,
                        default=None,
                    )
                    if bending_reference is not None:
                        section_value["bending_reference_kNm"] = float(
                            bending_reference
                        )
                    if bending_reference_axis is not None:
                        section_value["bending_reference_axis"] = str(
                            bending_reference_axis
                        )
                    if bending_reference_basis is not None:
                        section_value["bending_reference_basis"] = str(
                            bending_reference_basis
                        )
                    model.sections.append(section_value)
                    section_handles[target_name] = _SpecHandle(
                        model_name=model_name,
                        id=section_id,
                    )
                    continue
                if method == "surface_load":
                    if len(call.args) != 1:
                        raise StructuralDeclarationError(
                            "StructuralModel.surface_load(...) requires one surface handle"
                        )
                    allowed = {
                        "id",
                        "label",
                        "case",
                        "case_id",
                        "case_label",
                        "pressure_kPa",
                        "area_m2",
                        "direction",
                        "provenance",
                    }
                    unexpected = sorted(set(keywords) - allowed)
                    if unexpected:
                        raise StructuralDeclarationError(
                            "StructuralModel.surface_load(...) has unsupported keywords "
                            f"{unexpected}"
                        )
                    loaded_component = _component_handle(
                        call.args[0],
                        handles,
                        model_name=model_name,
                        context="StructuralModel.surface_load(...)",
                    )
                    load_id = str(_keyword_value(keywords, "id", names))
                    case = str(_keyword_value(keywords, "case", names))
                    case_id = _load_case_id(
                        str(
                            _keyword_value(
                                keywords,
                                "case_id",
                                names,
                                default=case,
                            )
                        )
                    )
                    case_label = str(
                        _keyword_value(
                            keywords,
                            "case_label",
                            names,
                            default=f"{case.title()} load",
                        )
                    )
                    model.loads.append(
                        {
                            "id": load_id,
                            "label": str(_keyword_value(keywords, "label", names)),
                            "case": case,
                            "case_id": case_id,
                            "component_id": loaded_component.component_id,
                            "pressure_kPa": float(
                                _keyword_value(keywords, "pressure_kPa", names)
                            ),
                            "area_m2": float(
                                _keyword_value(keywords, "area_m2", names)
                            ),
                            "direction": _direction_value(
                                _keyword_value(keywords, "direction", names)
                            ),
                            "provenance": str(
                                _keyword_value(keywords, "provenance", names)
                            ),
                        }
                    )
                    model.load_case_categories[case_id] = case
                    model.load_case_labels[case_id] = case_label
                    surface_load_handles[target_name] = _SpecHandle(
                        model_name=model_name,
                        id=load_id,
                    )
                    continue
                if method in _COMPONENT_METHOD_KINDS:
                    if len(call.args) != 1:
                        raise StructuralDeclarationError(
                            f"StructuralModel.{method}(...) requires one Build123D shape"
                        )
                    allowed = {"id", "label", "visual_node_id", "part_number"}
                    unexpected = sorted(set(keywords) - allowed)
                    if unexpected:
                        raise StructuralDeclarationError(
                            f"StructuralModel.{method}(...) has unsupported keywords "
                            f"{unexpected}"
                        )
                    component_id = str(_keyword_value(keywords, "id", names))
                    visual_node_id = str(
                        _keyword_value(
                            keywords,
                            "visual_node_id",
                            names,
                            default=component_id,
                        )
                    )
                    component: dict[str, Any] = {
                        "id": component_id,
                        "label": str(_keyword_value(keywords, "label", names)),
                        "kind": _COMPONENT_METHOD_KINDS[method],
                        "visual_node_id": visual_node_id,
                        "grounded": method == "ground",
                    }
                    part_number = _keyword_value(
                        keywords,
                        "part_number",
                        names,
                        default=None,
                    )
                    if part_number is not None:
                        component["part_number"] = str(part_number)
                    model.components.append(component)
                    handles[target_name] = _ComponentHandle(
                        model_name=model_name,
                        component_id=component_id,
                    )
                    continue
                if method == "assembly":
                    if len(call.args) != 1:
                        raise StructuralDeclarationError(
                            "StructuralModel.assembly(...) requires one list of handles"
                        )
                    unexpected = sorted(set(keywords) - {"label"})
                    if unexpected:
                        raise StructuralDeclarationError(
                            "StructuralModel.assembly(...) has unsupported keywords "
                            f"{unexpected}"
                        )
                    _keyword_value(keywords, "label", names)
                    model.assembled_component_ids = [
                        handle.component_id
                        for handle in _component_handle_list(
                            call.args[0],
                            handles,
                            model_name=model_name,
                            context="StructuralModel.assembly(...)",
                        )
                    ]
                    continue
                if method == "manifest" and target_name == DECLARATION_NAME:
                    if call.args or call.keywords:
                        raise StructuralDeclarationError(
                            "StructuralModel.manifest(...) does not accept arguments"
                        )
                    return model.declaration()

        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
            call = statement.value
            if (
                isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id in models
            ):
                model_name = call.func.value.id
                model = models[model_name]
                method = call.func.attr
                keywords = _call_keywords(call)
                if method == "design_basis":
                    if call.args:
                        raise StructuralDeclarationError(
                            "StructuralModel.design_basis(...) accepts keywords only"
                        )
                    allowed = {
                        "framework_id",
                        "framework_label",
                        "framework_reference",
                        "jurisdiction",
                        "analysis_method",
                        "standards",
                    }
                    unexpected = sorted(set(keywords) - allowed)
                    if unexpected:
                        raise StructuralDeclarationError(
                            "StructuralModel.design_basis(...) has unsupported "
                            f"keywords {unexpected}"
                        )
                    if model.design_basis is not None:
                        raise StructuralDeclarationError(
                            "StructuralModel.design_basis(...) may only be called once"
                        )
                    standards = _keyword_value(keywords, "standards", names)
                    if not isinstance(standards, dict) or not standards:
                        raise StructuralDeclarationError(
                            "StructuralModel.design_basis(...) requires a non-empty "
                            "standards mapping"
                        )
                    model.design_basis = {
                        "framework_id": str(
                            _keyword_value(keywords, "framework_id", names)
                        ),
                        "framework_label": str(
                            _keyword_value(keywords, "framework_label", names)
                        ),
                        "framework_reference": str(
                            _keyword_value(keywords, "framework_reference", names)
                        ),
                        "jurisdiction": str(
                            _keyword_value(keywords, "jurisdiction", names)
                        ),
                        "analysis_method": str(
                            _keyword_value(keywords, "analysis_method", names)
                        ),
                        "standards": {
                            str(role): str(reference)
                            for role, reference in standards.items()
                        },
                    }
                    continue
                if method == "stability":
                    if call.args:
                        raise StructuralDeclarationError(
                            "StructuralModel.stability(...) accepts keywords only"
                        )
                    allowed = {
                        "method",
                        "stability_combination_id",
                        "imperfection_case_id",
                        "imperfection_basis",
                        "base_stiffness_basis",
                        "base_stiffness_status",
                        "amplification_warning_ratio",
                    }
                    unexpected = sorted(set(keywords) - allowed)
                    if unexpected:
                        raise StructuralDeclarationError(
                            "StructuralModel.stability(...) has unsupported "
                            f"keywords {unexpected}"
                        )
                    if model.stability is not None:
                        raise StructuralDeclarationError(
                            "StructuralModel.stability(...) may only be called once"
                        )
                    model.stability = {
                        "method": str(_keyword_value(keywords, "method", names)),
                        "stability_combination_id": str(
                            _keyword_value(keywords, "stability_combination_id", names)
                        ),
                        "imperfection_case_id": _load_case_id(
                            str(_keyword_value(keywords, "imperfection_case_id", names))
                        ),
                        "imperfection_basis": str(
                            _keyword_value(keywords, "imperfection_basis", names)
                        ),
                        "base_stiffness_basis": str(
                            _keyword_value(keywords, "base_stiffness_basis", names)
                        ),
                        "base_stiffness_status": str(
                            _keyword_value(keywords, "base_stiffness_status", names)
                        ),
                        "amplification_warning_ratio": float(
                            _keyword_value(
                                keywords,
                                "amplification_warning_ratio",
                                names,
                                default=1.1,
                            )
                        ),
                    }
                    continue
                if method == "connect":
                    if len(call.args) != 2:
                        raise StructuralDeclarationError(
                            "StructuralModel.connect(...) requires source and target handles"
                        )
                    allowed = {"via", "id", "label", "transfers"}
                    unexpected = sorted(set(keywords) - allowed)
                    if unexpected:
                        raise StructuralDeclarationError(
                            f"StructuralModel.connect(...) has unsupported keywords "
                            f"{unexpected}"
                        )
                    source = _component_handle(
                        call.args[0],
                        handles,
                        model_name=model_name,
                        context="StructuralModel.connect(...) source",
                    )
                    target = _component_handle(
                        call.args[1],
                        handles,
                        model_name=model_name,
                        context="StructuralModel.connect(...) target",
                    )
                    connector_node = keywords.get("via")
                    connectors = (
                        []
                        if connector_node is None
                        else _component_handle_list(
                            connector_node,
                            handles,
                            model_name=model_name,
                            context="StructuralModel.connect(...) via",
                        )
                    )
                    model.connections.append(
                        {
                            "id": str(_keyword_value(keywords, "id", names)),
                            "label": str(_keyword_value(keywords, "label", names)),
                            "from_component_id": source.component_id,
                            "to_component_id": target.component_id,
                            "connector_component_ids": [
                                connector.component_id for connector in connectors
                            ],
                            "transfers": list(
                                _keyword_value(keywords, "transfers", names)
                            ),
                        }
                    )
                    continue
                if method == "surface_load":
                    if len(call.args) != 1:
                        raise StructuralDeclarationError(
                            "StructuralModel.surface_load(...) requires one surface handle"
                        )
                    allowed = {
                        "id",
                        "label",
                        "case",
                        "case_id",
                        "case_label",
                        "pressure_kPa",
                        "area_m2",
                        "direction",
                        "provenance",
                    }
                    unexpected = sorted(set(keywords) - allowed)
                    if unexpected:
                        raise StructuralDeclarationError(
                            "StructuralModel.surface_load(...) has unsupported keywords "
                            f"{unexpected}"
                        )
                    loaded_component = _component_handle(
                        call.args[0],
                        handles,
                        model_name=model_name,
                        context="StructuralModel.surface_load(...)",
                    )
                    case = str(_keyword_value(keywords, "case", names))
                    case_id = _load_case_id(
                        str(
                            _keyword_value(
                                keywords,
                                "case_id",
                                names,
                                default=case,
                            )
                        )
                    )
                    case_label = str(
                        _keyword_value(
                            keywords,
                            "case_label",
                            names,
                            default=f"{case.title()} load",
                        )
                    )
                    model.loads.append(
                        {
                            "id": str(_keyword_value(keywords, "id", names)),
                            "label": str(_keyword_value(keywords, "label", names)),
                            "case": case,
                            "case_id": case_id,
                            "component_id": loaded_component.component_id,
                            "pressure_kPa": float(
                                _keyword_value(keywords, "pressure_kPa", names)
                            ),
                            "area_m2": float(
                                _keyword_value(keywords, "area_m2", names)
                            ),
                            "direction": _direction_value(
                                _keyword_value(keywords, "direction", names)
                            ),
                            "provenance": str(
                                _keyword_value(keywords, "provenance", names)
                            ),
                        }
                    )
                    model.load_case_categories[case_id] = case
                    model.load_case_labels[case_id] = case_label
                    continue
                if method == "member_axis":
                    if len(call.args) != 1:
                        raise StructuralDeclarationError(
                            "StructuralModel.member_axis(...) requires one member handle"
                        )
                    allowed = {
                        "id",
                        "label",
                        "start",
                        "end",
                        "section",
                        "material",
                        "start_restraints",
                        "end_restraints",
                        "rotation_deg",
                        "start_releases",
                        "end_releases",
                        "deflection_limit_ratio",
                        "deflection_limit_mm",
                        "deflection_limit_basis",
                        "assumption",
                    }
                    unexpected = sorted(set(keywords) - allowed)
                    if unexpected:
                        raise StructuralDeclarationError(
                            "StructuralModel.member_axis(...) has unsupported keywords "
                            f"{unexpected}"
                        )
                    axis_component = _component_handle(
                        call.args[0],
                        handles,
                        model_name=model_name,
                        context="StructuralModel.member_axis(...)",
                    )
                    section_node = keywords.get("section")
                    material_node = keywords.get("material")
                    if section_node is None or material_node is None:
                        raise StructuralDeclarationError(
                            "StructuralModel.member_axis(...) requires section and material"
                        )
                    axis_section = _spec_handle(
                        section_node,
                        section_handles,
                        model_name=model_name,
                        context="StructuralModel.member_axis(...) section",
                    )
                    axis_material = _spec_handle(
                        material_node,
                        material_handles,
                        model_name=model_name,
                        context="StructuralModel.member_axis(...) material",
                    )
                    model.analytical_members.append(
                        {
                            "id": str(_keyword_value(keywords, "id", names)),
                            "label": str(_keyword_value(keywords, "label", names)),
                            "component_id": axis_component.component_id,
                            "start": _direction_value(
                                _keyword_value(keywords, "start", names)
                            ),
                            "end": _direction_value(
                                _keyword_value(keywords, "end", names)
                            ),
                            "start_restraints": _restraints_value(
                                _keyword_value(
                                    keywords,
                                    "start_restraints",
                                    names,
                                    default=(),
                                )
                            ),
                            "end_restraints": _restraints_value(
                                _keyword_value(
                                    keywords,
                                    "end_restraints",
                                    names,
                                    default=(),
                                )
                            ),
                            "rotation_deg": float(
                                _keyword_value(
                                    keywords,
                                    "rotation_deg",
                                    names,
                                    default=0.0,
                                )
                            ),
                            "start_releases": _restraints_value(
                                _keyword_value(
                                    keywords,
                                    "start_releases",
                                    names,
                                    default=(),
                                )
                            ),
                            "end_releases": _restraints_value(
                                _keyword_value(
                                    keywords,
                                    "end_releases",
                                    names,
                                    default=(),
                                )
                            ),
                            "deflection_limit_ratio": _keyword_value(
                                keywords,
                                "deflection_limit_ratio",
                                names,
                                default=None,
                            ),
                            "deflection_limit_mm": _keyword_value(
                                keywords,
                                "deflection_limit_mm",
                                names,
                                default=None,
                            ),
                            "deflection_limit_basis": _keyword_value(
                                keywords,
                                "deflection_limit_basis",
                                names,
                                default=None,
                            ),
                            "section_id": axis_section.id,
                            "material_id": axis_material.id,
                            "assumption": str(
                                _keyword_value(keywords, "assumption", names)
                            ),
                        }
                    )
                    continue
                if method == "distribute_surface_load":
                    if len(call.args) != 2:
                        raise StructuralDeclarationError(
                            "StructuralModel.distribute_surface_load(...) requires "
                            "surface-load and member handles"
                        )
                    allowed = {
                        "id",
                        "label",
                        "positions_m",
                        "weights",
                        "provenance",
                    }
                    unexpected = sorted(set(keywords) - allowed)
                    if unexpected:
                        raise StructuralDeclarationError(
                            "StructuralModel.distribute_surface_load(...) has "
                            f"unsupported keywords {unexpected}"
                        )
                    distributed_source = _spec_handle(
                        call.args[0],
                        surface_load_handles,
                        model_name=model_name,
                        context="StructuralModel.distribute_surface_load(...) load",
                    )
                    distributed_component = _component_handle(
                        call.args[1],
                        handles,
                        model_name=model_name,
                        context="StructuralModel.distribute_surface_load(...) member",
                    )
                    analytical_member = next(
                        (
                            member
                            for member in model.analytical_members
                            if member["component_id"]
                            == distributed_component.component_id
                        ),
                        None,
                    )
                    if analytical_member is None:
                        raise StructuralDeclarationError(
                            "StructuralModel.distribute_surface_load(...) member "
                            "has no analytical axis"
                        )
                    source_load = next(
                        load
                        for load in model.loads
                        if load["id"] == distributed_source.id
                    )
                    positions = [
                        float(value)
                        for value in _keyword_value(keywords, "positions_m", names)
                    ]
                    weights = [
                        float(value)
                        for value in _keyword_value(
                            keywords,
                            "weights",
                            names,
                            default=[1.0] * len(positions),
                        )
                    ]
                    if len(weights) != len(positions) or not positions:
                        raise StructuralDeclarationError(
                            "StructuralModel.distribute_surface_load(...) positions "
                            "and weights must have equal non-zero length"
                        )
                    if len(positions) != len(set(positions)) or any(
                        position <= 0 for position in positions
                    ):
                        raise StructuralDeclarationError(
                            "StructuralModel.distribute_surface_load(...) positions "
                            "must be unique and positive"
                        )
                    member_start = analytical_member["start"]
                    member_end = analytical_member["end"]
                    member_length = (
                        sum(
                            (member_end[axis] - member_start[axis]) ** 2
                            for axis in ("x", "y", "z")
                        )
                        ** 0.5
                    )
                    if any(position >= member_length for position in positions):
                        raise StructuralDeclarationError(
                            "StructuralModel.distribute_surface_load(...) positions "
                            "must lie within the analytical member"
                        )
                    if any(weight <= 0 for weight in weights):
                        raise StructuralDeclarationError(
                            "StructuralModel.distribute_surface_load(...) weights "
                            "must be positive"
                        )
                    direction = source_load["direction"]
                    direction_length = (
                        sum(direction[axis] ** 2 for axis in ("x", "y", "z")) ** 0.5
                    )
                    if direction_length == 0:
                        raise StructuralDeclarationError(
                            "StructuralModel.distribute_surface_load(...) source "
                            "load has a zero direction"
                        )
                    total_force = source_load["pressure_kPa"] * source_load["area_m2"]
                    weight_total = sum(weights)
                    distribution_id = str(_keyword_value(keywords, "id", names))
                    distribution_label = str(_keyword_value(keywords, "label", names))
                    provenance = str(_keyword_value(keywords, "provenance", names))
                    for index, (position, weight) in enumerate(
                        zip(positions, weights, strict=True),
                        start=1,
                    ):
                        scale = total_force * weight / weight_total / direction_length
                        model.member_loads.append(
                            {
                                "id": f"{distribution_id}-{index}",
                                "label": f"{distribution_label} {index}",
                                "member_id": analytical_member["id"],
                                "case_id": source_load.get(
                                    "case_id",
                                    _load_case_id(source_load["case"]),
                                ),
                                "distance_m": position,
                                "force": {
                                    axis: direction[axis] * scale
                                    for axis in ("x", "y", "z")
                                },
                                "moment": {"x": 0.0, "y": 0.0, "z": 0.0},
                                "source_load_id": distributed_source.id,
                                "provenance": provenance,
                            }
                        )
                    continue
                if method == "member_point_load":
                    if len(call.args) != 1:
                        raise StructuralDeclarationError(
                            "StructuralModel.member_point_load(...) requires one member handle"
                        )
                    allowed = {
                        "id",
                        "label",
                        "case",
                        "distance_m",
                        "force",
                        "moment",
                        "case_id",
                        "case_label",
                        "provenance",
                    }
                    unexpected = sorted(set(keywords) - allowed)
                    if unexpected:
                        raise StructuralDeclarationError(
                            "StructuralModel.member_point_load(...) has unsupported "
                            f"keywords {unexpected}"
                        )
                    loaded_component = _component_handle(
                        call.args[0],
                        handles,
                        model_name=model_name,
                        context="StructuralModel.member_point_load(...) member",
                    )
                    analytical_member = next(
                        (
                            member
                            for member in model.analytical_members
                            if member["component_id"] == loaded_component.component_id
                        ),
                        None,
                    )
                    if analytical_member is None:
                        raise StructuralDeclarationError(
                            "StructuralModel.member_point_load(...) member has no "
                            "analytical axis"
                        )
                    case = str(_keyword_value(keywords, "case", names))
                    case_id_value = _keyword_value(
                        keywords, "case_id", names, default=case
                    )
                    resolved_case_id = _load_case_id(str(case_id_value))
                    case_label_value = _keyword_value(
                        keywords,
                        "case_label",
                        names,
                        default=f"{case.title()} load",
                    )
                    model.load_case_categories[resolved_case_id] = case
                    model.load_case_labels[resolved_case_id] = str(case_label_value)
                    model.member_loads.append(
                        {
                            "id": str(_keyword_value(keywords, "id", names)),
                            "label": str(_keyword_value(keywords, "label", names)),
                            "member_id": analytical_member["id"],
                            "case_id": resolved_case_id,
                            "distance_m": float(
                                _keyword_value(keywords, "distance_m", names)
                            ),
                            "force": _direction_value(
                                _keyword_value(keywords, "force", names)
                            ),
                            "moment": _direction_value(
                                _keyword_value(
                                    keywords,
                                    "moment",
                                    names,
                                    default=(0.0, 0.0, 0.0),
                                )
                            ),
                            "source_load_id": None,
                            "provenance": str(
                                _keyword_value(keywords, "provenance", names)
                            ),
                        }
                    )
                    continue
                if method in {"member_distributed_load", "member_self_weight"}:
                    if len(call.args) != 1:
                        raise StructuralDeclarationError(
                            f"StructuralModel.{method}(...) requires one member handle"
                        )
                    distributed_component = _component_handle(
                        call.args[0],
                        handles,
                        model_name=model_name,
                        context=f"StructuralModel.{method}(...) member",
                    )
                    analytical_member = next(
                        (
                            member
                            for member in model.analytical_members
                            if member["component_id"]
                            == distributed_component.component_id
                        ),
                        None,
                    )
                    if analytical_member is None:
                        raise StructuralDeclarationError(
                            f"StructuralModel.{method}(...) member has no analytical axis"
                        )
                    member_start = analytical_member["start"]
                    member_end = analytical_member["end"]
                    member_length = (
                        sum(
                            (member_end[axis] - member_start[axis]) ** 2
                            for axis in ("x", "y", "z")
                        )
                        ** 0.5
                    )
                    load_id = str(_keyword_value(keywords, "id", names))
                    label = str(_keyword_value(keywords, "label", names))
                    provenance = str(
                        _keyword_value(
                            keywords,
                            "provenance",
                            names,
                            default=(
                                "Section mass per metre multiplied by standard gravity."
                            ),
                        )
                    )
                    if method == "member_self_weight":
                        allowed = {
                            "id",
                            "label",
                            "direction",
                            "gravity_m_s2",
                            "provenance",
                        }
                        unexpected = sorted(set(keywords) - allowed)
                        if unexpected:
                            raise StructuralDeclarationError(
                                "StructuralModel.member_self_weight(...) has "
                                f"unsupported keywords {unexpected}"
                            )
                        section = next(
                            section
                            for section in model.sections
                            if section["id"] == analytical_member["section_id"]
                        )
                        mass_kg_m = section.get("mass_kg_m")
                        if mass_kg_m is None:
                            raise StructuralDeclarationError(
                                "StructuralModel.member_self_weight(...) section "
                                "has no mass_kg_m"
                            )
                        direction = _direction_value(
                            _keyword_value(
                                keywords,
                                "direction",
                                names,
                                default=(0.0, 0.0, -1.0),
                            )
                        )
                        direction_length = (
                            sum(direction[axis] ** 2 for axis in ("x", "y", "z")) ** 0.5
                        )
                        gravity = float(
                            _keyword_value(
                                keywords,
                                "gravity_m_s2",
                                names,
                                default=9.80665,
                            )
                        )
                        magnitude = float(mass_kg_m) * gravity / 1000.0
                        start_force = {
                            axis: direction[axis] * magnitude / direction_length
                            for axis in ("x", "y", "z")
                        }
                        end_force = dict(start_force)
                        case = "dead"
                        source_kind = "self_weight"
                        source_load_id = None
                        start_distance = 0.0
                        end_distance = member_length
                    else:
                        allowed = {
                            "id",
                            "label",
                            "case",
                            "start_force_kN_m",
                            "end_force_kN_m",
                            "start_distance_m",
                            "end_distance_m",
                            "source_kind",
                            "source_load",
                            "provenance",
                        }
                        unexpected = sorted(set(keywords) - allowed)
                        if unexpected:
                            raise StructuralDeclarationError(
                                "StructuralModel.member_distributed_load(...) has "
                                f"unsupported keywords {unexpected}"
                            )
                        case = str(_keyword_value(keywords, "case", names))
                        start_force = _direction_value(
                            _keyword_value(
                                keywords,
                                "start_force_kN_m",
                                names,
                            )
                        )
                        end_force = _direction_value(
                            _keyword_value(
                                keywords,
                                "end_force_kN_m",
                                names,
                                default=start_force,
                            )
                        )
                        start_distance = float(
                            _keyword_value(
                                keywords,
                                "start_distance_m",
                                names,
                                default=0.0,
                            )
                        )
                        end_distance_value = _keyword_value(
                            keywords,
                            "end_distance_m",
                            names,
                            default=None,
                        )
                        end_distance = (
                            member_length
                            if end_distance_value is None
                            else float(end_distance_value)
                        )
                        source_kind = str(
                            _keyword_value(
                                keywords,
                                "source_kind",
                                names,
                                default="authored",
                            )
                        )
                        source_load_node = keywords.get("source_load")
                        source_load_id = (
                            None
                            if source_load_node is None
                            else _spec_handle(
                                source_load_node,
                                surface_load_handles,
                                model_name=model_name,
                                context=(
                                    "StructuralModel.member_distributed_load(...) "
                                    "source_load"
                                ),
                            ).id
                        )
                    resolved_case_id = _load_case_id(case)
                    model.load_case_categories[resolved_case_id] = case
                    model.load_case_labels.setdefault(
                        resolved_case_id,
                        f"{case.title()} load",
                    )
                    model.member_distributed_loads.append(
                        {
                            "id": load_id,
                            "label": label,
                            "member_id": analytical_member["id"],
                            "case_id": resolved_case_id,
                            "start_distance_m": start_distance,
                            "end_distance_m": end_distance,
                            "start_force_kN_m": start_force,
                            "end_force_kN_m": end_force,
                            "source_kind": source_kind,
                            "source_load_id": source_load_id,
                            "provenance": provenance,
                        }
                    )
                    continue
                if method == "load_combination":
                    if call.args:
                        raise StructuralDeclarationError(
                            "StructuralModel.load_combination(...) accepts keywords only"
                        )
                    allowed = {"id", "label", "limit_state", "factors"}
                    unexpected = sorted(set(keywords) - allowed)
                    if unexpected:
                        raise StructuralDeclarationError(
                            "StructuralModel.load_combination(...) has unsupported "
                            f"keywords {unexpected}"
                        )
                    raw_factors = dict(_keyword_value(keywords, "factors", names))
                    factors = {
                        (
                            str(case_id)
                            if str(case_id).startswith("case-")
                            else f"case-{case_id}"
                        ): float(factor)
                        for case_id, factor in raw_factors.items()
                        if float(factor) != 0
                    }
                    model.load_combinations.append(
                        {
                            "id": str(_keyword_value(keywords, "id", names)),
                            "label": str(_keyword_value(keywords, "label", names)),
                            "limit_state": str(
                                _keyword_value(keywords, "limit_state", names)
                            ),
                            "factors": factors,
                        }
                    )
                    continue

        if target_name is None or value_node is None:
            continue
        try:
            value = _static_value(value_node, names)
        except StructuralDeclarationError, ArithmeticError, TypeError, ValueError:
            if target_name == DECLARATION_NAME:
                raise
            continue
        names[target_name] = value
        if target_name == DECLARATION_NAME:
            if not isinstance(value, dict):
                raise StructuralDeclarationError(
                    f"{DECLARATION_NAME} must be a dictionary"
                )
            legacy_declaration = value

    return legacy_declaration


def _structural_declaration(source: str) -> dict[str, Any]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise StructuralDeclarationError(
            f"design.py is not valid Python: {exc.msg}"
        ) from exc

    declaration = _generated_structural_declaration(tree)
    if declaration is None:
        raise StructuralDeclarationError(
            f"design.py does not declare {DECLARATION_NAME}"
        )
    if not isinstance(declaration, dict):
        raise StructuralDeclarationError(f"{DECLARATION_NAME} must be a dictionary")
    return declaration


def _trace_load_paths(
    components: list[DesignComponent],
    connections: list[DesignConnection],
    loads: list[DesignSurfaceLoad],
) -> list[DesignLoadPath]:
    components_by_id = {component.id: component for component in components}
    outgoing: dict[str, list[DesignConnection]] = {}
    for connection in connections:
        outgoing.setdefault(connection.from_component_id, []).append(connection)
    for candidates in outgoing.values():
        candidates.sort(key=lambda connection: connection.id)

    paths: list[DesignLoadPath] = []
    for load in loads:
        queue: deque[tuple[str, list[str], list[str]]] = deque(
            [(load.component_id, [load.component_id], [])]
        )
        visited = {load.component_id}
        complete: DesignLoadPath | None = None
        while queue:
            component_id, component_path, connection_path = queue.popleft()
            component = components_by_id[component_id]
            if component.grounded:
                complete = DesignLoadPath(
                    load_id=load.id,
                    status="complete",
                    component_ids=component_path,
                    connection_ids=connection_path,
                    grounded_component_id=component_id,
                    detail=f"Load reaches grounded component {component.label}.",
                )
                break
            for connection in outgoing.get(component_id, []):
                next_id = connection.to_component_id
                if next_id in visited:
                    continue
                visited.add(next_id)
                queue.append(
                    (
                        next_id,
                        [*component_path, next_id],
                        [*connection_path, connection.id],
                    )
                )
        paths.append(
            complete
            or DesignLoadPath(
                load_id=load.id,
                status="blocked",
                component_ids=[load.component_id],
                connection_ids=[],
                detail="No declared connection path reaches a grounded component.",
            )
        )
    return paths


def _validate_graph_inputs(
    components: list[DesignComponent],
    connections: list[DesignConnection],
    loads: list[DesignSurfaceLoad],
) -> None:
    if not components:
        raise StructuralDeclarationError("structural capture contains no components")
    component_ids = [component.id for component in components]
    if len(component_ids) != len(set(component_ids)):
        raise StructuralDeclarationError("components contain duplicate IDs")
    visual_node_ids = [component.visual_node_id for component in components]
    if len(visual_node_ids) != len(set(visual_node_ids)):
        raise StructuralDeclarationError("components contain duplicate visual node IDs")
    connection_ids = [connection.id for connection in connections]
    if len(connection_ids) != len(set(connection_ids)):
        raise StructuralDeclarationError("connections contain duplicate IDs")
    load_ids = [load.id for load in loads]
    if len(load_ids) != len(set(load_ids)):
        raise StructuralDeclarationError("loads contain duplicate IDs")

    valid_ids = set(component_ids)
    components_by_id = {component.id: component for component in components}
    connected_ids: set[str] = set()
    used_connector_ids: set[str] = set()
    for connection in connections:
        if connection.from_component_id not in valid_ids:
            raise StructuralDeclarationError(
                "connection source component references missing ID "
                f"{connection.from_component_id!r}"
            )
        if connection.to_component_id not in valid_ids:
            raise StructuralDeclarationError(
                "connection target component references missing ID "
                f"{connection.to_component_id!r}"
            )
        if connection.from_component_id == connection.to_component_id:
            raise StructuralDeclarationError(
                f"connection {connection.id!r} connects a component to itself"
            )
        connected_ids.update({connection.from_component_id, connection.to_component_id})
        for connector_id in connection.connector_component_ids:
            if connector_id not in valid_ids:
                raise StructuralDeclarationError(
                    "connection connector component references missing ID "
                    f"{connector_id!r}"
                )
            if components_by_id[connector_id].kind != "connector":
                raise StructuralDeclarationError(
                    f"connection {connection.id!r} via component "
                    f"{connector_id!r} is not a connector"
                )
            used_connector_ids.add(connector_id)

    unconnected = sorted(
        component.id
        for component in components
        if component.kind not in {"ground", "connector"}
        and component.id not in connected_ids
    )
    if unconnected:
        raise StructuralDeclarationError(
            f"structural components have no declared connection: {unconnected}"
        )
    unused_connectors = sorted(
        component.id
        for component in components
        if component.kind == "connector" and component.id not in used_connector_ids
    )
    if unused_connectors:
        raise StructuralDeclarationError(
            f"connector components are not used by a connection: {unused_connectors}"
        )

    for load in loads:
        if load.component_id not in valid_ids:
            raise StructuralDeclarationError(
                f"load component references missing ID {load.component_id!r}"
            )
        if components_by_id[load.component_id].kind != "surface":
            raise StructuralDeclarationError(
                f"load component {load.component_id!r} is not a surface"
            )


def capture_project_structural_declaration(
    declaration: dict[str, Any],
    *,
    project_name: str,
    design_hash: str,
    capture_detail: str | None = None,
) -> ProjectStructuralCapture:
    try:
        components = [
            DesignComponent.model_validate(value)
            for value in declaration.get("components", [])
        ]
        connections = [
            DesignConnection.model_validate(value)
            for value in declaration.get("connections", [])
        ]
        loads = [
            DesignSurfaceLoad.model_validate(value)
            for value in declaration.get("loads", [])
        ]
        analysis_value = declaration.get("analysis")
        design_basis_value = declaration.get("design_basis")
        design_basis = (
            StructuralDesignBasis.model_validate(design_basis_value)
            if design_basis_value is not None
            else None
        )
        analysis = (
            DesignAnalysisDefinition.model_validate(analysis_value)
            if analysis_value is not None
            else None
        )
    except (TypeError, ValidationError) as exc:
        raise StructuralDeclarationError(f"invalid {DECLARATION_NAME}: {exc}") from exc

    _validate_graph_inputs(components, connections, loads)
    paths = _trace_load_paths(components, connections, loads)
    blocked_count = sum(path.status == "blocked" for path in paths)
    generated_authoring = (
        isinstance(declaration.get("authoring"), dict)
        and declaration["authoring"].get("mode") == "generated"
    )
    solver_ready = bool(
        analysis is not None
        and analysis.members
        and (analysis.member_loads or analysis.member_distributed_loads)
        and analysis.sections
        and analysis.materials
    )
    capabilities = [
        CapabilityState(
            id="design-capture",
            label="Design capture",
            status="online",
            detail=(
                capture_detail
                or (
                    "Generated structural authoring calls parsed without executing design.py."
                    if generated_authoring
                    else "Static structural declarations parsed without executing design.py."
                )
            ),
        ),
        CapabilityState(
            id="load-path",
            label="Load path",
            status="online" if paths and blocked_count == 0 else "blocked",
            detail=(
                f"{len(paths)} declared load path(s) reach ground."
                if paths and blocked_count == 0
                else f"{blocked_count or len(loads)} load path(s) do not reach ground."
            ),
        ),
        CapabilityState(
            id="solver",
            label="PyNite demand",
            status="online" if solver_ready else "pending",
            detail=(
                "Analytical axes, load combinations, and member loads are ready "
                "for PyNite."
                if solver_ready
                else "Connectivity captured; analytical axes and force distribution "
                "are not declared yet."
            ),
        ),
        CapabilityState(
            id="reports",
            label="Calculation reports",
            status="pending",
            detail="No calculation report artifact has been generated.",
        ),
    ]
    warnings = [
        "LOAD PATH CAPTURE ONLY — MEMBER, CONNECTION, ANCHOR, AND CONCRETE CAPACITIES ARE NOT CHECKED."
    ]
    if generated_authoring:
        warnings.append(
            "STRUCTURAL MANIFEST GENERATED FROM REGISTERED BUILD123D HANDLES; "
            "UNREGISTERED ASSEMBLY MEMBERS FAIL CAPTURE AND COMPILE."
        )
    if blocked_count:
        warnings.append(
            f"{blocked_count} declared load path(s) are disconnected from ground."
        )

    try:
        return ProjectStructuralCapture(
            project_name=project_name,
            design_hash=design_hash,
            title=str(
                declaration.get("title") or f"Structural Workbench — {project_name}"
            ),
            authoring_mode="generated" if generated_authoring else "legacy",
            design_basis=design_basis,
            components=components,
            connections=connections,
            loads=loads,
            load_paths=paths,
            analysis=analysis,
            capabilities=capabilities,
            warnings=warnings,
        )
    except ValidationError as exc:
        raise StructuralDeclarationError(f"invalid {DECLARATION_NAME}: {exc}") from exc


def parse_project_structural_capture(
    source: str,
    *,
    project_name: str,
) -> ProjectStructuralCapture:
    return capture_project_structural_declaration(
        _structural_declaration(source),
        project_name=project_name,
        design_hash=sha256(source.encode("utf-8")).hexdigest(),
    )
