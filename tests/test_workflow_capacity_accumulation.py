from dataclasses import replace
import hashlib
import json
import time
from types import SimpleNamespace as NS

import pytest

from beliefkv.policy.workflow_capacity_hold import CapacityHoldCandidate as Candidate, choose_capacity_hold
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from beliefkv.runtime.workflow_capacity_accumulation import ReadyCapacityAccumulation
from beliefkv.runtime.workflow_combination import WorkflowCombinationController
from scripts.audit_workflow_combination_choices import replay_choice, validate_selection_policy
from tests.test_internal_comparison import observed
from tests.test_paper_baseline_runtime import request
from tests.test_ready_wait_priority import WeightedPrediction, configured_priority
from tests.test_workflow_combination_runtime import runtime_with_queue
from beliefkv.runtime.ready_wait_priority import ReadyWaitPriority
from beliefkv.core.events import RuntimeEventKind as Kind


def candidates():
    return [
        Candidate("focus", "short", 120, .8, .9, 60000., 1),
        Candidate("left", "long-left", 60, .1, .1, 0., 2),
        Candidate("right", "long-right", 40, .1, .1, 0., 3),
    ]


def choose(rows, reference=("left", "right"), **kwargs):
    return choose_capacity_hold(rows, reference, **{
        "capacity": 100, "pool_capacity": 500, "minimum_wait_ms": 30000,
        "running_width": 2, **kwargs,
    })


def test_only_lower_priority_new_grants_are_held_without_any_capacity_credit():
    result = choose(candidates())
    assert result["focus"] == "focus"
    assert result["selected"] == ()
    assert result["held_reference_tokens"] == 100
    assert result["focus_logical_gap_tokens"] == 20


def test_join_higher_priority_and_same_workflow_members_keep_their_reference_rights():
    rows = [
        candidates()[0],
        Candidate("join", "parent", 20, .001, .001, 0., protected=True),
        Candidate("higher", "shorter", 10, .95, .95, 0.),
        Candidate("sibling", "short", 10, .1, .1, 0.),
        Candidate("lower", "long", 60, .1, .1, 0.),
    ]
    result = choose(rows, ("join", "higher", "sibling", "lower"))
    assert result["selected"] == ("join", "higher", "sibling")
    assert result["held_reference"] == ("lower",)


@pytest.mark.parametrize("width", (0, 1))
def test_no_hold_when_fewer_than_two_requests_are_actually_running(width):
    assert choose(candidates(), running_width=width) is None


@pytest.mark.parametrize("patch", (
    {"ready_wait_ms": 29999.}, {"logical_tokens": 100}, {"logical_tokens": 501},
    {"protected": True}, {"supported": False}, {"base_weight": .09}, {"weight": .09},
))
def test_wait_fit_support_and_priority_guards_keep_the_reference(patch):
    rows = candidates()
    rows[0] = replace(rows[0], **patch)
    assert choose(rows) is None


def test_new_hold_requires_the_withheld_budget_to_cover_the_current_gap():
    rows = candidates()
    rows[0] = replace(rows[0], logical_tokens=250)
    assert choose(rows) is None
    assert choose(rows, focus="focus")["continuing_existing_hold"]


@pytest.mark.parametrize("peer", (
    Candidate("other", "shorter", 10, .95, .95, 0.),
    Candidate("other", "aged", 10, .1, 1., 60000.),
    Candidate("other", "parent", 10, .01, .01, 0., protected=True),
    Candidate("other", "short", 10, .8, .9, 60000.),
))
@pytest.mark.parametrize("continuing", (False, True))
def test_hold_cannot_hide_a_fitting_priority_join_or_sibling_outside_the_reference(peer, continuing):
    assert choose(candidates() + [peer], focus="focus" if continuing else None) is None


@pytest.mark.parametrize("patch", (
    {"logical_tokens": True}, {"weight": float("nan")}, {"base_weight": True},
    {"ready_wait_ms": -1.}, {"ready_wait_ms": True}, {"protected": 1},
))
def test_invalid_metadata_cannot_create_an_unchecked_hold(patch):
    rows = candidates()
    rows[0] = replace(rows[0], **patch)
    with pytest.raises(ValueError):
        choose(rows)


@pytest.mark.parametrize("value", (True, -1, 500, 2001, 2., "2000", None))
def test_constructor_requires_an_explicit_matching_ordinary_window(tmp_path, value):
    control = configured_priority(tmp_path, 30000)
    path = control.admission.runtime.workflow_work_model
    raw = json.loads(path.read_bytes())
    raw["free_dispatch_coalescing_window_ms"] = 2000
    raw["ready_capacity_accumulation_ms"] = value
    path.write_text(json.dumps(raw))
    control.admission.runtime.workflow_work_model_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="capacity accumulation"):
        WorkflowCombinationController(control.admission)


