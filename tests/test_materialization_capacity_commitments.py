"""New child state cannot spend capacity committed to an existing native input."""

from copy import copy
from dataclasses import replace
from types import SimpleNamespace as NS
import time

import pytest

from beliefkv.runtime.demand_prefill_pipeline import DemandPrefillPipeline
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from beliefkv.runtime.subagent_materialization import observe_materialization_headroom
from tests.test_demand_prefill_pipeline import fixture as pipeline_fixture
from tests.test_full_only_native_lifecycle import full_native
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
