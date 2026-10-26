from array import array
from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import EventConfidence, RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.experiments.paper_baselines import RUNTIME_ARMS, environment
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from beliefkv.runtime.tokencake_baseline import TokenCakeAdmissionAdapter
from tests.test_full_only_native_lifecycle import full_native
from tests.test_kvflow_runtime import request, selection
from tests.test_paper_prefix_transfers import insert


@pytest.fixture
def live(monkeypatch, full_native):
    for name, value in environment({}, "thunderagent").items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "")
    runtime = NativeAdmissionRuntime()
    clock = NS(now=10.)
    adapter = TokenCakeAdmissionAdapter(
        runtime, generation_budget=8, context_limit=64, clock=lambda: clock.now,
    )
    runtime._paper_baseline = adapter
    cache, allocator, writes, loads, commits = full_native
    cache.load_back_threshold = 1
    runtime.attach_native_cache(cache)

    def acknowledged(commit):
        commits.append(commit)
        runtime.on_native_transfer_commit(commit)

    cache.on_hicache_transfer_commit = acknowledged
    yield runtime, adapter, cache, allocator, clock
    runtime.close()


def submit(live, identity, tokens, *, context=None, declared=None):
    runtime, adapter, *_ = live
    req = request(identity, tokens, context=context or identity)
    req.done = False
    req.finished = lambda: req.done
    req.kv = NS(kv_allocated_len=0, kv_committed_len=0)
    req.last_node = None
    req.private_indices = req.lock = None
    req.sampling_params = NS(max_new_tokens=1)
    if declared is not None:
        req.beliefkv_metadata["tokencake"] = declared
    assert runtime.register_visible_request(req)
    return req


def stage(live, req, *, extend=None):
    from sglang.srt.mem_cache.base_prefix_cache import MatchPrefixParams
    from sglang.srt.mem_cache.radix_cache import RadixKey

    runtime, _, cache, *_ = live
    assert runtime.allow_native_prefill_candidate(
        req, running_batch=NS(reqs=[]), adder=None,
    )
    match = cache.match_prefix(MatchPrefixParams(key=RadixKey(
        req.origin_input_ids, req.extra_key, cache_salt=req.cache_salt,
        limit=req._compute_max_prefix_len(len(req.origin_input_ids)),
    )))
    req.last_node = match.last_device_node
    req.lock = cache.inc_lock_ref(req.last_node).to_dec_params()
    prefix = len(match.device_indices)
    req.extend_range = NS(length=extend if extend is not None else len(req.origin_input_ids) - prefix)
    runtime.on_prefill_candidate_result(req, admitted=True, result="CONTINUE")
    return prefix


def materialize(live, requests):
    runtime, adapter, _, allocator, _ = live
    for req in requests:
        claim = adapter.capacity.requests[req.rid]
        private = sum(units for _, units in claim.private)
        req.private_indices = allocator.alloc(private) if private else None
        req.kv.kv_allocated_len = sum(
            adapter.capacity.extents[identity].tokens for identity in claim.prefix
        ) + private
        req.kv.kv_committed_len = req.kv.kv_allocated_len
    runtime.on_batch_selected(NS(reqs=requests))


def free_native(live, req):
    _, _, cache, allocator, _ = live
    if req.lock is not None:
        cache.dec_lock_ref(req.last_node, req.lock)
        req.lock = None
    if req.private_indices is not None:
        allocator.free(req.private_indices)
        req.private_indices = None
    req.kv.kv_allocated_len = req.kv.kv_committed_len = 0


def complete(live, req):
    free_native(live, req)
    req.done = True
    assert live[0].on_batch_completed(NS(reqs=[req])) == (req.rid,)


