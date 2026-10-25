import random

import pytest

from beliefkv.policy.paper_baselines import ThunderWorkingSet
from beliefkv.policy.thunder_agent_migration import ThunderAgentMigration


def state(policy):
    fields = (
        "tokens", "steps", "acting", "active", "marked_for_pause",
        "pending", "queued_at", "queue_sequence",
    )
    return {
        identity: tuple(getattr(item, name) for name in fields)
        for identity, item in policy.programs.items()
    }


@pytest.mark.parametrize("seed", (0, 21, 88))
def test_shared_thunder_core_matches_reference_without_blocking_or_restore_extensions(seed):
    rng = random.Random(seed)
    candidate, reference = ThunderAgentMigration(2000), ThunderWorkingSet(2000)
    now, next_control = 0., 0.
    for _ in range(1000):
        now += rng.uniform(.1, .9)
        identity = str(rng.randrange(12))
        item = reference.programs.get(identity)
        if item is None or not item.pending:
            tokens = rng.randrange(100, 500)
            reference.arrive(identity, tokens, now=now)
            candidate.arrive(identity, tokens, now=now)
        elif item.active:
            candidate.begin(identity)
            tokens = item.tokens + rng.randrange(1, 50)
            reference.completed(identity, tokens, now=now)
            candidate.completed(identity, tokens, now=now)
        else:
            candidate.rebalance(now=now)
        if now >= next_control:
            next_control = now + 5.
            reference.tick(now=now)
        assert state(candidate) == state(reference)
        assert candidate.used_tokens == 2000 - reference.remaining_capacity()


def test_blocked_parent_is_not_resumed_by_the_shared_five_second_control_tick():
    policy = ThunderAgentMigration(500)
    policy.arrive("parent", 300, now=0.)
    policy.begin("parent")
    policy.completed("parent", 302, now=.1)
    policy.block("parent", "join", now=.2)
    policy.rebalance(now=5.1)
    assert not policy.agents["parent"].active
    policy.arrive("child", 300, now=5.2)
    assert policy.allowed("child")
    policy.rebalance(now=10.2)
    assert not policy.agents["parent"].active
    assert policy.used_tokens == 400


def test_join_yield_does_not_credit_a_running_parent_before_its_boundary():
    policy = ThunderAgentMigration(500)
    policy.arrive("parent", 300, now=0.)
    policy.begin("parent")
    policy.block("parent", "join", now=.1)
    policy.arrive("child", 300, now=.2)
    assert not policy.allowed("child") and policy.used_tokens == 400
    policy.completed("parent", 302, now=.3)
    assert not policy.agents["parent"].active
    policy.rebalance(now=5.2)
    assert policy.allowed("child")


def test_join_can_supersede_an_ordinary_owner_without_leaking_its_reservation():
    policy = ThunderAgentMigration(500)
    policy.arrive("ordinary", 300, now=0.)
    policy.arrive("parent", 300, now=.1)
    assert policy.reserve_restore("ordinary", now=.2, critical=False)
    assert policy.reserve_restore("parent", now=.3, critical=True)
    assert policy.restore_owner == "parent"
    assert policy.agents["ordinary"].restore_until == 0.
    assert not policy.agents["ordinary"].active
    assert policy.used_tokens == 400


def test_join_reservation_cannot_pause_running_output_or_consume_fake_free_capacity():
    policy = ThunderAgentMigration(500)
    policy.arrive("running", 300, now=0.)
    policy.begin("running")
    policy.arrive("parent", 300, now=.1)
    assert not policy.reserve_restore("parent", now=.2, critical=True)
    assert policy.restore_owner is None and policy.allowed("running")
    assert not policy.agents["parent"].active


def test_expired_blocked_owner_is_released_before_another_join_claims_it():
    policy = ThunderAgentMigration(500)
    policy.arrive("a", 300, now=0.)
    policy.completed("a", 302, now=.1)
    policy.block("a", "join", now=.2)
    policy.arrive("b", 300, now=.3)
    policy.completed("b", 302, now=.4)
    policy.block("b", "join", now=.5)
    assert policy.reserve_restore("a", now=.6, critical=True)
    assert policy.reserve_restore("b", now=2.2, critical=True)
    assert not policy.agents["a"].active
    assert not policy.agents["a"].critical
    assert policy.restore_owner == "b"
    assert policy.used_tokens == 402


def test_repeated_short_tools_keep_the_author_continuation_eligibility():
    policy = ThunderAgentMigration(500)
    policy.arrive("active", 300, now=0.)
    policy.arrive("waiting", 300, now=.1)
    for step in range(100):
        now = float(step * 3)
        policy.begin("active")
        policy.completed("active", 302, now=now + 2.)
        policy.rebalance(now=now + 2.1)
        assert policy.agents["active"].active
        assert not policy.allowed("waiting")
        policy.arrive("active", 300, now=now + 2.2)
    assert policy.counts["activated"] == 1


def test_bounded_starvation_switches_only_a_safe_nonrunning_owner():
    policy = ThunderAgentMigration(500)
    policy.arrive("active", 300, now=0.)
    policy.arrive("waiting", 300, now=.1)
    policy.begin("active")
    policy.rebalance(now=1801.)
    assert not policy.allowed("waiting")
    policy.completed("active", 302, now=1801.1)
    policy.rebalance(now=1806.1)
    assert policy.allowed("waiting") and not policy.agents["active"].active


@pytest.mark.parametrize("seed", (0, 21, 88))
def test_random_blocking_restore_and_requeue_sequences_preserve_ownership(seed):
    rng, policy = random.Random(seed), ThunderAgentMigration(1000)
    now = 0.
    for _ in range(2000):
        now += rng.uniform(.01, .5)
        identity = str(rng.randrange(18))
        item = policy.agents.get(identity)
        action = rng.randrange(8)
        if item is None or action == 0 and not item.pending:
            policy.arrive(identity, rng.randrange(50, 500), now=now)
        elif action == 1 and policy.allowed(identity):
            policy.begin(identity)
        elif action == 2 and item.in_flight:
            policy.completed(identity, item.tokens + rng.randrange(1, 20), now=now)
        elif action == 3:
            policy.block(identity, "join", now=now)
        elif action == 4:
            policy.unblock(identity, now=now, critical=bool(rng.randrange(2)))
        elif action == 5:
            policy.reserve_restore(identity, now=now, critical=bool(rng.randrange(2)))
        elif action == 6 and not item.in_flight:
            policy.forget(identity, now=now)
        elif action == 7 and item.in_flight:
            policy.requeued(identity, now=now)
        policy.rebalance(now=now)
        assert policy.used_tokens == sum(
            program.tokens + policy.buffer_tokens
            for program in policy.agents.values() if program.active
        )
        for program in policy.agents.values():
            assert not program.in_flight or program.active
            if program.active and program.blocked and not program.in_flight:
                assert program.restore_until > now
                assert policy.restore_owner == program.identity
