"""TokenCake paper lifecycle on actual full-prompt native radix requests."""

from array import array
from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from tests.test_full_only_native_lifecycle import full_native
from tests.test_kvflow_runtime import selection
from tests.test_tokencake_paper_runtime import decode_rate, live
from tests.test_tokencake_public_runtime import active_and_waiting, send, start
from tests.test_tokencake_runtime import complete as complete_plain
from tests.test_tokencake_runtime import free_native, materialize


def submit_session(live, identity, tokens, *, context="agent", session="native-agent"):
    from sglang.srt.managers.schedule_batch import Req
    from sglang.srt.sampling.sampling_params import SamplingParams

    runtime, _, cache, *_ = live
    req = Req(
        rid=identity, origin_input_text="", origin_input_ids=array("q", tokens),
        sampling_params=SamplingParams(temperature=1., top_k=1, max_new_tokens=1),
        session_id=session, extra_key=None, cache_salt=None,
        beliefkv_metadata={
            "root_workflow_id": "workflow", "invocation_id": context,
            "context_id": context, "context_epoch": 0,
            "full_prompt_replay_guaranteed": True,
        },
    )
    req.session_generation = cache.ensure_session_generation(session)
    req.private_indices = req.lock = None
    assert runtime.register_visible_request(req)
    return req


def complete_session(live, req):
    from sglang.srt.managers.schedule_batch import FINISH_LENGTH

    free_native(live, req)
    req.lock_receipt = None
    req.finished_reason = FINISH_LENGTH(0)
    assert live[0].on_batch_completed(NS(reqs=[req])) == (req.rid,)


def stage_session(live, req):
    from sglang.srt.managers.schedule_policy import PrefillAdder

    runtime, _, cache, allocator, _ = live
    assert runtime.allow_native_prefill_candidate(req, running_batch=None, adder=None)
    req.init_next_round_input(cache, cow_mamba=False)
    adder = PrefillAdder(1, cache, allocator, None, 0., 64, 64, max_running_requests=4)
    result = adder.add_one_req(req, has_chunked_req=False, truncation_align_size=None)
    assert req in adder.can_run_list
    req.lock = req.lock_receipt
    runtime.on_prefill_candidate_result(req, admitted=True, result=result.name)
    return len(req.prefix_indices)


def finished_session(live):
    from test_beliefkv_terminal_capacity import insert_session

    runtime, adapter, cache, *_ = live
    leaf, generation = insert_session(cache, list(range(8)), "native-agent")
    send(live, Kind.LLM_SUBMIT, attributes={"request_id": "first-session"})
    req = submit_session(live, "first-session", list(range(9)))
    assert selection(runtime, [req]) == (req,)
    assert stage_session(live, req) == 8
    materialize(live, [req])
    complete_session(live, req)
    send(live, Kind.LLM_RESULT, attributes={"request_id": req.rid})
    record = adapter.temporal.registry.records[req.rid]
    assert record.snapshot.snapshot.leaf_id == leaf.id
    assert record.snapshot.snapshot.namespace == (None, None)
    assert cache.session_refs.snapshot_session_leaf_anchors("native-agent", generation)
    return req, leaf, record, generation


def cold_session(live):
    _, adapter, cache, allocator, _ = live
    _, leaf, record, generation = finished_session(live)
    peer, queued = active_and_waiting(live)
    decode_rate(live, peer)
    start(live)
    before = allocator.available_size()
    adapter.tick()
    assert record.pending_retention
    assert leaf.component_data[0].value is not None
    cache.writing_check(finish_count=1)
    assert leaf.component_data[0].value is None and record.retained
    assert allocator.available_size() == before + 8
    assert adapter.temporal.last_first_fit == queued.rid
    return peer, leaf, record, generation


def test_actual_session_request_is_admitted_with_observed_native_prefix(live):
    runtime, adapter, _, allocator, _ = live
    req, _, _, generation = finished_session(live)
    assert req.session_generation == generation and req.session is None
    assert req.beliefkv_metadata["full_prompt_replay_guaranteed"] is True
    assert not adapter.capacity.requests and not adapter.capacity.window.used
    assert allocator.available_size() == 56
    assert not runtime.physical_disabled


