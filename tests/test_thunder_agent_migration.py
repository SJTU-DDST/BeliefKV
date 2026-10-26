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
def test_shared_thunder_core_matches_reference_without_blocking_or_restore_extensions(seed, retain_blocked=False):
    rng = random.Random(seed)
    candidate, reference = ThunderAgentMigration(2000, retain_blocked=retain_blocked), ThunderWorkingSet(2000)
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


@pytest.mark.parametrize("seed", (0, 21, 88))
def test_retained_admission_preserves_the_reference_without_known_blocking(seed):
    test_shared_thunder_core_matches_reference_without_blocking_or_restore_extensions(
        seed, retain_blocked=True,
    )


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


@pytest.mark.parametrize("capacity,admitted", [(1000, True), (700, False)])
def test_observed_siblings_use_only_available_budget_without_pausing_ready_peer(capacity, admitted):
    policy = ThunderAgentMigration(
        capacity, retain_blocked=True, coadmit_join_children=True,
    )
    policy.observe_sibling_join("join", ("a", "b"), now=0.)
    policy.arrive("a", 300, now=.1)
    policy.arrive("large-peer", 800, now=.2)
    policy.arrive("b", 300, now=.3)
    assert policy.allowed("a")
    assert policy.allowed("b") is admitted
    assert policy.counts["sibling_coadmitted"] == int(admitted)
    assert policy.used_tokens <= capacity
    assert not policy.counts["paused"]
    policy.forget_sibling_join("join")
    assert not policy.observed_sibling_joins and not policy.observed_agent_joins


def test_sibling_admission_never_waits_for_an_unsubmitted_sibling():
    policy = ThunderAgentMigration(
        1000, retain_blocked=True, coadmit_join_children=True,
    )
    policy.observe_sibling_join("join", ("a", "b"), now=0.)
    policy.arrive("a", 300, now=.1)
    policy.begin("a")
    assert policy.agents["a"].in_flight
    assert "b" not in policy.agents


def test_starvation_does_not_rotate_all_old_waiters_or_immediately_readmit_a_victim():
    policy = ThunderAgentMigration(500, retain_blocked=True)
    for index, name in enumerate(("a", "b", "c")):
        policy.arrive(name, 300, now=index * .1)
    policy.rebalance(now=1801.)
    assert policy.allowed("b") and not policy.allowed("a") and not policy.allowed("c")
    assert policy.counts["starvation_handover"] == 1
    policy.rebalance(now=1806.1)
    assert policy.allowed("c") and not policy.allowed("a") and not policy.allowed("b")
    assert policy.counts["starvation_handover"] == 2


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


@pytest.mark.parametrize("reason", ("join", "long_tool", "elapsed_tool"))
def test_blocked_agent_keeps_eligibility_while_its_peer_can_compute(reason):
    policy = ThunderAgentMigration(1000, retain_blocked=True)
    policy.arrive("waiting", 300, now=0.)
    policy.arrive("computing", 300, now=.1)
    policy.begin("computing")
    policy.completed("waiting", 302, now=.2)
    policy.block("waiting", reason, now=.3)
    policy.arrive("new", 300, now=.4)
    policy.rebalance(now=5.2)
    assert policy.agents["waiting"].active
    assert not policy.allowed("new")
    assert policy.allowed("computing")


def test_known_join_stall_lends_only_blocked_budget_without_preempting_output():
    policy = ThunderAgentMigration(500, retain_blocked=True)
    policy.arrive("parent", 300, now=0.)
    policy.completed("parent", 302, now=.1)
    policy.block("parent", "join", now=.2)
    assert policy.agents["parent"].active
    policy.arrive("child", 300, now=.3)
    assert not policy.agents["parent"].active
    assert policy.allowed("child")
    assert policy.counts["capacity_switch:idle"] == 1
    assert policy.counts["idle_work_resumed"] == 1


def test_restore_cannot_revoke_a_ready_peer_in_retained_mode():
    policy = ThunderAgentMigration(500, retain_blocked=True)
    policy.arrive("normal", 300, now=0.)
    policy.arrive("parent", 300, now=.1)
    assert not policy.reserve_restore("parent", now=.2, critical=True)
    assert policy.allowed("normal") and not policy.agents["parent"].active
    assert policy.restore_owner is None


def test_lease_expiry_preserves_preexisting_eligibility_without_retaining_critical_privilege():
    policy = ThunderAgentMigration(500, retain_blocked=True)
    policy.arrive("parent", 300, now=0.)
    policy.completed("parent", 302, now=.1)
    policy.block("parent", "join", now=.2)
    assert policy.reserve_restore("parent", now=.3, critical=True)
    policy.rebalance(now=2.)
    assert policy.agents["parent"].active
    assert not policy.agents["parent"].critical
    assert policy.restore_owner is None


def test_lease_expiry_releases_newly_warmed_eligibility_but_does_not_cancel_dma():
    policy = ThunderAgentMigration(1000)
    policy.arrive("parent", 300, now=0.)
    policy.completed("parent", 302, now=.1)
    policy.block("parent", "join", now=.2)
    policy._pause(policy.agents["parent"], .3)
    assert policy.reserve_restore("parent", now=.4, critical=True)
    assert policy.agents["parent"].active
    policy.rebalance(now=2.)
    assert not policy.agents["parent"].active
    assert policy.restore_owner is None


def test_retained_eos_warming_keeps_normal_eligibility_after_lease_expiry():
    policy = ThunderAgentMigration(1000, retain_blocked=True)
    policy.arrive("parent", 300, now=0.)
    policy.completed("parent", 302, now=.1)
    policy.block("parent", "join", now=.2)
    policy._pause(policy.agents["parent"], .3)
    assert policy.reserve_restore("parent", now=.4, critical=True)
    policy.rebalance(now=2.)
    assert policy.agents["parent"].active
    assert not policy.agents["parent"].critical
    assert policy.restore_owner is None
    policy.unblock("parent", now=2.1, critical=True)
    policy.arrive("parent", 310, now=2.2)
    policy.begin("parent")
    policy.completed("parent", 312, now=2.3)
    policy.block("parent", "long_tool", now=2.4)
    policy.arrive("parent", 320, now=3.)
    assert policy.allowed("parent")
    assert policy.counts["paused"] == 1


@pytest.mark.parametrize("seed", (0, 21, 88))
def test_retained_random_wait_restore_and_boundary_transitions_preserve_ownership(seed):
    rng, policy = random.Random(seed), ThunderAgentMigration(1000, retain_blocked=True)
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
