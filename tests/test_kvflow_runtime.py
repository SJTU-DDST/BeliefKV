from array import array
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import EventConfidence, RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.experiments.paper_baselines import DISABLED_POLICIES, environment, validate_state
from beliefkv.runtime.kvflow_baseline import KVFlowObservedAdapter
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_full_only_native_lifecycle import full_native
from tests.test_paper_prefix_transfers import insert


@pytest.fixture
def live(monkeypatch, full_native):
    for name, value in environment({}, "kvflow_observed_graph").items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", KVFlowObservedAdapter.policy_name)
    runtime = NativeAdmissionRuntime()
    cache, allocator, writes, loads, commits = full_native
    cache.load_back_threshold = 1
    runtime.attach_native_cache(cache)

    def acknowledged(commit):
        commits.append(commit)
        runtime.on_native_transfer_commit(commit)

    cache.on_hicache_transfer_commit = acknowledged
    yield runtime, cache, allocator, writes, loads, commits
    runtime.close()


def request(identity, tokens, *, context="agent", invocation=None, epoch=0, workflow="workflow"):
    req = NS(
        rid=identity, origin_input_ids=array("q", tokens), output_ids=array("q"),
        extra_key=workflow, cache_salt=workflow, session_id=None,
        session_generation=None, cache_request_handle=NS(attempt_id=0),
        beliefkv_metadata={
            "root_workflow_id": workflow, "invocation_id": invocation or context,
            "context_id": context, "context_epoch": epoch,
        },
        finished=lambda: True, _compute_max_prefix_len=lambda n: max(n - 1, 0),
    )

    def refresh():
        req.full_untruncated_fill_ids = req.origin_input_ids + req.output_ids

    req._refresh_fill_ids = refresh
    return req


def register(runtime, req):
    assert runtime.register_visible_request(req)
    return runtime._paper_baseline


def complete(runtime, req):
    assert runtime.on_batch_completed(NS(reqs=[req])) == (req.rid,)


def selection(runtime, reqs):
    plan = runtime.plan_native_prefill(reqs, running_batch=NS(reqs=[]), adder=None)
    return select_native_prefill_candidates(
        reqs, plan=plan, current_semantic_revision=runtime.semantic_revision,
    ).candidates


def learned(live, *, tokens=(1, 2, 3, 4), context="agent"):
    runtime, cache, *_ = live
    insert(cache, list(tokens))
    first = request("first:" + context, tokens, context=context)
    adapter = register(runtime, first)
    complete(runtime, first)
    second = request("second:" + context, (*tokens, 5, 6), context=context)
    register(runtime, second)
    return adapter, second


def test_paper_owned_d2h_and_h2d_acks_do_not_enter_full_transaction_ledger(live):
    runtime, cache, _, _, _, commits = live
    leaf = insert(cache, [1, 2, 3, 4])
    adapter = runtime._paper_baseline
    owner = ("workflow", "agent", 0)
    with adapter._authority():
        snapshot = adapter.transfers.capture(leaf.id, owner=owner)
        assert adapter.transfers.backup(snapshot, owner=owner)
        assert adapter.transfers.receipts.pending_count == 1
    cache.writing_check(finish_count=1)
    assert commits[-1].child_commits
    assert not runtime.physical_disabled and not runtime.physical_ledger.pending_count
    with adapter._authority():
        assert adapter.transfers.receipts.pending_count == 0
        assert adapter.transfers.demote(snapshot, owner=owner, max_tokens=4) == 4
        assert adapter.transfers.restore(snapshot, owner=owner, available_tokens=4)
    cache.loading_check(finish_count=1)
    assert not runtime.physical_disabled
    assert adapter.transfers.receipts.pending_count == 0
    assert runtime.counts["paper_native_commits_completed"] == 2
    assert not runtime.completed_physical_actions


