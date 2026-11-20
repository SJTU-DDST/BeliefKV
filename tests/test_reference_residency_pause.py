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
from beliefkv.runtime.sglang_v0520_admission import _request_key
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.run_causal_comparison import preflight_runtime
from tests.test_demand_restore_capacity import _backup, bind_host_slots
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


@pytest.mark.parametrize(("new_input", "keeps_active"), ((4, True), (5, False)))
def test_native_pressure_tool_tail_defers_pause_until_successor_is_known(
    demand_runtime, monkeypatch, new_input, keeps_active,
):
    runtime, admission, _controller = install(demand_runtime)
    _, _, cache, _, donor, _, _ = demand_runtime
    runtime._native_chunk_input_reserve = 8
    key = admission.latest["tool"]
    cache._demote(donor.id, {})
    _, actions = cache.tree_core.split_host_only_full_prefix(donor.id, donor.creation_time, 28)
    cache._apply_cache_actions(actions)
    assert cache.load_back(donor.id)
    assert cache.ready_to_load_host_cache() >= 0
    cache.loading_check(finish_count=1)
    runtime._remember_parent_pressure_node(donor.id, key, donor.creation_time)

    remaining = admission.policy.remaining_capacity()
    freed = {}
    cache._demote(donor.id, freed)
    assert freed == {0: 4}
    runtime._on_parent_pressure_parked(donor.id, freed)
    assert admission.policy.programs["tool"].active
    assert admission.policy.remaining_capacity() == remaining
    assert key.invocation_id not in admission._residency_pause.paused
    assert admission.counts["residency_tool_tail_pause_deferred"] == 1

    runtime.on_events((event(
        10, Kind.TOOL_END, invocation_id="tool", attributes={"tool_run_id": "long"},
    ),))
    following = req("tool")
    following.rid = "tool-following"
    following.origin_input_ids = array("q", range(1, 33 + new_input))
    following.output_ids = array("q")
    following.session_id = key.session_id
    following.session_generation = key.session_generation
    following.kv = NS(req_pool_idx=None, kv_allocated_len=0, kv_committed_len=0)
    matching = cache.inspect_beliefkv_reentry(following)
    assert matching["device_checkpoint_tokens"] == 28
    assert matching["missing_full_tokens"] == 4
    monkeypatch.setattr(admission, "_admit_free_continuation", lambda **_kwargs: False)

    assert runtime.register_visible_request(following)
    assert admission.policy.programs["tool"].active is keeps_active
    assert admission.counts["residency_pauses"] == int(not keeps_active)


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


