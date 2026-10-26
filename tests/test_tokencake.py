from dataclasses import replace
import math

import pytest

from beliefkv.policy.tokencake import (
    AgentHistory, DecisionReason, DurationHistory, OffloadSettings, Pressure,
    TokenCakeMetadata, UploadCandidate, WaitingWork, first_fit_work,
    paper_upload_reservations, partition_capacity, public_offload_benefit, request_score,
)


def test_neutral_request_metadata_does_not_fabricate_graph_progress():
    metadata = TokenCakeMetadata()
    assert request_score(metadata, 10., 10.) == 0
    assert request_score(metadata, 10., 20.) == 80
    assert request_score(replace(metadata, near_completion=True), 10., 10.) == 90


def test_public_priority_includes_structure_waiting_and_known_join_pressure():
    metadata = TokenCakeMetadata(
        importance=2., depth=2, in_degree=2, out_degree=3, application_max_depth=4,
        remaining_depth=2, similarity=.5, application_started_at_s=10., join_group="actual_join",
    )
    assert request_score(metadata, 20., 40.) > request_score(metadata, 40., 40.)
    assert request_score(replace(metadata, critical_path=True), 20., 40.) == (
        request_score(metadata, 20., 40.) + 180
    )


def test_observed_history_increases_type_score_for_preemptions_and_rebuild_cost():
    baseline = AgentHistory()
    assert baseline.score(0., 0, 0.) == 1.
    assert AgentHistory(preemptions=3).score(0., 0, 0.) > baseline.score(0., 0, 0.)
    assert AgentHistory(samples=1, input_tokens=100000, completed=1, duration=5.).score(
        0., 0, 0.,
    ) > baseline.score(0., 0, 0.)


@pytest.mark.parametrize("total", (0, 1, 17, 1024))
def test_integer_reservations_conserve_capacity_and_keep_noncritical_work_in_shared_pool(total):
    plan = partition_capacity(
        {"a": 9., "b": 5., "c": 1.}, {"a": 1., "b": 1., "c": 1.},
        {"a": 10, "b": 100, "c": 50}, total, .3, .75,
    )
    assert plan.critical == {"a", "b"}
    assert sum(plan.reserved.values()) + plan.shared == total
    assert sum(plan.reserved.values()) == int(total * .3)
    assert plan.reserved.keys() == plan.critical


def test_no_active_type_keeps_entire_pool_shared():
    plan = partition_capacity({}, {}, {}, 100, .3, .75)
    assert plan.shared == 100 and not plan.reserved


def test_forecast_uses_only_completed_samples_with_user_fallback_and_blend():
    history = DurationHistory()
    key = ("type", "worker", "unknown_external")
    assert history.predict(key) == 1.
    assert history.predict(key, user_estimate=10.) == 10.
    history.observe(key, elapsed_seconds=4.)
    assert history.predict(key) == 4.
    assert history.predict(key, user_estimate=10.) == 7.
    history.observe(key, elapsed_seconds=8.)
    assert history.predict(key) == 6.
    # Unknown categories have no reusable estimate and cannot learn from a future end.
    history.observe(None, elapsed_seconds=300.)
    assert history.predict(None) == 1.
    assert history.predict(("type", "other", "unknown_external")) == 1.


def test_duration_history_is_bounded_and_recently_predicted_keys_stay_live():
    history = DurationHistory(limit=2)
    history.observe(("a",), elapsed_seconds=2.)
    history.observe(("b",), elapsed_seconds=3.)
    history.predict(("a",))
    history.observe(("c",), elapsed_seconds=4.)
    assert set(history.history) == {("a",), ("c",)}
    assert history.predict(("b",)) == 1.


@pytest.mark.parametrize("parameters", (
    {"alpha": 0.}, {"alpha": math.nan}, {"beta": 2.},
    {"default_seconds": math.inf}, {"limit": 0},
))
def test_invalid_prediction_configuration_fails(parameters):
    with pytest.raises(ValueError):
        DurationHistory(**parameters)


