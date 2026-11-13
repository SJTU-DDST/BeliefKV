from copy import deepcopy
import json

import pytest

from scripts import plot_paused_agent_timeline as plots
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


def recorded_prefix(tmp_path, *, finished=False, certified=False):
    run = tmp_path / "run"
    events = [event("workflow_start", 0), event("llm_submit", 100, request_id="r")]
    if finished:
        events.extend([
            event("llm_result", 2000, request_id="r"), event("workflow_end", 3000),
        ])
    for path, rows in (
        (run / "full/replay/runtime_events.replay.jsonl", events),
        (run / "full/opportunities/admission_opportunities.jsonl", [
            {"event": "admission_runtime_state", **state(when)}
            for when in range(0, 6001, 500)
        ]),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    arrivals = {"arrival_schedule": [{"workflow_id": "w", "offset_seconds": 0.}]}
    arrival_path, registration = tmp_path / "arrivals.json", tmp_path / "registration.json"
    plots.write(arrival_path, arrivals)
    plots.write(registration, {
        "run_root": str(run), "workflow_arrivals_path": str(arrival_path),
        "workflow_arrivals": arrivals,
    })
    if certified:
        plots.write(run / "cohort_termination/completion.json", {
            name: True for name in (
                "complete_measured_native_work_verified", "no_measured_workflow_truncated",
                "fresh_idle_drain_verified", "actual_owned_exit_verified",
            )
        })
    return registration, {
        "measured_workflow_ids": ["w"],
        "throughput_window_offset_seconds": [.5, 2. if finished else 10.],
    }


def test_live_prefix_stops_at_observed_time_without_filling_future_with_zeroes(tmp_path):
    registration, cohort = recorded_prefix(tmp_path)
    _rows, report = plots.inspect(registration, cohort, live_prefix=True)
    assert report["observed_through_offset_s"] == 6.
    assert report["common_window"]["end_s"] == 6.
    assert report["declared_common_window_offset_seconds"] == [.5, 10.]
    assert report["common_window"]["time_weighted_mean"]["ready_paused"] == 1.
    assert not report["complete_common_window_observed"]
    assert not report["complete_measured_work_drain_and_exit_verified"]
    assert report["cohort_completion_s"] is report["through_measured_completion"] is None
    assert report["input_jsonl_prefixes"]


def test_default_report_still_requires_completion_certificate(tmp_path):
    registration, cohort = recorded_prefix(tmp_path)
    with pytest.raises(FileNotFoundError, match="completion.json"):
        plots.inspect(registration, cohort)


def test_observed_workflow_end_alone_never_certifies_a_live_prefix(tmp_path):
    registration, cohort = recorded_prefix(tmp_path, finished=True)
    _rows, report = plots.inspect(registration, cohort, live_prefix=True)
    assert report["cohort_completion_s"] == 3.
    assert report["complete_common_window_observed"]
    assert not report["complete_measured_work_drain_and_exit_verified"]
    assert report["through_measured_completion"] is None
    assert report["through_observation"]["end_s"] == 3.


def test_completed_report_retains_its_whole_measurement_semantics(tmp_path):
    registration, cohort = recorded_prefix(tmp_path, finished=True, certified=True)
    _rows, report = plots.inspect(registration, cohort)
    assert report["cohort_completion_s"] == 3.
    assert report["common_window"]["end_s"] == 2.
    assert report["through_measured_completion"]["end_s"] == 3.
    assert report["complete_measured_work_drain_and_exit_verified"]
    assert not report["live_prefix"]
