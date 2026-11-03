from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from beliefkv.predictor.workflow_qwen_encoder import (
    DIMENSION, FILES, INSTRUCTION, KIND, REVISION, WEIGHTS_SHA256, QwenTaskEncoder,
    last_token_pool, validate_contract,
)
from beliefkv.predictor.workflow_task_encoder import TASK_FEATURES
from scripts.train_workflow_qwen_initial_work import calibration_choice, initial_metrics


def contract():
    return {
        "kind": KIND, "directory": "/offline/pinned/qwen", "revision": REVISION,
        "files_sha256": {name: WEIGHTS_SHA256 if name == "model.safetensors" else "0" * 64 for name in FILES},
        "instruction": INSTRUCTION, "maximum_task_characters": 4096, "maximum_tokens": 8192,
        "embedding_dimension": DIMENSION, "task_features": list(TASK_FEATURES),
        "pooling": "last_nonpadding_token_then_float32_l2", "device": "cpu",
        "dtype": "bfloat16", "maximum_cpu_threads": 1, "remote_code": False,
    }


@pytest.mark.parametrize("field,value", (
    ("device", "cuda"), ("remote_code", True), ("revision", "f" * 40),
    ("instruction", "Use the future complete trace"), ("maximum_task_characters", 100000),
))
def test_qwen_contract_rejects_changed_model_scope_or_resource_contract(field, value):
    raw = contract()
    assert validate_contract(raw) == raw
    raw[field] = value
    with pytest.raises(ValueError):
        validate_contract(raw)


def test_pooling_uses_last_observed_token_for_left_and_right_padding():
    states = torch.arange(2 * 4 * 3).reshape(2, 4, 3).to(torch.bfloat16)
    attention = torch.tensor([[0, 0, 1, 1], [1, 1, 0, 0]])
    actual = last_token_pool(states, attention)
    assert actual.dtype == torch.float32
    assert torch.equal(actual, torch.stack((states[0, 3], states[1, 1])).float())
    with pytest.raises(ValueError, match="nonempty"):
        last_token_pool(states, torch.zeros_like(attention))
    with pytest.raises(ValueError, match="finite"):
        last_token_pool(states.float().fill_(float("nan")), attention)


def test_encoder_uses_explicit_task_once_without_generation_cache_or_remote_calls():
    encoder = QwenTaskEncoder(contract())
    calls = []

    def tokenize(text, **kwargs):
        calls.append(("tokenize", text, kwargs))
        return {"input_ids": torch.tensor([[1, 2]]), "attention_mask": torch.tensor([[1, 1]])}

    def forward(**kwargs):
        calls.append(("forward", kwargs))
        return SimpleNamespace(last_hidden_state=torch.ones(1, 2, DIMENSION, dtype=torch.bfloat16))

    encoder._tokenizer, encoder._model = tokenize, forward
    vector, tokenization_coverage = encoder.encode("Explain the validation path.")
    assert vector.shape == (DIMENSION + len(TASK_FEATURES),)
    assert np.linalg.norm(vector[:DIMENSION]) == pytest.approx(1.)
    assert tokenization_coverage == 1.
    assert calls[0][1] == "Instruct: " + INSTRUCTION + "\nQuery: Explain the validation path."
    assert calls[0][2]["truncation"] is False
    assert calls[1][1]["use_cache"] is False
    assert len(encoder.measurements) == 1
    assert encoder.measurements[0]["input_tokens"] == 2
    assert encoder.encode("  ") == (None, 0.)
    assert len(calls) == 2
    with pytest.raises(ValueError, match="bounded explicit"):
        encoder.encode("x" * 4097)


def test_encoder_rejects_excess_tokens_before_model_execution():
    encoder = QwenTaskEncoder(contract())
    encoder._tokenizer = lambda *args, **kwargs: {
        "input_ids": torch.ones(1, 8193, dtype=torch.int64),
        "attention_mask": torch.ones(1, 8193, dtype=torch.int64),
    }

    def forbidden(**kwargs):
        raise AssertionError("oversized task must not reach the model")

    encoder._model = forbidden
    with pytest.raises(ValueError, match="do not silently truncate"):
        encoder.encode("bounded task text")


def test_initial_metric_measures_work_ranking_and_continuous_utility_without_queueing_labels():
    from beliefkv.predictor.workflow_continuous_work import remaining_work_weight

    rows = [{"total_work": value} for value in (24000., 230000., 1000.)]
    logs = np.log1p([row["total_work"] for row in rows])
    utility = np.asarray([remaining_work_weight(row["total_work"], 19144.) for row in rows])
    metrics = initial_metrics(rows, logs, utility, 19144.)
    assert metrics["pair_ranking_accuracy"] == 1.
    assert metrics["mean_squared_continuous_utility_error"] == 0.
    assert metrics["log1p_work_RMSE"] == 0.
    assert metrics["target"] == "initial_logical_model_work_not_waiting_or_JCT"


def test_initial_selection_does_not_trade_calibration_ranking_for_lower_utility_error():
    values = {
        "retained": {"pair_ranking_accuracy": .8, "mean_squared_continuous_utility_error": .1},
        "candidate": {"pair_ranking_accuracy": .7, "mean_squared_continuous_utility_error": .01},
    }
    before = deepcopy(values)
    assert calibration_choice(values) == "retained"
    assert values == before
    values["candidate"]["pair_ranking_accuracy"] = .8
    assert calibration_choice(values) == "candidate"
