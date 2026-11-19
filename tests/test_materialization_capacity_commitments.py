"""New child state cannot spend capacity committed to an existing native input."""

from array import array
from copy import copy
from dataclasses import replace
from types import SimpleNamespace as NS
import time

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.runtime.demand_prefill_pipeline import DemandPrefillPipeline
from beliefkv.runtime.ready_service_guard import ReadyServiceGuard
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from beliefkv.runtime.subagent_materialization import (
    SubagentMaterialization,
    observe_materialization_headroom,
)
from tests.test_demand_prefill_pipeline import fixture as pipeline_fixture
from tests.test_demand_prefill_pipeline_native import staged_native
from tests.test_demand_prefill_pipeline_scheduler import BatchConstructionReached, native_scheduler
from tests.test_full_only_native_lifecycle import full_native
from tests.test_internal_comparison import observed
from tests.test_paper_baseline_runtime import request
from tests.test_subagent_materialization import cache, child, new_root, node, prepare, runtime


def parent_request(
    live, *, allocated=100, tokens=600, identity="parent-request", session_id=None,
):
    req = request(identity, "parent", tokens=tokens, workflow="wf")
    if session_id is not None:
        req.session_id, req.session_generation = session_id, 1
    req.output_ids.clear()
    req.kv = NS(
        req_pool_idx=1 if allocated else None,
        kv_allocated_len=allocated, kv_committed_len=allocated,
    )
    req.finished = lambda: False
    live.register_visible_request(req)
    return req


@pytest.mark.parametrize("location", ("running", "chunk", "selected", "inflight"))
def test_existing_input_suffix_has_priority_over_new_child_state(runtime, location):
    parent = parent_request(runtime, allocated=0 if location == "selected" else 100)
    req = child(runtime)
    gate = runtime._subagent_materialization
    native = runtime._native_cache
    native.token_to_kv_pool_allocator.available_size = lambda: 750
    running = NS(reqs=[parent] if location == "running" else [])
    adder = NS(can_run_list=[parent] if location in ("chunk", "selected") else [])
    gate.prepare([req], running_batch=running, adder=adder, now=100.)

    assert not gate.allows(runtime.visible[req.rid])
    assert gate.last_decision["outstanding_input_tokens"] == (
        700 if location == "selected" else 600
    )
    assert parent.kv.kv_allocated_len == (0 if location == "selected" else 100)
    assert req.kv.req_pool_idx is None
    assert req.kv.kv_allocated_len == 0


def test_ordinary_continuation_inflight_suffix_is_reserved_too(runtime):
    first = parent_request(runtime, allocated=50, tokens=50)
    runtime.on_prefill_candidate_result(first, admitted=True, result="CONTINUE")
    first.finished = lambda: True
    runtime.on_batch_completed(NS(reqs=[first]))
    parent = parent_request(runtime, identity="parent-next")
    assert runtime._agent_admission.policy.programs["parent"].steps == 2
    req = child(runtime)
    runtime._native_cache.token_to_kv_pool_allocator.available_size = lambda: 750

    prepare(runtime, [req], running=[parent])
    gate = runtime._subagent_materialization
    assert gate.last_decision["outstanding_initial_tokens"] == 0
    assert gate.last_decision["outstanding_input_tokens"] == 600
    assert not gate.allows(runtime.visible[req.rid])


@pytest.mark.parametrize("runtime", ("pressure_v1", "pressure_v2"), indirect=True)
@pytest.mark.parametrize("state,expected", (
    ("ready_with_checkpoint", 200),
    ("ready_without_checkpoint", 700),
    ("paused", 0),
    ("not_in_native_queue", 0),
    ("selected", 200),
))
def test_ready_continuation_reserves_growth_before_new_state(runtime, monkeypatch, state, expected):
    first = parent_request(runtime, allocated=500, tokens=500)
    runtime.on_prefill_candidate_result(first, admitted=True, result="CONTINUE")
    first.finished = lambda: True
    runtime.on_batch_completed(NS(reqs=[first]))
    parent = parent_request(runtime, identity="parent-next", allocated=0)
    req = child(runtime)
    gate = runtime._subagent_materialization
    item = runtime._agent_admission.policy.programs["parent"]
    item.active = state != "paused"
    runtime._native_cache.token_to_kv_pool_allocator.available_size = lambda: 850
    reads = []

    def inspect(request, *, key, refresh):
        assert request is parent and key == runtime.visible[parent.rid]
        reads.append(refresh)
        return None if state == "ready_without_checkpoint" else {"checkpoint_tokens": 500}

    monkeypatch.setattr(runtime, "_read_request_reentry", inspect)
    adder = NS(can_run_list=[])
    if state == "selected":
        parent.prefix_indices = range(500)
        adder.can_run_list.append(parent)
    queue = [req] if state == "not_in_native_queue" else [parent, req]
    gate.prepare(queue, running_batch=NS(reqs=[]), adder=adder, now=100., _starting=True)

    ready = expected if gate.include_root_entries and state != "selected" else 0
    assert gate.last_decision.get("ready_continuation_tokens", 0) == ready
    assert gate.last_decision["outstanding_input_tokens"] == (
        ready + (expected if state == "selected" else 0)
    )
    assert reads == ([True] if ready else [])
    assert gate.allows(runtime.visible[req.rid]) is (ready != 700)


@pytest.mark.parametrize("runtime", ("pressure_v1", "pressure_v2"), indirect=True)
def test_running_decode_growth_is_reserved_before_first_materialization(runtime):
    parent = parent_request(runtime, allocated=620)
    parent.output_ids[:] = [1] * 20
    parent.sampling_params = NS(max_new_tokens=500)
    req = child(runtime)
    gate = runtime._subagent_materialization
    runtime._native_cache.token_to_kv_pool_allocator.available_size = lambda: 450

    prepare(runtime, [req], running=[parent])

    assert gate.last_decision["outstanding_input_tokens"] == (480 if gate.include_root_entries else 80)
    assert gate.allows(runtime.visible[req.rid]) is not gate.include_root_entries


@pytest.mark.parametrize("runtime", ("pressure_v2",), indirect=True)
def test_first_root_and_child_share_a_budget_including_clipped_generation(runtime):
    root, _ = new_root(runtime, "arrival")
    req = child(runtime)
    root.sampling_params = req.sampling_params = NS(max_new_tokens=16384)
    runtime._native_cache.token_to_kv_pool_allocator.available_size = lambda: 8000
    gate = runtime._subagent_materialization

    prepare(runtime, [root, req], running=[object()])

    assert gate.last_decision["allowed"] == [req.rid]
    assert gate.last_decision["held_root_request_ids"] == [root.rid]


@pytest.mark.parametrize("runtime", ("pressure_v2",), indirect=True)
def test_host_demand_reserve_does_not_replace_batch_page_overhead(runtime):
    req = child(runtime)
    gate = runtime._subagent_materialization
    unit = runtime._native_cache.host_pool_group.entry_map["kv"].host_pool.size_per_token
    runtime._host_demand_capacity = NS(reserve=lambda: 100 * unit)
    runtime._native_cache.token_to_kv_pool_allocator.available_size = lambda: 301

    prepare(runtime, [req], running=[object()])

    assert gate.last_decision["reserved_tokens"] == 102
    assert not gate.allows(runtime.visible[req.rid])


