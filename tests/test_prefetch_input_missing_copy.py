"""Output-tail Host copies do not imply a reusable input restore."""

from dataclasses import replace
from itertools import product
from unittest.mock import patch

import pytest

from beliefkv.runtime.sglang_v0520_physical import (
    has_host_backed_missing_copy,
    inspect_session_h2d_opportunity,
)
from tests.test_confirmed_join_restore_eligibility import observable_parent
from tests.test_join_confirmed_reentry import (
    complete_join, continuing_join_fixture, parallel_join_fixture,
)
from tests.test_sglang_v0520_physical import prepare_prefix_fixture


@pytest.mark.parametrize("input_tokens", (0, 2, 4, 7))
@pytest.mark.parametrize("second_leaf", (False, True))
def test_host_only_output_tail_does_not_expand_an_empty_input_plan(input_tokens, second_leaf):
    cache, anchors, nodes = prepare_prefix_fixture(
        backed=True, input_tokens=input_tokens, second_leaf=second_leaf,
    )
    nodes[3].component_data[0].value = None
    normal = inspect_session_h2d_opportunity(cache, anchors, max_steps=8)
    assert not normal.steps
    assert has_host_backed_missing_copy(cache, anchors) is True
    assert has_host_backed_missing_copy(cache, anchors, input_checkpoint_only=True) is False
    with patch(
        "beliefkv.runtime.sglang_v0520_physical.capture_action_local_shadow",
        side_effect=AssertionError("output tail must not allocate a detailed restore closure"),
    ):
        result = inspect_session_h2d_opportunity(
            cache, anchors, max_steps=8, skip_empty_restore=True,
        )
    assert not result.steps
    assert result.no_step_reason == "no_host_backed_missing_copy"


@pytest.mark.parametrize("node_id", (1, 2))
@pytest.mark.parametrize("second_leaf", (False, True))
def test_host_only_full_ancestor_still_restores_a_device_resident_checkpoint(node_id, second_leaf):
    cache, anchors, nodes = prepare_prefix_fixture(
        backed=True, input_tokens=4, second_leaf=second_leaf,
    )
    nodes[node_id].component_data[0].value = None
    assert has_host_backed_missing_copy(cache, anchors, input_checkpoint_only=True) is True
    normal = inspect_session_h2d_opportunity(cache, anchors, max_steps=8)
    optimized = inspect_session_h2d_opportunity(
        cache, anchors, max_steps=8, skip_empty_restore=True,
    )
    assert normal.step is not None
    assert optimized == normal


def test_full_between_the_last_checkpoint_and_input_limit_is_not_reusable():
    cache, anchors, nodes = prepare_prefix_fixture(backed=True, input_tokens=4)
    nodes[2].component_data[2].value = None
    nodes[2].component_data[0].value = None
    assert has_host_backed_missing_copy(cache, anchors, input_checkpoint_only=True) is False
    assert not inspect_session_h2d_opportunity(cache, anchors, max_steps=8).steps
    nodes[2].component_data[2].host_value = (0,)
    assert has_host_backed_missing_copy(cache, anchors, input_checkpoint_only=True) is True
    assert inspect_session_h2d_opportunity(cache, anchors, max_steps=8).step is not None


@pytest.mark.parametrize("second_leaf", (False, True))
@pytest.mark.parametrize("input_tokens", (0, 2, 4, 8))
def test_full_and_state_combinations_never_hide_a_legal_restore(second_leaf, input_tokens):
    count = 4 if second_leaf else 3
    for missing_full in product((False, True), repeat=count):
        for states in product(("device", "host", "absent"), repeat=count):
            cache, anchors, nodes = prepare_prefix_fixture(
                backed=True, input_tokens=input_tokens, second_leaf=second_leaf,
            )
            for node_id, (missing, state) in enumerate(zip(missing_full, states), 1):
                if missing:
                    nodes[node_id].component_data[0].value = None
                data = nodes[node_id].component_data[2]
                data.value = (0,) if state == "device" else None
                data.host_value = (0,) if state == "host" else None
            normal = inspect_session_h2d_opportunity(cache, anchors, max_steps=8)
            optimized = inspect_session_h2d_opportunity(
                cache, anchors, max_steps=8, skip_empty_restore=True,
            )
            assert optimized.steps == normal.steps, (missing_full, states)
            if normal.step is not None:
                assert optimized == normal


@pytest.mark.parametrize("state", ("unknown_geometry", "uncapped"))
def test_unknown_or_uncapped_input_does_not_prove_the_restore_empty(state):
    cache, anchors, nodes = prepare_prefix_fixture(backed=True)
    nodes[3].component_data[0].value = None
    if state == "unknown_geometry":
        nodes[2].key = None
        expected = None
    else:
        anchors = replace(anchors, reusable_input_tokens=None)
        expected = True
    assert has_host_backed_missing_copy(
        cache, anchors, input_checkpoint_only=True,
    ) is expected


