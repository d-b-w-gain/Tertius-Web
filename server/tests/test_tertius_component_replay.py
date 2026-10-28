from __future__ import annotations

import json

import pytest

from tertius import ConnectionDefinition, ConnectionResistanceDefinition, ProductDefinition

from test_tertius_runtime import connector_product, member_product


def _json_round_trip(payload: dict) -> dict:
    # Replay data lives in a cache sidecar, so it must survive JSON.
    return json.loads(json.dumps(payload))


@pytest.mark.parametrize("product", [member_product(), connector_product()])
def test_product_definition_round_trips_through_its_payload(product: ProductDefinition) -> None:
    rebuilt = ProductDefinition.from_payload(_json_round_trip(product.payload()))

    assert rebuilt == product
    assert rebuilt.definition_digest == product.definition_digest
    assert rebuilt.payload() == product.payload()


def test_product_payload_with_a_wrong_digest_is_rejected() -> None:
    payload = _json_round_trip(member_product().payload())
    payload["definition_digest"] = "0" * 64

    with pytest.raises(ValueError, match="does not match its digest"):
        ProductDefinition.from_payload(payload)


def test_connection_definition_round_trips_with_resistance() -> None:
    definition = ConnectionDefinition(
        key="test-knee",
        label="Test knee",
        family="test-bolted",
        transfers=("force", "shear", "moment"),
        analysis_model="rigid",
        stiffness_status="candidate",
        stiffness_basis="Test basis.",
        maximum_port_offset_mm=2.5,
        resistance=ConnectionResistanceDefinition(
            pack_id="test-pack",
            version="1",
            status="verified",
            basis="Test evidence.",
            connector_part_numbers=("TEST-KB01",),
            source="Test source",
            source_sha256="a" * 64,
            design_axial_capacity_kN=10,
            design_shear_capacity_kN=10,
            design_moment_capacity_kNm=1,
            assumptions=("Test assumption.",),
        ),
    )

    rebuilt = ConnectionDefinition.from_payload(_json_round_trip(definition.payload()))

    assert rebuilt == definition
    assert rebuilt.definition_digest == definition.definition_digest