@pytest.mark.parametrize("runtime", ("pressure_v2",), indirect=True)
@pytest.mark.parametrize("wait_s", (1799., 1800.))
def test_held_root_preserves_service_clock_until_capacity_allows_it(runtime, wait_s):
    req, _ = new_root(runtime, "waiting-root")
    admission, gate = runtime._agent_admission, runtime._subagent_materialization
    guard = admission._ready_service_guard = ReadyServiceGuard(admission, fit_after_s=600)
    key = runtime.visible[req.rid]
    deferred = []
    admission._deferred_dispatch = NS(defer=deferred.append)
    running = [object()]
    began = 100.

    prepare(runtime, [req], running=running, now=began)
    guard.begin(began)
    assert guard.since[key.invocation_id] == (key, began)

    ready_at = began + wait_s
    guard.begin(ready_at)
    assert not guard.fit_available
    assert guard.target is None
    assert not admission._timeout_force_resume_allowed(key)
    assert gate.defer_start(req)

    admission.next_tick = ready_at + 2.
    runtime._native_cache.token_to_kv_pool_allocator.available_size = lambda: 300
    gate.capacity_changed()
    prepare(runtime, [req], running=running, now=ready_at)
    assert gate.allows(key)
    assert deferred == [key]
    assert admission.next_tick == (0. if wait_s == guard.timeout_s else ready_at + 2.)
    guard.begin(ready_at)
    assert guard.since[key.invocation_id] == (key, began)
    assert admission._timeout_force_resume_allowed(key) is (wait_s == guard.timeout_s)

    runtime._native_cache.token_to_kv_pool_allocator.available_size = lambda: 0
    gate.capacity_changed()
    assert gate.defer_start(req)
    assert not admission._timeout_force_resume_allowed(key)
    guard.begin(ready_at + 1.)
    assert guard.since[key.invocation_id] == (key, began)
    assert guard.target is None


@pytest.mark.parametrize("runtime", ("pressure_v2",), indirect=True)
@pytest.mark.parametrize("block", ("tool", "stale_request"))
def test_held_root_wait_clock_requires_ready_and_exact_identity(runtime, block):
    req, _ = new_root(runtime, "waiting-root")
    admission = runtime._agent_admission
    guard = ReadyServiceGuard(admission)
    key = runtime.visible[req.rid]
    guard.begin(100.)
    assert key.invocation_id in guard.since
    if block == "tool":
        runtime.graph.invocations[key.invocation_id].active_tool_calls["tool"] = "execute"
    else:
        runtime.visible.pop(req.rid)
    guard.begin(2000.)
    assert key.invocation_id not in guard.since
    assert guard.target is None


def stage_parent_restore(runtime):
    parent = parent_request(runtime, allocated=0, session_id="parent")
    native = runtime._native_cache
    native.token_to_kv_pool_allocator.available_size = lambda: 750
    fixture = pipeline_fixture()
    fixture.controller.native_tokens = 100
    native.cache_controller = fixture.controller
    native.ready_to_load_host_cache = fixture.cache.ready_to_load_host_cache
    native.dec_lock_ref = fixture.cache.dec_lock_ref
    parent.lock_receipt = fixture.receipt
    parent.last_node = 7
    parent.host_hit_length = parent.host_loaded_length = 100
    parent.prefix_indices = list(range(100))
    fixture.adder.can_run_list[:] = [parent]
    fixture.adder.new_chunked_req = parent
    pipeline = runtime._demand_prefill_pipeline = DemandPrefillPipeline(runtime, native)
    assert pipeline.stage(
        parent, adder=fixture.adder, running_batch=fixture.running,
        candidate_count=1, native_load_queue_was_empty=True, chunked_req=None,
    )
    assert fixture.adder.can_run_list == []
    assert parent.kv.req_pool_idx is None and parent.kv.kv_allocated_len == 0
    return parent, pipeline, fixture


@pytest.mark.parametrize("identity", ("current", "stale_epoch", "different_object"))
def test_staged_native_restore_reserves_its_live_input_after_adder_is_cleared(
    runtime, identity,
):
    parent, pipeline, fixture = stage_parent_restore(runtime)
    member, = pipeline.pending.members
    original_key = member.key
    if identity == "stale_epoch":
        member.key = replace(member.key, context_epoch=member.key.context_epoch + 1)
    elif identity == "different_object":
        member.request = copy(parent)
    req = child(runtime)
    gate = runtime._subagent_materialization
    try:
        gate.prepare(
            [req], running_batch=fixture.running, adder=fixture.adder, now=100.,
        )
        assert gate.last_decision["outstanding_input_tokens"] == (
            600 if identity == "current" else 0
        )
        assert gate.allows(runtime.visible[req.rid]) is (identity != "current")
        assert parent.kv.req_pool_idx is None and parent.kv.kv_allocated_len == 0
    finally:
        member.key, member.request = original_key, parent
    if identity == "current":
        pipeline.cancel("test_native_selection_cancelled")
        gate.capacity_changed()
        gate.prepare(
            [req], running_batch=fixture.running, adder=fixture.adder, now=100.1,
        )
        assert gate.last_decision["outstanding_input_tokens"] == 0
        assert gate.allows(runtime.visible[req.rid])


def test_staged_restore_and_current_native_selection_share_one_input_commitment(runtime):
    parent, pipeline, fixture = stage_parent_restore(runtime)
    req = child(runtime)
    fixture.adder.can_run_list.append(parent)
    gate = runtime._subagent_materialization
    gate.prepare([req], running_batch=fixture.running, adder=fixture.adder, now=100.)
    assert gate.last_decision["outstanding_input_tokens"] == 600
    assert gate.last_decision["outstanding_initial_tokens"] == 0
    assert not gate.allows(runtime.visible[req.rid])
    assert pipeline.pending.members[0].request is parent
    assert parent.kv.req_pool_idx is None and parent.kv.kv_allocated_len == 0


def test_child_suffix_is_not_charged_twice_when_native_lists_overlap(runtime):
    req = child(runtime)
    gate = runtime._subagent_materialization
    prepare(runtime, [req])
    runtime.on_prefill_candidate_result(req, admitted=True, result="CONTINUE")
    req.kv.req_pool_idx = 1
    req.kv.kv_allocated_len = req.kv.kv_committed_len = 40
    other = child(runtime, "other")
    runtime._native_cache.token_to_kv_pool_allocator.available_size = lambda: 500
    gate.prepare(
        [other], running_batch=NS(reqs=[req]), adder=NS(can_run_list=[req]), now=102.,
    )
    assert gate.last_decision["outstanding_initial_tokens"] == 160
    assert gate.last_decision["available_before_tokens"] == 337
    assert gate.allows(runtime.visible[other.rid])


