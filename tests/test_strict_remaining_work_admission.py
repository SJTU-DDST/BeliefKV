from copy import deepcopy
from dataclasses import asdict, replace
from functools import partial
import json
from pathlib import Path
from types import SimpleNamespace as NS
import time

import pytest

from beliefkv.policy.workflow_combination import _evaluate
from beliefkv.policy.workflow_maximal_admission import choose_maximal_admission
from beliefkv.policy.workflow_shortest_frontier import (
    choose_shortest_work_first, strict_remaining_work_enabled,
)
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from beliefkv.runtime.subagent_materialization import SubagentMaterialization, _InitialRequest
from beliefkv.core.events import RuntimeEventKind as Kind
from scripts.audit_workflow_combination_choices import replay_choice, validate_selection_policy
from scripts.audit_strict_workflow_priority import compare
from scripts.finish_model_work_oracle import verify_same_objective
from scripts.prepare_strict_workflow_priority import prepare
from tests.test_model_work_oracle_finish import pair
from tests.test_ready_wait_aging_audit import recorded_aging
from tests.test_ready_wait_priority import WeightedPrediction
from tests.test_scheduler_parameter_artifacts import inputs
from tests.test_shortest_work_candidate_window import window_fixture
from tests.test_workflow_combination_runtime import runtime_with_queue
from tests.test_workflow_shortest_frontier import fixture
from tests.test_workflow_work_oracle import source
from beliefkv.runtime.workflow_work_oracle import WorkflowWorkOracleArtifact
from tests.test_internal_comparison import observed
from tests.test_paper_baseline_runtime import request
from tests.test_reference_sibling_coadmission import group


def choose(rows, reference, resources, costs, **kwargs):
    return choose_shortest_work_first(
        rows, reference, resources, costs, strict_remaining_work=True,
        clock=lambda: 0, **kwargs,
    )


def test_remaining_work_is_not_outvoted_by_aggregate_utility():
    rows, resources, costs = fixture()
    aggregate = choose_maximal_admission(
        rows, ("long-a", "long-b"), resources, costs,
        clock=lambda: 0, selection_objective="benefit",
    )
    strict = choose(rows, ("long-a", "long-b"), resources, costs)
    assert aggregate["selected"] == ("long-a", "long-b")
    assert strict["selected"] == ("short",)


@pytest.mark.parametrize("progress", ("linear", "quadratic"))
@pytest.mark.parametrize("fanout", (1, 4, 8))
def test_join_fanout_and_tolerance_do_not_change_workflow_priority(progress, fanout):
    rows, resources, costs = fixture()
    rows = [
        replace(rows[0], weight=.9, unfinished_members=fanout),
        replace(rows[1], logical_tokens=210, weight=.895),
    ]
    costs = replace(costs, join_progress=progress, relative_tie=.99)
    strict = choose(rows, ("long-a",), resources, costs)
    assert strict["selected"] == ("short",)
    assert _evaluate([rows[0]], resources, costs) is not None


def test_shortlisted_candidates_do_not_need_to_include_the_complete_fallback():
    rows, resources, costs = fixture()
    strict = choose(rows[:1], ("long-a", "long-b"), resources, costs)
    assert strict["selected"] == ("short",)
    assert strict["reference"] == ("long-a", "long-b")
    assert strict["reference_metadata_complete"] is False
    assert strict["reference_evaluation"] is None
    deadline = choose_shortest_work_first(
        rows[:1], ("long-a", "long-b"), resources, costs,
        strict_remaining_work=True, clock=iter((0, 500000, 500001)).__next__,
    )
    assert deadline["selected"] == ("long-a", "long-b")
    assert deadline["search_complete"] is False
    missing_join = choose(rows[:1], ("long-a",), resources, costs, required=("long-a",))
    assert missing_join["selected"] == ("long-a",) and not missing_join["search_complete"]


def test_full_eight_member_reference_does_not_hide_a_shorter_candidate():
    control, keys, calls = window_fixture()
    control.strict_remaining_work = True
    window = control._candidate_window(keys, keys[:8], 100, {})
    assert keys[0] in window and keys[-1] in window
    assert len(window) == 8 and set(window) != set(keys[:8])
    assert max(calls.values()) == 1


