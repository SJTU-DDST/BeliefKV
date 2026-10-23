from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
import torch

from beliefkv.experiments.causal_replay_sampling import (
    CapturedOutputLogitsProcessor, PARAMETER_KEY, POSITION_KEY,
    apply_position_logits, filter_position_batch, sample_with_positions,
)
from tests.test_causal_replay_native import native_root
from tests.test_causal_replay_sampling import parameters


class UnreadableMask:
    def nonzero(self, *args, **kwargs):
        raise AssertionError("position replay must not query the device mask")


def batch(rows, lengths):
    for row in rows:
        if isinstance(row, dict) and "__req__" in row:
            row["__req__"].origin_input_ids = [1] * 16
    info = NS(
        custom_params=rows,
        custom_logit_processor={"captured": (CapturedOutputLogitsProcessor(), UnreadableMask())},
    )
    return NS(
        sampling_info=info, seq_lens_cpu=torch.tensor(lengths),
        forward_mode=NS(is_extend=lambda: True),
    )


def native_sample(_runner, logits, forward):
    apply_position_logits(
        Mock(side_effect=AssertionError("legacy device-mask dispatch was called")),
        logits, forward.sampling_info,
    )
    return logits.argmax(dim=1)


def test_immediate_sampling_uses_this_forward_before_the_previous_result_is_committed():
    row = parameters("a", [3, 4, 9])
    forward = batch([row], [16])
    pending, emitted, chosen = None, [], []
    for position in range(4):
        forward.seq_lens_cpu = torch.tensor([16 + position])
        logits = torch.zeros(1, 16)
        logits[0, 11] = 1.
        selected = sample_with_positions(native_sample, None, logits, forward).item()
        chosen.append(selected)
        assert not hasattr(forward.sampling_info, POSITION_KEY)
        if pending is not None:
            emitted.append(pending)
            row["__req__"].output_ids.append(pending)
        if emitted == [3, 4, 9]:
            break
        pending = selected
    assert emitted == [3, 4, 9]
    assert chosen == [3, 4, 9, 11]


def test_chunked_prefill_and_retraction_use_native_input_progress():
    row = parameters("a", [3, 4, 9])
    forward = batch([row], [8])
    assert sample_with_positions(native_sample, None, torch.zeros(1, 16), forward).item() == 3
    forward.seq_lens_cpu = torch.tensor([16])
    assert sample_with_positions(native_sample, None, torch.zeros(1, 16), forward).item() == 3
    row["__req__"].output_ids = [3]
    forward.seq_lens_cpu = torch.tensor([18])
    assert sample_with_positions(native_sample, None, torch.zeros(1, 16), forward).item() == 9
    row["__req__"].output_ids = []
    forward.seq_lens_cpu = torch.tensor([16])
    assert sample_with_positions(native_sample, None, torch.zeros(1, 16), forward).item() == 3


def test_retracted_history_can_be_recomputed_in_chunks_before_retained_output():
    row = parameters("a", [3, 4, 9, 11, 13], [3, 4, 9])
    forward = batch([row], [8])
    for length in (8, 18, 19):
        forward.seq_lens_cpu = torch.tensor([length])
        assert sample_with_positions(native_sample, None, torch.zeros(1, 16), forward).item() == 11
        assert row["__req__"].output_ids == [3, 4, 9]


def test_decode_cannot_hide_a_forward_behind_committed_output_as_a_prefill_chunk():
    row = parameters("a", [3, 4, 9], [3])
    forward = batch([row], [16])
    forward.forward_mode = NS(is_extend=lambda: False)
    with pytest.raises(ValueError, match="decode forward precedes"):
        sample_with_positions(native_sample, None, torch.zeros(1, 16), forward)


def test_mixed_health_live_and_pending_terminal_rows_keep_their_native_identity():
    a, b = parameters("a", [3, 4, 9], [3]), parameters("b", [7, 9], [7])
    forward = batch([None, b, a], [1, 18, 18])
    logits = torch.randn(3, 16)
    before = logits.clone()
    result = sample_with_positions(native_sample, None, logits, forward)
    assert result[2].item() == 9
    assert torch.equal(logits[0], before[0])
    assert torch.equal(logits[1], before[1])


