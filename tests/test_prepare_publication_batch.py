"""Coalesce only post-forward D2H candidate publication, with live native pools."""

from collections import Counter
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from beliefkv.control.causal_graph import InvocationState
from beliefkv.runtime.sglang_v0520_prediction import NativeToolWaitHint
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_native_prepare_path import fixture


def publication_fixture(runtime_class=NativeAdmissionRuntime, *, contexts=4, depth=8):
    runtime, queue, _, actions, _ = fixture(
        runtime_class, workflows=contexts, depth=depth,
        backed=True, host_full=1_000_000, leases=0,
    )
    runtime._hotpath_timing.enabled = False
    runtime._opportunity_writer = None
    runtime.enable_tool_prefetch = False
    cache = runtime._native_cache
    original = cache.tree_core

    class Node(NS):
        __hash__ = object.__hash__

    nodes = {
        index: Node(**{**vars(original.node_by_id(index)), "backuped": True})
        for index in range(contexts * depth)
    }
    for node in nodes.values():
        node.parent = nodes[node.parent.id] if node.parent is not None else None
    reads = Counter()

    def node_by_id(index):
        reads["node_lookup"] += 1
        return nodes[index]

    leaves = {nodes[index] for index in range(depth - 1, contexts * depth, depth)}
    cache.tree_core = NS(
        node_by_id=node_by_id, evictable_device_leaves=leaves,
        lru_lists={2: NS(cache={
            node.id: node for node in nodes.values()
            if node.component_data[2].value is not None
        })},
    )
    cache.token_to_kv_pool_allocator.available_size = lambda: 16
    cache.req_to_token_pool.mamba_allocator.available_size = lambda: 64
    fixed_now = time.monotonic()
    for index, key in enumerate(runtime.context_sessions.values()):
        if index % 2:
            invocation = runtime.graph.invocations[key.invocation_id]
            invocation.state = InvocationState.WAIT_TOOL
            invocation.active_tool_calls = {"execute"}
            runtime.tool_wait_hints[key.context_id] = NativeToolWaitHint(
                key, 60000., 120000., 180000., fixed_now * 1000.,
                fixed_now * 1000. + 3_600_000., "a" * 64,
                invocation.updated_ts_ms,
            )
        runtime._prepare_probe_after_ms[key] = fixed_now * 1000. + 3_600_000.
    return runtime, queue, cache, nodes, reads, actions, fixed_now


def signature(cache):
    return (
        cache.beliefkv_join_pressure_candidates,
        cache.beliefkv_join_pressure_candidates_by_component,
    )


def test_post_forward_publishes_the_same_final_pool_prefix_once():
    reference, queue, before, *_ = publication_fixture()
    runtime, queue2, after, _, _, actions, now = publication_fixture()
    try:
        with patch("time.monotonic", return_value=now):
            # Standalone calls preserve their immediate-publication contract.
            reference.dispatch_join_prepare(queue)
            reference.dispatch_tool_prepare(queue)
            with patch.object(runtime, "_publish_parent_pressure_candidates",
                              wraps=runtime._publish_parent_pressure_candidates) as publish:
                runtime.dispatch_after_forward(queue2)
            publish.assert_called_once_with()
        assert signature(before) == signature(after)
        assert after.beliefkv_join_pressure_candidates == ()
        assert len(after.beliefkv_join_pressure_candidates_by_component[0]) == 4
        assert runtime.counts["prepare_probe_backoff"] == reference.counts["prepare_probe_backoff"]
        assert not actions and not runtime.physical_ledger.pending_count
    finally:
        reference.close()
        runtime.close()


@pytest.mark.parametrize("source", ("join", "tool"))
def test_standalone_prepare_still_publishes_immediately(source):
    runtime, queue, _, _, _, _, now = publication_fixture()
    try:
        dispatch = getattr(runtime, f"dispatch_{source}_prepare")
        with patch("time.monotonic", return_value=now), patch.object(
            runtime, "_publish_parent_pressure_candidates",
            wraps=runtime._publish_parent_pressure_candidates,
        ) as publish:
            assert dispatch(queue) is None
        publish.assert_called_once_with()
    finally:
        runtime.close()


