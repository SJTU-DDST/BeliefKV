from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest

PATH = Path(__file__).with_name("handoff_ack_review.py")
SPEC = importlib.util.spec_from_file_location("grouped_handoff_ack_audit", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def fixture():
    handoff = {
        "ts_ms": 120., "consumer_node_id": 1266, "reused_tokens": 11075,
        "consumer_nodes": 29,
    }
    ack = {
        "direction": "h2d", "status": "completed", "node_ids": [1266],
        "submit_ts_ms": 110., "actual_bytes": 11075 * 98304,
        "num_tokens_by_pool": {"kv": 11075},
        "tagged_child_commits": [], "node_commits": [], "enqueue_to_submit_ms": 0.,
    }
    return handoff, ack


def test_multi_node_path_ack_is_verified_by_actual_anchor_and_full_bytes():
    handoff, ack = fixture()
    assert MODULE.match_side(
        handoff, [ack], direction="h2d", unit=98304, previous=100.,
    ) is ack


@pytest.mark.parametrize("change", (
    "foreign_anchor", "partial_bytes", "other_pool_tokens", "before_episode",
    "after_journal", "incomplete", "tagged_other_command", "duplicate",
))
def test_anchor_only_contract_does_not_relax_bytes_time_completion_or_uniqueness(change):
    handoff, ack = fixture()
    if change == "foreign_anchor":
        ack["node_ids"] = [7]
    elif change == "partial_bytes":
        ack["actual_bytes"] -= 98304
    elif change == "other_pool_tokens":
        ack["num_tokens_by_pool"] = {"kv": 11074}
    elif change == "before_episode":
        ack["submit_ts_ms"] = 100.
    elif change == "after_journal":
        ack["submit_ts_ms"] = 121.
    elif change == "incomplete":
        ack["status"] = "submitted"
    elif change == "tagged_other_command":
        ack["tagged_child_commits"] = [{"command_id": "other"}]
    rows = [ack, deepcopy(ack)] if change == "duplicate" else [ack]
    with pytest.raises(ValueError):
        MODULE.match_side(handoff, rows, direction="h2d", unit=98304, previous=100.)


@pytest.mark.parametrize("path", ((16, 17, 18), (17, 18), (18, 18, 18)))
def test_d2h_ack_expands_the_backup_path_with_unique_exact_node_count(path):
    handoff = {
        "ts_ms": 120., "donor_node_id": 18, "donor_nodes": 3, "donor_tokens": 12000,
    }
    _consumer, ack = fixture()
    ack.update(
        direction="d2h", node_ids=list(path), actual_bytes=12000 * 98304,
        num_tokens_by_pool={"kv": 12000},
    )
    if path == (16, 17, 18):
        assert MODULE.match_side(
            handoff, [ack], direction="d2h", unit=98304, previous=100.,
        ) is ack
    else:
        with pytest.raises(ValueError):
            MODULE.match_side(handoff, [ack], direction="d2h", unit=98304, previous=100.)
