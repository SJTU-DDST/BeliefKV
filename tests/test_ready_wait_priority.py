from collections import Counter
from dataclasses import dataclass, replace
from functools import partial
import hashlib
import json
from types import SimpleNamespace as NS
import time

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.runtime.ready_wait_priority import ReadyWaitPriority, ReadyWaitSettings
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey, select_native_prefill_candidates
from beliefkv.runtime.workflow_combination import WorkflowCombinationController
from beliefkv.runtime.workflow_work_worker import WorkPrediction
from scripts.audit_workflow_combination_choices import validate_selection_policy
from tests.test_tool_predictive_transfers import event
from tests.test_workflow_size_selection_audit import key


def priority_fixture():
    keys = [PrefillCandidateKey(**key(str(index))) for index in range(3)]
    admission = NS(
        latest={item.invocation_id: item for item in keys},
        counts=Counter(), requests={str(index): (item, 0.) for index, item in enumerate(keys)},
    )
    return ReadyWaitPriority(admission, 30000), keys


def test_age_starts_at_first_observed_ready_and_does_not_grow_for_an_absent_sibling():
    priority, keys = priority_fixture()
    left, right, _ = keys
    priority.begin([left], 10.)
    assert priority.weight(left.root_workflow_id, .01) == .01
    priority.begin([left, right], 100.)
    assert priority.weight(left.root_workflow_id, .01) == .0125
    assert priority.weight(right.root_workflow_id, .02) == .02
    priority.begin([right], 190.)
    assert priority.weight(left.root_workflow_id, .01) == .01
    assert priority.weight(right.root_workflow_id, .02) == .025


def test_one_workflow_has_one_age_weight_even_with_multiple_ready_siblings():
    priority, keys = priority_fixture()
    left = keys[0]
    sibling = replace(keys[1], root_workflow_id=left.root_workflow_id)
    priority.admission.latest[sibling.invocation_id] = sibling
    priority.begin([left], 0.)
    priority.begin([left, sibling], 90.)
    assert priority.weight(left.root_workflow_id, .01) == .0125
    assert priority.workflow_age_ms == {left.root_workflow_id: 90000.}
    assert priority.weight(left.root_workflow_id, 0.) == 0.
    assert priority.weight(left.root_workflow_id, 1.) == 1.


@pytest.mark.parametrize("age,expected", (
    (0., .2), (60000., .2), (120000., .225),
    (180000., .25), (3600000., .25), (1e12, .25),
))
def test_grace_ramp_and_relative_cap_preserve_work_differences(age, expected):
    settings = ReadyWaitSettings(120000)
    assert settings.weight(.2, age) == pytest.approx(expected)
    assert settings.weight(.2, age) < settings.weight(.9, 0.)


def test_max_boost_one_disables_priority_changes_without_disabling_the_ready_clock():
    priority, keys = priority_fixture()
    priority = ReadyWaitPriority(priority.admission, 120000, grace_ms=0, max_boost=1.)
    priority.begin(keys, 0.)
    priority.begin(keys, 1e6)
    assert priority.weight(keys[0].root_workflow_id, .2) == .2
    assert priority.wait_ms(keys[0], 1e6) == 1e9


@pytest.mark.parametrize("kind", (Kind.TOOL_START, Kind.JOIN_WAIT, Kind.RETURN, Kind.INVOCATION_CANCEL))
def test_external_blocking_or_retirement_resets_the_ready_age(kind):
    priority, keys = priority_fixture()
    priority.begin([keys[0]], 0.)
    priority.events((event(10, kind, invocation_id=keys[0].invocation_id),))
    assert priority.weight(keys[0].root_workflow_id, .01) == .01
    priority.begin([keys[0]], 100.)
    assert priority.weight(keys[0].root_workflow_id, .01) == .01


def test_actual_service_resets_only_its_exact_request_epoch_and_attempt():
    priority, keys = priority_fixture()
    priority.begin(keys, 0.)
    priority.admission.requests["stale"] = (replace(keys[0], attempt_id=1), 0.)
    priority.serviced(NS(reqs=[NS(rid="stale")]))
    assert keys[0].invocation_id in priority.since
    priority.serviced(NS(reqs=[NS(rid="0")]))
    assert keys[0].invocation_id not in priority.since
    assert keys[1].invocation_id in priority.since
    assert priority.admission.counts["ready_wait_priority_service_resets"] == 1
    assert priority.weight(keys[0].root_workflow_id, .01) == .01