def test_two_native_sessions_share_capacity_once_and_keep_both_native_owners(live):
    from test_beliefkv_terminal_capacity import insert_session

    runtime, adapter, cache, allocator, _ = live
    first, generation = insert_session(cache, [1, 2, 3, 4], "native-agent")
    peer, peer_generation = insert_session(cache, [1, 2, 9, 10], "native-peer")
    assert allocator.available_size() == 58
    a = submit_session(live, "a", [1, 2, 3, 4, 5])
    b = submit_session(
        live, "b", [1, 2, 9, 10, 11], context="peer", session="native-peer",
    )
    assert selection(runtime, [a, b]) == (a, b)
    assert stage_session(live, a) == stage_session(live, b) == 4
    assert adapter.capacity.window.uncommitted == 40
    assert allocator.available_size() == 58
    materialize(live, [a, b])
    assert allocator.available_size() == 56
    assert adapter.capacity.window.uncommitted == 40
    complete_session(live, a)
    assert first.parent.component_data[0].value is not None
    assert cache.session_refs.snapshot_session_leaf_anchors("native-agent", generation)
    assert cache.session_refs.snapshot_session_leaf_anchors("native-peer", peer_generation)
    assert peer.component_data[0].value is not None
    complete_session(live, b)
    assert not adapter.capacity.requests and not adapter.capacity.extents
    assert allocator.available_size() == 58


def test_session_paper_upload_grants_use_real_prefix_slots_and_resume_same_context(live, full_native):
    runtime, adapter, cache, _, clock = live
    peer, _, record, generation = cold_session(live)
    checkpoint = adapter.temporal.checkpoints["first-session"]
    clock.now = checkpoint.finish_at - .05
    copied = []
    while sum(copied) < 8:
        previous = len(full_native[3])
        adapter.tick()
        assert len(full_native[3]) > previous
        copied.extend(len(op.host_indices) for op in full_native[3][previous:])
        assert record.retained
        cache.loading_check(finish_count=1)
    assert copied == [4, 2, 1, 1]
    send(live, Kind.TOOL_END, attributes={"tool_run_id": "external"})
    send(live, Kind.LLM_SUBMIT, attributes={"request_id": "next-session"})
    req = submit_session(live, "next-session", list(range(9)))
    assert selection(runtime, [req]) == (req,)
    assert stage_session(live, req) == 8
    assert not adapter.capacity.reservations and not adapter.temporal.checkpoints
    assert cache.session_refs._session_generations["native-agent"] == generation
    materialize(live, [req])
    complete_session(live, req)
    complete_plain(live, peer)
    assert not runtime.physical_disabled


def test_tool_return_during_session_upload_preserves_host_source_until_real_admission(live, full_native):
    runtime, adapter, cache, _, clock = live
    peer, leaf, _, generation = cold_session(live)
    checkpoint = adapter.temporal.checkpoints["first-session"]
    clock.now = checkpoint.finish_at - .05
    adapter.tick()
    assert cache.ongoing_load_back and leaf.component_data[0].host_lock_ref > 0
    send(live, Kind.TOOL_END, attributes={"tool_run_id": "external"})
    send(live, Kind.LLM_SUBMIT, attributes={"request_id": "returning-session"})
    req = submit_session(live, "returning-session", list(range(9)))
    assert selection(runtime, [req]) == (req,)
    assert not runtime.allow_native_prefill_candidate(req, running_batch=None, adder=None)
    assert checkpoint.host_leases and leaf.component_data[0].host_lock_ref > 0
    cache.loading_check(finish_count=1)
    assert selection(runtime, [req]) == (req,)
    assert stage_session(live, req) == 8
    assert not adapter.temporal.checkpoints and not adapter.capacity.reservations
    assert all(lease not in adapter.transfers._host_leases for lease in checkpoint.host_leases)
    assert len(full_native[3][-1].host_indices) == 4
    assert full_native[3][-1].beliefkv_command_id is None
    cache.cache_controller.start_loading()
    cache.loading_check(finish_count=1)
    assert cache.session_refs._session_generations["native-agent"] == generation
    materialize(live, [req])
    complete_session(live, req)
    complete_plain(live, peer)
    assert not runtime.physical_disabled
