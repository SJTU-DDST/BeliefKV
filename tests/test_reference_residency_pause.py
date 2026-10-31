from array import array
from copy import copy
from dataclasses import replace
from types import SimpleNamespace as NS
import time

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import MODES, arm_environment
from beliefkv.experiments.paper_baselines import environment as paper_environment
from beliefkv.runtime.reference_free_continuation import ReferenceFreeContinuationAdmission
from beliefkv.runtime.reference_thunder_admission import validate_reference_admission
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.run_causal_comparison import preflight_runtime
from tests.test_full_only_native_lifecycle import full_native
from tests.test_sglang_v0520_runtime import req
from tests.test_tool_policy_adaptation import demand_runtime
from tests.test_tool_predictive_transfers import event


def settings():
    return {
        "AGENT_ADMISSION": "thunderagent_reference",
        "AGENT_FREE_CONTINUATION_ADMISSION": "1",
        "AGENT_FREE_DISPATCH_INTERVAL_MS": "500",
        "AGENT_RESIDENCY_PAUSE": "1",
        "TOOL_PREFETCH": "0", "LAYER_HANDOFF": "0",
    }


def install(parts):
    runtime, controller, cache, _allocator, _donor, consumer, _writes = parts
    runtime.agent_residency_pause = True
    runtime.agent_free_continuation_admission = True
    runtime.agent_free_dispatch_interval_ms = 500
    runtime.enable_frontier_restore = runtime.enable_execution_handoff = False
    runtime.enable_tool_prefetch = False
    admission = ReferenceFreeContinuationAdmission(runtime)
    admission.attach(cache)
    admission.policy.buffer_tokens = 0
    admission.policy.arrive("tool", 32, now=time.monotonic())
    admission.policy.completed("tool", 32, now=time.monotonic())
    admission.latest["tool"] = runtime.context_sessions["ctx-tool"]
    admission.policy.arrive("hot", 12, now=time.monotonic())
    admission.registered(consumer, runtime.visible["consumer"])
    admission.next_tick = float("inf")
    runtime._agent_admission = admission
    return runtime, admission, controller


@pytest.mark.parametrize("source", ("prediction", "native_pressure"))
def test_real_full_release_pauses_owner_without_changing_the_consumer_grant(
    demand_runtime, source,
):
    runtime, admission, controller = install(demand_runtime)
    _runtime, _controller, cache, allocator, donor, consumer, _writes = demand_runtime
    key = admission.latest["tool"]
    assert admission.policy.programs["tool"].active
    before_revision = runtime.semantic_revision
    if source == "prediction":
        controller.dispatch()
    else:
        freed = {}
        cache._demote(donor.id, freed)
        runtime._on_parent_pressure_parked(donor.id, freed)
    assert donor.component_data[0].value is None
    assert donor.component_data[0].host_value is not None
    assert allocator.available_size() == 36
    assert not admission.policy.programs["tool"].active
    assert admission.policy.programs["consumer"].active
    assert key.invocation_id in admission._residency_pause.paused
    assert runtime.semantic_revision > before_revision
    assert admission.counts["residency_pauses"] == 1
    admission.tick(now=time.monotonic() + 10.)
    assert not admission.policy.programs["tool"].active
    assert "tool" not in admission._dispatch_pending


@pytest.mark.parametrize("change", ("backup_only", "mamba_only", "stale_key", "pending", "returned"))
def test_only_a_real_current_full_release_of_a_blocked_owner_revokes_eligibility(
    demand_runtime, change,
):
    runtime, admission, _controller = install(demand_runtime)
    key = admission.latest["tool"]
    freed = {0: 32}
    if change == "backup_only":
        freed = {}
    elif change == "mamba_only":
        freed = {2: 1}
    elif change == "stale_key":
        key = replace(key, session_generation=key.session_generation + 1)
    elif change == "pending":
        admission.policy.programs["tool"].pending = True
    else:
        runtime.on_events((event(
            10, Kind.TOOL_END, invocation_id="tool", attributes={"tool_run_id": "long"},
        ),))
    assert not admission.full_kv_offloaded(key, freed, source="test")
    assert admission.policy.programs["tool"].active
    assert not admission.counts["residency_pauses"]