def test_constructor_is_default_off_and_requires_wait_priority_and_benefit(tmp_path):
    control = configured_priority(tmp_path, 30000)
    assert control.capacity_accumulation is None
    path = control.admission.runtime.workflow_work_model
    raw = json.loads(path.read_bytes())
    raw["free_dispatch_coalescing_window_ms"] = raw["ready_capacity_accumulation_ms"] = 2000
    path.write_text(json.dumps(raw))
    control.admission.runtime.workflow_work_model_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    assert WorkflowCombinationController(control.admission).capacity_accumulation.max_wait_ms == 2000
    raw["ready_wait_priority_ms"] = 0
    path.write_text(json.dumps(raw))
    control.admission.runtime.workflow_work_model_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="capacity accumulation"):
        WorkflowCombinationController(control.admission)


def runtime_fixture(monkeypatch, tmp_path):
    from functools import partial
    from beliefkv.policy import workflow_maximal_admission
    from tests import test_workflow_combination_runtime as fixture

    original_request = fixture.request

    def bound_request(*args, **kwargs):
        req = original_request(*args, **kwargs)
        req.session_id, req.session_generation = "session-" + req.rid, 0
        return req

    monkeypatch.setattr(fixture, "request", bound_request)
    runtime, admission, _env = runtime_with_queue(monkeypatch, tmp_path)
    control = admission._combination
    control.shortest_work_first = control.cost_aware_maximal = True
    control.maximal_objective = "benefit"
    control.costs = replace(control.costs, decode_cost_basis="full_batch", selection_objective="benefit")
    admission.policy.buffer_tokens = 0
    control.window_ms = 2000
    monkeypatch.setattr(
        workflow_maximal_admission, "choose_maximal_admission",
        partial(workflow_maximal_admission.choose_maximal_admission, clock=lambda: 0),
    )
    busy = []
    for index in range(2):
        owner = "busy-" + str(index)
        runtime.on_events((
            replace(observed(index * 2 + 100, Kind.WORKFLOW_START), workflow_id=owner),
            replace(observed(
                index * 2 + 101, Kind.INVOCATION_CREATE,
                invocation_id=owner, context_id=owner + "-context",
            ), workflow_id=owner),
        ))
        req = request(owner + "-request", owner, tokens=125, workflow=owner)
        req.output_ids.clear()
        runtime.register_visible_request(req)
        admission.policy.programs[owner].active = True
        busy.append(req)
    admission._work._predictions.update({
        owner: WeightedPrediction(
            (1., 0., 0.), 0, 1, time.monotonic(), "model", .8 if owner == "a" else .1,
        ) for owner in admission.policy.programs
    })
    runtime._native_running_batch = NS(reqs=busy)
    clock = [time.monotonic()]
    control.ready_wait_priority = ReadyWaitPriority(admission, 30000)
    control.ready_wait_priority.begin([admission.latest["a"]], clock[0] - 60.)
    control.capacity_accumulation = ReadyCapacityAccumulation(
        control, 2000, clock=lambda: clock[0],
    )
    records = []
    runtime._opportunity_writer = NS(record=records.append)
    admission.next_tick, control.next_dispatch = 0., clock[0]
    return runtime, admission, control, busy, clock, records


def test_capacity_release_allows_the_waiting_target_to_reach_actual_prefill(monkeypatch, tmp_path):
    runtime, admission, control, busy, clock, records = runtime_fixture(monkeypatch, tmp_path)
    try:
        admission.tick(now=clock[0])
        assert control.capacity_accumulation.focus == admission.latest["a"]
        assert not any(admission.policy.programs[owner].active for owner in ("a", "b", "c"))
        assert admission.policy.remaining_capacity() == 250
        hold = next(row for row in records if row.get("capacity_accumulation"))
        assert replay_choice(hold, control.costs, control.cost_identity)["capacity_hold_replayed"]
        admission.forget("busy-0", now=clock[0] + 1.)
        runtime._native_running_batch = NS(reqs=busy[1:])
        clock[0] += 1.
        admission.next_tick = 0.
        admission.tick(now=clock[0])
        assert admission.policy.programs["a"].active
        assert not admission.policy.programs["b"].active
        assert not admission.policy.programs["c"].active
        assert admission.policy.remaining_capacity() == 75
        queue = [admission._live_requests[owner + "-request"] for owner in ("a", "b", "c")]
        plan = runtime.plan_native_prefill(queue, running_batch=runtime._native_running_batch, adder=None)
        selection = select_native_prefill_candidates(
            queue, plan=plan, current_semantic_revision=runtime.semantic_revision,
        )
        assert [req.rid for req in selection.candidates] == ["a-request"]
        assert not runtime.physical_ledger.pending_count
        assert control.capacity_accumulation.focus is None
        assert admission.counts["ready_capacity_accumulation_insufficient_running_work"] == 1
    finally:
        runtime._opportunity_writer = None
        runtime.close()


