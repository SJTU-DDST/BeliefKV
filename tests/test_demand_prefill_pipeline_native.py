"""Actual native requests, admission, cache locks and ACK lifecycle on CPU."""

from array import array
from collections import Counter
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.runtime.demand_prefill_pipeline import create_pipeline
from beliefkv.runtime.sglang_v0520_admission import _request_key
from tests.test_full_only_native_lifecycle import full_native


@pytest.fixture
def staged_native(full_native, request):
    from sglang.srt.managers.schedule_batch import Req
    from sglang.srt.managers.schedule_policy import PrefillAdder
    from sglang.srt.sampling.sampling_params import SamplingParams
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, _, loads, commits = full_native
    cache.load_back_threshold = 1
    cache.beliefkv_cold_full_owner_validator = lambda owners: True
    options = getattr(request, "param", 1)
    count = options["members"] if isinstance(options, dict) else options
    select = options.get("select", True) if isinstance(options, dict) else True
    members, leaves = [], []
    for index in range(count):
        begin = 1 + index * 20
        session = "parent" if index == 0 else f"parent-{index}"
        leaf, generation = insert_session(cache, list(range(begin, begin + 8)), session)
        assert cache.prepare_beliefkv_spawn_full(
            session_id=session, session_generation=generation,
        )["issued"]
        cache.writing_check(finish_count=1)
        free = allocator.available_size()
        assert cache.reclaim_beliefkv_handoff_capacity(
            full_tokens=free + 8, mamba_slots=0,
        ) == {0: 8}
        req = Req(
            rid=f"native-b-{index}", origin_input_text="",
            origin_input_ids=array("q", range(begin, begin + 9)),
            sampling_params=SamplingParams(max_new_tokens=2), session_id=session,
            beliefkv_metadata={
                "root_workflow_id": "root", "invocation_id": f"agent-b-{index}",
                "context_id": f"context-b-{index}", "context_epoch": 0,
            },
        )
        req.session_generation = generation
        members.append(req)
        leaves.append(leaf)
    keys = tuple(_request_key(req) for req in members)
    req, key, leaf = members[0], keys[0], leaves[0]
    runtime = NS(
        demand_prefill_pipeline_enabled=True, internal_comparison_mode="full",
        _paper_baseline=None, _agent_admission=NS(allows=lambda candidate: candidate in keys),
        visible={member.rid: key for member, key in zip(members, keys)},
        _terminal=lambda _: False, counts=Counter(),
        _opportunity_writer=NS(record=Mock()),
    )
    running = NS(
        reqs=[NS(output_ids=[], sampling_params=NS(max_new_tokens=4),
                 finished=lambda: False, to_finish=None)],
        is_prefill_only=False,
    )

    def adder():
        return PrefillAdder(
            page_size=1, tree_cache=cache, token_to_kv_pool_allocator=allocator,
            running_batch=NS(reqs=[]), new_token_ratio=1., rem_input_tokens=32,
            rem_chunk_tokens=32,
        )

    first = adder()
    if select:
        for member in members:
            member.init_next_round_input(cache)
            assert member.host_hit_length == 8 and len(member.prefix_indices) == 0
            first.add_one_req(member, has_chunked_req=False, truncation_align_size=None)
            assert member.host_loaded_length == 8
        assert first.can_run_list == members
        assert len(loads) == len(members)
        assert req.lock_receipt is not None
    assert req.kv.req_pool_idx is None
    controller = cache.cache_controller
    complete = NS(ready=False)
    native_start = controller.start_loading

    def start_loading(**kwargs):
        if not controller.load_queue:
            return -1
        result = native_start(**kwargs)
        ack = controller.ack_load_queue[-1]
        ack.finish_event.query = lambda: complete.ready
        counter = controller.layer_done_counter
        counter.events[counter.producer_index].finish_event = NS(query=lambda: complete.ready)
        ack.submit_ts_ms = 100.
        return result

    controller.start_loading = Mock(side_effect=start_loading)
    pipeline = create_pipeline(runtime, cache)
    if select:
        assert pipeline.stage(
            req, adder=first, running_batch=running, candidate_count=len(members),
            native_load_queue_was_empty=True, chunked_req=None,
        )
    return NS(
        cache=cache, allocator=allocator, loads=loads, commits=commits, req=req,
        key=key, runtime=runtime, first=first, adder=adder, pipeline=pipeline,
        running=running, waiting=members.copy(), complete=complete, leaf=leaf,
        members=members, leaves=leaves,
    )


