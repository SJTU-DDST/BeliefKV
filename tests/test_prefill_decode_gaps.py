from copy import deepcopy

import pytest

from scripts.audit_prefill_decode_gaps import inspect


def batch(index, phase, start, end, requests, *, before=0, delta=1):
    return {
        "event": "gpu_service_sample", "sample_id": f"native-service-{index:09d}",
        "forward_iter": index, "phase": phase, "batch_size": len(requests),
        "service_start_monotonic_ms": float(start), "complete_monotonic_ms": float(end),
        "monotonic_clock_domain": "one-clock",
        "request_samples": [{
            "request_id": request, "workflow_id": "wf-" + request,
            "invocation_id": "agent-" + request, "context_id": "ctx-" + request,
            "context_epoch": 0, "phase": phase, "output_tokens_before": before,
            "token_delta": delta,
            "token_delta_semantics": (
                "observed_output_ids_delta" if phase == "decode" else "prefill_extend_input_len"
            ),
        } for request in requests],
    }


def samples():
    return [
        batch(1, "decode", 0, 5, ("a", "b")),
        batch(2, "prefill", 5, 15, ("c",)),
        batch(3, "prefill", 15, 25, ("c",)),
        batch(4, "decode", 25, 30, ("a", "b"), before=1),
    ]


def test_positive_generation_bookends_identify_unrelated_prefill_intervals():
    result = inspect(iter(samples()))
    assert result["quiet_decode_gaps"] == 1
    assert result["prefill_worker_ms_in_quiet_decode_gaps"] == 20.
    assert result["quiet_request_prefill_interval_ms"] == 40.
    assert result["decode_bookend_gap_ms"]["p50"] == 20.
    assert len(result["rows"][0]["quiet_continuing_requests"]) == 2


def test_reentry_prefill_does_not_establish_continuous_decode_service():
    rows = samples()
    rows[1] = batch(2, "prefill", 5, 15, ("a", "c"))
    result = inspect(rows)
    assert result["quiet_request_prefill_interval_ms"] == 20.
    quiet = result["rows"][0]["quiet_continuing_requests"]
    assert [key[0] for key in quiet] == ["b"]


def test_mixed_prefill_decode_service_excludes_its_members_from_quiet_gaps():
    rows = samples()
    for index in (1, 2):
        for request in ("a", "b"):
            member = batch(10, "decode", 0, 1, (request,), before=index)["request_samples"][0]
            rows[index]["request_samples"].append(member)
        rows[index]["batch_size"] += 2
    rows[-1] = batch(4, "decode", 25, 30, ("a", "b"), before=3)
    result = inspect(rows)
    assert result["quiet_decode_gaps"] == 0
    assert result["counts"]["decode_members_in_prefill_batches"] == 4


def test_zero_output_residue_is_not_a_positive_generation_bookend():
    rows = samples()
    for member in rows[-1]["request_samples"]:
        member["token_delta"] = 0
    assert inspect(rows)["quiet_decode_gaps"] == 0


def test_prefill_tail_and_initial_prefill_are_preserved_without_fabricated_bookends():
    rows = [batch(0, "prefill", 0, 2, ("x",)), *samples()]
    rows.append(batch(5, "prefill", 30, 35, ("z",)))
    result = inspect(rows)
    assert result["counts"]["prefill_batches_before_first_decode"] == 1
    assert result["counts"]["trailing_prefill_batches_without_decode_bookend"] == 1
    assert result["prefill_worker_interval_union_ms"] == 27.
    assert result["prefill_worker_ms_in_quiet_decode_gaps"] == 20.


def test_overlapping_worker_intervals_are_clipped_to_the_decode_bookends():
    rows = [
        batch(1, "decode", 0, 10, ("a",)),
        batch(2, "prefill", 8, 20, ("c",)),
        batch(3, "decode", 15, 25, ("a",), before=1),
    ]
    result = inspect(rows)
    assert result["prefill_worker_ms_in_quiet_decode_gaps"] == 5.
    assert result["prefill_worker_interval_union_ms"] == 12.


@pytest.mark.parametrize("fault", (
    "clock", "reversed_time", "duplicate_sample", "duplicate_forward",
    "duplicate_member", "owner", "unobserved_delta", "negative_delta", "size",
))
def test_unproven_ownership_work_or_clock_is_rejected(fault):
    rows = deepcopy(samples())
    if fault == "clock":
        rows[-1]["monotonic_clock_domain"] = "another-clock"
    elif fault == "reversed_time":
        rows[-1]["service_start_monotonic_ms"] = 10.
    elif fault == "duplicate_sample":
        rows[-1]["sample_id"] = rows[0]["sample_id"]
    elif fault == "duplicate_forward":
        rows[-1]["forward_iter"] = rows[0]["forward_iter"]
    elif fault == "duplicate_member":
        rows[-1]["request_samples"].append(rows[-1]["request_samples"][0])
        rows[-1]["batch_size"] += 1
    elif fault == "owner":
        rows[-1]["request_samples"][0]["context_epoch"] = 1
    elif fault == "unobserved_delta":
        rows[-1]["request_samples"][0]["token_delta_semantics"] = "predicted"
    elif fault == "negative_delta":
        rows[-1]["request_samples"][0]["token_delta"] = -1
    else:
        rows[-1]["batch_size"] = 3
    with pytest.raises(ValueError):
        inspect(rows)
