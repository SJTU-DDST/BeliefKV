"""Native pressure consumes pool prefixes without a generic candidate walk."""

from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from tests.test_native_join_residency import native
from tests.test_prepare_publication_batch import publication_fixture
from tests.test_sglang_v0520_runtime import final_stage_runtime
from tests.test_waiting_full_tail_backup import native_cache, waiting_tail
from tests.test_full_only_native_lifecycle import full_native


class UnreadableFallback:
    def __getitem__(self, _):
        raise AssertionError("native used the obsolete generic pressure table")


def full_only_publication_runtime(full_native, *, owners):
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, *_ = full_native
    runtime = final_stage_runtime()
    runtime._native_cache = cache
    key = runtime.context_sessions["ctx-parent"]
    prefixes, leaves = [], []
    for owner in range(owners):
        leaf, _generation = insert_session(
            cache, list(range(1000 * owner + 1, 1000 * owner + 129)), f"owner-{owner}",
        )
        for _ in range(127):
            prefix, action = cache.tree_core._split_node(leaf.key, leaf, 1)
            assert action is None
            prefixes.append(prefix)
        leaves.append(leaf)
    for node in (*prefixes, *leaves):
        full = node.component_data[0]
        full.host_value = full.value.clone()
        cache.tree_core._update_duplicate_tracking(node)
        runtime._remember_parent_pressure_node(node.id, key, node.creation_time)
    assert cache.tree_components == (0,)
    assert 2 not in cache.tree_core.lru_lists
    assert set(leaves) == cache.tree_core.evictable_device_leaves
    return runtime, leaves


@pytest.mark.parametrize("full_native", (4096,), indirect=True)
@pytest.mark.parametrize("owners", (1, 12))
def test_real_full_only_publication_uses_sparse_device_leaves_without_mamba_index(
    full_native, owners,
):
    cache, allocator, *_ = full_native
    runtime, leaves = full_only_publication_runtime(full_native, owners=owners)
    free = allocator.available_size()
    try:
        with patch.object(cache.tree_core, "node_by_id", wraps=cache.tree_core.node_by_id) as lookup:
            runtime._publish_parent_pressure_candidates()
        assert cache.beliefkv_join_pressure_candidates == ()
        assert cache.beliefkv_join_pressure_candidates_by_component == {
            0: tuple((node.id, node.creation_time) for node in leaves[:8]), 2: (),
        }
        assert allocator.available_size() == free
        assert not cache.ongoing_write_through
        assert not cache.ongoing_load_back
        assert lookup.call_count <= 16 + min(owners, 8)
    finally:
        runtime.close()


@pytest.mark.parametrize("full_native", (4096,), indirect=True)
@pytest.mark.parametrize("cause", (
    "locked", "shared", "unbacked", "write_pending", "load_pending", "writing", "stale",
    "excluded",
))
def test_full_only_sparse_publication_keeps_live_candidate_guards(full_native, cause):
    cache, allocator, *_ = full_native
    runtime, leaves = full_only_publication_runtime(full_native, owners=3)
    node, excluded = leaves[0], ()
    if cause == "locked":
        node.component_data[0].lock_ref += 1
    elif cause == "shared":
        node.component_data[0].session_ref += 1
    elif cause == "unbacked":
        node.component_data[0].host_value = None
    elif cause == "write_pending":
        node.write_through_pending_id = node.id
    elif cause == "load_pending":
        node.load_back_pending_id = node.id
    elif cause == "writing":
        cache.ongoing_write_through[node.id] = object()
    elif cause == "stale":
        key, created = runtime._parent_pressure_candidates[node.id]
        runtime._parent_pressure_candidates[node.id] = key, created + 1
    else:
        excluded = (node.id,)
    free = allocator.available_size()
    try:
        runtime._publish_parent_pressure_candidates(exclude_node_ids=excluded)
        assert cache.beliefkv_join_pressure_candidates_by_component == {
            0: tuple((node.id, node.creation_time) for node in leaves[1:]), 2: (),
        }
        assert allocator.available_size() == free
    finally:
        runtime.close()


