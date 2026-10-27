import pytest

from scripts.audit_reference_capacity_waits import partition


def state(when, used=70, *, active=(), marks=()):
    return {
        "ts_ms": when, "physical_disabled": False,
        "agent_admission": {
            "strategy": "thunderagent_reference", "capacity_tokens": 100,
            "logical_active_tokens": used, "active_invocation_ids": list(active),
            "marked_for_pause_invocation_ids": list(marks),
            "logical_capacity_semantics": "reference_undiscounted_contexts_plus_buffers",
            "counts": {"control_ticks": int(when)},
        },
    }


def test_complete_input_capacity_and_active_membership_partition_the_entire_wait():
    result = partition(0., 4000., "target", 30, [
        state(0.), state(1000., 71), state(2000., 90, active=("target",)), state(3000.),
    ])
    assert result["inactive_full_logical_fit_ms"] == 2000.
    assert result["inactive_full_logical_shortfall_ms"] == 1000.
    assert result["active_ms"] == 1000.
    assert result["unknown_ms"] == 0.
    assert result["maximum_consecutive_sampled_fit_ms"] == 1000.


def test_full_budget_equality_fits_but_an_additional_token_does_not():
    assert partition(0., 1., "target", 30, [state(0.)])["inactive_full_logical_fit_ms"] == 1.
    assert partition(0., 1., "target", 31, [state(0.)])["inactive_full_logical_shortfall_ms"] == 1.


def test_stale_capacity_is_unknown_and_cannot_join_two_fitting_intervals():
    result = partition(0., 5000., "target", 30, [state(0.), state(4000.)])
    assert result["inactive_full_logical_fit_ms"] == 2500.
    assert result["unknown_ms"] == 2500.
    assert result["maximum_consecutive_sampled_fit_ms"] == 1500.


def test_preceding_sample_is_clipped_and_missing_beginning_is_unknown():
    result = partition(500., 3000., "target", 30, [state(1000.), state(2000.)])
    assert result["unknown_ms"] == 500.
    assert result["inactive_full_logical_fit_ms"] == 2000.
    assert result["first_sampled_fit_ts_ms"] == 1000.


def test_raw_overcommit_is_a_shortfall_even_if_physical_telemetry_claims_free_pages():
    row = state(0., 110)
    row["prefetch_residency_headroom"] = {"observable": True, "device_full_free_tokens": 1000}
    result = partition(0., 1000., "target", 30, [row])
    assert result["inactive_full_logical_shortfall_ms"] == 1000.


@pytest.mark.parametrize("change", (
    "backward", "unknown_budget", "unsupported", "unhealthy", "duplicate_active",
    "foreign_mark", "negative_used", "nonfinite",
))
def test_unsupported_or_corrupt_same_snapshot_evidence_is_rejected(change):
    first, second = state(0.), state(1000.)
    if change == "backward":
        second["ts_ms"] = -1.
    elif change == "unknown_budget":
        first["agent_admission"]["logical_active_tokens"] = None
    elif change == "unsupported":
        first["agent_admission"]["strategy"] = "other"
    elif change == "unhealthy":
        first["physical_disabled"] = True
    elif change == "duplicate_active":
        first["agent_admission"]["active_invocation_ids"] = ["same", "same"]
    elif change == "foreign_mark":
        first["agent_admission"]["marked_for_pause_invocation_ids"] = ["other"]
    elif change == "negative_used":
        first["agent_admission"]["logical_active_tokens"] = -1
    else:
        first["ts_ms"] = float("nan")
    with pytest.raises(ValueError):
        partition(0., 2000., "target", 30, [first, second])


@pytest.mark.parametrize("required", (0, -1, 3.5, True))
def test_missing_or_fractional_complete_demand_cannot_claim_fit(required):
    with pytest.raises(ValueError):
        partition(0., 1000., "target", required, [state(0.)])


def test_future_samples_do_not_backfill_an_unobserved_wait():
    result = partition(0., 1000., "target", 30, [state(2000.)])
    assert result["unknown_ms"] == 1000.
    assert result["inactive_full_logical_fit_ms"] == 0.
