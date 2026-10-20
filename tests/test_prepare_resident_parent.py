"""CPU checks for demand-bounded private FULL write-back."""

from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from beliefkv.runtime.sglang_v0520_physical import (
    capture_action_local_shadow,
    shadow_backup_steps,
)
from tests.test_sglang_v0520_physical import prepare_prefix_fixture
from tests.test_sglang_v0520_runtime import final_stage_runtime


def fixture(*, resident_parent=True):
    runtime = final_stage_runtime()
    runtime._clear_final_stage("join")
    runtime.enable_prepare_host = True
    key = runtime.context_sessions["ctx-parent"]
    cache, anchors, nodes = prepare_prefix_fixture()
    anchors = replace(anchors, key=key)
    for node in nodes.values():
        for data in node.component_data.values():
            data.session_ids = {key.session_id}
        node.backuped = False
    cache.tree_core.evictable_device_leaves = (nodes[3],)
    cache.supports_beliefkv_resident_parent_prepare = lambda: resident_parent
    runtime.attach_native_cache(cache)
    candidate = capture_action_local_shadow(cache, anchors, include_non_actionable=True)
    return runtime, cache, anchors, nodes, candidate


def test_writeback_backs_only_private_output_leaf_and_keeps_input_plan_unchanged():
    runtime, _, _, _, candidate = fixture()
    try:
        leaves = runtime._shadow_reclaim_tail_leaves(candidate)
        assert leaves == {3}
        legacy = shadow_backup_steps(candidate, reclaim_tail_leaves=leaves)
        selected = shadow_backup_steps(candidate, reclaim_device_leaves=leaves)
        assert [step.node_id for step in legacy] == [1, 2, 3]
        assert [(step.node_id, step.missing_full_prefix_tokens) for step in selected] == [(3, 4)]
        assert not selected[0].include_mamba
        assert candidate.anchors.reusable_input_tokens == 4
    finally:
        runtime.close()


@pytest.mark.parametrize("blocked", (
    "device_lock", "host_lock", "shared", "foreign_owner", "write", "load",
    "not_device_leaf", "replaced_anchor",
))
def test_private_leaf_proof_retains_locks_ownership_and_native_identity(blocked):
    runtime, cache, _, nodes, candidate = fixture()
    try:
        leaf = nodes[3]
        if blocked == "device_lock":
            leaf.component_data[2].lock_ref = 1
        elif blocked == "host_lock":
            leaf.component_data[0].host_lock_ref = 1
        elif blocked == "shared":
            leaf.component_data[0].session_ref = 2
        elif blocked == "foreign_owner":
            leaf.component_data[0].session_ids = {"other"}
        elif blocked == "write":
            leaf.write_through_pending_id = 10
        elif blocked == "load":
            leaf.load_back_pending_id = 10
        elif blocked == "not_device_leaf":
            cache.tree_core.evictable_device_leaves = ()
        else:
            leaf.creation_time += 1
        leaves = runtime._shadow_reclaim_tail_leaves(candidate)
        assert not leaves
        assert shadow_backup_steps(candidate, reclaim_device_leaves=leaves) == ()
        assert runtime.physical_ledger.pending_count == 0
    finally:
        runtime.close()


@pytest.mark.parametrize("blocked", (None, "missing_copy", "write", "load", "replaced_parent"))
def test_host_only_session_tail_exposes_next_private_device_leaf(blocked):
    runtime, cache, anchors, nodes, _ = fixture()
    try:
        leaf, parent = nodes[3], nodes[2]
        leaf.component_data[0].host_value = leaf.component_data[0].value
        leaf.component_data[0].value = None
        cache.tree_core.evictable_device_leaves = (parent,)
        if blocked == "missing_copy":
            leaf.component_data[0].host_value = None
        elif blocked == "write":
            leaf.write_through_pending_id = 10
        elif blocked == "load":
            leaf.load_back_pending_id = 10
        elif blocked == "replaced_parent":
            cache.tree_core.node_by_id = lambda node_id: (
                NS(**vars(parent)) if node_id == parent.id else nodes[node_id]
            )
        candidate = capture_action_local_shadow(cache, anchors, include_non_actionable=True)
        if candidate is None:
            assert blocked in ("write", "load", "replaced_parent")
            return
        leaves = runtime._shadow_reclaim_tail_leaves(candidate)
        assert leaves == ({2} if blocked is None else set())
        selected = shadow_backup_steps(candidate, reclaim_device_leaves=leaves)
        assert [step.node_id for step in selected] == ([2] if blocked is None else [])
    finally:
        runtime.close()


