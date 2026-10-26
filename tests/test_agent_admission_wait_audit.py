import pytest

from scripts.audit_agent_admission_wait import partition


def state(when, active):
    return {"ts_ms": when, "agent_admission": {"active_invocation_ids": active}}


def test_first_snapshot_cannot_backfill_preceding_eligibility():
    assert partition(0., 100., "agent", [state(20., ["agent"]), state(70., [])]) == {
        "active_ms": 50., "inactive_ms": 30., "unknown_ms": 20.,
    }


def test_stale_snapshot_gaps_remain_unknown_and_total_time_is_preserved():
    assert partition(500., 4000., "agent", [state(0., []), state(3000., ["agent"])]) == {
        "active_ms": 1000., "inactive_ms": 1000., "unknown_ms": 1500.,
    }


def test_preceding_snapshot_is_clipped_at_request_arrival_and_service():
    assert partition(50., 80., "agent", [state(0., ["agent"]), state(100., [])]) == {
        "active_ms": 30., "inactive_ms": 0., "unknown_ms": 0.,
    }


@pytest.mark.parametrize("admission", ({}, None))
def test_missing_agent_identity_observation_is_not_inactive(admission):
    result = partition(0., 100., "agent", [{"ts_ms": 0., "agent_admission": admission}])
    assert result == {"active_ms": 0., "inactive_ms": 0., "unknown_ms": 100.}


@pytest.mark.parametrize("times", [[0., 0.], [10., 5.], [float("nan")]])
def test_unordered_or_nonfinite_snapshots_are_rejected(times):
    with pytest.raises(ValueError, match="increasing times"):
        partition(0., 100., "agent", [state(when, []) for when in times])
