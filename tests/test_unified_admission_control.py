"""Compare the shared controller with the previous two-clock orchestration."""

from dataclasses import asdict, replace
import time
from types import MethodType, SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.runtime.deferred_free_admission import DeferredFreeAdmission
from beliefkv.runtime.reference_free_continuation import ReferenceFreeContinuationAdmission
from beliefkv.runtime.workflow_work_worker import DistributionWorkPrediction
from tests.test_global_combination_oracle import queued as combination_oracle_queue
from tests.test_global_workflow_oracle import queued as oracle_queue
from tests.test_internal_comparison import observed
from tests.test_learned_coalescing_control import queued as learned_queue


def previous_tick(admission, *, now):
    """Frozen pre-refactor timing; the underlying scheduling policy is shared."""
    deferred = admission._deferred_dispatch
    if (
        admission.policy is not None and deferred.window_ms and deferred.window_open(now)
        and admission._dispatch_pending and now < admission.next_tick
    ):
        admission.next_tick = now
        admission.counts[admission.coalescing_reconciliation_counter] += 1
    admission._tick_working_set(now=now)
    deferred.dispatch(now)


def queue(monkeypatch, path, *, source, ranking, window_ms, previous=False):
    path.mkdir()
    if source in ("oracle", "oracle_combination"):
        if source == "oracle_combination":
            runtime, admission = combination_oracle_queue(
                monkeypatch, path, window_ms=window_ms, ranking=ranking,
            )
        else:
            runtime, admission = oracle_queue(
                monkeypatch, path, global_order=True, activate_first=False,
                window_ms=window_ms, ranking=ranking,
            )
        names = ("active", "long", "short")
    else:
        runtime, admission = learned_queue(
            monkeypatch, path, ranking=ranking, window_ms=window_ms,
        )
        names = ("a", "b", "c")
        for index, name in enumerate(names):
            admission._work._predictions[name] = DistributionWorkPrediction(
                (1., 0., 0.) if index == 0 else (0., 0., 1.),
                0 if index == 0 else 2, 1, 100., "model", "calibrated_distribution",
                1000., 1,
            )
    if previous:
        admission.tick = MethodType(previous_tick, admission)
        admission._dispatch_at_boundary = MethodType(
            ReferenceFreeContinuationAdmission._dispatch_at_boundary, admission,
        )
    return runtime, admission, names


def snapshot(admission):
    return {
        "programs": {
            name: asdict(item) for name, item in admission.policy.programs.items()
        },
        "pending": tuple(admission._dispatch_pending.items()),
        "next_control": admission.next_tick,
        "next_dispatch": admission._deferred_dispatch.next_dispatch,
        "raw_capacity": admission.policy.remaining_capacity(),
        "control_ticks": admission.counts["control_ticks"],
        "free_grants": admission.counts["free_continuation_admitted"],
        "reconciliation_ticks": admission.counts[admission.coalescing_reconciliation_counter],
        "resumed": admission._capacity_snapshot.get("resumed_program_ids"),
    }


def exercise(monkeypatch, path, *, source, ranking, window_ms, scenario, previous):
    clock = [100.]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    runtime, admission, names = queue(
        monkeypatch, path, source=source, ranking=ranking, window_ms=window_ms,
        previous=previous,
    )
    states = []

    def record():
        states.append(snapshot(admission))

    def boundary(now):
        clock[0] = now
        admission.tick(now=now)
        record()

    def tool(kind, name, sequence):
        key = admission.latest[name]
        runtime.on_events((replace(
            observed(
                sequence, kind, invocation_id=name, context_id=key.context_id,
                attributes={"tool_run_id": "tool-" + name},
            ), workflow_id=key.root_workflow_id,
        ),))
        record()

    try:
        deadline = 100. + window_ms / 1000.
        admission.next_tick = float("inf")
        if scenario in ("decay", "continuity"):
            active = admission.policy.programs[names[0]]
            assert admission._admit_from_free_capacity(
                (active,), event="agent_dispatch_free_capacity_admitted", join_id=None,
            )
            if scenario == "decay":
                req = admission._live_requests[admission.latest[names[0]].request_id]
                admission.completed(NS(reqs=[req]), (req.rid,))
                tool(Kind.TOOL_START, names[0], 20)
            admission.next_tick = 100.5
        elif scenario == "delayed_tool_return":
            for name in names[:2]:
                admission.forget(name)
            tool(Kind.TOOL_START, names[2], 20)
        elif scenario == "protected_join":
            runtime._join_priority_pending[names[1]] = "observed-join"
            monkeypatch.setattr(
                runtime, "_ready_join_parent", lambda req, key: key.invocation_id == names[1],
            )
            admission._admit_free_continuation(preferred=admission.latest[names[1]])
        elif scenario == "stale":
            key = admission.latest[names[2]]
            runtime.visible[key.request_id] = replace(key, context_epoch=key.context_epoch + 1)
        elif scenario == "periodic_phase":
            admission.next_tick = 100.5
        for now in (100.2, 100.4, 100.5, 100.9):
            clock[0] = now
            admission._admit_free_continuation()
            record()
            boundary(now)
        boundary(deadline)
        boundary(deadline)
        if scenario == "delayed_tool_return":
            clock[0] = deadline + .1
            tool(Kind.TOOL_END, names[2], 21)
            boundary(clock[0])
        boundary(deadline + .5)
        boundary(deadline + window_ms / 1000.)
        return states
    finally:
        runtime.close()


