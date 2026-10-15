import pytest

from scripts.audit_wait_capacity_budgets import evaluate


def plan(**changes):
    return {
        "event": "wait_prefetch_plan", "source": "tool_wait", "workflow_id": "w",
        "invocation_id": "i", "context_id": "c", "context_epoch": 1,
        "active_tool_ids": ["a", "b"], "join_id": None,
        "blocked_detail": "prefetch_full_capacity_budget",
        "capacity_budget": {
            "source": "native_next_prefill",
            "requested_full_tokens": 8, "remaining_byte_limit": 0,
        },
        **changes,
    }


def test_zero_budget_snapshots_keep_wait_identity_and_source_separate():
    result = evaluate((
        plan(), plan(active_tool_ids=["b", "a"]), plan(context_epoch=2),
        plan(source="join_ticket", join_id="join"),
        plan(event="unrelated"), plan(source="execution_handoff"),
    ))
    bucket = result["buckets"]["native_zero_bytes_full_block"]
    assert bucket["snapshots"] == 4
    assert bucket["observed_wait_identities"] == 3
    assert bucket["by_source"] == {"tool_wait": 3, "join_ticket": 1}
    assert len(bucket["examples"]) == 3
    assert result["counts"]["records"] == 6
    assert result["counts"]["wait_prefetch_plan_records"] == 4


@pytest.mark.parametrize("budget", (
    None, {}, {"requested_full_tokens": 0, "remaining_byte_limit": 0},
    {"source": "legacy_capacity_unavailable", "requested_full_tokens": 8,
     "remaining_byte_limit": 0},
))
def test_absent_nonpositive_or_explicit_budgets_are_not_native_zero_capacity(budget):
    result = evaluate((plan(capacity_budget=budget),))
    assert "native_zero_bytes" not in result["buckets"]
    assert result["counts"]["plan_records_without_reclaim_diagnostics"] == 1
    assert result["recorded_reclaim_outcomes"] == {}


def test_positive_byte_full_shortfall_does_not_imply_zero_byte_budget():
    result = evaluate((plan(capacity_budget={
        "source": "native_next_prefill", "requested_full_tokens": 8,
        "remaining_byte_limit": 1024,
    }),))
    assert result["buckets"]["positive_bytes_full_block"]["snapshots"] == 1
    assert "native_zero_bytes" not in result["buckets"]


def test_reclaim_rejections_remain_overlapping_observations_not_unique_evictions():
    diagnosis = {
        "full_target_satisfied": False, "mamba_target_satisfied": True,
        "published_waiting_candidates": 1, "native_full_scan_truncated": True,
        "transaction_truncated": False,
        "native_full_rejections": {"unbacked": 4, "session_referenced": 2},
        "waiting_candidate_checks": [{
            "identity_matches": True, "causal_wait_valid": False, "inflight": True,
            "full_lock_ref": 1, "mamba_lock_ref": 0, "full_session_ref": 2,
            "full_device_tokens": 8, "full_host_tokens": 0,
        }],
    }
    result = evaluate((plan(capacity_reclaim=diagnosis), plan(capacity_reclaim=diagnosis)))
    assert result["recorded_reclaim_outcomes"] == {"full=False,mamba=True": 2}
    assert result["native_full_rejection_check_counts"]["unbacked"] == 8
    assert result["full_unsatisfied_native_rejection_check_counts"]["session_referenced"] == 4
    assert result["waiting_candidate_check_counts"]["causal_wait_valid=False"] == 2
    assert result["waiting_candidate_check_counts"]["full_host_shortfall"] == 2
    assert result["buckets"]["full_unsatisfied_with_published_waiter"][
        "observed_wait_identities"
    ] == 1
    assert "full_unsatisfied_transactions_truncated" not in result["buckets"]
    assert "full_unsatisfied_without_published_waiter" not in result["buckets"]