@pytest.mark.parametrize("sample", (-1., math.nan, math.inf))
def test_invalid_actual_duration_cannot_pollute_future_estimates(sample):
    with pytest.raises(ValueError):
        DurationHistory().observe(("tool",), elapsed_seconds=sample)


def benefit(**changes):
    arguments = {
        "settings": OffloadSettings(), "metadata": TokenCakeMetadata(
            importance=1., offload_eligible=True, reusable_prefix=True,
        ),
        "pressure": Pressure(.9, 0, 2, 30, 10, 0, 10),
        "blocks": 20, "cpu_available": 100, "cpu_needed": 20,
        "duration": 10., "transfer_time": .5,
    }
    arguments.update(changes)
    return public_offload_benefit(**arguments)


def test_offload_requires_real_reusable_state_host_capacity_and_useful_waiting_work():
    assert benefit().reason is DecisionReason.SELECTED
    assert benefit(metadata=TokenCakeMetadata()).reason is DecisionReason.NOT_ELIGIBLE
    assert benefit(cpu_available=0).reason is DecisionReason.CPU_CAPACITY
    assert benefit(blocks=0).reason is DecisionReason.EMPTY
    assert benefit(pressure=Pressure(.9, 0, 2, 30, 10, 0, 0)).reason is DecisionReason.NO_WAITING_DEMAND
    assert benefit(duration=.1).reason is DecisionReason.UNPROFITABLE


def test_waiting_fit_uses_queue_order_native_eligibility_and_estimated_work():
    waiting = (
        WaitingWork("oversize", 100, 10),
        WaitingWork("too_much_work", 10, 10000),
        WaitingWork("native_locked", 10, 10, False),
        WaitingWork("first", 12, 50),
        WaitingWork("smaller_later", 1, 1),
    )
    assert first_fit_work(
        waiting, freed_blocks=20, window_seconds=1., observed_tokens_per_second=100.,
    ) == "first"
    assert first_fit_work(
        waiting, freed_blocks=20, window_seconds=0., observed_tokens_per_second=100.,
    ) is None


def upload(identity="a", remaining=101, deficit=101, importance=1., urgency=1., finish=20.):
    return UploadCandidate(identity, remaining, deficit, importance, urgency, finish)


def test_paper_upload_protects_critical_waiting_capacity_before_progressive_reservation():
    grants = paper_upload_reservations(
        (upload(),), gpu_free=100, critical_waiting_demand=80, shared_free=20,
    )
    assert grants == {"a": 40}
    assert paper_upload_reservations(
        (upload(),), gpu_free=10, critical_waiting_demand=80, shared_free=20,
    ) == {}


def test_half_deficit_reservations_converge_and_do_not_claim_a_physical_transfer():
    reserved, deficit = 0, 101
    sequence = []
    while deficit:
        amount = paper_upload_reservations(
            (upload(deficit=deficit),), gpu_free=200 - reserved,
            critical_waiting_demand=0, shared_free=0,
        )["a"]
        sequence.append(amount)
        reserved += amount
        deficit -= amount
    assert sequence == [51, 25, 13, 6, 3, 2, 1]
    assert reserved == 101


def test_upload_ranking_uses_importance_plus_urgency_then_earliest_deadline():
    grants = paper_upload_reservations(
        (upload("later", finish=30.), upload("early", finish=10.), upload("critical", importance=10.)),
        gpu_free=70, critical_waiting_demand=0, shared_free=0,
    )
    assert grants == {"critical": 51, "early": 19}


@pytest.mark.parametrize("candidate", (
    upload(deficit=102), upload(deficit=-1), upload(urgency=math.nan), upload(remaining=-1),
))
def test_invalid_or_repeated_upload_ownership_is_rejected(candidate):
    with pytest.raises(ValueError):
        paper_upload_reservations(
            (candidate,), gpu_free=200, critical_waiting_demand=0, shared_free=0,
        )
    with pytest.raises(ValueError):
        paper_upload_reservations(
            (upload(), upload()), gpu_free=200, critical_waiting_demand=0, shared_free=0,
        )
