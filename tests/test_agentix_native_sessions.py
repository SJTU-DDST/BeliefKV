"""Agentix checkpoints for actual full-prompt native radix sessions."""

from array import array
from types import SimpleNamespace as NS

import pytest

from beliefkv.runtime.agentix_capacity import AgentixNativeCapacity
from tests.test_agentix_adapter import adapter
from tests.test_agentix_swap import ack, swap
from tests.test_full_only_native_lifecycle import full_native


def running_session(cache, identity="agent", *, session=None, output=(80, 81)):
    from sglang.srt.managers.schedule_batch import Req
    from sglang.srt.sampling.sampling_params import SamplingParams

    session = session or "native-" + identity
    req = Req(
        rid=identity, origin_input_text="", origin_input_ids=array("q", (1, 2, 3, 4)),
        sampling_params=SamplingParams(temperature=1., top_k=1, max_new_tokens=8),
        session_id=session, extra_key=None, cache_salt=None,
        beliefkv_metadata={
            "root_workflow_id": "workflow", "invocation_id": identity,
            "context_id": identity, "context_epoch": 0, "full_prompt_replay_guaranteed": True,
        },
    )
    req.session_generation = cache.ensure_session_generation(session)
    req.output_ids.extend(output)
    assert cache.req_to_token_pool.alloc([req]) is not None
    count = req.seqlen - 1
    indices = cache.token_to_kv_pool_allocator.alloc(count)
    assert indices is not None
    cache.req_to_token_pool.write((req.kv.req_pool_idx, slice(0, count)), indices)
    req.kv.kv_committed_len = req.kv.kv_allocated_len = count
    req.last_node = cache.root_node_handle()
    return req


def test_native_session_checkpoint_restores_through_real_prefill_consumer(swap):
    from sglang.srt.managers.schedule_policy import PrefillAdder

    actor, cache, allocator, _, loads, _, _ = swap
    req = running_session(cache)
    namespace = req.session_id, req.session_generation
    original = tuple(req.origin_input_ids), tuple(req.output_ids), req.sampling_params
    before = allocator.available_size()
    actor.prepare([req], running_batch=NS(reqs=[req]))
    assert actor.commit() is None
    assert allocator.available_size() == before
    ack(cache)
    assert actor.commit()["demoted_full_tokens"] == 5
    pin = actor.paused[req.rid]
    assert cache.session_refs.snapshot_session_leaf_anchors(*namespace) is not None
    path = actor.transfers.resolve(pin.snapshot, owner=actor._owner(pin.proof))
    assert path and sum(len(node.key) for node in path) == 5
    assert all(node.component_data[0].host_lock_ref > 0 for node in path)
    cache.load_back_threshold = 1
    req.init_next_round_input(cache, cow_mamba=False)
    adder = PrefillAdder(1, cache, allocator, None, 0., 64, 64, max_running_requests=4)
    adder.add_one_req(req, has_chunked_req=False, truncation_align_size=None)
    assert req in adder.can_run_list and len(req.prefix_indices) == 5
    assert cache.req_to_token_pool.alloc([req]) is not None
    private = allocator.alloc(req.extend_range.length)
    assert private is not None
    import torch
    indices = torch.cat((req.prefix_indices, private))
    cache.req_to_token_pool.write((req.kv.req_pool_idx, slice(0, len(indices))), indices)
    req.kv.kv_allocated_len = req.kv.kv_committed_len = len(indices)
    assert actor.resumed(req) and not actor.paused
    assert pin.lease not in actor.transfers._host_leases
    assert loads and cache.tree_core.node_by_id(pin.leaf).component_data[0].host_lock_ref > 0
    cache.cache_controller.start_loading()
    cache.loading_check(finish_count=1)
    assert (req.session_id, req.session_generation) == namespace
    assert (tuple(req.origin_input_ids), tuple(req.output_ids), req.sampling_params) == original