@pytest.mark.parametrize("source", ("learned", "oracle", "oracle_combination"))
@pytest.mark.parametrize("ranking", (False, True))
@pytest.mark.parametrize("window_ms", (1000, 2000))
@pytest.mark.parametrize("scenario", (
    "decay", "continuity", "delayed_tool_return", "protected_join", "stale", "periodic_phase",
))
def test_same_event_sequence_preserves_grants_pauses_and_capacity(
    monkeypatch, tmp_path, source, ranking, window_ms, scenario,
):
    results = []
    for previous in (True, False):
        with monkeypatch.context() as patch:
            results.append(exercise(
                patch, tmp_path / str(previous), source=source, ranking=ranking,
                window_ms=window_ms, scenario=scenario, previous=previous,
            ))
    assert results[1] == results[0]


def test_event_burst_causes_one_reconciliation_and_no_independent_half_second_checks(
    monkeypatch, tmp_path,
):
    clock = [100.]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    runtime, admission, _names = queue(
        monkeypatch, tmp_path / "shared", source="learned", ranking=False, window_ms=2000,
    )
    try:
        admission.next_tick = 102.
        dispatch = Mock(wraps=admission._dispatch_free_capacity)
        monkeypatch.setattr(admission, "_dispatch_free_capacity", dispatch)
        for now in (100.1, 100.5, 101., 101.5):
            clock[0] = now
            for _ in range(30):
                admission._admit_free_continuation(preferred=admission.latest["b"])
            admission.tick(now=now)
        assert not dispatch.call_count
        assert not admission.counts["control_ticks"]
        assert not admission.counts["free_dispatch_fallback_ticks"]
        clock[0] = 102.
        admission.tick(now=clock[0])
        admission.tick(now=clock[0])
        assert admission.counts["control_ticks"] == 1
        assert dispatch.call_count == 1
        assert admission.next_tick == admission._deferred_dispatch.next_dispatch == 104.
        status = admission.status()
        assert status["workflow_admission_ordinary_boundary_ms"] == 2000
        assert status["workflow_admission_separate_free_fallback"] is False
    finally:
        runtime.close()


@pytest.mark.parametrize("source", ("learned", "oracle", "oracle_combination"))
def test_idle_boundary_preserves_window_phase_when_first_call_arrives_later(
    monkeypatch, tmp_path, source,
):
    results = []
    for previous in (True, False):
        with monkeypatch.context() as patch:
            clock = [100.]
            patch.setattr(time, "monotonic", lambda: clock[0])
            runtime, admission, names = queue(
                patch, tmp_path / str(previous), source=source, ranking=False,
                window_ms=2000, previous=previous,
            )
            try:
                first = admission.latest[names[0]]
                req = admission._live_requests[first.request_id]
                for name in names:
                    admission.forget(name)
                deferred = admission._deferred_dispatch
                deferred.window_started = None
                deferred.next_dispatch = 0.
                deferred.dirty = False
                admission.next_tick = float("inf")
                states = []
                admission.tick(now=clock[0])
                states.append(snapshot(admission))
                assert deferred.next_dispatch == 102.
                clock[0] = 100.4
                admission.registered(req, first)
                assert deferred.next_dispatch == 102.
                states.append(snapshot(admission))
                clock[0] = 102.
                admission.tick(now=clock[0])
                states.append(snapshot(admission))
                results.append(states)
            finally:
                runtime.close()
    assert results[1] == results[0]


@pytest.mark.parametrize("fail", (False, True))
def test_boundary_prediction_snapshot_is_shared_and_never_reused_after_exit(fail):
    calls = []
    version = [1]
    admission = NS(
        policy=None, _work=NS(prediction=lambda workflow: calls.append(workflow) or version[0]),
    )
    controller = DeferredFreeAdmission(admission, window_ms=2000)

    def control(*, now):
        assert controller.prediction("wf") == 1
        version[0] = 2
        if fail:
            raise RuntimeError("control failed")

    admission._tick_working_set = control

    def dispatch(now):
        assert controller.prediction("wf") == 1

    controller.dispatch = dispatch
    if fail:
        with pytest.raises(RuntimeError, match="control failed"):
            controller.tick(102.)
    else:
        controller.tick(102.)
    assert calls == ["wf"]
    assert controller.prediction("wf") == 2
    assert calls == ["wf", "wf"]


def test_residual_dispatch_rechecks_raw_capacity_after_full_control(monkeypatch, tmp_path):
    clock = [100.]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    runtime, admission, _names = queue(
        monkeypatch, tmp_path / "capacity", source="learned", ranking=False, window_ms=2000,
    )
    try:
        admission.next_tick = 102.
        seen = []
        original = admission._dispatch_free_capacity

        def residual():
            before = admission.policy.remaining_capacity()
            seen.append(before)
            result = original()
            assert admission.policy.remaining_capacity() >= 0
            return result

        monkeypatch.setattr(admission, "_dispatch_free_capacity", residual)
        clock[0] = 102.
        admission.tick(now=clock[0])
        assert seen == [0]
        assert {item.identity for item in admission.policy.programs.values() if item.active} == {"b", "c"}
        assert not admission.counts["free_continuation_admitted"]
    finally:
        runtime.close()
