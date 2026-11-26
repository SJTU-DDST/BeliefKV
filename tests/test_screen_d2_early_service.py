import pytest

from scripts.audit_d2_tool_wait_trace import ToolCall
from scripts.screen_d2_early_service import Consumer, first_cover, logical_pressure_candidates, screen_consumers


def forecast(observed, until):
    return {
        "observed_monotonic_ms": observed, "expires_monotonic_ms": 1000.,
        "p10_ms": until, "p50_ms": until, "p90_ms": until, "release_cdf": [[until, 1.]],
    }


def test_earlier_release_uses_same_deficit_reference_for_both_sizes():
    call = ToolCall(
        {"workflow_id": "donor", "invocation_id": "donor-root", "attributes": {"request_id": "origin"}},
        0., ("run", "tool"), end=1800.,
    )
    receiver = Consumer("consumer", ("other", "other-root"), 150., 800., 300., 80, 100)
    releases = {call: [(500., 100, (1, 1.)), (620., 3000, (2, 1.))]}
    hints = {call: [(50., forecast(49., 1400.))]}
    rows, counts = screen_consumers(
        (receiver,), releases, hints, chunk_tokens=2048, token_bytes=100_000, survival=.75,
    )
    assert counts["pairs_covering_before_original_first_launch"] == 1
    assert rows["at_shortfall"][0][0] == 200.
    assert rows["at_ingress"][0][0] == 350.
    assert rows["at_forecast"][0][0] == pytest.approx(247.5)
    assert rows["at_forecast_min_chunk"][0][0] == pytest.approx(210.)

    # The long quote was superseded while the recipient had yet to register.
    hints[call].append((149., forecast(148., 80.)))
    rows, _ = screen_consumers(
        (receiver,), releases, hints, chunk_tokens=2048, token_bytes=100_000, survival=.75,
    )
    assert not rows.get("at_forecast")


def test_release_size_does_not_double_count_same_node_incarnation():
    assert first_cover([(5, 60, (1, 1.)), (6, 60, (1, 1.))], 100) is None
    assert first_cover([(5, 60, (1, 1.)), (6, 60, (1, 4.))], 100) == (6, 120)


def test_logical_credit_is_bounded_by_existing_grant_and_both_raw_samples():
    donor = ToolCall({"attributes": {"request_id": "origin"}}, 0., ("run", "tool"), end=300.)
    key = ("consumer", "consumer-root", "context", 1, "request", 0, "session", 1)
    controls = [(100., [(key, 50, 100)], [("other-root", 64, donor)])]
    releases = {donor: [(120., 64, (1, 1.)), (190., 32, (2, 1.))]}
    rows, counts = logical_pressure_candidates(
        controls, {key: [180.]}, {"request": 250.}, [(90., 120), (110., 130)],
        releases, chunk_tokens=2048,
    )
    assert counts["observations_with_one_fitting_tool_donor_credit"] == 1
    assert counts["logical_shortfall_with_nearby_raw_free_fit"] == 0
    assert rows["logical_pressure_zero_copy"][0][:2] == (80., "request")
    assert rows["logical_pressure_with_observed_chunk"][0][:2] == (20., "request")
    assert not rows.get("logical_pressure_with_sampled_raw_fit")
    rows, counts = logical_pressure_candidates(
        controls, {key: [180.]}, {"request": 250.}, [(90., 125), (110., 130)],
        releases, chunk_tokens=2048,
    )
    assert counts["logical_shortfall_with_nearby_raw_free_fit"] == 1
    assert rows["logical_pressure_with_sampled_raw_fit"][0][:2] == (80., "request")
