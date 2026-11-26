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
from beliefkv.runtime.subagent_materialization import (
    MaterializationHeadroom, SubagentMaterialization, _InitialRequest,
)
from beliefkv.core.events import RuntimeEventKind as Kind
from scripts.audit_r5_shortest_contract import inspect_choice
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


@pytest.mark.parametrize("reference_in_window", (False, True))
def test_service_capacity_guard_keeps_reference_count(reference_in_window):
    rows, resources, costs = fixture()
    if not reference_in_window:
        rows = rows[:1]
    reference = ("long-a", "long-b")
    result = choose(rows, reference, resources, costs, preserve_reference_service_capacity=True)
    assert result["selected"] == reference
    assert result["ranked_selected"] == ("short",)
    assert result["reason"] == "shortest_work_reference_count_preserved"
    assert result["search_complete"]


def test_service_capacity_guard_keeps_immediately_fitting_reference():
    rows, resources, costs = fixture()
    short = replace(rows[0], missing_extents=((("short", 1.), 20),))
    reference = ("long-a",)
    call = partial(choose, [short, rows[1]], reference, costs=costs,
                   preserve_reference_service_capacity=True)
    guarded = call(resources=replace(resources, device_tokens=19))
    assert guarded["selected"] == reference
    assert guarded["ranked_selected"] == ("short",)
    assert guarded["reason"] == "shortest_work_reference_immediate_fit_preserved"
    fitting = call(resources=replace(resources, device_tokens=20))
    assert fitting["selected"] == ("short",)
    assert fitting["reason"] == "shortest_work_set_changed"


def test_service_capacity_guard_needs_immediate_fit_to_replace_unpriced_reference():
    rows, resources, costs = fixture()
    short = replace(rows[0], missing_extents=((("short", 1.), 20),))
    reference = ("long-a",)
    call = partial(choose, [short], reference, costs=costs, preserve_reference_service_capacity=True)
    guarded = call(resources=replace(resources, device_tokens=19))
    assert guarded["selected"] == reference
    assert guarded["ranked_selected"] == ("short",)
    assert guarded["reference_metadata_complete"] is False
    assert guarded["reason"] == "shortest_work_reference_fit_unobserved"
    fitting = call(resources=replace(resources, device_tokens=20))
    assert fitting["selected"] == ("short",)


@pytest.mark.parametrize("missing", (19, 20, 21))
def test_service_capacity_guard_does_not_increase_an_existing_hbm_deficit(missing):
    rows, resources, costs = fixture()
    short = replace(rows[0], missing_extents=((("short", 1.), missing),))
    incumbent = replace(rows[1], missing_extents=((("long-a", 1.), 20),))
    options = [short, incumbent], ("long-a",), replace(resources, device_tokens=10), costs
    unguarded = choose(*options)
    assert unguarded["selected"] == ("short",)
    assert unguarded["reference_evaluation"]["current_device_summary_fits"] is False
    assert unguarded["selected_evaluation"]["current_device_summary_fits"] is False
    guarded = choose(*options, preserve_reference_service_capacity=True)
    assert guarded["selected"] == (("long-a",) if missing > 20 else ("short",))
    if missing > 20:
        assert guarded["ranked_selected"] == ("short",)
        assert guarded["reason"] == "shortest_work_reference_device_deficit_preserved"


def test_service_capacity_retry_preserves_count_with_a_fitting_shorter_candidate():
    rows, resources, costs = fixture()
    rows[0] = replace(rows[0], missing_extents=((("short", 1.), 90),))
    medium = replace(
        rows[0], identity="medium", workflow="medium", group="medium", logical_tokens=110,
        weight=.4, missing_extents=((("medium", 1.), 12),),
    )
    candidates = [*rows, medium]
    reference = ("long-a", "long-b")
    resources = replace(resources, device_tokens=20)
    assert choose(candidates, reference, resources, costs)["selected"] == ("short",)
    result = choose(candidates, reference, resources, costs, preserve_reference_service_capacity=True)
    assert result["selected"] == ("medium", "long-a")
    assert result["selected_evaluation"]["current_device_summary_fits"]
    assert result["service_capacity_retry"] == "shortest_work_reference_count_preserved"
    audit = inspect_choice({
        "monotonic_s": 1., "source": "periodic_decay_resume", "decision": result,
        "candidates": [asdict(row) for row in candidates], "resources": asdict(resources),
        "preserve_reference_service_capacity": True,
    }, costs)
    assert not audit["short_priority_inversions"]
    assert not audit["unlimited_CPU_choice_changed"]

    ticks = iter((0, 0, 0, 0, 0, 500_000, 500_001))
    expired = choose_shortest_work_first(
        candidates, reference, resources, costs, strict_remaining_work=True,
        preserve_reference_service_capacity=True, clock=lambda: next(ticks),
    )
    assert expired["selected"] == reference
    assert expired["reason"] == "shortest_work_soft_deadline_reference_fallback"
    assert not expired["search_complete"]


