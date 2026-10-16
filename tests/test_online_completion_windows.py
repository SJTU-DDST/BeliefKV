import json

import pytest

from beliefkv.experiments.arrival_schedule import arrival_schedule_digest
from scripts.audit_online_completion_windows import arrival_states, audit_arm, comparisons, snapshot


def states():
    return {
        "a": {"scheduled_seconds": 0., "submitted_seconds": .1, "started_seconds": .2, "finished_seconds": 5.},
        "b": {"scheduled_seconds": 2., "submitted_seconds": 2.1, "started_seconds": 2.2},
        "c": {"scheduled_seconds": 4., "submitted_seconds": 4.1, "started_seconds": 4.2, "finished_seconds": 6.},
        "d": {"scheduled_seconds": 20.},
    }


def test_due_denominator_retains_unfinished_and_failed_workflows():
    result = snapshot(states(), {
        "a": {"outcome": "completed", "native_agent_jct_eligible": True},
        "c": {"outcome": "incomplete", "native_agent_jct_eligible": False},
    }, 10., observed_through=30.)
    assert result["counts"]["due_workflows"] == 3
    assert result["counts"]["recorded_completed"] == 1
    assert result["finished_outcomes"] == {"completed": 1, "incomplete": 1}
    assert result["capped_sojourn_mean_seconds"]["runner_finish_capped_seconds"] == 5.
    assert result["capped_sojourn_mean_seconds"]["recorded_completed_capped_seconds"] == 19 / 3


def test_future_finish_and_future_result_do_not_change_a_snapshot():
    before = {"a": {"scheduled_seconds": 0., "submitted_seconds": 1., "started_seconds": 2.}}
    after = {"a": {**before["a"], "finished_seconds": 20.}}
    completed = {"a": {"outcome": "completed", "native_agent_jct_eligible": True}}
    assert snapshot(before, {}, 10., observed_through=30.) == snapshot(
        after, completed, 10., observed_through=30.,
    )


def test_guarded_and_unknown_eligibility_completions_are_not_removed_from_cohort():
    results = {
        "a": {"outcome": "completed", "native_agent_jct_eligible": False},
        "c": {"outcome": "completed"},
    }
    result = snapshot(states(), results, 10., observed_through=30.)
    assert result["counts"]["recorded_completed"] == 2
    assert result["counts"]["explicitly_jct_eligible_completed"] == 0
    assert result["capped_sojourn_mean_seconds"]["eligible_completed_capped_seconds"] == 8.
    assert result["finished_task_correctness_valid"] == {"None": 2}


def test_missing_finished_result_is_unknown_instead_of_a_success():
    result = snapshot(states(), {}, 10., observed_through=30.)
    assert result["finished_outcomes"] == {"result_missing": 2}
    assert result["counts"]["recorded_completed"] == 0
    assert result["capped_sojourn_mean_seconds"]["recorded_completed_capped_seconds"] == 8.


def test_late_or_unobserved_submission_still_accrues_sojourn_from_planned_arrival():
    result = snapshot({
        "a": {"scheduled_seconds": 2., "submitted_seconds": 12., "started_seconds": 12.1},
        "b": {"scheduled_seconds": 4.},
    }, {}, 10., observed_through=30.)
    assert result["counts"]["due_workflows"] == 2
    assert result["counts"]["due_submission_unobserved"] == 2
    assert result["capped_sojourn_mean_seconds"]["runner_finish_capped_seconds"] == 7.


def test_boundary_finish_is_included_and_empty_cohort_mean_stays_unknown():
    result = snapshot({
        "a": {"scheduled_seconds": 0., "submitted_seconds": 1., "started_seconds": 2., "finished_seconds": 10.},
    }, {"a": {"outcome": "completed"}}, 10., observed_through=10.)
    assert result["counts"]["recorded_completed"] == 1
    empty = snapshot({"a": {"scheduled_seconds": 11.}}, {}, 10., observed_through=10.)
    assert empty["capped_sojourn_mean_seconds"]["runner_finish_capped_seconds"] is None


@pytest.mark.parametrize("horizon", [0., -1., float("inf"), float("nan")])
def test_invalid_or_uncovered_horizon_is_rejected(horizon):
    with pytest.raises(ValueError):
        snapshot(states(), {}, horizon, observed_through=30.)
    with pytest.raises(ValueError, match="covered"):
        snapshot(states(), {}, 31., observed_through=30.)


