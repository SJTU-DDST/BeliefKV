from copy import deepcopy

import pytest

from scripts.audit_eos_trigger_lifecycle import classify


TRIGGER = {
    "child_request_id": "old", "workflow_id": "workflow",
    "child_invocation_id": "child", "join_id": "join", "ts_ms": 1.,
}
RECEIPT = {
    "request_id": "old", "exact_tokens_verified": True,
    "output_sha256": "hash", "output_tokens": 2,
    "native_receipt": {
        "request_id": "old", "identity": ["workflow", "child", "context", 0],
        "native_finish_reason": {"type": "stop"}, "output_token_ids": [42, 151645],
    },
}


def event(kind, request=None, *, context="context", epoch=0, **attrs):
    return {
        "kind": kind, "workflow_id": "workflow", "invocation_id": "child",
        "context_id": context, "context_epoch": epoch,
        "attributes": {**({"request_id": request} if request else {}), **attrs},
    }


def observe(receipts, events):
    return classify(
        [TRIGGER], receipts, events, opener_ids={151657}, closer_ids={151658},
    )


def test_final_trigger_requires_actual_completed_return_for_its_last_result():
    result = observe([RECEIPT], [
        event("llm_result", "old"), event("return", outcome="completed"),
    ])
    assert result["summary"]["all_triggered_requests_observed_final"] is True
    assert result["rows"][0]["status"] == "final_request_observed"


@pytest.mark.parametrize("events", (
    [], [event("llm_result", "old")],
    [event("return", outcome="completed")],
    [event("llm_result", "old"), event("return", outcome="cancelled")],
    [event("llm_result", "other"), event("return", outcome="completed")],
))
def test_absent_successor_or_unrelated_return_never_proves_finality(events):
    result = observe([RECEIPT], events)
    assert result["summary"]["all_triggered_requests_observed_final"] is False
    assert result["rows"][0]["status"] == "later_lifecycle_unresolved"


def test_later_same_context_epoch_overrides_an_earlier_completed_return_label():
    result = observe([RECEIPT], [
        event("llm_result", "old"), event("return", outcome="completed"),
        event("llm_submit", "later", epoch=1),
    ])
    assert result["rows"][0]["status"] == "intermediate_request_observed"
    assert result["rows"][0]["next_observed_request_id"] == "later"
    assert result["summary"]["all_triggered_requests_observed_final"] is False


def test_internal_requests_or_another_context_do_not_relabel_final_output():
    result = observe([RECEIPT], [
        event("llm_result", "old"),
        event("llm_submit", "internal", epoch=1, runtime_internal=True),
        event("llm_result", "internal", runtime_internal=True),
        event("llm_submit", "other-context", context="other", epoch=1),
        event("return", outcome="completed"),
    ])
    assert result["rows"][0]["status"] == "final_request_observed"


@pytest.mark.parametrize("token,expected", (
    (151657, "opener_present"), (151658, "closer_without_opener"),
))
def test_tool_boundary_evidence_is_retained_for_intermediate_trigger(token, expected):
    receipt = deepcopy(RECEIPT)
    receipt["native_receipt"]["output_token_ids"][0] = token
    result = observe([receipt], [event("llm_submit", "later", epoch=1)])
    assert result["rows"][0]["native_tool_delimiter_evidence"] == expected
    assert result["rows"][0]["status"] == "intermediate_request_observed"


def test_uncompleted_trigger_is_unknown_and_zero_triggers_are_not_a_positive_control():
    assert observe([], [])["rows"][0]["status"] == "completion_unobserved"
    empty = classify([], [], [], opener_ids={151657}, closer_ids={151658})
    assert empty["summary"]["all_triggered_requests_observed_final"] is False


@pytest.mark.parametrize("change", ("duplicate", "identity", "request", "length", "unverified", "tokens"))
def test_invalid_native_evidence_cannot_qualify_a_trigger(change):
    receipt = deepcopy(RECEIPT)
    if change == "identity":
        receipt["native_receipt"]["identity"][1] = "other"
    elif change == "request":
        receipt["native_receipt"]["request_id"] = "other"
    elif change == "length":
        receipt["native_receipt"]["native_finish_reason"]["type"] = "length"
    elif change == "unverified":
        receipt["exact_tokens_verified"] = False
    elif change == "tokens":
        receipt["output_tokens"] = 3
    with pytest.raises(ValueError):
        observe([receipt, receipt] if change == "duplicate" else [receipt], [])