def test_shortlist_reserves_live_join_capacity_before_optional_prefix_checks():
    control, keys, _calls = window_fixture()
    control.strict_remaining_work = True
    control.admission.policy.programs[keys[0].invocation_id].tokens = 70
    assert control._candidate_window(keys, keys[:2], 100, {}) == [keys[0]]


@pytest.mark.parametrize("change", (
    {"strict_remaining_work_admission": 1},
    {"ready_wait_priority_ms": 30000},
    {"ready_wait_priority_ms": False},
    {"ready_wait_priority_grace_ms": 0},
    {"ready_wait_priority_max_boost": 1.},
    {"ready_capacity_accumulation_ms": 2000},
    {"ready_capacity_accumulation_ms": False},
    {"cost_aware_maximal_admission": True},
    {"maximal_admission_objective": "benefit"},
    {"tool_boundary_preemption": {}},
    {"pressure_work_order": "remaining_model_work_within_tool_retention_bands_v1"},
    {"shortest_work_first_admission": False},
))
def test_strict_mode_refuses_wait_or_utility_overrides(change):
    raw = {
        "strict_remaining_work_admission": True, "shortest_work_first_admission": True,
        **change,
    }
    with pytest.raises(ValueError, match="strict remaining-work"):
        strict_remaining_work_enabled(raw)


def strict_runtime(monkeypatch, tmp_path):
    runtime, admission, _env = runtime_with_queue(monkeypatch, tmp_path)
    control = admission._combination
    control.strict_remaining_work = control.shortest_work_first = True
    control.cost_aware_maximal = False
    control.costs = replace(control.costs, decode_cost_basis="full_batch", selection_objective="benefit")
    admission._work._predictions.update({
        owner: WeightedPrediction(
            (1., 0., 0.), 0, 1, time.monotonic(), "model", .9 if owner == "a" else .35,
        ) for owner in ("a", "b", "c")
    })
    from beliefkv.policy import workflow_shortest_frontier as policy

    monkeypatch.setattr(
        policy, "choose_shortest_work_first", partial(policy.choose_shortest_work_first, clock=lambda: 0),
    )
    admission.next_tick = 0.
    return runtime, admission


def mark_unmaterialized(runtime, keys):
    gate = runtime._subagent_materialization = SubagentMaterialization(
        runtime, include_root_entries=True,
    )
    for key in keys:
        request = runtime._agent_admission._live_requests[key.request_id]
        gate.pending[key.request_id] = _InitialRequest(key, request, 0., permitted=True)
    return gate


@pytest.mark.parametrize("source", ("periodic_decay_resume", "undiscounted_free_dispatch"))
def test_resume_only_preserves_first_materialization_reference(monkeypatch, tmp_path, source):
    runtime, admission = strict_runtime(monkeypatch, tmp_path)
    control = admission._combination
    control.resume_only_work_ranking = True
    a, b, c = (admission.latest[owner] for owner in ("a", "b", "c"))
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    try:
        gate = mark_unmaterialized(runtime, (b,))
        select = partial(control.select, capacity=500, maximum=4, source=source)
        assert select([a, b, c], [b, c]) == [b, c]
        assert records[-1]["decision"]["reason"] == "initial_materialization_reference_preserved"
        assert select([a, b, c], [c]) == [a]
        assert {candidate["identity"] for candidate in records[-1]["candidates"]} == {"a", "c"}
        gate.pending[b.request_id].started = True
        assert select([a, b, c], [b, c]) == [a]
    finally:
        runtime.close()


def test_resume_only_keeps_materialization_and_tool_order(monkeypatch, tmp_path):
    runtime, admission = strict_runtime(monkeypatch, tmp_path)
    control = admission._combination
    control.completion_path_oracle = True
    admission._conditional_distribution = True
    admission.workflow_pressure_pause = True
    a, b = (admission.latest[owner] for owner in ("a", "b"))
    try:
        gate = mark_unmaterialized(runtime, (b, a))
        queued = list(gate.pending.values())
        assert gate._ordered(queued) == queued[::-1]
        assert admission.order_tool_replacement([b, a]) == [a, b]
        assert admission._pressure_preference(admission.policy.programs["a"], 0.) is not None

        control.resume_only_work_ranking = True
        assert gate._ordered(queued) == queued
        assert admission.order_tool_replacement([b, a]) == [b, a]
        assert admission._pressure_preference(admission.policy.programs["a"], 0.) is None
        status = admission.status()
        assert not status["workflow_distribution_changes_pressure_victims"]
        assert not status["workflow_distribution_changes_tool_replacement_order"]
    finally:
        runtime.close()