def test_output_tail_does_not_create_a_confirmed_recovery_ticket():
    runtime, key, _, cache, _ = continuing_join_fixture()
    nodes, _ = observable_parent(runtime, key, cache, "resident")
    runtime._context_tokens[key.context_id] = (key.context_epoch, 5, 0, False)
    nodes[3].component_data[0].value = None
    try:
        complete_join(runtime)
        assert not runtime._confirmed_join_tickets
        assert runtime._join_ticket is None
        assert runtime.counts["confirmed_join_empty_restore_skipped"] == 1
        assert runtime._empty_join_reentry_origins == {"join": key}
    finally:
        runtime.close()


def test_output_tail_parent_leaves_the_first_selection_to_an_actionable_eos():
    runtime, first, second, plans, cache, _ = parallel_join_fixture(provisional=True)
    nodes, _ = observable_parent(runtime, first, cache, "resident")
    runtime._context_tokens[first.context_id] = (first.context_epoch, 5, 0, False)
    nodes[3].component_data[0].value = None
    try:
        complete_join(runtime)
        with patch.object(
            runtime, "inspect_context_h2d_opportunity",
            side_effect=lambda **kwargs: plans[kwargs["context_id"]],
        ) as inspect:
            runtime.dispatch_join_prefetch()
        assert [call.kwargs["context_id"] for call in inspect.call_args_list] == [second.context_id]
        cache.prefetch_gpu_session_nodes.assert_called_once()
        assert cache.prefetch_gpu_session_nodes.call_args.kwargs["session_id"] == second.session_id
        assert runtime.counts["confirmed_join_yielded_to_observed_eos"] == 0
        assert runtime.physical_ledger.pending_count == 4
    finally:
        runtime.close()


def test_read_only_scan_shares_empty_ancestry_across_distinct_session_leaves():
    cache, anchors, nodes = prepare_prefix_fixture(backed=True)
    scope = (cache.tree_core, {})
    first = replace(anchors, component_leaves=((0, ((4, 5.),)), (2, ((4, 5.),))))
    assert has_host_backed_missing_copy(
        cache, first, input_checkpoint_only=True, _empty_restore_cache=scope,
    ) is False
    with patch.object(cache.tree_core, "node_by_id", wraps=cache.tree_core.node_by_id) as lookup:
        assert has_host_backed_missing_copy(
            cache, anchors, input_checkpoint_only=True, _empty_restore_cache=scope,
        ) is False
    assert not any(call.args == (0,) for call in lookup.call_args_list)
    assert nodes[1].component_data[0].value is not None


@pytest.mark.parametrize("input_tokens", (0, 4, 8, None))
@pytest.mark.parametrize("missing_node,pool", ((2, 0), (3, 0), (2, 2), (3, 2)))
def test_new_host_only_branch_keeps_full_input_checkpoint_selection(
    input_tokens, missing_node, pool,
):
    cache, anchors, nodes = prepare_prefix_fixture(backed=True, second_leaf=True)
    data = nodes[missing_node].component_data[pool]
    data.host_value, data.value = data.value, None
    first = replace(anchors, component_leaves=((0, ((4, 5.),)), (2, ((4, 5.),))))
    scope = (cache.tree_core, {})
    assert has_host_backed_missing_copy(
        cache, first, input_checkpoint_only=True, _empty_restore_cache=scope,
    ) is False
    other = replace(
        anchors, reusable_input_tokens=input_tokens,
        component_leaves=((0, ((4, 5.), (3, 4.))), (2, ((4, 5.), (3, 4.)))),
    )
    assert has_host_backed_missing_copy(
        cache, other, input_checkpoint_only=True, _empty_restore_cache=scope,
    ) is has_host_backed_missing_copy(cache, other, input_checkpoint_only=True)


def test_empty_ancestry_proof_does_not_cross_native_tree_identity():
    first_cache, first, _ = prepare_prefix_fixture(backed=True)
    scope = (first_cache.tree_core, {})
    assert has_host_backed_missing_copy(
        first_cache, first, input_checkpoint_only=True, _empty_restore_cache=scope,
    ) is False
    cache, anchors, nodes = prepare_prefix_fixture(backed=True)
    nodes[1].component_data[0].value = None
    assert has_host_backed_missing_copy(
        cache, anchors, input_checkpoint_only=True, _empty_restore_cache=scope,
    ) is True


