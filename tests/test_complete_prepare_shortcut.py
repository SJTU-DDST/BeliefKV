"""Skip empty PREPARE closures while preserving native pressure candidates."""

from unittest.mock import patch
from dataclasses import replace
import time

import pytest

from beliefkv.control.causal_graph import InvocationState
from beliefkv.runtime.sglang_v0520_prediction import NativeToolWaitHint
from beliefkv.runtime.sglang_v0520_physical import (
    capture_action_local_shadow,
    missing_prepare_full_prefix_tokens,
)
from tests.test_prepare_publication_batch import publication_fixture
from tests.test_sglang_v0520_observer import UnifiedTreeCore
from tests.test_sglang_v0520_physical import prepare_prefix_fixture


def backed_runtime(*, shortcut, depth=16, input_tokens=None):
    runtime, queue, cache, nodes, reads, actions, now = publication_fixture(
        contexts=1, depth=depth,
    )
    # Publication tests use namespace nodes; closure validation requires the
    # same named native stand-ins as the rest of the physical CPU tests.
    type(next(iter(nodes.values()))).__name__ = "UnifiedTreeNode"
    cache.tree_core = UnifiedTreeCore(**vars(cache.tree_core))
    cache.supports_beliefkv_resident_parent_prepare = lambda: True
    runtime.enable_prepare_host = True
    if shortcut:
        cache._beliefkv_unbacked_full = {}
    runtime._parent_pressure_candidates.clear()
    runtime._prepare_probe_after_ms.clear()
    key = runtime.context_sessions["ctx-wf-0-root"]
    runtime.graph.invocations[key.invocation_id].state = InvocationState.WAIT_JOIN
    runtime.graph.invocations[key.invocation_id].active_tool_calls.clear()
    if input_tokens is not None:
        runtime._context_tokens[key.context_id] = (0, input_tokens + 1, 0, False)
    return runtime, key, cache, nodes, now


@pytest.mark.parametrize("input_tokens", (0, 2, 3, 4, 8))
@pytest.mark.parametrize("second_leaf", (False, True))
def test_complete_probe_keeps_all_backed_input_nodes_and_only_proven_output_tails(
    input_tokens, second_leaf,
):
    cache, anchors, nodes = prepare_prefix_fixture(
        input_tokens=input_tokens, second_leaf=second_leaf, backed=True,
    )
    tails = frozenset((3,))
    backed = []
    assert missing_prepare_full_prefix_tokens(
        cache, anchors, complete_backed_nodes=backed, reclaim_tail_leaves=tails,
    ) == 0
    reachable = (1, 2, 3, 4) if second_leaf else (1, 2, 3)
    prefixes = {1: 2, 2: 4, 3: 8, 4: 4}
    assert backed == [
        (node_id, nodes[node_id].creation_time) for node_id in reachable
        if prefixes[node_id] <= input_tokens or node_id in tails
    ]


@pytest.mark.parametrize(
    "change", ("host_lost", "partial_host", "oversized_host", "write", "load", "replaced"),
)
def test_uncertain_complete_probe_leaves_the_callers_candidates_unchanged(change):
    cache, anchors, nodes = prepare_prefix_fixture(backed=True)
    if change == "host_lost":
        nodes[3].component_data[0].host_value = None
    elif change == "partial_host":
        nodes[3].component_data[0].host_value = range(1)
    elif change == "oversized_host":
        nodes[3].component_data[0].host_value = range(8)
    elif change == "write":
        nodes[2].write_through_pending_id = 1
    elif change == "load":
        nodes[2].load_back_pending_id = 1
    else:
        original = cache.tree_core.node_by_id

        def lookup(node_id):
            if node_id == 1:
                return type(nodes[1])(**vars(nodes[1]))
            return original(node_id)

        cache.tree_core.node_by_id = lookup
    backed = [(99, 100.)]
    assert missing_prepare_full_prefix_tokens(
        cache, anchors, complete_backed_nodes=backed,
    ) is None
    assert backed == [(99, 100.)]


def test_detached_mamba_branch_retains_the_complete_closure_path():
    cache, anchors, _ = prepare_prefix_fixture(backed=True)
    anchors = replace(anchors, component_leaves=((0, ((3, 4.),)), (2, ((4, 5.),))))
    backed = [(99, 100.)]
    assert missing_prepare_full_prefix_tokens(
        cache, anchors, complete_backed_nodes=backed,
    ) is None
    assert backed == [(99, 100.)]


