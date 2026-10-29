from copy import deepcopy

import pytest

from scripts.audit_free_dispatch_service import associate


def evidence():
    key = dict(
        request_id="r", root_workflow_id="wf", invocation_id="agent",
        context_id="ctx", context_epoch=2, attempt_id=0, session_id="session", session_generation=3,
    )
    grant = {
        "ts_ms": 1000., "monotonic_s": 1.,
        "programs": [{"key": key, "observed_context_tokens": 200, "complete_budget_tokens": 300}],
        "required_tokens": 300, "remaining_before_tokens": 500, "remaining_after_tokens": 200,
        "native_capacity_authority_preserved": True,
    }
    sample = {**key, "workflow_id": "wf", "phase": "prefill", "output_tokens_before": 0}
    sample.pop("root_workflow_id")
    batch = {
        "event": "gpu_service_sample", "request_samples": [sample],
        "service_start_ts_ms": 1015., "complete_ts_ms": 1100.,
        "service_start_monotonic_ms": 1015.,
        "complete_monotonic_ms": 1100., "monotonic_clock_domain": "clock",
    }
    submission = {
        "kind": "llm_submit", "ts_ms": 900., "workflow_id": "wf", "invocation_id": "agent",
        "context_id": "ctx", "context_epoch": 2, "attributes": {"request_id": "r", "prompt_tokens": 200},
    }
    receipt = {"request_id": "r", "native_complete_monotonic_ms": 1200., "native_receipt": {
        "identity": ["wf", "agent", "ctx", 2], "session_id": "session", "session_generation": 3,
        "complete_monotonic_ms": 1200., "clock_domain": "clock",
    }}
    return [grant], [submission], [batch], [receipt]


def test_complete_grant_requires_exact_native_service_and_owned_receipt():
    result = associate(*evidence(), complete=True)
    assert result["counts"]["first_native_service_verified"] == 1
    assert result["counts"]["completed_receipt_verified"] == 1
    assert result["grant_to_first_native_service_ms"]["p50"] == 15.
    assert result["rows"][0]["queue_to_grant_ms"] == 100.


@pytest.mark.parametrize("field,value", (("attempt_id", 1), ("session_generation", 4), ("context_epoch", 3)))
def test_another_native_attempt_or_owner_does_not_prove_service(field, value):
    grants, submits, batches, receipts = evidence()
    batches[0]["request_samples"][0][field] = value
    result = associate(grants, submits, batches, receipts)
    assert result["counts"].get("first_native_service_verified", 0) == 0
    assert result["rows"][0]["status"] == "open_or_cross_stream_coverage_unknown"
    with pytest.raises(ValueError, match="unverified grant"):
        associate(grants, submits, batches, receipts, complete=True)


def test_live_pending_grant_is_unknown_and_not_a_zero_latency_success():
    grants, submits, _batches, _receipts = evidence()
    result = associate(grants, submits, [], [])
    assert result["counts"]["open_or_cross_stream_coverage_unknown"] == 1
    assert result["grant_to_first_native_service_ms"]["count"] == 0


def test_compact_decode_cannot_replace_exact_prefill_attempt_and_session():
    grants, submits, batches, receipts = evidence()
    compact = deepcopy(batches[0])
    compact["request_samples"][0]["phase"] = "decode"
    compact["service_start_ts_ms"] += 100.
    for name in ("attempt_id", "session_id", "session_generation"):
        compact["request_samples"][0].pop(name)
    result = associate(grants, submits, [*batches, compact], receipts, complete=True)
    assert result["counts"]["first_native_service_verified"] == 1
    assert result["counts"]["service_samples_without_full_attempt_session_identity"] == 1
    assert result["grant_to_first_native_service_ms"]["p50"] == 15.
    with pytest.raises(ValueError, match="unverified grant"):
        associate(grants, submits, [compact], receipts, complete=True)


