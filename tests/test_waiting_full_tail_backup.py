"""Real CPU radix allocation/reclaim with Host metadata and copy stand-ins."""

from array import array
from dataclasses import replace
import os
from pathlib import Path
import time
from types import SimpleNamespace as NS

import pytest

from beliefkv.runtime.sglang_v0520_physical import (
    capture_action_local_shadow, plan_prefetch_gpu_steps, shadow_backup_steps,
)
from beliefkv.core.events import RuntimeEventKind
from tests.test_tool_predictive_transfers import event, tool_runtime


@pytest.fixture
def native_cache(monkeypatch, request):
    checkout = os.environ.get("SGLANG_SOURCE_CHECKOUT")
    if not checkout:
        pytest.skip("set SGLANG_SOURCE_CHECKOUT to the qualified hybrid engine")
    checkout = Path(checkout)
    monkeypatch.syspath_prepend(str(checkout / "python"))
    monkeypatch.syspath_prepend(str(checkout / "test/srt"))
    import test_beliefkv_terminal_capacity as fixtures
    from tests.test_sglang_v0520_observer import (
        HostPoolGroup, MHATokenToKVPoolHost, MambaPoolHost,
    )

    fixture_cache, allocator, request_pool = fixtures.hybrid_cache.__wrapped__(monkeypatch, request)
    cache = type(fixture_cache)(fixture_cache.cache_init_params)
    cache._execute_and_commit_kv_backup = fixture_cache._execute_and_commit_kv_backup
    full_pool = allocator._kvcache.full_kv_pool
    mamba_pool = request_pool.mamba_pool
    # The native unit fixture allocates draft buffers; this serving path uses
    # the non-speculative state over the same real CPU tensors.
    mamba_pool.mamba_cache = mamba_pool.State(
        conv=mamba_pool.mamba_cache.conv, temporal=mamba_pool.mamba_cache.temporal,
    )
    full_host = MHATokenToKVPoolHost(
        size=128, size_per_token=64, device_pool=full_pool,
        available_size=lambda: 64,
    )
    mamba_host = MambaPoolHost(
        size=16, size_per_token=64, device_pool=mamba_pool,
        available_size=lambda: 16,
    )
    full_entry = NS(host_pool=full_host, device_pool=full_pool)
    group = HostPoolGroup(
        entry_map={
            "kv": full_entry,
            "mamba": NS(host_pool=mamba_host, device_pool=mamba_pool),
        },
        anchor_entry=full_entry,
    )
    cache.host_pool_group = group
    cache.cache_controller = NS(mem_pool_host=group)
    return cache, allocator, request_pool


@pytest.fixture
def waiting_tail(native_cache):
    import torch
    from sglang.srt.mem_cache.base_prefix_cache import InsertParams
    from sglang.srt.mem_cache.radix_cache import RadixKey
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, request_pool = native_cache
    checkpoint, generation = insert_session(cache, list(range(1, 33)), "s")
    full = checkpoint.component_data[0]
    values = torch.cat((full.value, allocator.alloc(2)))
    leaf = cache.tree_core.node_by_id(cache.insert(InsertParams(
        key=RadixKey(array("q", range(1, 35))),
        value=values.to(torch.int64), prev_prefix_len=32,
        mamba_value=request_pool.mamba_allocator.alloc(1),
    )).last_device_node)
    cache.session_refs.register_session_ref(NS(
        session_id="s", session_generation=generation, session=None,
        last_node=leaf.id, origin_input_ids=array("q", range(1, 34)),
        output_ids=array("q", (34,)), extra_key=None, beliefkv_metadata={},
    ))
    full.host_value = full.value.clone()
    cache.tree_core._update_duplicate_tracking(checkpoint)
    cache.is_write_back = True
    cache.host_memory_mode = "cache"
    runtime, hint = tool_runtime()
    runtime.enable_prepare_host = True
    hint = replace(
        hint, wait_p10_ms=5000., wait_p50_ms=10000., wait_p90_ms=20000.,
        expires_monotonic_ms=time.monotonic() * 1000. + 60000.,
    )
    runtime.tool_wait_hints[hint.key.context_id] = hint
    runtime._context_tokens[hint.key.context_id] = (hint.key.context_epoch, 33)
    runtime.attach_native_cache(cache)
    try:
        assert generation == hint.key.session_generation
        assert leaf.parent is checkpoint
        assert checkpoint not in cache.tree_core.evictable_device_leaves
        assert leaf in cache.tree_core.evictable_device_leaves
        assert full.session_ref == leaf.component_data[0].session_ref == 1
        assert checkpoint.component_data[2].session_ref == 1
        assert leaf.component_data[2].session_ref == 0
        yield runtime, hint, cache, allocator, request_pool, checkpoint, leaf
    finally:
        runtime.close()