@pytest.mark.parametrize("input_tokens", (0, 640, 1920))
def test_fully_backed_selection_skips_scalar_closure_and_preserves_publication(input_tokens):
    variants = []
    try:
        for shortcut in (False, True):
            runtime, key, cache, nodes, now = backed_runtime(
                shortcut=shortcut, input_tokens=input_tokens,
            )
            variants.append(runtime)
            with patch(
                "beliefkv.runtime.sglang_v0520_runtime.capture_action_local_shadow",
                wraps=capture_action_local_shadow,
            ) as capture:
                assert runtime.refreshed_shadow_backup_step(
                    context_id=key.context_id, source="join_prepare",
                ) is None
            assert capture.call_count == int(not shortcut)
            runtime._publish_parent_pressure_candidates()
            assert runtime.counts["prepare_fully_backed_capture_skipped"] == int(shortcut)
            assert not runtime.physical_disabled and not runtime.physical_ledger.pending_count
        before, after = variants
        assert tuple(before._parent_pressure_candidates.items()) == tuple(
            after._parent_pressure_candidates.items()
        )
        assert before._native_cache.beliefkv_join_pressure_candidates_by_component == (
            after._native_cache.beliefkv_join_pressure_candidates_by_component
        )
        assert before.counts["prepare_backed_full_tail_registered"] == (
            after.counts["prepare_backed_full_tail_registered"]
        )
    finally:
        for runtime in variants:
            runtime.close()


@pytest.mark.parametrize("change", ("host_lost", "write", "load", "dirty", "old_engine"))
def test_complete_shortcut_never_replaces_missing_copy_or_inflight_validation(change):
    runtime, key, cache, nodes, now = backed_runtime(shortcut=True)
    if change == "host_lost":
        # Deliberately stale empty dirty index: the native path must still win.
        nodes[15].component_data[0].host_value = None
    elif change == "write":
        nodes[15].write_through_pending_id = 1
    elif change == "load":
        nodes[15].load_back_pending_id = 1
    elif change == "dirty":
        cache._beliefkv_unbacked_full[15] = nodes[15]
        nodes[15].component_data[0].host_value = None
    else:
        cache.supports_beliefkv_resident_parent_prepare = lambda: False
    try:
        with patch(
            "beliefkv.runtime.sglang_v0520_runtime.capture_action_local_shadow",
            wraps=capture_action_local_shadow,
        ) as capture:
            step = runtime.refreshed_shadow_backup_step(
                context_id=key.context_id, source="join_prepare",
            )
        assert capture.call_count == 1
        assert runtime.counts["prepare_fully_backed_capture_skipped"] == 0
        assert (step is not None) == (change in ("host_lost", "dirty"))
    finally:
        runtime.close()


@pytest.mark.parametrize("dirty_node", (0, 8, 15, 987654321))
def test_complete_parent_uses_live_copies_even_when_global_dirty_index_is_nonempty(dirty_node):
    variants = []
    try:
        for shortcut in (False, True):
            runtime, key, cache, nodes, now = backed_runtime(shortcut=shortcut)
            variants.append(runtime)
            if shortcut:
                cache._beliefkv_unbacked_full[dirty_node] = nodes.get(dirty_node, object())
            with patch(
                "beliefkv.runtime.sglang_v0520_runtime.capture_action_local_shadow",
                wraps=capture_action_local_shadow,
            ) as capture:
                assert runtime.refreshed_shadow_backup_step(
                    context_id=key.context_id, source="join_prepare",
                ) is None
            assert capture.call_count == int(not shortcut or dirty_node == 15)
            runtime._publish_parent_pressure_candidates()
        before, after = variants
        assert tuple(before._parent_pressure_candidates.items()) == tuple(
            after._parent_pressure_candidates.items()
        )
        assert before._native_cache.beliefkv_join_pressure_candidates_by_component == (
            after._native_cache.beliefkv_join_pressure_candidates_by_component
        )
        assert after.counts["prepare_fully_backed_capture_skipped"] == int(dirty_node != 15)
        assert not after.physical_disabled and not after.physical_ledger.pending_count
    finally:
        for runtime in variants:
            runtime.close()