@pytest.mark.parametrize("free", (10, 20))
def test_service_capacity_retry_keeps_join_rights_and_counts_shared_sibling_kv_once(free):
    rows, resources, costs = fixture()
    rows[0] = replace(rows[0], logical_tokens=50, missing_extents=((("short", 1.), 90),))
    rows[2] = replace(rows[2], missing_extents=((("long-b", 1.), 20),))
    first = replace(
        rows[0], identity="first", workflow="peer", group="join", logical_tokens=30, category=1,
        weight=.4, missing_extents=((("shared", 1.), 16),), unfinished_members=2,
    )
    second = replace(first, identity="second", category=0)
    result = choose(
        [*rows, first, second], ("long-a", "long-b"), replace(resources, device_tokens=free), costs,
        required=("long-a",), preserve_reference_service_capacity=True,
    )
    assert set(result["selected"]) == {"long-a", "first", "second"}
    assert result["selected_evaluation"]["required_device_tokens"] == 16
    assert result["reference_evaluation"]["required_device_tokens"] == 20
    assert result["service_capacity_retry"] == (
        "shortest_work_reference_immediate_fit_preserved" if free == 20 else
        "shortest_work_reference_device_deficit_preserved"
    )


@pytest.mark.parametrize("options", (
    {"preserve_reference_service_capacity": True},
    {
        "strict_remaining_work_admission": True, "shortest_work_first_admission": True,
        "preserve_reference_service_capacity": 1,
    },
))
def test_service_capacity_guard_requires_strict_and_boolean_artifact(options):
    with pytest.raises(ValueError, match="reference service capacity guard"):
        strict_remaining_work_enabled(options)


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
def test_service_capacity_guard_is_applied_at_both_selection_entries(monkeypatch, tmp_path, source):
    runtime, admission = strict_runtime(monkeypatch, tmp_path)
    control = admission._combination
    control.preserve_reference_service_capacity = True
    a, b, c = (admission.latest[owner] for owner in ("a", "b", "c"))
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    try:
        result = control.select([a, b, c], [b, c], capacity=500, maximum=4, source=source)
        assert result == [b, c]
        assert admission.status()["workflow_preserve_reference_service_capacity"]
        assert records[-1]["decision"]["reason"] == "shortest_work_reference_count_preserved"
        verify = partial(
            validate_selection_policy, records, shortest_work_first=True, strict_remaining_work=True,
        )
        verify(preserve_reference_service_capacity=True)
        with pytest.raises(ValueError, match="recorded service capacity guard"):
            verify()
    finally:
        runtime.close()


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