def test_runtime_sequential_guards_reserve_the_entire_selected_batch_before_native_allocation(live):
    runtime, adapter, cache, allocator, _ = live
    insert(cache, [0, 1, 2, 3])
    a = submit(live, "a", [*range(24)])
    b = submit(live, "b", [0, 1, 2, 3, *range(100, 120)])
    c = submit(live, "c", [0, 1, 2, 3, 200, 201])
    assert selection(runtime, [a, b, c]) == (a, b, c)
    stage(live, a)
    stage(live, b)
    assert sum(claim.pending_private for claim in adapter.capacity.requests.values()) == 40
    assert adapter.capacity.window.uncommitted == 4
    assert allocator.available_size() == 60
    assert not runtime.allow_native_prefill_candidate(c, running_batch=NS(reqs=[]), adder=None)
    assert adapter.counts["guard_wall_ns"] >= adapter.counts["guard_max_wall_ns"] > 0
    materialize(live, [a, b])
    assert adapter.capacity.window.uncommitted == 4
    assert allocator.available_size() == 20
    assert not any(claim.pending_private for claim in adapter.capacity.requests.values())
    complete(live, a)
    assert runtime.allow_native_prefill_candidate(c, running_batch=NS(reqs=[b]), adder=None)
    complete(live, b)
    assert not adapter.capacity.requests and not adapter.capacity.window.used
    assert adapter.capacity.window.uncommitted == 64


def test_replay_recorded_output_limit_changes_neither_admission_nor_capacity(live):
    runtime, adapter, *_ = live
    req = submit(live, "a", list(range(24)))
    selection(runtime, [req])
    assert runtime.allow_native_prefill_candidate(req, running_batch=None, adder=None)
    before = adapter.requests[req.rid].probe
    req.sampling_params.max_new_tokens = 1_000_000
    assert runtime.allow_native_prefill_candidate(req, running_batch=None, adder=None)
    assert adapter.requests[req.rid].probe.target_tokens == before.target_tokens == 32
    stage(live, req)
    materialize(live, [req])
    assert adapter.capacity.window.growth == {"a": 8}
    complete(live, req)


def test_native_capacity_defer_does_not_stick_after_real_completion(live):
    runtime, adapter, *_ = live
    a = submit(live, "a", list(range(32)))
    b = submit(live, "b", list(range(100, 132)))
    selection(runtime, [a, b])
    stage(live, a)
    materialize(live, [a])
    assert not runtime.allow_native_prefill_candidate(b, running_batch=NS(reqs=[a]), adder=None)
    assert adapter.counts["capacity_candidates_deferred"] == 1
    complete(live, a)
    stage(live, b)
    materialize(live, [b])
    complete(live, b)
    assert adapter.history["unknown"].completed == 2


def test_native_retraction_handoff_precedes_eviction_and_preserves_original_arrival(live):
    from sglang.srt.mem_cache.base_prefix_cache import EvictParams

    runtime, adapter, cache, _, clock = live
    leaf = insert(cache, [1, 2, 3, 4])
    req = submit(live, "a", [1, 2, 3, 4, 5])
    selection(runtime, [req])
    stage(live, req)
    materialize(live, [req])
    arrived = adapter.requests["a"].arrived
    with adapter._authority():
        snapshot = adapter.transfers.capture(leaf.id, owner=("workflow", "a", 0))
        adapter.transfers.backup(snapshot, owner=("workflow", "a", 0))
    cache.writing_check(finish_count=1)
    runtime.before_native_request_release(req, reason="decode_retraction")
    assert not adapter.capacity.requests and not adapter.capacity.extents
    free_native(live, req)
    # Native eviction now must not find stale live TokenCake ownership.
    cache.evict(EvictParams(num_tokens=64))
    assert leaf.component_data[0].value is None
    clock.now += 100.
    req.cache_request_handle.attempt_id += 1
    runtime.on_requests_requeued([req], is_retracted=True)
    assert adapter.requests["a"].arrived == arrived
    assert adapter.requests["a"].key.attempt_id == 1
    selection(runtime, [req])
    stage(live, req)
    materialize(live, [req])
    complete(live, req)
    assert adapter.history["unknown"].samples == 1
    assert adapter.history["unknown"].preemptions == 1
    assert adapter.history["unknown"].completed == 1