@pytest.mark.parametrize("location", ("selected", "staged"))
def test_unallocated_selected_child_keeps_permission_without_a_second_budget_charge(runtime, location):
    first = child(runtime, "first", tokens=300)
    second = child(runtime, "second", tokens=100)
    gate = runtime._subagent_materialization
    runtime._native_cache.token_to_kv_pool_allocator.available_size = lambda: 603
    adder = NS(can_run_list=[], rem_chunk_tokens=2048, dllm_config=None)
    gate.prepare([first, second], running_batch=NS(reqs=[]), adder=adder, now=100.)
    assert gate.allows(runtime.visible[first.rid])
    assert gate.allows(runtime.visible[second.rid])
    if location == "selected":
        adder.can_run_list.append(first)
    else:
        runtime._demand_prefill_pipeline = NS(pending=NS(members=[
            NS(key=runtime.visible[first.rid], request=first),
        ]))
    assert not runtime.defer_prefill_for_prefetch(second)
    assert gate.last_decision["outstanding_input_tokens"] == 400
    assert gate.last_decision["committed_initial_request_ids"] == [first.rid]
    assert gate.allows(runtime.visible[first.rid])
    assert gate.allows(runtime.visible[second.rid])
    assert not gate.pending[first.rid].started
    assert first.kv.req_pool_idx is None and first.kv.kv_allocated_len == 0

    # Cancellation removes the commitment without declaring the child started
    # or granting it a lasting materialization exemption.
    adder.can_run_list.clear()
    runtime._demand_prefill_pipeline = None
    runtime._native_cache.token_to_kv_pool_allocator.available_size = lambda: 100
    gate.capacity_changed()
    gate.prepare([first, second], running_batch=NS(reqs=[object()]), adder=adder, now=101.)
    assert gate.last_decision["outstanding_input_tokens"] == 0
    assert not gate.last_decision["committed_initial_request_ids"]
    assert not gate.allows(runtime.visible[first.rid])
    assert not gate.allows(runtime.visible[second.rid])


def test_selected_child_does_not_spend_the_pending_candidate_window_twice(runtime):
    first, second = child(runtime, "first"), child(runtime, "second")
    gate = runtime._subagent_materialization
    gate.max_candidates = 1
    runtime._native_cache.token_to_kv_pool_allocator.available_size = lambda: 403
    adder = NS(can_run_list=[])
    gate.prepare([first, second], running_batch=NS(reqs=[]), adder=adder, now=100.)
    assert gate.allows(runtime.visible[first.rid])
    assert not gate.allows(runtime.visible[second.rid])
    adder.can_run_list.append(first)
    gate.capacity_changed()
    gate.prepare([first, second], running_batch=NS(reqs=[]), adder=adder, now=101.)
    assert gate.last_decision["outstanding_input_tokens"] == 200
    assert [key["request_id"] for key in gate.last_decision["candidates"]] == [second.rid]
    assert gate.allows(runtime.visible[first.rid])
    assert gate.allows(runtime.visible[second.rid])


def test_foreign_selected_object_cannot_hold_a_child_permission(runtime):
    first, second = child(runtime, "first"), child(runtime, "second")
    gate = runtime._subagent_materialization
    runtime._native_cache.token_to_kv_pool_allocator.available_size = lambda: 403
    adder = NS(can_run_list=[])
    gate.prepare([first, second], running_batch=NS(reqs=[]), adder=adder, now=100.)
    adder.can_run_list.append(copy(first))
    runtime._native_cache.token_to_kv_pool_allocator.available_size = lambda: 100
    gate.capacity_changed()
    gate.prepare([first, second], running_batch=NS(reqs=[object()]), adder=adder, now=101.)
    assert gate.last_decision["outstanding_input_tokens"] == 0
    assert not gate.last_decision["committed_initial_request_ids"]
    assert not gate.allows(runtime.visible[first.rid])
    assert not gate.allows(runtime.visible[second.rid])


def test_native_start_rechecks_permission_after_other_requests_consume_capacity(runtime, monkeypatch):
    req = child(runtime)
    gate = runtime._subagent_materialization
    native = runtime._native_cache
    native.token_to_kv_pool_allocator.available_size = lambda: 1000
    prepare(runtime, [req], running=[object()])
    assert gate.allows(runtime.visible[req.rid])
    deadline = gate.next_check

    monkeypatch.setattr("time.monotonic", lambda: 100.1)
    assert 100.1 < deadline
    native.token_to_kv_pool_allocator.available_size = lambda: 50
    assert runtime.defer_prefill_for_prefetch(req)
    assert not gate.allows(runtime.visible[req.rid])
    assert not gate.pending[req.rid].started
    assert req.kv.req_pool_idx is None
    assert req.kv.kv_allocated_len == 0


def test_native_start_respects_an_existing_selected_input_commitment(runtime):
    req = child(runtime)
    gate = runtime._subagent_materialization
    native = runtime._native_cache
    native.token_to_kv_pool_allocator.available_size = lambda: 750
    adder = NS(can_run_list=[])
    gate.prepare([req], running_batch=NS(reqs=[]), adder=adder, now=100.)
    assert gate.allows(runtime.visible[req.rid])

    parent = parent_request(runtime, allocated=0)
    adder.can_run_list.append(parent)
    assert runtime.defer_prefill_for_prefetch(req)
    assert not gate.allows(runtime.visible[req.rid])
    assert parent.kv.kv_allocated_len == req.kv.kv_allocated_len == 0


def test_capacity_permission_is_not_recomputed_on_every_decode_or_for_existing_requests(runtime):
    req = child(runtime)
    gate = runtime._subagent_materialization
    native = runtime._native_cache
    native.token_to_kv_pool_allocator.available_size = lambda: 1000
    prepare(runtime, [req], running=[object()])
    assert not runtime.defer_prefill_for_prefetch(req)
    runtime.on_prefill_candidate_result(req, admitted=True, result="CONTINUE")
    checks = runtime.counts["subagent_materialization_checks"]
    for _ in range(10):
        assert not runtime.defer_prefill_for_prefetch(req)
    assert runtime.counts["subagent_materialization_checks"] == checks


def test_exhausted_native_chunk_defers_start_without_another_capacity_census(runtime):
    req = child(runtime)
    gate = runtime._subagent_materialization
    runtime._native_cache.token_to_kv_pool_allocator.available_size = lambda: 1000
    adder = NS(can_run_list=[], rem_chunk_tokens=2048, dllm_config=None)
    gate.prepare([req], running_batch=NS(reqs=[]), adder=adder, now=100.)
    assert gate.allows(runtime.visible[req.rid])
    checks = runtime.counts["subagent_materialization_checks"]
    adder.rem_chunk_tokens = 0
    assert runtime.defer_prefill_for_prefetch(req)
    assert runtime.counts["subagent_materialization_checks"] == checks
    assert not runtime.counts["subagent_materialization_start_checks"]
    adder.rem_chunk_tokens = 2048
    assert not runtime.defer_prefill_for_prefetch(req)


