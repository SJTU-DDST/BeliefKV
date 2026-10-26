from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from beliefkv.policy.tokencake import TokenCakeMetadata
from beliefkv.policy.tokencake_lifecycle import (
    LifecycleEvent, LifecycleSettings, TokenCakeLifecycleRegistry,
)


class Retention:
    def __init__(self):
        self.ready = {"first", "second"}
        self.owned, self.released = set(), []

    def retain(self, keys):
        result = keys & self.ready
        self.owned.update(result)
        return result

    def release(self, keys):
        self.owned.difference_update(keys)
        self.released.append(set(keys))


@pytest.fixture
def lifecycle():
    clock, manager = NS(now=10.), Retention()
    registry = TokenCakeLifecycleRegistry(manager=manager, clock=lambda: clock.now)
    return registry, clock, manager


def associate(registry, identifier="a", **metadata):
    registry.associate(identifier, TokenCakeMetadata(agent_type="worker", **metadata))


@pytest.mark.parametrize("finish_first", (False, True))
def test_start_and_generation_completion_can_arrive_in_either_order(lifecycle, finish_first):
    registry, _, manager = lifecycle
    associate(registry)
    if finish_first:
        registry.generation_finished("a", "completed", snapshot="prefix")
        assert registry.records["a"].state == "awaiting_start"
    started = LifecycleEvent("stall_started", "a", "external")
    assert registry.apply(started).state == "active"
    if not finish_first:
        assert not registry.records["a"].pending_evaluation
        registry.generation_finished("a", "completed", snapshot="prefix")
    record = registry.records["a"]
    assert record.snapshot == "prefix" and record.pending_evaluation
    assert registry.retain_ready(record, {"first", "not_ready"}) == {"first"}
    assert manager.owned == {"first"}
    assert registry.apply(started).disposition == "duplicate"
    assert registry.apply(replace(started, kind="other")).status_code == 409


def test_actual_finish_learns_duration_and_releases_each_owned_key(lifecycle):
    registry, clock, manager = lifecycle
    associate(registry)
    registry.generation_finished("a", "completed")
    registry.apply(LifecycleEvent("stall_started", "a", "external", 10.))
    record = registry.records["a"]
    assert registry.retain_ready(record, {"first", "second"}) == {"first", "second"}
    clock.now += 4.
    result = registry.apply(LifecycleEvent("stall_finished", "a"))
    assert result.state == "finished" and result.disposition == "applied"
    assert manager.released == [{"first", "second"}] and not manager.owned
    assert registry.history.predict(("type", "worker", "external")) == 4.
    associate(registry, "b")
    registry.apply(LifecycleEvent("stall_started", "b", "external", 10.))
    assert registry.records["b"].predicted_duration == 7.
    assert registry.apply(LifecycleEvent("stall_finished", "a")).disposition == "duplicate"


def test_public_release_deadline_drops_host_ownership_without_faking_a_finish(lifecycle):
    registry, clock, manager = lifecycle
    associate(registry)
    registry.apply(LifecycleEvent("stall_started", "a", "external", 10.))
    registry.retain_ready(registry.records["a"], {"first"})
    registry.set_h2d_estimate(registry.records["a"], 2.)
    assert registry.records["a"].release_deadline == 17.5
    clock.now = 17.5
    registry.expire()
    record = registry.records["a"]
    assert record.state == "released" and record.terminal_at is None
    assert not manager.owned
    assert not registry.retain_ready(record, {"second"})
    clock.now = 19.
    assert registry.apply(LifecycleEvent("stall_finished", "a")).state == "finished"
    assert registry.history.predict(("type", "worker", "external")) == 9.


@pytest.mark.parametrize("estimate,deadline", ((1., 70.), (100., 410.), (2000., 3610.)))
def test_safety_deadline_uses_the_public_floor_and_cap(lifecycle, estimate, deadline):
    registry, clock, _ = lifecycle
    associate(registry)
    registry.apply(LifecycleEvent("stall_started", "a", "external", estimate))
    assert registry.records["a"].safety_deadline == deadline
    clock.now = deadline
    registry.expire()
    assert registry.records["a"].state == "expired"
    assert registry.apply(LifecycleEvent("stall_finished", "a")).disposition == "late_finish"
    assert not registry.history.history


def test_expired_stall_cannot_reuse_an_identifier_while_its_generation_is_live(lifecycle):
    registry, clock, _ = lifecycle
    associate(registry)
    registry.apply(LifecycleEvent("stall_started", "a"))
    clock.now += 121.
    registry.expire()
    clock.now += 60.
    registry.expire()
    assert "a" not in registry.records and "a" in registry.generations
    with pytest.raises(ValueError, match="already belongs"):
        associate(registry)
    registry.generation_finished("a", "completed")
    associate(registry)
    assert registry.records["a"].state == "generating"


def test_unattached_completed_generation_expires_then_retains_a_tombstone(lifecycle):
    registry, clock, _ = lifecycle
    associate(registry)
    registry.generation_finished("a", "completed", snapshot="prefix")
    clock.now = 70.
    registry.expire()
    assert registry.records["a"].state == "expired"
    clock.now = 130.
    registry.expire()
    assert "a" not in registry.records
    assert registry.apply(LifecycleEvent("stall_started", "a")).status_code == 404


@pytest.mark.parametrize("cause", ("aborted", "error"))
def test_failed_generations_release_started_stalls_and_never_enter_preservation(lifecycle, cause):
    registry, _, manager = lifecycle
    associate(registry)
    registry.apply(LifecycleEvent("stall_started", "a"))
    registry.retain_ready(registry.records["a"], {"first"})
    registry.generation_finished("a", cause, snapshot="ignored")
    assert registry.records["a"].state == cause and not manager.owned
    assert registry.records["a"].snapshot is None


def test_anonymous_stall_observation_is_not_used_as_an_unrelated_type_forecast(lifecycle):
    registry, clock, _ = lifecycle
    registry.associate("a", TokenCakeMetadata())
    registry.apply(LifecycleEvent("stall_started", "a"))
    clock.now += .2
    registry.apply(LifecycleEvent("stall_finished", "a"))
    assert registry.records["a"].anonymous_sample == pytest.approx(.2)
    assert not registry.history.history


def test_disabled_lifecycle_terminalizes_completed_generation_without_offloading(lifecycle):
    _, clock, manager = lifecycle
    registry = TokenCakeLifecycleRegistry(
        LifecycleSettings(enabled=False), manager, clock=lambda: clock.now,
    )
    associate(registry)
    registry.generation_finished("a", "completed", snapshot="ignored")
    assert registry.records["a"].state == "completed"
    assert registry.records["a"].snapshot is None
