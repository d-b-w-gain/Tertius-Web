import json
from typing import Any

from core.config import Settings


TEST_LLM_MODEL_ID = "test-openai-compatible-model"
TEST_LLM_MODELS_JSON = json.dumps(
    [
        {
            "id": TEST_LLM_MODEL_ID,
            "label": "Test Model",
            "model": TEST_LLM_MODEL_ID,
            "endpoint": "https://llm.example.test/v1/chat/completions",
            "api": "openai-chat-completions",
            "input_price_per_million": 1.0,
            "output_price_per_million": 2.0,
            "cached_read_price_per_million": 0.1,
            "cached_write_price_per_million": None,
            "enabled": True,
        }
    ]
)


def make_llm_settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "llm_models_json": TEST_LLM_MODELS_JSON,
        "llm_default_model_id": TEST_LLM_MODEL_ID,
    }
    defaults.update(overrides)
    return Settings(**defaults)