@pytest.mark.parametrize("result", ("NO_TOKEN", "OTHER"))
def test_native_rejection_backs_off_only_token_pressure_and_release_wakes_it(
    runtime, monkeypatch, result,
):
    req = child(runtime)
    gate = runtime._subagent_materialization
    runtime._native_cache.token_to_kv_pool_allocator.available_size = lambda: 1000
    monkeypatch.setattr("time.monotonic", lambda: 100.)
    prepare(runtime, [req], running=[object()])
    assert not runtime.defer_prefill_for_prefetch(req)
    checks = runtime.counts["subagent_materialization_checks"]
    runtime.on_prefill_candidate_result(req, admitted=False, result=result)
    monkeypatch.setattr("time.monotonic", lambda: 100.1)

    assert runtime.defer_prefill_for_prefetch(req) is (result == "NO_TOKEN")
    assert runtime.counts["subagent_materialization_checks"] == checks + (result != "NO_TOKEN")
    assert gate.allows(runtime.visible[req.rid])
    assert not gate.pending[req.rid].started
    if result == "NO_TOKEN":
        monkeypatch.setattr("time.monotonic", lambda: 100.41)
        assert not runtime.defer_prefill_for_prefetch(req)
        runtime.on_prefill_candidate_result(req, admitted=False, result=result)
        monkeypatch.setattr("time.monotonic", lambda: 100.42)
        assert runtime.defer_prefill_for_prefetch(req)
    gate.capacity_changed()
    assert not runtime.defer_prefill_for_prefetch(req)
    assert req.kv.req_pool_idx is None and req.kv.kv_allocated_len == 0


def test_unreferenced_host_ancestor_is_not_available_through_a_locked_descendant():
    ancestor = node(1, 200, refs=0)
    descendant = node(2, 800, refs=0, host_lock=1)
    ancestor.children = {2: descendant}
    descendant.children = {}
    descendant.parent = ancestor
    native = cache(ancestor, descendant)
    ancestor.parent = native.tree_core.root_node
    observed = observe_materialization_headroom(native)
    assert observed.observable
    assert observed.reclaimable_host_tokens == 0
    assert observed.available_tokens == 0


def test_duplicate_capacity_must_be_visible_to_native_reclamation():
    mirror = node(1, 1000, device=True)
    native = cache(mirror)
    native.tree_core.full_host_duplicates = {}
    observed = observe_materialization_headroom(native)
    assert observed.observable
    assert observed.reclaimable_host_tokens == 0
    assert observed.available_tokens == 0


def _track_native_host_allocations(native, capacity, *, occupied=0):
    from sglang.srt.mem_cache.unified_cache.cache_action import BackupKV

    group = native.host_pool_group
    pool = group.entry_map["kv"].host_pool
    component = native.components[0]
    pool.size = capacity
    free_host = capacity - occupied

    def available():
        return free_host

    def free(indices, **kwargs):
        nonlocal free_host
        free_host += len(indices)
        assert free_host <= pool.size

    controller = native.cache_controller
    native_write = controller.write

    def write(indices, *, beliefkv_defer_writing=False, **kwargs):
        nonlocal free_host
        assert len(indices) <= free_host
        reserved = native_write(indices, beliefkv_defer_writing=True, **kwargs)
        free_host -= len(reserved)
        if not beliefkv_defer_writing:
            controller.start_writing()
        return reserved

    pool.available_size = group.available_size = available
    group.free = free
    component._full_kv_pool_host = pool
    controller.write = write

    def back_up(node, *, settle=True):
        assert native._execute_and_commit_kv_backup(
            BackupKV([node.id]), no_host_reclaim=True,
        ) == len(node.component_data[0].value)
        if settle:
            native.writing_check(finish_count=1)

    return back_up


@pytest.mark.parametrize(
    "staged_native,case",
    [
        pytest.param({"members": 2, "select": False}, case, id=case)
        for case in (
            "pressure", "free_host", "join_priority", "service_deadline", "no_decode",
            "host_pin", "bounded_credit",
        )
    ] + [
        pytest.param(
            {"members": 2, "select": False, "prefix_sizes": (4, 8)},
            "partial_credit", id="partial_credit",
        ),
        pytest.param(
            {"members": 2, "select": False, "prefix_sizes": (2, 8)},
            "small_credit", id="small_credit",
        ),
    ],
    indirect=["staged_native"],
)
def test_d3_submits_selected_demand_before_destructive_writeback(staged_native, case):
    from test_beliefkv_terminal_capacity import insert_session

    f = staged_native
    native, allocator = f.cache, f.allocator
    first_hit = len(f.leaf.component_data[0].host_value)
    occupied = first_hit + len(f.leaves[1].component_data[0].host_value)
    free_host = 4 if case == "free_host" else 0
    back_up = _track_native_host_allocations(native, 8 + occupied + free_host, occupied=occupied)
    waiting, generation = insert_session(native, list(range(100, 108)), "waiting-parent")
    back_up(waiting)
    native._demote(waiting.id, {0: 0})
    free_after_first_load = 2 if case in ("partial_credit", "small_credit") else 4
    free_before = first_hit + free_after_first_load
    insert_session(native, list(range(200, 200 + allocator.size - free_before)), "unbacked")
    assert allocator.available_size() == free_before
    load = native.init_load_back
    f.runtime._native_chunk_input_reserve = 4
    gate = SubagentMaterialization(f.runtime, include_root_entries=True)
    gate.attach(native)
    if case == "bounded_credit":
        _, actions = native.tree_core.split_host_only_full_prefix(
            f.leaf.id, f.leaf.creation_time, 4,
        )
        native._apply_cache_actions(actions)
        gate.max_candidates = 1
    scheduler, construction, invoke = native_scheduler(f, enabled=True)

    def candidates(running, adder):
        gate.prepare(scheduler.waiting_queue, running_batch=running, adder=adder)
        return tuple(scheduler.waiting_queue)

    scheduler._beliefkv_prefill_candidates = candidates
    if case == "join_priority":
        f.runtime._join_priority_promoted = f.members[1].rid
    if case == "service_deadline":
        f.runtime._agent_admission._ready_service_guard = NS(
            elapsed_service_timeout=lambda key, now: key == f.runtime.visible[f.members[1].rid],
        )
    if case == "host_pin":
        host_lock = native.inc_host_lock_ref(f.leaf.id).to_dec_params()
    deferred = case in ("pressure", "partial_credit", "bounded_credit")
    if case == "no_decode":
        f.running.reqs.clear()
        with pytest.raises(BatchConstructionReached):
            invoke()
        assert construction == [tuple(f.members)]
        assert len(f.loads) == 2
        assert f.pipeline.pending is None
        native.ready_to_load_host_cache()
    else:
        assert invoke() == (None, f.running)
        assert not construction
        selected = [entry.request for entry in f.pipeline.pending.members]
        if deferred:
            assert selected == f.members[:1]
            assert len(f.loads) == 1
            assert f.leaves[1].component_data[0].host_lock_ref == 0
            assert f.leaves[1].load_back_pending_id is None
            assert native.host_pool_group.available_size() == 0
            assert invoke() == (None, f.running)
            assert len(f.loads) == 1
        else:
            assert selected == f.members
            assert len(f.loads) == 2

    f.complete.ready = True
    native.loading_check(finish_count=1)
    if deferred:
        assert native.tree_core.full_host_duplicates[f.leaves[0].id] is f.leaves[0]
        with pytest.raises(BatchConstructionReached):
            invoke()
        assert construction == [tuple(f.members)]
        assert len(f.loads) == 2
        native.loading_check(finish_count=1)
    elif case != "no_decode":
        with pytest.raises(BatchConstructionReached):
            invoke()
    assert not native.ongoing_load_back
    if case == "host_pin":
        assert f.leaf.component_data[0].host_lock_ref == 1
        native.dec_host_lock_ref(f.leaf.id, host_lock)

    observed = native.inspect_beliefkv_reentry(NS(
        session_id="waiting-parent", session_generation=generation,
        origin_input_ids=array("q", range(100, 109)), extra_key=None, cache_salt=None,
    ))
    expected = {
        "pressure": 8, "free_host": 8, "bounded_credit": 8,
        "partial_credit": 6, "small_credit": 2,
    }
    assert observed["checkpoint_tokens"] == expected.get(case, 4)
    assert f.runtime.counts["subagent_materialization_restore_pressure_deferrals"] == deferred
    assert f.runtime.counts["subagent_materialization_restore_pressure_covered_tokens"] == (
        4 if deferred else 0
    )
    for member in f.members:
        native.dec_lock_ref(member.last_node, member.lock_receipt)
    gate.close()
    assert native.init_load_back == load