@pytest.mark.parametrize("full_native", (4096,), indirect=True)
def test_full_only_publication_observes_new_native_leaf_after_real_demotion(full_native):
    cache, allocator, *_ = full_native
    runtime, leaves = full_only_publication_runtime(full_native, owners=3)
    free = allocator.available_size()
    try:
        runtime._publish_parent_pressure_candidates()
        first = leaves[0]
        assert cache._can_beliefkv_full_demote(first, full_session_ref=1)
        cache._demote(first.id, {})
        assert allocator.available_size() == free + 1
        runtime._publish_parent_pressure_candidates()
        assert cache.beliefkv_join_pressure_candidates_by_component == {
            0: tuple(
                (node.id, node.creation_time) for node in (first.parent, *leaves[1:])
            ),
            2: (),
        }
        assert not cache.cache_controller.write_queue
        assert not cache.cache_controller.load_queue
    finally:
        runtime.close()


@pytest.mark.parametrize("full_native", (4096,), indirect=True)
@pytest.mark.parametrize("components", ((), (0, 2)))
def test_unknown_or_hybrid_layout_without_mamba_index_keeps_full_walk(full_native, components):
    cache, *_ = full_native
    runtime, leaves = full_only_publication_runtime(full_native, owners=3)
    cache.tree_components = components
    try:
        with patch.object(cache.tree_core, "node_by_id", wraps=cache.tree_core.node_by_id) as lookup:
            runtime._publish_parent_pressure_candidates()
        assert cache.beliefkv_join_pressure_candidates_by_component == {
            0: tuple((node.id, node.creation_time) for node in leaves), 2: (),
        }
        assert lookup.call_count == 384
    finally:
        runtime.close()


@pytest.mark.parametrize("contexts", (36, 108))
@pytest.mark.parametrize("layout", ("no_device", "unbacked_state", "sparse", "dense"))
def test_publication_scales_with_live_pool_members_and_keeps_their_order(contexts, layout):
    runtime, _, cache, nodes, reads, actions, now = publication_fixture(
        contexts=contexts, depth=8,
    )
    leaves = sorted(cache.tree_core.evictable_device_leaves, key=lambda node: node.id)
    selected = leaves[-2:] if layout == "sparse" else leaves
    live_ids = {node.id for node in selected}
    try:
        if layout != "dense":
            for node in nodes.values():
                if layout != "sparse" or node.id not in live_ids:
                    node.component_data[0].value = None
                if layout in ("no_device", "sparse") and node.id not in live_ids:
                    node.component_data[2].value = None
                if layout == "no_device":
                    node.component_data[2].value = None
                elif layout == "unbacked_state":
                    node.component_data[2].host_value = None
            cache.tree_core.evictable_device_leaves = (
                set(selected) if layout == "sparse" else set()
            )
            cache.tree_core.lru_lists[2].cache = {
                node.id: node for node in nodes.values()
                if node.component_data[2].value is not None
            }
        with patch("time.monotonic", new=lambda: now):
            runtime._prune_parent_pressure_candidates()
            reads.clear()
            runtime._publish_parent_pressure_candidates()
        expected = (
            tuple((node.id, node.creation_time) for node in selected[:8])
            if layout in ("sparse", "dense") else ()
        )
        assert cache.beliefkv_join_pressure_candidates == ()
        assert cache.beliefkv_join_pressure_candidates_by_component == {
            0: expected, 2: expected,
        }
        if layout == "no_device":
            assert reads["node_lookup"] <= 16
        elif layout == "unbacked_state":
            assert reads["node_lookup"] <= 16 + contexts
        elif layout == "sparse":
            assert reads["node_lookup"] <= 18
        assert not actions
        assert runtime.physical_ledger.pending_count == 0
        assert not runtime.physical_disabled
    finally:
        runtime.close()