@pytest.mark.parametrize("source", ("periodic_decay_resume", "undiscounted_free_dispatch"))
@pytest.mark.parametrize("ready", (False, True))
def test_strict_empty_shortlist_skips_native_capacity(monkeypatch, tmp_path, source, ready):
    runtime, admission = strict_runtime(monkeypatch, tmp_path)
    control = admission._combination
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)

    def unexpected_capacity_check(*args):
        pytest.fail("empty shortlist should not inspect native capacity")

    monkeypatch.setattr(control, "_resources", unexpected_capacity_check)
    try:
        keys = list(admission.latest.values()) if ready else []
        capacity = admission.policy.buffer_tokens + 1
        assert control.select(keys, [], capacity=capacity, maximum=4, source=source) == []
        choice, = records
        assert choice["decision"]["reason"] == "no_competing_choice"
        assert choice["resources"] is None
        assert not any(item.active for item in admission.policy.programs.values())
    finally:
        runtime.close()


def test_strict_choice_changes_actual_eligibility_and_native_candidates(monkeypatch, tmp_path):
    runtime, admission = strict_runtime(monkeypatch, tmp_path)
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    try:
        runtime._join_priority_pending["b"] = "stale-join"
        admission.tick()
        assert {row.identity for row in admission.policy.programs.values() if row.active} == {"a"}
        requests = list(admission._live_requests.values())
        plan = runtime.plan_native_prefill(requests, running_batch=NS(reqs=[]), adder=None)
        actual = select_native_prefill_candidates(
            requests, plan=plan, current_semantic_revision=runtime.semantic_revision,
        )
        assert actual.candidates == (admission._live_requests["a-request"],)
        choices = [row for row in records if row["event"] == "workflow_combination_selection"]
        assert choices and all(row["selection_policy"] == "strict_remaining_work" for row in choices)
        assert not any("b" in row["decision"].get("required_reference_members", ()) for row in choices)
        validate_selection_policy(choices, shortest_work_first=True, strict_remaining_work=True)
        assert admission.status()["workflow_public_timeout_force_resume"] is False
    finally:
        runtime.close()


@pytest.mark.parametrize("mode, capacity, blocked_peer, expected", (
    ("all", 500, False, {"child-0", "child-1"}),
    ("all", 499, False, {"child-0"}),
    ("any", 500, False, {"child-0"}),
    ("all", 500, True, {"child-0"}),
))
def test_free_dispatch_considers_ready_sibling_outside_rotating_window(
    monkeypatch, tmp_path, mode, capacity, blocked_peer, expected,
):
    _check_indexed_sibling_dispatch(monkeypatch, tmp_path, mode, capacity, blocked_peer, expected)


def test_resume_only_excludes_unmaterialized_indexed_sibling(monkeypatch, tmp_path):
    _check_indexed_sibling_dispatch(
        monkeypatch, tmp_path, "all", 500, False, {"child-0"}, first_prefill=True,
    )


