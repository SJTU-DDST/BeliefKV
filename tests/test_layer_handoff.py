"""Native cache ownership/admission with event/copy stand-ins, not GPU proof."""

import asyncio
from array import array
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
import torch

from beliefkv.experiments.internal_comparison import MODES, arm_environment
from beliefkv.experiments.paper_baselines import environment as paper_environment
from tests.test_full_only_native_lifecycle import full_native
from tests.test_tool_offload_before_admission import enable_early, configuration
from tests.test_tool_policy_adaptation import demand_runtime


def test_model_probe_reader_waits_for_complete_jsonl_records(tmp_path):
    from scripts.qualify_layer_handoff_model import records

    path = tmp_path / "observations.jsonl"
    assert records(path) == []
    path.write_text('{"event":"old"}\n{"event":"pending"')
    assert records(path) == [{"event": "old"}]
    path.write_text('{"event":"old"}\n{"event":"new"}\n')
    assert records(path) == [{"event": "old"}, {"event": "new"}]
    path.write_text('{"broken":\n')
    with pytest.raises(ValueError):
        records(path)


def test_model_probe_requires_actual_matching_delivered_evidence(tmp_path, monkeypatch):
    from scripts import qualify_layer_handoff_model as qualifier

    path = tmp_path / "observations.jsonl"
    path.write_text('{"request":"other"}\n')
    delivered = []

    async def flush(_interval):
        delivered.append(True)
        path.write_text('{"request":"other"}\n{"request":"selected"}\n')

    monkeypatch.setattr(qualifier.asyncio, "sleep", flush)
    ready = lambda rows: any(row.get("request") == "selected" for row in rows)
    result = asyncio.run(qualifier.wait_evidence(path, ready, lambda: None))
    assert delivered == [True]
    assert result[-1] == {"request": "selected"}
    with pytest.raises(TimeoutError):
        asyncio.run(qualifier.wait_evidence(
            path, lambda _rows: False, lambda: None, timeout=0.,
        ))


@pytest.fixture
def native_handoff(demand_runtime, monkeypatch):
    from sglang.srt.mem_cache import beliefkv_layer_handoff as native

    runtime, offloader, cache, allocator, donor, request, _ = demand_runtime
    enable_early(demand_runtime)
    donor.component_data[0].host_value = None
    cache.tree_core._update_duplicate_tracking(donor)
    consumer = cache.tree_core.node_by_id(
        dict(cache.inspect_beliefkv_reentry(request)["component_leaves"])[0][0][0],
    )
    cc = cache.cache_controller
    host_owned, host_released, calls = [], [], []

    class Event:
        def __init__(self):
            self.done = False

        def record(self, stream=None):
            calls.append(("record", self, stream))

        def query(self):
            return self.done

        def synchronize(self):
            assert self.done, "CPU test must explicitly complete its copy stand-in"

    module = NS(Event=Event, current_stream=lambda: NS(
        wait_event=lambda event: calls.append(("wait", event)),
    ))
    monkeypatch.setattr(native, "device_module", module)
    monkeypatch.setattr(native, "supported", lambda _cache: True)

    def allocate_host(size):
        indices = torch.arange(1000, 1000 + size)
        host_owned.append(indices)
        return indices

    cc.mem_pool_device_allocator = allocator
    cc.mem_pool_host.alloc = allocate_host
    cc.mem_pool_host.free = lambda indices: host_released.append(indices)
    cc.move_indices = lambda host, device: (host, device)
    cc.evict_device = lambda indices: (allocator.free(indices), len(indices))[1]
    cc.layer_num = 3
    counter = cc.layer_done_counter
    for item in counter.events:
        item.start_event = Event()

    def update():
        counter.producer_index = (counter.producer_index + 1) % counter.num_counters
        return counter.producer_index

    counter.update_producer = update
    cc.mem_pool_host.anchor_entry.host_pool.layer_num = 3

    def submit(a, b, **kwargs):
        assert a.device_pool is b.device_pool
        assert a.device_indices.data_ptr() == b.device_indices.data_ptr()
        return NS(
            donor=NS(start_event=Event(), finish_event=Event(), timing_enabled=False),
            consumer=NS(start_event=Event(), finish_event=Event(), timing_enabled=False),
            consumer_layer_ready=tuple(Event() for _ in range(kwargs["layer_num"])),
        )

    cc.l2_transfer_engine = NS(submit_layer_handoff=Mock(side_effect=submit))
    runtime.layer_handoff_enabled = True
    runtime._capacity_waiting_queue = [request]
    offloader.consumer = request, runtime.visible["consumer"]
    offloader._dispatch_stage = "before_native_admission"
    args = dict(
        donor_node_id=donor.id, donor_creation_time=donor.creation_time,
        donor_session_id="s", donor_session_generation=cache.session_refs._session_generations["s"],
        consumer_node_id=consumer.id, consumer_creation_time=consumer.creation_time,
        consumer_session_id="consumer-session",
        consumer_session_generation=cache.session_refs._session_generations["consumer-session"],
        reserve_tokens=2, authorize=lambda *_: True,
    )
    return NS(
        native=native, runtime=runtime, offloader=offloader, cache=cache,
        allocator=allocator, donor=donor, consumer=consumer, request=request,
        args=args, host_owned=host_owned, host_released=host_released,
    )


