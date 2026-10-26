from array import array
from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.experiments.paper_baselines import environment
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from beliefkv.runtime.tokencake_baseline import TokenCakePublicAdapter
from tests.test_full_only_native_lifecycle import full_native
from tests.test_kvflow_runtime import selection
from tests.test_paper_prefix_transfers import insert
from tests.test_tokencake_runtime import submit, stage, materialize, complete


@pytest.fixture
def live(monkeypatch, full_native):
    for name, value in environment({}, "thunderagent").items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "")
    runtime, clock = NativeAdmissionRuntime(), NS(now=10., sequence=0)
    adapter = TokenCakePublicAdapter(
        runtime, generation_budget=8, context_limit=64, running_limit=4,
        prefill_token_budget=32, chunked_prefill_tokens=8, policy_block_tokens=1,
        eviction_window_tokens=16, clock=lambda: clock.now,
    )
    runtime._paper_baseline = adapter
    cache, allocator, writes, loads, commits = full_native
    cache.load_back_threshold = 1
    runtime.attach_native_cache(cache)

    def acknowledged(commit):
        commits.append(commit)
        runtime.on_native_transfer_commit(commit)

    cache.on_hicache_transfer_commit = acknowledged
    result = runtime, adapter, cache, allocator, clock
    send(result, Kind.WORKFLOW_START, invocation=None)
    for agent in ("agent", "peer", "queued"):
        send(result, Kind.INVOCATION_CREATE, invocation=agent)
    yield result
    runtime.close()


def send(live, kind, *, invocation="agent", attributes=None, **kwargs):
    runtime, _, _, _, clock = live
    clock.sequence += 1
    runtime.on_events([RuntimeEvent(
        "event:" + str(clock.sequence), float(clock.sequence), kind, "workflow",
        invocation_id=invocation, context_id=invocation,
        agent_definition_id="worker", context_epoch=0,
        attributes=attributes or {}, **kwargs,
    )])


def finished_prefix(live, *, length=8, declared=None):
    runtime, adapter, cache, _, _ = live
    leaf = insert(cache, list(range(length)))
    send(live, Kind.LLM_SUBMIT, attributes={"request_id": "first"})
    req = submit(
        live, "first", list(range(length + 1)), context="agent", declared=declared,
    )
    selection(runtime, [req])
    stage(live, req)
    materialize(live, [req])
    complete(live, req)
    send(live, Kind.LLM_RESULT, attributes={"request_id": req.rid})
    record = adapter.temporal.registry.records[req.rid]
    assert record.snapshot.snapshot.leaf_id == leaf.id
    return req, leaf, record


def active_and_waiting(live):
    runtime, *_ = live
    peer = submit(live, "peer:r", list(range(100, 139)), context="peer")
    selection(runtime, [peer])
    stage(live, peer)
    materialize(live, [peer])
    queued = submit(live, "queued:r", list(range(200, 208)), context="queued")
    return peer, queued


def start(live, *, tool="external", estimate=10., **attributes):
    send(live, Kind.TOOL_START, attributes={
        "tool_run_id": tool, "tool_family": "agentx_unknown_external",
        "estimated_duration_s": estimate, **attributes,
    })


def test_public_stall_preserves_real_prefix_after_ack_without_a_predictive_h2d(live, full_native):
    runtime, adapter, cache, allocator, _ = live
    _, leaf, record = finished_prefix(live)
    peer, _ = active_and_waiting(live)
    start(live)
    before = allocator.available_size()
    adapter.tick()
    writes, loads = full_native[2:4]
    assert len(writes) == 1 and not loads
    assert sum(len(op.device_indices) for op in writes) == 8
    assert record.pending_retention and not record.retained
    assert allocator.available_size() == before and leaf.component_data[0].lock_ref > 0
    cache.writing_check(finish_count=1)
    assert record.retained and not record.pending_retention
    assert leaf.component_data[0].host_lock_ref > 0 and leaf.component_data[0].lock_ref == 0
    assert adapter.temporal.counts["preservation_acks_settled"] == 1
    with adapter._authority():
        assert adapter.transfers.demote(
            record.snapshot.snapshot, owner=("workflow", "agent", 0), max_tokens=8,
        ) == 8
    assert allocator.available_size() == before + 8
    assert not loads
    assert adapter.status()["predictive_upload_enabled"] is False
    complete(live, peer)


