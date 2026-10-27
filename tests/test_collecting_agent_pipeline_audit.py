from copy import deepcopy

import pytest

from scripts.audit_collecting_agent_pipeline import blocked_composition, validate_receipts


def snapshot(when, *, inactive=1):
    budgets = [
        {"invocation_id": "parent", "tokens": 200, "buffer_tokens": 100,
         "pending": False, "blocked": "join"},
        {"invocation_id": "running", "tokens": 100, "buffer_tokens": 100,
         "pending": True, "blocked": None},
    ]
    return {"ts_ms": when, "agent_admission": {
        "active_invocation_ids": ["parent", "running"],
        "active_agent_budgets": budgets,
        "logical_active_tokens": 500, "pending_requests": 1 + inactive,
    }}


def test_join_budget_is_reported_separately_under_observed_registered_demand():
    result = blocked_composition(
        [snapshot(0.), snapshot(100., inactive=0)], begin=0., end=200.,
    )
    assert result["active_budget_fraction_by_block_reason"] == {"join": .6}
    assert result["active_budget_fraction_blocked_with_inactive_requests"] == .3
    assert result["observation_ms"]["ms_with_inactive_registered_requests"] == 100.
    assert "physical HBM" in result["semantics"]


def test_actual_logical_release_disappears_from_later_budget_samples():
    released = snapshot(100.)
    admission = released["agent_admission"]
    admission["active_invocation_ids"] = ["running"]
    admission["active_agent_budgets"] = admission["active_agent_budgets"][1:]
    admission["logical_active_tokens"] = 200
    result = blocked_composition([snapshot(0.), released], begin=0., end=200.)
    assert result["active_budget_fraction_by_block_reason"]["join"] == 300 / 700


def test_old_uninstrumented_samples_remain_unknown_and_are_not_assumed_zero():
    old = snapshot(0.)
    del old["agent_admission"]["active_agent_budgets"]
    result = blocked_composition([old], begin=0., end=5000.)
    assert result["observation_ms"]["unknown_ms"] == 1500.
    assert result["active_budget_fraction_blocked_with_inactive_requests"] is None


def test_snapshot_age_limit_and_request_span_clip_the_diagnostic():
    result = blocked_composition(
        [snapshot(0.), snapshot(5000.)], begin=1000., end=5500.,
    )
    assert result["observation_ms"]["observed_ms"] == 1000.
    assert result["observation_ms"]["logical_budget_token_ms"] == 500_000.


@pytest.mark.parametrize("change", ("sum", "pending", "identity", "duplicate_clock", "negative"))
def test_inconsistent_snapshot_proof_is_rejected(change):
    rows = [snapshot(0.)]
    if change == "sum":
        rows[0]["agent_admission"]["logical_active_tokens"] = 600
    elif change == "pending":
        rows[0]["agent_admission"]["pending_requests"] = 0
    elif change == "identity":
        rows[0]["agent_admission"]["active_invocation_ids"] = ["someone-else"]
    elif change == "duplicate_clock":
        rows.append(deepcopy(rows[0]))
    else:
        rows[0]["agent_admission"]["active_agent_budgets"][0]["tokens"] = -1
    with pytest.raises(ValueError):
        blocked_composition(rows, begin=0., end=100.)


def receipts():
    return {
        "request_id": "r", "prompt_tokens": 10, "output_tokens": 2,
        "prompt_sha256": "a" * 64, "output_sha256": "b" * 64,
        "native_finish_reason": "stop", "exact_tokens_verified": True,
        "native_receipt": {"sampling_path": "position"},
    }


@pytest.mark.parametrize("change", (None, "tokens", "hash", "stop", "sampler", "unknown", "duplicate"))
def test_partial_receipts_require_exact_work_and_native_stopping(change):
    row = receipts()
    expected = {"r": (10, 2, "a" * 64, "b" * 64, "stop")}
    rows = [row]
    if change == "tokens":
        row["output_tokens"] = 3
    elif change == "hash":
        row["output_sha256"] = "c" * 64
    elif change == "stop":
        row["native_finish_reason"] = "length"
    elif change == "sampler":
        row["native_receipt"]["sampling_path"] = "different"
    elif change == "unknown":
        row["request_id"] = "another"
    elif change == "duplicate":
        rows.append(deepcopy(row))
    if change is None:
        assert validate_receipts(rows, expected) == 1
    else:
        with pytest.raises(ValueError):
            validate_receipts(rows, expected)