def test_recycled_extent_is_never_free_and_tail_frees_only_after_real_ack(native_handoff):
    f = native_handoff
    before = f.allocator.available_size()
    indices = f.donor.component_data[0].value.clone()
    result = f.native.begin(f.cache, **f.args)
    assert result["donor_tokens"] == 32 and result["reused_tokens"] == 16
    assert f.allocator.available_size() == before == 4
    assert f.donor.component_data[0].value is None
    assert torch.equal(f.consumer.component_data[0].value, indices[:16])
    assert f.donor.write_through_pending_id == f.donor.id
    assert f.consumer.load_back_pending_id == f.consumer.id
    assert f.native.blocks_load(f.cache, f.donor.id)
    assert not f.cache.load_back(f.donor.id)
    assert f.cache.ready_to_load_host_cache() == result["consumer_index"]
    assert f.cache._beliefkv_layer_handoff is not None
    f.cache.sanity_check()
    with pytest.raises(RuntimeError, match="drain"):
        f.cache.reset()
    transaction = f.cache._beliefkv_layer_handoff
    transaction.completion.donor.finish_event.done = True
    f.cache.writing_check(finish_count=1)
    assert f.allocator.available_size() == 20
    assert f.donor.component_data[0].host_lock_ref == 0
    assert not f.native.blocks_load(f.cache, f.donor.id)
    assert f.cache._beliefkv_layer_handoff is transaction
    transaction.completion.consumer.finish_event.done = True
    for event in transaction.completion.consumer_layer_ready:
        event.done = True
    f.cache.loading_check(finish_count=1)
    f.native.reap(f.cache)
    assert f.cache._beliefkv_layer_handoff is None
    assert f.consumer.component_data[0].lock_ref == 0
    assert f.consumer.component_data[0].host_lock_ref == 0
    f.cache.sanity_check()


