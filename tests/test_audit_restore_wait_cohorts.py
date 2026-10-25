from copy import deepcopy

import pytest

from scripts.audit_restore_wait_cohorts import analyze, audit


def sample(ids=("tool",), *, offsets=True):
    row = {
        "event": "gpu_restore_dependency_wait", "forward_iter": 1,
        "request_ids": list(ids), "batch_size": len(ids), "sampling_interval": 16,
        "gpu_layer_dependency_wait_ms": 63., "gpu_observed_layer_span_ms": 90.,
        "layers": [
            {"layer": -1, "gpu_wait_ms": 2.},
            {"layer": 2, "gpu_wait_ms": 1.},
            {"layer": 5, "gpu_wait_ms": 60.},
        ],
    }
    if offsets:
        for layer, begin, end in zip(row["layers"], (0., 12., 30.), (2., 13., 90.)):
            layer.update(gpu_wait_start_offset_ms=begin, gpu_wait_end_offset_ms=end)
    return row


COHORTS = {"tool": "post_tool", "join": "post_join"}
ROLES = {"tool": "child", "join": "root"}
PROMPTS = {"tool": 10_000, "join": 110_000}


def test_mixed_batch_wait_is_counted_once_with_no_request_level_attribution():
    result = analyze([sample(("tool", "join"))], COHORTS, ROLES, PROMPTS)
    assert result["summary"]["sampled_batches"] == 1
    assert result["summary"]["distinct_sampled_requests"] == 2
    assert result["summary"]["total_wait_ms"]["sum"] == 63.
    assert list(result["by_successor_cohort"]) == ["mixed:post_join+post_tool"]
    assert list(result["by_invocation_role"]) == ["mixed:child+root"]
    assert list(result["by_prompt_class"]) == ["some_ge_100k"]
    assert result["summary"]["between_recorded_waits_ms"]["sum"] == 27.
    assert result["summary"]["first_observed_full_wait_ms"]["sum"] == 1.
    assert result["summary"]["later_observed_full_wait_ms"]["sum"] == 60.
    assert result["summary"]["batches_with_later_full_wait_over_50ms"] == 1


def test_legacy_samples_keep_unknown_offsets_without_inventing_them():
    result = analyze([sample(offsets=False)], {}, {}, {})
    assert result["summary"]["batches_with_layer_offsets"] == 0
    assert list(result["by_successor_cohort"]) == ["unknown"]
    assert list(result["by_prompt_class"]) == ["unknown"]
    assert result["summary"]["sampling_intervals"] == {16: 1}


@pytest.mark.parametrize("change", (
    "duplicate_request", "wrong_batch_size", "duplicate_layer", "negative_wait",
    "bad_total", "bad_span", "partial_offset", "reversed_offset", "offset_wrong_wait",
    "boolean_batch_size",
))
def test_invalid_membership_waits_and_offsets_do_not_silently_become_evidence(change):
    row = deepcopy(sample())
    if change == "duplicate_request":
        row["request_ids"] *= 2
        row["batch_size"] = 2
    elif change == "wrong_batch_size":
        row["batch_size"] = 2
    elif change == "duplicate_layer":
        row["layers"][2]["layer"] = 2
    elif change == "negative_wait":
        row["layers"][2]["gpu_wait_ms"] = -60.
    elif change == "bad_total":
        row["gpu_layer_dependency_wait_ms"] = 5.
    elif change == "bad_span":
        row["gpu_observed_layer_span_ms"] = 5.
    elif change == "partial_offset":
        row["layers"][0].pop("gpu_wait_start_offset_ms")
    elif change == "reversed_offset":
        row["layers"][1]["gpu_wait_start_offset_ms"] = 1.
        row["layers"][1]["gpu_wait_end_offset_ms"] = 2.
    elif change == "offset_wrong_wait":
        row["layers"][1]["gpu_wait_end_offset_ms"] = 14.
    elif change == "boolean_batch_size":
        row["batch_size"] = True
    with pytest.raises(ValueError):
        analyze([row], COHORTS, ROLES, PROMPTS)


@pytest.mark.parametrize("missing", ("runtime_events.sglang.jsonl", "runtime_audit.jsonl"))
def test_missing_native_inputs_are_not_reported_as_zero_restore_wait(tmp_path, missing):
    replay = tmp_path / "replay"
    replay.mkdir()
    (replay / "runtime_events.replay.jsonl").touch()
    server = tmp_path / "server"
    server.mkdir()
    for name in ("runtime_events.sglang.jsonl", "runtime_audit.jsonl"):
        if name != missing:
            (server / name).touch()
    with pytest.raises(ValueError, match="missing native restore observation"):
        audit(tmp_path)