def test_safe_h2d_checkpoint_remains_separate_from_the_cold_full_tail(waiting_tail):
    runtime, hint, cache, allocator, _, checkpoint, leaf = waiting_tail
    anchors = runtime.snapshot_session_anchors(
        cache, context_id=hint.key.context_id, context_epoch=hint.key.context_epoch,
    )
    assert anchors.reusable_input_tokens == 32
    assert dict(anchors.component_leaves)[0] == ((leaf.id, leaf.creation_time),)
    assert dict(anchors.component_leaves)[2] == ((checkpoint.id, checkpoint.creation_time),)
    rejected = []
    candidate = capture_action_local_shadow(
        cache, anchors, include_non_actionable=True, on_failure=rejected.append,
    )
    assert candidate is not None, rejected
    assert candidate.missing_full_host_tokens == 2
    assert shadow_backup_steps(candidate) == ()
    runtime._register_backed_pressure_nodes(hint.key, candidate=candidate)
    runtime._publish_parent_pressure_candidates()
    available = allocator.available_size()
    assert cache.reclaim_beliefkv_handoff_capacity(
        full_tokens=available + 34, mamba_slots=0,
    ) == {}
    assert allocator.available_size() == available
    cache._execute_and_commit_kv_backup.assert_not_called()


def test_waiting_full_tail_is_selected_to_unlock_the_backed_input(waiting_tail):
    runtime, hint, _, _, _, _, leaf = waiting_tail
    step = runtime.refreshed_shadow_backup_step(context_id=hint.key.context_id)
    assert step is not None
    assert step.node_id == leaf.id
    assert step.missing_full_prefix_tokens == 2
    assert not step.include_mamba


def test_an_already_backed_waiting_full_tail_is_published_for_reclaim(waiting_tail):
    runtime, hint, cache, allocator, request_pool, checkpoint, leaf = waiting_tail
    tail = leaf.component_data[0]
    tail.host_value = tail.value.clone()
    cache.tree_core._update_duplicate_tracking(leaf)
    state = checkpoint.component_data[2].value
    full_available = allocator.available_size()
    mamba_available = request_pool.mamba_allocator.available_size()
    assert runtime.refreshed_shadow_backup_step(context_id=hint.key.context_id) is None
    runtime._publish_parent_pressure_candidates()

    result = cache.reclaim_beliefkv_handoff_capacity(
        full_tokens=full_available + 34, mamba_slots=0, max_nodes=4,
    )

    assert result.get(0, 0) == 34
    assert allocator.available_size() == full_available + 34
    assert request_pool.mamba_allocator.available_size() == mamba_available + 1
    assert checkpoint.component_data[2].value is state
    assert checkpoint.component_data[0].value is None
    assert tail.value is None
    assert tail.host_value is not None
    anchors = runtime.snapshot_session_anchors(
        cache, context_id=hint.key.context_id, context_epoch=hint.key.context_epoch,
    )
    candidate = capture_action_local_shadow(
        cache, anchors, for_prefetch=True, include_non_actionable=True,
    )
    assert [step.node_id for step in plan_prefetch_gpu_steps(candidate)] == [checkpoint.id]
    assert cache.session_refs.snapshot_latest_session_leaf_anchors(
        "s", hint.key.session_generation,
    ) is not None
    observation = cache.inspect_beliefkv_reentry(NS(
        origin_input_ids=array("q", [*range(1, 33), 99]), extra_key=None,
        session_id="s", session_generation=hint.key.session_generation,
    ))
    assert observation["checkpoint_tokens"] == 32
    assert observation["missing_full_tokens"] == 32
    assert observation["missing_mamba_slots"] == 0
    cache._execute_and_commit_kv_backup.assert_not_called()