def test_real_full_reclaim_uses_the_component_table_and_preserves_the_checkpoint(waiting_tail):
    runtime, hint, cache, allocator, request_pool, checkpoint, leaf = waiting_tail
    leaf.component_data[0].host_value = leaf.component_data[0].value.clone()
    cache.tree_core._update_duplicate_tracking(leaf)
    assert runtime.refreshed_shadow_backup_step(context_id=hint.key.context_id) is None
    runtime._publish_parent_pressure_candidates()
    assert cache.beliefkv_join_pressure_candidates == ()
    assert cache.beliefkv_join_pressure_candidates_by_component[0]
    cache.beliefkv_join_pressure_candidates = UnreadableFallback()
    full_available = allocator.available_size()
    mamba_available = request_pool.mamba_allocator.available_size()
    state = checkpoint.component_data[2].value
    anchors = cache.session_refs.snapshot_latest_session_leaf_anchors(
        "s", hint.key.session_generation,
    )
    result = cache.reclaim_beliefkv_handoff_capacity(
        full_tokens=full_available + 34, mamba_slots=0, max_nodes=4,
    )
    assert result.get(0, 0) == 34
    assert allocator.available_size() == full_available + 34
    assert request_pool.mamba_allocator.available_size() == mamba_available + 1
    assert checkpoint.component_data[2].value is state
    assert cache.session_refs.snapshot_latest_session_leaf_anchors(
        "s", hint.key.session_generation,
    ) == anchors
    assert checkpoint.component_data[0].value is None
    assert leaf.component_data[0].value is None
    assert leaf.component_data[0].host_value is not None


def test_native_mamba_pressure_consumes_its_pool_prefix_with_an_unreadable_fallback():
    class Node(NS):
        __hash__ = object.__hash__

    cache = native(
        "mem_cache/unified_radix_cache.py", "UnifiedRadixCache",
        {"_evict_backed_join_parent"}, {"BASE_COMPONENT_TYPE": 0, "ComponentType": NS(MAMBA=2)},
    )
    runtime = final_stage_runtime()
    runtime.enable_prepare_host = True
    key = runtime.context_sessions["ctx-parent"]
    state, host = (10,), (20,)
    node = Node(
        id=7, creation_time=2., backuped=True,
        component_data={
            0: NS(value=None, host_value=None, lock_ref=0, session_ref=1),
            2: NS(value=state, host_value=host, lock_ref=0, session_ref=1),
        },
        write_through_pending_id=None, load_back_pending_id=None,
    )
    cache.is_write_back = True
    cache.ongoing_write_through = {}
    cache.beliefkv_join_pressure_validator = lambda *_: runtime._live_parent_pressure_key(key)
    demote = Mock(return_value=NS(device_frees={}, host_frees={}, tracker={2: 1}))
    cache.tree_core = NS(
        evictable_device_leaves=set(),
        lru_lists={2: NS(cache={7: node}, get_lru_no_lock=lambda: node)},
        node_by_id=lambda _: node, demote_backed_mamba_state=demote,
    )
    cache._free_values = Mock()
    cache._accumulate_tracker = lambda target, delta: target.update(delta)
    cache.beliefkv_join_pressure_parked = Mock()
    runtime._native_cache = cache
    try:
        runtime._remember_parent_pressure_node(7, key, 2.)
        runtime._publish_parent_pressure_candidates()
        assert cache.beliefkv_join_pressure_candidates == ()
        assert cache.beliefkv_join_pressure_candidates_by_component == {
            0: (), 2: ((7, 2.),),
        }
        cache.beliefkv_join_pressure_candidates = UnreadableFallback()
        tracker = {0: 0, 2: 0}
        cache._evict_backed_join_parent(2, tracker)
        demote.assert_called_once_with(7)
        assert tracker == {0: 0, 2: 1}
        assert node.component_data[2].value is state
        assert node.component_data[2].host_value is host
    finally:
        runtime.close()