@pytest.mark.parametrize("change", ("duplicate", "bytes", "foreign", "direction"))
def test_invalid_paper_transfer_receipts_still_disable_physical_mutation(live, change):
    runtime, cache, _, _, _, commits = live
    leaf = insert(cache, [1, 2, 3, 4])
    adapter, owner = runtime._paper_baseline, ("workflow", "agent", 0)
    with adapter._authority():
        snapshot = adapter.transfers.capture(leaf.id, owner=owner)
        assert adapter.transfers.backup(snapshot, owner=owner)
    # Capture the real native receipt without consuming it in the runtime.
    cache.on_hicache_transfer_commit = commits.append
    cache.writing_check(finish_count=1)
    commit = commits[-1]
    if change == "duplicate":
        runtime.on_native_transfer_commit(commit)
        assert not runtime.physical_disabled
    elif change == "bytes":
        child = commit.child_commits[0]
        commit = commit._replace(child_commits=(child._replace(num_bytes=child.num_bytes + 64),))
    elif change == "foreign":
        child = commit.child_commits[0]
        commit = commit._replace(child_commits=(child._replace(command_id="unowned-command"),))
    else:
        commit = commit._replace(direction="h2d")
    assert runtime.on_native_transfer_commit(commit) == ()
    assert runtime.physical_disabled
    assert runtime.counts["physical_receipt_failed"] == 1


def test_actual_runtime_has_no_first_prompt_or_future_graph_assumption(live):
    runtime, cache, *_ = live
    insert(cache, [1, 2, 3, 4])
    first = request("first", [1, 2, 3, 4])
    adapter = register(runtime, first)
    assert not adapter.history.boundaries
    assert adapter.contexts["agent"].fixed_key is None
    assert not adapter.priorities._paths
    assert adapter.status()["application_declared_graph_equivalence_claimed"] is False
    assert not runtime._custom_kv_management and not runtime.enable_resident_first
    assert not runtime.enable_join_parent_priority and runtime._tool_continuity is None
    assert adapter.status()["future_information_used"] is False
    validate_state("kvflow_observed_graph", {
        **dict.fromkeys(DISABLED_POLICIES, False),
        "internal_comparison_mode": runtime.internal_comparison_mode,
        "paper_baseline": adapter.status(),
    })


def test_completed_same_context_input_learns_real_boundary_and_dynamic_suffix_priority(live):
    adapter, second = learned(live)
    runtime, cache, *_ = live
    fixed = adapter.contexts["agent"].snapshot
    assert fixed.prefix_tokens == adapter.history.boundaries["agent"] == 4
    suffix = insert(cache, list(second.origin_input_ids))
    with adapter._authority():
        adapter.priorities.set_steps({"agent": 1})
    assert adapter.priorities._eviction_key(
        cache.tree_core.node_by_id(fixed.leaf_id),
    )[0] == -1
    assert adapter.priorities._eviction_key(suffix)[0] == -float("inf")
    assert adapter.contexts["agent"].prior_input.token_ids is not second.origin_input_ids
    assert len(adapter.requests) == 1


@pytest.mark.parametrize("confidence", (EventConfidence.OBSERVED_EXACT, EventConfidence.INFERRED))
def test_all_and_any_observed_nested_joins_drive_different_distances(live, confidence):
    runtime, *_ = live
    events = [
        RuntimeEvent("start", 0., Kind.WORKFLOW_START, "workflow"),
        *(
            RuntimeEvent(
                "create:" + agent, 1., Kind.INVOCATION_CREATE, "workflow",
                invocation_id=agent, context_id=agent + ":context",
            ) for agent in ("root", "child", "leaf", "peer")
        ),
        RuntimeEvent(
            "inner", 2., Kind.JOIN_CREATE, "workflow", join_id="inner",
            member_invocation_ids=("leaf",),
        ),
        RuntimeEvent(
            "inner_wait", 3., Kind.JOIN_WAIT, "workflow",
            invocation_id="child", join_id="inner",
        ),
        RuntimeEvent(
            "outer", 4., Kind.JOIN_CREATE, "workflow", join_id="outer",
            member_invocation_ids=("child", "peer"),
        ),
        RuntimeEvent(
            "outer_wait", 5., Kind.JOIN_WAIT, "workflow",
            invocation_id="root", join_id="outer",
        ),
    ]
    events = [
        replace(event, confidence=confidence)
        if event.kind in (Kind.JOIN_CREATE, Kind.JOIN_WAIT) else event
        for event in events
    ]
    runtime.on_events(events)
    adapter = runtime._paper_baseline
    adapter.tick(now=0.)
    if confidence is EventConfidence.INFERRED:
        assert adapter.steps == {
            "root:context": 0, "child:context": 0, "leaf:context": 0, "peer:context": 0,
        }
        assert not runtime.graph.joins
        return
    assert adapter.steps == {
        "root:context": 2, "child:context": 1, "leaf:context": 0, "peer:context": 0,
    }
    from beliefkv.control.causal_graph import JoinMode

    runtime.graph.joins["outer"].mode = JoinMode.ANY
    adapter._graph_dirty = True
    adapter.tick(now=.1)
    assert adapter.steps["root:context"] == 1
    before = adapter.counts["graph_updates"]
    adapter.tick(now=.15)
    assert adapter.counts["graph_updates"] == before


