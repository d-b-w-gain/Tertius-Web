from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, replace
from math import dist
from typing import Any

import build123d as bd

from ._canonical import FrozenJson, freeze_json, required_text, thaw_json
from .component_cache import record_registration
from .components import (
    ComponentPort,
    ComponentRegistration,
    PortPlacement,
    PortSet,
)
from .connections import ConnectionDefinition, ConnectionRegistration
from .products import ProductDefinition


class TertiusRuntimeError(ValueError):
    """Raised when managed mechanical authoring cannot be reconciled safely."""


_CURRENT_SESSION: ContextVar["CompileSession | None"] = ContextVar(
    "tertius_compile_session",
    default=None,
)


@dataclass(frozen=True)
class SessionRegistrationCounts:
    products: int
    components: int
    connections: int


class CompileSession:
    def __init__(self) -> None:
        self._products: dict[str, ProductDefinition] = {}
        self._components: list[ComponentRegistration] = []
        self._components_by_token: dict[str, ComponentRegistration] = {}
        self._connections: list[ConnectionRegistration] = []
        self._marks: set[str] = set()
        self._connected_ports: set[tuple[str, str]] = set()
        self._used_connector_tokens: set[str] = set()
        self._finalized = False

    @property
    def counts(self) -> SessionRegistrationCounts:
        return SessionRegistrationCounts(
            products=len(self._products),
            components=len(self._components),
            connections=len(self._connections),
        )

    @property
    def products(self) -> tuple[ProductDefinition, ...]:
        return tuple(self._products.values())

    @property
    def components(self) -> tuple[ComponentRegistration, ...]:
        return tuple(self._components)

    @property
    def connections(self) -> tuple[ConnectionRegistration, ...]:
        return tuple(self._connections)

    def _ensure_open(self) -> None:
        if self._finalized:
            raise TertiusRuntimeError("the Tertius compile session is already finalized")

    def _register_product(self, product: ProductDefinition) -> None:
        existing = self._products.get(product.key)
        if existing is not None and existing.definition_digest != product.definition_digest:
            raise TertiusRuntimeError(
                f"product key {product.key!r} resolved to different definitions in one compile"
            )
        self._products.setdefault(product.key, product)

    def register_component(
        self,
        shape: bd.Shape,
        *,
        product: ProductDefinition,
        mark: str | None,
        role: str | None,
        fabrication: FrozenJson,
        ports: Mapping[str, PortPlacement],
    ) -> bd.Shape:
        self._ensure_open()
        self._register_product(product)
        normalized_mark = required_text("component mark", mark) if mark is not None else None
        if normalized_mark is not None:
            if normalized_mark in self._marks:
                raise TertiusRuntimeError(f"component mark {normalized_mark!r} is already used")
            self._marks.add(normalized_mark)
        normalized_role = required_text("component role", role) if role is not None else None
        token = f"component-token-{len(self._components) + 1:06d}"
        instance_id = normalized_mark or f"component-{len(self._components) + 1:06d}"
        component_ports: dict[str, ComponentPort] = {}
        declared_port_names = set(product.port_families)
        accepts_fabricated_ports = "*" in declared_port_names
        expected_port_names = declared_port_names - {"*"}
        unexpected = (
            []
            if accepts_fabricated_ports
            else sorted(set(ports) - expected_port_names)
        )
        if unexpected:
            raise TertiusRuntimeError(
                f"product {product.key!r} does not define ports {unexpected}"
            )
        for name, placement in ports.items():
            if not isinstance(placement, PortPlacement):
                raise TypeError(f"component port {name!r} requires PortPlacement")
            port_name = required_text("component port name", name)
            product_families = product.port_family_names(port_name)
            declared_families = placement.compatible_families or product_families
            if product_families and not set(declared_families).issubset(product_families):
                raise TertiusRuntimeError(
                    f"port {port_name!r} declares families outside product definition"
                )
            component_ports[port_name] = ComponentPort(
                component_token=token,
                name=port_name,
                point_mm=placement.point_mm,
                direction=placement.direction,
                x_direction=placement.x_direction,
                compatible_families=tuple(declared_families),
                engagement_length_mm=placement.engagement_length_mm,
            )
        missing = sorted(expected_port_names - set(component_ports))
        if missing:
            raise TertiusRuntimeError(
                f"product {product.key!r} requires port placements {missing}"
            )
        registration = ComponentRegistration(
            token=token,
            instance_id=instance_id,
            shape=shape,
            product=product,
            mark=normalized_mark,
            role=normalized_role,
            fabrication=fabrication,
            ports=component_ports,
        )
        self._components.append(registration)
        self._components_by_token[token] = registration
        self._tag_component(shape, registration)
        # A cached part replays this on a hit (see replay_registrations).
        record_registration(
            {
                "kind": "component",
                "token": token,
                "product": product.payload(),
                "mark": normalized_mark,
                "role": normalized_role,
                "fabrication": thaw_json(fabrication),
                "ports": {
                    name: {
                        "point_mm": list(placement.point_mm),
                        "direction": list(placement.direction),
                        "x_direction": list(placement.x_direction),
                        "compatible_families": list(placement.compatible_families),
                        "engagement_length_mm": placement.engagement_length_mm,
                    }
                    for name, placement in ports.items()
                },
            }
        )
        return shape

    @staticmethod
    def _tag_component(shape: bd.Shape, registration: ComponentRegistration) -> None:
        setattr(shape, "tertius_component_token", registration.token)
        setattr(shape, "tertius_component_id", registration.instance_id)
        setattr(shape, "tertius_product_key", registration.product.key)
        setattr(
            shape,
            "tertius_product_definition_digest",
            registration.product.definition_digest,
        )
        setattr(shape, "ports", PortSet(registration.ports))

    @staticmethod
    def _tag_connection(shape: bd.Shape, registration: ConnectionRegistration) -> None:
        setattr(shape, "tertius_connection_token", registration.token)
        setattr(shape, "tertius_connection_id", registration.connection_id)
        setattr(
            shape,
            "tertius_connection_definition_digest",
            registration.definition.definition_digest,
        )

    def register_connection(
        self,
        shape: bd.Shape,
        *,
        definition: ConnectionDefinition,
        ports: Sequence[ComponentPort],
        connector_components: Sequence[bd.Shape],
        mark: str | None,
    ) -> bd.Shape:
        self._ensure_open()
        if len(ports) < 2:
            raise TertiusRuntimeError("a physical connection requires at least two ports")
        normalized_ports: list[ComponentPort] = []
        for port in ports:
            if not isinstance(port, ComponentPort):
                raise TypeError("physical connections require ComponentPort handles")
            component = self._components_by_token.get(port.component_token)
            if component is None or component.ports.get(port.name) is not port:
                raise TertiusRuntimeError("connection port does not belong to this compile session")
            if port.compatible_families and definition.family not in port.compatible_families:
                raise TertiusRuntimeError(
                    f"port {component.instance_id}.{port.name} is incompatible with "
                    f"connection family {definition.family!r}"
                )
            port_key = (port.component_token, port.name)
            if port_key in self._connected_ports:
                raise TertiusRuntimeError(
                    f"port {component.instance_id}.{port.name} already belongs to a "
                    "physical connection"
                )
            normalized_ports.append(port)
        if len({port.component_token for port in normalized_ports}) < 2:
            raise TertiusRuntimeError("a physical connection must join different components")
        origin = normalized_ports[0].point_mm
        largest_offset = max(dist(origin, port.point_mm) for port in normalized_ports[1:])
        if largest_offset > definition.maximum_port_offset_mm + 1e-9:
            raise TertiusRuntimeError(
                f"physical connection ports are {largest_offset:g} mm apart; "
                f"{definition.key!r} permits at most "
                f"{definition.maximum_port_offset_mm:g} mm"
            )

        connector_tokens: list[str] = []
        for connector in connector_components:
            if not isinstance(connector, bd.Shape):
                raise TypeError("connector components must be Build123D shapes")
            token = getattr(connector, "tertius_component_token", None)
            connector_registration = self._components_by_token.get(str(token))
            if connector_registration is None or connector_registration.shape is not connector:
                raise TertiusRuntimeError(
                    "connection connector geometry must be a managed component from this session"
                )
            structural = connector_registration.product.structural
            shared_connection_component = bool(
                structural is not None
                and structural.properties.get("shared_connection_component") is True
            )
            if (
                connector_registration.token in self._used_connector_tokens
                and not shared_connection_component
            ):
                raise TertiusRuntimeError(
                    f"connection component {connector_registration.instance_id!r} is already "
                    "used by another physical connection; the managed connector product "
                    "must explicitly declare shared_connection_component=true when one "
                    "physical item spans multiple authored joints"
                )
            if structural is not None and structural.kind != "connector":
                raise TertiusRuntimeError(
                    f"connection component {connector_registration.instance_id!r} is not a structural connector"
                )
            connector_tokens.append(connector_registration.token)
        if not connector_tokens:
            raise TertiusRuntimeError("a physical connection requires managed connector components")
        if len(connector_tokens) != len(set(connector_tokens)):
            raise TertiusRuntimeError("a physical connection repeats a connector component")

        normalized_mark = required_text("connection mark", mark) if mark is not None else None
        token = f"connection-token-{len(self._connections) + 1:06d}"
        connection_id = normalized_mark or f"connection-{len(self._connections) + 1:06d}"
        connection_registration = ConnectionRegistration(
            token=token,
            connection_id=connection_id,
            shape=shape,
            definition=definition,
            ports=tuple(normalized_ports),
            connector_component_tokens=tuple(connector_tokens),
            mark=normalized_mark,
        )
        self._connections.append(connection_registration)
        self._connected_ports.update(
            (port.component_token, port.name) for port in normalized_ports
        )
        self._used_connector_tokens.update(connector_tokens)
        self._tag_connection(shape, connection_registration)
        record_registration(
            {
                "kind": "connection",
                "token": token,
                "definition": definition.payload(),
                "ports": [[port.component_token, port.name] for port in normalized_ports],
                "connector_tokens": connector_tokens,
                "mark": normalized_mark,
            }
        )
        return shape

    def replay_registrations(self, shape: bd.Shape, effects: Sequence[Mapping[str, Any]]) -> None:
        """Redo the registrations a cached part made when it was built, onto
        the shape loaded from its cache entry. Registered nodes are found by
        the tokens stored with the entry and get this session's tokens, so
        the session ends up exactly as if the part had been built."""
        components, connections = _tokened_nodes(shape)
        new_tokens: dict[str, str] = {}
        products: dict[str, ProductDefinition] = {}
        for effect in effects:
            if effect["kind"] == "component":
                node = components.get(effect["token"])
                if node is None:
                    raise TertiusRuntimeError(
                        f"cached part is missing registered component {effect['mark'] or effect['token']!r}"
                    )
                digest = effect["product"]["definition_digest"]
                product = products.get(digest)
                if product is None:
                    product = products[digest] = ProductDefinition.from_payload(effect["product"])
                self.register_component(
                    node,
                    product=product,
                    mark=effect["mark"],
                    role=effect["role"],
                    fabrication=freeze_json(effect["fabrication"], label="component fabrication"),
                    ports={
                        name: PortPlacement(
                            placement["point_mm"],
                            placement["direction"],
                            placement["compatible_families"],
                            x_direction=placement["x_direction"],
                            engagement_length_mm=placement["engagement_length_mm"],
                        )
                        for name, placement in effect["ports"].items()
                    },
                )
                new_tokens[effect["token"]] = node.tertius_component_token
            elif effect["kind"] == "connection":
                node = connections.get(effect["token"])
                if node is None:
                    raise TertiusRuntimeError(
                        f"cached part is missing registered connection {effect['mark'] or effect['token']!r}"
                    )
                self.register_connection(
                    node,
                    definition=ConnectionDefinition.from_payload(effect["definition"]),
                    ports=[
                        self._components_by_token[new_tokens[token]].ports[name]
                        for token, name in effect["ports"]
                    ],
                    connector_components=[
                        self._components_by_token[new_tokens[token]].shape
                        for token in effect["connector_tokens"]
                    ],
                    mark=effect["mark"],
                )
            else:
                raise TertiusRuntimeError(f"unknown cached registration {effect['kind']!r}")

    def rebind_registrations(self, loaded: bd.Shape) -> None:
        """A newly built cached part hands its caller the copy read back from
        its cache entry. Point this session's registrations at that copy's
        nodes, so the final model contains the registered shapes."""
        components, connections = _tokened_nodes(loaded)
        positions = {registration.token: index for index, registration in enumerate(self._components)}
        for token, node in components.items():
            registration = self._components_by_token.get(token)
            if registration is None:
                continue
            moved = replace(registration, shape=node)
            self._components_by_token[token] = moved
            self._components[positions[token]] = moved
            self._tag_component(node, moved)
        for index, connection in enumerate(self._connections):
            connection_node = connections.get(connection.token)
            if connection_node is not None:
                moved_connection = replace(connection, shape=connection_node)
                self._connections[index] = moved_connection
                self._tag_connection(connection_node, moved_connection)

    def validate_cached_registrations(
        self,
        shape: bd.Shape,
        effects: Sequence[Mapping[str, Any]],
    ) -> str | None:
        """Why a newly built part can't be stored with its registrations, or
        None. A hit replays only what the part itself registered, onto its
        own result, so the result must hold exactly those registrations."""
        try:
            components, connections = _tokened_nodes(shape)
        except TertiusRuntimeError:
            return "component_replay_ineligible_repeated_registration"
        registered_components = {e["token"] for e in effects if e["kind"] == "component"}
        registered_connections = {e["token"] for e in effects if e["kind"] == "connection"}
        if set(components) - registered_components or set(connections) - registered_connections:
            return "component_replay_ineligible_foreign_registration"
        if registered_components - set(components) or registered_connections - set(connections):
            return "component_replay_ineligible_registration_outside_result"
        for effect in effects:
            if effect["kind"] == "connection" and (
                {token for token, _ in effect["ports"]} | set(effect["connector_tokens"])
            ) - registered_components:
                return "component_replay_ineligible_external_connection"
        return None

    def finalize(self, model: bd.Shape) -> dict[str, Any]:
        self._ensure_open()
        from .graph import build_compiled_design_graph

        graph = build_compiled_design_graph(self, model)
        self._finalized = True
        setattr(model, "tertius_compiled_design", graph)
        return graph


