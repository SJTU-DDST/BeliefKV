"""Action capture stops at blocking native dependencies without stale snapshots."""

from dataclasses import replace
from unittest.mock import patch

import pytest

from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from beliefkv.runtime.sglang_v0520_observer import (
    UnifiedNodeSummary,
    observe_unified_node_closure,
)
from beliefkv.runtime.sglang_v0520_physical import (
    ContextSessionAnchors,
    capture_action_local_shadow,
)
from tests.test_sglang_v0520_observer import UnifiedTreeCore, _node, _static_cache


def fixture(*, depth=64, prefetch=False):
    cache = _static_cache()
    cache.token_to_kv_pool_allocator.size = 128
    cache.token_to_kv_pool_allocator._kvcache.full_kv_pool.size = 128
    cache.host_pool_group.entry_map["kv"].host_pool.size = 128
    nodes = {0: _node(0)}
    nodes[0].key = ()
    for index in range(1, depth + 1):
        nodes[index] = _node(
            index, nodes[index - 1],
            device=None if prefetch else [index],
            host=[index] if prefetch else None,
            mamba_host=prefetch and index == depth,
        )
        nodes[index].key = (index,)
    cache.tree_core = UnifiedTreeCore(node_by_id=nodes.__getitem__)
    anchors = ContextSessionAnchors(
        PrefillCandidateKey("r", "wf", "agent", "ctx", 0, 0, "s", 1),
        ((0, ((depth, nodes[depth].creation_time),)),
         (2, ((depth, nodes[depth].creation_time),))),
        captured_monotonic_s=1., reusable_input_tokens=depth,
    )
    return cache, nodes, anchors


@pytest.mark.parametrize("pending", ("write", "load"))
@pytest.mark.parametrize("prefetch", (False, True))
@pytest.mark.parametrize("at_depth", (64, 32))
def test_action_capture_skips_unneeded_ancestor_summaries_and_rereads_after_ack(
    pending, prefetch, at_depth,
):
    cache, nodes, anchors = fixture(prefetch=prefetch)
    target = nodes[at_depth]
    field = "write_through_pending_id" if pending == "write" else "load_back_pending_id"
    setattr(target, field, target.id)
    reasons = []
    with patch(
        "beliefkv.runtime.sglang_v0520_observer.UnifiedNodeSummary",
        wraps=UnifiedNodeSummary,
    ) as summary:
        assert capture_action_local_shadow(
            cache, anchors, for_prefetch=prefetch, include_non_actionable=True,
            on_failure=reasons.append,
        ) is None
        assert summary.call_count == 64 - at_depth
    assert reasons == [f"pending_{pending}_transfer"]
    assert nodes[0].parent is None
    setattr(target, field, None)
    # No cross-call result is retained: ACKed paths become candidates immediately.
    assert capture_action_local_shadow(
        cache, anchors, for_prefetch=prefetch, include_non_actionable=True,
    ) is not None


@pytest.mark.parametrize("prefetch", (False, True))
def test_action_mode_preserves_all_live_fields_on_an_unblocked_path(prefetch):
    cache, nodes, anchors = fixture(prefetch=prefetch)
    with patch("beliefkv.runtime.sglang_v0520_observer.time.monotonic", return_value=2.):
        full = observe_unified_node_closure(cache, 64)
        action = observe_unified_node_closure(cache, 64, reject_pending=True)
    assert action == full and action.observable
    with patch(
        "beliefkv.runtime.sglang_v0520_physical.observe_unified_node_closure",
        return_value=full,
    ):
        original = capture_action_local_shadow(
            cache, anchors, for_prefetch=prefetch, include_non_actionable=True,
        )
    observed = capture_action_local_shadow(
        cache, anchors, for_prefetch=prefetch, include_non_actionable=True,
    )
    assert observed is not None
    assert replace(observed, anchors=original.anchors) == original


@pytest.mark.parametrize("pending", ("write", "load"))
def test_diagnostics_still_capture_the_full_path_with_pending_transfers(pending):
    cache, nodes, _ = fixture()
    field = "write_through_pending_id" if pending == "write" else "load_back_pending_id"
    setattr(nodes[64], field, 64)
    observed = observe_unified_node_closure(cache, 64)
    assert observed.observable and len(observed.nodes) == 65
    assert getattr(observed.nodes[0], f"pending_{pending}_id") == 64


@pytest.mark.parametrize("pending_id", (-1, True, "stale"))
def test_invalid_pending_identity_is_rejected_in_both_modes(pending_id):
    cache, nodes, _ = fixture()
    nodes[64].write_through_pending_id = pending_id
    for fast in (False, True):
        observed = observe_unified_node_closure(cache, 64, reject_pending=fast)
        assert not observed.observable
        assert observed.reason == "invalid pending transfer ID"


def test_pending_rejection_never_reads_the_blocked_nodes_tensor_metadata():
    cache, nodes, _ = fixture()

    class Indices:
        @property
        def shape(self):
            raise AssertionError("a blocked plan needs no tensor metadata")

    nodes[64].component_data[0].value = Indices()
    nodes[64].write_through_pending_id = 64
    observed = observe_unified_node_closure(cache, 64, reject_pending=True)
    assert not observed.observable and observed.reason == "pending_write_transfer"


def test_unknown_capture_mode_is_rejected():
    cache, _, _ = fixture()
    observed = observe_unified_node_closure(cache, 64, reject_pending=1)
    assert not observed.observable
