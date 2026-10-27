"""Predictive offload must actually free native KV and preserve return races."""

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import MODES, arm_environment
from beliefkv.experiments.paper_baselines import environment as paper_environment
from beliefkv.runtime.predicted_tool_offload import PredictedToolOffload
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_tool_predictive_transfers import event, tool_runtime
from tests.test_waiting_full_tail_backup import native_cache, waiting_tail
from tests.test_full_only_native_lifecycle import full_native
from tests.test_prepare_ack_allocation import native_write_start


@pytest.mark.parametrize("mode", MODES)
def test_configuration_enables_offload_only_in_kv_arms(mode):
    env = arm_environment({"PREDICTED_TOOL_OFFLOAD": "1"}, mode)
    assert env["BELIEFKV_ENABLE_PREDICTED_TOOL_OFFLOAD"] == (
        "1" if mode in ("full", "kv_only") else "0"
    )


@pytest.mark.parametrize("arm", ("thunderagent", "thunderagent_acting_decay", "kvflow_observed_graph"))
def test_paper_baselines_never_inherit_predictive_offload(arm):
    env = paper_environment({"PREDICTED_TOOL_OFFLOAD": "1"}, arm)
    assert env["BELIEFKV_ENABLE_PREDICTED_TOOL_OFFLOAD"] == "0"


def test_runtime_installs_explicit_offload_controller(monkeypatch):
    monkeypatch.setenv("BELIEFKV_ENABLE_PREDICTED_TOOL_OFFLOAD", "1")
    runtime = NativeAdmissionRuntime()
    try:
        assert runtime.enable_predicted_tool_offload
        assert isinstance(runtime._predicted_tool_offload, PredictedToolOffload)
    finally:
        runtime.close()


def test_candidate_keeps_the_frozen_comparison_profile_except_explicit_offload():
    root = Path(__file__).resolve().parents[1]
    base = json.loads((root / "configs/migration/predictive_join_dependency_admission_20261027.json").read_bytes())
    candidate = json.loads((root / "configs/migration/predictive_tool_offload_20261027.json").read_bytes())
    common = candidate["common_environment"]
    assert {
        name: value for name, value in common.items()
        if name not in ("PREDICTED_TOOL_OFFLOAD", "BELIEFKV_ENABLE_PREDICTED_TOOL_OFFLOAD")
    } == base["common_environment"]
    from scripts.run_causal_comparison import experiment_reference

    reference = experiment_reference({"arms": {"full": {}}}, candidate)
    env = arm_environment(reference["arms"]["full"], "full")
    assert env["BELIEFKV_ENABLE_PREDICTED_TOOL_OFFLOAD"] == "1"


def controller_fixture(waiting_tail):
    runtime, hint, cache, allocator, pool, checkpoint, leaf = waiting_tail
    controller = PredictedToolOffload(runtime)
    cache.evict = Mock(side_effect=AssertionError("no global eviction"))
    cache.writing_check = Mock(side_effect=AssertionError("no blocking write ACK"))
    cache._execute_and_commit_kv_backup = Mock(
        side_effect=AssertionError("no untagged synchronous backup"),
    )
    runtime._predicted_tool_offload = controller
    # Complete the private FULL tail exactly as a settled native ACK would.
    leaf.component_data[0].host_value = leaf.component_data[0].value.clone()
    cache.tree_core._update_duplicate_tracking(leaf)
    runtime._register_backed_pressure_nodes(hint.key)
    return runtime, hint, cache, allocator, pool, checkpoint, leaf, controller


def test_real_native_offload_frees_device_slots_and_retains_host_and_agent(waiting_tail):
    runtime, hint, cache, allocator, pool, checkpoint, leaf, controller = controller_fixture(waiting_tail)
    initial = allocator.available_size()
    state = checkpoint.component_data[2].value
    logical = runtime.graph.invocations[hint.key.invocation_id].state
    controller.dispatch()
    assert allocator.available_size() == initial + 34
    assert checkpoint.component_data[0].value is None
    assert leaf.component_data[0].value is None
    assert leaf.component_data[0].host_value is not None
    assert checkpoint.component_data[2].value is state
    assert runtime.graph.invocations[hint.key.invocation_id].state is logical
    assert runtime.counts["predicted_tool_offload_freed_component_0"] == 34
    assert not runtime.physical_disabled
    controller.next_ms = 0.
    controller.dispatch()
    assert allocator.available_size() == initial + 34
    assert runtime.counts["predicted_tool_offload_freed_component_0"] == 34


def test_existing_tool_prepare_hook_also_offloads_without_a_running_forward(waiting_tail):
    runtime, _, _, allocator, _, _, _, _ = controller_fixture(waiting_tail)
    runtime._tool_prepare_next_ms = float("inf")
    initial = allocator.available_size()
    runtime.dispatch_tool_prepare()
    assert allocator.available_size() == initial + 34


