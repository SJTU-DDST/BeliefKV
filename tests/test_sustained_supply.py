from copy import deepcopy
import json

import pytest

from beliefkv.experiments.replay_arrivals import poisson_arrival_plan
from beliefkv.experiments.sustained_supply import inspect_supply
from beliefkv.experiments.workflow_cohort import declare_cohort
from scripts.audit_sustained_supply import bound_arrivals


def inputs():
    arrivals = poisson_arrival_plan(
        [f"w{i}" for i in range(6)], rate_per_second=.01, seed=31, initial_workflows=3,
    )
    cohort = declare_cohort(
        arrivals, warmup=1, measured=2, throughput_start_s=0.,
        throughput_end_s=arrivals["arrival_schedule"][-1]["offset_seconds"],
    )
    events = [
        {"kind": "workflow_start", "workflow_id": f"w{i}", "ts_ms": 1000.}
        for i in range(3)
    ]
    requests = {}
    for index, prompt in ((1, 100), (2, 300)):
        identity = f"w{index}"
        requests[identity] = {
            "workflow_id": identity, "invocation_id": identity,
            "prompt_tokens": prompt, "input_increment": prompt, "output_tokens": 5,
        }
        events.append({
            "kind": "llm_submit", "workflow_id": identity, "invocation_id": identity,
            "ts_ms": 1020., "attributes": {"request_id": identity},
        })
    totals = {f"w{i}": 1000 for i in range(6)}
    totals["w2"] = 5000
    state = {
        "event": "admission_runtime_state", "physical_disabled": False,
        "monotonic_ms": 1100.,
        "agent_admission": {
            "active_invocation_ids": ["w0"], "paused_pending_invocation_ids": ["w1", "w2"],
            "marked_for_pause_invocation_ids": [], "capacity_tokens": 1000,
            "logical_active_tokens": 500, "counts": {},
        },
    }
    return arrivals, cohort, events, [state], requests, totals


def test_observed_pending_competition_is_separate_from_physical_feasibility():
    report = inspect_supply(*inputs())
    assert report["sample_counts"]["with_cross_workflow_pair_competition"] == 1
    choice = report["competition_snapshots"][0]
    assert choice["active_ready_agents"] == 0
    assert choice["ready_paused_agents"] == 2
    assert choice["maximum_competing_remaining_work_ratio"] == 5
    assert not report["scope"]["physical_counterfactual_verified"]
    assert report["measured_completed"] == 0
    assert report["background_covers_all_measured_completions"] is None


def test_three_agent_capacity_competition_does_not_require_an_infeasible_pair():
    args = list(inputs())
    args[4]["w0"] = {
        "workflow_id": "w0", "invocation_id": "w0", "prompt_tokens": 200,
        "input_increment": 200, "output_tokens": 5,
    }
    args[4]["w1"]["prompt_tokens"] = args[4]["w2"]["prompt_tokens"] = 200
    args[2].append({
        "kind": "llm_submit", "workflow_id": "w0", "invocation_id": "w0",
        "ts_ms": 1020., "attributes": {"request_id": "w0"},
    })
    args[3][0]["agent_admission"].update(
        active_invocation_ids=[], paused_pending_invocation_ids=["w0", "w1", "w2"],
        logical_active_tokens=200,
    )
    report = inspect_supply(*args)
    assert report["sample_counts"]["with_cross_workflow_pair_competition"] == 0
    assert report["sample_counts"]["with_cross_workflow_set_competition"] == 1
    assert len(report["competition_snapshots"]) == 1
    assert report["competition_snapshots"][0]["maximum_set_competing_remaining_work_ratio"] == 5.


def test_only_completed_intrinsic_work_is_consumed_and_queue_time_is_not_work():
    args = list(inputs())
    args[2].extend((
        {"kind": "llm_result", "workflow_id": "w1", "invocation_id": "w1",
         "ts_ms": 1120., "attributes": {"request_id": "w1"}},
        {"kind": "llm_submit", "workflow_id": "w1", "invocation_id": "w1",
         "ts_ms": 1130., "attributes": {"request_id": "next"}},
    ))
    args[4]["next"] = {**args[4]["w1"], "input_increment": 1}
    second = deepcopy(args[3][0])
    second["monotonic_ms"] = 1200.
    third = deepcopy(second)
    third["monotonic_ms"] = 2000.
    args[3].extend((second, third))
    report = inspect_supply(*args)
    values = [
        row["maximum_competing_remaining_work_ratio"] for row in report["competition_snapshots"]
    ]
    assert values == [5., 12.5, 12.5]


@pytest.mark.parametrize("blocker", ("tool", "join", "marked", "no_request", "no_budget"))
def test_blocked_or_unsubmitted_agents_and_nonfitting_candidates_are_not_opportunities(blocker):
    args = list(inputs())
    if blocker == "tool":
        args[2].append({
            "kind": "tool_start", "workflow_id": "w2", "invocation_id": "w2",
            "ts_ms": 1050., "attributes": {"tool_run_id": "tool"},
        })
    elif blocker == "join":
        args[2].append({
            "kind": "join_wait", "workflow_id": "w2", "invocation_id": "w2",
            "ts_ms": 1050., "join_id": "join",
        })
    elif blocker == "marked":
        args[3][0]["agent_admission"]["marked_for_pause_invocation_ids"] = ["w2"]
    elif blocker == "no_request":
        args[2] = [row for row in args[2] if row.get("invocation_id") != "w2"]
    else:
        args[3][0]["agent_admission"]["logical_active_tokens"] = 999
    assert inspect_supply(*args)["sample_counts"]["with_cross_workflow_pair_competition"] == 0


@pytest.mark.parametrize("mutation", ("arrival", "request_owner", "duplicate_clock", "overlap"))
def test_changed_arrivals_owners_or_serial_request_state_are_rejected(mutation):
    args = list(inputs())
    if mutation == "arrival":
        args[2][0]["ts_ms"] += 1.
    elif mutation == "request_owner":
        args[4]["w1"]["workflow_id"] = "w2"
    elif mutation == "duplicate_clock":
        args[3].append(deepcopy(args[3][0]))
    else:
        args[2].append(deepcopy(args[2][-1]))
    with pytest.raises(ValueError):
        inspect_supply(*args)


def test_reader_uses_registered_arrival_path_and_compares_the_embedded_plan(tmp_path):
    arrivals = inputs()[0]
    path = tmp_path / "arrivals.json"
    path.write_text(json.dumps(arrivals))
    registration = {"workflow_arrivals_path": str(path), "workflow_arrivals": arrivals}
    assert bound_arrivals(registration) == (path, arrivals)
    changed = deepcopy(arrivals)
    changed["arrival_schedule"][0]["offset_seconds"] += 1.
    path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="arrivals changed"):
        bound_arrivals(registration)