def test_cpu_copy_lifecycle_skips_loading_then_uses_real_ack(live):
    adapter, second = learned(live)
    runtime, cache, allocator, writes, loads, commits = live
    context = adapter.contexts["agent"]
    adapter.steps = {"agent": 1}
    adapter._graph_dirty = False
    adapter._prefetch_order = ("agent",)
    before = allocator.available_size()
    adapter.tick(now=0.)
    assert len(writes) == 1 and not loads
    with adapter._authority():
        assert adapter.transfers.demote(
            context.snapshot, owner=context.owner, max_tokens=4,
        ) == 0
    cache.writing_check(finish_count=1)
    with adapter._authority():
        assert adapter.transfers.demote(
            context.snapshot, owner=context.owner, max_tokens=4,
        ) == 4
    adapter.tick(now=.2)
    assert len(loads) == 1 and allocator.available_size() == before
    assert selection(runtime, [second]) == ()
    assert adapter.counts["loading_requests_deferred"] > 0
    cache.loading_check(finish_count=1)
    assert selection(runtime, [second]) == (second,)
    assert [row.direction for row in commits] == ["d2h", "h2d"]


def test_pending_dynamic_suffix_blocks_even_when_fixed_prefix_is_resident(live):
    adapter, second = learned(live)
    runtime, cache, *_ = live
    suffix = insert(cache, [1, 2, 3, 4, 5])
    owner = adapter.contexts["agent"].owner
    snap = adapter.transfers.capture(suffix.id, owner=owner)
    with adapter._authority():
        adapter.transfers.backup(snap, owner=owner)
    cache.writing_check(finish_count=1)
    with adapter._authority():
        assert adapter.transfers.demote(snap, owner=owner, max_tokens=1) == 1
        assert adapter.transfers.restore(snap, owner=owner, available_tokens=1)
    fixed = adapter.contexts["agent"].snapshot
    assert adapter._status(fixed, owner) == "gpu"
    assert selection(runtime, [second]) == ()
    cache.loading_check(finish_count=1)
    assert selection(runtime, [second]) == (second,)


def test_new_cache_suffix_invalidates_previously_partial_match(live):
    adapter, second = learned(live)
    runtime, cache, *_ = live
    assert selection(runtime, [second]) == (second,)
    item = adapter.requests[second.rid]
    assert item.snapshot.prefix_tokens == 4
    revision = item.matched_revision
    suffix = insert(cache, [1, 2, 3, 4, 5])
    assert adapter.priorities.revision > revision
    suffix.load_back_pending_id = 123
    assert selection(runtime, [second]) == ()
    assert item.snapshot.prefix_tokens == 5
    suffix.load_back_pending_id = None


def test_host_only_demand_is_not_permanently_rejected_without_speculative_capacity(live):
    adapter, second = learned(live)
    runtime, cache, *_ = live
    context = adapter.contexts["agent"]
    with adapter._authority():
        adapter.transfers.backup(context.snapshot, owner=context.owner)
    cache.writing_check(finish_count=1)
    with adapter._authority():
        adapter.transfers.demote(context.snapshot, owner=context.owner, max_tokens=4)
    adapter._next_tick = float("inf")
    assert selection(runtime, [second]) == (second,)
    assert not adapter._inflight


