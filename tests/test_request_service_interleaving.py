from copy import deepcopy

import pytest

from scripts.audit_request_service_interleaving import account, aggregate, path_partition


def fixture():
    def receipt(rid, end):
        return {
            "request_id": rid, "submitted_monotonic_ms": 1.,
            "native_complete_monotonic_ms": end, "exact_tokens_verified": True,
            "prompt_tokens": 100, "output_tokens": 2,
            "native_receipt": {
                "request_id": rid, "identity": ["w", rid, "context-" + rid, 0],
                "clock_domain": "clock", "complete_monotonic_ms": end,
            },
        }

    def sample(index, launch, begin, end, ids, phase="decode"):
        return {
            "event": "gpu_service_sample", "forward_iter": index,
            "sample_id": f"native-service-{index:09d}", "monotonic_clock_domain": "clock",
            "launch_monotonic_ms": launch, "service_start_monotonic_ms": begin,
            "complete_monotonic_ms": end, "service_elapsed_ms": end - begin,
            "phase": phase, "batch_size": len(ids),
            "request_samples": [{
                "request_id": rid, "workflow_id": "w", "invocation_id": rid,
                "context_id": "context-" + rid, "context_epoch": 0,
                "phase": phase, "token_delta": 0,
            } for rid in ids],
        }

    return [receipt("a", 20.), receipt("b", 18.)], [
        sample(1, 2., 3., 8., ("a",), "prefill"),
        sample(2, 5., 7., 10., ("a", "b")),
        sample(3, 12., 12., 21., ("a", "b")),
        sample(4, 22., 22., 25., ("a", "b")),
    ]


def test_member_union_clips_completion_and_preserves_overlap_and_tail_diagnostics():
    requests, samples = fixture()
    rows = account(requests, samples, expected_samples=4)
    a, b = rows["a"], rows["b"]
    assert a["first_launch_to_complete_ms"] == 18.
    assert a["member_worker_interval_union_ms"] == 15.
    assert a["outside_member_worker_intervals_ms"] == 3.
    assert a["prefill_member_interval_union_ms"] == 5.
    assert a["decode_member_interval_union_ms"] == 11.
    assert a["cross_phase_member_overlap_ms"] == 1.
    assert a["member_interval_ms_outside_request_lifetime"] == 4.
    assert a["member_samples_crossing_or_after_completion"] == 2
    assert b["first_launch_to_complete_ms"] == 13.
    assert b["member_worker_interval_union_ms"] == 9.
    assert b["outside_member_worker_intervals_ms"] == 4.
    assert b["member_interval_ms_outside_request_lifetime"] == 6.
    result = aggregate(rows.values())
    assert result["requests"] == 2 and result["output_tokens"] == 4
    assert result["sum_ms"]["first_launch_to_complete_ms"] == 31.
    assert result["membership_diagnostics"]["member_samples_crossing_or_after_completion"] == 4
    assert result["membership_diagnostics"]["member_interval_ms_outside_request_lifetime"] == 10.


def test_path_partition_reconstructs_existing_queue_and_processing_components():
    requests, samples = fixture()
    rows = account(requests, samples, expected_samples=4)
    workflows = {"w": {
        "jct_ms": 20., "critical_path_requests": ["a"],
        "components_ms": {
            "request_submit_to_first_launch": 1.,
            "request_first_launch_to_native_complete": 18.,
        },
    }}
    result = path_partition(rows, workflows)["workflow_equal_weight_mean_s"]
    assert result["first_launch_to_complete_s"] == .018
    assert result["member_worker_interval_union_s"] == .015
    assert result["outside_member_worker_intervals_s"] == .003
    for fault in ("foreign-workflow", "unknown-request", "wrong-timing", "duplicate-path", "empty"):
        invalid = deepcopy(workflows)
        if fault == "foreign-workflow":
            invalid["foreign"] = invalid.pop("w")
        elif fault == "unknown-request":
            invalid["w"]["critical_path_requests"] = ["missing"]
        elif fault == "wrong-timing":
            invalid["w"]["components_ms"]["request_first_launch_to_native_complete"] = 19.
        elif fault == "duplicate-path":
            invalid["w"]["critical_path_requests"] = ["a", "a"]
        else:
            invalid.clear()
        with pytest.raises(ValueError, match="critical path"):
            path_partition(rows, invalid)


@pytest.mark.parametrize("fault", (
    "duplicate-request", "wrong-request-clock", "unverified-request", "bad-receipt",
    "missing-sample", "duplicate-forward", "sample-gap", "wrong-sample-clock",
    "before-submission", "bad-worker-start", "bad-elapsed", "nan-timing",
    "unknown-member", "duplicate-member", "foreign-identity", "wrong-member-phase",
    "bad-geometry", "positive-output-after-completion", "missing-request-coverage",
))
def test_invalid_native_population_membership_and_clock_evidence_is_rejected(fault):
    requests, samples = fixture()
    count = 4
    if fault == "duplicate-request":
        requests.append(deepcopy(requests[0]))
    elif fault == "wrong-request-clock":
        requests[0]["native_receipt"]["clock_domain"] = "foreign"
    elif fault == "unverified-request":
        requests[0]["exact_tokens_verified"] = False
    elif fault == "bad-receipt":
        requests[0]["native_receipt"]["complete_monotonic_ms"] = 21.
    elif fault == "missing-sample":
        count = 5
    elif fault == "duplicate-forward":
        samples[1]["forward_iter"] = samples[0]["forward_iter"]
    elif fault == "sample-gap":
        samples[1]["sample_id"] = "native-service-000000008"
    elif fault == "wrong-sample-clock":
        samples[0]["monotonic_clock_domain"] = "foreign"
    elif fault == "before-submission":
        requests[0]["submitted_monotonic_ms"] = 3.
    elif fault == "bad-worker-start":
        samples[0]["service_start_monotonic_ms"] = 1.
    elif fault == "bad-elapsed":
        samples[0]["service_elapsed_ms"] = 10.
    elif fault == "nan-timing":
        samples[0]["complete_monotonic_ms"] = float("nan")
    elif fault == "unknown-member":
        samples[0]["request_samples"][0]["request_id"] = "missing"
    elif fault == "duplicate-member":
        samples[1]["request_samples"][1] = deepcopy(samples[1]["request_samples"][0])
    elif fault == "foreign-identity":
        samples[0]["request_samples"][0]["invocation_id"] = "foreign"
    elif fault == "wrong-member-phase":
        samples[0]["request_samples"][0]["phase"] = "decode"
    elif fault == "bad-geometry":
        samples[0]["batch_size"] = 2
    elif fault == "positive-output-after-completion":
        samples[-1]["request_samples"][0]["token_delta"] = 1
    else:
        for row in samples:
            row["request_samples"] = [member for member in row["request_samples"] if member["request_id"] != "b"]
            row["batch_size"] = len(row["request_samples"])
    with pytest.raises(ValueError):
        account(requests, samples, expected_samples=count)
