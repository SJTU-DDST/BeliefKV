from types import SimpleNamespace

import pytest
import torch

from beliefkv.experiments.causal_replay_sampling import PARAMETER_KEY
from scripts.benchmark_causal_sampling import (
    ReplaySamplingBatch, VARIANTS, distribution, step, validate_limits,
)


def test_all_calibration_paths_choose_the_same_tokens_and_dispatch_the_actual_batch():
    parameters = [{
        "__req__": SimpleNamespace(rid="a", output_ids=[]),
        PARAMETER_KEY: {"request_id": "a", "output_token_ids": [3]},
    }]
    batch = ReplaySamplingBatch(torch.ones(1, dtype=torch.bool), parameters)
    calls = []

    def dispatch(logits, actual):
        assert actual is batch and len(actual) == 1
        processor, mask = actual.custom_logit_processor["captured"]
        assert mask.tolist() == [True]
        calls.append(actual)
        processor(logits, actual.custom_params)

    for variant in VARIANTS:
        logits = torch.zeros(1, 16)
        logits[0, 3] = 1.
        assert step(variant, logits, batch, dispatch).tolist() == [3]
    assert calls == [batch]
    assert parameters[0]["__req__"].output_ids == []


@pytest.mark.parametrize("samples,cycles", (
    (2, 64), (32, 64), (True, 64), (11, 0), (11, 513), (11, True),
))
def test_invalid_measurement_limits_are_rejected(samples, cycles):
    with pytest.raises(ValueError):
        validate_limits(samples, cycles)


def test_component_summary_preserves_negative_differences_without_claiming_performance():
    assert distribution([1., 2., 6.]) == {
        "samples": 3, "mean_ms": 3., "median_ms": 2., "min_ms": 1., "max_ms": 6.,
    }
    with pytest.raises(ValueError, match="variant"):
        step("invented", torch.zeros(1, 16), None, None)


def test_position_calibration_includes_cpu_progress_without_legacy_mask_dispatch():
    parameters = [{
        "__req__": SimpleNamespace(rid="a", output_ids=[], origin_input_ids=[1] * 64),
        PARAMETER_KEY: {"request_id": "a", "output_token_ids": [3]},
    }]
    batch = ReplaySamplingBatch(torch.ones(1, dtype=torch.bool), parameters)

    def legacy(*_args):
        raise AssertionError("position calibration queried the old device mask")

    assert step(
        "position_forcing_dispatch", torch.zeros(1, 16), batch, legacy,
    ).tolist() == [3]
    assert parameters[0]["__req__"].output_ids == []
