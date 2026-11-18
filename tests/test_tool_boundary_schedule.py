from array import array
from copy import copy
from dataclasses import replace
import json
import time
from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.runtime.tool_boundary_preemption import ToolBoundaryPreemption
from beliefkv.runtime.workflow_work_oracle import WorkflowWorkOracle
from scripts.prepare_tool_boundary_preemption import BOUNDED_POLICY
from tests.test_full_only_native_lifecycle import full_native
from tests.test_reference_tool_replacement import run
from tests.test_tool_boundary_preemption import enabled
from tests.test_tool_policy_adaptation import demand_runtime
from tests.test_tool_predictive_transfers import event
from tests.test_workflow_work_oracle import add_serial_graphs, oracle_file


def bounded(parts, tmp_path):
    parts, old, request = enabled(parts, tmp_path)
    runtime, admission, controller, _adder = parts
    raw = dict(admission._work.artifact.raw, tool_boundary_preemption=dict(BOUNDED_POLICY))
    boundary = admission._tool_boundary_preemption = ToolBoundaryPreemption(admission, raw)
    boundary.events((event(30, Kind.TOOL_START, invocation_id="tool"),))
    controller.rearm()
    return parts, boundary, request


def test_bounded_native_handover_releases_only_for_the_selected_short_consumer(demand_runtime, tmp_path):
    parts, boundary, request = bounded(demand_runtime, tmp_path)
    runtime, admission, controller, _adder = parts
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    assert run(parts)
    assert admission.policy.programs["short"].active
    assert not admission.policy.programs["tool"].active
    assert boundary.preempted["tool"]["consumer"] == runtime.visible[request.rid]
    assert admission.counts["tool_boundary_plan_donors_granted"] == 1
    assert boundary.active_plan is None
    marker = next(row for row in records if row["event"] == "agent_predicted_tool_replacement")
    assert marker["tool_boundary_donors"] == [marker["donor"]]
    assert boundary.migration_allowance() < int(admission.policy.capacity_tokens * 4)


@pytest.mark.parametrize("guard", ("ratio", "rate", "interval", "cooldown", "protected"))
def test_bounded_guards_prevent_an_additional_release(demand_runtime, tmp_path, guard):
    parts, boundary, _request = bounded(demand_runtime, tmp_path)
    _runtime, admission, _controller, _adder = parts
    now = time.monotonic() * 1000.
    key = admission.latest["tool"]
    if guard == "ratio":
        boundary.config["min_work_ratio"] = 1000.
    elif guard == "rate":
        boundary.migration_history.append((now, 1_000_000))
    elif guard == "interval":
        boundary.last_switch_ms = now
    elif guard == "cooldown":
        boundary.cooldowns[key.root_workflow_id] = now + 10_000.
    else:
        boundary.protected[key.invocation_id] = key
    assert not run(parts)
    assert admission.policy.programs["tool"].active
    assert not admission.policy.programs["short"].active
    assert demand_runtime[4].component_data[0].value is not None


def test_new_ready_identity_rearms_once_but_ordinary_registration_does_not_poll(demand_runtime, tmp_path):
    parts, boundary, request = bounded(demand_runtime, tmp_path)
    runtime, admission, controller, _adder = parts
    key = runtime.visible[request.rid]
    boundary.ready_since.clear()
    controller.replacement.next_ms = float("inf")
    boundary.registered(key)
    assert controller.replacement.next_ms == 0.
    controller.replacement.next_ms = float("inf")
    boundary.registered(key)
    assert controller.replacement.next_ms == float("inf")


def test_cheap_work_filter_precedes_native_consumer_prefix_reads(demand_runtime, tmp_path, monkeypatch):
    parts, boundary, _request = bounded(demand_runtime, tmp_path)
    _runtime, _admission, controller, _adder = parts
    boundary.config["min_work_ratio"] = 1000.
    monkeypatch.setattr(
        controller.replacement, "demand",
        lambda *_args, **_kwargs: pytest.fail("read a prefix without an eligible work comparison"),
    )
    assert not run(parts)


def test_migration_window_expiration_and_long_workflow_cooldown_are_separate(
    demand_runtime, tmp_path, monkeypatch,
):
    parts, boundary, _request = bounded(demand_runtime, tmp_path)
    _runtime, admission, _controller, _adder = parts
    now = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: now)
    boundary.migration_history.extend(((now * 1000. - 60_001., 10), (now * 1000., 20)))
    assert boundary.migration_allowance() == int(admission.policy.capacity_tokens * 4) - 20
    assert len(boundary.migration_history) == 1