@pytest.mark.parametrize("due", ("join", "tool", "neither"))
def test_only_due_prepare_sources_can_request_publication(due):
    runtime, queue, _, _, _, _, now = publication_fixture()
    if due != "join":
        runtime._join_prepare_next_ms = now * 1000. + 1000.
    if due != "tool":
        runtime._tool_prepare_next_ms = now * 1000. + 1000.
    try:
        with patch("time.monotonic", return_value=now), patch.object(
            runtime, "_publish_parent_pressure_candidates",
            wraps=runtime._publish_parent_pressure_candidates,
        ) as publish:
            runtime.dispatch_after_forward(queue)
        assert publish.call_count == int(due != "neither")
    finally:
        runtime.close()


@pytest.mark.parametrize("change", ("no_pressure", "disabled", "no_contexts"))
def test_no_prepare_work_does_not_add_a_publication(change):
    runtime, queue, cache, _, _, _, now = publication_fixture()
    if change == "no_pressure":
        cache.token_to_kv_pool_allocator.available_size = lambda: 1_000_000
    elif change == "disabled":
        runtime.enable_prepare_host = False
    else:
        runtime._parent_pressure_candidates.clear()
        runtime.context_sessions.clear()
        runtime.tool_wait_hints.clear()
    try:
        with patch("time.monotonic", return_value=now), patch.object(
            runtime, "_publish_parent_pressure_candidates",
            wraps=runtime._publish_parent_pressure_candidates,
        ) as publish:
            runtime.dispatch_after_forward(queue)
        publish.assert_not_called()
    finally:
        runtime.close()


@pytest.mark.parametrize("field", ("lock", "inflight", "host_lost"))
def test_final_publication_observes_changes_during_the_second_selector(field):
    runtime, queue, cache, nodes, _, _, now = publication_fixture()
    victim = max(nodes)
    original = runtime.dispatch_tool_prepare

    def tool_prepare(*args, **kwargs):
        if field == "lock":
            nodes[victim].component_data[0].lock_ref = 1
        elif field == "inflight":
            nodes[victim].write_through_pending_id = 10
        else:
            nodes[victim].component_data[0].host_value = None
        return original(*args, **kwargs)

    runtime.dispatch_tool_prepare = tool_prepare
    try:
        with patch("time.monotonic", return_value=now), patch.object(
            runtime, "_publish_parent_pressure_candidates",
            wraps=runtime._publish_parent_pressure_candidates,
        ) as publish:
            runtime.dispatch_after_forward(queue)
        publish.assert_called_once_with()
        assert victim not in {
            node for node, _ in cache.beliefkv_join_pressure_candidates_by_component[0]
        }
    finally:
        runtime.close()


def test_critical_reclaim_publication_remains_immediate_and_background_sees_final_publication():
    runtime, queue, cache, _, _, _, now = publication_fixture()
    key = next(iter(runtime.context_sessions.values()))
    runtime._critical_parent_demand = (key, 100_000, 1, now + 10.)
    calls = []
    original = runtime._publish_parent_pressure_candidates

    def publish(**kwargs):
        calls.append("critical" if kwargs else "prepare")
        original(**kwargs)

    runtime._publish_parent_pressure_candidates = publish
    runtime._reclaim_critical_parent_headroom = lambda: publish(exclude_node_ids=(1,)) or False
    runtime.dispatch_idle_prepare = lambda: calls.append("idle")
    try:
        with patch("time.monotonic", return_value=now):
            runtime.dispatch_after_forward(queue)
        assert calls == ["critical", "prepare", "idle"]
        assert any(cache.beliefkv_join_pressure_candidates_by_component.values())
    finally:
        runtime.close()


def test_a_failed_round_does_not_leave_publication_flags_for_another_round():
    runtime, queue, _, _, _, _, now = publication_fixture()
    runtime.dispatch_join_prepare = Mock(return_value=True)
    runtime.dispatch_tool_prepare = Mock(side_effect=RuntimeError("selector failed"))
    try:
        with patch("time.monotonic", return_value=now), patch.object(
            runtime, "_publish_parent_pressure_candidates",
        ) as publish:
            with pytest.raises(RuntimeError, match="selector failed"):
                runtime.dispatch_after_forward(queue)
            publish.assert_not_called()
            runtime.dispatch_join_prepare.return_value = None
            runtime.dispatch_tool_prepare.side_effect = None
            runtime.dispatch_tool_prepare.return_value = None
            runtime.dispatch_after_forward(queue)
            publish.assert_not_called()
    finally:
        runtime.close()
