import pytest

from scripts.audit_wait_service_phases import evaluate, partition


def wait(**changes):
    return {
        "source": "join_ticket", "node_command_count": 1,
        "completion_ts_ms": 100., "client_submit_ts_ms": 120.,
        "native_arrival_ts_ms": 150., "first_service_ts_ms": 200.,
        "last_ack_to_first_service_ms": 150.,
        "early_started_and_reused_full_bytes": 10,
        "remaining_native_full_host_hit_bytes": 0, **changes,
    }


@pytest.mark.parametrize("gap,expected", (
    (150., (50., 20., 30., 50.)),
    (90., (0., 10., 30., 50.)),
    (30., (0., 0., 0., 30.)),
    (0., (0., 0., 0., 0.)),
))
def test_phases_intersect_actual_ack_interval_and_sum_without_double_counting(gap, expected):
    row = partition(wait(last_ack_to_first_service_ms=gap))
    assert row["status"] == "partitioned"
    assert tuple(row["phases_ms"].values()) == expected
    assert sum(row["phases_ms"].values()) == gap


@pytest.mark.parametrize("changes,status", (
    ({"native_arrival_ts_ms": None}, "missing_or_invalid_boundary"),
    ({"completion_ts_ms": float("nan")}, "missing_or_invalid_boundary"),
    ({"client_submit_ts_ms": 90.}, "reversed_completion_submission_service_chain"),
    ({"last_ack_to_first_service_ms": -1.}, "service_observed_before_last_ack"),
))
def test_unknown_or_reversed_boundaries_are_retained_without_fabricating_zero(changes, status):
    row = partition(wait(**changes))
    assert row["status"] == status
    assert "phases_ms" not in row


def test_cohorts_keep_no_action_and_unknown_waits_visible():
    result = evaluate({"wait_event_attribution": {"rows": [
        wait(), wait(node_command_count=0),
        wait(native_arrival_ts_ms=None), wait(early_started_and_reused_full_bytes=0),
    ]}}, long_gap_ms=100.)
    source = result["sources"]["join_ticket"]
    assert source["observed_waits"] == 4
    assert source["waits_with_node_commands"] == 3
    assert source["partition_status"]["missing_or_invalid_boundary"] == 1
    assert source["cohorts"]["early_verified_full"]["events"] == 1
    assert source["cohorts"]["no_early_verified_full"]["events"] == 1
    assert source["cohorts"]["long_gap"]["events"] == 2
    windows = source["all_wait_completion_windows_ms"]
    assert windows["completion_to_client_submit"]["count"] == 4
    assert windows["completion_to_client_submit"]["p50"] == 20.
    assert windows["completion_to_native_arrival"]["count"] == 3
    assert windows["completion_to_native_arrival"]["missing_or_unordered_events"] == 1


def test_completion_windows_keep_no_action_demand_and_invalid_boundaries_separate():
    result = evaluate({"wait_event_attribution": {"rows": [
        wait(node_command_count=0, remaining_native_full_host_hit_bytes=100),
        wait(node_command_count=0, client_submit_ts_ms=90.),
        wait(node_command_count=0, completion_ts_ms=None),
    ]}})
    source = result["sources"]["join_ticket"]
    assert source["waits_with_node_commands"] == 0
    assert source["all_wait_completion_windows_ms"]["completion_to_client_submit"] == {
        "count": 1, "p50": 20., "p90": 20., "min": 20., "max": 20.,
        "missing_or_unordered_events": 2,
    }
    assert source["positive_native_full_restore_completion_windows_ms"][
        "completion_to_native_arrival"
    ]["count"] == 1