def test_resident_request_can_proceed_while_peer_full_prefix_is_loading(live):
    adapter, second = learned(live)
    runtime, cache, *_ = live
    context = adapter.contexts["agent"]
    with adapter._authority():
        adapter.transfers.backup(context.snapshot, owner=context.owner)
    cache.writing_check(finish_count=1)
    with adapter._authority():
        adapter.transfers.demote(context.snapshot, owner=context.owner, max_tokens=4)
        adapter.transfers.restore(context.snapshot, owner=context.owner, available_tokens=4)
    insert(cache, [9, 8, 7, 6])
    peer = request("peer", [9, 8, 7, 6], context="peer")
    register(runtime, peer)
    assert selection(runtime, [second, peer]) == (peer,)


def test_return_keeps_unknown_context_but_explicit_retirement_discards_history(live):
    adapter, second = learned(live)
    runtime, *_ = live
    adapter.events((RuntimeEvent(
        "return", 1., Kind.RETURN, "workflow", invocation_id="agent",
        context_id="agent", attributes={"context_lifetime": "unknown"},
    ),))
    assert "agent" in adapter.contexts and adapter.history.boundaries["agent"] == 4
    adapter.events((RuntimeEvent(
        "close", 2., Kind.RETURN, "workflow", invocation_id="agent",
        context_id="agent", attributes={"context_retired": True},
    ),))
    assert not adapter.contexts and not adapter.history.boundaries
    assert second.rid not in adapter.requests


def test_workflow_end_drops_only_its_anchors_and_retains_peer(live):
    adapter, second = learned(live)
    runtime, cache, *_ = live
    insert(cache, [1, 2, 3], "other")
    peer = request("peer", [1, 2, 3], workflow="other", context="peer")
    register(runtime, peer)
    adapter.events((RuntimeEvent("end", 1., Kind.WORKFLOW_END, "workflow"),))
    assert set(adapter.contexts) == {"peer"}
    assert set(adapter.requests) == {"peer"}
    assert not adapter.priorities._paths


def test_epoch_change_discards_old_fixed_prefix_evidence(live):
    adapter, second = learned(live)
    runtime, *_ = live
    complete(runtime, second)
    third = request("third", [1, 2, 3, 4, 9], epoch=1)
    register(runtime, third)
    assert not adapter.history.boundaries
    assert adapter.contexts["agent"].owner[-1] == 1
    assert adapter.contexts["agent"].prior_input is None


def test_retraction_rebinds_attempt_and_rechecks_generated_prefix(live):
    adapter, second = learned(live)
    runtime, cache, *_ = live
    second.output_ids.extend([7, 8])
    insert(cache, list(second.origin_input_ids + second.output_ids))
    second.cache_request_handle.attempt_id = 1
    runtime.on_requests_requeued([second], is_retracted=True)
    assert adapter.requests[second.rid].key.attempt_id == 1
    assert selection(runtime, [second]) == (second,)
    assert adapter.requests[second.rid].snapshot.prefix_tokens == 7


def test_native_tagged_identity_change_cannot_be_authorized(live):
    adapter, second = learned(live)
    runtime, *_ = live
    second.cache_request_handle.attempt_id = 1
    assert selection(runtime, [second]) == ()


def test_hot_plan_does_not_repeat_tree_match_without_native_tree_change(live):
    adapter, second = learned(live)
    runtime, *_ = live
    adapter._next_tick = float("inf")
    assert selection(runtime, [second]) == (second,)
    before = adapter.counts["native_prefix_matches"]
    for _ in range(10):
        assert selection(runtime, [second]) == (second,)
    assert adapter.counts["native_prefix_matches"] == before
    assert adapter.counts["maintenance_ticks"] == 0


def test_admitted_agents_are_not_speculatively_backed_or_restored(live):
    adapter, second = learned(live)
    _, _, _, writes, loads, _ = live
    adapter._graph_dirty = False
    adapter.steps, adapter._prefetch_order = {"agent": 0}, ("agent",)
    adapter.candidate_result(second, admitted=True, result="CONTINUE")
    adapter.tick(now=0.)
    assert not writes and not loads


