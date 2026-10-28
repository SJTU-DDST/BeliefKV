"""Observe a current eligible consumer before native demand removes its queue entry."""

from array import array
from dataclasses import replace
from types import SimpleNamespace as NS
import time
from unittest.mock import Mock

import pytest

from beliefkv.control.causal_graph import InvocationState
from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import MODES, arm_environment
from beliefkv.experiments.paper_baselines import environment as paper_environment
from beliefkv.runtime.reference_thunder_admission import ReferenceThunderAdmission
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts import run_causal_comparison as comparison
from tests.test_full_only_native_lifecycle import full_native
from tests.test_reference_thunder_admission import state
from tests.test_tool_policy_adaptation import demand_runtime
from tests.test_tool_predictive_transfers import event


def install_reference(runtime, cache, consumer):
    runtime.enable_execution_handoff = False
    runtime.enable_frontier_restore = False
    adapter = ReferenceThunderAdmission(runtime)
    adapter.attach(cache)
    # The real native CPU fixture has 64 slots; its buffer is normalized only here.
    adapter.policy.buffer_tokens = 0
    adapter.policy.arrive("tool", 32, now=0.)
    adapter.policy.completed("tool", 32, now=.1)
    adapter.latest["tool"] = runtime.context_sessions["ctx-tool"]
    adapter.policy.arrive("hot", 12, now=.15)
    adapter.registered(consumer, runtime.visible["consumer"])
    adapter.next_tick = float("inf")
    runtime._agent_admission = adapter
    runtime._native_prefill_order = True
    return adapter


def enable_early(demand_runtime):
    runtime, controller, cache, _allocator, _donor, consumer, _writes = demand_runtime
    adapter = install_reference(runtime, cache, consumer)
    runtime.enable_predicted_tool_offload = True
    runtime.tool_offload_before_admission = True
    runtime._predicted_tool_offload = controller
    runtime._capacity_waiting_queue = ()
    return adapter


def configuration():
    return {
        "AGENT_ADMISSION": "thunderagent_reference",
        "PREDICTED_TOOL_OFFLOAD": "1",
        "BELIEFKV_TOOL_OFFLOAD_POLICY": "demand_cost",
        "TOOL_OFFLOAD_BEFORE_ADMISSION": "1",
        "EXECUTION_FRONTIER_RESTORE": "0",
        "EXECUTION_HANDOFF": "0",
    }


@pytest.mark.parametrize("enabled", (False, True))
def test_eligible_host_waiter_is_seen_before_native_allocation_and_queue_removal(
    demand_runtime, enabled,
):
    runtime, controller, cache, allocator, donor, consumer, _writes = demand_runtime
    adapter = install_reference(runtime, cache, consumer)
    runtime.enable_predicted_tool_offload = True
    runtime.tool_offload_before_admission = enabled
    runtime._predicted_tool_offload = controller
    # The earlier post-forward pass cannot see this newly submitted consumer.
    runtime._capacity_waiting_queue = ()
    controller.dispatch()
    assert runtime.counts["tool_offload_no_host_waiting_consumer"] == 1
    assert allocator.available_size() == 4
    previous = state(adapter)

    plan = runtime.plan_native_prefill(
        [consumer], running_batch=NS(reqs=[]), adder=None,
    )

    assert plan.prioritized == (runtime.visible["consumer"],)
    assert allocator.available_size() == (36 if enabled else 4)
    assert (donor.component_data[0].value is None) is enabled
    assert donor.component_data[0].host_value is not None
    assert runtime._capacity_waiting_queue == ()
    assert state(adapter) == previous
    assert not runtime.physical_disabled


@pytest.mark.parametrize("mode", MODES)
def test_controls_strip_early_offload(mode):
    settings = arm_environment(configuration(), mode)
    expected = "1" if mode == "full" else "0"
    assert settings["TOOL_OFFLOAD_BEFORE_ADMISSION"] == expected
    assert settings["BELIEFKV_TOOL_OFFLOAD_BEFORE_ADMISSION"] == expected


@pytest.mark.parametrize("method", (
    "thunderagent", "thunderagent_acting_decay", "kvflow_observed_graph",
))
def test_paper_methods_do_not_inherit_early_offload(method):
    assert paper_environment(configuration(), method)["BELIEFKV_TOOL_OFFLOAD_BEFORE_ADMISSION"] == "0"


def test_actual_runtime_constructor_binds_explicit_reference_candidate(monkeypatch, tmp_path):
    for key, value in arm_environment(configuration(), "full").items():
        monkeypatch.setenv(key, value)
    runtime = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "early.sock"))
    try:
        assert runtime.tool_offload_before_admission
        assert runtime._predicted_tool_offload is not None
        assert runtime._agent_admission.strategy == "thunderagent_reference"
    finally:
        runtime.close()