@pytest.mark.parametrize("runtime,host_free,donor_sizes,needed,loses_prefix", (
    ("pressure_v1", 0, (2,), 2, True),
    ("pressure_v2", 0, (2,), 2, False),
    ("pressure_v2", 0, (1, 1), 2, False),
    ("pressure_v2", 0, (2,), 3, True),
    ("pressure_v2", 0, (), 2, True),
    ("pressure_v2", 8, (2,), 2, False),
), indirect=["runtime"])
def test_native_allocation_uses_settled_backing_before_host_pressure_write_back(
    runtime, full_native, host_free, donor_sizes, needed, loses_prefix,
):
    from sglang.srt.mem_cache.common import evict_from_tree_cache
    from test_beliefkv_terminal_capacity import insert_session

    native, allocator, writes, _, _ = full_native
    component = native.components[0]
    evict_device_leaf = native._evict_device_leaf
    back_up = _track_native_host_allocations(native, 8 + host_free)

    waiting_tokens = list(range(8 - sum(donor_sizes)))
    waiting, generation = insert_session(native, waiting_tokens, "waiting-parent")
    back_up(waiting)
    assert waiting.component_data[0].host_value is not None
    native._demote(waiting.id, {0: 0})
    assert waiting in native.tree_core.evictable_host_leaves
    unbacked, _ = insert_session(native, list(range(20, 28)), "existing-continuation")
    donors = []
    for index, size in enumerate(donor_sizes):
        start = 40 + index * 8
        donor, _ = insert_session(native, list(range(start, start + size)), f"donor-{index}")
        back_up(donor)
        assert donor in native.tree_core.evictable_device_leaves
        assert component.session_ref_eviction_strategy(unbacked) < component.session_ref_eviction_strategy(donor)
        donors.append(donor)

    # Replace the setup fixture's synthetic cache before any request is registered.
    runtime._agent_admission.policy = None
    runtime.attach_native_cache(native)
    runtime._native_chunk_input_reserve = needed
    gate = runtime._subagent_materialization
    assert gate.status().get("native_host_pressure_aware_write_back", False) is gate.include_root_entries
    device_target = allocator.available_size() + needed
    writes_before = len(writes)
    evict_from_tree_cache(native, device_target)
    assert allocator.available_size() >= device_target

    used_settled_donors = gate.include_root_entries and sum(donor_sizes) >= needed and not host_free
    if used_settled_donors:
        assert all(donor.component_data[0].value is None for donor in donors)
        assert all(donor.component_data[0].host_value is not None for donor in donors)
        assert unbacked.component_data[0].value is not None
        assert len(writes) == writes_before
        assert runtime.counts["subagent_materialization_writeback_avoided"] == 1
        assert runtime.counts["subagent_materialization_writeback_avoided_device_tokens"] == needed
    else:
        assert all(donor.component_data[0].value is not None for donor in donors)
        assert unbacked.component_data[0].value is None
        assert writes[-1].node_id == unbacked.id
        assert len(writes) == writes_before + 1
        assert runtime.counts["subagent_materialization_writeback_avoided"] == 0

    observed = native.inspect_beliefkv_reentry(NS(
        session_id="waiting-parent", session_generation=generation,
        origin_input_ids=array("q", [*waiting_tokens, 99]), extra_key=None, cache_salt=None,
    ))
    checkpoint = observed["checkpoint_tokens"] if observed else 0
    assert (checkpoint < len(waiting_tokens)) is loses_prefix
    if loses_prefix and gate.include_root_entries:
        assert checkpoint == len(waiting_tokens) - (needed - sum(donor_sizes))
    elif loses_prefix:
        assert checkpoint == 0
    gate.close()
    assert native._evict_device_leaf == evict_device_leaf


@pytest.mark.parametrize("runtime", ("pressure_v2",), indirect=True)
@pytest.mark.parametrize("branch", (
    "settled", "second_backed_child", "unbacked_child", "host_locked", "pending",
))
def test_native_allocation_can_demote_a_settled_ancestor_after_its_device_children(
    runtime, full_native, branch,
):
    from sglang.srt.mem_cache.common import evict_from_tree_cache
    from test_beliefkv_terminal_capacity import insert_session

    native, allocator, writes, _, _ = full_native
    back_up = _track_native_host_allocations(native, 16)
    backed_sibling = branch == "second_backed_child"
    waiting_tokens = list(range(8 if backed_sibling else 10))
    waiting, generation = insert_session(native, waiting_tokens, "waiting-parent")
    back_up(waiting)
    native._demote(waiting.id, {0: 0})
    unbacked, _ = insert_session(native, list(range(20, 32)), "native-victim")
    ancestor_tokens = list(range(40, 44))
    ancestor, _ = insert_session(native, ancestor_tokens, "backed-path")
    child, _ = insert_session(native, [*ancestor_tokens, 44, 45], "backed-path")
    back_up(child)
    donor_children = [child]
    if branch in ("second_backed_child", "unbacked_child"):
        sibling, _ = insert_session(native, [*ancestor_tokens, 46, 47], "other-path")
        if backed_sibling:
            back_up(sibling)
            donor_children.append(sibling)
    back_up(ancestor, settle=branch != "pending")
    if branch == "host_locked":
        native.inc_host_lock_ref(ancestor.id)

    assert ancestor not in native.tree_core.evictable_device_leaves
    assert all(node in native.tree_core.evictable_device_leaves for node in donor_children)
    assert native.host_pool_group.available_size() == 0
    runtime._agent_admission.policy = None
    runtime.attach_native_cache(native)
    before = len(writes)
    needed = 4 + 2 * len(donor_children)
    runtime._native_chunk_input_reserve = needed
    target = allocator.available_size() + needed
    evict_from_tree_cache(native, target)
    assert allocator.available_size() >= target

    preserved = branch in ("settled", "second_backed_child")
    assert (unbacked.component_data[0].value is not None) is preserved
    assert len(writes) == before + (0 if preserved else 1)
    for node in (*donor_children, ancestor):
        assert (node.component_data[0].value is None) is preserved
        if preserved:
            assert node.component_data[0].host_value is not None
    assert runtime.counts["subagent_materialization_writeback_avoided"] == int(preserved)
    assert runtime.counts["subagent_materialization_writeback_pressure_checks"] == 1
    observed = native.inspect_beliefkv_reentry(NS(
        session_id="waiting-parent", session_generation=generation,
        origin_input_ids=array("q", [*waiting_tokens, 99]), extra_key=None, cache_salt=None,
    ))
    # Six reclaimable duplicate slots cover the split write in the
    # unbacked-child case. A locked or pending ancestor leaves only two.
    expected_checkpoint = len(waiting_tokens) if preserved or branch == "unbacked_child" else 6
    assert (observed["checkpoint_tokens"] if observed else 0) == expected_checkpoint