@pytest.mark.parametrize("blocked", (
    "tool_return", "context_epoch", "session_generation", "native_generation",
    "native_closed", "prediction_expired", "prediction_short", "foreign_predictor",
    "no_prediction", "device_lock", "shared", "foreign_owner", "write_pending",
    "load_pending", "host_copy_missing", "prefetch_selected", "physical_disabled",
))
def test_native_offload_rejects_stale_locked_unbacked_and_restoring_state(waiting_tail, blocked):
    runtime, hint, cache, allocator, _, checkpoint, leaf, controller = controller_fixture(waiting_tail)
    if blocked == "tool_return":
        runtime.on_events((event(3, Kind.TOOL_END, invocation_id="tool",
                                 attributes={"tool_run_id": "long"}),))
    elif blocked == "context_epoch":
        runtime.graph.contexts[hint.key.context_id].epoch += 1
    elif blocked == "session_generation":
        runtime.context_sessions[hint.key.context_id] = replace(hint.key, session_generation=2)
    elif blocked == "native_generation":
        cache.session_refs._session_generations[hint.key.session_id] += 1
    elif blocked == "native_closed":
        cache.session_refs._closed_session_ids[hint.key.session_id] = 1
    elif blocked == "prediction_expired":
        runtime.tool_wait_hints[hint.key.context_id] = replace(hint, expires_monotonic_ms=0.)
    elif blocked == "prediction_short":
        runtime.tool_wait_hints[hint.key.context_id] = replace(
            hint, wait_p10_ms=1., wait_p50_ms=2., wait_p90_ms=3.,
        )
    elif blocked == "foreign_predictor":
        runtime.tool_wait_hints[hint.key.context_id] = replace(hint, predictor_sha256="b" * 64)
    elif blocked == "no_prediction":
        runtime.tool_wait_hints.clear()
    elif blocked == "device_lock":
        leaf.component_data[0].lock_ref += 1
    elif blocked == "shared":
        leaf.component_data[0].session_ref = 2
    elif blocked == "foreign_owner":
        leaf.component_data[0].session_ids = {"other"}
    elif blocked == "write_pending":
        leaf.write_through_pending_id = 99
    elif blocked == "load_pending":
        leaf.load_back_pending_id = 99
    elif blocked == "host_copy_missing":
        leaf.component_data[0].host_value = None
        runtime.issue_shadow_backup_steps = Mock(return_value=())
    elif blocked == "prefetch_selected":
        runtime._tool_ticket = NS(key=hint.key)
    else:
        runtime.physical_disabled = True
    initial = allocator.available_size()
    controller.dispatch()
    assert allocator.available_size() == initial
    assert checkpoint.component_data[0].value is not None
    assert leaf.component_data[0].value is not None
    assert not runtime.counts["predicted_tool_offload_freed_component_0"]


def test_tool_return_delivered_at_dispatch_prevents_offload(waiting_tail):
    runtime, _, _, allocator, _, _, _, controller = controller_fixture(waiting_tail)
    runtime.event_server = NS(
        drain=Mock(side_effect=lambda **_: runtime.on_events((event(
            3, Kind.TOOL_END, invocation_id="tool", attributes={"tool_run_id": "long"},
        ),))),
        close=Mock(),
    )
    initial = allocator.available_size()
    controller.dispatch()
    assert allocator.available_size() == initial
    runtime.event_server.drain.assert_called_once_with(max_messages=128)


def test_backup_enqueue_is_not_eviction_and_next_dispatch_requires_settled_copy(waiting_tail):
    runtime, hint, cache, allocator, _, _, leaf = waiting_tail
    controller = PredictedToolOffload(runtime)
    selected = runtime._refreshed_shadow_backup_selection(context_id=hint.key.context_id)
    assert selected is not None
    runtime.issue_shadow_backup_steps = Mock(return_value=((selected[0], "copy"),))
    runtime._prepare_step_rank = Mock(return_value=(-128., 128.))
    initial = allocator.available_size()
    controller.dispatch()
    assert runtime.counts["predicted_tool_offload_backup_issued"] == 1
    assert allocator.available_size() == initial
    leaf.component_data[0].host_value = leaf.component_data[0].value.clone()
    leaf.write_through_pending_id = 11
    controller.next_ms = 0.
    controller.dispatch()
    assert allocator.available_size() == initial
    leaf.write_through_pending_id = None
    controller.next_ms = 0.
    controller.dispatch()
    assert allocator.available_size() == initial + 34


def test_control_is_throttled_and_never_uses_critical_demand_as_a_prediction(waiting_tail):
    runtime, hint, _, _, _, _, _, controller = controller_fixture(waiting_tail)
    short = replace(hint, wait_p10_ms=1., wait_p50_ms=2., wait_p90_ms=3.)
    runtime.tool_wait_hints[hint.key.context_id] = short
    with patch.object(runtime, "_critical_tool_donor", return_value=True):
        assert runtime._long_tool_wait(hint.key)
        assert not controller._eligible(hint.key)
    with patch.object(runtime, "_refreshed_shadow_backup_selection") as inspect:
        controller.dispatch()
        controller.dispatch()
    inspect.assert_not_called()