def test_real_native_restore_ack_rematch_and_readmission_keep_one_lock(staged_native):
    f = staged_native
    data = f.leaf.component_data[0]
    assert data.lock_ref == 2  # Native DMA plus the staged admission.
    assert f.pipeline.defer(f.waiting, f.running)
    assert data.host_lock_ref > 0
    f.complete.ready = True
    f.cache.loading_check(finish_count=1)
    assert data.lock_ref == 1 and data.host_lock_ref == 0
    assert not f.pipeline.defer(f.waiting, f.running)
    assert data.lock_ref == 0
    f.req.init_next_round_input(f.cache)
    assert f.req.host_hit_length == 0 and len(f.req.prefix_indices) == 8
    again = f.adder()
    again.add_one_req(f.req, has_chunked_req=False, truncation_align_size=None)
    assert again.can_run_list == [f.req]
    assert len(f.loads) == 1 and not f.cache.cache_controller.load_queue
    assert data.lock_ref == 1
    f.cache.dec_lock_ref(f.req.last_node, f.req.lock_receipt)
    assert data.lock_ref == 0
    assert [row.direction for row in f.commits] == ["d2h", "h2d"]
    assert f.commits[-1].actual_bytes == 8 * 64
    assert not f.commits[-1].child_commits
    assert f.req.cached_tokens_host == 0


@pytest.mark.parametrize("cancel", ("abort", "removed", "close", "epoch"))
def test_real_native_cancellation_keeps_dma_pins_until_ack(staged_native, cancel):
    f = staged_native
    if cancel == "abort":
        f.pipeline.on_abort(NS(abort_all=False, rid=f.req.rid), exact=True)
    elif cancel == "close":
        f.pipeline.close()
    else:
        if cancel == "epoch":
            f.req.beliefkv_metadata["context_epoch"] += 1
        else:
            f.waiting.clear()
        assert not f.pipeline.defer(f.waiting, f.running)
    assert f.pipeline.pending is None
    assert f.leaf.component_data[0].lock_ref == 1
    assert f.leaf.component_data[0].host_lock_ref > 0
    assert f.leaf.load_back_pending_id is not None
    f.complete.ready = True
    f.cache.loading_check(finish_count=1)
    assert f.leaf.component_data[0].lock_ref == 0
    assert f.leaf.component_data[0].host_lock_ref == 0
    assert not f.cache.ongoing_load_back
    f.pipeline.close()
    assert len(f.loads) == 1


def test_real_native_fallback_before_ack_reacquires_own_lock_and_fence(staged_native):
    f = staged_native
    f.running.reqs.clear()
    assert not f.pipeline.defer(f.waiting, f.running)
    assert f.leaf.component_data[0].lock_ref == 1
    f.req.init_next_round_input(f.cache)
    again = f.adder()
    again.add_one_req(f.req, has_chunked_req=False, truncation_align_size=None)
    assert again.can_run_list == [f.req]
    assert f.leaf.component_data[0].lock_ref == 2
    batch = NS(reqs=[f.req], hicache_consumer_index=-1)
    f.pipeline.bind(batch)
    assert batch.hicache_consumer_index == 0
    assert f.pipeline.pending is None
    f.complete.ready = True
    f.cache.loading_check(finish_count=1)
    assert f.leaf.component_data[0].lock_ref == 1
    f.cache.dec_lock_ref(f.req.last_node, f.req.lock_receipt)
    assert f.leaf.component_data[0].lock_ref == 0
    assert len(f.loads) == 1


