from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from beliefkv.control.causal_graph import InvocationState
from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.runtime.native_transfer_service import NativeServiceSample
from beliefkv.runtime.sglang_v0520_physical import revalidate_prefetch_subset
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_wait_prefetch_dispatch_cpu import dispatch_fixture


@pytest.mark.parametrize("source", ("join_ticket", "tool_wait"))
@pytest.mark.parametrize("native_budget", (False, True))
@pytest.mark.parametrize("change,expected_nodes,expected_mamba", [
    ("stable", (1, 2, 3, 4), True),
    ("capacity_shrank", (1, 2), False),
    ("partial_tail", (1, 2), False),
    ("partial_checkpoint", (1, 2, 3), False),
    ("partial_first", (), False),
    ("mamba_slot_lost", (1, 2, 3, 4), False),
    ("first_extent_resident", (2, 3, 4), True),
    ("interior_extent_resident", (1, 3, 4), True),
    ("checkpoint_resident", (1, 2, 3), False),
    ("wrong_node_creation", (), False),
    ("wrong_leaf_creation", (), False),
    ("lost_generation", (), False),
    ("closed_session", (), False),
    ("source_completed", (), False),
])
def test_final_live_plan_preserves_safe_selected_work_without_issuing_other_targets(
    source, native_budget, change, expected_nodes, expected_mamba,
):
    runtime, key, cache, _, actions, rows, _, dispatch, now = dispatch_fixture(
        NativeAdmissionRuntime, source=source, depth=4, diagnostics=True,
        native_budget=native_budget,
    )
    lookup = cache.tree_core.node_by_id
    budget = runtime._current_residency_budget()
    if change in ("partial_tail", "partial_checkpoint", "partial_first"):
        cache.split_beliefkv_prefetch_full_prefix = Mock(
            side_effect=AssertionError("final enqueue must not split a changed native tree"),
        )

    def drain(**kwargs):
        if change in ("capacity_shrank", "partial_tail"):
            cache.token_to_kv_pool_allocator.available_size = lambda: (
                300 + budget.reserve_full_tokens
            )
        elif change == "partial_first":
            cache.token_to_kv_pool_allocator.available_size = lambda: (
                100 + budget.reserve_full_tokens
            )
        elif change == "partial_checkpoint":
            cache.token_to_kv_pool_allocator.available_size = lambda: (
                450 + budget.reserve_full_tokens
            )
        elif change == "mamba_slot_lost":
            cache.req_to_token_pool.mamba_allocator.available_size = lambda: (
                budget.reserve_mamba_slots
            )
        elif change in ("first_extent_resident", "interior_extent_resident", "checkpoint_resident"):
            node = lookup({
                "first_extent_resident": 1,
                "interior_extent_resident": 2,
                "checkpoint_resident": 4,
            }[change])
            node.component_data[0].value = node.component_data[0].host_value
            if change == "checkpoint_resident":
                node.component_data[2].value = node.component_data[2].host_value
        elif change == "wrong_node_creation":
            lookup(1).creation_time += 100.
        elif change == "wrong_leaf_creation":
            lookup(4).creation_time += 100.
        elif change == "lost_generation":
            cache.session_refs._session_generations[key.session_id] += 1
        elif change == "closed_session":
            cache.session_refs._closed_session_ids.add(key.session_id)
        elif change == "source_completed":
            if source == "tool_wait":
                runtime.on_events((RuntimeEvent(
                    "tool-return", 1., RuntimeEventKind.TOOL_END,
                    key.root_workflow_id, invocation_id=key.invocation_id,
                    context_id=key.context_id, context_epoch=key.context_epoch,
                    attributes={"tool_run_id": "long"},
                ),))
            else:
                runtime.graph.contexts[key.context_id].epoch += 1

    runtime.event_server = NS(drain=Mock(side_effect=drain), close=lambda: None)
    try:
        with patch("time.monotonic", lambda: now):
            dispatch()
        assert tuple(action[0] for action in actions) == expected_nodes
        assert any(action[2] for action in actions) is expected_mamba
        assert runtime.physical_ledger.pending_count == len(expected_nodes)
        assert not runtime.physical_disabled
        assert not runtime.completed_physical_actions
        pending = [
            runtime.physical_ledger.pending_expectation(command)
            for command in runtime._prefetch_steps
        ]
        assert sum(
            dict(child.pool_bytes).get("kv", 0)
            for item in pending for child in item.children
        ) == len(expected_nodes) * 128 * 20
        if expected_nodes:
            assert all(item.context_id == key.context_id for item in pending)
            if change != "stable":
                assert runtime.counts["prefetch_burst_subset_revalidated"] == 1
            submitted = [row for row in rows if row["event"] == "wait_prefetch_plan"
                         and row["reason"].startswith("submitted")]
            assert len(submitted) == 1
            assert submitted[0]["planned_full_tokens"] == len(expected_nodes) * 128
            assert submitted[0]["planned_mamba_slots"] == int(expected_mamba)
        if change in ("partial_tail", "partial_checkpoint", "partial_first"):
            cache.split_beliefkv_prefetch_full_prefix.assert_not_called()
        runtime.event_server.drain.assert_called_once_with(max_messages=128)
    finally:
        runtime.close()


