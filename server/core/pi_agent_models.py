import json
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_PI_AGENT_MODELS = 20
MODEL_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
DEFAULT_PI_AGENT_MODELS_JSON = json.dumps(
    [
        {"id": "gpt-5.6-sol", "label": "GPT-5.6 Sol"},
        {"id": "gpt-5.6-luna", "label": "GPT-5.6 Luna"},
        {"id": "gpt-5.6-terra", "label": "GPT-5.6 Terra"},
    ],
    separators=(",", ":"),
)


class PiAgentModelOption(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=80)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if MODEL_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("model id has an invalid format")
        return value

    @field_validator("label", mode="before")
    @classmethod
    def trim_label(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


def parse_pi_agent_models(raw: str) -> tuple[PiAgentModelOption, ...]:
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("PI_AGENT_MODELS_JSON must be valid JSON") from exc

    if not isinstance(payload, list) or not 1 <= len(payload) <= MAX_PI_AGENT_MODELS:
        raise ValueError("PI_AGENT_MODELS_JSON must contain 1 to 20 models")

    try:
        models = tuple(PiAgentModelOption.model_validate(item) for item in payload)
    except (TypeError, ValueError) as exc:
        raise ValueError("PI_AGENT_MODELS_JSON contains an invalid model entry") from exc

    ids = [model.id for model in models]
    if len(ids) != len(set(ids)):
        raise ValueError("PI_AGENT_MODELS_JSON contains duplicate model ids")
    return models


def validate_default_pi_agent_model(
    raw: str,
    default_model: str,
) -> tuple[PiAgentModelOption, ...]:
    models = parse_pi_agent_models(raw)
    if default_model not in {model.id for model in models}:
        raise ValueError("PI_AGENT_MODEL must reference a model in PI_AGENT_MODELS_JSON")
    return models
