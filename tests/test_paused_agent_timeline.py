from copy import deepcopy

import pytest

from scripts.plot_paused_agent_timeline import aggregate, samples


def event(kind, when, owner="a", workflow="w", **attributes):
    return {
        "kind": kind, "ts_ms": when, "invocation_id": owner, "workflow_id": workflow,
        "join_id": "join", "attributes": attributes,
    }


def state(when, *, paused=("a",), active=()):
    return {
        "physical_disabled": False, "monotonic_ms": when, "ts_ms": when + 100000.,
        "agent_admission": {
            "paused_invocation_ids": list(paused), "active_invocation_ids": list(active),
            "capacity_tokens": 100, "logical_active_tokens": 50,
        },
    }


def evaluate(events, states):
    return samples(events, states, {"arrival_schedule": [{"workflow_id": "w", "offset_seconds": 0.}]})[0]


def test_join_and_tool_overlap_is_conserved_without_being_counted_twice():
    events = [
        event("workflow_start", 0), event("tool_start", 1, tool_run_id="t"),
        event("join_wait", 2), event("join_satisfied", 4), event("tool_end", 6, tool_run_id="t"),
        event("llm_submit", 7, request_id="r"),
    ]
    original = deepcopy(events)
    rows = evaluate(events, [state(t) for t in (0, 3, 5, 8)])
    assert rows[1]["join_paused"] == rows[1]["join_and_tool_paused_overlap"] == 1
    assert rows[1]["tool_paused"] == rows[1]["ready_paused"] == 0
    assert rows[2]["tool_paused"] == 1
    assert rows[3]["ready_paused"] == 1
    assert all(
        row["paused_total"] == sum(row[name] for name in (
            "ready_paused", "join_paused", "tool_paused", "other_paused",
        )) for row in rows
    )
    assert events == original


def test_no_submitted_successor_is_other_paused_and_active_tool_is_not_paused():
    events = [event("workflow_start", 0), event("tool_start", 1, tool_run_id="t")]
    rows = evaluate(events, [state(0), state(2, paused=(), active=("a",))])
    assert rows[0]["other_paused"] == 1
    assert rows[1]["paused_total"] == rows[1]["tool_paused"] == 0


def test_parallel_tools_wait_for_the_last_member_and_join_can_complete_without_owner():
    events = [
        event("workflow_start", 0), event("tool_start", 1, tool_run_id="t1"),
        event("tool_start", 2, tool_run_id="t2"), event("tool_end", 3, tool_run_id="t1"),
        event("llm_submit", 4, request_id="r"), event("join_wait", 5),
        event("join_satisfied", 6, owner=None), event("tool_end", 7, tool_run_id="t2"),
    ]
    rows = evaluate(events, [state(t) for t in (4, 5, 8)])
    assert rows[0]["tool_paused"] == 1 and rows[0]["ready_paused"] == 0
    assert rows[1]["join_paused"] == 1
    assert rows[2]["ready_paused"] == 1


def test_ended_workflow_members_are_reported_as_stale_instead_of_waiting():
    events = [event("workflow_start", 0), event("workflow_end", 1)]
    rows = evaluate(events, [state(0), state(2)])
    assert rows[-1]["paused_total"] == 0
    assert rows[-1]["ended_native_paused_records"] == 1


@pytest.mark.parametrize("change", ("unknown_owner", "overlap", "missing", "clock"))
def test_missing_or_inconsistent_evidence_is_rejected(change):
    states = [state(0), state(2)]
    if change == "unknown_owner":
        states[1]["agent_admission"]["paused_invocation_ids"] = ["foreign"]
    elif change == "overlap":
        states[1]["agent_admission"]["active_invocation_ids"] = ["a"]
    elif change == "missing":
        states[1]["agent_admission"].pop("paused_invocation_ids")
    else:
        states[1]["monotonic_ms"] = 0
    with pytest.raises(ValueError):
        evaluate([event("workflow_start", 0)], states)


def test_time_weighting_clips_edges_and_does_not_fill_unobserved_gaps():
    rows = evaluate(
        [event("workflow_start", 0), event("llm_submit", 100, request_id="r")],
        [state(t) for t in (0, 200, 400, 3000, 3200)],
    )
    report = aggregate(rows, .1, 3.1)
    assert report["covered_s"] == pytest.approx(.4)
    assert report["time_weighted_mean"]["ready_paused"] == pytest.approx(.75)
    assert report["ready_present_fraction"] == pytest.approx(.75)