@pytest.mark.parametrize("runtime,shortfall,referenced,retained", (
    ("pressure_v1", 3, True, 0),
    ("pressure_v2", 3, True, 5),
    ("pressure_v2", 8, True, 0),
    ("pressure_v2", 3, False, 0),
), indirect=["runtime"])
def test_host_pressure_evicts_only_the_needed_suffix_of_a_live_prefix(
    runtime, full_native, shortfall, referenced, retained,
):
    from sglang.srt.mem_cache.common import evict_from_tree_cache
    from test_beliefkv_terminal_capacity import insert_session

    native, allocator, writes, _, _ = full_native
    tree, component = native.tree_core, native.components[0]
    evict_host = component.drive_host_eviction
    evict_leaf = tree._evict_host_leaf
    back_up = _track_native_host_allocations(native, 8)
    waiting_tokens = list(range(8))
    waiting, generation = insert_session(native, waiting_tokens, "waiting-parent")
    back_up(waiting)
    native._demote(waiting.id, {0: 0})
    if not referenced:
        native.release_radix_session("waiting-parent")
    writer, _ = insert_session(native, list(range(20, 20 + shortfall)), "native-victim")
    assert waiting in tree.evictable_host_leaves
    assert native.host_pool_group.available_size() == 0
    last_access = waiting.last_access_time

    runtime._agent_admission.policy = None
    runtime.attach_native_cache(native)
    target = allocator.available_size() + shortfall
    try:
        evict_from_tree_cache(native, target)

        assert allocator.available_size() == target
        assert len(writes) == 2
        assert writes[-1].node_id == writer.id
        assert native.host_pool_group.available_size() == 8 - shortfall - retained
        assert tree._evict_host_leaf == evict_leaf
        assert runtime.counts["subagent_materialization_host_eviction_retained_tokens"] == retained
        if referenced:
            observed = native.inspect_beliefkv_reentry(NS(
                session_id="waiting-parent", session_generation=generation,
                origin_input_ids=array("q", [*waiting_tokens, 99]), extra_key=None,
                cache_salt=None,
            ))
            assert (observed["checkpoint_tokens"] if observed else 0) == retained
            if retained:
                assert waiting.parent.component_data[0].session_ref == 1
                assert waiting.parent.last_access_time == last_access
    finally:
        runtime._subagent_materialization.close()
    assert component.drive_host_eviction == evict_host


@pytest.mark.parametrize("runtime", ("pressure_v2",), indirect=True)
def test_host_pressure_trims_after_accounting_for_existing_duplicate_reclaims(
    runtime, full_native,
):
    from test_beliefkv_terminal_capacity import insert_session

    native, _, _, _, _ = full_native
    back_up = _track_native_host_allocations(native, 10)
    waiting, _ = insert_session(native, list(range(8)), "waiting-parent")
    back_up(waiting)
    native._demote(waiting.id, {0: 0})
    mirror, _ = insert_session(native, [10, 11], "device-mirror")
    back_up(mirror)

    runtime._agent_admission.policy = None
    runtime.attach_native_cache(native)

    assert native.evict_host(5) == 5
    assert native.host_pool_group.available_size() == 5
    assert len(mirror.component_data[0].value) == 2
    assert mirror.component_data[0].host_value is None
    assert len(waiting.parent.component_data[0].host_value) == 5
    assert runtime.counts["subagent_materialization_host_eviction_trimmed_nodes"] == 1
    assert runtime.counts["subagent_materialization_host_eviction_retained_tokens"] == 5


@pytest.mark.parametrize("runtime,host_free,chunk,checkpoint,device_freed", (
    ("pressure_v1", 2, None, 0, 8),
    ("pressure_v2", 0, None, 0, 8),
    ("pressure_v2", 2, None, 8, 2),
    ("pressure_v2", 8, None, 8, 8),
    ("pressure_v2", 0, 4, 6, 2),
    ("pressure_v2", 2, 4, 8, 2),
    ("pressure_v2", 4, 4, 8, 4),
), indirect=["runtime"])
def test_small_native_allocation_bounds_writeback_under_host_pressure(
    runtime, full_native, host_free, chunk, checkpoint, device_freed,
):
    from sglang.srt.mem_cache.common import evict_from_tree_cache
    from test_beliefkv_terminal_capacity import insert_session

    native, allocator, writes, _, _ = full_native
    back_up = _track_native_host_allocations(native, 8 + host_free)
    waiting_tokens = list(range(8))
    waiting, generation = insert_session(native, waiting_tokens, "waiting-parent")
    back_up(waiting)
    native._demote(waiting.id, {0: 0})
    writer_tokens = list(range(20, 28))
    writer, writer_generation = insert_session(native, writer_tokens, "continuation")
    last_access = writer.last_access_time

    runtime._agent_admission.policy = None
    runtime.attach_native_cache(native)
    runtime._native_chunk_input_reserve = chunk
    before = allocator.available_size()
    evict_from_tree_cache(native, before + 2)

    assert allocator.available_size() - before == device_freed
    assert len(writes) == 2
    assert writes[-1].node_id == writer.id
    assert len(writes[-1].device_indices) == device_freed
    assert writer.component_data[0].value is None
    split = device_freed < len(writer_tokens)
    assert runtime.counts["subagent_materialization_writeback_split_nodes"] == int(split)
    if split:
        assert len(writer.parent.component_data[0].value) == len(writer_tokens) - device_freed
        assert writer.parent.last_access_time == last_access
    for owner, owner_generation, tokens, expected in (
        ("waiting-parent", generation, waiting_tokens, checkpoint),
        ("continuation", writer_generation, writer_tokens, len(writer_tokens)),
    ):
        observed = native.inspect_beliefkv_reentry(NS(
            session_id=owner, session_generation=owner_generation,
            origin_input_ids=array("q", [*tokens, 99]), extra_key=None, cache_salt=None,
        ))
        assert (observed["checkpoint_tokens"] if observed else 0) == expected


