from copy import deepcopy

import pytest

from scripts.audit_join_reentry_service import attach_service, join_reentries, summarize


def event(kind, when, **values):
    return {"kind": kind, "ts_ms": when, "workflow_id": "wf", **values}


def client_events():
    return [
        event("workflow_start", 100.),
        event("join_wait", 200., join_id="join", invocation_id="parent"),
        event("join_satisfied", 300., join_id="join"),
        event(
            "llm_submit", 320., invocation_id="parent", context_id="ctx",
            context_epoch=2, attributes={"request_id": "request"},
        ),
    ]


def native_events():
    return [event(
        "llm_submit", 100_000., invocation_id="parent", context_id="ctx",
        context_epoch=2, attributes={
            "request_id": "request", "cached_tokens_host": 192,
            "cached_tokens_device": 0, "mamba_host_hit_slots": 1,
            "native_ingress_timing_ms": {"api_received_ts_ms": 99_970.},
        },
    )]


def services():
    return [{
        "event": "gpu_service_sample", "service_start_ts_ms": 100_040.,
        "complete_ts_ms": 100_060., "request_samples": [{
            "request_id": "request", "workflow_id": "wf", "invocation_id": "parent",
            "context_id": "ctx", "context_epoch": 2,
        }],
    }]


def test_join_service_without_prefetch_or_request_completion_keeps_clocks_separate():
    rows, anchor = join_reentries(client_events())
    attach_service(rows, native_events(), services())
    assert anchor == 100.
    row = rows[0]
    assert row["status"] == "first_service_observed"
    assert row["client_ready_to_submit_ms"] == 20.
    assert row["native_queue_to_service_ms"] == 40.
    assert row["api_received_to_queue_ms"] == 30.
    assert row["api_received_to_service_ms"] == 70.
    assert "join_to_service_ms" not in row
    summary = summarize(rows)
    assert summary["served_request_logical_host_hit_tokens"] == 192
    assert "h2d_bytes" not in summary
    assert summary["first_service_observed_requests"] == 1


@pytest.mark.parametrize("cutoff,status", [
    (2, "join_not_satisfied_in_prefix"),
    (3, "no_parent_submit_in_prefix"),
    (4, "submitted_no_native_service_in_prefix"),
])
def test_pending_join_and_unserved_requests_are_not_lost(cutoff, status):
    rows, _ = join_reentries(client_events()[:cutoff])
    attach_service(rows, [], [])
    summary = summarize(rows)
    assert summary["join_waiter_episodes"] == 1
    assert summary["status_counts"] == {status: 1}
    assert summary["native_queue_to_service_ms"]["count"] == 0


@pytest.mark.parametrize("field,value", [
    ("workflow_id", "other"), ("invocation_id", "other"),
    ("context_id", "other"), ("context_epoch", 3), ("context_epoch", True),
])
def test_native_identity_must_match_every_client_field(field, value):
    rows, _ = join_reentries(client_events())
    native = native_events()
    native[0][field] = value
    attach_service(rows, native, services())
    assert rows[0]["status"] == "native_identity_mismatch"
    assert "native_queue_to_service_ms" not in rows[0]


def test_first_service_uses_matching_identity_and_first_start():
    rows, _ = join_reentries(client_events())
    service = services()
    other = deepcopy(service[0])
    other["service_start_ts_ms"] = 100_010.
    other["request_samples"][0]["context_epoch"] = 1
    later = deepcopy(service[0])
    later["service_start_ts_ms"], later["complete_ts_ms"] = 100_100., 100_120.
    attach_service(rows, native_events(), [other, later, *service])
    assert rows[0]["native_queue_to_service_ms"] == 40.


def test_join_after_early_satisfaction_uses_actual_wait_boundary():
    events = client_events()
    events[2]["ts_ms"] = 190.
    rows, _ = join_reentries(events)
    assert rows[0]["client_ready_to_submit_ms"] == 120.


@pytest.mark.parametrize("kind,reason", [
    ("return", "parent_terminal"), ("invocation_cancel", "parent_terminal"),
    ("join_wait", "another_join_wait"),
])
def test_parent_terminal_or_new_wait_does_not_borrow_a_later_request(kind, reason):
    events = client_events()
    events.insert(3, event(kind, 310., invocation_id="parent", join_id="other"))
    rows, _ = join_reentries(events)
    target = next(row for row in rows if row["join_id"] == "join")
    assert target["status"] == reason
    assert "request_id" not in target


def test_same_time_ambiguous_submits_remain_unattributed():
    events = client_events()
    events.append({**events[-1], "attributes": {"request_id": "other-request"}})
    rows, _ = join_reentries(events)
    assert rows[0]["status"] == "ambiguous_parent_submit"


@pytest.mark.parametrize("duplicate", ["join_satisfied", "native_submit"])
def test_duplicate_boundaries_are_rejected(duplicate):
    events = client_events()
    if duplicate == "join_satisfied":
        with pytest.raises(ValueError, match="duplicate JOIN"):
            join_reentries([*events, events[2]])
    else:
        rows, _ = join_reentries(events)
        native = native_events()
        with pytest.raises(ValueError, match="duplicate native"):
            attach_service(rows, [*native, *native], services())


def test_window_is_relative_to_arrivals_and_keeps_unsatisfied_waits():
    rows, _ = join_reentries(client_events(), start_seconds=.2, end_seconds=.3)
    assert len(rows) == 1
    assert rows[0]["join_ready_seconds_from_first_arrival"] == .2
    rows, _ = join_reentries(client_events(), start_seconds=0., end_seconds=.2)
    assert not rows
    rows, _ = join_reentries(client_events()[:2], start_seconds=0., end_seconds=.2)
    assert rows[0]["status"] == "join_not_satisfied_in_prefix"


def test_out_of_order_service_does_not_become_negative_latency():
    rows, _ = join_reentries(client_events())
    service = services()
    service[0]["service_start_ts_ms"] = 99_999.
    attach_service(rows, native_events(), service)
    assert rows[0]["status"] == "native_service_precedes_queue"


def test_anonymous_or_internal_requests_cannot_supply_parent_reentry():
    events = client_events()
    events[-1]["attributes"]["runtime_internal"] = True
    rows, _ = join_reentries(events)
    assert rows[0]["status"] == "no_parent_submit_in_prefix"


@pytest.mark.parametrize("kind,reason", [
    ("return", "parent_terminal"), ("invocation_cancel", "parent_terminal"),
    ("workflow_end", "workflow_terminal"),
])
def test_terminal_before_join_satisfaction_cannot_borrow_a_future_request(kind, reason):
    events = client_events()
    events.insert(2, event(kind, 250., invocation_id="parent"))
    rows, _ = join_reentries(events)
    assert rows[0]["status"] == reason
    assert "request_id" not in rows[0]


@pytest.mark.parametrize("start,end", [
    (-1., None), (0., 0.), (0., float("nan")), (float("inf"), None),
])
def test_invalid_cohort_bounds_are_rejected_before_reading_events(start, end):
    with pytest.raises(ValueError, match="cohort bounds"):
        join_reentries(client_events(), start_seconds=start, end_seconds=end)