def test_no_useful_waiting_work_rejects_public_preservation(live, full_native):
    _, adapter, _, _, _ = live
    finished_prefix(live)
    start(live)
    adapter.tick()
    assert not full_native[2] and not full_native[3]
    assert adapter.temporal.counts["no_waiting_demand"] == 1


@pytest.mark.parametrize("excluded", ("offload_eligible", "reusable_prefix"))
def test_explicit_application_exclusions_survive_observed_stall_intent(live, full_native, excluded):
    _, adapter, *_ = live
    finished_prefix(live, declared={excluded: False})
    peer, _ = active_and_waiting(live)
    start(live)
    adapter.tick()
    assert not full_native[2]
    assert adapter.temporal.counts["not_eligible"] == 1
    complete(live, peer)


def test_finish_before_d2h_ack_does_not_reacquire_expired_host_ownership(live):
    _, adapter, cache, _, clock = live
    _, leaf, record = finished_prefix(live)
    peer, _ = active_and_waiting(live)
    start(live)
    adapter.tick()
    clock.now += .2
    send(live, Kind.TOOL_END, attributes={"tool_run_id": "external"})
    assert record.state == "finished" and not record.retained
    assert leaf.write_through_pending_id is not None
    cache.writing_check(finish_count=1)
    assert leaf.component_data[0].host_value is not None
    assert leaf.component_data[0].host_lock_ref == 0 and not record.retained
    assert not adapter.temporal._pending
    complete(live, peer)


def test_early_tool_return_reuses_the_busy_gpu_copy_source_before_its_d2h_ack(live, full_native):
    runtime, adapter, cache, _, _ = live
    finished_prefix(live)
    peer, _ = active_and_waiting(live)
    start(live)
    adapter.tick()
    assert cache.ongoing_write_through
    send(live, Kind.TOOL_END, attributes={"tool_run_id": "external"})
    send(live, Kind.LLM_SUBMIT, attributes={"request_id": "early"})
    req = submit(live, "early", list(range(9)), context="agent")
    selection(runtime, [req])
    stage(live, req)
    materialize(live, [req])
    assert cache.ongoing_write_through and not full_native[3]
    assert adapter.capacity.counts["external_prefix_tokens_adopted"] == 8
    assert adapter.capacity.window.uncommitted == 0
    cache.writing_check(finish_count=1)
    with adapter._authority():
        adapter.capacity.refresh()
    assert adapter.capacity.window.uncommitted == 0
    complete(live, req)
    complete(live, peer)


def test_incremental_backup_keeps_old_host_prefix_protected_through_split_and_ack(live, full_native):
    from sglang.srt.mem_cache.base_prefix_cache import MatchPrefixParams
    from sglang.srt.mem_cache.radix_cache import RadixKey

    _, adapter, cache, _, _ = live
    first = insert(cache, list(range(4)))
    with adapter._authority():
        snapshot = adapter.transfers.capture(first.id, owner=("workflow", "agent", 0))
        adapter.transfers.backup(snapshot, owner=("workflow", "agent", 0))
    cache.writing_check(finish_count=1)
    _, _, record = finished_prefix(live)
    peer, _ = active_and_waiting(live)
    start(live)
    adapter.tick()
    assert [len(op.device_indices) for op in full_native[2]] == [4, 4]
    assert record.retained
    assert cache.evict_host(100) == 0
    cache.match_prefix(MatchPrefixParams(key=RadixKey(
        array("q", [0, 1]), "workflow", cache_salt="workflow",
    )))
    cache.writing_check(finish_count=1)
    assert len(record.retained) == 1 and not record.pending_retention
    lease = adapter.transfers._host_leases[next(iter(record.retained))]
    assert lease.complete and len(lease.pins) == 3
    assert cache.evict_host(100) == 0
    complete(live, peer)


def test_public_deadline_releases_all_host_pins_and_still_never_uploads(live, full_native):
    _, adapter, cache, _, clock = live
    _, leaf, record = finished_prefix(live)
    peer, _ = active_and_waiting(live)
    start(live)
    adapter.tick()
    cache.writing_check(finish_count=1)
    clock.now = record.release_deadline
    adapter.tick()
    assert record.state == "released" and record.terminal_at is None
    assert not record.retained and leaf.component_data[0].host_lock_ref == 0
    assert not full_native[3]
    clock.now += .5
    send(live, Kind.TOOL_END, attributes={"tool_run_id": "external"})
    assert record.state == "finished"
    complete(live, peer)