@pytest.mark.parametrize("cause", (
    "device_lock", "host_lock", "shared", "stale_donor", "stale_consumer",
    "closed_donor", "closed_consumer", "host_missing", "already_backed",
    "busy_write", "busy_load", "busy_ack", "too_little_growth_space",
    "too_large_consumer", "authorization", "unready_producer",
))
def test_refusal_preserves_native_ownership_and_capacity(native_handoff, cause):
    f = native_handoff
    a, b, cc = f.donor.component_data[0], f.consumer.component_data[0], f.cache.cache_controller
    if cause == "device_lock":
        a.lock_ref += 1
    elif cause == "host_lock":
        a.host_lock_ref += 1
    elif cause == "shared":
        a.session_ids.add("other")
        a.session_ref += 1
    elif cause == "stale_donor":
        f.args["donor_session_generation"] += 1
    elif cause == "stale_consumer":
        f.args["consumer_creation_time"] += 1
    elif cause.startswith("closed_"):
        f.cache.session_refs._closed_session_ids[
            "s" if cause == "closed_donor" else "consumer-session"
        ] = None
    elif cause == "host_missing":
        b.host_value = None
    elif cause == "already_backed":
        a.host_value = a.value.clone()
    elif cause == "busy_write":
        cc.write_queue.append(object())
    elif cause == "busy_load":
        cc.load_queue.append(object())
    elif cause == "busy_ack":
        cc.ack_write_queue.append(object())
    elif cause == "too_little_growth_space":
        f.args["reserve_tokens"] = 5
    elif cause == "too_large_consumer":
        b.host_value = torch.arange(33)
    elif cause == "authorization":
        f.args["authorize"] = lambda *_: False
    else:
        cc.layer_done_counter.events[0].finish_event.query = lambda: False
    old_a, old_b, free = a.value, b.value, f.allocator.available_size()
    assert f.native.begin(f.cache, **f.args) is None
    assert a.value is old_a and b.value is old_b
    assert f.allocator.available_size() == free
    assert getattr(f.cache, "_beliefkv_layer_handoff", None) is None
    cc.l2_transfer_engine.submit_layer_handoff.assert_not_called()
    assert len(f.host_owned) == len(f.host_released)


def test_unrelated_resident_prefill_does_not_wait_for_the_handoff(native_handoff):
    f = native_handoff
    result = f.native.begin(f.cache, **f.args)
    hot = next(iter(f.cache.components[0]._session_leaves["hot"]))
    index = result["consumer_index"]
    assert f.cache.beliefkv_prefill_load_consumer([NS(last_node=hot.id)], index) == -1
    assert f.cache.beliefkv_prefill_load_consumer(
        [NS(last_node=f.consumer.id)], index,
    ) == index


def test_completed_consumer_does_not_release_donor_tail_early(native_handoff):
    f = native_handoff
    assert f.native.begin(f.cache, **f.args) is not None
    transaction = f.cache._beliefkv_layer_handoff
    transaction.completion.consumer.finish_event.done = True
    f.cache.loading_check(finish_count=1)
    f.native.reap(f.cache)
    assert f.allocator.available_size() == 4
    assert f.native.blocks_load(f.cache, f.donor.id)
    transaction.completion.donor.finish_event.done = True
    f.cache.writing_check(finish_count=1)
    assert f.cache._beliefkv_layer_handoff is None
    assert f.allocator.available_size() == 20


def split_prefix(f, tokens):
    from sglang.srt.mem_cache.base_prefix_cache import MatchPrefixParams
    from sglang.srt.mem_cache.radix_cache import RadixKey

    return f.cache.match_prefix(MatchPrefixParams(key=RadixKey(array("q", tokens))))


def test_segmented_paths_are_one_transaction_with_no_duplicate_capacity(native_handoff):
    f = native_handoff
    for count in (8, 16, 24):
        split_prefix(f, range(1, count + 1))
    split_prefix(f, range(101, 109))
    result = f.native.begin(f.cache, **f.args)
    assert result["donor_nodes"] == result["consumer_nodes"] == 2
    assert result["donor_tokens"] == result["reused_tokens"] == 16
    assert result["reserved_tail_tokens"] == 0
    transaction = f.cache._beliefkv_layer_handoff
    transaction.completion.donor.finish_event.done = True
    f.cache.writing_check(finish_count=1)
    transaction.completion.consumer.finish_event.done = True
    f.cache.loading_check(finish_count=1)
    assert f.cache._beliefkv_layer_handoff is None
    assert f.allocator.available_size() == 4
    assert len(split_prefix(f, range(1, 33)).device_indices) == 16
    assert len(split_prefix(f, range(101, 117)).device_indices) == 16
    f.cache.sanity_check()