def _check_indexed_sibling_dispatch(
    monkeypatch, tmp_path, mode, capacity, blocked_peer, expected, *, first_prefill=False,
):
    runtime, admission = strict_runtime(monkeypatch, tmp_path)
    try:
        from beliefkv.runtime.join_sibling_service import JoinSiblingService

        admission._siblings = JoinSiblingService(admission)
        admission.policy.capacity_tokens = capacity
        admission.max_continuation_checks = 1
        admission.next_tick = float("inf")
        admission._combination.next_dispatch = float("inf")
        group(runtime, mode=mode, sequence=100)
        for owner in ("child-0", "child-1"):
            req = request(owner, owner, tokens=150, workflow="wf")
            req.output_ids.clear()
            runtime.register_visible_request(req)
        if first_prefill:
            admission._combination.resume_only_work_ranking = True
            mark_unmaterialized(runtime, (admission.latest["child-1"],))
        if blocked_peer:
            runtime.on_events((observed(
                110, Kind.TOOL_START, invocation_id="child-1", context_id="child-1-context",
                attributes={"tool_call_id": "tool"},
            ),))
        admission._work._predictions["wf"] = WeightedPrediction(
            (1., 0., 0.), 0, 1, time.monotonic(), "model", .9,
        )
        admission._dispatch_pending.move_to_end("child-0", last=False)
        records = []
        runtime._opportunity_writer = NS(record=records.append, close=lambda: None)

        assert admission._dispatch_free_capacity()
        assert {item.identity for item in admission.policy.programs.values() if item.active} == expected
        assert admission.policy.remaining_capacity() >= 0
        choice = next(row for row in records if row["event"] == "workflow_combination_selection")
        assert [key["invocation_id"] for key in choice.get("indexed_sibling_candidates", ())] == (
            ["child-1"] if mode == "all" and not blocked_peer and not first_prefill else []
        )
        requests = list(admission._live_requests.values())
        plan = runtime.plan_native_prefill(requests, running_batch=NS(reqs=[]), adder=None)
        actual = select_native_prefill_candidates(
            requests, plan=plan, current_semantic_revision=runtime.semantic_revision,
        )
        assert {req.rid for req in actual.candidates} == expected
    finally:
        runtime.close()


def test_full_reference_replacement_reaches_native_service(
    monkeypatch, tmp_path,
):
    runtime, admission = strict_runtime(monkeypatch, tmp_path)
    admission.policy.buffer_tokens = 0
    control = admission._combination
    control.window_ms, control.next_dispatch = 2000, float("inf")
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    try:
        for index in range(8):
            owner = "small-" + str(index)
            runtime.on_events((
                replace(observed(100 + index * 2, Kind.WORKFLOW_START), workflow_id=owner),
                replace(observed(
                    101 + index * 2, Kind.INVOCATION_CREATE,
                    invocation_id=owner, context_id=owner + "-context",
                ), workflow_id=owner),
            ))
            req = request(owner + "-request", owner, tokens=60, workflow=owner)
            req.output_ids.clear()
            runtime.register_visible_request(req)
        admission._work._predictions.update({
            owner: WeightedPrediction(
                (1., 0., 0.), 0, 1, time.monotonic(), "model", .9 if owner == "a" else .001,
            ) for owner in admission.policy.programs
        })
        control.window_ms, control.next_dispatch = 0, 0.
        admission.next_tick = 0.
        admission.tick()
        choice = next(
            row for row in records if row["event"] == "workflow_combination_selection"
            and row["source"] == "periodic_decay_resume"
        )
        assert len(choice["decision"]["reference"]) == len(choice["candidates"]) == 8
        assert choice["decision"]["reference_metadata_complete"] is False
        assert "a" in choice["decision"]["selected"]
        assert admission.policy.programs["a"].active
        assert admission.policy.remaining_capacity() >= 0
        # This native selection fixture is sessionless; it cannot attest the
        # separate session-bound journal contract used by the GPU workload.
        with pytest.raises(ValueError, match="session ownership"):
            replay_choice(choice, control.costs, control.cost_identity)
        requests = list(admission._live_requests.values())
        plan = runtime.plan_native_prefill(requests, running_batch=NS(reqs=[]), adder=None)
        actual = select_native_prefill_candidates(
            requests, plan=plan, current_semantic_revision=runtime.semantic_revision,
        )
        assert admission._live_requests["a-request"] in actual.candidates
    finally:
        runtime.close()


@pytest.mark.parametrize("strict", (False, True))
def test_legacy_timeout_is_retained_but_cannot_bypass_strict_priority(monkeypatch, tmp_path, strict):
    runtime, admission = strict_runtime(monkeypatch, tmp_path)
    admission._combination.strict_remaining_work = strict
    now = time.monotonic()
    for request, (key, _arrived) in tuple(admission.requests.items()):
        admission.requests[request] = key, now - 1801.
    try:
        admission.tick(now=now)
        active = {row.identity for row in admission.policy.programs.values() if row.active}
        assert active == ({"a"} if strict else {"a", "b", "c"})
        assert admission.counts["public_timeout_force_resumed"] == (0 if strict else 2)
        if strict:
            assert admission.policy.remaining_capacity() >= 0
    finally:
        runtime.close()