@pytest.mark.parametrize("race", (None, "tool_return", "consumer_disappears"))
def test_new_unbacked_donor_still_waits_for_ack_and_rechecks_pair(
    demand_runtime, tmp_path, race,
):
    from unittest.mock import Mock
    from tests.test_prepare_ack_allocation import native_write_start

    parts, boundary, _request = bounded(demand_runtime, tmp_path)
    runtime, admission, controller, _adder = parts
    cache, donor = demand_runtime[2], demand_runtime[4]
    native_write_start(cache)
    donor.component_data[0].host_value = None
    cache.tree_core._update_duplicate_tracking(donor)
    cache._remember_beliefkv_unbacked_full(donor)
    cache.cache_controller._num_tokens_by_pool = lambda op: {"kv": len(op.device_indices)}
    cache.cache_controller._transfer_num_bytes = lambda op: 64 * len(op.device_indices)
    cache.host_pool_group.alloc = Mock(side_effect=AssertionError("stand-in owns Host indices"))
    cache.host_pool_group.free = Mock()
    cache.on_hicache_transfer_commit = runtime.on_native_transfer_commit
    assert not run(parts)
    assert runtime.physical_ledger.pending_action_count("PREPARE_HOST") == 1
    assert donor.component_data[0].value is not None
    assert not admission.allows(admission.latest["tool"])
    if race == "tool_return":
        runtime.graph.invocations["tool"].active_tool_calls.clear()
    elif race == "consumer_disappears":
        runtime._capacity_waiting_queue.clear()
    cache.writing_check(finish_count=1)
    assert not runtime.physical_ledger.pending_count
    assert run(parts) is (race is None)
    assert admission.policy.programs["short"].active is (race is None)


def two_donors(parts, tmp_path):
    parts, _old, consumer = bounded(parts, tmp_path)
    runtime, admission, controller, _adder = parts
    node = next(
        node for node in runtime._native_cache.root_node.children.values()
        if node.component_data[0].session_ids == {"hot"}
    )
    generation = runtime._native_cache.session_refs._session_generations["hot"]
    node.component_data[0].host_value = node.component_data[0].value.clone()
    runtime._native_cache.tree_core._update_duplicate_tracking(node)
    runtime.on_events((
        replace(event(40, Kind.WORKFLOW_START), workflow_id="other-long"),
        replace(event(41, Kind.INVOCATION_CREATE, invocation_id="hot", context_id="ctx-hot"),
                workflow_id="other-long"),
        replace(event(42, Kind.LLM_SUBMIT, invocation_id="hot", context_id="ctx-hot",
                      attributes={"request_id": "hot"}), workflow_id="other-long"),
    ))
    request = copy(consumer)
    request.rid = "hot"
    request.origin_input_ids = array("q", range(201, 214))
    request.session_id, request.session_generation = "hot", generation
    request.beliefkv_metadata = {
        "root_workflow_id": "other-long", "invocation_id": "hot",
        "context_id": "ctx-hot", "context_epoch": 0,
    }
    runtime._context_tokens["ctx-hot"] = 0, 12
    admission.policy.completed("hot", 12, now=time.monotonic())
    runtime.register_visible_request(request)
    runtime.on_events((
        replace(event(43, Kind.LLM_RESULT, invocation_id="hot", context_id="ctx-hot",
                      attributes={"request_id": "hot"}), workflow_id="other-long"),
        replace(event(44, Kind.TOOL_START, invocation_id="hot", context_id="ctx-hot",
                      attributes={"tool_run_id": "other-tool", "tool_name": "read_file"}),
                workflow_id="other-long"),
    ))
    admission.policy.completed("hot", 12, now=time.monotonic())
    raw = dict(admission._work.artifact.raw)
    raw["tool_boundary_preemption"] = dict(BOUNDED_POLICY)
    raw["requests"] = {
        **raw["requests"], "future-other": {
            "workflow_id": "other-long", "invocation_id": "hot", "context_id": "ctx-hot",
            "prompt_tokens": 1, "input_increment": 1, "output_tokens": 2000,
            "predecessor_request_id": None,
        },
    }
    raw["workflows"] = {
        **raw["workflows"], "other-long": {"total_work_units": 200001, "requests": 1},
    }
    add_serial_graphs(raw)
    admission._work = WorkflowWorkOracle(*oracle_file(tmp_path, raw))
    boundary = admission._tool_boundary_preemption = ToolBoundaryPreemption(admission, raw)
    assert boundary.bounded
    boundary.events((
        event(45, Kind.TOOL_START, invocation_id="tool"),
        replace(event(46, Kind.TOOL_START, invocation_id="hot"), workflow_id="other-long"),
    ))
    runtime._register_backed_pressure_nodes(admission.latest["hot"])
    runtime._remember_parent_pressure_node(node.id, admission.latest["hot"], node.creation_time)
    admission.policy.programs["short"].tokens = 43
    admission.policy.programs["short"].active = False
    admission._remember_dispatch(admission.latest["short"])
    admission.policy.programs["hot"].active = True
    assert {key.invocation_id for key in boundary.donor_keys()} == {"tool", "hot"}
    controller.rearm()
    return parts, boundary, node


