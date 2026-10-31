from dataclasses import replace
import time
from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.runtime.workflow_work_oracle import WorkflowWorkOracleArtifact
from tests.test_global_workflow_oracle import queued
from tests.test_internal_comparison import observed
from tests.test_workflow_work_oracle import source


@pytest.mark.parametrize("window_ms", (1000, 2000))
def test_timed_window_holds_ordinary_admissions_across_periodic_and_free_paths(
    monkeypatch, tmp_path, window_ms,
):
    clock = [100.]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    runtime, admission = queued(
        monkeypatch, tmp_path, global_order=True, activate_first=False, window_ms=window_ms,
    )
    try:
        deadline = 100. + window_ms / 1000.
        assert admission._deferred_dispatch.next_dispatch == deadline
        clock[0] = deadline - .001
        admission.next_tick = 0.
        admission.tick(now=clock[0])
        assert not any(item.active for item in admission.policy.programs.values())
        clock[0] = deadline
        admission.tick(now=clock[0])
        assert {item.identity for item in admission.policy.programs.values() if item.active} == {"short"}
        assert admission.status()["workflow_oracle_coalescing_window_ms"] == window_ms
        assert admission._deferred_dispatch.next_dispatch == deadline + window_ms / 1000.
    finally:
        runtime.close()


def test_new_notifications_do_not_restart_an_unexpired_window(monkeypatch, tmp_path):
    clock = [100.]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    runtime, admission = queued(
        monkeypatch, tmp_path, global_order=True, activate_first=False, window_ms=1000,
    )
    try:
        for now in (100.2, 100.5, 100.9):
            clock[0] = now
            admission._admit_free_continuation(preferred=admission.latest["long"])
            admission.tick(now=now)
            assert admission._deferred_dispatch.next_dispatch == 101.
            assert not any(item.active for item in admission.policy.programs.values())
        clock[0] = 101.
        admission.tick(now=clock[0])
        assert admission.policy.programs["short"].active
    finally:
        runtime.close()


def test_a_ready_join_parent_bypasses_the_window_without_granting_ordinary_peers(monkeypatch, tmp_path):
    clock = [100.]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    runtime, admission = queued(
        monkeypatch, tmp_path, global_order=True, activate_first=False, window_ms=1000,
    )
    try:
        runtime._join_priority_pending["long"] = "observed-join"
        monkeypatch.setattr(runtime, "_ready_join_parent", lambda req, key: key.invocation_id == "long")
        admission._admit_free_continuation(preferred=admission.latest["long"])
        clock[0] = 100.2
        admission.tick(now=clock[0])
        assert {item.identity for item in admission.policy.programs.values() if item.active} == {"long"}
        assert admission._deferred_dispatch.next_dispatch == 101.
        assert not admission.counts["oracle_coalescing_windows"]
    finally:
        runtime.close()


def test_periodic_window_observation_does_not_call_deferred_candidates_eligible(monkeypatch, tmp_path):
    clock = [100.]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    runtime, admission = queued(
        monkeypatch, tmp_path, global_order=True, activate_first=False, window_ms=1000,
    )
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    try:
        admission.next_tick = 0.
        clock[0] = 100.5
        admission.tick(now=clock[0])
        row = next(row for row in records if row["event"] == "agent_periodic_admission_decision")
        assert all(item["application_ready"] for item in row["programs"])
        assert not any(item["resume_window_eligible"] for item in row["programs"])
        assert not row["resumed_program_ids"]
    finally:
        runtime.close()


def test_existing_active_agents_keep_their_eligibility_during_coalescing(monkeypatch, tmp_path):
    clock = [100.]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    runtime, admission = queued(
        monkeypatch, tmp_path, global_order=True, activate_first=False, window_ms=1000,
    )
    try:
        assert admission._admit_from_free_capacity(
            (admission.policy.programs["active"],),
            event="agent_dispatch_free_capacity_admitted", join_id=None,
        )
        admission.next_tick = 0.
        clock[0] = 100.5
        admission.tick(now=clock[0])
        clock[0] = 101.
        admission.tick(now=clock[0])
        assert {item.identity for item in admission.policy.programs.values() if item.active} == {"active"}
        assert not admission.status()["workflow_size_new_preemption"]
    finally:
        runtime.close()


def test_window_deadline_reconciles_decay_when_periodic_control_has_another_phase(
    monkeypatch, tmp_path,
):
    clock = [100.]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    runtime, admission = queued(
        monkeypatch, tmp_path, global_order=True, activate_first=False, window_ms=1000,
    )
    try:
        active = admission.policy.programs["active"]
        assert admission._admit_from_free_capacity(
            (active,), event="agent_dispatch_free_capacity_admitted", join_id=None,
        )
        admission.completed(
            NS(reqs=[admission._live_requests["active-request"]]), ("active-request",),
        )
        runtime.on_events((replace(observed(
            20, Kind.TOOL_START, invocation_id="active",
            attributes={"tool_run_id": "slow-tool"},
        ), workflow_id="active-work"),))
        admission.next_tick = 100.5
        clock[0] = 100.5
        admission.tick(now=clock[0])
        assert active.active and active.acting and admission.policy.remaining_capacity() == 0
        assert not admission.policy.programs["long"].active
        assert admission.next_tick == 100.5 + admission.control_interval_s
        clock[0] = 101.
        assert admission.policy.remaining_capacity_with_decay(now=clock[0]) == 200
        admission.tick(now=clock[0])
        assert admission.policy.programs["long"].active
        assert not active.active
        assert not admission.policy.programs["short"].active
        assert admission.policy.remaining_capacity() >= 0
        assert admission._deferred_dispatch.next_dispatch == 102.
    finally:
        runtime.close()


def test_delayed_tool_return_reindexes_its_caller_but_respects_the_window(
    monkeypatch, tmp_path,
):
    clock = [100.]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    runtime, admission = queued(
        monkeypatch, tmp_path, global_order=True, activate_first=False, window_ms=1000,
    )
    try:
        admission.forget("active")
        admission.forget("long")
        runtime.on_events((replace(observed(
            20, Kind.TOOL_START, invocation_id="short",
            attributes={"tool_run_id": "slow-tool"},
        ), workflow_id="short-work"),))
        clock[0] = 101.
        admission.tick(now=clock[0])
        assert "short" not in admission._dispatch_pending
        assert not admission.policy.programs["short"].active
        clock[0] = 101.1
        runtime.on_events((replace(observed(
            21, Kind.TOOL_END, invocation_id="short",
            attributes={"tool_run_id": "slow-tool"},
        ), workflow_id="short-work"),))
        assert "short" in admission._dispatch_pending
        assert not admission.policy.programs["short"].active
        admission.tick(now=clock[0])
        assert not admission.policy.programs["short"].active
        clock[0] = 102.
        admission.tick(now=clock[0])
        assert admission.policy.programs["short"].active
        assert admission.counts["free_dispatch_ready_event_reindexed"] == 1
    finally:
        runtime.close()


@pytest.mark.parametrize("value", (True, False, None, "1000", 1000., 0., 500, -1000, 3000))
def test_invalid_oracle_window_declarations_fail_before_runtime(value):
    raw = source()
    raw.update(admission_order="global_ready_remaining_path", free_dispatch_coalescing_window_ms=value)
    with pytest.raises(ValueError, match="coalescing"):
        WorkflowWorkOracleArtifact(raw)


def test_timed_window_requires_the_explicit_global_oracle():
    raw = source()
    raw["free_dispatch_coalescing_window_ms"] = 1000
    with pytest.raises(ValueError, match="coalescing"):
        WorkflowWorkOracleArtifact(raw)
