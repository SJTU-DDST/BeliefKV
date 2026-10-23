import json

import pytest

from scripts.audit_wait_capacity_budgets import evaluate, wait_identity
from scripts.audit_wait_prefetch_capacity import audit, identity


def plan(**changes):
    return {
        "event": "wait_prefetch_plan", "ts_ms": 1000.,
        "source": "join_ticket", "workflow_id": "workflow",
        "invocation_id": "invocation", "context_id": "context",
        "context_epoch": 0, "join_id": "join", "active_tool_ids": [],
        "reason": "no_restore_plan", "no_step_reason": "prefetch_capacity_budget",
        "blocked_detail": "prefetch_full_capacity_budget",
        "capacity_budget": {
            "source": "native_next_prefill", "requested_full_tokens": 8,
            "remaining_byte_limit": 0, "full_limit_tokens": 0,
        },
        **changes,
    }


@pytest.mark.parametrize("key", (identity, wait_identity))
def test_confirmed_join_epoch_advance_keeps_the_same_wait_identity(key):
    assert key(plan()) == key(plan(context_epoch=1, active_tool_ids=["unrelated"]))
    assert key(plan()) != key(plan(join_id="later-join"))
    assert key(plan()) != key(plan(workflow_id="other-workflow"))


@pytest.mark.parametrize("key", (identity, wait_identity))
def test_parallel_tool_completion_keeps_the_round_but_not_the_next_epoch(key):
    first = plan(source="tool_wait", join_id=None, active_tool_ids=["a", "b"])
    remaining = {**first, "active_tool_ids": ["b"]}
    assert key(first) == key(remaining)
    assert key(first) != key({**remaining, "context_epoch": 1})


@pytest.mark.parametrize("key", (identity, wait_identity))
def test_missing_identity_is_rejected_instead_of_grouped_with_other_unknowns(key):
    with pytest.raises(ValueError, match="identity"):
        key(plan(context_id=None))


def test_capacity_reports_count_a_join_once_across_epoch_advance(tmp_path):
    rows = [plan(), plan(context_epoch=1), plan(join_id="later-join", context_epoch=2)]
    path = tmp_path / "opportunities.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    report = audit(path)["sources"]["join_ticket"]
    assert report["record_counts"]["capacity_rejected"] == 3
    assert report["distinct_recorded_wait_episodes"]["capacity_rejected"] == 2
    bucket = evaluate(rows)["buckets"]["native_zero_bytes_full_block"]
    assert bucket["snapshots"] == 3
    assert bucket["observed_wait_identities"] == 2