@pytest.mark.parametrize("scenario", (
    "waiting", "returned_before_ack", "short_at_enqueue", "short_after_event_drain",
))
def test_full_only_native_backup_ack_offload_and_return_race(full_native, scenario):
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, writes, _, commits = full_native
    native_write_start(cache)
    node, generation = insert_session(cache, list(range(1, 33)), "s")
    runtime, hint = tool_runtime()
    hint = replace(
        hint, wait_p10_ms=5000., wait_p50_ms=10_000., wait_p90_ms=20_000.,
    )
    runtime.tool_wait_hints[hint.key.context_id] = hint
    runtime._context_tokens[hint.key.context_id] = (0, 32)
    runtime.attach_native_cache(cache)
    assert generation == hint.key.session_generation
    controller = PredictedToolOffload(runtime)
    runtime._predicted_tool_offload = controller
    cache.cache_controller._num_tokens_by_pool = lambda operation: {
        "kv": len(operation.device_indices),
    }
    cache.cache_controller._transfer_num_bytes = lambda operation: (
        64 * len(operation.device_indices)
    )
    cache.host_pool_group.alloc = Mock(
        side_effect=AssertionError("CPU controller owns its stand-in Host indices"),
    )
    cache.host_pool_group.free = Mock()

    def committed(commit):
        commits.append(commit)
        runtime.on_native_transfer_commit(commit)

    cache.on_hicache_transfer_commit = committed
    if scenario in ("short_at_enqueue", "short_after_event_drain"):
        runtime._critical_tool_donor = lambda _: True

        def shorten():
            runtime.tool_wait_hints[hint.key.context_id] = replace(
                hint, wait_p10_ms=1., wait_p50_ms=2., wait_p90_ms=3.,
            )

        if scenario == "short_at_enqueue":
            native_write = cache.cache_controller.write

            def write(*args, **kwargs):
                shorten()
                return native_write(*args, **kwargs)

            cache.cache_controller.write = write
        else:
            drains = []

            def drain(**kwargs):
                drains.append(True)
                if len(drains) == 2:
                    shorten()
                return ()

            runtime.event_server = NS(drain=drain, close=Mock())
    initial = allocator.available_size()
    try:
        controller.dispatch()
        if scenario.startswith("short_"):
            assert writes == []
            assert not runtime.physical_ledger.pending_count
            assert allocator.available_size() == initial
            assert not runtime.physical_disabled
            return
        assert len(writes) == 1, json.dumps(dict(runtime.counts))
        assert runtime.physical_ledger.pending_action_count("PREPARE_HOST") == 1
        assert node.component_data[0].value is not None
        assert allocator.available_size() == initial
        controller.next_ms = 0.
        controller.dispatch()
        assert len(writes) == 1
        assert allocator.available_size() == initial
        return_before_ack = scenario == "returned_before_ack"
        if return_before_ack:
            runtime.on_events((event(
                3, Kind.TOOL_END, invocation_id="tool", attributes={"tool_run_id": "long"},
            ),))
        cache.writing_check(finish_count=1)
        assert not runtime.physical_ledger.pending_count
        assert not runtime.physical_disabled
        assert len(commits) == 1 and commits[0].direction == "d2h"
        assert node.component_data[0].host_value is not None
        controller.dispatch()
        assert allocator.available_size() == initial + (0 if return_before_ack else 32)
        assert (node.component_data[0].value is None) is not return_before_ack
    finally:
        runtime.close()


def test_bounded_node_scan_progresses_through_a_long_segmented_prefix(full_native):
    from array import array
    from sglang.srt.mem_cache.base_prefix_cache import MatchPrefixParams
    from sglang.srt.mem_cache.radix_cache import RadixKey
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, _, _, _ = full_native
    leaf, _ = insert_session(cache, list(range(1, 34)), "s")
    for length in range(1, 33):
        cache.match_prefix(MatchPrefixParams(key=RadixKey(array("q", range(1, length + 1)))))
    nodes, node = [], leaf
    while node is not cache.tree_core.root_node:
        nodes.append(node)
        node = node.parent
    assert len(nodes) == 33
    for node in nodes:
        node.component_data[0].host_value = node.component_data[0].value.clone()
        cache.tree_core._update_duplicate_tracking(node)
    runtime, hint = tool_runtime()
    runtime.tool_wait_hints[hint.key.context_id] = replace(
        hint, wait_p10_ms=5000., wait_p50_ms=10_000., wait_p90_ms=20_000.,
    )
    runtime._context_tokens[hint.key.context_id] = (0, 33)
    runtime.attach_native_cache(cache)
    controller = PredictedToolOffload(runtime)
    initial = allocator.available_size()
    try:
        for _ in range(10):
            controller.next_ms = 0.
            controller.dispatch()
        assert allocator.available_size() == initial + 33, (
            dict(runtime.counts), controller.node_cursor,
            controller._eligible(hint.key),
            [(node.id, node.component_data[0].value is not None) for node in nodes],
            len(runtime._parent_pressure_candidates),
            len(runtime._pressure_candidate_groups().get(hint.key, ())),
        )
        assert all(node.component_data[0].value is None for node in nodes)
    finally:
        runtime.close()
