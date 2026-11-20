"""Bounded staged demand restore across a long native FULL radix path."""

from array import array
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.runtime.demand_restore_chunk import DemandRestoreChunk
from beliefkv.runtime.sglang_v0520_admission import _request_key
from tests.test_demand_restore_capacity import _backup, bind_host_slots
from tests.test_full_only_native_lifecycle import full_native


SOURCE_NODES = 260
EXTENT_TOKENS = 4
ANCHOR_TOKENS = 4
BOUNDARY_NODES = 257
BOUNDARY_TOKENS = BOUNDARY_NODES * EXTENT_TOKENS

pytestmark = pytest.mark.parametrize("full_native", (2048,), indirect=True)


def _path(anchor, leaf):
    nodes = []
    while leaf is not anchor:
        nodes.append(leaf)
        leaf = leaf.parent
    return nodes[::-1]


def _load_params(req):
    from sglang.srt.mem_cache.base_prefix_cache import InitLoadBackParams

    return InitLoadBackParams(
        best_match_node=req.best_match_node, host_hit_length=req.host_hit_length, req=req,
    )


@pytest.fixture
def long_native(full_native, monkeypatch):
    from sglang.srt.managers.schedule_batch import Req
    from sglang.srt.sampling.sampling_params import SamplingParams
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, writes, loads, commits = full_native
    host = bind_host_slots(cache, writes, 2048, monkeypatch)
    tree = cache.tree_core
    source_tokens = SOURCE_NODES * EXTENT_TOKENS
    tokens = list(range(1, 1 + ANCHOR_TOKENS + source_tokens))
    anchor, _ = insert_session(cache, tokens[:ANCHOR_TOKENS], "parent")
    leaf, generation = insert_session(cache, tokens, "parent")
    assert leaf.parent is anchor
    host_slots = _backup(cache, leaf, demote=True)
    assert len(host_slots) == source_tokens

    for _ in range(SOURCE_NODES - 1):
        _, actions = tree.split_host_only_full_prefix(leaf.id, leaf.creation_time, EXTENT_TOKENS)
        cache._apply_cache_actions(actions)
    source = _path(anchor, leaf)
    assert len(source) == SOURCE_NODES
    for node in source:
        full = node.component_data[0]
        assert len(node.key) == EXTENT_TOKENS
        assert full.value is None and full.host_value is not None
        assert full.session_ref == 1
        assert full.lock_ref == full.host_lock_ref == 0

    req = Req(
        rid="long-restore", origin_input_text="",
        origin_input_ids=array("q", [*tokens, tokens[-1] + 1]),
        sampling_params=SamplingParams(max_new_tokens=2), session_id="parent",
        beliefkv_metadata={
            "root_workflow_id": "root", "invocation_id": "child",
            "context_id": "context", "context_epoch": 0,
        },
    )
    req.session_generation = generation
    req.init_next_round_input(cache)
    assert req.last_node == anchor.id and req.best_match_node == leaf.id
    assert req.host_hit_length == source_tokens
    key = _request_key(req)
    assert key is not None
    return NS(
        cache=cache, allocator=allocator, host=host, loads=loads, commits=commits,
        anchor=anchor, leaf=leaf, source=source, tokens=tokens, host_slots=host_slots,
        req=req, key=key,
    )