@pytest.mark.parametrize("change", (
    "jump", "negative", "diverged", "wrong_count", "bool_position", "exhausted",
))
def test_invalid_forward_progress_fails_without_mutating_logits(change):
    row, positions = parameters("a", [3, 4, 9], [3]), (2,)
    if change == "jump":
        positions = (3,)
    elif change == "negative":
        positions = (-1,)
    elif change == "diverged":
        row["__req__"].output_ids = [6]
    elif change == "wrong_count":
        positions = ()
    elif change == "bool_position":
        positions = (True,)
    elif change == "exhausted":
        positions = (4,)
    logits = torch.randn(1, 16)
    before = logits.clone()
    with pytest.raises(ValueError):
        CapturedOutputLogitsProcessor()(logits, [row], positions=positions)
    assert torch.equal(logits, before)


def test_position_binding_is_removed_when_native_sampling_fails():
    forward = batch([parameters("a", [3, 9])], [16])
    original = Mock(side_effect=RuntimeError("native sample failed"))
    with pytest.raises(RuntimeError, match="native sample failed"):
        sample_with_positions(original, None, torch.zeros(1, 16), forward)
    assert not hasattr(forward.sampling_info, POSITION_KEY)


@pytest.mark.parametrize("change", ("missing", "device", "shape", "rows", "nested"))
def test_native_progress_requires_an_unambiguous_cpu_forward_snapshot(change):
    forward = batch([parameters("a", [3, 9])], [16])
    if change == "missing":
        forward.seq_lens_cpu = None
    elif change == "device":
        forward.seq_lens_cpu = NS(device=NS(type="cuda"))
    elif change == "shape":
        forward.seq_lens_cpu = torch.tensor([[16]])
    elif change == "rows":
        forward.seq_lens_cpu = torch.tensor([16, 16])
    else:
        setattr(forward.sampling_info, POSITION_KEY, (0,))
    original = Mock()
    with pytest.raises(ValueError):
        sample_with_positions(original, None, torch.zeros(1, 16), forward)
    original.assert_not_called()


def test_ordinary_sampling_uses_the_original_entry_without_replay_work():
    forward = NS(sampling_info=NS(custom_params=[None]))
    original = Mock(return_value="ordinary")
    logits, runner = object(), object()
    assert sample_with_positions(original, runner, logits, forward) == "ordinary"
    original.assert_called_once_with(runner, logits, forward)


def test_unbound_custom_processors_keep_native_dispatch():
    original = Mock(return_value="native")
    logits, info = object(), NS()
    assert apply_position_logits(original, logits, info) == "native"
    original.assert_called_once_with(logits, info, 1)


@pytest.mark.parametrize("change", ("speculative", "foreign_processor", "extra_processor"))
def test_position_mode_rejects_unqualified_sampling_semantics(change):
    forward = batch([parameters("a", [3, 9])], [16])
    info = forward.sampling_info
    setattr(info, POSITION_KEY, (0,))
    repeat = 1
    if change == "speculative":
        repeat = 2
    elif change == "foreign_processor":
        info.custom_logit_processor["captured"] = (object(), UnreadableMask())
    else:
        info.custom_logit_processor["other"] = (object(), UnreadableMask())
    with pytest.raises(ValueError):
        apply_position_logits(Mock(), torch.zeros(1, 16), info, repeat)


def test_position_and_committed_progress_agree_at_every_original_output_boundary():
    legacy, current = CapturedOutputLogitsProcessor(), CapturedOutputLogitsProcessor()
    a, b = parameters("a", [3, 4, 9]), parameters("b", [7, 9])
    for rows, positions in (([a, b], (0, 0)), ([b, a], (1, 1)), ([a], (2,))):
        for row, position in zip(rows, positions, strict=True):
            row["__req__"].output_ids = row[PARAMETER_KEY]["output_token_ids"][:position]
        logits = torch.randn(len(rows), 16)
        before = legacy(logits.clone(), deepcopy(rows))
        after = current(logits.clone(), deepcopy(rows), positions=positions)
        assert torch.equal(before, after)


class NoScalarReadTensor(torch.Tensor):
    def __bool__(self):
        raise AssertionError("filtering queried a device scalar")


def filtering_batch(*, no_scalar=False):
    rows = [parameters(name, tokens) for name, tokens in (
        ("a", [3, 4, 9]), ("b", [7, 9]), ("c", [11, 9]),
    )]
    for row in rows:
        row["__req__"].custom_logit_processor = "qualified-replay"
    mask = torch.ones(3, dtype=torch.bool)
    if no_scalar:
        mask = mask.as_subclass(NoScalarReadTensor)
    return NS(
        custom_params=rows, has_custom_logit_processor=True,
        custom_logit_processor={
            hash("qualified-replay"): (CapturedOutputLogitsProcessor(), mask),
        },
    )