@pytest.mark.parametrize("full_native", (32768,), indirect=True)
@pytest.mark.parametrize("ownership", ("released", "stale-request"))
def test_native_staged_restore_preserves_ready_continuation(full_native, monkeypatch, ownership):
    from sglang.srt.managers.schedule_batch import Req
    from sglang.srt.mem_cache.base_prefix_cache import InitLoadBackParams
    from sglang.srt.sampling.sampling_params import SamplingParams
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, writes, loads, _ = full_native
    slice_tokens, missing = 2048, 14_000
    checkpoint = slice_tokens + missing
    host = bind_host_slots(cache, writes, checkpoint, monkeypatch)
    leaf, generation = insert_session(cache, range(1, checkpoint + 1), "root-session")
    host_slots = _backup(cache, leaf)

    for name, value in arm_environment({
        **settings(), "DEMAND_PREFILL_PIPELINE": "1",
        "BELIEFKV_SUBAGENT_MATERIALIZATION": "pressure_v2",
    }, "full").items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH", "0")
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "")
    monkeypatch.setenv("SGLANG_HICACHE_SKIP_HOST_DUPLICATE_RECLAIM", "0")
    runtime = NativeAdmissionRuntime()
    try:
        runtime.attach_native_cache(cache)
        runtime._native_chunk_input_reserve = slice_tokens
        runtime._native_page_size = cache.page_size
        admission = runtime._agent_admission
        pause = admission._residency_pause
        gate = runtime._subagent_materialization
        restore = gate._demand_restore_chunk
        now = time.monotonic()
        admission.policy.arrive("root", checkpoint, now=now)
        admission.policy.completed("root", checkpoint, now=now)
        runtime.on_events((
            event(0, Kind.WORKFLOW_START),
            event(1, Kind.INVOCATION_CREATE, invocation_id="root", context_id="ctx-root",
                  context_epoch=14),
            event(2, Kind.LLM_SUBMIT, invocation_id="root", context_id="ctx-root",
                  context_epoch=14, attributes={"request_id": "root-following"}),
        ))
        successor = Req(
            rid="root-following", origin_input_text="",
            origin_input_ids=array("q", range(1, checkpoint + 2)),
            sampling_params=SamplingParams(max_new_tokens=2),
            session_id="root-session",
            beliefkv_metadata={
                "root_workflow_id": "wf", "invocation_id": "root",
                "context_id": "ctx-root", "context_epoch": 14,
            },
        )
        successor.session_generation = generation
        assert runtime.register_visible_request(successor)
        key = runtime.visible[successor.rid]
        item = admission.policy.programs[key.invocation_id]
        assert item.active and item.pending and item.steps == 2
        assert runtime._executable_waiting_key(successor) == key and admission.allows(key)
        assert cache.inspect_beliefkv_reentry(successor)["missing_full_tokens"] == 0

        cache._demote(leaf.id, {})
        successor.init_next_round_input(cache)
        assert successor.best_match_node == leaf.id and successor.host_hit_length == checkpoint
        params = InitLoadBackParams(
            best_match_node=leaf.id, host_hit_length=checkpoint, req=successor,
        )
        assert restore.start(
            params, key, slice_tokens, device_reserve=slice_tokens + 1, protected_nodes=(leaf.id,),
        ) is None
        pending = restore.pending
        assert (pending.request, pending.key, pending.remaining_tokens) == (successor, key, missing)
        ack, = cache.cache_controller.ack_load_queue
        assert ack.node_ids == [pending.prefix_node] and ack.num_tokens_by_pool == {"kv": slice_tokens}
        cache.loading_check(finish_count=1)
        ack.finish_event.synchronize.assert_called_once_with()

        prefix = cache.tree_core.node_by_id(pending.prefix_node).component_data[0]
        suffix = leaf.component_data[0]
        assert (prefix.lock_ref, prefix.host_lock_ref, suffix.host_lock_ref) == (1, 0, 1)
        assert suffix.value is None and host.slot_used[host_slots].all()
        observed = runtime._read_request_reentry(successor, key=key, refresh=True)
        assert observed["device_checkpoint_tokens"] == slice_tokens
        assert observed["missing_full_tokens"] == missing
        assert not gate.has_staged_restore(successor, replace(key, attempt_id=key.attempt_id + 1))
        assert gate.has_staged_restore(successor, key)

        capacity, revision = admission.policy.remaining_capacity(), runtime.semantic_revision
        admission.synchronize_residency([successor])
        assert item.active and admission.allows(key)
        assert admission.policy.remaining_capacity() == capacity
        assert runtime.semantic_revision == revision
        assert not pause.paused and key not in pause.compared_requests
        assert admission.counts["residency_pauses"] == 0
        assert restore.pending is pending and (prefix.lock_ref, suffix.host_lock_ref) == (1, 1)

        current = successor
        if ownership == "released":
            assert not gate.defer_start(successor)
            assert restore.pending is None and (prefix.lock_ref, suffix.host_lock_ref) == (0, 0)
        else:
            current = copy(successor)
            assert current is not successor and _request_key(current) == key
            admission._live_requests[key.request_id] = current
            assert restore.pending is pending and (prefix.lock_ref, suffix.host_lock_ref) == (1, 1)
        assert not gate.has_staged_restore(current, key)
        assert runtime._read_request_reentry(current, key=key, refresh=True)["missing_full_tokens"] == missing

        pause.next_check = 0.
        admission.synchronize_residency([current])
        assert not item.active and pause.paused[key.invocation_id] == key
        assert key in pause.compared_requests
        assert admission.counts["residency_pauses"] == 1
        assert admission.counts["residency_pause_observed_missing_full_tokens"] == missing
        assert admission.policy.remaining_capacity() == capacity + item.tokens + admission.policy.buffer_tokens
        assert len(loads) == 1 and allocator.available_size() == allocator.size - slice_tokens
        cache.sanity_check()
    finally:
        if cache.cache_controller.ack_load_queue:
            cache.loading_check(finish_count=1)
        runtime.close()


@pytest.mark.parametrize(("new_input", "keeps_active"), ((4, True), (5, False)))
def test_partial_host_tail_preserves_admission_within_one_prefill_chunk(
    demand_runtime, monkeypatch, new_input, keeps_active,
):
    runtime, admission, _controller = install(demand_runtime)
    _, _, cache, _, donor, consumer, _ = demand_runtime
    runtime._native_chunk_input_reserve = 8
    cache._demote(donor.id, {})
    anchors = cache.inspect_beliefkv_reentry(consumer)
    (leaf_id, created), = dict(anchors["component_leaves"])[0]
    device_prefix, actions = cache.tree_core.split_host_only_full_prefix(leaf_id, created, 12)
    cache._apply_cache_actions(actions)
    assert cache.load_back(device_prefix)
    assert cache.ready_to_load_host_cache() >= 0
    cache.loading_check(finish_count=1)

    admission.completed(NS(reqs=[consumer]), (consumer.rid,))
    following = copy(consumer)
    following.rid = "consumer-following"
    following.origin_input_ids = array("q", range(101, 117 + new_input))
    following.kv = NS(req_pool_idx=None, kv_allocated_len=0, kv_committed_len=0)
    matching = cache.inspect_beliefkv_reentry(following)
    assert matching["device_checkpoint_tokens"] == 12
    assert matching["missing_full_tokens"] == 4
    assert len(following.origin_input_ids) - matching["checkpoint_tokens"] == new_input
    monkeypatch.setattr(admission, "_admit_free_continuation", lambda **_kwargs: False)
    runtime.register_visible_request(following)
    assert admission.policy.programs["consumer"].active is keeps_active

    if keeps_active:
        cache._demote(device_prefix, {})
        admission._residency_pause.next_check = 0.
        admission.synchronize_residency([following])
        assert not admission.policy.programs["consumer"].active
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