@pytest.mark.parametrize("change", (
    {"TOOL_OFFLOAD_BEFORE_ADMISSION": "yes"},
    {"PREDICTED_TOOL_OFFLOAD": "0"},
    {"BELIEFKV_TOOL_OFFLOAD_POLICY": "fixed"},
    {"AGENT_ADMISSION": "off"},
))
def test_candidate_rejects_incompatible_settings(change):
    with pytest.raises(ValueError, match="offload before admission"):
        arm_environment({**configuration(), **change}, "full")


@pytest.mark.parametrize("method,observed", (
    ("full", False), ("full", None), ("thunderagent_acting_decay", True),
))
def test_complete_observer_rejects_missing_or_leaked_early_offload(
    monkeypatch, tmp_path, method, observed,
):
    plan = {"reference_environment": configuration(), "source_plan": {"requests": 0}}
    snapshot = {
        "predicted_tool_offload": method == "full",
        "paper_baseline": {"counts": {"registered": 0, "completed": 0}},
    }
    if observed is not None:
        snapshot["tool_offload_before_admission"] = observed
    monkeypatch.setattr(comparison.comparison, "observe_arm", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(comparison.qualified, "last_runtime_state", lambda *_args: snapshot)
    with pytest.raises(ValueError, match="effective tool offload before admission"):
        comparison.observed_configuration(plan, method, tmp_path, final=True)


@pytest.mark.parametrize("reason", (
    "returned", "ready_donor", "pending_donor", "inactive_consumer", "stale_consumer",
    "missing_host", "locked", "short_prediction", "no_pressure", "disabled",
))
def test_early_hook_preserves_demand_prediction_identity_and_native_guards(
    demand_runtime, reason,
):
    runtime, _controller, cache, allocator, donor, consumer, _writes = demand_runtime
    adapter = enable_early(demand_runtime)
    if reason == "returned":
        runtime.on_events((event(
            5, Kind.TOOL_END, invocation_id="tool", attributes={"tool_run_id": "long"},
        ),))
    elif reason == "ready_donor":
        runtime.graph.invocations["tool"].state = InvocationState.RUNNING_LLM
    elif reason == "pending_donor":
        adapter.policy.arrive("tool", 32, now=.2)
    elif reason == "inactive_consumer":
        adapter.policy.programs["consumer"].active = False
    elif reason == "stale_consumer":
        consumer.cache_request_handle.attempt_id += 1
    elif reason == "missing_host":
        donor.component_data[0].host_value = None
        cache.tree_core._update_duplicate_tracking(donor)
        runtime.issue_shadow_backup_steps = Mock(return_value=())
    elif reason == "locked":
        donor.component_data[0].lock_ref += 1
    elif reason == "short_prediction":
        hint = runtime.tool_wait_hints["ctx-tool"]
        runtime.tool_wait_hints["ctx-tool"] = replace(
            hint, wait_p10_ms=1., wait_p50_ms=2., wait_p90_ms=3.,
        )
    elif reason == "no_pressure":
        consumer.origin_input_ids = array("q", range(101, 103))
    else:
        runtime.physical_disabled = True
    runtime.plan_native_prefill([consumer], running_batch=NS(reqs=[]), adder=None)
    assert allocator.available_size() == 4
    assert donor.component_data[0].value is not None
    assert not runtime.counts["predicted_tool_offload_freed_component_0"]
    assert runtime._capacity_waiting_queue == ()


@pytest.mark.parametrize("blocked_adder", (
    {"rem_chunk_tokens": 0}, {"rem_input_tokens": 0},
    {"max_running_requests": 1, "can_run_list": [NS()]},
    {"max_prefill_bs": 1, "can_run_list": [NS()]},
    {"prefill_max_requests": 1, "can_run_list": [NS()]},
))
def test_no_early_release_when_current_native_pass_cannot_admit(demand_runtime, blocked_adder):
    runtime, _controller, _cache, allocator, donor, consumer, _writes = demand_runtime
    enable_early(demand_runtime)
    runtime.plan_native_prefill(
        [consumer], running_batch=NS(reqs=[]), adder=NS(**blocked_adder),
    )
    assert allocator.available_size() == 4
    assert donor.component_data[0].value is not None
    assert not runtime.counts["tool_offload_before_admission_checks"]


@pytest.mark.parametrize("kind", (Kind.TOOL_END, Kind.INVOCATION_CANCEL))
def test_event_drain_revalidates_release_and_rebuilds_changed_plan(demand_runtime, kind):
    runtime, _controller, _cache, allocator, donor, consumer, _writes = demand_runtime
    enable_early(demand_runtime)
    incoming = (
        event(5, kind, invocation_id="tool", attributes={"tool_run_id": "long"})
        if kind is Kind.TOOL_END else event(
            5, kind, invocation_id="consumer", context_id="ctx-consumer",
        )
    )
    runtime.event_server = NS(
        drain=Mock(side_effect=lambda **_kwargs: runtime.on_events((incoming,))),
        close=Mock(),
    )
    plan = runtime.plan_native_prefill([consumer], running_batch=NS(reqs=[]), adder=None)
    assert allocator.available_size() == 4
    assert donor.component_data[0].value is not None
    assert runtime.counts["tool_offload_admission_event_replans"] == 1
    assert plan.semantic_revision == runtime.semantic_revision
    assert bool(plan.prioritized) is (kind is Kind.TOOL_END)
    assert runtime._capacity_waiting_queue == ()


def test_same_pending_request_retains_maintenance_backoff(demand_runtime, monkeypatch):
    runtime, controller, _cache, allocator, donor, consumer, _writes = demand_runtime
    enable_early(demand_runtime)
    monkeypatch.setattr(time, "monotonic", lambda now=time.monotonic(): now)
    donor.component_data[0].lock_ref += 1
    runtime.plan_native_prefill([consumer], running_batch=NS(reqs=[]), adder=None)
    assert runtime.counts["tool_offload_before_admission_checks"] == 1
    for _ in range(10):
        runtime.plan_native_prefill([consumer], running_batch=NS(reqs=[]), adder=None)
    assert runtime.counts["tool_offload_before_admission_checks"] == 1
    assert runtime.counts["tool_offload_admission_new_request_rearms"] == 1
    assert allocator.available_size() == 4
    assert controller._dispatch_stage == "maintenance"


def test_early_dispatch_restores_maintenance_scope_after_failure(demand_runtime):
    runtime, controller, _cache, _allocator, _donor, consumer, _writes = demand_runtime
    enable_early(demand_runtime)
    controller.consumer_cursor = 13
    controller.dispatch = Mock(side_effect=RuntimeError("fixture failure"))
    before = runtime._native_running_batch
    with pytest.raises(RuntimeError, match="fixture failure"):
        runtime.plan_native_prefill([consumer], running_batch=before, adder=None)
    assert runtime._capacity_waiting_queue == ()
    assert runtime._native_running_batch is before
    assert controller.consumer_cursor == 13
    assert controller._dispatch_stage == "maintenance"


@pytest.mark.parametrize("reason", ("no_hints", "disabled"))
def test_unavailable_tool_source_skips_additional_candidate_inspection(demand_runtime, reason):
    runtime, controller, _cache, _allocator, _donor, consumer, _writes = demand_runtime
    enable_early(demand_runtime)
    if reason == "no_hints":
        runtime.tool_wait_hints.clear()
    else:
        runtime.physical_disabled = True
    controller.before_admission = Mock(side_effect=AssertionError("unnecessary candidate inspection"))
    runtime.plan_native_prefill([consumer], running_batch=NS(reqs=[]), adder=None)
    controller.before_admission.assert_not_called()


def test_existing_native_adder_and_load_see_only_actually_freed_capacity(demand_runtime):
    from sglang.srt.managers.schedule_policy import PrefillAdder

    runtime, _controller, cache, allocator, donor, consumer, _writes = demand_runtime
    enable_early(demand_runtime)
    batch = NS(reqs=[])
    adder = PrefillAdder(
        1, cache, allocator, batch, 1., 1, None, max_running_requests=24,
    )
    assert allocator.available_size() == 4
    before = adder.rem_total_tokens
    hot = next(
        node for node in cache.tree_core._node_arena.values()
        if node.component_data[0].session_ids == {"hot"}
    )
    hot_indices = hot.component_data[0].value.clone()
    runtime.plan_native_prefill([consumer], running_batch=batch, adder=adder)
    assert donor.component_data[0].value is None
    assert allocator.available_size() == 36
    assert adder.rem_total_tokens >= before
    source = next(
        node for node in cache.tree_core._node_arena.values()
        if node.component_data[0].session_ids == {"consumer-session"}
    )
    loaded = cache.cache_controller.load(
        source.component_data[0].host_value, node_id=source.id, extra_pools=None,
        beliefkv_no_device_reclaim=False, beliefkv_command_id=None,
        beliefkv_before_enqueue=None,
    )
    assert len(loaded) == 16
    assert allocator.available_size() == 20
    assert hot.component_data[0].value.equal(hot_indices)
    assert not runtime.physical_disabled
