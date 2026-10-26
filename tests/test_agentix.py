from dataclasses import replace
import math

import pytest

from beliefkv.policy.agentix import AgentixConfig, AgentixScheduler


def configuration(**overrides):
    return AgentixConfig(**{
        "mode": "atlas", "band_edges_ms": (10., 30.),
        "quanta_ms": (4., 8., 16.), "starvation_ratio": 1000.,
        "decode_steps_per_schedule": 4, **overrides,
    })


def select(policy, ready, *, limit=1, resident=(), now=0.):
    return policy.plan(
        ready, resident=resident, can_fit=lambda request, batch: len(batch) < limit,
        now_ms=now,
    )


def execute(policy, requests, duration, *, now=0.):
    policy.running(requests, now_ms=now)
    policy.charge(requests, executor_ms=duration)


@pytest.mark.parametrize("mode,expected", (("plas", 12.), ("atlas", 9.)))
def test_children_share_root_service_and_parallel_work_uses_declared_program_mode(mode, expected):
    policy = AgentixScheduler(configuration(mode=mode))
    policy.arrive("parent", "workflow", now_ms=0.)
    execute(policy, ["parent"], 4.)
    policy.complete("parent", now_ms=4.)
    policy.arrive("child-a", "workflow", now_ms=1000.)
    policy.arrive("child-b", "workflow", now_ms=1000.)
    assert policy.service.calls["child-a"].inherited_service_ms == 4.
    assert policy.service.calls["child-b"].inherited_service_ms == 4.
    execute(policy, ["child-a", "child-b"], 3., now=1000.)
    policy.complete("child-a", now_ms=1003.)
    execute(policy, ["child-b"], 2., now=1003.)
    assert policy.complete("child-b", now_ms=1005.) == expected
    policy.arrive("joined-parent", "workflow", now_ms=2000.)
    assert policy.service.calls["joined-parent"].inherited_service_ms == expected
    assert policy.program_wait_ms == {"workflow": 0.}


def test_fcfs_inside_a_band_does_not_gain_our_join_or_resident_priority():
    policy = AgentixScheduler(configuration())
    for identity in ("earlier", "resident", "joined"):
        policy.arrive(identity, identity, now_ms=0.)
    assert select(
        policy, ["joined", "resident", "earlier"], resident=["resident"], limit=3,
    ) == ("earlier", "resident", "joined")


def test_arriving_calls_inherit_completed_service_band_instead_of_resetting_to_q1():
    policy = AgentixScheduler(configuration())
    policy.arrive("old", "old-program", now_ms=0.)
    execute(policy, ["old"], 12.)
    policy.complete("old", now_ms=12.)
    assert policy.arrive("old-successor", "old-program", now_ms=1000.) == 1
    assert policy.arrive("new", "new-program", now_ms=1000.) == 0
    assert select(policy, ["old-successor", "new"], now=1000.) == ("new",)


def test_quantum_exhaustion_preempts_only_at_multi_step_schedule_boundary():
    policy = AgentixScheduler(configuration())
    for identity in ("a", "b"):
        policy.arrive(identity, identity, now_ms=0.)
    assert select(policy, ["a", "b"]) == ("a",)
    execute(policy, ["a"], 5.)
    policy.decode_completed(3)
    assert select(policy, ["a", "b"], now=5.) == ("a",)
    assert policy.calls["a"].queue == 0
    policy.decode_completed()
    assert select(policy, ["a", "b"], now=5.) == ("b",)
    assert policy.calls["a"].queue == 1
    assert policy.calls["a"].quantum_left_ms == 8.


def test_between_full_schedules_only_gpu_resident_queued_calls_refill_open_slots():
    policy = AgentixScheduler(configuration())
    for identity in ("a", "b", "cold", "resident"):
        policy.arrive(identity, identity, now_ms=0.)
    assert select(policy, list(policy.calls), limit=2) == ("a", "b")
    execute(policy, ["a", "b"], 1.)
    policy.complete("a", now_ms=1.)
    assert select(
        policy, ["b", "cold", "resident"], resident=["b", "resident"], limit=2, now=1.,
    ) == ("b", "resident")
    assert policy.counts["full_schedules"] == 1
    assert policy.counts["resident_refill_schedules"] == 1


def test_all_previous_calls_finished_allows_cold_work_before_next_decode_epoch():
    policy = AgentixScheduler(configuration())
    for identity in ("first", "cold"):
        policy.arrive(identity, identity, now_ms=0.)
    assert select(policy, ["first", "cold"]) == ("first",)
    execute(policy, ["first"], 1.)
    policy.complete("first", now_ms=1.)
    assert select(policy, ["cold"], now=1.) == ("cold",)
    assert policy.counts["full_schedules"] == 2


def test_read_only_schedule_due_does_not_consume_the_epoch_before_native_drain():
    policy = AgentixScheduler(configuration())
    policy.arrive("a", "root", now_ms=0.)
    assert policy.schedule_due(["a"])
    assert policy.schedule_due(["a"])
    assert not policy.counts["full_schedules"]
    assert select(policy, ["a"]) == ("a",)
    assert not policy.schedule_due(["a"])
    policy.decode_completed(4)
    assert policy.schedule_due(["a"])
    assert policy.schedule_due(["a"])
    assert policy.counts["full_schedules"] == 1
    assert select(policy, ["a"]) == ("a",)
    assert not policy.schedule_due(["a"])


def test_delayed_policy_registration_preserves_native_arrival_and_rejects_future_age():
    policy = AgentixScheduler(configuration())
    with pytest.raises(ValueError, match="after"):
        policy.arrive("future", "root", now_ms=1., queued_since_ms=2.)
    assert not policy.calls and not policy.service.service_ms
    policy.arrive("a", "root", now_ms=5., queued_since_ms=1.)
    execute(policy, ["a"], 2., now=6.)
    assert policy.calls["a"].total_wait_ms == 5.
    policy.complete("a", now_ms=8.)
    assert policy.program_wait_ms["root"] == 5.


