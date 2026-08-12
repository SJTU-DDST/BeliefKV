from __future__ import annotations

import pytest

from scripts.stress_qwen_code_kv_pool import synthetic_request


def test_synthetic_request_has_stable_pressure_prompt() -> None:
    request = synthetic_request(model="model", prompt_words=4)

    assert request["model"] == "model"
    assert request["messages"][1]["content"] == "pressure " * 4
    assert request["temperature"] == 0.0


def test_synthetic_request_requires_model_and_positive_words() -> None:
    with pytest.raises(ValueError, match="--model"):
        synthetic_request(model="", prompt_words=4)
    with pytest.raises(ValueError, match="positive"):
        synthetic_request(model="model", prompt_words=0)
