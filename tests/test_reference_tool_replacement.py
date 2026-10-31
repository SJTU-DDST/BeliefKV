from array import array
from copy import copy
from dataclasses import replace
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from tests.test_full_only_native_lifecycle import full_native
from tests.test_global_workflow_oracle import queued
from tests.test_reference_residency_pause import install
from tests.test_tool_policy_adaptation import demand_runtime
from tests.test_tool_predictive_transfers import event


def pair(parts):
    runtime, admission, controller = install(parts)
    runtime._predicted_tool_offload = controller
    runtime.tool_offload_before_admission = True
    admission.policy.capacity_tokens = 48
    admission.policy._pause(admission.policy.programs["consumer"], time.monotonic())
    admission._remember_dispatch(admission.latest["consumer"])
    admission._next_dispatch = float("inf")
    controller.rearm()
    adder = NS(
        max_running_requests=4, can_run_list=[], rem_input_tokens=8,
        rem_chunk_tokens=8, page_size=1,
    )
    return runtime, admission, controller, adder


def run(parts, *, through_plan=False):
    runtime, admission, controller, adder = parts
    requests = list(runtime._capacity_waiting_queue)
    if through_plan:
        plan = runtime.plan_native_prefill(requests, running_batch=NS(reqs=[]), adder=adder)
        return select_native_prefill_candidates(
            requests, plan=plan, current_semantic_revision=runtime.semantic_revision,
        ).candidates
    return controller.before_agent_admission(requests, running_batch=NS(reqs=[]), adder=adder)


@pytest.mark.parametrize("through_plan", (False, True))
def test_long_tool_replacement_grants_actual_ready_consumer_without_a_timer(
    demand_runtime, through_plan,
):
    parts = pair(demand_runtime)
    runtime, admission, controller, _adder = parts
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    donor, request = demand_runtime[4:6]
    assert not admission.allows(runtime.visible[request.rid])
    assert run(parts, through_plan=through_plan)
    assert donor.component_data[0].value is None
    assert not admission.policy.programs["tool"].active
    assert admission.allows(runtime.visible[request.rid])
    assert admission.policy.programs["hot"].active
    assert admission.counts["tool_event_replacement_grants"] == 1
    assert not admission.counts["control_ticks"]
    assert controller.action_detail["logical_handover_budget_ms"] == 0.
    decision = next(row for row in records if row["event"] == "agent_predicted_tool_replacement")
    assert decision["consumer"]["request_id"] == request.rid
    assert decision["coalescing_window_bypassed"]
    assert not decision["ordinary_restore_lease"]
    assert not runtime.enable_tool_prefetch and not runtime.enable_frontier_restore
    assert not runtime.physical_ledger.pending_count


def test_competing_paused_consumers_are_ranked_before_selecting_the_donor(
    demand_runtime, monkeypatch,
):
    from test_beliefkv_terminal_capacity import insert_session

    parts = pair(demand_runtime)
    runtime, admission, controller, _adder = parts
    cache = demand_runtime[2]
    other_node, generation = insert_session(cache, [301, 302], "other-session")
    other_node.component_data[0].host_value = other_node.component_data[0].value.clone()
    cache.tree_core._update_duplicate_tracking(other_node)
    cache._demote(other_node.id, {})
    runtime.on_events((
        replace(event(20, Kind.WORKFLOW_START), workflow_id="short-work"),
        replace(event(
            21, Kind.INVOCATION_CREATE, invocation_id="short", context_id="ctx-short",
        ), workflow_id="short-work"),
        replace(event(
            22, Kind.LLM_SUBMIT, invocation_id="short", context_id="ctx-short",
            attributes={"request_id": "short"},
        ), workflow_id="short-work"),
    ))
    other = copy(demand_runtime[5])
    other.rid = "short"
    other.beliefkv_metadata = {
        "root_workflow_id": "short-work", "invocation_id": "short",
        "context_id": "ctx-short", "context_epoch": 0,
    }
    other.origin_input_ids = array("q", (301, 302, 303))
    other.session_id, other.session_generation = "other-session", generation
    runtime.register_visible_request(other)
    # Both continuations fit separately in A's logical capacity, not together.
    admission.policy.programs["consumer"].tokens = 30
    admission.policy.programs["short"].tokens = 30
    admission.policy.programs["short"].active = False
    admission._remember_dispatch(admission.latest["short"])
    runtime._capacity_waiting_queue.append(other)
    compared = []

    def order(keys):
        compared.extend(key.invocation_id for key in keys)
        return sorted(keys, key=lambda key: key.invocation_id != "short")

    monkeypatch.setattr(admission, "order_tool_replacement", order)
    assert run(parts)
    assert set(compared) == {"consumer", "short"}
    assert admission.policy.programs["short"].active
    assert not admission.policy.programs["consumer"].active
    assert not admission.policy.programs["tool"].active