def test_paper_scan_stops_at_first_unfitting_call_and_never_authorizes_native_admission():
    policy = AgentixScheduler(configuration())
    for identity in ("large", "small"):
        policy.arrive(identity, identity, now_ms=0.)
    inspected = []

    def can_fit(request, chosen):
        inspected.append(request)
        return request == "small"

    assert policy.plan(
        ["large", "small"], resident=["small"], can_fit=can_fit, now_ms=0.,
    ) == ()
    assert inspected == ["large"]
    assert policy.executing == set()
    assert policy.status()["physical_execution_or_swap_authorized"] is False


def test_aging_uses_program_history_and_resets_fairness_without_erasing_completed_work():
    policy = AgentixScheduler(configuration(starvation_ratio=2.))
    policy.arrive("initial", "program", now_ms=0.)
    execute(policy, ["initial"], 12.)
    policy.complete("initial", now_ms=12.)
    policy.arrive("long", "program", now_ms=12.)
    policy.arrive("peer", "peer", now_ms=12.)
    assert select(policy, ["long", "peer"], now=12.) == ("peer",)
    execute(policy, ["long"], 2., now=12.)
    policy.running([], now_ms=14.)
    policy.decode_completed(4)
    assert select(policy, ["long", "peer"], now=43.) == ("peer",)
    assert policy.calls["long"].queue == 0
    assert policy.calls["long"].model_ms == 0.
    assert policy.calls["long"].total_model_ms == 2.
    execute(policy, ["long"], 3., now=43.)
    assert policy.complete("long", now_ms=46.) == 17.
    assert policy.program_wait_ms["program"] == 29.
    assert policy.counts["promoted"] == 1


def test_waited_but_unserved_demoted_call_avoids_division_by_zero_and_returns_to_q1():
    policy = AgentixScheduler(configuration(starvation_ratio=2.))
    policy.arrive("a", "a", now_ms=0.)
    policy.calls["a"].quantum_left_ms = 0.
    assert select(policy, ["a"], now=1.) == ("a",)
    assert policy.calls["a"].queue == 0
    assert policy.counts["promoted"] == 1


def test_last_queue_exhaustion_rotates_without_losing_or_duplicating_requests():
    policy = AgentixScheduler(configuration(band_edges_ms=(), quanta_ms=(4.,)))
    for identity in ("a", "b"):
        policy.arrive(identity, identity, now_ms=0.)
    assert select(policy, ["a", "b"]) == ("a",)
    execute(policy, ["a"], 4.)
    policy.decode_completed(4)
    assert select(policy, ["a", "b"], now=4.) == ("b",)
    assert list(policy.queues[0]) == ["b", "a"]
    assert len(policy.calls) == 2


def test_queued_admission_and_external_wall_time_cannot_be_charged_as_executor_service():
    policy = AgentixScheduler(configuration())
    policy.arrive("a", "workflow", now_ms=0.)
    assert select(policy, ["a"], now=10000.) == ("a",)
    with pytest.raises(ValueError, match="queued or external"):
        policy.charge(["a"], executor_ms=10000.)
    assert policy.service.service_ms == {"workflow": 0.}
    execute(policy, ["a"], 2., now=10000.)
    assert policy.complete("a", now_ms=10002.) == 2.
    assert policy.program_wait_ms == {"workflow": 10000.}


@pytest.mark.parametrize("bad", (-1., math.nan, math.inf, True, "1"))
def test_invalid_executor_interval_cannot_partially_charge_a_batch(bad):
    policy = AgentixScheduler(configuration())
    for identity in ("a", "b"):
        policy.arrive(identity, identity, now_ms=0.)
    policy.running(["a", "b"], now_ms=0.)
    with pytest.raises(ValueError):
        policy.charge(["a", "b"], executor_ms=bad)
    assert all(call.total_model_ms == 0. for call in policy.calls.values())


def test_unknown_or_duplicate_membership_does_not_change_actual_running_calls():
    policy = AgentixScheduler(configuration())
    policy.arrive("a", "root", now_ms=0.)
    policy.running(["a"], now_ms=0.)
    for membership in (["a", "missing"], ["a", "a"]):
        with pytest.raises(ValueError, match="identities"):
            policy.running(membership, now_ms=1.)
        assert policy.executing == {"a"}


def test_program_retirement_requires_no_outstanding_calls_and_cancel_never_completes_work():
    policy = AgentixScheduler(configuration())
    policy.arrive("a", "root", now_ms=0.)
    with pytest.raises(ValueError):
        policy.forget("root")
    policy.cancel("a", now_ms=1.)
    policy.forget("root")
    assert not policy.calls and not policy.program_wait_ms
    assert not policy.service.service_ms
    assert not any(policy.queues)
    assert policy.counts["completed"] == 0


@pytest.mark.parametrize("change", (
    {"mode": "tokens"},
    {"band_edges_ms": (10., 10.)},
    {"band_edges_ms": (0., 10.)},
    {"quanta_ms": (4.,)},
    {"quanta_ms": (4., 0., 10.)},
    {"quanta_ms": (4., math.inf, 10.)},
    {"starvation_ratio": 0.},
    {"starvation_ratio": math.nan},
    {"decode_steps_per_schedule": True},
    {"decode_steps_per_schedule": 0},
))
def test_undefined_paper_parameters_must_be_declared_and_validated(change):
    with pytest.raises(ValueError):
        replace(configuration(), **change)
