from copy import deepcopy

import pytest

from experiments.reports.join_blocked_layer_groups_20261028.handoff_ack_review import geometry, match_side


def handoff(*, extended=True):
    result = {
        "donor_node_id": 1, "consumer_node_id": 2, "donor_nodes": 1, "consumer_nodes": 1,
        "donor_tokens": 14, "reused_tokens": 14, "reserved_tail_tokens": 0,
        "checkpoint_missing_tokens": 16 if extended else 14,
        "restored_decode_tail_tokens": 0, "ts_ms": 20.,
    }
    if extended:
        result.update(restored_tokens=16, free_extent_tokens=2)
    return result


def transfer(tokens):
    return {
        "direction": "h2d", "status": "completed", "node_ids": [2],
        "submit_ts_ms": 10., "actual_bytes": tokens * 100, "num_tokens_by_pool": {"kv": tokens},
        "tagged_child_commits": [], "node_commits": [], "enqueue_to_submit_ms": 0.,
    }


def test_consumer_copy_includes_both_recycled_and_actually_allocated_free_extent():
    event = handoff()
    assert geometry(event) == 16
    ack = transfer(16)
    assert match_side(event, [ack], direction="h2d", unit=100, previous=0.) is ack
    with pytest.raises(ValueError, match="exact anchor"):
        match_side(event, [transfer(14)], direction="h2d", unit=100, previous=0.)


def test_legacy_donor_tail_and_no_extra_extent_keep_their_original_geometry():
    event = handoff(extended=False)
    event.update(donor_tokens=20, reserved_tail_tokens=6)
    assert geometry(event) == 14
    assert match_side(event, [transfer(14)], direction="h2d", unit=100, previous=0.)


@pytest.mark.parametrize("field,value", (
    ("free_extent_tokens", 3), ("reused_tokens", 13), ("reserved_tail_tokens", 1),
    ("restored_tokens", 15), ("checkpoint_missing_tokens", 15),
    ("restored_decode_tail_tokens", 1), ("donor_tokens", True),
))
def test_malformed_accounting_cannot_be_accepted_as_complete_native_bytes(field, value):
    event = handoff()
    event[field] = value
    with pytest.raises(ValueError, match="reserved-extent accounting"):
        geometry(event)


def test_two_matching_acks_are_not_double_credited():
    ack = transfer(16)
    with pytest.raises(ValueError, match="exact anchor"):
        match_side(handoff(), [ack, deepcopy(ack)], direction="h2d", unit=100, previous=0.)