def test_missing_native_pre_release_hook_is_detected_on_requeue(live):
    runtime, _, *_ = live
    req = submit(live, "a", [1, 2, 3])
    selection(runtime, [req])
    stage(live, req)
    materialize(live, [req])
    with pytest.raises(ValueError, match="pre-release ownership"):
        runtime.on_requests_requeued([req], is_retracted=True)
    runtime.before_native_request_release(req, reason="decode_retraction")
    free_native(live, req)


@pytest.mark.parametrize("change", ("neither", "capacity", "completion"))
def test_retracted_request_waits_for_real_capacity_or_a_completion_before_retry(live, change):
    runtime, adapter, cache, allocator, _ = live
    insert(cache, [1, 2, 3, 4])
    a = submit(live, "a", [1, 2, 3, 4, 5, 6])
    b = submit(live, "b", [1, 2, 3, 4, 9, 10])
    selection(runtime, [a, b])
    stage(live, a)
    stage(live, b)
    materialize(live, [a, b])
    external = allocator.alloc(8)
    runtime.before_native_request_release(a, reason="decode_retraction")
    free_native(live, a)
    a.cache_request_handle.attempt_id += 1
    runtime.on_requests_requeued([a], is_retracted=True)
    snapshot = adapter.requests["a"].retry_capacity
    assert snapshot[0] == adapter.capacity.window.uncommitted
    selection(runtime, [a])
    assert not runtime.allow_native_prefill_candidate(a, running_batch=NS(reqs=[b]), adder=None)
    b_done = False
    if change == "capacity":
        allocator.free(external)
        external = None
    elif change == "completion":
        complete(live, b)
        b_done = True
    else:
        selection(runtime, [a])
        assert not runtime.allow_native_prefill_candidate(a, running_batch=NS(reqs=[b]), adder=None)
        complete(live, b)
        b_done = True
    stage(live, a)
    materialize(live, [a])
    assert adapter.requests["a"].retry_capacity is None
    complete(live, a)
    if not b_done:
        complete(live, b)
    if external is not None:
        allocator.free(external)


def test_selected_batch_cannot_silently_skip_the_sequential_admission_guard(live):
    runtime, _, *_ = live
    req = submit(live, "a", [1, 2, 3])
    selection(runtime, [req])
    with pytest.raises(ValueError, match="bypassed its capacity guard"):
        runtime.on_prefill_candidate_result(req, admitted=True, result="CONTINUE")
    with pytest.raises(ValueError, match="without native admission"):
        runtime.on_batch_selected(NS(reqs=[req]))


def test_abort_releases_capacity_without_requiring_future_requeue(live):
    runtime, adapter, *_ = live
    req = submit(live, "a", [1, 2, 3])
    selection(runtime, [req])
    stage(live, req)
    materialize(live, [req])
    runtime.on_abort_request(NS(rid="a", abort_all=False))
    free_native(live, req)
    assert not adapter.requests and not adapter.capacity.requests
    with adapter._authority():
        adapter.capacity.refresh()
    assert adapter.capacity.window.uncommitted == 64


def test_active_shared_prefix_affinity_is_bounded_by_the_public_score_band(live):
    runtime, adapter, cache, allocator, _ = live
    insert(cache, list(range(16)))
    active = submit(live, "active", list(range(20)))
    selection(runtime, [active])
    stage(live, active)
    materialize(live, [active])
    external = allocator.alloc(24)
    resident = submit(live, "resident", list(range(17)))
    modest = submit(live, "modest", [100, 101], declared={"importance": 1.})
    urgent = submit(live, "urgent", [200, 201], declared={"importance": 20.})
    assert selection(runtime, [modest, resident, urgent]) == (urgent, resident, modest)
    allocator.free(external)
    complete(live, active)