@pytest.mark.parametrize("runtime", ("pressure_v2",), indirect=True)
@pytest.mark.parametrize("mirror_size,mirror_state,device_freed,checkpoint", (
    (3, "settled", 3, 8),
    (2, "settled", 2, 8),
    (4, "settled", 4, 8),
    (1, "settled", 2, 7),
    (3, "pinned", 2, 6),
    (3, "reclaim_disabled", 2, 6),
))
def test_writeback_padding_does_not_turn_safe_duplicate_reclaim_into_live_host_loss(
    runtime, full_native, monkeypatch, mirror_size, mirror_state, device_freed, checkpoint,
):
    from sglang.srt.mem_cache.common import evict_from_tree_cache
    from test_beliefkv_terminal_capacity import insert_session

    native, allocator, writes, _, _ = full_native
    back_up = _track_native_host_allocations(native, 8 + mirror_size)
    waiting, generation = insert_session(native, list(range(8)), "waiting-parent")
    back_up(waiting)
    native._demote(waiting.id, {0: 0})
    writer, _ = insert_session(native, list(range(20, 28)), "continuation")
    mirror, _ = insert_session(native, list(range(40, 40 + mirror_size)), "resident")
    back_up(mirror)
    device_lock = native.inc_lock_ref(mirror.id).to_dec_params()
    host_lock = None
    if mirror_state == "pinned":
        host_lock = native.inc_host_lock_ref(mirror.id).to_dec_params()
    if mirror_state == "reclaim_disabled":
        monkeypatch.setenv("SGLANG_HICACHE_SKIP_HOST_DUPLICATE_RECLAIM", "1")

    runtime._agent_admission.policy = None
    runtime.attach_native_cache(native)
    runtime._native_chunk_input_reserve = 4
    before = allocator.available_size()
    original_writer_tokens = len(writer.component_data[0].value)
    evict_from_tree_cache(native, before + 2)

    assert allocator.available_size() - before == device_freed
    assert writes[-1].node_id == writer.id
    assert len(writes[-1].device_indices) == device_freed
    assert len(writer.parent.component_data[0].value) + device_freed == original_writer_tokens
    assert runtime.counts["subagent_materialization_writeback_padding_tokens_avoided"] == (
        4 - device_freed
    )
    assert len(mirror.component_data[0].value) == mirror_size
    observed = native.inspect_beliefkv_reentry(NS(
        session_id="waiting-parent", session_generation=generation,
        origin_input_ids=array("q", range(9)), extra_key=None, cache_salt=None,
    ))
    assert observed["checkpoint_tokens"] == checkpoint
    if host_lock is not None:
        native.dec_host_lock_ref(mirror.id, host_lock)
    native.dec_lock_ref(mirror.id, device_lock)


@pytest.mark.parametrize(
    "runtime,host_free,keep_reference,discarded,checkpoint",
    (
        ("pressure_v2", 0, False, True, 8),
        ("pressure_v2", 0, True, False, 6),
        ("pressure_v2", 2, False, False, 8),
        ("pressure_v1", 0, False, False, 0),
    ),
    indirect=["runtime"],
)
def test_cold_device_leaf_needs_no_backup_at_the_expense_of_live_host(
    runtime, full_native, host_free, keep_reference, discarded, checkpoint,
):
    from sglang.srt.mem_cache.common import evict_from_tree_cache
    from test_beliefkv_terminal_capacity import insert_session

    native, allocator, writes, _, _ = full_native
    back_up = _track_native_host_allocations(native, 8 + host_free)
    waiting, generation = insert_session(native, list(range(8)), "join-parent")
    back_up(waiting)
    native._demote(waiting.id, {0: 0})
    cold, _ = insert_session(native, list(range(20, 28)), "other")
    if not keep_reference:
        native.release_radix_session("other")

    runtime._agent_admission.policy = None
    runtime.attach_native_cache(native)
    runtime._native_chunk_input_reserve = 4
    before = allocator.available_size()
    evict_from_tree_cache(native, before + 2)

    assert allocator.available_size() >= before + 2
    assert len(writes) == (1 if discarded else 2)
    assert (cold.id not in native.tree_core._node_arena) is discarded
    assert runtime.counts["subagent_materialization_cold_device_discarded_tokens"] == (
        8 if discarded else 0
    )
    prefix = native.inspect_beliefkv_reentry(NS(
        session_id="join-parent", session_generation=generation,
        origin_input_ids=array("q", range(9)), extra_key=None, cache_salt=None,
    ))
    assert (prefix["checkpoint_tokens"] if prefix else 0) == checkpoint


@pytest.mark.parametrize("runtime", ("pressure_v2",), indirect=True)
def test_writeback_keeps_an_unreferenced_device_boundary_over_a_host_child(
    runtime, full_native,
):
    from sglang.srt.mem_cache.common import evict_from_tree_cache
    from test_beliefkv_terminal_capacity import insert_session

    native, allocator, writes, _, _ = full_native
    back_up = _track_native_host_allocations(native, 12)
    waiting, _ = insert_session(native, list(range(8)), "join-parent")
    back_up(waiting)
    native._demote(waiting.id, {0: 0})
    child, _ = insert_session(native, [20, 21, 22, 23, 24, 25], "old-child")
    insert_session(native, [20, 21, 22, 23], "old-parent")
    back_up(child)
    native._demote(child.id, {0: 0})
    native.release_radix_session("old-parent")
    native.release_radix_session("old-child")
    parent = child.parent
    assert parent.component_data[0].session_ref == 0
    assert parent in native.tree_core.evictable_device_leaves

    runtime._agent_admission.policy = None
    runtime.attach_native_cache(native)
    runtime._native_chunk_input_reserve = 4
    before = allocator.available_size()
    evict_from_tree_cache(native, before + 4)

    assert allocator.available_size() == before + 4
    assert len(writes) == 3
    assert writes[-1].node_id == parent.id
    assert not runtime.counts["subagent_materialization_cold_device_discarded_tokens"]


@pytest.mark.parametrize(
    "runtime,case,rebound_tokens",
    (
        ("pressure_v2", "matching", 8),
        ("pressure_v2", "partial", 4),
        ("pressure_v2", "different_namespace", 0),
        ("pressure_v2", "old_session_open", 0),
        ("pressure_v2", "stale_generation", 0),
        ("pressure_v1", "matching", 0),
    ),
    indirect=["runtime"],
)
def test_compacted_continuation_references_only_a_matching_native_prefix(
    runtime, full_native, case, rebound_tokens,
):
    from sglang.srt.managers.schedule_batch import Req
    from sglang.srt.sampling.sampling_params import SamplingParams
    from test_beliefkv_terminal_capacity import insert_session

    native, _, writes, loads, _ = full_native
    back_up = _track_native_host_allocations(native, 12)
    previous, _ = insert_session(native, list(range(8)), "before-compact")
    back_up(previous)
    native._demote(previous.id, {0: 0})
    cold, _ = insert_session(native, list(range(20, 24)), "cold")
    back_up(cold)
    native._demote(cold.id, {0: 0})
    native.release_radix_session("cold")

    runtime._agent_admission.policy = None
    runtime.attach_native_cache(native)
    first = parent_request(runtime, allocated=8, tokens=8, session_id="before-compact")
    runtime.on_prefill_candidate_result(first, admitted=True, result="CONTINUE")
    first.finished = lambda: True
    runtime.on_batch_completed(NS(reqs=[first]))
    runtime.on_events((replace(
        observed(2, Kind.CONTEXT_COMPACT, invocation_id="parent", context_id="parent-context"),
        context_epoch=1, attributes={"previous_context_epoch": 0},
    ),))
    if case != "old_session_open":
        native.release_radix_session("before-compact")

    ids = array("q", range(9))
    if case == "partial":
        ids[4:] = array("q", [30, 31, 32, 33, 34])
    req = Req(
        rid="compacted", origin_input_text="", origin_input_ids=ids,
        sampling_params=SamplingParams(max_new_tokens=2), session_id="after-compact",
        extra_key="different" if case == "different_namespace" else None,
        beliefkv_metadata={
            "root_workflow_id": "wf", "invocation_id": "parent",
            "context_id": "parent-context", "context_epoch": 1,
        },
    )
    req.session_generation = native.ensure_session_generation(req.session_id)
    if case == "stale_generation":
        req.session_generation += 1
    initial_node = req.last_node
    assert runtime.register_visible_request(req)

    assert runtime.counts["subagent_materialization_rebound_prefix_tokens"] == rebound_tokens
    if case == "matching" and rebound_tokens:
        assert native.evict_host(2) == 4  # The unrelated, unreferenced leaf.
        assert native.inspect_beliefkv_reentry(req)["checkpoint_tokens"] == 8
        assert native.release_radix_session(req.session_id) == 0
        assert previous.component_data[0].session_ref == 0
    elif case == "partial":
        assert previous.component_data[0].session_ref == 0
        assert previous.parent.component_data[0].session_ref == 1
    assert req.last_node == initial_node
    assert len(writes) == 2 and not loads