def test_two_native_sessions_share_checkpoint_pages_and_keep_both_owners(swap):
    actor, cache, allocator, writes, _, _, _ = swap
    first = running_session(cache, "first")
    second = running_session(cache, "second", output=(90, 91))
    prepared = actor.prepare([first, second], running_batch=NS(reqs=[first, second]))
    assert prepared["unique_checkpoint_tokens"] == 6
    assert sum(len(operation.device_indices) for operation in writes) == 6
    before = allocator.available_size()
    ack(cache)
    assert actor.commit()["demoted_full_tokens"] == 6
    assert allocator.available_size() == before + 6
    assert len(actor.paused) == 2
    for req in (first, second):
        anchors = cache.session_refs.snapshot_session_leaf_anchors(
            req.session_id, req.session_generation,
        )
        assert anchors is not None
        assert cache.session_refs._session_generations[req.session_id] == req.session_generation
        assert actor.release(req.rid)


def test_preexisting_native_session_anchors_survive_checkpoint_demotion(swap):
    from test_beliefkv_terminal_capacity import insert_session

    actor, cache, *_ = swap
    _, generation = insert_session(cache, [1, 2], "native-agent")
    req = running_session(cache)
    before = cache.session_refs.snapshot_session_leaf_anchors(req.session_id, generation)
    assert before is not None and any(leaves for _, leaves in before)
    actor.prepare([req], running_batch=NS(reqs=[req]))
    ack(cache)
    actor.commit()
    after = cache.session_refs.snapshot_session_leaf_anchors(req.session_id, generation)
    assert after == before
    assert actor.paused[req.rid].proof.namespace[2:] == (req.session_id, generation)


@pytest.mark.parametrize("fault", (
    "missing_full_prompt", "false_full_prompt", "closed", "stale_generation",
    "boolean_generation", "reconstruction", "embeddings", "position_override",
))
@pytest.mark.parametrize("path", ("swap", "capacity"))
def test_unsupported_session_transport_is_rejected_before_native_context_mutation(swap, fault, path):
    actor, cache, allocator, writes, _, _, _ = swap
    req = running_session(cache)
    if fault == "missing_full_prompt":
        del req.beliefkv_metadata["full_prompt_replay_guaranteed"]
    elif fault == "false_full_prompt":
        req.beliefkv_metadata["full_prompt_replay_guaranteed"] = False
    elif fault == "closed":
        cache.session_refs._closed_session_ids[req.session_id] = None
    elif fault == "stale_generation":
        cache.session_refs._session_generations[req.session_id] += 1
    elif fault == "boolean_generation":
        req.session_generation = True
    elif fault == "reconstruction":
        req.session_params = NS()
    elif fault == "embeddings":
        req.input_embeds = [[0.]]
    else:
        req.positional_embed_overrides = object()
    before = allocator.available_size()
    if path == "swap":
        action = lambda: actor.prepare([req], running_batch=NS(reqs=[req]))
    else:
        ledger = AgentixNativeCapacity(actor.transfers, context_limit=64, decode_growth_tokens=4)
        action = lambda: ledger.prepare([req])
    with pytest.raises(ValueError, match="session|token|full.prompt|plain running|observed native"):
        action()
    assert allocator.available_size() == before and not writes and not actor.pending


def test_native_session_generation_is_rechecked_before_checkpoint_release(swap):
    actor, cache, allocator, _, _, _, _ = swap
    req = running_session(cache)
    actor.prepare([req], running_batch=NS(reqs=[req]))
    ack(cache)
    before = allocator.available_size()
    cache.session_refs._session_generations[req.session_id] += 1
    with pytest.raises(ValueError, match="session"):
        actor.commit()
    assert req.kv.holds_kv and allocator.available_size() == before and not actor.paused


def test_native_session_calls_still_share_the_root_workflow_service_account(adapter):
    state = adapter
    state.runtime.attach_native_scheduler(state.scheduler)
    requests = [running_session(state.cache, name) for name in ("parent", "child")]
    for req in requests:
        assert state.runtime.register_visible_request(req)
    assert {call.program_id for call in state.paper.policy.calls.values()} == {"workflow"}
    assert state.paper.calls.counts["registered"] == 2
    assert state.paper.status()["complete_baseline"] is False