@pytest.mark.parametrize(("source", "capacity", "headroom", "expected", "reason"), (
    ("periodic_decay_resume", 650, 4096, ("b", "a"), "shortest_work_set_changed"),
    ("periodic_decay_resume", 500, 4096, ("b", "c"), "initial_materialization_reference_preserved"),
    ("periodic_decay_resume", 650, 0, ("b", "c"), "resume_pressure_reference"),
    ("undiscounted_free_dispatch", 650, 4096, ("b", "c"), "initial_materialization_reference_preserved"),
))
def test_adaptive_mixed_window_preserves_first_grants_and_ranks_residual(
    monkeypatch, tmp_path, source, capacity, headroom, expected, reason,
):
    from tests import test_workflow_combination_runtime as fixture_module

    def bound_request(*args, **kwargs):
        req = request(*args, **kwargs)
        req.session_id, req.session_generation = req.rid + "-session", 0
        return req

    monkeypatch.setattr(fixture_module, "request", bound_request)
    runtime, admission = strict_runtime(monkeypatch, tmp_path)
    control = admission._combination
    control.resume_only_work_ranking = control.adaptive_resume_ranking = True
    control.preserve_reference_service_capacity = True
    a, b, c = (admission.latest[owner] for owner in ("a", "b", "c"))
    gate = mark_unmaterialized(runtime, (b,))
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    try:
        gate._observe_resume_ranking(
            MaterializationHeadroom(True, device_free_tokens=headroom), 0, time.monotonic(),
        )
        selected = control.select([a, b, c], [b, c], capacity=capacity, maximum=4, source=source)
        assert tuple(key.invocation_id for key in selected) == expected
        choice, = records
        assert choice["reference_initial_materialization_members"] == ["b"]
        assert choice["decision"]["reason"] == reason
        assert gate.pending[b.request_id].permitted and gate.awaiting_first_prefill(b)
        if expected == ("b", "a"):
            assert choice["decision"]["required_reference_members"] == ("b",)
            assert sum(
                row["logical_tokens"] for row in choice["candidates"] if row["identity"] in expected
            ) == 650
            replayed = replay_choice(choice, control.costs, control.cost_identity)
            assert replayed["search_replayed"] and replayed["selected"] == ["b", "a"]
        else:
            assert not choice["candidates"]
    finally:
        runtime.close()


@pytest.mark.parametrize("condition", ("missing_first_grant_quote", "shorter_set_cannot_fit_now"))
def test_adaptive_mixed_window_preserves_reference_when_native_checks_fail(
    monkeypatch, tmp_path, condition,
):
    runtime, admission = strict_runtime(monkeypatch, tmp_path)
    control = admission._combination
    control.resume_only_work_ranking = control.adaptive_resume_ranking = True
    control.preserve_reference_service_capacity = True
    a, b, c = (admission.latest[owner] for owner in ("a", "b", "c"))
    gate = mark_unmaterialized(runtime, (b,))
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    try:
        gate._observe_resume_ranking(
            MaterializationHeadroom(True, device_free_tokens=4096), 0, time.monotonic(),
        )
        if condition == "missing_first_grant_quote":
            quote = control._prefix

            def missing_first_quote(key, item):
                if key == b:
                    control._prefix_issue = "unobservable_missing_closure"
                    return None
                return quote(key, item)

            monkeypatch.setattr(control, "_prefix", missing_first_quote)
            reason = "reference_candidate_summary_unavailable"
        else:
            def limited_headroom(capacity, maximum):
                return replace(resources(capacity, maximum), device_tokens=400, device_capacity_tokens=1000)

            resources = control._resources
            monkeypatch.setattr(control, "_resources", limited_headroom)
            reason = "shortest_work_reference_immediate_fit_preserved"
        assert control.select(
            [a, b, c], [b, c], capacity=650, maximum=4, source="periodic_decay_resume",
        ) == [b, c]
        choice, = records
        assert choice["decision"]["reason"] == reason
        assert gate.pending[b.request_id].permitted and gate.awaiting_first_prefill(b)
    finally:
        runtime.close()


def test_periodic_control_publishes_mixed_grants_without_changing_d3_permit(monkeypatch, tmp_path):
    runtime, admission = strict_runtime(monkeypatch, tmp_path)
    control = admission._combination
    control.resume_only_work_ranking = control.adaptive_resume_ranking = True
    control.preserve_reference_service_capacity = True
    a, b, c = (admission.latest[owner] for owner in ("a", "b", "c"))
    gate = mark_unmaterialized(runtime, (b,))
    admission.policy.capacity_tokens = 650
    programs = admission.policy.programs
    programs["a"].steps = programs["c"].steps = 2
    programs["c"].tokens = 180
    admission._live_requests[c.request_id].origin_input_ids = list(range(180))
    monkeypatch.setattr(runtime, "_read_request_reentry", lambda *_args, **_kwargs: {
        "checkpoint_tokens": 0, "missing_full_tokens": 0, "missing_mamba_slots": 0,
    })
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    now = time.monotonic()
    try:
        gate._observe_resume_ranking(MaterializationHeadroom(True, device_free_tokens=4096), 0, now)
        admission.tick(now=now)
        choice = next(
            row for row in records if row.get("source") == "periodic_decay_resume"
        )
        assert choice["decision"]["reference"] == ("c", "b")
        assert choice["decision"]["selected"] == ("b", "a")
        assert admission.allows(b) and admission.allows(a) and not admission.allows(c)
        assert admission.policy.remaining_capacity() == 0
        assert gate.pending[b.request_id].permitted and gate.awaiting_first_prefill(b)
    finally:
        runtime.close()