def test_new_epoch_drops_old_age_and_history_is_bounded():
    priority, keys = priority_fixture()
    priority.max_requests = 2
    priority.begin(keys[:2], 0.)
    replacement = replace(keys[0], context_epoch=1, request_id="new")
    priority.admission.latest[replacement.invocation_id] = replacement
    priority.begin([replacement, keys[1], keys[2]], 60.)
    assert all(entry[0] != keys[0] for entry in priority.since.values())
    assert len(priority.since) == 2
    assert priority.admission.counts["ready_wait_priority_history_evictions"] == 1


def configured_priority(tmp_path, value, *, maximal=True, **tuning):
    from tests.test_maximal_benefit_admission import controller

    control = controller(tmp_path, "benefit" if maximal else "benefit_per_cost", maximal=maximal)
    path = control.admission.runtime.workflow_work_model
    raw = json.loads(path.read_bytes())
    raw["ready_wait_priority_ms"] = value
    raw.update(tuning)
    path.write_text(json.dumps(raw))
    control.admission.runtime.workflow_work_model_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    return WorkflowCombinationController(control.admission)


@pytest.mark.parametrize("value", (True, -1, 3600001, 30., "30000", None))
def test_invalid_or_implicit_wait_priority_is_rejected(tmp_path, value):
    with pytest.raises(ValueError, match="ready wait priority"):
        configured_priority(tmp_path, value)


def test_wait_priority_is_default_off_and_requires_the_maximal_selector(tmp_path):
    from tests.test_maximal_benefit_admission import controller

    assert controller(tmp_path, "benefit").ready_wait_priority is None
    assert configured_priority(tmp_path, 30000).ready_wait_priority.scale_ms == 30000
    assert configured_priority(tmp_path, 120000).ready_wait_priority.scale_ms == 120000
    with pytest.raises(ValueError, match="ready wait priority"):
        configured_priority(tmp_path, 30000, maximal=False)


@pytest.mark.parametrize("scale_ms", (60001, 120000, 3600000))
def test_slower_growth_intervals_are_explicitly_supported(tmp_path, scale_ms):
    assert configured_priority(tmp_path, scale_ms).ready_wait_priority.scale_ms == scale_ms


@pytest.mark.parametrize("tuning", (
    {"ready_wait_priority_grace_ms": -1}, {"ready_wait_priority_grace_ms": True},
    {"ready_wait_priority_grace_ms": 3600001}, {"ready_wait_priority_grace_ms": 60.},
    {"ready_wait_priority_max_boost": .99}, {"ready_wait_priority_max_boost": 2.01},
    {"ready_wait_priority_max_boost": True}, {"ready_wait_priority_max_boost": float("nan")},
    {"ready_wait_priority_max_boost": float("inf")},
))
def test_invalid_tuning_is_rejected_by_the_actual_controller(tmp_path, tuning):
    with pytest.raises(ValueError, match="ready wait priority"):
        configured_priority(tmp_path, 120000, **tuning)


def test_disabled_aging_cannot_silently_accept_tuning(tmp_path):
    with pytest.raises(ValueError, match="requires enabled"):
        configured_priority(tmp_path, 0, ready_wait_priority_max_boost=1.25)


def test_controller_reads_independent_grace_ramp_and_cap(tmp_path):
    control = configured_priority(
        tmp_path, 120000, ready_wait_priority_grace_ms=90000,
        ready_wait_priority_max_boost=1.1,
    )
    assert control.ready_wait_priority.settings == ReadyWaitSettings(120000, 90000, 1.1)


@dataclass(frozen=True)
class WeightedPrediction(WorkPrediction):
    effective_weight: float


@pytest.mark.parametrize("aging", (False, True))
def test_waiting_adjustment_changes_actual_eligibility_and_native_prefill(
    monkeypatch, tmp_path, aging,
):
    check_waiting_adjustment(monkeypatch, tmp_path, aging, .01)


@pytest.mark.parametrize("aging", (False, True))
def test_limited_wait_protection_can_break_a_close_combination_tie(
    monkeypatch, tmp_path, aging,
):
    check_waiting_adjustment(monkeypatch, tmp_path, aging, .035)


