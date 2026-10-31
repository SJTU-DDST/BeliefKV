from types import SimpleNamespace as NS
import time

import pytest

from beliefkv.experiments.internal_comparison import MODES, arm_environment
from beliefkv.policy.paper_baselines import ThunderProgram
from beliefkv.policy.workflow_size_resume import WorkflowSizeWorkingSet
from beliefkv.runtime.reference_thunder_admission import validate_reference_admission
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.run_causal_comparison import preflight_runtime
from tests.test_global_workflow_oracle import queued
from tests.test_workflow_size_admission import settings


def policy(*, supported=True):
    ranks = {"small-kv-short-work": 1., "large-kv-long-work": 100.}
    live = WorkflowSizeWorkingSet(
        100, buffer_tokens=0, rank=lambda identity, now: 1,
        sibling_group=lambda identity: None,
    )
    live.pressure_preference = lambda item, now: ranks.get(item.identity) if supported else None
    return live


@pytest.mark.parametrize("supported", (False, True))
def test_mandatory_acting_pressure_can_pause_long_work_instead_of_the_smallest_kv(supported):
    live = policy(supported=supported)
    live.programs = {
        name: ThunderProgram(name, size, steps=2, active=True, acting=True)
        for name, size in (("small-kv-short-work", 20), ("large-kv-long-work", 90))
    }
    live.tick(now=10.)
    selected = "large-kv-long-work" if supported else "small-kv-short-work"
    assert not live.programs[selected].active
    assert live.programs[next(name for name in live.programs if name != selected)].active
    assert live.remaining_capacity() >= 0
    assert live.last_pressure[0]["selected"] == selected
    assert live.last_pressure[0]["reference_selected"] == "small-kv-short-work"
    assert live.last_pressure[0]["prediction_used"] is supported


def test_reasoning_is_only_marked_at_a_request_boundary_and_does_not_yield_real_capacity():
    live = policy()
    live.programs = {
        name: ThunderProgram(name, size, steps=2, active=True, pending=True)
        for name, size in (("small-kv-short-work", 20), ("large-kv-long-work", 90))
    }
    live.tick(now=10.)
    assert live.programs["large-kv-long-work"].marked_for_pause
    assert not live.programs["small-kv-short-work"].marked_for_pause
    assert all(p.active for p in live.programs.values())
    assert live.remaining_capacity() == -10
    live.tick(now=11.)
    assert not live.programs["small-kv-short-work"].marked_for_pause
    live.completed("large-kv-long-work", 90, now=12.)
    assert not live.programs["large-kv-long-work"].active
    assert live.programs["small-kv-short-work"].active
    assert live.remaining_capacity() == 80


def test_no_pressure_never_asks_for_a_work_prediction():
    live = policy()
    live.programs["a"] = ThunderProgram("a", 50, active=True, pending=True)
    live.pressure_preference = lambda *_args: pytest.fail("unnecessary pressure prediction")
    live.tick(now=10.)
    assert not live.last_pressure


@pytest.mark.parametrize("mode", MODES)
def test_workflow_pressure_is_declared_and_stripped_from_controls(tmp_path, mode):
    configured = arm_environment({
        **settings(tmp_path), "AGENT_PRESSURE_PAUSE_POLICY": "workflow_remaining",
    }, mode)
    assert configured["BELIEFKV_AGENT_PRESSURE_PAUSE_POLICY"] == (
        "workflow_remaining" if mode == "full" else "reference"
    )


def test_exact_oracle_cached_work_also_drives_the_pressure_choice(monkeypatch, tmp_path):
    runtime, admission = queued(monkeypatch, tmp_path, global_order=True, activate_first=False)
    try:
        admission.workflow_pressure_pause = True
        admission.policy.pressure_preference = admission._pressure_preference
        admission.policy.buffer_tokens = 0
        for identity in ("active", "short"):
            item = admission.policy.programs[identity]
            item.active, item.pending, item.acting = True, False, True
        admission.policy.programs["long"].active = False
        runtime._opportunity_writer = NS(record=lambda row: None)
        admission.next_tick = 0.
        admission.tick(now=time.monotonic())
        assert not admission.policy.programs["active"].active
        assert admission.policy.programs["short"].active
        assert admission.counts["workflow_pressure_changed_victims"] >= 1
        validate_reference_admission(
            admission.status(), control_interval_ms=2000,
            free_continuation_admission=True, free_dispatch_interval_ms=500,
            workflow_size_resume=True, workflow_work_source="oracle",
            workflow_work_model_sha256=runtime.workflow_work_model_sha256,
            pressure_pause_policy="workflow_remaining",
        )
    finally:
        runtime._opportunity_writer = None
        runtime.close()


def test_actual_constructor_and_preflight_accept_only_the_declared_pressure_extension(
    monkeypatch, tmp_path,
):
    configured = arm_environment({
        **settings(tmp_path), "AGENT_PRESSURE_PAUSE_POLICY": "workflow_remaining",
    }, "full")
    for name, value in configured.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "")
    runtime = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "pressure.sock"))
    try:
        runtime.attach_native_cache(NS(
            tree_components=(0,), token_to_kv_pool_allocator=NS(size_full=1000),
        ))
        assert runtime._agent_admission.policy.pressure_preference is not None
        validate_reference_admission(
            runtime._agent_admission.status(),
            control_interval_ms=2000, free_continuation_admission=True,
            workflow_size_resume=True,
            workflow_work_model_sha256=runtime.workflow_work_model_sha256,
            pressure_pause_policy="workflow_remaining",
        )
        with pytest.raises(ValueError, match="pressure policy"):
            validate_reference_admission(
                runtime._agent_admission.status(),
                control_interval_ms=2000, free_continuation_admission=True,
                workflow_size_resume=True,
                workflow_work_model_sha256=runtime.workflow_work_model_sha256,
            )
        assert preflight_runtime(configured, str(tmp_path))[
            "verified_configuration"
        ]["agent_pressure_pause_policy"] == "workflow_remaining"
    finally:
        runtime.close()