@pytest.mark.parametrize("guard", (
    "no_consumer", "not_ready", "oversized", "allocated", "running", "returned",
    "no_rows", "no_input", "no_batch_slot", "active_peer", "shared", "locked",
    "unsupported_cache",
))
def test_no_donor_release_without_a_safe_useful_complete_pair(demand_runtime, guard):
    parts = pair(demand_runtime)
    runtime, admission, controller, adder = parts
    cache, donor, request = demand_runtime[2], demand_runtime[4], demand_runtime[5]
    if guard == "no_consumer":
        runtime._capacity_waiting_queue.clear()
    elif guard == "not_ready":
        runtime.graph.invocations["consumer"].active_tool_calls["another"] = "execute"
    elif guard == "oversized":
        admission.policy.programs["consumer"].tokens = 100
    elif guard == "allocated":
        request.kv.req_pool_idx = 0
    elif guard == "running":
        request.output_ids.append(1)
    elif guard == "returned":
        runtime.on_events((event(
            10, Kind.TOOL_END, invocation_id="tool", attributes={"tool_run_id": "long"},
        ),))
    elif guard == "no_rows":
        cache.req_to_token_pool.available_size = lambda: 0
    elif guard == "no_input":
        adder.rem_input_tokens = 0
    elif guard == "no_batch_slot":
        adder.max_running_requests = 0
    elif guard == "active_peer":
        runtime.graph.invocations["tool"].active_tool_calls.clear()
    elif guard == "shared":
        donor.component_data[0].session_ids.add("another-session")
    elif guard == "unsupported_cache":
        cache.is_write_back = False
    else:
        donor.component_data[0].lock_ref = 1
    assert not run(parts)
    assert donor.component_data[0].value is not None
    assert admission.policy.programs["tool"].active
    assert not admission.counts["tool_event_replacement_grants"]


@pytest.mark.parametrize("race", (None, "tool_return", "consumer_disappears"))
def test_backup_ack_retries_immediately_and_revalidates_both_sides(demand_runtime, race):
    from tests.test_prepare_ack_allocation import native_write_start

    parts = pair(demand_runtime)
    runtime, admission, controller, _adder = parts
    cache, allocator, donor = demand_runtime[2], demand_runtime[3], demand_runtime[4]
    native_write_start(cache)
    donor.component_data[0].host_value = None
    cache.tree_core._update_duplicate_tracking(donor)
    cache.cache_controller._num_tokens_by_pool = lambda op: {"kv": len(op.device_indices)}
    cache.cache_controller._transfer_num_bytes = lambda op: 64 * len(op.device_indices)
    cache.host_pool_group.alloc = Mock(side_effect=AssertionError("stand-in owns Host indices"))
    cache.host_pool_group.free = Mock()
    cache.on_hicache_transfer_commit = runtime.on_native_transfer_commit
    assert not run(parts)
    assert runtime.physical_ledger.pending_action_count("PREPARE_HOST") == 1
    assert admission.policy.programs["tool"].active
    assert not admission.policy.programs["consumer"].active
    assert allocator.available_size() == 4
    assert not controller.replacement.consumer
    if race == "tool_return":
        runtime.on_events((event(
            10, Kind.TOOL_END, invocation_id="tool", attributes={"tool_run_id": "long"},
        ),))
    elif race == "consumer_disappears":
        runtime._capacity_waiting_queue.clear()
    cache.writing_check(finish_count=1)
    assert not runtime.physical_ledger.pending_count
    assert controller.replacement.next_ms == 0.
    assert run(parts) is (race is None)
    assert allocator.available_size() == (36 if race is None else 4)
    assert admission.policy.programs["tool"].active is (race is not None)
    assert admission.policy.programs["consumer"].active is (race is None)


def test_first_prefill_can_use_a_tool_donors_capacity(demand_runtime):
    parts = pair(demand_runtime)
    runtime, admission, controller, _adder = parts
    request = demand_runtime[5]
    request.origin_input_ids = array("q", range(401, 418))
    observed = runtime._read_request_reentry(request, refresh=True)
    assert observed is None
    assert run(parts)
    assert admission.policy.programs["consumer"].active
    assert controller.action_detail["beneficiary_restore_bytes_budgeted"] == (
        len(request.origin_input_ids) * runtime._residency_pool_bytes[0]
    )