def test_two_real_private_donors_can_jointly_admit_a_consumer_that_neither_can_fit(
    demand_runtime, tmp_path,
):
    parts, boundary, node = two_donors(demand_runtime, tmp_path)
    runtime, admission, controller, adder = parts
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    changed = run(parts)
    assert changed, json.dumps({
        "admission": dict(admission.counts), "runtime": dict(runtime.counts),
        "programs": {key: vars(value) for key, value in admission.policy.programs.items()},
        "pending": list(admission._dispatch_pending),
        "records": records, "preempted": boundary.preempted,
        "graph": {key: repr(value.state) for key, value in runtime.graph.invocations.items()},
    }, default=str)
    assert admission.policy.programs["short"].active
    assert not admission.policy.programs["tool"].active
    assert not admission.policy.programs["hot"].active
    assert node.component_data[0].value is None
    assert admission.counts["tool_boundary_plan_donors_granted"] == 2
    assert all(debt["grant_complete"] for debt in boundary.preempted.values())
    marker = next(row for row in records if row["event"] == "agent_predicted_tool_replacement")
    assert {key["invocation_id"] for key in marker["tool_boundary_donors"]} == {"tool", "hot"}


@pytest.mark.parametrize("unsafe", ("locked", "unbacked", "returned"))
def test_two_owner_preflight_does_not_release_first_owner_for_an_incomplete_group(
    demand_runtime, tmp_path, unsafe,
):
    parts, boundary, node = two_donors(demand_runtime, tmp_path)
    runtime, admission, _controller, _adder = parts
    if unsafe == "locked":
        node.component_data[0].lock_ref = 1
    elif unsafe == "unbacked":
        node.component_data[0].host_value = None
        runtime._native_cache.tree_core._update_duplicate_tracking(node)
    else:
        runtime.graph.invocations["hot"].active_tool_calls.clear()
    assert not run(parts)
    assert demand_runtime[4].component_data[0].value is not None
    assert node.component_data[0].value is not None
    assert admission.policy.programs["tool"].active
    assert admission.policy.programs["hot"].active
    assert not admission.policy.programs["short"].active
    assert not boundary.preempted


def test_partial_group_release_is_charged_and_can_finish_only_for_the_same_consumer(
    demand_runtime, tmp_path, monkeypatch,
):
    parts, boundary, node = two_donors(demand_runtime, tmp_path)
    runtime, admission, controller, _adder = parts
    original = controller._evict_backed
    monkeypatch.setattr(
        controller, "_evict_backed",
        lambda key, **kwargs: False if key.invocation_id == "tool" else original(key, **kwargs),
    )
    assert not run(parts)
    assert node.component_data[0].value is None
    assert not admission.policy.programs["short"].active
    assert boundary.preempted["hot"]["consumer"] == admission.latest["short"]
    assert not boundary.preempted["hot"]["grant_complete"]
    assert boundary.migration_allowance() == 4 * admission.policy.capacity_tokens - 24
    monkeypatch.setattr(controller, "_evict_backed", original)
    controller.rearm()
    assert run(parts)
    assert admission.policy.programs["short"].active
    assert all(debt["grant_complete"] for debt in boundary.preempted.values())


