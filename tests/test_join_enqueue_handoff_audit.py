from copy import deepcopy

import pytest

from scripts.audit_join_enqueue_handoff import analyze


def fixture():
    common = {"workflow_id": "wf", "join_id": "join", "context_id": "ctx"}
    session = {"session_id": "session", "session_generation": 7}
    events = [
        {
            **common, "event": "confirmed_join_client_bound", "ts_ms": 10.,
            "context_epoch": 2, "target_context_epoch": 3, "request_id": "next",
        },
        {
            **common, **session, "event": "wait_prefetch_plan", "ts_ms": 11.,
            "source": "join_ticket", "reason": "enqueue_causal_changed",
            "context_epoch": 2, "request_id": "previous",
        },
        {
            **common, **session, "event": "prefetch_native_issued", "ts_ms": 13.,
            "source": "join_ticket", "context_epoch": 3, "request_id": "next",
            "checkpoint_context_epoch": 2, "command_id": "load",
        },
        {
            **common, "event": "wait_prefetch_plan", "ts_ms": 14.,
            "source": "join_ticket", "context_epoch": 3, "request_id": "next",
            "reason": "no_restore_plan",
        },
    ]
    acknowledgements = [{
        "action": "PREFETCH_GPU", "command_id": "load", "ts_ms": 15.,
        "context_id": "ctx", "context_epoch": 3, "pool_bytes": {"kv": 100},
    }]
    return events, acknowledgements


def test_observed_rebind_and_retry_is_not_a_permanent_prefetch_loss():
    events, acknowledgements = fixture()
    report = analyze(events, acknowledgements)
    assert report["status_counts"] == {"client_handoff_retry_submitted": 1}
    [row] = report["rows"]
    assert row["rejection_to_first_retry_ms"] == 2.
    assert row["first_retry_acked_full_bytes"] == 100
    assert row["following_target_plan_reasons"] == {"no_restore_plan": 1}
    assert "reused_bytes" not in row


@pytest.mark.parametrize("field,value", (
    ("workflow_id", "other"), ("join_id", "other"), ("context_id", "other"),
    ("context_epoch", 4), ("request_id", "other"),
    ("checkpoint_context_epoch", 1), ("session_id", "other"),
    ("session_generation", 8), ("ts_ms", 9.),
))
def test_other_identity_or_clock_cannot_prove_a_retry(field, value):
    events, acknowledgements = fixture()
    events[2][field] = value
    report = analyze(events, acknowledgements)
    assert report["status_counts"] == {"client_handoff_without_observed_retry": 1}
    assert "first_retry_acked_full_bytes" not in report["rows"][0]


@pytest.mark.parametrize("field,value", (
    ("workflow_id", "other"), ("join_id", "other"), ("context_id", "other"),
    ("context_epoch", 1), ("target_context_epoch", 4), ("request_id", "previous"),
    ("request_id", ""), ("ts_ms", 12.),
))
def test_binding_requires_the_existing_parent_and_adjacent_epoch(field, value):
    events, acknowledgements = fixture()
    events[0][field] = value
    assert analyze(events, acknowledgements)["status_counts"] == {"client_handoff_unproven": 1}


def test_future_binding_is_not_borrowed_and_pending_retry_is_not_an_error():
    events, acknowledgements = fixture()
    report = analyze([events[1], events[0], *events[2:]], acknowledgements)
    assert report["status_counts"] == {"client_handoff_unproven": 1}
    report = analyze(events[:2], [])
    assert report["status_counts"] == {"client_handoff_without_observed_retry": 1}


@pytest.mark.parametrize("field,value", (
    ("context_id", "other"), ("context_epoch", 2), ("ts_ms", 12.),
    ("pool_bytes", {"mamba": 100}), ("pool_bytes", {"kv": True}),
    ("pool_bytes", {"kv": -1}),
))
def test_full_ack_requires_the_bound_consumer_and_valid_full_bytes(field, value):
    events, acknowledgements = fixture()
    acknowledgements[0][field] = value
    [row] = analyze(events, acknowledgements)["rows"]
    assert row["status"] == "client_handoff_retry_submitted"
    assert not row["first_retry_acked_full_command_ids"]
    assert row["first_retry_acked_full_bytes"] == 0


def test_counts_only_the_first_retry_batch_and_keeps_missing_ack_explicit():
    events, acknowledgements = fixture()
    second = {**events[2], "command_id": "same-batch"}
    later = {**events[2], "command_id": "later", "ts_ms": 14.}
    [row] = analyze([*events[:3], second, later, events[3]], acknowledgements)["rows"]
    assert row["first_retry_command_ids"] == ["load", "same-batch"]
    assert row["first_retry_acked_full_command_ids"] == ["load"]
    assert row["first_retry_acked_full_bytes"] == 100
    with pytest.raises(ValueError, match="duplicate"):
        analyze(events, [*acknowledgements, deepcopy(acknowledgements[0])])
