from types import SimpleNamespace

from scripts.screen_d2_all_tool_starts import (
    Consumer, Lane, Samples, active_suffix, check_consumer, queue_overlaps,
)


def test_all_starts_can_match_a_request_without_a_capacity_quote():
    call = SimpleNamespace(start=0., end=500., origin={"invocation_id": "donor"})
    consumer = Consumer("req", "receiver", 10., 400., 32, 0)
    counters = {"starts_with_another_native_queued_request": 0}
    assert list(queue_overlaps([call], [consumer], counters)) == [(call, [consumer])]
    assert counters["starts_with_another_native_queued_request"] == 1


def test_service_credit_starts_after_the_final_observed_grant():
    grant = active_suffix([(5., True), (10., False), (80., True)], ingress=3.)
    assert grant == 80.
    consumer = Consumer("req", "receiver", 3., 250., 32, 8, active_from=grant)
    samples = Samples([(0., 64, 10, 0)])
    arguments = (consumer, 4., 500., 30, [10., 100.], samples, Lane([(5., 20.)]))
    assert check_consumer(*arguments, rate=1., copy_ms=30.) == 100.
    fast_lane = (*arguments[:-1], Lane(()))
    assert check_consumer(*fast_lane, rate=.1, copy_ms=30.) == 10.
    assert check_consumer(*fast_lane, rate=.1, copy_ms=30., require_active=True) == 100.


def test_sampled_host_shortage_and_missing_observation_are_distinct():
    pool = Samples([(0., 4, 160, 50), (1000., 13, 140, 40)])
    assert pool.host_funding(10., 1200., 5) == "below_reserved_floor"
    assert pool.host_funding(1010., 1200., 5) == "funded"
    assert pool.host_funding(1010., 2500., 5) == "unobserved"