def _tokened_nodes(shape: bd.Shape) -> tuple[dict[str, bd.Shape], dict[str, bd.Shape]]:
    """{token: node} for the registered components and connections in a
    shape tree. A token on two nodes (a moved copy of a registered shape)
    can't be replayed onto one node, so it raises."""
    components: dict[str, bd.Shape] = {}
    connections: dict[str, bd.Shape] = {}
    for node in bd.PreOrderIter(shape):
        for attribute, found in (
            ("tertius_component_token", components),
            ("tertius_connection_token", connections),
        ):
            token = getattr(node, attribute, None)
            if token is None:
                continue
            if str(token) in found:
                raise TertiusRuntimeError(f"registration {token!r} appears twice in one part")
            found[str(token)] = node
    return components, connections


def current_session() -> CompileSession:
    session = _CURRENT_SESSION.get()
    if session is None:
        raise TertiusRuntimeError(
            "workbench-enabled components require an active Tertius compile session; "
            "run the project through Tertius or `python -m tertius.runner <project>`"
        )
    return session


@contextmanager
def compile_session() -> Iterator[CompileSession]:
    if _CURRENT_SESSION.get() is not None:
        raise TertiusRuntimeError("nested Tertius compile sessions are not supported")
    session = CompileSession()
    token: Token[CompileSession | None] = _CURRENT_SESSION.set(session)
    try:
        yield session
    finally:
        _CURRENT_SESSION.reset(token)