@pytest.mark.parametrize("kind", (
    Kind.TOOL_START, Kind.JOIN_WAIT, Kind.RETURN, Kind.INVOCATION_CANCEL,
))
def test_blocking_or_retirement_revokes_only_its_matching_capacity_focus(monkeypatch, tmp_path, kind):
    from tests.test_tool_predictive_transfers import event

    runtime, admission, control, _busy, clock, _records = runtime_fixture(monkeypatch, tmp_path)
    try:
        admission.tick(now=clock[0])
        accumulation = control.capacity_accumulation
        accumulation.events((event(50, kind, invocation_id="unrelated"),))
        assert accumulation.focus == admission.latest["a"]
        accumulation.events((event(51, kind, invocation_id="a"),))
        assert accumulation.focus is None
        assert accumulation.next_start > clock[0] + 2.
    finally:
        runtime._opportunity_writer = None
        runtime.close()


def test_changed_request_epoch_cannot_inherit_a_capacity_hold(monkeypatch, tmp_path):
    runtime, admission, control, _busy, clock, _records = runtime_fixture(monkeypatch, tmp_path)
    try:
        admission.tick(now=clock[0])
        admission.latest["a"] = replace(admission.latest["a"], request_id="next-a", context_epoch=1)
        assert control.capacity_accumulation.consider(
            [admission.latest["b"]], [admission.latest["b"]], 250, {}, "undiscounted_free_dispatch"
        ) is None
        assert control.capacity_accumulation.focus is None
        assert admission.counts["ready_capacity_accumulation_identity_changed"] == 1
    finally:
        runtime._opportunity_writer = None
        runtime.close()


def test_cached_target_age_survives_a_smaller_free_dispatch_window(monkeypatch, tmp_path):
    runtime, admission, control, _busy, clock, _records = runtime_fixture(monkeypatch, tmp_path)
    try:
        admission.tick(now=clock[0])
        only = [admission.latest["b"]]
        clock[0] += .5
        control.ready_wait_priority.begin(only, clock[0])
        decision = control.capacity_accumulation.consider(
            only, only, 250, {}, "undiscounted_free_dispatch",
        )
        assert decision == []
        focus = next(
            row for row in control.capacity_accumulation.last["candidates"]
            if row["identity"] == "a"
        )
        assert focus["ready_wait_ms"] >= 60500.
        assert .8 < focus["weight"] <= .8 * 1.25
    finally:
        runtime._opportunity_writer = None
        runtime.close()


def test_aging_tuning_does_not_change_capacity_wait_or_cooldown(monkeypatch, tmp_path):
    runtime, admission, control, _busy, clock, records = runtime_fixture(monkeypatch, tmp_path)
    try:
        control.ready_wait_priority = ReadyWaitPriority(admission, 120000, grace_ms=90000)
        control.ready_wait_priority.begin([admission.latest["a"]], clock[0] - 60.)
        admission.tick(now=clock[0])
        hold = next(row for row in records if row.get("capacity_accumulation"))
        assert hold["ready_wait_priority"]["scale_ms"] == 120000
        assert hold["capacity_accumulation"]["minimum_ready_wait_ms"] == 30000
        assert hold["capacity_accumulation"]["cooldown_ms"] == 30000
        assert replay_choice(hold, control.costs, control.cost_identity)["capacity_hold_replayed"]
        assert control.capacity_accumulation.next_start == pytest.approx(clock[0] + 30.)
    finally:
        runtime._opportunity_writer = None
        runtime.close()


@pytest.mark.parametrize("fault", ("selected", "deadline", "running_width", "key"))
def test_audit_rejects_fabricated_capacity_hold_or_changed_owner(monkeypatch, tmp_path, fault):
    from copy import deepcopy

    runtime, admission, control, _busy, clock, records = runtime_fixture(monkeypatch, tmp_path)
    try:
        admission.tick(now=clock[0])
        row = deepcopy(next(row for row in records if row.get("capacity_accumulation")))
        if fault == "selected":
            row["decision"]["selected"] = list(row["decision"]["reference"])
        elif fault == "deadline":
            row["capacity_accumulation"]["deadline_monotonic_s"] += 1.
        elif fault == "running_width":
            row["capacity_accumulation"]["running_width"] = 0
        else:
            key = next(iter(row["capacity_accumulation"]["keys"].values()))
            key["invocation_id"] = "foreign"
        with pytest.raises(ValueError):
            replay_choice(row, control.costs, control.cost_identity)
    finally:
        runtime._opportunity_writer = None
        runtime.close()