@pytest.mark.parametrize("source", ("periodic_decay_resume", "undiscounted_free_dispatch"))
def test_adaptive_resume_ranking_reverts_without_pricing_candidates(monkeypatch, tmp_path, source):
    runtime, admission = strict_runtime(monkeypatch, tmp_path)
    control = admission._combination
    control.resume_only_work_ranking = control.adaptive_resume_ranking = True
    control.preserve_reference_service_capacity = True
    admission._conditional_distribution = True
    a, b, c = (admission.latest[owner] for owner in ("a", "b", "c"))
    gate = mark_unmaterialized(runtime, ())
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    select = partial(control.select, capacity=500, maximum=4, source=source)
    try:
        now = time.monotonic()
        gate._observe_resume_ranking(MaterializationHeadroom(True), 0, now)
        with monkeypatch.context() as scoped:
            def unexpected_prediction(*args):
                pytest.fail("the reference selector should not consult the predictor")

            scoped.setattr(control, "_work_weight", unexpected_prediction)
            scoped.setattr(control, "prediction", unexpected_prediction)
            assert admission._rank(a.invocation_id, now) == 1
            assert select([a, b, c], [b, c]) == [b, c]
            assert records[-1]["decision"]["reason"] == "resume_pressure_reference"
            assert not records[-1]["candidates"]
            replayed = replay_choice(records[-1], control.costs, control.cost_identity)
            assert replayed["fallback_reference_preserved"] and replayed["selected"] == ["b", "c"]

        gate._observe_resume_ranking(
            MaterializationHeadroom(True, device_free_tokens=4096), 0, now,
        )
        assert select([a, b, c], [b]) == [a]
        assert records[-1]["decision"]["reason"] == "shortest_work_set_changed"
        assert admission.status()["workflow_adaptive_resume_ranking"]
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
@pytest.mark.parametrize("preserve_capacity", (False, True))
def test_strict_journal_exactly_replays_original_cutoff_and_external_reference(deadline, preserve_capacity):
    row, costs = recorded_aging()
    rows = [replace(value, weight=.9) for value in fixture()[0][:1]]
    # Keep the original exact identity for the retained short candidate.
    row["candidates"] = [asdict(value) for value in rows]
    row["selection_policy"] = "strict_remaining_work"
    if preserve_capacity:
        row["preserve_reference_service_capacity"] = True
    row.pop("ready_wait_priority")
    calls = -1

    def clock():
        nonlocal calls
        calls += 1
        return 500000 if deadline is not None and calls >= deadline else 0

    row["decision"] = choose_shortest_work_first(
        rows, ("long-a", "long-b"), fixture()[1], costs,
        strict_remaining_work=True, clock=clock,
        preserve_reference_service_capacity=preserve_capacity,
    )
    row = json.loads(json.dumps(row))
    result = replay_choice(
        row, costs, "frozen", allow_feasible_resource_alternatives=True, plan_device_capacity=True,
    )
    assert result["search_replayed"]
    assert result["selected"] == list(row["decision"]["selected"])
    assert result["search_complete"] == row["decision"]["search_complete"]
    if preserve_capacity and deadline is None:
        assert result["selected"] == ["long-a", "long-b"]
        mismatched = dict(row, preserve_reference_service_capacity=False)
        with pytest.raises(ValueError, match="does not reproduce"):
            replay_choice(
                mismatched, costs, "frozen", allow_feasible_resource_alternatives=True,
                plan_device_capacity=True,
            )


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