@pytest.mark.parametrize("control_point", ("maintenance", "selected_chunk"))
def test_speculation_preserves_known_unallocated_native_chunk_input(live, control_point):
    from sglang.srt.mem_cache.allocation import alloc_token_slots
    from sglang.srt.managers.schedule_batch import FINISH_LENGTH, Req
    from sglang.srt.managers.schedule_policy import PrefillAdder
    from sglang.srt.sampling.sampling_params import SamplingParams

    runtime, cache, allocator, _, loads, _ = live
    adapter, waiting = learned(live, tokens=tuple(range(8)))
    context = adapter.contexts["agent"]
    with adapter._authority():
        assert adapter.transfers.backup(context.snapshot, owner=context.owner)
    cache.writing_check(finish_count=1)
    with adapter._authority():
        assert adapter.transfers.demote(context.snapshot, owner=context.owner, max_tokens=8) == 8
    allocated = allocator.alloc(55)
    busy = Req(
        rid="busy", origin_input_text="", origin_input_ids=array("q", range(100, 159)),
        sampling_params=SamplingParams(temperature=1., top_k=1, max_new_tokens=1),
        extra_key="workflow", cache_salt="workflow",
        beliefkv_metadata={
            "root_workflow_id": "workflow", "invocation_id": "busy",
            "context_id": "busy", "context_epoch": 0,
        },
    )
    busy.prefix_indices = allocated
    busy.kv.kv_allocated_len = busy.kv.kv_committed_len = 55
    busy._refresh_fill_ids()
    register(runtime, busy)
    adapter.candidate_result(busy, admitted=True, result="CONTINUE")
    adapter.steps, adapter._prefetch_order = {"agent": 1}, ("agent",)
    adapter._graph_dirty = False
    if control_point == "maintenance":
        adapter.tick(now=0.)
        assert adapter.counts["native_growth_reserve_tokens"] == 5
    else:
        adder = PrefillAdder(1, cache, allocator, None, 0., 64, 4)
        adder.add_chunked_req(busy)
        assert busy.extend_range.length == 4 and adder.can_run_list == [busy]
        runtime.plan_native_prefill([waiting], running_batch=None, adder=adder)
        assert adapter.counts["maintenance_deferred_after_native_selection"] == 1
    assert not loads and allocator.available_size() == 9
    native_chunk = alloc_token_slots(cache, 4)
    assert len(native_chunk) == 4
    allocator.free(native_chunk)
    allocator.free(allocated)
    busy.finished_reason = FINISH_LENGTH(0)
    complete(runtime, busy)
    adapter.tick(now=1.)
    assert len(loads) == 1
    cache.loading_check(finish_count=1)
    assert adapter._status(context.snapshot, context.owner) == "gpu"


def test_unbudgeted_restore_reproduces_native_chunk_allocation_failure(live):
    from sglang.srt.mem_cache.allocation import alloc_token_slots

    adapter, _ = learned(live, tokens=tuple(range(8)))
    _, cache, allocator, *_ = live
    context = adapter.contexts["agent"]
    with adapter._authority():
        assert adapter.transfers.backup(context.snapshot, owner=context.owner)
    cache.writing_check(finish_count=1)
    with adapter._authority():
        assert adapter.transfers.demote(context.snapshot, owner=context.owner, max_tokens=8) == 8
    held = allocator.alloc(55)
    with adapter._authority():
        assert adapter.transfers.restore(context.snapshot, owner=context.owner, available_tokens=9)
    assert allocator.available_size() == 1 and cache.evictable_size() == 0
    with pytest.raises(RuntimeError, match="Out of memory"):
        alloc_token_slots(cache, 4)
    cache.loading_check(finish_count=1)
    allocator.free(held)


def test_optional_backup_does_not_lock_capacity_promised_to_admitted_input(live):
    adapter, _ = learned(live, tokens=tuple(range(8)))
    runtime, cache, allocator, writes, *_ = live
    held = allocator.alloc(54)
    busy = request("busy", list(range(100, 160)), context="busy")
    busy.kv = NS(kv_allocated_len=54)
    register(runtime, busy)
    adapter.candidate_result(busy, admitted=True, result="CONTINUE")
    adapter.steps, adapter._prefetch_order = {"agent": 1}, ("agent",)
    adapter._graph_dirty = False
    adapter.tick(now=0.)
    assert not writes and adapter.counts["backup_native_growth_deferred"] == 1
    allocator.free(held)
    complete(runtime, busy)
    adapter.tick(now=1.)
    assert len(writes) == 1
    cache.writing_check(finish_count=1)


