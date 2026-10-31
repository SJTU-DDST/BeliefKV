from copy import deepcopy

import pytest

from scripts.audit_previous_prefix_prefill import inspect


def probe():
    return {
        "event": "context_prefix_reuse_probe", "request_id": "r", "workflow_id": "w",
        "invocation_id": "a", "context_id": "c", "context_epoch": 0,
        "common_previously_served_input_tokens": 10, "native_cached_input_tokens": 2,
        "previously_served_prefix_recompute_proxy_tokens": 8,
    }


def batch(identity, start, length):
    return {
        "event": "gpu_service_sample", "sample_id": identity, "phase": "prefill",
        "service_elapsed_ms": 5.,
        "request_samples": [{
            "request_id": "r", "workflow_id": "w", "invocation_id": "a", "context_id": "c",
            "context_epoch": 0, "attempt_id": 0, "session_id": "s", "session_generation": 1,
            "sequence_tokens_before": start, "token_delta": length,
            "token_delta_semantics": "prefill_extend_input_len",
        }],
    }


def test_cache_probe_is_grounded_in_actual_input_ranges_excluding_new_input():
    result = inspect([probe()], [batch("first", 2, 5), batch("second", 7, 7)])
    assert result["counts"]["supported_distinct_old_prefix_extend_tokens"] == 8
    assert result["cases"][0]["sampled_prefill_extend_tokens"] == 12
    assert result["cases"][0]["status"] == "fully_observed_old_prefix_extend"
    assert result["affected_whole_batch_worker_intervals_ms"] == 10.


def test_overlapping_or_retracted_ranges_are_not_counted_twice_in_distinct_tokens():
    result = inspect([probe()], [batch("first", 2, 6), batch("second", 6, 4)])
    assert result["counts"]["supported_distinct_old_prefix_extend_tokens"] == 8
    assert result["counts"]["supported_old_prefix_extend_tokens_including_repeated_ranges"] == 10


@pytest.mark.parametrize("change", ("missing_attempt", "multiple_attempts", "invalid_geometry"))
def test_unsupported_identity_or_geometry_does_not_claim_complete_recomputation(change):
    rows = [batch("first", 2, 4), batch("second", 6, 4)]
    if change == "missing_attempt":
        rows[1]["request_samples"][0]["session_id"] = None
    elif change == "multiple_attempts":
        rows[1]["request_samples"][0]["attempt_id"] = 1
    else:
        rows[1]["request_samples"][0]["token_delta_semantics"] = "unknown"
    result = inspect([probe()], rows)
    assert result["cases"][0]["status"] == "unknown_identity_or_geometry"
    assert result["counts"].get("supported_distinct_old_prefix_extend_tokens", 0) == 0
    assert result["batches_with_old_prefix_extend"] == 0


def test_unobserved_and_partial_ranges_remain_explicit():
    assert inspect([probe()], [])["cases"][0]["status"] == "unknown_identity_or_geometry"
    result = inspect([probe()], [batch("first", 2, 3)])
    assert result["cases"][0]["status"] == "partially_observed_old_prefix_extend"
    assert result["counts"]["supported_distinct_old_prefix_extend_tokens"] == 3


@pytest.mark.parametrize("change", ("probe", "batch", "ownership"))
def test_duplicate_observations_and_foreign_ownership_are_rejected(change):
    probes, batches = [probe()], [batch("first", 2, 8)]
    if change == "probe":
        probes.append(deepcopy(probes[0]))
    elif change == "batch":
        batches.append(deepcopy(batches[0]))
    else:
        batches[0]["request_samples"][0]["context_epoch"] = 1
    with pytest.raises(ValueError):
        inspect(probes, batches)
