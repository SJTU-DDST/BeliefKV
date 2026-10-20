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