def returned_successor(parts, tmp_path):
    parts, boundary, consumer = bounded(parts, tmp_path)
    runtime, admission, controller, _adder = parts
    previous = admission.latest["tool"]
    returned = (event(
        50, Kind.TOOL_END, invocation_id="tool", attributes={"tool_run_id": "long"},
    ),)
    runtime.on_events(returned)
    boundary.events(returned)
    following = copy(consumer)
    following.rid = "tool-next"
    following.origin_input_ids = array("q", range(1, 34))
    following.session_id, following.session_generation = previous.session_id, previous.session_generation
    following.beliefkv_metadata = {
        "root_workflow_id": previous.root_workflow_id, "invocation_id": "tool",
        "context_id": previous.context_id, "context_epoch": previous.context_epoch + 1,
    }
    runtime.on_events((event(
        51, Kind.LLM_SUBMIT, invocation_id="tool", context_id=previous.context_id,
        context_epoch=previous.context_epoch + 1, attributes={"request_id": following.rid},
    ),))
    runtime.register_visible_request(following)
    runtime._capacity_waiting_queue = [consumer, following]
    controller.rearm()
    return parts, boundary, following, previous


def test_tool_return_does_not_erase_the_unstarted_successor_preemption_boundary(demand_runtime, tmp_path):
    parts, boundary, request, previous = returned_successor(demand_runtime, tmp_path)
    runtime, admission, _controller, _adder = parts
    key = runtime.visible[request.rid]
    assert key.context_epoch == previous.context_epoch + 1
    assert boundary.returned_candidate(key) and boundary.donor_candidate(key), {
        "boundary": boundary.boundaries.get("tool"),
        "candidate": admission._candidate(key.request_id, key, include_first=True),
        "unallocated": runtime._unallocated_waiting_full_request(key, request),
        "state": runtime.graph.invocations["tool"].state,
        "request": vars(request),
    }
    assert admission.policy.programs["tool"].pending and admission.policy.programs["tool"].active
    changed = run(parts)
    assert changed, json.dumps({
        "admission": dict(admission.counts), "runtime": dict(runtime.counts),
        "programs": {key: vars(value) for key, value in admission.policy.programs.items()},
        "pending": list(admission._dispatch_pending),
        "context_tokens": runtime._context_tokens,
        "groups": [(str(key), list(nodes)) for key, nodes in runtime._pressure_candidate_groups().items()],
    }, default=str)
    assert admission.policy.programs["short"].active
    assert not admission.policy.programs["tool"].active
    assert admission.policy.programs["tool"].pending
    assert "tool" in admission._dispatch_pending
    assert demand_runtime[4].component_data[0].value is None


@pytest.mark.parametrize("reason", (
    "allocated", "output", "native_running", "expired", "served", "long_wait", "unsatisfied_join",
))
def test_returned_boundary_never_preempts_started_or_protected_successors(
    demand_runtime, tmp_path, reason,
):
    parts, boundary, request, previous = returned_successor(demand_runtime, tmp_path)
    runtime, admission, controller, adder = parts
    if reason == "allocated":
        request.kv.req_pool_idx = 0
    elif reason == "output":
        request.output_ids = array("q", (1,))
    elif reason == "native_running":
        runtime._native_running_batch = NS(reqs=[request])
    elif reason == "expired":
        boundary.boundaries["tool"]["returned_ms"] -= 3000.
    elif reason == "served":
        boundary.serviced(NS(reqs=[request]))
    elif reason == "unsatisfied_join":
        runtime.graph.invocations["tool"].join_id = "still-waiting"
        runtime.graph.joins["still-waiting"] = NS(satisfied=False)
    else:
        boundary.protected["tool"] = previous
    changed = (
        controller.before_agent_admission(
            runtime._capacity_waiting_queue, running_batch=runtime._native_running_batch, adder=adder,
        ) if reason == "native_running" else run(parts)
    )
    assert not changed
    assert demand_runtime[4].component_data[0].value is not None
    assert admission.policy.programs["tool"].active


