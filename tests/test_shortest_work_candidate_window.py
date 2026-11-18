from collections import Counter
from dataclasses import replace
from types import SimpleNamespace as NS
import time

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey, select_native_prefill_candidates
from beliefkv.runtime.workflow_combination import WorkflowCombinationController
from beliefkv.runtime.workflow_work_worker import WorkPrediction
from tests.test_internal_comparison import observed
from tests.test_paper_baseline_runtime import request
from tests.test_workflow_combination_runtime import runtime_with_queue
from tests.test_workflow_size_selection_audit import key


def window_fixture(*, shortest=True):
    keys = [PrefillCandidateKey(**key(str(index))) for index in range(12)]
    weights = {item.root_workflow_id: .1 + index / 20 for index, item in enumerate(keys)}
    calls = Counter()

    def prediction(workflow):
        calls[workflow] += 1
        return NS(effective_weight=weights[workflow], evidence="model")

    controller = WorkflowCombinationController.__new__(WorkflowCombinationController)
    controller.shortest_work_first = shortest
    controller.prediction = prediction
    controller.admission = NS(
        counts=Counter(), _supported=lambda value: value is not None,
        _protected_join=lambda value: value is keys[0],
        policy=NS(
            buffer_tokens=10,
            programs={item.invocation_id: NS(tokens=20) for item in keys},
        ),
    )
    return controller, keys, calls


def test_shortest_optional_member_is_visible_before_the_prefix_window_is_truncated():
    controller, keys, calls = window_fixture()
    original = list(keys)
    predictions = {}
    result = controller._candidate_window(keys, keys[1:3], 100, predictions)
    assert result == [keys[1], keys[2], keys[0], keys[11], keys[10], keys[9]]
    assert keys == original
    assert len(result) == controller.max_candidates
    assert all(count == 1 for count in calls.values())
    assert set(keys[1:3]) <= set(result)


def test_logically_oversized_optional_members_do_not_consume_the_prefix_window():
    controller, keys, _calls = window_fixture()
    for item in keys[8:]:
        controller.admission.policy.programs[item.invocation_id].tokens = 101
    result = controller._candidate_window(keys, keys[1:3], 100, {})
    assert result == [keys[1], keys[2], keys[0], keys[7], keys[6], keys[5]]


def test_equal_or_unknown_work_preserves_existing_order_and_join_preference():
    controller, keys, _calls = window_fixture()
    controller.prediction = lambda workflow: None
    result = controller._candidate_window(keys, keys[1:3], 100, {})
    assert result == [keys[1], keys[2], keys[0], keys[3], keys[4], keys[5]]


def test_legacy_selector_retains_its_original_window_and_does_not_query_work():
    controller, keys, calls = window_fixture(shortest=False)
    result = controller._candidate_window(keys, keys[1:3], 100, {})
    assert result == [keys[1], keys[2], keys[0], keys[3], keys[4], keys[5]]
    assert not calls


@pytest.mark.parametrize("count", (6, 7, 8))
def test_reference_and_required_join_members_are_never_removed_for_a_new_short_member(count):
    controller, keys, calls = window_fixture()
    reference = keys[:count]
    result = controller._candidate_window(keys, reference, 100, {})
    assert result[:count] == reference
    assert set(reference) <= set(result)
    assert len(result) == controller.max_sibling_candidates
    if count == 8:
        assert not calls
    else:
        assert keys[-1] in result
        assert all(value == 1 for value in calls.values())
        assert controller.admission.counts["shortest_work_competitive_window_expansions"] == 1


@pytest.mark.parametrize("shortest", (False, True))
def test_full_reference_with_no_better_peer_keeps_the_bounded_fast_path(shortest):
    controller, keys, calls = window_fixture(shortest=shortest)
    controller.admission._protected_join = lambda key: False
    reference = keys[-6:]
    assert controller._candidate_window(keys, reference, 100, {}) == reference
    assert not controller.admission.counts["shortest_work_competitive_window_expansions"]
    if not shortest:
        assert not calls


def test_an_oversized_better_peer_cannot_expand_a_full_reference():
    controller, keys, _calls = window_fixture()
    controller.admission._protected_join = lambda key: False
    for key in keys[6:]:
        controller.admission.policy.programs[key.invocation_id].tokens = 101
    assert controller._candidate_window(keys, keys[:6], 100, {}) == keys[:6]
    assert not controller.admission.counts["shortest_work_competitive_window_expansions"]