@pytest.mark.parametrize("confidence", (EventConfidence.OBSERVED_EXACT, EventConfidence.INFERRED))
def test_observed_join_inherits_live_priority_but_no_future_graph_is_read(live, confidence):
    runtime, adapter, *_ = live
    runtime.on_events([
        RuntimeEvent("start", 0., Kind.WORKFLOW_START, "workflow"),
        *(
            RuntimeEvent(
                "create:" + agent, 1., Kind.INVOCATION_CREATE, "workflow",
                invocation_id=agent, context_id=agent,
                agent_definition_id="specialist",
            ) for agent in ("a", "b")
        ),
    ])
    a = submit(live, "a:r", [1, 2, 3], context="a", declared={"importance": 2.})
    b = submit(live, "b:r", [4, 5, 6], context="b")
    selection(runtime, [a, b])
    assert adapter.scores["a:r"] > adapter.scores["b:r"]
    assert not adapter.requests["b:r"].metadata.application_max_depth
    runtime.on_events([RuntimeEvent(
        "join", 2., Kind.JOIN_CREATE, "workflow",
        join_id="observed", member_invocation_ids=("a", "b"),
        confidence=confidence,
    )])
    selection(runtime, [a, b])
    if confidence is EventConfidence.INFERRED:
        assert adapter.scores["a:r"] > adapter.scores["b:r"]
        assert not adapter.join_groups
        assert not runtime.graph.joins
    else:
        assert adapter.scores["a:r"] == adapter.scores["b:r"]
    assert adapter.agent_scores["a:r"] > adapter.agent_scores["b:r"]
    assert adapter.requests["a:r"].metadata.agent_type == "specialist"
    assert adapter.status()["future_information_used"] is False


def test_partition_clock_includes_decode_steps_without_double_counting_the_prefill_plan(live):
    runtime, adapter, *_ = live
    req = submit(live, "a", [1, 2, 3])
    selection(runtime, [req])
    stage(live, req)
    materialize(live, [req])
    assert adapter.counts["partition_updates"] == 1
    for _ in range(499):
        adapter.tick()
    assert adapter.status()["scheduler_steps"] == 500
    assert adapter.counts["partition_updates"] == 2
    selection(runtime, [])
    assert adapter.status()["scheduler_steps"] == 500
    assert adapter.counts["partition_updates"] == 2
    assert adapter.counts["control_wall_ns"] >= adapter.counts["control_max_wall_ns"] > 0
    complete(live, req)


def test_idle_native_cache_does_not_inflate_token_cake_active_usage_or_reserved_fraction(live):
    runtime, adapter, cache, *_ = live
    insert(cache, list(range(60)))
    req = submit(live, "a", [100, 101])
    selection(runtime, [req])
    stage(live, req)
    materialize(live, [req])
    for _ in range(499):
        adapter.tick()
    assert adapter.capacity.window.free == 62
    assert adapter.reserve_ratio == .05
    complete(live, req)


def test_native_tree_reset_rebinds_spatial_ownership_and_tracks_new_shared_splits(live):
    runtime, adapter, cache, allocator, _ = live
    previous = adapter.capacity
    insert(cache, [100, 101])
    cache.tree_core.reset()
    allocator.clear()
    selection(runtime, [])
    assert adapter.capacity is not previous
    assert cache.tree_core.beliefkv_full_resident_observer is adapter.capacity._observer
    assert adapter.counts["native_cache_resets"] == 1
    insert(cache, [1, 2, 3, 4])
    a = submit(live, "a", [1, 2, 3, 4, 5, 6])
    selection(runtime, [a])
    stage(live, a)
    materialize(live, [a])
    b = submit(live, "b", [1, 2, 9, 10])
    selection(runtime, [b])
    stage(live, b)
    materialize(live, [b])
    assert adapter.capacity.counts["split_extents"] == 1
    assert sum(adapter.capacity.window.used.values()) == 8
    complete(live, a)
    complete(live, b)


