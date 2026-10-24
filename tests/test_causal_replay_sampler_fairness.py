from copy import deepcopy
from dataclasses import replace
import time
from types import SimpleNamespace as NS

import pytest
import torch

from beliefkv.experiments.causal_replay_backend import request_payload, verify_native_receipt
from beliefkv.experiments.causal_replay_native import terminal_receipt
from beliefkv.experiments.causal_replay_sampling import (
    PARAMETER_KEY, POSITION_KEY, apply_position_logits, sample_with_positions,
)
from beliefkv.experiments.causal_replay_tokens import CapturedTokenCorpus
from beliefkv.runtime.clock_evidence import local_monotonic_clock_domain
from beliefkv.runtime.replay_token_capture import capture_record, generation_parameters
from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata
from beliefkv.runtime.v0520_native_telemetry import NativeReactiveTelemetry
from tests.test_causal_replay_backend import native_request, response_payload, submitted
from tests.test_causal_replay_position_sampling import batch
from tests.test_causal_replay_sampling import parameters
from tests.test_causal_replay_tokens import source, write_source
from tests.test_replay_token_capture import request


CODING = {
    "temperature": .6, "top_p": .95, "top_k": 20, "min_p": 0.,
    "frequency_penalty": 0., "presence_penalty": 0., "repetition_penalty": 1.,
    "sampling_seed": 21,
}


def coding_source(tmp_path):
    records, expected, _ = source(count=1, width=16)
    for row in records:
        row["generation_parameters"] = deepcopy(CODING)
    path = tmp_path / "tokens.jsonl"
    write_source(path, records)
    return CapturedTokenCorpus(path, expected)


def test_new_capture_keeps_effective_sampler_and_does_not_label_old_metadata_complete(tmp_path):
    assert generation_parameters(NS(**CODING)) == CODING
    assert generation_parameters(NS(max_new_tokens=32)) is None
    with coding_source(tmp_path) as corpus:
        captured = corpus.get("r0")
        assert captured.generation_parameters == CODING
        assert corpus.evidence["original_generation_parameters_complete"]
        payload = request_payload(
            captured, submitted(), BeliefKVRequestMetadata("w", "i", "c", 0), processor="capture",
        )
        assert {name: payload["sampling_params"][name] for name in CODING} == CODING
        assert payload["sampling_params"]["custom_params"][PARAMETER_KEY]["force_after_sample"]
        payload["sampling_params"]["temperature"] = 0.
        assert corpus.get("r0").generation_parameters == CODING


@pytest.mark.parametrize("change", (
    "temperature", "top_p", "top_k", "min_p", "frequency_penalty",
    "presence_penalty", "repetition_penalty", "sampling_seed", "missing", "changed_output",
))
def test_corrupt_or_changed_sampling_metadata_cannot_qualify_tokens(tmp_path, change):
    records, expected, _ = source(count=1, width=16)
    for row in records:
        row["generation_parameters"] = deepcopy(CODING)
    if change == "missing":
        del records[0]["generation_parameters"]["top_k"]
    elif change == "changed_output":
        records[1]["generation_parameters"]["temperature"] = 1.
    else:
        records[0]["generation_parameters"][change] = True
    path = tmp_path / "invalid.jsonl"
    write_source(path, records)
    with pytest.raises(ValueError, match="generation"):
        CapturedTokenCorpus(path, expected)


def test_captured_seed_cannot_silently_be_replaced(tmp_path):
    with coding_source(tmp_path) as corpus:
        event = replace(submitted(), attributes={"request_id": "r0", "sampling_seed": 22})
        with pytest.raises(ValueError, match="captured sampling seed"):
            request_payload(
                corpus.get("r0"), event, BeliefKVRequestMetadata("w", "i", "c", 0),
                processor="capture",
            )