@pytest.mark.parametrize("reference_count", (6, 7))
@pytest.mark.parametrize("expand", (False, True))
def test_full_reference_cannot_hide_a_shorter_workflow_from_actual_native_selection(
    monkeypatch, tmp_path, reference_count, expand,
):
    from functools import partial
    from beliefkv.policy import workflow_maximal_admission
    from tests.test_ready_wait_priority import WeightedPrediction

    runtime, admission, _env = runtime_with_queue(monkeypatch, tmp_path)
    control = admission._combination
    control.shortest_work_first = control.cost_aware_maximal = True
    control.maximal_objective = "benefit"
    control.costs = replace(control.costs, decode_cost_basis="full_batch", selection_objective="benefit")
    admission.policy.buffer_tokens = 0
    control.window_ms, control.next_dispatch = 2000, float("inf")
    records = []
    runtime._opportunity_writer = NS(record=records.append)
    original_window = control._candidate_window
    if not expand:
        monkeypatch.setattr(
            control, "_candidate_window",
            lambda keys, reference, *args: list(reference) if len(reference) >= 6
            else original_window(keys, reference, *args),
        )
    monkeypatch.setattr(
        workflow_maximal_admission, "choose_maximal_admission",
        partial(workflow_maximal_admission.choose_maximal_admission, clock=lambda: 0),
    )
    try:
        for index in range(reference_count):
            owner = "small-kv-" + str(index)
            runtime.on_events((
                replace(observed(index * 2 + 100, Kind.WORKFLOW_START), workflow_id=owner),
                replace(observed(
                    index * 2 + 101, Kind.INVOCATION_CREATE,
                    invocation_id=owner, context_id=owner + "-context",
                ), workflow_id=owner),
            ))
            req = request(
                owner + "-request", owner,
                tokens=70 if reference_count == 6 else 60, workflow=owner,
            )
            req.output_ids.clear()
            runtime.register_visible_request(req)
        admission._work._predictions.update({
            owner: WeightedPrediction(
                (1., 0., 0.), 0, 1, time.monotonic(), "model",
                .9 if owner == "a" else .001,
            ) for owner in admission.policy.programs
        })
        control.window_ms, control.next_dispatch = 0, 0.
        admission.next_tick = 0.
        admission.tick()
        active = {item.identity for item in admission.policy.programs.values() if item.active}
        assert ("a" in active) is expand
        choice = next(
            row for row in records
            if row["event"] == "workflow_combination_selection" and row["source"] == "periodic_decay_resume"
        )
        assert len(choice["decision"]["reference"]) == reference_count
        assert len(choice["candidates"]) == (8 if expand else 0)
        assert admission.policy.remaining_capacity() >= 0
        requests = list(admission._live_requests.values())
        plan = runtime.plan_native_prefill(requests, running_batch=NS(reqs=[]), adder=None)
        selected = select_native_prefill_candidates(
            requests, plan=plan, current_semantic_revision=runtime.semantic_revision,
        )
        assert (admission._live_requests["a-request"] in selected.candidates) is expand
        assert not choice["physical_capacity_credit"]
    finally:
        runtime._opportunity_writer = None
        runtime.close()


@pytest.mark.parametrize("priority_window", (False, True))
def test_priority_outside_old_window_changes_actual_eligibility_and_native_selection(
    monkeypatch, tmp_path, priority_window,
):
    runtime, admission, _env = runtime_with_queue(monkeypatch, tmp_path)
    records = []
    runtime._opportunity_writer = NS(record=records.append)
    controller = admission._combination
    controller.shortest_work_first = True
    controller.costs = replace(
        controller.costs, decode_cost_basis="full_batch", selection_objective="benefit",
    )
    # Collect all ingress before opening one shared native control boundary.
    controller.window_ms = 2000
    controller.next_dispatch = float("inf")
    if not priority_window:
        legacy = NS(
            admission=admission, shortest_work_first=False,
            max_candidates=controller.max_candidates,
            max_sibling_candidates=controller.max_sibling_candidates,
        )
        monkeypatch.setattr(
            controller, "_candidate_window",
            lambda *args: WorkflowCombinationController._candidate_window(legacy, *args),
        )
    from beliefkv.policy import workflow_shortest_frontier

    original = workflow_shortest_frontier.choose_shortest_work_first
    monkeypatch.setattr(
        workflow_shortest_frontier, "choose_shortest_work_first",
        lambda *args, **kwargs: original(*args, **kwargs, clock=lambda: 0),
    )
    try:
        for index in range(8):
            owner = "extra-" + str(index)
            runtime.on_events((
                replace(observed(100 + index * 2, Kind.WORKFLOW_START), workflow_id=owner),
                replace(observed(
                    101 + index * 2, Kind.INVOCATION_CREATE, invocation_id=owner,
                    context_id=owner + "-context",
                ), workflow_id=owner),
            ))
            req = request(owner + "-request", owner, tokens=10 + index, workflow=owner)
            req.output_ids.clear()
            runtime.register_visible_request(req)
        admission._work._predictions.update({
            owner: WorkPrediction(
                (1., 0., 0.) if owner == "a" else (0., 0., 1.),
                0 if owner == "a" else 2, 1, time.monotonic(), "model",
            )
            for owner in admission.policy.programs
        })
        assert not any(item.active for item in admission.policy.programs.values())
        controller.window_ms = 0
        controller.next_dispatch = 0.
        admission.next_tick = 0.
        admission.tick()
        active = {item.identity for item in admission.policy.programs.values() if item.active}
        assert ("a" in active) is priority_window
        if priority_window:
            assert active == {"a"}
        requests = list(admission._live_requests.values())
        native_plan = runtime.plan_native_prefill(requests, running_batch=NS(reqs=[]), adder=None)
        actual = select_native_prefill_candidates(
            requests, plan=native_plan, current_semantic_revision=runtime.semantic_revision,
        )
        assert (admission._live_requests["a-request"] in actual.candidates) is priority_window
        choice = next(
            row for row in records if row["event"] == "workflow_combination_selection"
        )
        assert len(choice["candidates"]) <= controller.max_sibling_candidates
        assert set(choice["predictions"]) == {row["workflow"] for row in choice["candidates"]}
        assert not choice["physical_capacity_credit"]
    finally:
        runtime._opportunity_writer = None
        runtime.close()