def test_returned_offloaded_tool_requires_a_real_request_and_then_competes_again(demand_runtime):
    runtime, admission, controller = install(demand_runtime)
    controller.dispatch()
    runtime.on_events((event(
        10, Kind.TOOL_END, invocation_id="tool", attributes={"tool_run_id": "long"},
    ),))
    assert not admission.policy.programs["tool"].active
    assert "tool" not in admission._dispatch_pending
    following = req("tool")
    following.rid = "tool-following"
    following.origin_input_ids = array("q", range(1, 34))
    following.output_ids = array("q")
    following.session_id = admission.latest["tool"].session_id
    following.session_generation = admission.latest["tool"].session_generation
    following.kv = NS(req_pool_idx=None, kv_allocated_len=0, kv_committed_len=0)
    runtime.register_visible_request(following)
    assert admission.policy.programs["tool"].active
    assert admission.counts["free_ordinary_continuation_admitted"] == 1
    key = admission.latest["tool"]
    assert key in admission._residency_pause.compared_requests
    admission.synchronize_residency([following])
    assert admission.policy.programs["tool"].active
    assert admission.counts["residency_pauses"] == 1
    admission.native_service(NS(reqs=[following]), ())
    assert "tool" not in admission._residency_pause.paused


@pytest.mark.parametrize("guard", (None, "running", "allocated", "output"))
def test_native_host_checkpoint_cannot_bypass_comparison_on_a_continuation(
    demand_runtime, monkeypatch, guard,
):
    runtime, admission, _controller = install(demand_runtime)
    consumer = demand_runtime[5]
    admission.completed(NS(reqs=[consumer]), (consumer.rid,))
    following = copy(consumer)
    following.rid = "consumer-following"
    following.kv = NS(req_pool_idx=None, kv_allocated_len=0, kv_committed_len=0)
    following.output_ids = array("q")
    if guard == "running":
        runtime._native_running_batch = NS(reqs=[following])
    elif guard == "allocated":
        following.kv.req_pool_idx = 0
        following.kv.kv_allocated_len = 16
    elif guard == "output":
        following.output_ids.append(1)
    monkeypatch.setattr(admission, "_admit_free_continuation", lambda **_kwargs: False)
    runtime.register_visible_request(following)
    assert admission.policy.programs["consumer"].active is (guard is not None)
    assert admission.counts["residency_pauses"] == (0 if guard is not None else 1)
    if guard is None:
        assert runtime.visible[following.rid] in admission._residency_pause.compared_requests
        assert "consumer" in admission._dispatch_pending


def test_queue_time_native_loss_is_detected_and_a_chosen_restore_is_not_paused_again(
    demand_runtime,
):
    runtime, admission, _controller = install(demand_runtime)
    consumer = demand_runtime[5]
    admission.policy.programs["consumer"].steps = 2
    admission.synchronize_residency([consumer])
    assert not admission.policy.programs["consumer"].active
    assert admission.counts["residency_continuation_checks"] == 1
    assert admission._dispatch_free_capacity()
    assert admission.policy.programs["consumer"].active
    admission._residency_pause.next_check = 0.
    admission.synchronize_residency([consumer])
    assert admission.policy.programs["consumer"].active
    assert admission.counts["residency_continuation_checks"] == 1
    assert admission.counts["residency_pauses"] == 1


@pytest.mark.parametrize("mode", MODES)
def test_residency_coupling_is_an_explicit_full_only_contract(mode):
    configured = arm_environment(settings(), mode)
    assert configured["BELIEFKV_AGENT_RESIDENCY_PAUSE"] == ("1" if mode == "full" else "0")


@pytest.mark.parametrize("method", ("thunderagent", "thunderagent_acting_decay", "kvflow_observed_graph"))
def test_paper_policy_never_inherits_full_residency_pause(method):
    assert paper_environment(settings(), method)["BELIEFKV_AGENT_RESIDENCY_PAUSE"] == "0"


@pytest.mark.parametrize("change", (
    {"AGENT_RESIDENCY_PAUSE": "yes"}, {"AGENT_FREE_CONTINUATION_ADMISSION": "0"},
    {"AGENT_FREE_DISPATCH_INTERVAL_MS": "0"}, {"AGENT_ADMISSION": "off"},
))
def test_incompatible_residency_pause_is_rejected(change):
    with pytest.raises(ValueError):
        arm_environment({**settings(), **change}, "full")


def test_constructor_preflight_and_runtime_status_require_the_same_declaration(monkeypatch, tmp_path):
    configured = arm_environment(settings(), "full")
    for name, value in configured.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "")
    runtime = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "coupled.sock"))
    try:
        runtime.attach_native_cache(NS(
            tree_components=(0,), token_to_kv_pool_allocator=NS(size_full=1000),
        ))
        state = runtime._agent_admission.status()
        validate_reference_admission(
            state, free_continuation_admission=True, free_dispatch_interval_ms=500,
            residency_pause=True,
        )
        with pytest.raises(ValueError, match="residency pause"):
            validate_reference_admission(
                state, free_continuation_admission=True, free_dispatch_interval_ms=500,
            )
        assert preflight_runtime(configured, str(tmp_path))[
            "verified_configuration"
        ]["agent_residency_pause"] is True
    finally:
        runtime.close()