@pytest.mark.parametrize("runtime", ("pressure_v2",), indirect=True)
def test_native_allocation_finds_sparse_backing_outside_the_bounded_heap_prefix(runtime, full_native):
    from sglang.srt.mem_cache.common import evict_from_tree_cache
    from test_beliefkv_terminal_capacity import insert_session

    native, allocator, writes, _, _ = full_native
    back_up = _track_native_host_allocations(native, 8)
    waiting, _ = insert_session(native, list(range(6)), "waiting-parent")
    back_up(waiting)
    native._demote(waiting.id, {0: 0})
    victim, _ = insert_session(native, list(range(20, 28)), "native-victim")
    others = [
        insert_session(native, [start, start + 1], f"unbacked-{start}")[0]
        for start in (30, 32, 34)
    ]
    donor, _ = insert_session(native, [40, 41], "backed-donor")
    back_up(donor)
    priority = native.components[0].session_ref_eviction_strategy
    assert all(priority(victim) < priority(other) < priority(donor) for other in others)
    assert list(native.tree_core.full_host_duplicates) == [waiting.id, donor.id]

    runtime._agent_admission.policy = None
    runtime.attach_native_cache(native)
    runtime._subagent_materialization.max_candidates = 2
    target = allocator.available_size() + 2
    evict_from_tree_cache(native, target)
    assert allocator.available_size() == target
    assert len(writes) == 2
    assert waiting.component_data[0].host_value is not None
    assert victim.component_data[0].value is not None
    assert donor.component_data[0].value is None
    assert donor.component_data[0].host_value is not None


def test_real_native_host_lock_blocks_ancestor_credit_until_native_eviction_can_use_it(full_native):
    from test_beliefkv_terminal_capacity import insert_session

    native, allocator, writes, loads, commits = full_native
    pool = native.host_pool_group.entry_map["kv"].host_pool
    pool.size = 8
    pool.available_size = lambda: 0
    ancestor, generation = insert_session(native, [1, 2, 3, 4], "waiting")
    native.prepare_beliefkv_spawn_full(session_id="waiting", session_generation=generation)
    native.writing_check(finish_count=1)
    leaf, _ = insert_session(native, list(range(1, 9)), "waiting")
    native.prepare_beliefkv_spawn_full(session_id="waiting", session_generation=generation)
    native.writing_check(finish_count=1)
    native.beliefkv_cold_full_owner_validator = lambda _: True
    assert native.reclaim_beliefkv_handoff_capacity(
        full_tokens=allocator.available_size() + 8, mamba_slots=0,
    ) == {0: 8}
    native.release_radix_session("waiting")
    lock = native.tree_core.inc_host_lock_ref(leaf.id).to_dec_params()
    assert ancestor.component_data[0].session_ref == 0
    observed = observe_materialization_headroom(native)
    assert observed.observable and observed.reclaimable_host_tokens == 0
    assert native.evict_host(4) == 0
    assert ancestor.component_data[0].host_value is not None

    native.tree_core.dec_host_lock_ref(leaf.id, lock)
    observed = observe_materialization_headroom(native)
    assert observed.reclaimable_host_tokens == 8
    assert native.evict_host(8) == observed.reclaimable_host_tokens
    assert allocator.available_size() == 64
    assert len(writes) == 2 and not loads


def test_real_native_existing_prefix_survives_child_hold_and_release(runtime, full_native):
    from test_beliefkv_terminal_capacity import insert_session

    native, allocator, writes, loads, commits = full_native
    pool = native.host_pool_group.entry_map["kv"].host_pool
    pool.size = 32
    pool.available_size = lambda: 0
    waiting, generation = insert_session(native, list(range(32)), "waiting-parent")
    native.prepare_beliefkv_spawn_full(
        session_id="waiting-parent", session_generation=generation,
    )
    native.writing_check(finish_count=1)
    native.beliefkv_cold_full_owner_validator = lambda _: True
    assert native.reclaim_beliefkv_handoff_capacity(
        full_tokens=allocator.available_size() + 32, mamba_slots=0,
    ) == {0: 32}
    root, root_generation = insert_session(native, list(range(100, 116)), "inflight-root")
    assert allocator.available_size() == 48
    runtime._native_cache = native
    admission, gate = runtime._agent_admission, runtime._subagent_materialization
    admission.policy.capacity_tokens = 64
    admission.policy.buffer_tokens = 1
    parent = parent_request(runtime, allocated=16, tokens=60)
    req = child(runtime, tokens=4)
    req.origin_input_ids = list(range(1000, 1004))
    preserved = waiting.component_data[0].host_value

    prepare(runtime, [req], running=[parent])
    assert gate.last_decision["outstanding_input_tokens"] == 45
    assert not gate.allows(runtime.visible[req.rid])
    assert runtime.defer_prefill_for_prefetch(req)
    assert allocator.available_size() == 48
    assert waiting.component_data[0].host_value is preserved
    assert waiting.component_data[0].value is None
    assert not loads

    # Actual context retirement makes space; the held request can then enter
    # the real runtime/native selector without discarding the waiting prefix.
    retired = native.retire_beliefkv_child_session(
        session_id="inflight-root", session_generation=root_generation,
    )
    assert retired["freed_device_units"] == {0: 16}
    parent.finished = lambda: True
    runtime.on_batch_completed(NS(reqs=[parent]))
    gate.capacity_changed()
    now = time.monotonic() + 4.
    prepare(runtime, [req], now=now)
    admission.next_tick = 0.
    admission.tick(now=now)
    plan = runtime.plan_native_prefill([req], running_batch=NS(reqs=[]), adder=None)
    selected = select_native_prefill_candidates(
        [req], plan=plan, current_semantic_revision=runtime.semantic_revision,
    )
    assert selected.candidates == (req,)
    assert not runtime.defer_prefill_for_prefetch(req)
    assert allocator.available_size() == 64
    assert waiting.component_data[0].host_value is preserved
    assert req.kv.req_pool_idx is None and req.kv.kv_allocated_len == 0
    assert len(writes) == 1 and not loads
