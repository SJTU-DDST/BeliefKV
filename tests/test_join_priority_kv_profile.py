"""Full can share the complete JOIN-only ordering policy while retaining KV control."""

from dataclasses import replace
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.experiments.internal_comparison import arm_environment, validate_runtime
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_internal_comparison import join_fixture
from tests.test_tool_predictive_transfers import (
    locked_runtime, req,
    test_tool_h2d_uses_same_wait_identity_and_does_not_admit_agent as exercise_tool_h2d,
)
from tests.test_idle_full_prepare_pipeline import (
    test_idle_prepare_tracks_issuance_without_any_capacity_or_prefetch_credit as exercise_idle_prepare,
)


@pytest.mark.parametrize("quota", (0, 3, 4))
@pytest.mark.parametrize("aged", ("none", "ordinary", "parent"))
@pytest.mark.parametrize("pending", (False, True))
def test_full_kv_profile_matches_join_only_even_between_join_boosts(
    monkeypatch, quota, aged, pending,
):
    observations = []
    for mode in ("full", "join_priority_only"):
        runtime, requests = join_fixture(
            monkeypatch, mode=mode, scheduling="join_priority",
        )
        runtime._join_priority_normal_admissions = quota
        if not pending:
            runtime._join_priority_pending.clear()
        if aged != "none":
            runtime._visible_since[requests[int(aged == "parent")].rid] = time.monotonic() - 11.
        try:
            for name in (
                "_prefill_causal_ranks", "_read_request_residency_hint",
                "_read_request_reentry", "_resident_aged_bypass", "_final_admission_work",
            ):
                monkeypatch.setattr(runtime, name, Mock(side_effect=AssertionError(name)))
            plan = runtime.plan_native_prefill(requests, running_batch=None, adder=None)
            selected = next(request for request in requests
                            if request.rid == plan.prioritized[0].request_id)
            runtime.on_prefill_candidate_result(selected, admitted=True, result="CONTINUE")
            observations.append((
                [key.request_id for key in plan.prioritized],
                runtime._join_priority_normal_admissions,
                dict(runtime._join_priority_pending),
            ))
            assert runtime.prefill_scheduling_policy == "join_priority"
            assert not runtime.allow_prefill_capacity_bypass(
                requests[0], native_budget_available=True, bypassed=0,
            )
            assert runtime._custom_kv_management is (mode == "full")
        finally:
            runtime.close()
    assert observations[0] == observations[1]


@pytest.mark.parametrize("operation", ("tool_h2d", "background_prepare", "pressure_unlock"))
def test_simple_ordering_keeps_actual_kv_control_paths(monkeypatch, operation):
    monkeypatch.setenv("BELIEFKV_PREFILL_SCHEDULING_POLICY", "join_priority")
    if operation == "tool_h2d":
        exercise_tool_h2d()
    elif operation == "background_prepare":
        exercise_idle_prepare()
    else:
        runtime, _, _, action, receipt, _ = locked_runtime()
        try:
            runtime.on_prefill_candidate_result(req("other"), admitted=False, result="NO_TOKEN")
            runtime._native_cache.tree_core.dec_lock_ref.assert_called_once_with(1, receipt)
            assert runtime._prefetch_service_leases[action.command_id].lock_params is None
        finally:
            runtime.close()


def test_restore_lease_cannot_change_simple_queue_order_or_trigger_inspection(monkeypatch):
    runtime, requests = join_fixture(monkeypatch, mode="full", scheduling="join_priority")
    runtime._join_priority_pending.clear()
    try:
        from beliefkv.runtime.sglang_v0520_runtime import _PrefetchServiceLease

        key = runtime.visible[requests[1].rid]
        runtime._prefetch_service_leases["ready"] = _PrefetchServiceLease(
            replace(key, request_id="prior"), "ready", 1, 1., "tool_wait", None,
            time.monotonic(), time.monotonic() + 10., (), demand_ready=True,
        )
        runtime._read_request_reentry = Mock(side_effect=AssertionError("no restore ranking"))
        runtime._prefill_causal_ranks = Mock(side_effect=AssertionError("no causal ranking"))
        plan = runtime.plan_native_prefill(requests, running_batch=None, adder=None)
        assert [key.request_id for key in plan.prioritized] == [request.rid for request in requests]
        assert runtime._prefetch_priority_promoted is None
    finally:
        runtime.close()


@pytest.mark.parametrize("mode", ("full", "scheduler_only", "join_priority_only", "kv_only"))
@pytest.mark.parametrize("profile", ("workflow", "join_priority"))
def test_effective_profile_and_runtime_validation_are_independent_of_kv_mode(
    monkeypatch, tmp_path, mode, profile,
):
    environment = arm_environment({"PREFILL_SCHEDULING_POLICY": profile}, mode)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    for name in ("ONLINE_TOOL_TIMING", "BORROWABLE_PARENT_HEADROOM"):
        monkeypatch.setenv("BELIEFKV_" + name, environment[name])
    runtime = NativeAdmissionRuntime(
        event_socket_path=str(tmp_path / "runtime.sock"),
        enable_tool_prefetch=mode in ("full", "kv_only"),
    )
    runtime._opportunity_writer = Mock()
    try:
        runtime._record_runtime_state()
        state = runtime._opportunity_writer.record.call_args.args[0]
        expected = (
            "native" if mode == "kv_only" else
            "join_priority" if mode == "join_priority_only" else profile
        )
        assert state["prefill_scheduling_policy"] == expected
        assert state["resident_first"] is (mode in ("full", "scheduler_only") and profile == "workflow")
        assert state["custom_kv_management"] is (mode in ("full", "kv_only"))
        validate_runtime(mode, state, scheduling_policy=profile)
        if mode in ("full", "scheduler_only"):
            with pytest.raises(ValueError, match="prefill_scheduling_policy"):
                validate_runtime(
                    mode, state,
                    scheduling_policy="workflow" if profile == "join_priority" else "join_priority",
                )
    finally:
        runtime._opportunity_writer = None
        runtime.close()


@pytest.mark.parametrize("invalid", ("resident", "", "2"))
def test_unknown_profile_is_rejected(monkeypatch, invalid):
    monkeypatch.setenv("BELIEFKV_PREFILL_SCHEDULING_POLICY", invalid)
    with pytest.raises(ValueError, match="prefill scheduling policy"):
        NativeAdmissionRuntime()