@pytest.mark.parametrize("blocked", (
    "shared", "full_lock", "full_host_lock", "mamba_lock", "mamba_host_lock",
    "d2h", "h2d", "released_session", "ready", "new_epoch",
))
def test_waiting_tail_keeps_native_ownership_and_dependency_checks(waiting_tail, blocked):
    runtime, hint, cache, _, _, _, leaf = waiting_tail
    full, mamba = leaf.component_data[0], leaf.component_data[2]
    if blocked == "shared":
        generation = cache.ensure_session_generation("other")
        cache.session_refs.register_session_ref(NS(
            session_id="other", session_generation=generation, session=None,
            last_node=leaf.id, origin_input_ids=array("q", range(1, 34)),
            output_ids=array("q", (34,)), extra_key=None, beliefkv_metadata={},
        ))
    elif blocked == "full_lock":
        full.lock_ref += 1
    elif blocked == "full_host_lock":
        full.host_lock_ref += 1
    elif blocked == "mamba_lock":
        mamba.lock_ref += 1
    elif blocked == "mamba_host_lock":
        mamba.host_lock_ref += 1
    elif blocked == "d2h":
        leaf.write_through_pending_id = 7
    elif blocked == "h2d":
        leaf.load_back_pending_id = 7
    elif blocked == "released_session":
        cache.release_radix_session("s")
    elif blocked == "ready":
        runtime.on_events((event(
            3, RuntimeEventKind.TOOL_END, invocation_id="tool",
            attributes={"tool_run_id": "long"},
        ),))
    elif blocked == "new_epoch":
        runtime.context_sessions[hint.key.context_id] = replace(hint.key, context_epoch=2)
    assert runtime.refreshed_shadow_backup_step(context_id=hint.key.context_id) is None
    runtime._publish_parent_pressure_candidates()
    assert (leaf.id, leaf.creation_time) not in cache.beliefkv_join_pressure_candidates_by_component[0]


def test_backed_tail_cannot_be_reclaimed_after_its_session_owner_changes(waiting_tail):
    runtime, hint, cache, allocator, _, _, leaf = waiting_tail
    tail = leaf.component_data[0]
    tail.host_value = tail.value.clone()
    cache.tree_core._update_duplicate_tracking(leaf)
    runtime.refreshed_shadow_backup_step(context_id=hint.key.context_id)
    runtime._publish_parent_pressure_candidates()
    assert (leaf.id, leaf.creation_time) in cache.beliefkv_join_pressure_candidates_by_component[0]
    cache.release_radix_session("s")
    generation = cache.ensure_session_generation("other")
    cache.session_refs.register_session_ref(NS(
        session_id="other", session_generation=generation, session=None,
        last_node=leaf.id, origin_input_ids=array("q", range(1, 34)),
        output_ids=array("q", (34,)), extra_key=None, beliefkv_metadata={},
    ))
    available = allocator.available_size()
    assert not runtime._live_parent_pressure_node(leaf.id, leaf.creation_time)
    assert not cache.reclaim_beliefkv_handoff_capacity(
        full_tokens=available + 34, mamba_slots=0,
    )
    assert allocator.available_size() == available


def test_waiting_tail_requires_a_reusable_input_checkpoint(waiting_tail):
    runtime, hint, _, _, _, _, _ = waiting_tail
    runtime._context_tokens[hint.key.context_id] = (hint.key.context_epoch, 1)
    assert runtime.refreshed_shadow_backup_step(context_id=hint.key.context_id) is None


def test_waiting_session_leaf_with_device_descendants_is_not_a_cold_tail(waiting_tail):
    import torch
    from sglang.srt.mem_cache.base_prefix_cache import InsertParams
    from sglang.srt.mem_cache.radix_cache import RadixKey

    runtime, hint, cache, allocator, request_pool, _, leaf = waiting_tail
    cache.insert(InsertParams(
        key=RadixKey(array("q", range(1, 36))),
        value=torch.cat((
            leaf.parent.component_data[0].value, leaf.component_data[0].value,
            allocator.alloc(1),
        )).to(torch.int64),
        prev_prefix_len=34, mamba_value=request_pool.mamba_allocator.alloc(1),
    ))
    assert leaf not in cache.tree_core.evictable_device_leaves
    assert runtime.refreshed_shadow_backup_step(context_id=hint.key.context_id) is None
