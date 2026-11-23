from copy import deepcopy

import pytest

from scripts.audit_free_dispatch_service import identity
from scripts.audit_ranked_restore_service import (
    attach_host_loss, selected_episodes, selected_host_losses, staged_transfers, summarize,
)
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


def test_ranked_restore_requires_exact_bounded_grants_and_counts_shared_bytes_once():
    journals, transfers = observations()
    staged = journals[0]
    sibling = {**staged, "request_id": "sibling", "invocation_id": "sibling", "session_id": "peer"}
    staged.update(requests=[dict(staged), sibling], native_batch_size=2)
    staged.update(monotonic_ms=51., monotonic_clock_domain="boot")
    transfers[0]["telemetry_origin"] = "native_hicache_ack_v0520"
    keys = [identity(member) for member in staged["requests"]]
    decisions = [{
        "source": "periodic_decay_resume", "monotonic_s": .049,
        "following_periodic_grant_verified": True, "newly_selected_keys": keys,
        "periodic_service": [{
            "key": key, "episode": {
                "key": key, "selection_ms": 50., "first_new_useful_launch_ms": 2.,
                "observation_window_end_ms": 55.,
                "new_service_before_next_control_or_intervention": True,
            },
        } for key in keys],
    }]
    episodes = selected_episodes(decisions)
    counts = staged_transfers(
        episodes, journals, transfers, bytes_per_token=100, clock_domain="boot",
    )
    assert counts["native_h2d_bytes"] == 800
    assert summarize(row for row in episodes) == {
        "selected_episodes": 2, "episodes_with_verified_bounded_launch": 2,
        "episodes_with_staged_H2D": 2, "distinct_staged_native_batches": 1,
        "staged_native_batch_bytes": 800,
        "attempts_with_exact_request_lifetime_host_loss": 0, "attempts_with_unverified_host_loss": 0,
        "request_lifetime_executed_host_loss_tokens": 0,
    }
    assert all(row["staged_transfers"][0]["request_H2D_tokens"] is None for row in episodes)

    late = {**staged, "monotonic_ms": 53.}
    stale_session = {
        **staged, "requests": [{**member, "session_generation": 3} for member in staged["requests"]],
    }
    staged_transfers(
        episodes := selected_episodes(decisions), [late, stale_session], transfers,
        bytes_per_token=100, clock_domain="boot",
    )
    assert summarize(episodes)["episodes_with_staged_H2D"] == 0


def test_ranked_restore_excludes_independent_tool_grant_and_unbounded_launch():
    staged = observations()[0][0]
    key = identity(staged)
    decision = {
        "source": "undiscounted_free_dispatch", "monotonic_s": .05, "newly_selected_keys": [key],
        "free_service": [{
            "key": key, "actual_grant_verified": True, "separate_tool_replacement": True,
            "episode": {"key": key, "new_service_before_next_control_or_intervention": True},
        }],
    }
    assert selected_episodes([decision])[0]["first_launch_ms"] is None
    service = decision["free_service"][0]
    service["separate_tool_replacement"] = False
    service["episode"]["new_service_before_next_control_or_intervention"] = False
    assert selected_episodes([decision])[0]["first_launch_ms"] is None


def test_ranked_host_loss_uses_exact_attempt_and_deduplicates_request_lifetime():
    staged = observations()[0][0]
    key = identity(staged)
    row = {"key": key, "first_launch_ms": 52., "staged_transfers": [], "request_lifetime_host_loss": None}
    other_attempt = {**row, "key": (*key[:5], 1, *key[6:])}
    without_service = {**row, "first_launch_ms": None}
    case = {
        **staged, "identity_and_geometry_supported": True, "observed_attempts": [[0, "s", 2]],
        "sampled_prefill_extend_tokens": 31, "sampled_lost_prefix_tokens": 7,
    }
    episodes = [row, dict(row), other_attempt, without_service]
    attach_host_loss(episodes, [case])
    counts = summarize(episodes)
    assert counts["attempts_with_exact_request_lifetime_host_loss"] == 1
    assert counts["attempts_with_unverified_host_loss"] == 1
    assert counts["request_lifetime_executed_host_loss_tokens"] == 7
    assert other_attempt["request_lifetime_host_loss"]["executed_host_loss_tokens"] is None
    assert without_service["request_lifetime_host_loss"] is None


def test_ranked_host_loss_reconciles_the_complete_native_token_count_before_filtering():
    target = {
        "event": "host_block_reaccess_attributed", "pool": "full",
        "request_id": "selected", "full_recomputed_units": 7,
    }
    rows = [target, {**target, "request_id": "other", "full_recomputed_units": 9},
            {**target, "event": "context_prefix_reuse_probe"}]
    assert list(selected_host_losses(rows, {"selected"}, expected_tokens=16)) == [target]
    with pytest.raises(ValueError, match="differ from the drained writer"):
        list(selected_host_losses(rows, {"selected"}, expected_tokens=7))
