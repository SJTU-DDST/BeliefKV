from copy import deepcopy

import pytest

from scripts.audit_staged_native_restore import inspect


def observations():
    return [{
        "event": "native_demand_restore_staged", "ts_ms": 101.,
        "request_id": "r", "workflow_id": "w", "invocation_id": "i",
        "context_id": "c", "context_epoch": 1, "attempt_id": 0,
        "session_id": "s", "session_generation": 2,
        "native_submit_ts_ms": 100., "native_node_ids": [3, 7],
        "native_bytes": 800, "native_full_tokens": 8,
    }], [{
        "direction": "h2d", "status": "completed", "submit_ts_ms": 100.,
        "node_ids": [3, 7], "actual_bytes": 800, "num_tokens_by_pool": {"kv": 8},
        "tagged_child_commits": [],
    }]


def test_exact_untagged_demand_matches_without_claiming_native_cache_hits_or_service():
    journals, transfers = observations()
    result = inspect(journals, transfers, bytes_per_token=100)
    assert result["counts"] == {
        "matched_staging_episodes": 1, "native_selected_requests": 1,
        "native_full_tokens": 8, "native_h2d_bytes": 800,
    }
    assert result["exact_native_ack_matches_verified"]
    assert not result["first_service_and_hidden_transfer_time_proven"]
    assert "cached_tokens_host" not in result["episodes"][0]


@pytest.mark.parametrize("fault", (
    "missing_ack", "duplicate_ack", "duplicate_claim", "wrong_bytes", "wrong_nodes",
    "wrong_time", "tagged_copy", "wrong_pool", "incomplete", "bad_identity",
    "boolean_epoch", "journal_before_submit",
))
def test_inexact_or_double_credited_staged_demand_is_rejected(fault):
    journals, transfers = observations()
    if fault == "missing_ack":
        transfers.clear()
    elif fault == "duplicate_ack":
        transfers.append(deepcopy(transfers[0]))
    elif fault == "duplicate_claim":
        journals.append(deepcopy(journals[0]))
    elif fault == "wrong_bytes":
        transfers[0]["actual_bytes"] += 1
    elif fault == "wrong_nodes":
        transfers[0]["node_ids"] = [7, 3]
    elif fault == "wrong_time":
        transfers[0]["submit_ts_ms"] += 1.
    elif fault == "tagged_copy":
        transfers[0]["tagged_child_commits"] = [{"command_id": "prefetch", "num_bytes": 800}]
    elif fault == "wrong_pool":
        transfers[0]["num_tokens_by_pool"] = {"kv": 7, "mamba": 1}
    elif fault == "incomplete":
        transfers[0]["status"] = "pending"
    elif fault == "bad_identity":
        journals[0]["session_id"] = None
    elif fault == "boolean_epoch":
        journals[0]["context_epoch"] = False
    else:
        journals[0]["ts_ms"] = 99.
    with pytest.raises(ValueError, match="native|demand"):
        inspect(journals, transfers, bytes_per_token=100)


def test_old_arms_with_no_staging_retain_zero_staged_demand():
    assert inspect([], [{"direction": "h2d", "actual_bytes": 800}], bytes_per_token=100)["counts"] == {}


def test_shared_native_batch_counts_transfer_once_for_all_selected_members():
    journals, transfers = observations()
    head = journals[0]
    fields = (
        "request_id", "workflow_id", "invocation_id", "context_id", "context_epoch",
        "attempt_id", "session_id", "session_generation",
    )
    head["requests"] = [
        {field: head[field] for field in fields},
        {**{field: head[field] for field in fields}, "request_id": "sibling",
         "invocation_id": "sibling", "session_id": "sibling"},
    ]
    head["native_batch_size"] = 2
    result = inspect(journals, transfers, bytes_per_token=100)
    assert result["counts"]["native_selected_requests"] == 2
    assert result["counts"]["native_h2d_bytes"] == 800
    assert result["episodes"][0]["native_batch_size"] == 2