def test_cancelled_owner_keeps_native_layer_fence_for_a_shared_prefix_user(staged_native):
    from sglang.srt.managers.schedule_batch import Req
    from sglang.srt.sampling.sampling_params import SamplingParams

    f = staged_native
    f.pipeline.on_abort(NS(abort_all=False, rid=f.req.rid), exact=True)
    other = Req(
        rid="shared-consumer", origin_input_text="",
        origin_input_ids=array("q", range(1, 10)),
        sampling_params=SamplingParams(max_new_tokens=2), session_id="parent",
    )
    other.session_generation = f.req.session_generation
    other.init_next_round_input(f.cache)
    assert len(other.prefix_indices) == 8 and other.host_hit_length == 0
    adder = f.adder()
    adder.add_one_req(other, has_chunked_req=False, truncation_align_size=None)
    assert adder.can_run_list == [other]
    consumer = f.cache.ready_to_load_host_cache()
    assert consumer == 0
    assert f.cache.beliefkv_prefill_load_consumer([other], consumer) == 0
    assert len(f.loads) == 1
    f.complete.ready = True
    f.cache.loading_check(finish_count=1)
    assert f.cache.ready_to_load_host_cache() == -1
    f.cache.dec_lock_ref(other.last_node, other.lock_receipt)
    assert f.leaf.component_data[0].lock_ref == 0


def test_staged_demand_does_not_attach_an_unrelated_layer_fence_to_resident_decode(staged_native):
    from test_beliefkv_terminal_capacity import insert_session

    f = staged_native
    resident, _ = insert_session(f.cache, [20, 21, 22, 23], "resident")
    f.running.reqs[0].last_node = resident.id
    f.running.hicache_consumer_index = 0
    assert not f.complete.ready
    assert f.pipeline.defer(f.waiting, f.running)
    assert f.running.hicache_consumer_index == -1
    assert f.runtime.counts["demand_restore_unrelated_decode_fences_cleared"] == 1
    assert f.leaf.load_back_pending_id is not None
    f.pipeline.close()
    f.complete.ready = True
    f.cache.loading_check(finish_count=1)


def test_real_native_staging_journal_reconciles_with_untagged_commit(staged_native):
    from scripts.audit_staged_native_restore import inspect

    f = staged_native
    journals = [call.args[0] for call in f.runtime._opportunity_writer.record.call_args_list]
    f.complete.ready = True
    f.cache.loading_check(finish_count=1)
    commit = f.commits[-1]
    result = inspect(journals, [{
        "direction": commit.direction, "status": commit.status,
        "submit_ts_ms": commit.submit_ts_ms, "node_ids": list(commit.node_ids),
        "actual_bytes": commit.actual_bytes, "num_tokens_by_pool": dict(commit.num_tokens_by_pool),
        "tagged_child_commits": list(commit.child_commits),
    }], bytes_per_token=64)
    assert result["counts"]["native_h2d_bytes"] == 512
    assert result["counts"]["matched_staging_episodes"] == 1
    assert not f.pipeline.defer(f.waiting, f.running)


@pytest.mark.parametrize("staged_native", (2,), indirect=True)
def test_two_actual_native_members_restore_and_readmit_together(staged_native):
    from scripts.audit_staged_native_restore import inspect

    f = staged_native
    assert len(f.pipeline.pending.members) == 2
    assert all(leaf.component_data[0].lock_ref == 2 for leaf in f.leaves)
    assert f.pipeline.defer(f.waiting, f.running)
    journals = [call.args[0] for call in f.runtime._opportunity_writer.record.call_args_list]
    assert journals[0]["native_batch_size"] == len(journals[0]["requests"]) == 2
    f.complete.ready = True
    f.cache.loading_check(finish_count=1)
    assert not f.pipeline.defer(f.waiting, f.running)
    adder = f.adder()
    for member in f.members:
        member.init_next_round_input(f.cache)
        adder.add_one_req(member, has_chunked_req=False, truncation_align_size=None)
    assert adder.can_run_list == f.members
    assert len(f.loads) == 2
    assert all(leaf.component_data[0].lock_ref == 1 for leaf in f.leaves)
    commit = f.commits[-1]
    result = inspect(journals, [{
        "direction": commit.direction, "status": commit.status,
        "submit_ts_ms": commit.submit_ts_ms, "node_ids": list(commit.node_ids),
        "actual_bytes": commit.actual_bytes, "num_tokens_by_pool": dict(commit.num_tokens_by_pool),
        "tagged_child_commits": list(commit.child_commits),
    }], bytes_per_token=64)
    assert result["counts"]["native_h2d_bytes"] == 16 * 64
    for member in f.members:
        f.cache.dec_lock_ref(member.last_node, member.lock_receipt)
    assert all(leaf.component_data[0].lock_ref == 0 for leaf in f.leaves)