@pytest.mark.parametrize("inside_extent", (False, True), ids=("node-boundary", "inside-node"))
def test_stage_long_native_path_and_restore_exact_remainder(long_native, inside_extent):
    f = long_native
    cache, req = f.cache, f.req
    controller, tree = cache.cache_controller, cache.tree_core
    params = _load_params(req)
    tokens = BOUNDARY_TOKENS + int(inside_extent)
    free = f.allocator.available_size()
    anchor_slots = req.prefix_indices.tolist()
    chunk = DemandRestoreChunk(cache, max_source_nodes=SOURCE_NODES)

    assert chunk.start(params, f.key, tokens, device_reserve=2, protected_nodes=()) is None
    assert req._beliefkv_d3_staged_restore_key is f.key
    pending = chunk.pending
    assert pending.request is req and pending.suffix_node == f.leaf.id
    prefix = tree.node_by_id(pending.prefix_node)
    path = _path(f.anchor, f.leaf)
    boundary = f.source[BOUNDARY_NODES - 1]
    assert len(path) == SOURCE_NODES + int(inside_extent)
    if inside_extent:
        remainder = f.source[BOUNDARY_NODES]
        assert prefix.parent is boundary
        assert remainder.parent is prefix
        assert (len(prefix.key), len(remainder.key)) == (1, EXTENT_TOKENS - 1)
    else:
        assert prefix is boundary

    load, = f.loads
    ack, = controller.ack_load_queue
    assert load.node_id == prefix.id
    assert load.host_indices.tolist() == f.host_slots[:tokens].tolist()
    assert ack.node_ids == [prefix.id] and ack.num_tokens_by_pool == {"kv": tokens}
    assert cache._beliefkv_prefetch_consumer_index == controller.layer_done_counter.producer_index
    assert tuple(cache.ongoing_load_back) == (prefix.id,)
    assert not controller.load_queue
    assert f.allocator.available_size() == free - tokens

    offset = 0
    for node in path:
        full = node.component_data[0]
        end = offset + len(node.key)
        assert tuple(node.key) == tuple(f.tokens[ANCHOR_TOKENS + offset:ANCHOR_TOKENS + end])
        assert full.host_value.tolist() == f.host_slots[offset:end].tolist()
        assert full.session_ref == 1
        if end <= tokens:
            assert full.value.tolist() == load.device_indices[offset:end].tolist()
            assert node.load_back_pending_id == prefix.id and full.lock_ref == 2
        else:
            assert full.value is None
            assert node.load_back_pending_id is None and full.lock_ref == 0
        assert full.host_lock_ref == int(node is prefix or node is f.leaf)
        offset = end
    assert offset == params.host_hit_length and f.host.slot_used[f.host_slots].all()

    assert chunk.defer_until_ack(req)
    assert not chunk.continues(params, f.key)
    cache.loading_check(finish_count=0)
    assert chunk.defer_until_ack(req) and len(f.loads) == 1
    cache.loading_check(finish_count=1)
    assert chunk.pending is pending and prefix.component_data[0].lock_ref == 1
    assert prefix.component_data[0].host_lock_ref == 0
    assert f.leaf.component_data[0].host_lock_ref == 1
    assert not chunk.defer_until_ack(req) and chunk.pending is None
    assert not cache.ongoing_load_back and not controller.ack_load_queue
    for node in (f.anchor, *path):
        assert node.load_back_pending_id is None
        assert node.component_data[0].lock_ref == node.component_data[0].host_lock_ref == 0

    req.init_next_round_input(cache)
    tail_tokens = params.host_hit_length - tokens
    assert req.last_node == prefix.id and req.best_match_node == f.leaf.id
    assert req.host_hit_length == tail_tokens and len(req.prefix_indices) == ANCHOR_TOKENS + tokens
    tail = _load_params(req)
    assert chunk.continues(tail, f.key)
    assert not chunk.continues(params, f.key)
    assert not chunk.continues(replace(tail, best_match_node=prefix.id), f.key)
    assert not chunk.continues(tail, replace(f.key, context_epoch=f.key.context_epoch + 1))
    indices, last_node = cache.init_load_back(tail)
    assert len(indices) == tail_tokens and last_node == f.leaf.id
    assert len(f.loads) == 2
    assert f.loads[-1].host_indices.tolist() == f.host_slots[tokens:].tolist()
    assert indices.tolist() == f.loads[-1].device_indices.tolist()
    cache.ready_to_load_host_cache()
    cache.loading_check(finish_count=1)
    assert [
        (commit.node_ids, dict(commit.num_tokens_by_pool))
        for commit in f.commits if commit.direction == "h2d"
    ] == [((prefix.id,), {"kv": tokens}), ((f.leaf.id,), {"kv": tail_tokens})]
    req.init_next_round_input(cache)
    assert req.last_node == f.leaf.id
    assert req.host_hit_length == 0
    assert req.prefix_indices.tolist() == (
        anchor_slots + load.device_indices.tolist() + indices.tolist()
    )
    assert f.allocator.available_size() == free - params.host_hit_length
    cache.sanity_check()


def _cache_snapshot(f):
    tree = f.cache.tree_core
    controller = f.cache.cache_controller

    def indices(value):
        if value is None:
            return None
        # The native root stores an empty list instead of a tensor.
        return tuple(value) if isinstance(value, list) else tuple(value.tolist())

    nodes = {}
    for node_id, node in tree._node_arena.items():
        full = node.component_data[0]
        nodes[node_id] = (
            None if node.parent is None else node.parent.id,
            tuple(child.id for child in node.children.values()), tuple(node.key),
            indices(full.host_value), indices(full.value),
            full.session_ref, full.host_lock_ref, full.lock_ref,
            node.write_through_pending_id, node.load_back_pending_id,
        )
    return (
        nodes, indices(f.allocator.free_pages), indices(f.host.slot_used),
        f.host.available_size(), controller.layer_done_counter.producer_index,
        getattr(f.cache, "_beliefkv_prefetch_consumer_index", None),
        getattr(f.req, "_beliefkv_d3_staged_restore_key", None),
    )


@pytest.mark.parametrize(("case", "reason"), (
    ("busy-past-256", "source_extent_unowned_or_busy"),
    ("busy-unstaged-tail", "source_extent_unowned_or_busy"),
    ("one-node-short", "source_path_bound"),
    ("understated-hit", "source_prefix_mismatch"),
    ("overstated-hit", "source_prefix_mismatch"),
))
def test_reject_invalid_long_native_promise_without_mutation(long_native, monkeypatch, case, reason):
    f = long_native
    params = _load_params(f.req)
    limit = SOURCE_NODES
    busy = None
    if case == "busy-past-256":
        # The source walk starts at the leaf, so this lock is 260 nodes into it.
        busy = f.source[0]
    elif case == "busy-unstaged-tail":
        busy = f.source[-2]
    elif case == "one-node-short":
        limit -= 1
    else:
        delta = -1 if case == "understated-hit" else 1
        params = replace(params, host_hit_length=params.host_hit_length + delta)

    receipt = None if busy is None else f.cache.inc_host_lock_ref(busy.id).to_dec_params()
    before = _cache_snapshot(f)
    funding = Mock(side_effect=AssertionError("invalid source reached capacity funding"))
    monkeypatch.setattr(
        "beliefkv.runtime.demand_restore_chunk.ensure_device_capacity_preserving_live_kv", funding,
    )
    try:
        chunk = DemandRestoreChunk(f.cache, max_source_nodes=limit)
        assert chunk.start(
            params, f.key, BOUNDARY_TOKENS + 1, device_reserve=2, protected_nodes=(),
        ) == reason
        assert chunk.pending is None
        assert _cache_snapshot(f) == before
        funding.assert_not_called()
        assert not f.loads and not f.cache.ongoing_load_back
        controller = f.cache.cache_controller
        assert not controller.load_queue and not controller.ack_load_queue
        assert [commit.direction for commit in f.commits] == ["d2h"]
    finally:
        if busy is not None:
            f.cache.dec_host_lock_ref(busy.id, receipt)
    f.cache.sanity_check()