def test_tool_return_uses_real_native_demand_loading_after_public_preservation(live, full_native):
    from sglang.srt.managers.schedule_batch import FINISH_LENGTH, Req
    from sglang.srt.managers.schedule_policy import PrefillAdder
    from sglang.srt.sampling.sampling_params import SamplingParams

    runtime, adapter, cache, allocator, _ = live
    _, _, record = finished_prefix(live)
    peer, _ = active_and_waiting(live)
    start(live)
    adapter.tick()
    cache.writing_check(finish_count=1)
    with adapter._authority():
        assert adapter.transfers.demote(
            record.snapshot.snapshot, owner=("workflow", "agent", 0), max_tokens=8,
        ) == 8
    assert not full_native[3]
    send(live, Kind.TOOL_END, attributes={"tool_run_id": "external"})
    assert not record.retained
    send(live, Kind.LLM_SUBMIT, attributes={"request_id": "return"})
    req = Req(
        "return", None, array("q", list(range(9))), SamplingParams(max_new_tokens=1),
        extra_key="workflow", cache_salt="workflow",
        beliefkv_metadata={
            "root_workflow_id": "workflow", "invocation_id": "agent",
            "context_id": "agent", "context_epoch": 0,
        },
    )
    assert runtime.register_visible_request(req)
    selection(runtime, [req])
    running = NS(reqs=[peer], batch_size=lambda: 1)
    adder = PrefillAdder(
        page_size=1, tree_cache=cache, token_to_kv_pool_allocator=allocator,
        running_batch=NS(reqs=[], batch_size=lambda: 0),
        new_token_ratio=1., rem_input_tokens=32, rem_chunk_tokens=32,
    )
    assert runtime.allow_native_prefill_candidate(req, running_batch=running, adder=adder)
    req.init_next_round_input(cache)
    assert req.host_hit_length == 8
    verdict = adder.add_one_req(req, has_chunked_req=False, truncation_align_size=None)
    assert adder.can_run_list == [req] and len(full_native[3]) == 1
    runtime.on_prefill_candidate_result(req, admitted=True, result=verdict.name)
    cache.ready_to_load_host_cache()
    req.private_indices = allocator.alloc(1)
    req.kv.kv_allocated_len = 9
    req.lock = req.lock_receipt
    runtime.on_batch_selected(NS(reqs=[req]))
    cache.loading_check(finish_count=1)
    assert adapter.status()["predictive_upload_enabled"] is False
    req.finished_reason = FINISH_LENGTH(1)
    complete(live, req)
    complete(live, peer)


def test_parallel_tools_are_one_stall_until_the_last_observed_finish(live):
    _, adapter, cache, _, clock = live
    _, _, record = finished_prefix(live)
    peer, _ = active_and_waiting(live)
    start(live, tool="a")
    start(live, tool="b")
    adapter.tick()
    cache.writing_check(finish_count=1)
    clock.now += .2
    send(live, Kind.TOOL_END, attributes={"tool_run_id": "a"})
    assert record.state == "active" and record.retained
    assert not adapter.temporal.registry.history.history
    clock.now += .3
    send(live, Kind.TOOL_END, attributes={"tool_run_id": "b"})
    assert record.state == "finished" and not record.retained
    assert adapter.temporal.registry.history.predict(("type", "worker", "agentx_unknown_external")) == .5
    complete(live, peer)


def test_past_result_identity_wins_over_a_newer_registered_generation(live):
    _, adapter, *_ = live
    _, _, old = finished_prefix(live)
    submit(live, "newer", [0, 1, 2, 3, 10], context="agent")
    start(live)
    assert old.start.lifecycle_id == "first"
    assert adapter.temporal.registry.records["newer"].start is None


@pytest.mark.parametrize("retired", (False, True))
def test_return_closes_host_ownership_only_with_explicit_context_retirement(live, retired):
    _, adapter, cache, _, _ = live
    _, leaf, record = finished_prefix(live)
    peer, _ = active_and_waiting(live)
    start(live)
    adapter.tick()
    cache.writing_check(finish_count=1)
    send(live, Kind.RETURN, attributes={"context_retired": retired})
    assert bool(record.retained) is (not retired)
    assert (leaf.component_data[0].host_lock_ref > 0) is (not retired)
    assert leaf.component_data[0].host_value is not None
    complete(live, peer)