def test_native_reset_cannot_silently_discard_an_active_capacity_claim(live):
    runtime, adapter, cache, _, _ = live
    req = submit(live, "a", [1, 2, 3])
    selection(runtime, [req])
    stage(live, req)
    materialize(live, [req])
    previous = adapter.capacity
    cache.tree_core.reset()
    with pytest.raises(ValueError, match="discarded active request ownership"):
        selection(runtime, [])
    assert adapter.capacity is previous and req.rid in adapter.capacity.requests
def test_each_decode_victim_uses_current_shared_ownership_and_host_recompute_cost(live):
    runtime, adapter, cache, _, _ = live
    shared = insert(cache, [1, 2, 3, 4])
    a = submit(live, "a", [1, 2, 3, 4, 5, 6])
    b = submit(live, "b", [1, 2, 3, 4, 9, 10])
    selection(runtime, [a, b])
    stage(live, a)
    stage(live, b)
    materialize(live, [a, b])
    batch = NS(reqs=[a, b], new_tokens_required_next_decode=lambda _: 60)
    with adapter._authority():
        assert adapter._released_and_cost(a) == (2, 6)
    assert runtime.select_native_decode_victim(batch, (0, 1)) == 0
    runtime.before_native_request_release(a, reason="decode_retraction")
    free_native(live, a)
    with adapter._authority():
        assert adapter._released_and_cost(b) == (6, 6)
        snapshot = adapter.transfers.capture(shared.id, owner=("workflow", "b", 0))
        assert adapter.transfers.backup(snapshot, owner=("workflow", "b", 0))
    cache.writing_check(finish_count=1)
    with adapter._authority():
        assert adapter._released_and_cost(b) == (6, 2)
    complete(live, b)


@pytest.mark.parametrize("important,near_finish,expected", (
    (False, False, 2), (True, False, 0), (False, True, 0),
))
def test_pressure_victim_balances_released_capacity_with_score_and_completion(live, important, near_finish, expected):
    import torch

    runtime, _, cache, allocator, _ = live
    insert(cache, [1, 2, 3, 4])
    a = submit(live, "a", [1, 2, 3, 4, 5, 6])
    b = submit(live, "b", [1, 2, 3, 4, 9, 10])
    c = submit(
        live, "c", list(range(100, 112)),
        declared={"importance": 40. if important else 0.},
    )
    requests = [a, b, c]
    selection(runtime, requests)
    for req in requests:
        stage(live, req)
    materialize(live, requests)
    if near_finish:
        c.output_ids.extend(range(200, 208))
        extra = allocator.alloc(8)
        c.private_indices = torch.cat((c.private_indices, extra))
        c.kv.kv_allocated_len += 8
        c.kv.kv_committed_len += 8
        runtime.on_batch_selected(NS(reqs=requests))
    batch = NS(reqs=requests, new_tokens_required_next_decode=lambda _: 60)
    assert runtime.select_native_decode_victim(batch, (0, 1, 2)) == expected
    for req in requests:
        complete(live, req)


def test_still_loading_prefix_defers_without_disabling_resident_peers(live):
    runtime, adapter, cache, _, _ = live
    leaf = insert(cache, [1, 2, 3, 4])
    with adapter._authority():
        snapshot = adapter.transfers.capture(leaf.id, owner=("workflow", "a", 0))
        adapter.transfers.backup(snapshot, owner=("workflow", "a", 0))
    cache.writing_check(finish_count=1)
    with adapter._authority():
        adapter.transfers.demote(snapshot, owner=("workflow", "a", 0), max_tokens=4)
        assert adapter.transfers.restore(snapshot, owner=("workflow", "a", 0), available_tokens=4)
    a = submit(live, "a", [1, 2, 3, 4, 5])
    b = submit(live, "b", [9, 8, 7])
    selection(runtime, [a, b])
    assert not runtime.allow_native_prefill_candidate(a, running_batch=None, adder=None)
    assert runtime.allow_native_prefill_candidate(b, running_batch=None, adder=None)
    cache.loading_check(finish_count=1)
    assert runtime.allow_native_prefill_candidate(a, running_batch=None, adder=None)


