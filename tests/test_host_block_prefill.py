from array import array
from copy import deepcopy
import time
from types import SimpleNamespace as NS

import pytest

from scripts.audit_host_block_prefill import cohort_membership, inspect
from tests.test_previous_prefix_prefill import batch


def loss(*, end=10, units=8, lost=8, eviction=1):
    return {
        "event": "host_block_reaccess_attributed", "pool": "full",
        "request_id": "r", "workflow_id": "w", "invocation_id": "a",
        "context_id": "c", "context_epoch": 0, "ts_ms": 10.,
        "prefix_tokens": end, "evicted_units": units, "full_recomputed_units": lost,
        "full_device_hit_units": units - lost, "full_host_hit_units": 0,
        "outcome": "full_partial_hit_and_recompute" if units > lost else "full_recompute",
        "eviction_ids": {"first": eviction, "last": eviction},
        "prefix_hmac_blake2b128": f"{end:032x}",
        "cache_namespace_hmac_blake2b128": "b" * 32,
        "attribution_semantics": "exact_radix_prefix_and_full_token_interval",
    }


def test_fixed_cohort_groups_preserve_registered_population():
    assert cohort_membership({
        "kind": "fixed_arrival_index_workflow_cohort",
        "measured_workflow_ids": ["m"], "warmup_workflow_ids": ["w"],
        "background_workflow_ids": ["b"],
    }, ["w", "m", "b"]) == {"m": "measured", "w": "warmup", "b": "background"}


@pytest.mark.parametrize("change", ("campaign_plan", "duplicate", "missing_member", "empty_measured"))
def test_wrong_cohort_or_population_is_rejected_before_native_log_analysis(change):
    cohort = {
        "kind": "fixed_arrival_index_workflow_cohort",
        "measured_workflow_ids": ["m"], "warmup_workflow_ids": ["w"],
        "background_workflow_ids": ["b"],
    }
    if change == "campaign_plan":
        cohort = {"stages": {"control": {}}, "cohort": "measurement.json"}
    elif change == "duplicate":
        cohort["background_workflow_ids"].append("m")
    elif change == "missing_member":
        cohort["background_workflow_ids"].clear()
    else:
        cohort["measured_workflow_ids"].clear()
    with pytest.raises(ValueError):
        cohort_membership(cohort, ["w", "m", "b"])


def test_actual_input_intersections_merge_overlapping_Host_loss_intervals():
    report = inspect(
        [loss(), loss(end=8, units=4, lost=4, eviction=2)],
        [batch("one", 2, 5), batch("two", 7, 7)],
    )
    case, = report["cases"]
    assert case["attributed_tokens_including_overlaps"] == 12
    assert case["distinct_attributed_tokens"] == 8
    assert case["sampled_prefill_extend_tokens"] == 12
    assert case["distinct_observed_prefill_tokens"] == 8
    assert case["status"] == "fully_observed_Host_loss_prefill"
    assert report["affected_whole_batch_worker_intervals_ms"] == 10.


def test_partial_native_tier_hit_reconstructs_only_the_uncached_tail():
    report = inspect([loss(units=8, lost=3)], [batch("one", 6, 8)])
    assert report["counts"]["supported_distinct_executed_prefill_tokens"] == 3
    assert report["cases"][0]["sampled_prefill_extend_tokens"] == 8


def test_retracted_or_overlapping_prefill_ranges_are_not_counted_twice():
    report = inspect([loss()], [batch("one", 2, 6), batch("two", 6, 4)])
    assert report["counts"]["supported_distinct_executed_prefill_tokens"] == 8
    assert report["counts"]["supported_executed_tokens_including_repeated_ranges"] == 10


def test_partial_and_unobserved_service_remain_explicit():
    missing = inspect([loss()], [])
    assert missing["cases"][0]["status"] == "unknown_identity_or_geometry"
    partial = inspect([loss()], [batch("one", 2, 3)])
    assert partial["cases"][0]["status"] == "partially_observed_Host_loss_prefill"
    assert partial["counts"]["supported_distinct_executed_prefill_tokens"] == 3