@pytest.mark.parametrize("deadline", (None, 1, 2))
def test_strict_journal_exactly_replays_original_cutoff_and_external_reference(deadline):
    row, costs = recorded_aging()
    rows = [replace(value, weight=.9) for value in fixture()[0][:1]]
    # Keep the original exact identity for the retained short candidate.
    row["candidates"] = [asdict(value) for value in rows]
    row["selection_policy"] = "strict_remaining_work"
    row.pop("ready_wait_priority")
    calls = -1

    def clock():
        nonlocal calls
        calls += 1
        return 500000 if deadline is not None and calls >= deadline else 0

    row["decision"] = choose_shortest_work_first(
        rows, ("long-a", "long-b"), fixture()[1], costs,
        strict_remaining_work=True, clock=clock,
    )
    row = json.loads(json.dumps(row))
    result = replay_choice(
        row, costs, "frozen", allow_feasible_resource_alternatives=True, plan_device_capacity=True,
    )
    assert result["search_replayed"]
    assert result["selected"] == list(row["decision"]["selected"])
    assert result["search_complete"] == row["decision"]["search_complete"]


def test_strict_preparation_preserves_forecast_traffic_and_disables_all_wait_overrides(tmp_path):
    paths = inputs(tmp_path)
    output = tmp_path / "strict"
    originals = {name: path.read_bytes() for name, path in paths.items()}
    report = prepare(paths["learned"], paths["oracle"], output)
    assert report["GPU_action"] is report["Full_rerun_registered"] is False
    for name, path in paths.items():
        assert path.read_bytes() == originals[name]
        old = json.loads(originals[name])
        current = json.loads((output / (name + ".json")).read_bytes())
        for field in ("workflow_arrivals", "private_analysis_cohort", "tool_timing_overlay"):
            assert old.get(field) == current.get(field)
        raw = json.loads((output / (name + "_work.json")).read_bytes())
        assert strict_remaining_work_enabled(raw)
        assert raw["ready_wait_priority_ms"] == raw["ready_capacity_accumulation_ms"] == 0
        assert raw["cost_aware_maximal_admission"] is False
        assert not any(key in raw for key in (
            "ready_wait_priority_grace_ms", "ready_wait_priority_max_boost", "maximal_admission_objective",
        ))
    with pytest.raises(FileExistsError):
        prepare(paths["learned"], paths["oracle"], output)


def test_oracle_and_learned_cannot_silently_use_different_priority_rules():
    learned, oracle, model, truth = pair()
    for raw in (model, truth):
        raw.update(strict_remaining_work_admission=True, shortest_work_first_admission=True)
    verify_same_objective(learned, oracle, model, truth)
    truth.pop("strict_remaining_work_admission")
    with pytest.raises(ValueError, match="differs"):
        verify_same_objective(learned, oracle, model, truth)


def test_strict_oracle_cannot_silently_rank_by_legacy_bins_or_tool_path():
    raw = source()
    raw.update(strict_remaining_work_admission=True, shortest_work_first_admission=True)
    with pytest.raises(ValueError, match="continuous model-work"):
        WorkflowWorkOracleArtifact(raw)


def test_fixed_state_audit_separates_policy_changes_without_future_jct():
    row, costs = recorded_aging()
    before = deepcopy(row)
    report = compare([row], {"identity": "frozen", "costs": asdict(costs)})
    assert report["variants"]["observed"]["feasible_prefix_priority_inversion_pairs"] == 2
    assert report["variants"]["strict_remaining_work"]["feasible_prefix_priority_inversion_pairs"] == 0
    assert report["new_shortlists_queue_native_service_migration_or_JCT_not_simulated"] is True
    assert report["performance_accepted"] is False
    assert row == before


def test_policy_comparison_does_not_treat_a_different_algorithm_as_the_same_deadline():
    row, costs = recorded_aging(deadline=1)
    report = compare([row], {"identity": "frozen", "costs": asdict(costs)})
    assert report["counts"]["exact_actual_searches_replayed"] == 1
    assert report["counts"]["actual_incomplete_searches_excluded_from_alternative_selection"] == 1
    assert not report["decisions"]