def test_host_ack_is_forwarded_even_when_our_own_physical_ledger_is_disabled(live):
    runtime, adapter, *_ = live
    seen = []
    adapter.transfer_completed = seen.append
    runtime.physical_disabled = True
    marker = object()
    assert runtime.on_native_transfer_commit(marker) == ()
    assert seen == [marker]


def test_spatial_component_is_not_the_registered_full_paper_adapter(live):
    _, adapter, *_ = live
    assert "tokencake_admission" not in RUNTIME_ARMS
    assert "tokencake_public_observed" not in RUNTIME_ARMS
    assert RUNTIME_ARMS["tokencake_paper_observed"] == "tokencake_paper_observed_metadata"
    assert adapter.status()["complete_baseline"] is False
    assert adapter.status()["generation_budget_source"] == "separately_declared_serving_budget"
    assert adapter.status()["generation_budget"] == 8


@pytest.mark.parametrize("host_only", (False, True))
def test_real_native_request_and_adder_bind_the_guard_to_actual_admission_fields(live, host_only):
    from sglang.srt.managers.schedule_batch import Req
    from sglang.srt.managers.schedule_policy import PrefillAdder
    from sglang.srt.sampling.sampling_params import SamplingParams

    runtime, adapter, cache, allocator, _ = live
    leaf = insert(cache, [1, 2, 3, 4])
    owner = ("workflow", "agent", 0)
    if host_only:
        with adapter._authority():
            snapshot = adapter.transfers.capture(leaf.id, owner=owner)
            adapter.transfers.backup(snapshot, owner=owner)
        cache.writing_check(finish_count=1)
        with adapter._authority():
            assert adapter.transfers.demote(snapshot, owner=owner, max_tokens=4) == 4
    req = Req(
        "real", None, array("q", [1, 2, 3, 4, 5]), SamplingParams(max_new_tokens=1),
        extra_key="workflow", cache_salt="workflow",
        beliefkv_metadata={
            "root_workflow_id": "workflow", "invocation_id": "agent",
            "context_id": "agent", "context_epoch": 0,
        },
    )
    assert runtime.register_visible_request(req)
    selection(runtime, [req])
    running = NS(reqs=[], batch_size=lambda: 0)
    adder = PrefillAdder(
        page_size=1, tree_cache=cache, token_to_kv_pool_allocator=allocator,
        running_batch=running, new_token_ratio=1.,
        rem_input_tokens=32, rem_chunk_tokens=32,
    )
    assert runtime.allow_native_prefill_candidate(req, running_batch=running, adder=adder)
    req.init_next_round_input(cache)
    result = adder.add_one_req(req, has_chunked_req=False, truncation_align_size=None)
    assert adder.can_run_list == [req]
    runtime.on_prefill_candidate_result(req, admitted=True, result=result.name)
    assert req.extend_range.length == 1 and len(req.prefix_indices) == 4
    assert adapter.capacity.requests[req.rid].pending_private == 1
    assert sum(adapter.capacity.window.used.values()) == 5
    assert adapter.capacity.window.uncommitted == 51
    if host_only:
        assert cache.ready_to_load_host_cache() == 0
    private = allocator.alloc(1)
    req.kv.kv_allocated_len = 5
    runtime.on_batch_selected(NS(reqs=[req]))
    assert adapter.capacity.window.uncommitted == 51
    if host_only:
        cache.loading_check(finish_count=1)
    runtime.before_native_request_release(req, reason="decode_retraction")
    cache.dec_lock_ref(req.last_node, req.lock_receipt)
    allocator.free(private)
