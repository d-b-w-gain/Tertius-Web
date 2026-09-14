import json

import pytest

from core.pi_agent_models import (
    DEFAULT_PI_AGENT_MODELS_JSON,
    MAX_PI_AGENT_MODELS,
    parse_pi_agent_models,
    validate_default_pi_agent_model,
)


def test_default_catalog_is_ordered_sol_luna_terra():
    assert [
        (model.id, model.label)
        for model in parse_pi_agent_models(DEFAULT_PI_AGENT_MODELS_JSON)
    ] == [
        ("gpt-5.6-sol", "GPT-5.6 Sol"),
        ("gpt-5.6-luna", "GPT-5.6 Luna"),
        ("gpt-5.6-terra", "GPT-5.6 Terra"),
    ]


@pytest.mark.parametrize(
    ("raw", "expected_rule"),
    [
        ("not-json", "valid JSON"),
        ("{}", "1 to 20 models"),
        ("[]", "1 to 20 models"),
        (
            json.dumps(
                [
                    {"id": f"model-{index}", "label": str(index)}
                    for index in range(MAX_PI_AGENT_MODELS + 1)
                ]
            ),
            "1 to 20 models",
        ),
        (
            json.dumps(
                [
                    {"id": "gpt-5.6-sol", "label": "Sol"},
                    {"id": "gpt-5.6-sol", "label": "Duplicate"},
                ]
            ),
            "duplicate model ids",
        ),
        (
            json.dumps([{"id": "unsafe/model", "label": "Unsafe"}]),
            "invalid model entry",
        ),
        (
            json.dumps([{"id": "gpt-5.6-sol", "label": "   "}]),
            "invalid model entry",
        ),
        (
            json.dumps([{"id": "gpt-5.6-sol", "label": "x" * 81}]),
            "invalid model entry",
        ),
        (
            json.dumps(
                [
                    {
                        "id": "gpt-5.6-sol",
                        "label": "Sol",
                        "private": "raw-extra-field-sentinel",
                    }
                ]
            ),
            "invalid model entry",
        ),
    ],
)
def test_catalog_rejects_invalid_values_without_echoing_input(raw, expected_rule):
    with pytest.raises(ValueError) as exc_info:
        parse_pi_agent_models(raw)

    message = str(exc_info.value)
    assert "PI_AGENT_MODELS_JSON" in message
    assert expected_rule in message
    assert raw not in message


def test_catalog_trims_labels():
    models = parse_pi_agent_models('[{"id":"gpt-5.6-sol","label":"  Sol  "}]')

    assert models[0].label == "Sol"


def test_default_model_must_belong_to_catalog():
    raw = '[{"id":"gpt-5.6-sol","label":"GPT-5.6 Sol"}]'

    with pytest.raises(ValueError, match="PI_AGENT_MODEL must reference") as exc_info:
        validate_default_pi_agent_model(raw, "private-default-sentinel")

    assert "private-default-sentinel" not in str(exc_info.value)
