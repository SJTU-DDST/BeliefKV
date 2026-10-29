"""KVFlow on full-prompt radix sessions, using real native CPU cache state."""

from array import array
from types import SimpleNamespace as NS

import pytest

from tests.test_full_only_native_lifecycle import full_native
from tests.test_kvflow_runtime import complete, live, register, request, selection


def radix_request(cache, identity, tokens, *, session="native-agent", epoch=0):
    req = request(identity, tokens, epoch=epoch)
    req.extra_key = req.cache_salt = None
    req.session_id = session
    req.session_generation = cache.ensure_session_generation(session)
    req.beliefkv_metadata["full_prompt_replay_guaranteed"] = True
    return req


def learned_session(live):
    from test_beliefkv_terminal_capacity import insert_session

    runtime, cache, *_ = live
    leaf, generation = insert_session(cache, [1, 2, 3, 4], "native-agent")
    first = radix_request(cache, "first-session", [1, 2, 3, 4])
    adapter = register(runtime, first)
    complete(runtime, first)
    second = radix_request(cache, "second-session", [1, 2, 3, 4, 5, 6])
    register(runtime, second)
    return adapter, second, leaf, generation


def test_actual_native_request_with_full_prompt_radix_identity_registers(live):
    from sglang.srt.managers.schedule_batch import Req
    from sglang.srt.sampling.sampling_params import SamplingParams

    runtime, cache, *_ = live
    tokens = array("q", [1, 2, 3, 4])
    req = Req(
        rid="actual-native", origin_input_text="", origin_input_ids=tokens,
        sampling_params=SamplingParams(temperature=1., top_k=1, max_new_tokens=1),
        session_id="native-agent", extra_key=None, cache_salt=None,
        beliefkv_metadata={
            "root_workflow_id": "workflow", "invocation_id": "agent",
            "context_id": "agent", "context_epoch": 0,
            "full_prompt_replay_guaranteed": True,
        },
    )
    req.session_generation = cache.ensure_session_generation(req.session_id)
    adapter = register(runtime, req)
    item = adapter.requests[req.rid]
    assert item.key.session_id == req.session_id
    assert item.key.session_generation == req.session_generation
    assert tuple(item.input_key.token_ids) == tuple(tokens)
    assert item.input_key.extra_key is item.input_key.cache_salt is None
    assert req.session is None and not adapter.history.boundaries


def test_same_context_session_inputs_learn_ordinary_native_fixed_prefix(live):
    adapter, second, leaf, generation = learned_session(live)
    _, cache, *_ = live
    context = adapter.contexts["agent"]
    assert context.snapshot.leaf_id == leaf.id
    assert context.snapshot.namespace == (None, None)
    assert context.snapshot.prefix_tokens == adapter.history.boundaries["agent"] == 4
    assert context.owner == ("workflow", "agent", 0)
    assert cache.session_refs.snapshot_session_leaf_anchors(
        "native-agent", generation,
    ) == ((0, ((leaf.id, leaf.creation_time),)),)
    with adapter._authority():
        adapter.priorities.set_steps({"agent": 1})
    assert adapter.priorities._eviction_key(leaf)[0] == -1
    assert selection(live[0], [second]) == (second,)


def test_session_backup_demote_restore_preserves_native_provenance_and_capacity(live):
    adapter, second, leaf, generation = learned_session(live)
    runtime, cache, allocator, writes, loads, commits = live
    context = adapter.contexts["agent"]
    anchors = cache.session_refs.snapshot_session_leaf_anchors("native-agent", generation)
    before = allocator.available_size()
    adapter.steps, adapter._prefetch_order = {"agent": 1}, ("agent",)
    adapter._graph_dirty = False
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
    assert allocator.available_size() == before + 4
    assert leaf.component_data[0].value is None
    assert cache.session_refs.snapshot_session_leaf_anchors(
        "native-agent", generation,
    ) == anchors
    adapter.tick(now=.2)
    assert allocator.available_size() == before and len(loads) == 1
    assert selection(runtime, [second]) == ()
    cache.loading_check(finish_count=1)
    assert selection(runtime, [second]) == (second,)
    assert cache.session_refs.snapshot_session_leaf_anchors(
        "native-agent", generation,
    ) == anchors
    assert cache.session_refs._session_generations["native-agent"] == generation
    assert [commit.direction for commit in commits] == ["d2h", "h2d"]
    assert not runtime.physical_disabled and not adapter.transfers.receipts.pending_count
    assert not runtime.physical_ledger.pending_count