def test_shared_primitive_requires_callback_authority_and_reports_depth_rejection(live):
    adapter, _ = learned(live)
    _, cache, *_ = live
    context = adapter.contexts["agent"]
    with pytest.raises(ValueError, match="native scheduler cache authority"):
        adapter.transfers.backup(context.snapshot, owner=context.owner)
    suffix = insert(cache, [1, 2, 3, 4, 5])
    adapter.transfers.max_nodes = 1
    assert adapter.transfers.capture(suffix.id, owner=context.owner) is None
    assert adapter.transfers.counts["path_depth_declined"] == 1


def test_cold_next_step_is_not_starved_by_optional_backups(live):
    adapter, _ = learned(live, tokens=(1, 2), context="a")
    adapter, _ = learned(live, tokens=(9, 8), context="z")
    _, cache, _, writes, loads, _ = live
    cold = adapter.contexts["z"]
    with adapter._authority():
        assert adapter.transfers.backup(cold.snapshot, owner=cold.owner)
    cache.writing_check(finish_count=1)
    with adapter._authority():
        assert adapter.transfers.demote(cold.snapshot, owner=cold.owner, max_tokens=2) == 2
    adapter.path_check_limit = 1
    adapter.steps, adapter._prefetch_order = {"a": 0, "z": 1}, ("a", "z")
    adapter._graph_dirty = False
    adapter.tick(now=0.)
    assert len(loads) == 1 and len(writes) == 1
    assert adapter.counts["maintenance_path_checks"] == 1
    assert set(adapter._inflight) == {"z"}


def test_same_distance_path_budget_rotates_when_host_capacity_remains_full(live):
    for agent, tokens in (("a", (1, 2)), ("b", (8, 9)), ("c", (6, 7))):
        adapter, _ = learned(live, tokens=tokens, context=agent)
    _, cache, *_ = live
    cache.cache_controller.mem_pool_host.available_size = lambda: 0
    adapter.path_check_limit = 1
    adapter.steps = dict.fromkeys(("a", "b", "c"), 0)
    adapter._prefetch_order = ("a", "b", "c")
    adapter._graph_dirty = False
    checked = []
    backup = adapter.transfers.backup

    def observe(snapshot, *, owner):
        checked.append(owner[1])
        return backup(snapshot, owner=owner)

    adapter.transfers.backup = observe
    for timestamp in (0., .2, .4):
        adapter.tick(now=timestamp)
    assert checked == ["a", "b", "c"]
    assert adapter.transfers.counts["backup_host_capacity_declined"] == 3


def test_speculative_reclaim_preserves_target_ancestry_and_uses_native_heap(live):
    runtime, cache, allocator, _, loads, _ = live
    ancestor = insert(cache, [1, 2])
    adapter, _ = learned(live)
    context = adapter.contexts["agent"]
    with adapter._authority():
        adapter.transfers.backup(context.snapshot, owner=context.owner)
    cache.writing_check(finish_count=1)
    with adapter._authority():
        assert adapter.transfers.demote(
            context.snapshot, owner=context.owner, max_tokens=2,
        ) == 2
    ancestor_value = ancestor.component_data[0].value
    donor = insert(cache, list(range(100, 100 + allocator.available_size())))
    owner = ("workflow", "donor", 0)
    donor_snapshot = adapter.transfers.capture(donor.id, owner=owner)
    with adapter._authority():
        assert adapter.transfers.backup(donor_snapshot, owner=owner)
    cache.writing_check(finish_count=1)
    assert allocator.available_size() == 0
    adapter.steps, adapter._prefetch_order = {"agent": 1}, ("agent",)
    adapter._graph_dirty = False
    adapter.tick(now=0.)
    assert len(loads) == 1
    assert ancestor.component_data[0].value is ancestor_value
    assert donor.component_data[0].value is None
    assert context.snapshot.leaf_id in cache.ongoing_load_back


def test_namespace_change_resets_stable_prefix_without_reading_future(live):
    adapter, second = learned(live)
    runtime, *_ = live
    complete(runtime, second)
    third = request("third", [1, 2, 3, 4, 5])
    third.cache_salt = "rotated"
    register(runtime, third)
    assert not adapter.history.boundaries
    assert adapter.contexts["agent"].fixed_key is None
    assert adapter.counts["namespace_history_resets"] == 1