def test_real_capture_and_receipt_snapshot_effective_native_parameters(tmp_path, monkeypatch):
    monkeypatch.setenv("BELIEFKV_REPLAY_TOKEN_CAPTURE", "1")
    telemetry = NativeReactiveTelemetry(tmp_path / "server")
    req = request("sample", [1, 2, 3])
    req.sampling_params = NS(max_new_tokens=32, **CODING)
    emitted = []
    telemetry._emit = lambda _, row: emitted.append(row)
    try:
        telemetry._queue_context_prefix(req, telemetry._identity(req), served=False)
        req.sampling_params.temperature = 0.
        row = capture_record(emitted[0], [1, 2, 3], event="request_prompt")
        assert row["generation_parameters"] == CODING
    finally:
        telemetry.close()
    with coding_source(tmp_path) as corpus:
        req = native_request(corpus.get("r0"))
        for name, value in CODING.items():
            setattr(req.sampling_params, name, value)
        req._beliefkv_native_sampler_preserved = True
        receipt = terminal_receipt(req)
        req.sampling_params.top_k = 1
        assert receipt["generation_parameters"] == CODING
        assert receipt["native_sampler_preserved"]


@pytest.mark.parametrize("dtype", (torch.int32, torch.int64))
@pytest.mark.parametrize("mixed", (False, True))
def test_post_sample_override_keeps_real_logits_native_sampling_and_forward_progress(dtype, mixed):
    replay = parameters("a", [3, 4, 9])
    replay[PARAMETER_KEY]["force_after_sample"] = True
    rows = [None, replay] if mixed else [replay]
    forward = batch(rows, [1, 17] if mixed else [17])
    forward.return_logprob = False
    logits = torch.arange(len(rows) * 16, dtype=torch.float32).view(len(rows), 16)
    before = logits.clone()
    sampled = []

    def native(_runner, output, actual):
        apply_position_logits(
            lambda *_: pytest.fail("legacy mask dispatch"),
            output.next_token_logits, actual.sampling_info,
        )
        assert torch.equal(output.next_token_logits, before)
        # Deliberately choose another token through an unmodified sampling step.
        result = output.next_token_logits.softmax(-1).argmax(-1).to(dtype)
        sampled.extend(result.tolist())
        return result

    result = sample_with_positions(native, None, NS(next_token_logits=logits), forward)
    assert sampled == [15] * len(rows)
    assert result.tolist() == ([15, 4] if mixed else [4])
    assert torch.equal(logits, before)
    assert replay["__req__"].output_ids == []
    assert replay["__req__"]._beliefkv_native_sampler_preserved
    assert not hasattr(forward.sampling_info, POSITION_KEY)


def test_post_sample_terminal_overlap_does_not_replace_discarded_native_result():
    replay = parameters("a", [3, 9], [3])
    replay[PARAMETER_KEY]["force_after_sample"] = True
    forward = batch([replay], [18])
    logits = torch.zeros(1, 16)

    def native(_runner, output, actual):
        apply_position_logits(lambda *_: None, output.next_token_logits, actual.sampling_info)
        return torch.tensor([11], dtype=torch.int32)

    assert sample_with_positions(native, None, NS(next_token_logits=logits), forward).tolist() == [11]


@pytest.mark.parametrize("change", (None, "greedy", "seed", "missing_proof"))
def test_receipt_checks_actual_native_sampler_settings_and_post_sample_evidence(tmp_path, change):
    with coding_source(tmp_path) as corpus:
        captured = corpus.get("r0")
        started = time.monotonic() * 1000.
        # The historical test fixture has no generation settings.
        payload = response_payload(replace(captured, generation_parameters=None))
        receipt = payload["meta_info"]["beliefkv_causal_replay_receipt"][0]
        receipt["generation_parameters"] = deepcopy(CODING)
        receipt["native_sampler_preserved"] = True
        if change == "greedy":
            receipt["generation_parameters"]["top_k"] = 1
        elif change == "seed":
            receipt["generation_parameters"]["sampling_seed"] = 22
        elif change == "missing_proof":
            receipt["native_sampler_preserved"] = False
        kwargs = {
            "submitted_ms": started, "now_ms": time.monotonic() * 1000.,
            "clock_domain": local_monotonic_clock_domain(),
        }
        if change is None:
            assert verify_native_receipt(captured, payload, **kwargs).output_tokens == 2
        else:
            with pytest.raises(ValueError, match="original sampler"):
                verify_native_receipt(captured, payload, **kwargs)