@pytest.mark.parametrize("race", (None, "allocated_before_ack", "tool_boundary_expires"))
def test_returned_unstarted_successor_needs_real_backup_ack_and_current_authority(
    demand_runtime, tmp_path, race,
):
    from unittest.mock import Mock
    from tests.test_prepare_ack_allocation import native_write_start

    parts, boundary, following, _previous = returned_successor(demand_runtime, tmp_path)
    runtime, admission, controller, _adder = parts
    cache, donor = demand_runtime[2], demand_runtime[4]
    native_write_start(cache)
    donor.component_data[0].host_value = None
    cache.tree_core._update_duplicate_tracking(donor)
    cache._remember_beliefkv_unbacked_full(donor)
    cache.cache_controller._num_tokens_by_pool = lambda op: {"kv": len(op.device_indices)}
    cache.cache_controller._transfer_num_bytes = lambda op: 64 * len(op.device_indices)
    cache.host_pool_group.alloc = Mock(side_effect=AssertionError("stand-in owns Host indices"))
    cache.host_pool_group.free = Mock()
    cache.on_hicache_transfer_commit = runtime.on_native_transfer_commit
    assert not run(parts)
    assert runtime.physical_ledger.pending_action_count("PREPARE_HOST") == 1
    assert donor.component_data[0].value is not None
    assert not admission.allows(admission.latest["tool"])
    if race == "allocated_before_ack":
        following.kv.req_pool_idx = 0
    elif race == "tool_boundary_expires":
        boundary.boundaries["tool"]["returned_ms"] -= 3000.
    cache.writing_check(finish_count=1)
    assert not runtime.physical_ledger.pending_count
    assert run(parts) is (race is None)
    assert admission.policy.programs["short"].active is (race is None)


def test_previous_epoch_node_evidence_survives_pruning_of_the_old_logical_index(
    demand_runtime, tmp_path,
):
    parts, boundary, request, _previous = returned_successor(demand_runtime, tmp_path)
    runtime, admission, _controller, _adder = parts
    assert boundary.boundaries["tool"]["nodes"]
    runtime._parent_pressure_candidates.clear()
    assert run(parts)
    assert not admission.policy.programs["tool"].active
    assert admission.policy.programs["short"].active
    assert admission.counts["tool_boundary_previous_epoch_nodes_revalidated"] > 0


def test_late_tool_events_bind_the_last_serviced_key_after_latest_has_advanced(demand_runtime, tmp_path):
    parts, boundary, request, previous = returned_successor(demand_runtime, tmp_path)
    runtime, admission, _controller, _adder = parts
    boundary.last_serviced["tool"] = previous
    boundary.boundaries.clear()
    boundary.events((
        event(52, Kind.TOOL_START, invocation_id="tool"),
        event(53, Kind.TOOL_END, invocation_id="tool"),
    ))
    assert boundary.boundaries["tool"]["key"] == previous
    assert admission.latest["tool"] == runtime.visible[request.rid] != previous
    assert run(parts)


@pytest.mark.parametrize("stale", ("session", "creation"))
def test_previous_epoch_nodes_are_not_reused_after_native_identity_changes(
    demand_runtime, tmp_path, stale,
):
    parts, boundary, request, _previous = returned_successor(demand_runtime, tmp_path)
    runtime, admission, _controller, _adder = parts
    if stale == "session":
        runtime._native_cache.session_refs._session_generations[request.session_id] += 1
    else:
        boundary.boundaries["tool"]["nodes"] = tuple(
            (node, stamp + 1) for node, stamp in boundary.boundaries["tool"]["nodes"]
        )
    assert not run(parts)
    assert demand_runtime[4].component_data[0].value is not None
    assert admission.policy.programs["tool"].active


def test_complete_fragmented_plan_progresses_in_bounded_release_chunks(demand_runtime, tmp_path):
    from sglang.srt.mem_cache.base_prefix_cache import MatchPrefixParams
    from sglang.srt.mem_cache.radix_cache import RadixKey

    parts, boundary, request = bounded(demand_runtime, tmp_path)
    runtime, admission, controller, adder = parts
    cache = runtime._native_cache
    for end in range(1, 33):
        cache.match_prefix(MatchPrefixParams(key=RadixKey(array("q", range(1, end + 1)))))
    pending = list(cache.root_node.children.values())
    while pending:
        node = pending.pop()
        pending.extend(node.children.values())
        if node.component_data[0].session_ids == {"s"}:
            runtime._remember_parent_pressure_node(node.id, admission.latest["tool"], node.creation_time)
    request.origin_input_ids = array("q", range(301, 325))
    adder.rem_input_tokens = adder.rem_chunk_tokens = 32
    reference = controller.replacement
    reference.consumer = request, runtime.visible[request.rid]
    before = reference.demand(request, runtime.visible[request.rid], refresh=True)
    reference.consumer = None
    assert before is not None and 16 < before[0] <= 32
    assert not run(parts)
    assert boundary.preempted["tool"]["grant_complete"] is False
    assert not admission.policy.programs["short"].active
    assert controller.replacement.next_ms == 0.
    assert run(parts)
    assert admission.policy.programs["short"].active
    assert boundary.preempted["tool"]["grant_complete"]