def test_shared_session_prefix_split_keeps_priority_and_unique_allocator_accounting(live):
    from test_beliefkv_terminal_capacity import insert_session

    adapter, second, leaf, generation = learned_session(live)
    runtime, cache, allocator, *_ = live
    before = allocator.available_size()
    peer, peer_generation = insert_session(cache, [1, 2, 7, 8], "native-peer")
    assert allocator.available_size() == before - 2
    shared = leaf.parent
    assert shared is peer.parent and len(shared.key) == 2
    assert {shared.id, leaf.id} == adapter.priorities._paths["agent"]
    context = adapter.contexts["agent"]
    with adapter._authority():
        assert adapter.transfers.backup(context.snapshot, owner=context.owner)
    cache.writing_check(finish_count=1)
    before_demote = allocator.available_size()
    with adapter._authority():
        assert adapter.transfers.demote(
            context.snapshot, owner=context.owner, max_tokens=4,
        ) == 2
    assert allocator.available_size() == before_demote + 2
    assert shared.component_data[0].value is not None
    assert peer.component_data[0].value is not None
    assert cache.session_refs.snapshot_session_leaf_anchors("native-peer", peer_generation)
    assert cache.session_refs.snapshot_session_leaf_anchors("native-agent", generation)
    adapter._next_tick = float("inf")
    assert selection(runtime, [second]) == (second,)


@pytest.mark.parametrize("field", ("session", "session_params", "input_embeds", "positional_embed_overrides"))
@pytest.mark.parametrize("native_session", (False, True))
def test_reconstructed_sessions_and_input_overrides_remain_unsupported(live, field, native_session):
    runtime, cache, *_ = live
    req = (
        radix_request(cache, "invalid", [1, 2, 3])
        if native_session else request("invalid", [1, 2, 3])
    )
    setattr(req, field, NS(session_id=req.session_id) if field == "session" else {})
    with pytest.raises(ValueError, match="plain token requests"):
        register(runtime, req)
    assert not runtime._paper_baseline.requests
    assert not runtime._paper_baseline.contexts


@pytest.mark.parametrize("guarantee", (None, False, 1, "true"))
def test_radix_session_requires_explicit_full_prompt_contract(live, guarantee):
    runtime, cache, *_ = live
    req = radix_request(cache, "no-full-prompt", [1, 2, 3])
    req.beliefkv_metadata["full_prompt_replay_guaranteed"] = guarantee
    with pytest.raises(ValueError, match="full-prompt"):
        register(runtime, req)
    assert not runtime._paper_baseline.contexts


@pytest.mark.parametrize("mode", ("missing", "stale", "unknown", "closed", "disabled"))
def test_radix_session_must_have_current_native_generation(live, mode):
    runtime, cache, *_ = live
    req = radix_request(cache, "bad-session", [1, 2, 3])
    if mode == "missing":
        req.session_generation = None
    elif mode == "stale":
        cache.open_radix_session(req.session_id)
    elif mode == "unknown":
        req.session_id = "unknown"
    elif mode == "closed":
        cache.release_radix_session(req.session_id)
    else:
        cache.enable_session_radix_cache = False
    with pytest.raises(ValueError, match="live native radix session"):
        register(runtime, req)
    assert not runtime._paper_baseline.requests
    assert not runtime._paper_baseline.contexts


def test_rejected_new_epoch_cannot_retire_completed_same_context_history(live):
    adapter, second, *_ = learned_session(live)
    runtime, cache, *_ = live
    complete(runtime, second)
    context = adapter.contexts["agent"]
    boundary = dict(adapter.history.boundaries)
    priorities = set(adapter.priorities._paths["agent"])
    req = radix_request(cache, "invalid-new-epoch", [1, 2, 3, 4], epoch=1)
    req.beliefkv_metadata["full_prompt_replay_guaranteed"] = False
    with pytest.raises(ValueError, match="full-prompt"):
        register(runtime, req)
    assert adapter.contexts["agent"] is context
    assert adapter.history.boundaries == boundary
    assert adapter.priorities._paths["agent"] == priorities
