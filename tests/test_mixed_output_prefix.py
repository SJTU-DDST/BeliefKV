from copy import deepcopy

import pytest

from scripts.audit_mixed_output_prefix import inspect


def evidence():
    def member(rid, before, delta):
        return {
            "request_id": rid, "workflow_id": "wf", "invocation_id": rid,
            "context_id": rid, "context_epoch": 0, "phase": "decode",
            "output_tokens_reported_before": before, "output_tokens_after": before + delta,
            "output_token_delta": delta,
        }

    batches = [{
        "event": "gpu_service_sample", "sample_id": str(index), "forward_iter": index,
        "phase": phase, "batch_size": len(members), "decode_batch_size": len(members),
        "request_samples": members, "complete_monotonic_ms": float(index * 10),
        "service_elapsed_ms": 2., "monotonic_clock_domain": "clock",
        "output_token_accounting": "observed_output_ids_delta_all_forward_modes_v1",
    } for index, phase, members in (
        (1, "decode", [member("a", 0, 1), member("b", 0, 1)]),
        (2, "prefill", [member("a", 1, 1), member("b", 1, 1)]),
        (3, "decode", [member("a", 2, 0), member("b", 2, 1)]),
    )]
    events = [{
        "kind": "llm_result", "workflow_id": "wf", "invocation_id": "a",
        "context_id": "a", "context_epoch": 0, "attributes": {
            "request_id": "a", "complete_monotonic_ms": 20.,
            "monotonic_clock_domain": "clock", "output_tokens": 2,
        },
    }]
    receipts = [{
        "request_id": "a", "output_tokens": 2, "exact_tokens_verified": True,
        "native_receipt": {
            "request_id": "a", "identity": ["wf", "a", "a", 0], "clock_domain": "clock",
            "native_sampler_preserved": True, "sampling_path": "position",
            "output_token_ids": [7, 8],
        },
    }]
    return batches, events, receipts


def test_collecting_mixed_outputs_keep_open_request_separate_from_completed_receipt():
    result = inspect(*evidence())
    assert result["observed_total_output_tokens"] == 5
    assert result["exact_receipts_in_common_prefix"] == 1
    assert result["requests_without_observed_native_completion"] == 1
    assert result["counts"]["decode_output_tokens_inside_prefill"] == 2
    assert result["worker_intervals"] == {"decode_worker_ms": 4., "mixed_prefill_worker_ms": 2.}
    assert result["complete_output_conservation"] is False
    with pytest.raises(ValueError):
        inspect(*evidence(), complete=True)


def test_complete_conservation_requires_all_native_and_client_completions():
    batches, events, receipts = evidence()
    event, receipt = deepcopy(events[0]), deepcopy(receipts[0])
    event.update(invocation_id="b", context_id="b")
    event["attributes"].update(request_id="b", output_tokens=3, complete_monotonic_ms=30.)
    receipt.update(request_id="b", output_tokens=3)
    receipt["native_receipt"].update(
        request_id="b", identity=["wf", "b", "b", 0], output_token_ids=[1, 2, 3],
    )
    result = inspect(batches, [*events, event], [*receipts, receipt], complete=True)
    assert result["complete_output_conservation"]
    assert result["common_completed_output_tokens"] == result["observed_total_output_tokens"] == 5


def test_later_native_and_client_records_remain_outside_the_common_frontier():
    batches, events, receipts = evidence()
    events[0]["attributes"]["complete_monotonic_ms"] = 40.
    result = inspect(batches, events, receipts)
    assert result["exact_receipts_in_common_prefix"] == 0
    assert result["counts"]["native_completions_after_forward_frontier"] == 1
    assert result["counts"]["receipts_outside_common_completion_prefix"] == 1


def test_missing_client_receipt_is_open_coverage_not_a_zero_workflow():
    batches, events, _receipts = evidence()
    result = inspect(batches, events, [])
    assert result["native_completions_without_client_receipts"] == 1
    assert not result["complete_output_conservation"]


def test_complete_mode_rejects_a_native_completion_beyond_the_forward_frontier():
    batches, events, receipts = evidence()
    batches = batches[:1]
    batches[0]["request_samples"] = batches[0]["request_samples"][:1]
    batches[0].update(batch_size=1, decode_batch_size=1)
    events[0]["attributes"].update(output_tokens=1, complete_monotonic_ms=10.)
    receipts[0]["output_tokens"] = 1
    receipts[0]["native_receipt"]["output_token_ids"] = [7]
    later = deepcopy(events[0])
    later.update(invocation_id="later", context_id="later")
    later["attributes"].update(request_id="later", complete_monotonic_ms=40.)
    with pytest.raises(ValueError, match="unmatched native/client"):
        inspect(batches, [*events, later], receipts, complete=True)


@pytest.mark.parametrize("fault", (
    "missing_ordinal", "duplicate_forward", "duplicate_sample", "duplicate_member",
    "batch_size", "decode_membership", "clock", "ownership", "native_count",
    "duplicate_completion", "receipt_count", "receipt_identity", "receipt_clock",
    "duplicate_receipt", "unverified_receipt",
))
def test_unproven_mixed_progress_or_completion_is_rejected(fault):
    batches, events, receipts = evidence()
    if fault == "missing_ordinal":
        del batches[-1]["request_samples"][0]["output_tokens_after"]
    elif fault == "duplicate_forward":
        batches[-1]["forward_iter"] = 1
    elif fault == "duplicate_sample":
        batches[-1]["sample_id"] = "1"
    elif fault == "duplicate_member":
        batches[-1]["request_samples"][1] = deepcopy(batches[-1]["request_samples"][0])
    elif fault == "batch_size":
        batches[-1]["batch_size"] = 4
    elif fault == "decode_membership":
        batches[1]["decode_batch_size"] = 1
    elif fault == "clock":
        batches[-1]["monotonic_clock_domain"] = "other"
    elif fault == "ownership":
        batches[-1]["request_samples"][0]["context_epoch"] = 1
    elif fault == "native_count":
        events[0]["attributes"]["output_tokens"] = 3
    elif fault == "duplicate_completion":
        events.append(deepcopy(events[0]))
    elif fault == "receipt_count":
        receipts[0]["output_tokens"] = 3
    elif fault == "receipt_identity":
        receipts[0]["native_receipt"]["identity"][-1] = 1
    elif fault == "receipt_clock":
        receipts[0]["native_receipt"]["clock_domain"] = "other"
    elif fault == "duplicate_receipt":
        receipts.append(deepcopy(receipts[0]))
    else:
        receipts[0]["exact_tokens_verified"] = False
    with pytest.raises(ValueError):
        inspect(batches, events, receipts)