def test_split_during_dma_keeps_source_blocked_and_native_ack_ownership(native_handoff):
    f = native_handoff
    assert f.native.begin(f.cache, **f.args)
    split_prefix(f, range(1, 17))
    source_prefix = f.donor.parent
    assert f.native.blocks_load(f.cache, source_prefix.id)
    split_prefix(f, range(101, 109))
    transaction = f.cache._beliefkv_layer_handoff
    transaction.completion.donor.finish_event.done = True
    f.cache.writing_check(finish_count=1)
    assert source_prefix.write_through_pending_id is None
    transaction.completion.consumer.finish_event.done = True
    f.cache.loading_check(finish_count=1)
    assert f.cache._beliefkv_layer_handoff is None
    assert f.consumer.parent.load_back_pending_id is None
    assert f.allocator.available_size() == 20
    f.cache.sanity_check()


def test_real_selected_consumer_uses_handoff_without_logical_priority_change(native_handoff):
    from beliefkv.runtime.layer_handoff import try_handoff
    from tests.test_reference_thunder_admission import state

    f = native_handoff
    before = state(f.runtime._agent_admission)
    key = f.runtime.context_sessions["ctx-tool"]
    step = NS(node_id=f.donor.id, creation_time=f.donor.creation_time)
    assert try_handoff(f.offloader, key, step), dict(f.runtime.counts)
    assert state(f.runtime._agent_admission) == before
    assert f.runtime.counts["layer_handoff_submitted"] == 1
    assert f.allocator.available_size() == 4


def test_selected_handoff_reserves_actual_input_and_preserves_growth(native_handoff):
    from beliefkv.runtime.layer_handoff import try_handoff

    f = native_handoff
    f.runtime._native_input_reserve = 10
    f.runtime._current_residency_budget = lambda: NS(
        source="native_next_prefill", reserve_full_tokens=12,
    )
    f.offloader._admission_adder = NS(can_run_list=[], dllm_config=None)
    assert try_handoff(
        f.offloader, f.runtime.context_sessions["ctx-tool"],
        NS(node_id=f.donor.id, creation_time=f.donor.creation_time),
    ), dict(f.runtime.counts)
    assert f.runtime.counts["layer_handoff_selected_input_reserve"] == 1
    assert f.allocator.available_size() == 4
    assert f.cache._beliefkv_layer_handoff is not None
    f.cache.sanity_check()


def test_actual_prefill_plan_binds_and_releases_its_current_adder(native_handoff):
    f = native_handoff
    f.runtime._native_input_reserve = 10
    f.runtime._current_residency_budget = lambda: NS(
        source="native_next_prefill", reserve_full_tokens=12,
    )
    adder = NS(can_run_list=[], dllm_config=None, rem_input_tokens=10, rem_chunk_tokens=10)
    f.runtime.plan_native_prefill([f.request], running_batch=NS(reqs=[]), adder=adder)
    assert f.runtime.counts["layer_handoff_submitted"] == 1, dict(f.runtime.counts)
    assert f.offloader._admission_adder is None
    assert f.allocator.available_size() == 4
    f.cache.sanity_check()


