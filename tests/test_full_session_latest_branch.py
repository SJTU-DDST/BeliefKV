"""Completed BeliefKV continuations retain FULL coverage on their latest path."""

from array import array
from types import SimpleNamespace as NS

import pytest

from tests.test_waiting_full_tail_backup import native_cache


def register(cache, tokens, *, session="s", tagged=True):
    from test_beliefkv_terminal_capacity import insert_session

    leaf, generation = insert_session(cache, tokens, session)
    if tagged:
        cache.session_refs.register_session_ref(NS(
            session_id=session, session_generation=generation, session=None,
            last_node=leaf.id, origin_input_ids=array("q", (*tokens, 9999)),
            output_ids=array("q"), extra_key=None, beliefkv_metadata={},
        ))
    return leaf, generation


def test_latest_full_branch_releases_obsolete_coverage_without_freeing_cache(native_cache):
    cache, allocator, _ = native_cache
    old, _ = register(cache, [1, 2, 3, 4])
    latest, generation = register(cache, [1, 2, 7, 8])
    available = allocator.available_size()
    full = cache.tree_components[0]
    assert old.component_data[0].session_ref == 0
    assert old.component_data[0].session_ids is None
    assert latest.component_data[0].session_ref == 1
    assert latest.parent.component_data[0].session_ref == 1
    component = cache.tree_core.components_by_type[full]
    assert component._session_leaves["s"] == {latest}
    assert component.latest_session_branch_retirements == 1
    assert dict(cache.session_refs.snapshot_latest_session_leaf_anchors("s", generation))[0] == (
        (latest.id, latest.creation_time),
    )
    assert old.component_data[0].value is not None
    assert allocator.available_size() == available
    cache._execute_and_commit_kv_backup.assert_not_called()


def test_runtime_reports_reference_retirement_without_claiming_freed_capacity(native_cache):
    from tests.test_tool_predictive_transfers import tool_runtime

    cache, allocator, _ = native_cache
    register(cache, [1, 2, 3, 4])
    latest, _ = register(cache, [1, 2, 7, 8])
    available = allocator.available_size()
    runtime, _ = tool_runtime()
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    cache.cache_controller.load_fence_stream = None
    runtime.attach_native_cache(cache)
    try:
        runtime._record_runtime_state()
        assert records[-1]["latest_full_session_branches_retired"] == 1
        cache.session_refs.register_session_ref(NS(
            session_id="s", session_generation=cache.ensure_session_generation("s"),
            session=None, last_node=latest.id,
            origin_input_ids=array("q", (1, 2, 7, 8, 9999)),
            output_ids=array("q"), extra_key=None, beliefkv_metadata={},
        ))
        runtime._record_runtime_state()
        assert records[-1]["latest_full_session_branches_retired"] == 1
        assert allocator.available_size() == available
    finally:
        runtime.close()


def test_untagged_native_sessions_keep_their_multiple_branches(native_cache):
    cache, _, _ = native_cache
    old, _ = register(cache, [1, 2, 3, 4], tagged=False)
    latest, _ = register(cache, [1, 2, 7, 8], tagged=False)
    assert old.component_data[0].session_ref == 1
    assert latest.component_data[0].session_ref == 1
    assert latest.parent.component_data[0].session_ref == 2


def test_latest_full_branch_preserves_other_session_coverage(native_cache):
    cache, _, _ = native_cache
    old, _ = register(cache, [1, 2, 3, 4])
    register(cache, [1, 2, 3, 4], session="other")
    latest, _ = register(cache, [1, 2, 7, 8])
    assert old.component_data[0].session_ref == 1
    assert old.component_data[0].session_ids == {"other"}
    assert latest.component_data[0].session_ids == {"s"}
    assert latest.parent.component_data[0].session_ref == 2


def test_latest_full_branch_preserves_active_request_locks(native_cache):
    cache, allocator, _ = native_cache
    old, _ = register(cache, [1, 2, 3, 4])
    lock = cache.inc_lock_ref(old.id).to_dec_params()
    try:
        latest, _ = register(cache, [1, 2, 7, 8])
        available = allocator.available_size()
        assert old.component_data[0].session_ref == 0
        assert old.component_data[0].lock_ref > 0
        assert old.component_data[0].value is not None
        assert latest.component_data[0].session_ref == 1
        assert allocator.available_size() == available
        cache._execute_and_commit_kv_backup.assert_not_called()
    finally:
        cache.dec_lock_ref(old.id, lock)


def test_old_backed_full_branch_becomes_native_reclaimable(native_cache):
    cache, allocator, _ = native_cache
    old, _ = register(cache, [1, 2, 3, 4])
    latest, _ = register(cache, [1, 2, 7, 8])
    full = old.component_data[0]
    full.host_value = full.value.clone()
    cache.tree_core._update_duplicate_tracking(old)
    cache.is_write_back = True
    cache.host_memory_mode = "cache"
    cache.beliefkv_join_pressure_validator = lambda *_: False
    excluded, node = [], latest
    while node is not None:
        excluded.append(node.id)
        node = node.parent
    available = allocator.available_size()
    reclaimed = cache.reclaim_beliefkv_handoff_capacity(
        full_tokens=available + 2, mamba_slots=0,
        exclude_node_ids=tuple(excluded), collect_diagnostics=True,
    )
    assert reclaimed.get(0) == 2
    assert allocator.available_size() == available + 2
    assert old.component_data[0].value is None
    assert latest.component_data[0].value is not None
    assert latest.component_data[0].session_ref == 1
    cache._execute_and_commit_kv_backup.assert_not_called()