def test_private_backup_fits_host_without_reserving_all_ancestor_bytes():
    runtime, cache, anchors, _, _ = fixture()
    try:
        with patch.object(runtime, "snapshot_session_anchors", return_value=anchors), patch(
            "beliefkv.runtime.sglang_v0520_runtime.missing_prepare_full_prefix_tokens",
            side_effect=AssertionError("write-back does not reserve the whole input"),
        ):
            candidate = runtime.capture_shadow_candidate(
                cache, context_id=anchors.key.context_id,
                context_epoch=anchors.key.context_epoch,
                include_non_actionable=True, host_full_free_tokens=0,
            )
        assert candidate is not None
        selected = shadow_backup_steps(
            candidate, reclaim_device_leaves=runtime._shadow_reclaim_tail_leaves(candidate),
        )
        headroom = NS(host_full_free_tokens=4, host_mamba_free_slots=0)
        assert runtime._prepare_step_rank(
            selected[0], headroom, full_pressure=True, mamba_pressure=True,
        ) is not None
        headroom.host_full_free_tokens = 3
        assert runtime._prepare_step_rank(
            selected[0], headroom, full_pressure=True, mamba_pressure=True,
        ) is None
    finally:
        runtime.close()


def test_legacy_engine_keeps_ancestor_first_planning():
    runtime, _, _, _, candidate = fixture(resident_parent=False)
    try:
        leaves = runtime._shadow_reclaim_tail_leaves(candidate)
        assert leaves == {3}
        with patch.object(runtime, "_refreshed_shadow_backup_candidate", return_value=candidate):
            selected = runtime._refreshed_shadow_backup_selection(source="join_prepare")
        assert selected[0].node_id == 1
    finally:
        runtime.close()


def test_reused_selection_reserves_only_selected_leaf_and_waits_for_ack():
    runtime, cache, _, nodes, candidate = fixture()
    try:
        controller = cache.cache_controller
        controller.mem_pool_host = cache.host_pool_group
        controller._num_tokens_by_pool = lambda op: {"kv": len(op.device_indices)}
        controller._transfer_num_bytes = lambda op: (
            len(op.device_indices) * cache.host_pool_group.entry_map["kv"].host_pool.size_per_token
        )
        cache.prepare_host_session_nodes = Mock(side_effect=AssertionError("one leaf"))

        def prepare(**kwargs):
            assert kwargs["node_id"] == 3
            assert kwargs["beliefkv_include_mamba"] is False
            assert kwargs["beliefkv_before_enqueue"](NS(
                beliefkv_command_id=kwargs["beliefkv_command_id"],
                node_ids=[3], device_indices=tuple(range(4)), host_indices=tuple(range(4)),
            ))
            return NS(issued=True, node_id=3)

        cache.prepare_host_shadow = prepare
        selected = shadow_backup_steps(candidate, reclaim_device_leaves=frozenset({3}))[0]
        issued = runtime.issue_shadow_backup_steps(
            selected, source="join_prepare", _selection_candidate=candidate,
        )
        assert len(issued) == runtime.physical_ledger.pending_count == 1
        assert not runtime.completed_physical_actions
        assert nodes[2].component_data[0].host_value is None
        assert nodes[3].component_data[0].value is not None
    finally:
        runtime.close()
