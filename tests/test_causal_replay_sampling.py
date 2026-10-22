import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
import torch

from beliefkv.experiments.causal_replay_sampling import (
    CapturedOutputLogitsProcessor, PARAMETER_KEY,
)


def parameters(request, tokens, output=()):
    return {
        "__req__": NS(rid=request, output_ids=list(output)),
        PARAMETER_KEY: {"request_id": request, "output_token_ids": tokens},
    }


def test_mixed_batch_uses_each_native_request_progress_after_filtering_and_reordering():
    processor = CapturedOutputLogitsProcessor()
    a, b = parameters("a", [3, 4, 9]), parameters("b", [7, 9])
    original = torch.randn(2, 16)
    result = processor(original, [a, b])
    assert result is original
    assert result.argmax(dim=1).tolist() == [3, 7]
    assert torch.isfinite(result).sum(dim=1).tolist() == [1, 1]
    a["__req__"].output_ids.append(3)
    b["__req__"].output_ids.append(7)
    result = processor(torch.randn(2, 16), [b, a])
    assert result.argmax(dim=1).tolist() == [9, 4]
    a["__req__"].output_ids.append(4)
    assert processor(torch.randn(1, 16), [a]).argmax(dim=1).tolist() == [9]


def test_retraction_restarts_at_actual_retained_progress_without_an_internal_cursor():
    processor = CapturedOutputLogitsProcessor()
    current = parameters("a", [3, 4, 9], [3, 4])
    assert processor(torch.zeros(1, 16), [current]).argmax().item() == 9
    current["__req__"].output_ids = [3]
    assert processor(torch.zeros(1, 16), [current]).argmax().item() == 4
    current["__req__"].output_ids = []
    assert processor(torch.zeros(1, 16), [current]).argmax().item() == 3


def test_exhausted_sequence_requires_native_stop_and_never_invents_an_extra_eos():
    processor = CapturedOutputLogitsProcessor()
    logits = torch.randn(1, 16)
    original = logits.clone()
    with pytest.raises(ValueError, match="failed to stop"):
        processor(logits, [parameters("a", [3, 9], [3, 9])])
    assert torch.equal(logits, original)


@pytest.mark.parametrize("change", (
    "foreign_req", "duplicate_req", "missing_req", "missing_payload",
    "divergent_output", "out_of_vocab", "negative_token", "noninteger_token",
    "row_count", "missing_params", "speculative_repeated_rows", "integer_logits",
    "three_dimensions", "invalid_params_row",
))
def test_invalid_batch_fails_before_mutating_any_logits(change):
    processor = CapturedOutputLogitsProcessor()
    a, b = parameters("a", [3, 9]), parameters("b", [7, 9])
    batch, logits = [a, b], torch.randn(2, 16)
    if change == "foreign_req":
        b["__req__"].rid = "foreign"
    elif change in ("duplicate_req", "speculative_repeated_rows"):
        batch = [a, a]
    elif change == "missing_req":
        del b["__req__"]
    elif change == "missing_payload":
        del b[PARAMETER_KEY]
    elif change == "divergent_output":
        b["__req__"].output_ids = [6]
    elif change == "out_of_vocab":
        b[PARAMETER_KEY]["output_token_ids"] = [16]
    elif change == "negative_token":
        b[PARAMETER_KEY]["output_token_ids"] = [-1]
    elif change == "noninteger_token":
        b[PARAMETER_KEY]["output_token_ids"] = [True]
    elif change == "row_count":
        batch = [a]
    elif change == "missing_params":
        batch = None
    elif change == "integer_logits":
        logits = torch.zeros((2, 16), dtype=torch.int64)
    elif change == "three_dimensions":
        logits = torch.zeros(2, 2, 16)
    else:
        batch[1] = None
    original = logits.clone()
    with pytest.raises(ValueError):
        processor(logits, batch)
    assert torch.equal(logits, original)


def test_serialized_processor_loads_through_the_actual_native_deserializer():
    checkout = os.environ.get("SGLANG_SOURCE_CHECKOUT")
    if not checkout:
        pytest.skip("set SGLANG_SOURCE_CHECKOUT to qualify the native API")
    path = Path(checkout) / "python/sglang/srt/sampling/custom_logit_processor.py"
    spec = importlib.util.spec_from_file_location("_replay_native_logit_processor", path)
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    serialized = CapturedOutputLogitsProcessor.to_str()
    restored = native.CustomLogitProcessor.from_str(serialized)
    assert isinstance(restored, CapturedOutputLogitsProcessor)
    assert restored(torch.randn(1, 16), [parameters("a", [9])]).argmax().item() == 9
    assert not native.supports_sampling_mask(serialized)