@pytest.mark.parametrize("keep", ([0, 1, 2], [2, 0], [1], [], [0, 0]))
def test_position_filter_matches_actual_native_request_order_and_mask(monkeypatch, keep):
    native_root(monkeypatch)
    from sglang.srt.sampling.sampling_batch_info import SamplingBatchInfo

    original = SamplingBatchInfo._filter_batch_custom_logit_processor
    before, after = filtering_batch(), filtering_batch()
    indices = torch.tensor(keep, dtype=torch.int64)
    original(before, keep, indices)
    filter_position_batch(original, after, keep, indices)
    assert after.has_custom_logit_processor is before.has_custom_logit_processor
    if not keep:
        assert after.custom_params is after.custom_logit_processor is None
    else:
        assert [row["__req__"].rid for row in after.custom_params] == [
            row["__req__"].rid for row in before.custom_params
        ]
        key = hash("qualified-replay")
        assert torch.equal(after.custom_logit_processor[key][1], before.custom_logit_processor[key][1])


@pytest.mark.parametrize("keep", ([2, 0], []))
def test_actual_native_scalar_read_is_removed_only_for_qualified_position_filter(monkeypatch, keep):
    native_root(monkeypatch)
    from sglang.srt.sampling.sampling_batch_info import SamplingBatchInfo

    original = SamplingBatchInfo._filter_batch_custom_logit_processor
    indices = torch.tensor(keep, dtype=torch.int64)
    with pytest.raises(AssertionError, match="queried a device scalar"):
        original(filtering_batch(no_scalar=True), keep, indices)
    info = filtering_batch(no_scalar=True)
    filter_position_batch(
        Mock(side_effect=AssertionError("unexpected native fallback")),
        info, keep, indices,
    )
    assert info.has_custom_logit_processor is bool(keep)
    if keep:
        assert next(iter(info.custom_logit_processor.values()))[1].tolist() == [True] * len(keep)


@pytest.mark.parametrize("change", (
    "ordinary", "extra_processor", "mixed_health", "foreign_request", "foreign_flag",
    "unknown_parameters", "wrong_shape",
))
def test_unproven_filter_ownership_keeps_the_native_path(change):
    info = filtering_batch()
    key = hash("qualified-replay")
    if change == "ordinary":
        info.custom_logit_processor[key] = (object(), torch.ones(3, dtype=torch.bool))
    elif change == "extra_processor":
        info.custom_logit_processor[1] = (object(), torch.zeros(3, dtype=torch.bool))
    elif change == "mixed_health":
        info.custom_params[0] = None
    elif change == "foreign_request":
        info.custom_params[0]["__req__"].rid = "foreign"
    elif change == "foreign_flag":
        info.custom_params[0]["__req__"].custom_logit_processor = "different-processor"
    elif change == "unknown_parameters":
        info.custom_params = None
    else:
        info.custom_logit_processor[key] = (CapturedOutputLogitsProcessor(), torch.ones(2, dtype=torch.bool))
    original, indices = Mock(return_value="native"), torch.tensor([1, 2])
    assert filter_position_batch(original, info, [1, 2], indices) == "native"
    original.assert_called_once_with(info, [1, 2], indices)


def test_position_filter_then_native_merge_keeps_output_tokens_and_terminal_state(monkeypatch):
    native_root(monkeypatch)
    from sglang.srt.sampling.sampling_batch_info import SamplingBatchInfo

    info = filtering_batch()
    original_outputs = [list(row["__req__"].output_ids) for row in info.custom_params]
    filter_position_batch(
        SamplingBatchInfo._filter_batch_custom_logit_processor,
        info, [2, 0], torch.tensor([2, 0]),
    )
    other = filtering_batch()
    key = hash("qualified-replay")
    merged = SamplingBatchInfo.merge_custom_logit_processor(
        info.custom_logit_processor, other.custom_logit_processor,
        2, 3, "cpu",
    )
    assert merged[key][1].tolist() == [True] * 5
    forward = batch(info.custom_params, [16, 16])
    assert sample_with_positions(native_sample, None, torch.zeros(2, 16), forward).tolist() == [11, 3]
    assert original_outputs == [[], [], []]
    assert [row["__req__"].output_ids for row in info.custom_params] == [[], []]