def test_close_unwinds_both_host_and_capacity_observers(live):
    runtime, adapter, cache, _, _ = live
    finished_prefix(live)
    peer, _ = active_and_waiting(live)
    start(live)
    adapter.tick()
    cache.writing_check(finish_count=1)
    complete(live, peer)
    assert cache.tree_core.beliefkv_full_resident_observer is not None
    runtime.close()
    assert not adapter.transfers._host_leases
    assert cache.tree_core.beliefkv_full_resident_observer is None


def test_real_native_cache_reset_invalidates_old_host_ownership_and_rebinds_queued_work(live):
    runtime, adapter, cache, allocator, _ = live
    _, _, record = finished_prefix(live)
    peer, queued = active_and_waiting(live)
    start(live)
    adapter.tick()
    cache.writing_check(finish_count=1)
    complete(live, peer)
    assert record.retained and not adapter.capacity.requests
    previous = adapter.transfers
    cache.cache_controller.reset = lambda: None
    cache.cache_controller.enable_storage = False
    cache.cache_controller.mem_pool_host.clear = lambda: None
    cache.reset()
    allocator.clear()
    selection(runtime, [queued])
    assert adapter.transfers is not previous and previous._closed
    assert adapter.temporal.transfers is adapter.transfers
    assert record.state == "reset" and record.snapshot is None
    assert not record.retained and not previous._host_leases
    assert cache.tree_core.beliefkv_full_resident_observer is adapter.capacity._observer
    stage(live, queued)
    materialize(live, [queued])
    complete(live, queued)


def test_native_abort_reason_never_creates_a_reusable_generation_snapshot(live):
    from sglang.srt.managers.schedule_batch import FINISH_ABORT

    runtime, adapter, *_ = live
    req = submit(live, "failed", [1, 2, 3], context="agent")
    req.finished_reason = FINISH_ABORT("native OOM", status_code=500)
    req.done = True
    assert runtime.on_batch_completed(NS(reqs=[req])) == ("failed",)
    record = adapter.temporal.registry.records["failed"]
    assert record.state == "error" and record.snapshot is None


def test_private_future_interval_fields_do_not_seed_the_cold_start_forecast(live):
    _, adapter, *_ = live
    finished_prefix(live)
    send(live, Kind.TOOL_START, attributes={
        "tool_run_id": "external", "tool_family": "agentx_unknown_external",
        "duration_ms": 900_000., "remaining_ms": 900_000., "raw_gap_s": 900.,
    })
    assert adapter.temporal.registry.records["first"].predicted_duration == 1.


@pytest.mark.parametrize("complete_configuration", (False, True))
def test_real_runtime_constructor_requires_explicit_serving_and_temporal_parameters(monkeypatch, full_native, complete_configuration):
    for name, value in environment({}, "thunderagent").items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", TokenCakePublicAdapter.policy_name)
    parameters = {
        "GENERATION_BUDGET": "8", "CONTEXT_LIMIT": "64", "RUNNING_LIMIT": "4",
        "PREFILL_TOKEN_BUDGET": "32", "CHUNKED_PREFILL_TOKENS": "8",
        "POLICY_BLOCK_TOKENS": "1", "EVICTION_WINDOW_TOKENS": "16",
    }
    for name, value in parameters.items():
        variable = "BELIEFKV_TOKENCAKE_" + name
        if complete_configuration:
            monkeypatch.setenv(variable, value)
        else:
            monkeypatch.delenv(variable, raising=False)
    if not complete_configuration:
        with pytest.raises(ValueError, match="requires all declared"):
            NativeAdmissionRuntime()
        return
    runtime = NativeAdmissionRuntime()
    try:
        runtime.attach_native_cache(full_native[0])
        state = runtime._paper_baseline.status()
        assert state["policy"] == TokenCakePublicAdapter.policy_name
        assert state["generation_budget"] == 8
        assert state["temporal_parameters"]["prefill_token_budget"] == 32
        assert not runtime._custom_kv_management
        assert not runtime.enable_join_parent_priority and not runtime.enable_resident_first
        assert runtime._agent_admission is None
    finally:
        runtime.close()