@pytest.mark.parametrize("missing_node", (1, 8, 15))
@pytest.mark.parametrize("pending", (None, "write", "load"))
def test_nonempty_dirty_index_keeps_partial_copy_and_dma_decisions(missing_node, pending):
    variants = []
    try:
        outputs = []
        for shortcut in (False, True):
            runtime, key, cache, nodes, now = backed_runtime(shortcut=shortcut)
            variants.append(runtime)
            nodes[missing_node].component_data[0].host_value = None
            if pending == "write":
                nodes[15].write_through_pending_id = 1
            elif pending == "load":
                nodes[15].load_back_pending_id = 1
            if shortcut:
                cache._beliefkv_unbacked_full[missing_node] = nodes[missing_node]
            with patch(
                "beliefkv.runtime.sglang_v0520_runtime.capture_action_local_shadow",
                wraps=capture_action_local_shadow,
            ) as capture:
                step = runtime.refreshed_shadow_backup_step(
                    context_id=key.context_id, source="join_prepare",
                )
            assert capture.call_count == int(pending is not None or missing_node == 15 or not shortcut)
            runtime._publish_parent_pressure_candidates()
            outputs.append((
                step, tuple(runtime._parent_pressure_candidates.items()),
                cache.beliefkv_join_pressure_candidates_by_component,
            ))
            assert not runtime.counts["prepare_fully_backed_capture_skipped"]
            assert runtime.counts["prepare_empty_private_full_capture_skipped"] == int(
                shortcut and pending is None and missing_node != 15
            )
            assert not runtime.physical_disabled and not runtime.physical_ledger.pending_count
        assert outputs[0] == outputs[1]
    finally:
        for runtime in variants:
            runtime.close()


@pytest.mark.parametrize("input_tokens", (0, 2, 4, 8))
@pytest.mark.parametrize("missing_node", (1, 2, 3))
def test_private_probe_keeps_backed_ancestors_and_rejects_an_unbacked_private_leaf(
    input_tokens, missing_node,
):
    cache, anchors, nodes = prepare_prefix_fixture(input_tokens=input_tokens, backed=True)
    nodes[missing_node].component_data[0].host_value = None
    backed = []
    result = missing_prepare_full_prefix_tokens(
        cache, anchors, complete_backed_nodes=backed,
        reclaim_tail_leaves=frozenset({3}), reclaim_device_leaves=frozenset({3}),
    )
    if missing_node == 3:
        assert result is None and backed == []
    else:
        assert result == len(nodes[missing_node].component_data[0].value)
        prefixes = {1: 2, 2: 4, 3: 8}
        assert backed == [
            (node_id, nodes[node_id].creation_time) for node_id in (1, 2, 3)
            if (
                node_id != missing_node
                or nodes[node_id].component_data[2].host_value is not None
                and nodes[node_id].component_data[2].value is not None
            )
            and (prefixes[node_id] <= input_tokens or node_id == 3)
        ]


@pytest.mark.parametrize("source", ("join_prepare", "tool_wait"))
def test_a_demoted_backed_leaf_reopens_backup_for_its_new_private_ancestor(source):
    variants = []
    try:
        selections = []
        for shortcut in (False, True):
            runtime, key, cache, nodes, now = backed_runtime(shortcut=shortcut)
            variants.append(runtime)
            if source == "tool_wait":
                invocation = runtime.graph.invocations[key.invocation_id]
                invocation.state = InvocationState.WAIT_TOOL
                invocation.active_tool_calls = {"execute"}
                runtime.predictor_sha256 = "a" * 64
                now_ms = time.monotonic() * 1000.
                runtime.tool_wait_hints[key.context_id] = NativeToolWaitHint(
                    key, 60_000., 120_000., 180_000., now_ms, now_ms + 3_600_000.,
                    runtime.predictor_sha256, invocation.updated_ts_ms,
                )
            nodes[14].component_data[0].host_value = None
            if shortcut:
                cache._beliefkv_unbacked_full[14] = nodes[14]
            assert runtime.refreshed_shadow_backup_step(
                context_id=key.context_id, source=source,
            ) is None
            nodes[15].component_data[0].value = None
            cache.tree_core.evictable_device_leaves = {nodes[14]}
            step = runtime.refreshed_shadow_backup_step(
                context_id=key.context_id, source=source,
            )
            assert step is not None and step.node_id == 14 and not step.include_mamba
            selections.append(step)
            assert not runtime.physical_disabled and not runtime.physical_ledger.pending_count
        assert selections[0] == selections[1]
    finally:
        for runtime in variants:
            runtime.close()


def test_complete_shortcut_still_checks_parent_lifecycle_before_inspection():
    runtime, key, cache, nodes, now = backed_runtime(shortcut=True)
    runtime.graph.invocations[key.invocation_id].state = InvocationState.READY
    try:
        with patch(
            "beliefkv.runtime.sglang_v0520_runtime.missing_prepare_full_prefix_tokens",
            side_effect=AssertionError("no live waiting parent"),
        ):
            assert runtime.refreshed_shadow_backup_step(
                context_id=key.context_id, source="join_prepare",
            ) is None
        assert not runtime._parent_pressure_candidates
    finally:
        runtime.close()