@pytest.mark.parametrize("change", (
    "missing_attempt", "multiple_attempts", "multiple_sessions", "geometry", "multiple_probes",
))
def test_unsupported_native_identity_or_geometry_does_not_prove_executed_tokens(change):
    probes, batches = [loss()], [batch("one", 2, 4), batch("two", 6, 4)]
    if change == "missing_attempt":
        batches[1]["request_samples"][0]["attempt_id"] = None
    elif change == "multiple_attempts":
        batches[1]["request_samples"][0]["attempt_id"] = 1
    elif change == "multiple_sessions":
        batches[1]["request_samples"][0]["session_generation"] = 2
    elif change == "geometry":
        batches[1]["request_samples"][0]["token_delta_semantics"] = "unknown"
    else:
        probes.append({**loss(end=9, units=3, lost=3, eviction=2), "ts_ms": 11.})
    report = inspect(probes, batches)
    assert report["cases"][0]["status"] == "unknown_identity_or_geometry"
    assert report["counts"].get("supported_distinct_executed_prefill_tokens", 0) == 0
    assert report["affected_prefill_batches"] == 0


@pytest.mark.parametrize("change", (
    "ownership", "epoch", "namespace", "prefix", "duplicate_eviction", "duplicate_batch", "budget",
))
def test_conflicting_or_duplicate_evidence_is_rejected(change):
    probes, batches = [loss()], [batch("one", 2, 8)]
    if change == "ownership":
        batches[0]["request_samples"][0]["invocation_id"] = "foreign"
    elif change == "epoch":
        batches[0]["request_samples"][0]["context_epoch"] = 1
    elif change == "namespace":
        probes.append({**loss(end=8, units=3, lost=3, eviction=2),
                       "cache_namespace_hmac_blake2b128": "c" * 32})
    elif change == "prefix":
        probes.append({**loss(eviction=2), "prefix_hmac_blake2b128": "d" * 32})
    elif change == "duplicate_eviction":
        probes.append(deepcopy(probes[0]))
    elif change == "duplicate_batch":
        batches.append(deepcopy(batches[0]))
    else:
        probes[0]["full_recomputed_units"] += 1
    with pytest.raises(ValueError):
        inspect(probes, batches)


def test_native_mamba_attribution_does_not_imply_full_prefill_work():
    report = inspect([{"event": "host_block_reaccess_attributed", "pool": "mamba"}], [])
    assert report["counts"]["nonfull_attributions_excluded"] == 1
    assert not report["cases"]


def test_real_native_telemetry_partial_hit_is_grounded_in_its_prefill_geometry(tmp_path):
    from beliefkv.runtime.v0520_native_telemetry import NativeReactiveTelemetry
    from tests.test_v0520_native_telemetry import _Mode, _read

    output = tmp_path / "native"
    audit = NativeReactiveTelemetry(output)
    audit._host_pool_geometry = {
        "full": {"capacity_units": 100, "bytes_per_unit": 16},
    }
    key = NS(
        raw_token_ids=lambda: array("q", range(1, 9)),
        extra_key="tenant", cache_salt="salt", is_bigram=False,
    )
    audit.on_native_host_block_eviction(NS(id=8, parent=NS(parent=None, key=None), key=key), 0, 4)
    request = NS(
        rid="r", origin_input_ids=array("q", range(1, 9)), output_ids=[],
        beliefkv_metadata={
            "root_workflow_id": "w", "invocation_id": "a", "context_id": "c",
            "context_epoch": 0, "attempt_id": 0,
        },
        session_id="session", session_generation=1, extra_key="tenant", cache_salt="salt",
        cache_request_handle=NS(attempt_id=0),
        cached_tokens_device=5, cached_tokens_host=1, mamba_host_hit_length=0,
        extend_input_len=2, sampling_params=NS(max_new_tokens=8), finished=lambda: False,
    )
    native_batch = NS(
        forward_mode=_Mode("prefill"), launch_ts=time.monotonic(), forward_iter=1, reqs=[request],
    )
    try:
        audit.on_enqueue(request)
        audit.on_launch(native_batch)
        audit.on_completed(native_batch)
    finally:
        audit.close()
    report = inspect(
        _read(output / "eviction_attribution.jsonl"), _read(output / "runtime_audit.jsonl"),
    )
    assert report["counts"]["supported_distinct_executed_prefill_tokens"] == 2
    assert report["cases"][0]["distinct_attributed_tokens"] == 2
    assert report["cases"][0]["status"] == "fully_observed_Host_loss_prefill"