def test_free_dispatch_cannot_start_an_independent_hold_or_extend_a_deadline(monkeypatch, tmp_path):
    runtime, admission, control, _busy, clock, _records = runtime_fixture(monkeypatch, tmp_path)
    try:
        keys = [admission.latest[owner] for owner in ("a", "b", "c")]
        accumulation = control.capacity_accumulation
        assert accumulation.consider(
            keys, [admission.latest["b"]], 250, {}, "undiscounted_free_dispatch"
        ) is None
        assert accumulation.focus is None
        admission.tick(now=clock[0])
        deadline = accumulation.deadline
        clock[0] += .5
        accumulation.consider(
            keys, [admission.latest["b"]], 250, {}, "undiscounted_free_dispatch"
        )
        assert accumulation.deadline == deadline
        clock[0] = deadline
        assert accumulation.consider(
            keys, [admission.latest["b"]], 250, {}, "undiscounted_free_dispatch"
        ) is None
        assert accumulation.focus is None
    finally:
        runtime._opportunity_writer = None
        runtime.close()


def test_native_service_resets_only_the_exact_focused_request(monkeypatch, tmp_path):
    runtime, admission, control, _busy, clock, _records = runtime_fixture(monkeypatch, tmp_path)
    try:
        admission.tick(now=clock[0])
        accumulation = control.capacity_accumulation
        req = admission._live_requests["a-request"]
        original = admission.requests[req.rid]
        admission.requests[req.rid] = replace(original[0], attempt_id=1), original[1]
        accumulation.serviced(NS(reqs=[req]))
        assert accumulation.focus == original[0]
        admission.requests[req.rid] = original
        accumulation.serviced(NS(reqs=[req]))
        assert accumulation.focus is None
        assert admission.counts["ready_capacity_accumulation_actual_native_service"] == 1
    finally:
        runtime._opportunity_writer = None
        runtime.close()


def test_learning_and_oracle_must_declare_identical_capacity_accumulation(tmp_path):
    from copy import deepcopy
    from pathlib import Path
    from scripts.finish_model_work_oracle import verify_same_objective
    from scripts.prepare_maximal_cost_admission import prepare_artifacts
    from scripts.prepare_shortest_work_admission import prepare_artifacts as shortest
    from tests.test_model_work_choice_audit import model_work_pair

    _full, oracle, learned = model_work_pair(tmp_path)
    model = json.loads(Path(learned["common_environment"]["WORKFLOW_WORK_MODEL"]).read_bytes())
    truth = json.loads(Path(oracle["common_environment"]["WORKFLOW_WORK_MODEL"]).read_bytes())
    model, truth = prepare_artifacts(*shortest(model, truth))
    model, truth = prepare_artifacts(model, truth, selection_objective="benefit")
    for value in (model, truth):
        value["ready_wait_priority_ms"] = 30000
        value["free_dispatch_coalescing_window_ms"] = 2000
        value["ready_capacity_accumulation_ms"] = 2000
    verify_same_objective(learned, oracle, model, truth)
    wrong = deepcopy(truth)
    wrong["ready_capacity_accumulation_ms"] = 0
    with pytest.raises(ValueError, match="declared work provider"):
        verify_same_objective(learned, oracle, model, wrong)


def test_deadline_and_cooldown_restore_normal_admission_without_pausing_active_work(monkeypatch, tmp_path):
    runtime, admission, control, busy, clock, records = runtime_fixture(monkeypatch, tmp_path)
    try:
        admission.tick(now=clock[0])
        clock[0] += 2.001
        admission.next_tick = 0.
        admission.tick(now=clock[0])
        assert control.capacity_accumulation.focus is None
        assert admission.counts["ready_capacity_accumulation_deadline"] == 1
        assert admission.counts["ready_capacity_accumulation_started"] == 1
        assert all(admission.policy.programs["busy-" + str(index)].active for index in range(2))
        assert any(admission.policy.programs[owner].active for owner in ("b", "c"))
        validate_selection_policy(
            records, shortest_work_first=True, cost_aware_maximal=True,
            maximal_objective="benefit", ready_wait_priority_ms=30000,
            ready_capacity_accumulation_ms=2000,
        )
    finally:
        runtime._opportunity_writer = None
        runtime.close()