def test_duplicate_missing_start_and_out_of_order_lifecycles_are_rejected():
    schedule = [{"instance_id": "a", "offset_seconds": 1.}]
    submitted = {
        "event": "workflow_submitted", "instance_id": "a",
        "scheduled_offset_seconds": 1., "actual_offset_seconds": 1.1,
    }
    with pytest.raises(ValueError, match="duplicate"):
        arrival_states(schedule, [submitted, submitted])
    with pytest.raises(ValueError, match="no observed start"):
        arrival_states(schedule, [{
            "event": "workflow_finished", "instance_id": "a", "actual_offset_seconds": 2.,
        }])
    with pytest.raises(ValueError, match="unordered"):
        arrival_states(schedule, [{**submitted, "actual_offset_seconds": .5}])
    with pytest.raises(ValueError, match="differs"):
        arrival_states(schedule, [{**submitted, "scheduled_offset_seconds": 2.}])


def test_different_arrival_tables_cannot_be_compared():
    with pytest.raises(ValueError, match="schedules differ"):
        comparisons({"left": {"arrival_schedule_sha256": "a"}, "right": {"arrival_schedule_sha256": "b"}})


def test_live_prefix_and_closed_results_produce_an_all_due_snapshot(tmp_path):
    client = tmp_path / "client_2"
    client.mkdir()
    schedule = [
        {"instance_id": "a", "offset_seconds": 1.},
        {"instance_id": "b", "offset_seconds": 20.},
    ]
    (client / "manifest.json").write_text(json.dumps({
        "arrival_schedule": schedule, "arrival_schedule_sha256": arrival_schedule_digest(schedule),
    }))
    events = [
        {"event": "workflow_submitted", "instance_id": "a", "scheduled_offset_seconds": 1., "actual_offset_seconds": 1.1},
        {"event": "workflow_started", "instance_id": "a", "scheduled_offset_seconds": 1., "actual_offset_seconds": 1.2},
        {"event": "workflow_finished", "instance_id": "a", "actual_offset_seconds": 11.},
    ]
    (client / "workflow_arrivals.jsonl").write_bytes(
        b"".join((json.dumps(row) + "\n").encode() for row in events) + b'{"event":'
    )
    work = client / "workflows/a"
    work.mkdir(parents=True)
    (work / "result.json").write_text(json.dumps({
        "instance_id": "a", "outcome": "completed", "native_agent_jct_eligible": True,
    }))
    report = audit_arm(tmp_path, [5., 11.])
    early, late = report["snapshots"]
    assert early["counts"]["recorded_completed"] == 0
    assert late["counts"]["recorded_completed"] == 1
    assert early["counts"]["due_workflows"] == late["counts"]["due_workflows"] == 1
    assert early["capped_sojourn_mean_seconds"]["recorded_completed_capped_seconds"] == 4.
    assert late["capped_sojourn_mean_seconds"]["recorded_completed_capped_seconds"] == 10.


def test_fully_finished_planned_cohort_covers_later_comparison_horizons():
    closed = {
        "a": {"scheduled_seconds": 0., "submitted_seconds": 1., "started_seconds": 2., "finished_seconds": 5.},
        "b": {"scheduled_seconds": 2., "submitted_seconds": 3., "started_seconds": 4., "finished_seconds": 6.},
    }
    report = snapshot(closed, {
        "a": {"outcome": "completed", "native_agent_jct_eligible": True},
        "b": {"outcome": "incomplete", "native_agent_jct_eligible": False},
    }, 20., observed_through=6.)
    assert report["counts"]["due_workflows"] == report["counts"]["runner_finished"] == 2
    assert report["counts"]["recorded_completed"] == 1
    assert report["capped_sojourn_mean_seconds"]["runner_finish_capped_seconds"] == 4.5
    assert report["capped_sojourn_mean_seconds"]["recorded_completed_capped_seconds"] == 11.5


@pytest.mark.parametrize("uncovered", ["future_planned_task", "finish_after_observed_prefix"])
def test_partial_or_inconsistent_cohort_cannot_cover_later_horizons(uncovered):
    closed = {
        "a": {"scheduled_seconds": 0., "submitted_seconds": 1., "started_seconds": 2., "finished_seconds": 5.},
    }
    if uncovered == "future_planned_task":
        closed["b"] = {"scheduled_seconds": 8.}
    else:
        closed["a"]["finished_seconds"] = 15.
    with pytest.raises(ValueError, match="covered"):
        snapshot(closed, {}, 20., observed_through=5.)