@pytest.mark.parametrize("tail", ("complete", "diverged", "partial"))
def test_handoff_accounts_for_observed_decode_tail_without_loading_a_foreign_prefix(
    native_handoff, tail,
):
    from beliefkv.runtime.layer_handoff import try_handoff

    f = native_handoff
    split_prefix(f, range(101, 115))
    tokens = list(range(101, 117))
    if tail == "diverged":
        tokens[-1] = -1
    elif tail == "partial":
        tokens.pop()
    f.request.origin_input_ids = array("q", tokens)
    observed = f.cache.inspect_beliefkv_reentry(f.request)
    assert observed["checkpoint_tokens"] == observed["missing_full_tokens"] == 14
    result = try_handoff(
        f.offloader, f.runtime.context_sessions["ctx-tool"],
        NS(node_id=f.donor.id, creation_time=f.donor.creation_time),
    )
    assert result is (tail == "complete"), dict(f.runtime.counts)
    assert f.allocator.available_size() == 4
    if result:
        assert f.runtime.counts["layer_handoff_reused_tokens"] == 16
        assert f.offloader.action_detail["beneficiary_restore_bytes_budgeted"] == (
            16 * f.runtime._residency_pool_bytes[0]
        )
    else:
        assert getattr(f.cache, "_beliefkv_layer_handoff", None) is None
    f.cache.sanity_check()


@pytest.mark.parametrize("cause", ("selected_chunk", "unknown_adder", "growth_space"))
def test_selected_input_reserve_never_spends_another_batch_or_growth_budget(
    native_handoff, cause,
):
    from beliefkv.runtime.layer_handoff import try_handoff

    f = native_handoff
    f.runtime._native_input_reserve = 10
    f.runtime._current_residency_budget = lambda: NS(
        source="native_next_prefill", reserve_full_tokens=15 if cause == "growth_space" else 12,
    )
    f.offloader._admission_adder = (
        None if cause == "unknown_adder"
        else NS(can_run_list=[object()] if cause == "selected_chunk" else [], dllm_config=None)
    )
    f.runtime._opportunity_writer = Mock()
    assert not try_handoff(
        f.offloader, f.runtime.context_sessions["ctx-tool"],
        NS(node_id=f.donor.id, creation_time=f.donor.creation_time),
    )
    assert f.allocator.available_size() == 4
    assert getattr(f.cache, "_beliefkv_layer_handoff", None) is None
    refusal = f.runtime._opportunity_writer.record.call_args.args[0]
    assert refusal["event"] == "layer_kv_handoff_refused"
    assert refusal["device_free_tokens"] == 4
    assert refusal["required_reserve_tokens"] > 4
    f.cache.sanity_check()


@pytest.mark.parametrize("cause", ("maintenance", "disabled", "short_tool", "inactive_consumer"))
def test_runtime_handoff_requires_current_prediction_and_actual_admission(native_handoff, cause):
    from beliefkv.runtime.layer_handoff import try_handoff

    f = native_handoff
    if cause == "maintenance":
        f.offloader._dispatch_stage = "maintenance"
    elif cause == "disabled":
        f.runtime.layer_handoff_enabled = False
    elif cause == "inactive_consumer":
        f.runtime._agent_admission.policy.programs["consumer"].active = False
    else:
        hint = f.runtime.tool_wait_hints["ctx-tool"]
        f.runtime.tool_wait_hints["ctx-tool"] = replace(
            hint, wait_p10_ms=1., wait_p50_ms=2., wait_p90_ms=3.,
        )
    assert not try_handoff(
        f.offloader, f.runtime.context_sessions["ctx-tool"],
        NS(node_id=f.donor.id, creation_time=f.donor.creation_time),
    )
    assert f.allocator.available_size() == 4


@pytest.mark.parametrize("mode", MODES)
def test_internal_controls_keep_handoff_full_only(mode):
    settings = arm_environment({**configuration(), "LAYER_HANDOFF": "1"}, mode)
    assert settings["BELIEFKV_LAYER_HANDOFF"] == str(int(mode == "full"))


@pytest.mark.parametrize("method", ("thunderagent", "thunderagent_acting_decay", "kvflow_observed_graph"))
def test_paper_controls_never_inherit_handoff(method):
    assert paper_environment(
        {**configuration(), "LAYER_HANDOFF": "1"}, method,
    )["BELIEFKV_LAYER_HANDOFF"] == "0"