def check_waiting_adjustment(monkeypatch, tmp_path, aging, long_weight):
    from beliefkv.policy import workflow_maximal_admission
    from tests.test_workflow_combination_runtime import runtime_with_queue

    runtime, admission, _ = runtime_with_queue(monkeypatch, tmp_path)
    control = admission._combination
    control.shortest_work_first = control.cost_aware_maximal = True
    control.maximal_objective = "benefit"
    control.costs = replace(control.costs, decode_cost_basis="full_batch", selection_objective="benefit")
    records = []
    runtime._opportunity_writer = NS(record=records.append)
    monkeypatch.setattr(
        workflow_maximal_admission, "choose_maximal_admission",
        partial(workflow_maximal_admission.choose_maximal_admission, clock=lambda: 0),
    )
    admission._work._predictions.update({
        owner: WeightedPrediction((1., 0., 0.), 0, 1, time.monotonic(), "model", weight)
        for owner, weight in (("a", long_weight), ("b", .02), ("c", .02))
    })
    if aging:
        control.ready_wait_priority = ReadyWaitPriority(admission, 30000)
        control.ready_wait_priority.begin([admission.latest["a"]], time.monotonic() - 120.)
    try:
        admission.tick()
        expected = {"a"} if aging and long_weight == .035 else {"b", "c"}
        assert {item.identity for item in admission.policy.programs.values() if item.active} == expected
        journal = [row for row in records if row["event"] == "workflow_combination_selection"]
        assert set(journal[0]["decision"]["selected"]) == expected
        validate_selection_policy(
            journal, shortest_work_first=True, cost_aware_maximal=True,
            maximal_objective="benefit", ready_wait_priority_ms=30000 if aging else 0,
        )
        requests = [admission._live_requests[owner + "-request"] for owner in ("a", "b", "c")]
        plan = runtime.plan_native_prefill(requests, running_batch=NS(reqs=[]), adder=None)
        actual = select_native_prefill_candidates(
            requests, plan=plan, current_semantic_revision=runtime.semantic_revision,
        )
        assert {request.rid for request in actual.candidates} == {owner + "-request" for owner in expected}
        if aging:
            owner = sorted(expected)[0]
            admission.native_service(NS(reqs=[requests[ord(owner) - ord("a")]]), [])
            assert owner not in control.ready_wait_priority.since
            assert admission.counts["ready_wait_priority_service_resets"] == 1
    finally:
        runtime._opportunity_writer = None
        runtime.close()


def test_audit_refuses_unmatched_or_unregistered_wait_priority():
    row = {
        "event": "workflow_combination_selection", "selection_policy": "maximal_benefit",
        "ready_wait_priority": {"scale_ms": 30000},
    }
    with pytest.raises(ValueError, match="ready wait priority"):
        validate_selection_policy(
            [row], shortest_work_first=True, cost_aware_maximal=True, maximal_objective="benefit",
        )
    with pytest.raises(ValueError, match="ready wait priority"):
        validate_selection_policy(
            [row], shortest_work_first=True, cost_aware_maximal=True,
            maximal_objective="benefit", ready_wait_priority_ms=1000,
        )


def test_oracle_and_learned_objectives_must_declare_the_same_wait_priority(tmp_path):
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
    model["ready_wait_priority_ms"] = truth["ready_wait_priority_ms"] = 30000
    verify_same_objective(learned, oracle, model, truth)
    wrong = deepcopy(truth)
    wrong["ready_wait_priority_ms"] = 1000
    with pytest.raises(ValueError, match="declared work provider"):
        verify_same_objective(learned, oracle, model, wrong)
    for name, value in (
        ("ready_wait_priority_grace_ms", 0), ("ready_wait_priority_max_boost", 1.5),
    ):
        wrong = deepcopy(truth)
        wrong[name] = value
        with pytest.raises(ValueError, match="declared work provider"):
            verify_same_objective(learned, oracle, model, wrong)


@pytest.mark.parametrize("name,value", (
    ("kind", "unbounded"), ("grace_ms", 0), ("max_boost", 2.), ("scale_ms", 30000),
))
def test_audit_rejects_bounded_tuning_that_differs_from_the_artifact(name, value):
    row = {
        "event": "workflow_combination_selection", "selection_policy": "maximal_benefit",
        "ready_wait_priority": ReadyWaitSettings(120000).record(),
    }
    kwargs = dict(
        shortest_work_first=True, cost_aware_maximal=True, maximal_objective="benefit",
        ready_wait_priority_ms=120000, ready_wait_priority_grace_ms=60000,
        ready_wait_priority_max_boost=1.25,
    )
    validate_selection_policy([row], **kwargs)
    row["ready_wait_priority"][name] = value
    with pytest.raises(ValueError, match="ready wait priority"):
        validate_selection_policy([row], **kwargs)


def test_legacy_journal_remains_auditable_but_cannot_attest_new_bounded_tuning():
    row = {
        "event": "workflow_combination_selection", "selection_policy": "maximal_benefit",
        "ready_wait_priority": {"scale_ms": 30000},
    }
    kwargs = dict(
        shortest_work_first=True, cost_aware_maximal=True,
        maximal_objective="benefit", ready_wait_priority_ms=30000,
    )
    validate_selection_policy([row], **kwargs)
    with pytest.raises(ValueError, match="legacy ready wait"):
        validate_selection_policy([row], ready_wait_priority_max_boost=1.25, **kwargs)