@pytest.mark.parametrize("change", (
    "foreign_context", "foreign_session", "foreign_leaf", "reordered",
    "new_target", "skipped_missing_ancestor", "duplicate_selection",
))
def test_revalidated_subset_cannot_introduce_or_reorder_targets_or_skip_missing_ancestors(change):
    runtime, key, _, _, _, _, _, _, _ = dispatch_fixture(
        NativeAdmissionRuntime, source="tool_wait", depth=4,
    )
    try:
        observed = runtime._wait_prefetch_opportunity(key)
        selected = observed.steps
        first, *rest = selected
        if change in ("foreign_context", "foreign_session"):
            field = "context_id" if change == "foreign_context" else "session_id"
            first = replace(first, key=replace(key, **{field: "other"}))
            observed = replace(observed, steps=(first, *rest))
        elif change == "foreign_leaf":
            observed = replace(observed, steps=(replace(first, leaf_node_id=99), *rest))
        elif change == "reordered":
            observed = replace(observed, steps=(selected[1], first, *selected[2:]))
        elif change == "new_target":
            observed = replace(observed, steps=(replace(first, node_id=99), *rest))
        elif change == "skipped_missing_ancestor":
            observed = replace(observed, steps=tuple(rest))
        else:
            selected = (first, *selected)
        assert revalidate_prefetch_subset(observed, selected) is None
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()


def test_confirmed_parent_registration_does_not_walk_unrelated_visible_requests():
    runtime, _, _, _, actions, _, _, dispatch, now = dispatch_fixture(
        NativeAdmissionRuntime, source="join_ticket", depth=4,
    )

    class NoVisibleScan(dict):
        def values(self):
            raise AssertionError("a confirmed parent already has a live causal binding")

    runtime.visible = NoVisibleScan(runtime.visible)
    try:
        with patch("time.monotonic", lambda: now):
            dispatch()
        assert len(actions) == 4
        assert runtime.physical_ledger.pending_count == 4
    finally:
        runtime.close()


@pytest.mark.parametrize("source", ("join_ticket", "tool_wait"))
@pytest.mark.parametrize("native_budget", (False, True))
@pytest.mark.parametrize("depth", (3, 4))
def test_final_plan_keeps_selected_work_when_resident_ancestor_exposes_unselected_tail(
    source, native_budget, depth,
):
    runtime, key, cache, _, actions, rows, _, dispatch, now = dispatch_fixture(
        NativeAdmissionRuntime, source=source, depth=depth, diagnostics=True,
        native_budget=native_budget,
    )
    reserve = runtime._current_residency_budget().reserve_full_tokens
    cache.token_to_kv_pool_allocator.available_size = lambda: 256 + reserve
    runtime._native_service_samples.extend(
        NativeServiceSample(256 * 20, 10., shape="full") for _ in range(3)
    )

    def drain(**kwargs):
        first = cache.tree_core.node_by_id(1)
        first.component_data[0].value = first.component_data[0].host_value
        cache.token_to_kv_pool_allocator.available_size = lambda: 384 + reserve

    runtime.event_server = NS(drain=drain, close=lambda: None)
    try:
        with patch("time.monotonic", lambda: now):
            dispatch()
        assert [item[0] for item in actions] == [2]
        assert not actions[0][2]
        assert runtime.physical_ledger.pending_count == 1
        assert not runtime.completed_physical_actions
        submitted = next(row for row in rows if row.get("reason") == "submitted_partial")
        assert submitted["planned_full_tokens"] == 128
        assert submitted["planned_mamba_slots"] == 0
        assert submitted["deferred_mamba_slots"] == int(depth == 3)
        assert not runtime.physical_disabled
    finally:
        runtime.close()


@pytest.mark.parametrize("invalid", ("unknown_node", "replaced_node"))
def test_unselected_tail_with_invalid_native_identity_cannot_validate_a_subset(invalid):
    runtime, key, _, _, _, _, _, _, _ = dispatch_fixture(
        NativeAdmissionRuntime, source="tool_wait", depth=4,
    )
    try:
        observed = runtime._wait_prefetch_opportunity(key)
        original = observed.steps[:2]
        tail = replace(
            observed.steps[2], **(
                {"node_id": 99} if invalid == "unknown_node" else {"creation_time": 999.}
            ),
        )
        fresh = replace(observed, steps=(*original, tail))
        assert revalidate_prefetch_subset(fresh, original) is None
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()


@pytest.mark.parametrize("source", ("join_ticket", "tool_wait"))
@pytest.mark.parametrize("change,stop_at,issued", (
    ("missing_context", 1, ()),
    ("context_advanced", 1, ()),
    ("context_advanced", 2, (1,)),
    ("source_completed", 2, (1,)),
))
def test_each_native_node_rechecks_context_and_source_before_reserving(
    source, change, stop_at, issued,
):
    runtime, key, cache, _, actions, _, _, dispatch, now = dispatch_fixture(
        NativeAdmissionRuntime, source=source, depth=4,
    )
    native = cache.prefetch_gpu_session_nodes

    def interrupting_native(**kwargs):
        reserve = kwargs["beliefkv_before_enqueue"]

        def before_enqueue(operation):
            if operation.node_ids == [stop_at]:
                if change == "missing_context":
                    del runtime.graph.contexts[key.context_id]
                elif change == "context_advanced":
                    runtime.graph.contexts[key.context_id].epoch += 1
                elif source == "tool_wait":
                    runtime.graph.invocations[key.invocation_id].state = InvocationState.READY
                else:
                    runtime.graph.joins[
                        runtime.graph.invocations[key.invocation_id].join_id
                    ].satisfied = False
            return reserve(operation)

        return native(**{**kwargs, "beliefkv_before_enqueue": before_enqueue})

    cache.prefetch_gpu_session_nodes = interrupting_native
    try:
        with patch("time.monotonic", lambda: now):
            dispatch()
        assert tuple(item[0] for item in actions) == issued
        assert runtime.physical_ledger.pending_count == len(issued)
        assert not runtime.completed_physical_actions
        assert not runtime.physical_disabled
    finally:
        runtime.close()