def test_cached_empty_ancestry_still_rejects_a_replaced_leaf_identity():
    cache, anchors, _ = prepare_prefix_fixture(backed=True)
    scope = (cache.tree_core, {})
    assert has_host_backed_missing_copy(
        cache, anchors, input_checkpoint_only=True, _empty_restore_cache=scope,
    ) is False
    invalid = replace(
        anchors, component_leaves=((0, ((3, 999.),)), (2, ((3, 999.),))),
    )
    assert has_host_backed_missing_copy(
        cache, invalid, input_checkpoint_only=True, _empty_restore_cache=scope,
    ) is None


@pytest.mark.parametrize("change", ("host_only", "cycle", "replaced_ancestor"))
def test_positive_or_unknown_branch_cannot_publish_partial_empty_proofs(change):
    cache, anchors, nodes = prepare_prefix_fixture(backed=True)
    first = replace(anchors, component_leaves=((0, ((4, 5.),)), (2, ((4, 5.),))))
    scope = (cache.tree_core, {})
    assert has_host_backed_missing_copy(
        cache, first, input_checkpoint_only=True, _empty_restore_cache=scope,
    ) is False
    prior = dict(scope[1])
    if change == "host_only":
        nodes[2].component_data[0].value = None
    elif change == "cycle":
        nodes[2].parent = nodes[3]
    else:
        from types import SimpleNamespace as NS

        lookup = cache.tree_core.node_by_id
        cache.tree_core.node_by_id = lambda node_id: (
            NS(**vars(nodes[2])) if node_id == 2 else lookup(node_id)
        )
    assert has_host_backed_missing_copy(
        cache, anchors, input_checkpoint_only=True, _empty_restore_cache=scope,
    ) is has_host_backed_missing_copy(cache, anchors, input_checkpoint_only=True)
    assert scope[1] == prior


def test_tool_empty_ancestry_is_rechecked_in_the_next_scan():
    from scripts.benchmark_empty_tool_restore_cpu import empty_tool_fixture
    from beliefkv.runtime.native_transfer_policy import TransferStartWindow
    from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime

    runtime, key, cache, _, actions, _, _, _, now = empty_tool_fixture(
        NativeAdmissionRuntime, contexts=3, depth=4, sharing=3,
    )
    try:
        with patch("time.monotonic", lambda: now):
            assert runtime._roll_tool_prefetch() is None
        cache.tree_core.node_by_id(1).component_data[0].value = None
        with patch("time.monotonic", lambda: now + .11), patch.object(
            runtime, "_h2d_start_window", return_value=TransferStartWindow(100., 0., 1000.),
        ):
            opportunity = runtime._roll_tool_prefetch()
        assert opportunity is not None and opportunity.step is not None
        assert runtime._tool_ticket.key == key
        assert not actions and not runtime.physical_ledger.pending_count
    finally:
        runtime.close()


def test_tool_capacity_reclaim_discards_all_same_scan_empty_ancestry():
    from types import SimpleNamespace as NS
    from scripts.benchmark_empty_tool_restore_cpu import empty_tool_fixture
    from beliefkv.runtime.native_transfer_policy import TransferStartWindow
    from beliefkv.runtime.sglang_v0520_physical import PrefetchLoadStep
    from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime

    runtime, _, cache, _, actions, _, _, _, now = empty_tool_fixture(
        NativeAdmissionRuntime, contexts=3, depth=4, sharing=3,
    )
    second = runtime.context_sessions["ctx-wf-1-root"]
    third = runtime.context_sessions["ctx-wf-2-root"]
    blocked = NS(
        step=PrefetchLoadStep(second, 50, 1., 50, 1., False),
        fits_current_free_lists=False,
    )
    original = runtime._wait_prefetch_opportunity
    scopes = []

    def inspect(key, **kwargs):
        scope = kwargs["_empty_restore_cache"]
        if key == second:
            scopes.append(scope)
            assert scope[1]
            return blocked
        return original(key, **kwargs)

    def reclaim(key, opportunity, *, source):
        assert key == second and source == "tool_wait" and opportunity is blocked
        assert not scopes[-1][1]
        cache.tree_core.node_by_id(1).component_data[0].value = None
        return blocked

    try:
        with patch("time.monotonic", lambda: now), patch.object(
            runtime, "_wait_prefetch_opportunity", side_effect=inspect,
        ), patch.object(
            runtime, "_budget_wait_prefetch_opportunity", side_effect=lambda key, op, **kw: op,
        ), patch.object(
            runtime, "_h2d_start_window", return_value=TransferStartWindow(100., 0., 1000.),
        ), patch.object(runtime, "_reclaim_wait_prefetch_capacity", side_effect=reclaim):
            opportunity = runtime._roll_tool_prefetch()
        assert opportunity is not None and opportunity.step is not None
        assert runtime._tool_ticket.key == third
        assert not actions and not runtime.physical_ledger.pending_count
    finally:
        runtime.close()