def test_reference_maintenance_cannot_consume_an_event_replacement_attempt(demand_runtime):
    parts = pair(demand_runtime)
    _runtime, admission, controller, _adder = parts
    controller.dispatch()
    assert controller.next_ms > time.monotonic() * 1000.
    assert run(parts)
    assert admission.counts["tool_event_replacement_grants"] == 1


def test_selected_continuation_is_not_paused_again_before_native_restore_service(demand_runtime):
    parts = pair(demand_runtime)
    _runtime, admission, _controller, _adder = parts
    request = demand_runtime[5]
    admission.policy.programs["consumer"].steps = 2
    assert run(parts)
    admission._residency_pause.next_check = 0.
    admission.synchronize_residency([request])
    assert admission.policy.programs["consumer"].active
    assert admission.counts["residency_pauses"] == 1
    assert admission.latest["consumer"] in admission._residency_pause.compared_requests


def test_fragmented_private_backed_prefix_is_released_in_native_leaf_order(demand_runtime):
    from sglang.srt.mem_cache.base_prefix_cache import MatchPrefixParams
    from sglang.srt.mem_cache.radix_cache import RadixKey

    parts = pair(demand_runtime)
    runtime, admission, _controller, _adder = parts
    cache = demand_runtime[2]
    cache.match_prefix(MatchPrefixParams(key=RadixKey(array("q", range(1, 17)))))
    runtime._register_backed_pressure_nodes(admission.latest["tool"])
    nodes = runtime._pressure_candidate_groups()[admission.latest["tool"]]
    assert len(nodes) == 2
    assert run(parts)
    assert admission.policy.programs["consumer"].active
    assert runtime.counts["predicted_tool_offload_freed_component_0"] == 32


def test_bounded_private_plan_rotates_past_ancestors_without_a_current_leaf(
    demand_runtime, monkeypatch,
):
    from sglang.srt.mem_cache.base_prefix_cache import MatchPrefixParams
    from sglang.srt.mem_cache.radix_cache import RadixKey

    parts = pair(demand_runtime)
    runtime, admission, controller, _adder = parts
    cache, leaf = demand_runtime[2], demand_runtime[4]
    for length in range(1, 32):
        cache.match_prefix(MatchPrefixParams(key=RadixKey(array("q", range(1, length + 1)))))
    runtime._register_backed_pressure_nodes(admission.latest["tool"])
    nodes, current = [], leaf
    while current is not cache.tree_core.root_node:
        nodes.append(current.id)
        current = current.parent
    assert len(nodes) == 32
    key = admission.latest["tool"]
    monkeypatch.setattr(runtime, "_pressure_candidate_groups", lambda: {key: tuple(reversed(nodes))})
    controller.node_cursor = 0
    assert controller.replacement._release_plan(key) == ((), 0)
    order, tokens = controller.replacement._release_plan(key)
    assert order[0] == leaf.id and tokens == 16
    assert len(order) <= controller.max_nodes


def test_two_second_oracle_window_does_not_filter_event_replacement_ranking(monkeypatch, tmp_path):
    runtime, admission = queued(
        monkeypatch, tmp_path, global_order=True, activate_first=False, window_ms=2000,
    )
    try:
        keys = [admission.latest[name] for name in ("long", "short")]
        assert admission._order_dispatch_keys(keys, {}) == []
        assert admission.order_tool_replacement(keys) == keys[::-1]
        assert not any(item.active for item in admission.policy.programs.values())
        assert admission.status()["workflow_oracle_coalescing_window_ms"] == 2000
    finally:
        runtime.close()


def test_new_tool_return_event_rearms_without_advancing_the_control_clock(demand_runtime):
    parts = pair(demand_runtime)
    runtime, admission, controller, _adder = parts
    controller.next_ms = controller.replacement.next_ms = float("inf")
    old_tick = admission.next_tick
    runtime.on_events((event(
        10, Kind.TOOL_END, invocation_id="tool", attributes={"tool_run_id": "long"},
    ),))
    assert controller.next_ms == controller.replacement.next_ms == 0.
    assert admission.next_tick == old_tick


def test_retired_request_in_an_earlier_candidate_snapshot_cannot_crash_replacement(demand_runtime):
    parts = pair(demand_runtime)
    _runtime, admission, controller, _adder = parts
    controller.replacement.order = (admission.latest["consumer"],)
    admission.forget("consumer")
    assert not controller.replacement._replace()
    assert demand_runtime[4].component_data[0].value is not None
    assert admission.policy.programs["tool"].active
