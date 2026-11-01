import json

import pytest

from scripts.profile_native_token_cost import (
    checked_response, cold_prefill_case, finish_prefill_profile, payload, trace_summary,
)


def test_batch_has_separate_observed_identities_and_fixed_numerical_probe():
    request = payload({"width": 2, "members": [{"tokens": [1, 2, 3]}, {"tokens": [4, 5, 6]}]}, "measure0", 96)
    assert request["input_ids"] == [[1, 2], [4, 5]]
    assert len(set(request["rid"])) == 2
    assert len(request["beliefkv_metadata"]) == 2
    assert request["sampling_params"]["max_new_tokens"] == 96
    assert request["sampling_params"]["ignore_eos"]


def test_real_kernel_events_only_and_union_does_not_double_count(tmp_path):
    path = tmp_path / "trace.json"
    path.write_text(json.dumps({"traceEvents": [
        {"cat": "kernel", "ph": "X", "name": "a", "ts": 0, "dur": 10},
        {"cat": "kernel", "ph": "X", "name": "b", "ts": 5, "dur": 10},
        {"cat": "cpu_op", "ph": "X", "name": "cpu", "ts": 0, "dur": 100},
    ]}))
    result = trace_summary(path)
    assert result["kernel_duration_sum_ms"] == .02
    assert result["kernel_interval_union_ms"] == .015
    assert len(result["top_kernels"]) == 2


def test_missing_outputs_are_rejected():
    with pytest.raises(ValueError, match="one response"):
        checked_response([], {"rid": ["r"]}, 1)


def test_cold_prefill_uses_one_unchanged_captured_prefix():
    small = {"tokens": [1] * 1024, "request_id": "small"}
    selected = {"tokens": [2] * 66000, "request_id": "selected"}
    large = {"tokens": [3] * 200000, "request_id": "large"}
    result = cold_prefill_case({"cases": [
        {"width": 2, "members": [small, selected]},
        {"width": 1, "members": [large]},
    ]})
    assert result["width"] == 1
    assert result["captured_from_width"] == 2
    assert result["members"][0] is selected
    request = payload(result, "prefill_measure0", 1)
    assert request["input_ids"] == [selected["tokens"][:-1]]


def test_native_automatic_export_is_not_stopped_twice(tmp_path):
    directory = tmp_path / "profile"
    directory.mkdir()
    (directory / "auto.trace.json").write_text(json.dumps({"traceEvents": [
        {"cat": "kernel", "ph": "X", "name": "a", "ts": 0, "dur": 10},
    ]}))

    class NoStop:
        def post(self, path):
            raise AssertionError("already exported profile must not be stopped twice")

    assert finish_prefill_profile(NoStop(), tmp_path)["automatic_export_already_complete"]


def test_empty_export_cannot_qualify_automatic_completion(tmp_path):
    directory = tmp_path / "profile"
    directory.mkdir()
    (directory / "auto.trace.json").write_text('{"traceEvents": []}')
    with pytest.raises(ValueError, match="no actual CUDA"):
        finish_prefill_profile(None, tmp_path)