@pytest.mark.parametrize("change", ("capacity", "buffer", "ingress", "receipt", "clock", "earlier_service"))
def test_invalid_authority_identity_or_clock_is_rejected(change):
    grants, submits, batches, receipts = evidence()
    if change == "capacity":
        grants[0]["remaining_before_tokens"] = 200
    elif change == "buffer":
        grants[0]["programs"][0]["complete_budget_tokens"] = 200
    elif change == "ingress":
        submits[0]["attributes"]["prompt_tokens"] = 300
    elif change == "receipt":
        receipts[0]["native_receipt"]["session_id"] = "other"
    elif change == "clock":
        grants[0]["monotonic_s"] = 2.
    else:
        batches[0]["service_start_ts_ms"] = 900.
        batches[0]["service_start_monotonic_ms"] = 900.
        result = associate(grants, submits, batches, receipts)
        assert result["counts"].get("first_native_service_verified", 0) == 0
        assert result["rows"][0]["status"] == "open_or_cross_stream_coverage_unknown"
        return
    with pytest.raises(ValueError):
        associate(grants, submits, batches, receipts)


def test_repeated_grants_are_retained_as_episodes_not_independent_requests():
    grants, submits, batches, receipts = evidence()
    grants.append(deepcopy(grants[0]))
    grants[1]["ts_ms"], grants[1]["monotonic_s"] = 1005., 1.005
    result = associate(grants, submits, batches, receipts, complete=True)
    assert result["counts"]["grant_episodes"] == 2
    assert result["counts"]["unique_granted_request_attempts"] == 1
    assert result["counts"]["repeated_grants_to_same_attempt"] == 1


def test_regrant_uses_only_the_first_following_exact_service():
    grants, submits, batches, receipts = evidence()
    grants.append(deepcopy(grants[0]))
    grants[1]["ts_ms"], grants[1]["monotonic_s"] = 1070., 1.07
    later = deepcopy(batches[0])
    later.update(service_start_ts_ms=1080., service_start_monotonic_ms=1080.)
    result = associate(grants, submits, [*batches, later], receipts, complete=True)
    assert [row["grant_to_first_service_ms"] for row in result["rows"]] == [15., 10.]
    assert result["counts"]["grant_episodes_with_earlier_exact_service"] == 1


def test_old_exact_service_does_not_prove_consumption_of_a_new_grant():
    grants, submits, batches, receipts = evidence()
    grants[0]["ts_ms"], grants[0]["monotonic_s"] = 1070., 1.07
    result = associate(grants, submits, batches, receipts)
    assert not result["rows"][0]["first_service_observed"]
    assert result["counts"]["open_or_cross_stream_coverage_unknown"] == 1
    assert result["grant_to_first_native_service_ms"]["count"] == 0
    with pytest.raises(ValueError, match="unverified grant"):
        associate(grants, submits, batches, receipts, complete=True)


@pytest.mark.parametrize("completed", (995., 1010.))
def test_completion_before_grant_or_following_service_cannot_verify_consumption(completed):
    grants, submits, batches, receipts = evidence()
    receipts[0]["native_complete_monotonic_ms"] = completed
    receipts[0]["native_receipt"]["complete_monotonic_ms"] = completed
    result = associate(grants, submits, batches, receipts)
    assert not result["rows"][0]["completion_receipt_observed"]
    assert result["counts"]["open_or_cross_stream_coverage_unknown"] == 1
    with pytest.raises(ValueError, match="unverified grant"):
        associate(grants, submits, batches, receipts, complete=True)


def test_reentry_context_budget_can_include_exact_already_generated_output():
    grants, submits, batches, receipts = evidence()
    grants[0]["programs"][0].update(
        observed_context_tokens=220, complete_budget_tokens=320,
    )
    grants[0].update(required_tokens=320, remaining_after_tokens=180)
    batches[0]["request_samples"][0]["output_tokens_before"] = 20
    result = associate(grants, submits, batches, receipts, complete=True)
    assert result["counts"]["native_input_verified"] == 1
    assert result["counts"]["observed_reentry_output_verified"] == 1
    batches[0]["request_samples"][0]["output_tokens_before"] = 19
    with pytest.raises(ValueError, match="observed reentry output"):
        associate(grants, submits, batches, receipts)


@pytest.mark.parametrize("changed", ("receipt_clock", "duplicate_receipt", "completion_disagreement"))
def test_receipt_identity_and_completion_clock_are_unambiguous(changed):
    grants, submits, batches, receipts = evidence()
    if changed == "receipt_clock":
        receipts[0]["native_receipt"]["clock_domain"] = "other-clock"
    elif changed == "duplicate_receipt":
        receipts.append(deepcopy(receipts[0]))
    else:
        receipts[0]["native_complete_monotonic_ms"] = 1199.
    with pytest.raises(ValueError):
        associate(grants, submits, batches, receipts)
