import random

import pytest

from beliefkv.policy.agent_working_set import AgentWorkingSet


def test_short_tool_return_retains_agent_budget_across_different_requests():
    policy = AgentWorkingSet(500)
    policy.arrive("a", 300, now=0.)
    policy.begin("a")
    policy.arrive("b", 300, now=.1)
    policy.completed("a", 302, now=.2)
    assert policy.agents["a"].active
    assert not policy.allowed("b")
    policy.arrive("a", 310, now=.4)
    assert policy.allowed("a") and not policy.allowed("b")
    assert policy.used_tokens == 410


def test_join_yield_never_credits_capacity_from_a_running_call():
    policy = AgentWorkingSet(500)
    policy.arrive("parent", 300, now=0.)
    policy.begin("parent")
    policy.arrive("child", 300, now=.1)
    policy.block("parent", "join", now=.2)
    assert policy.agents["parent"].active
    assert policy.agents["parent"].pause_at_boundary
    assert not policy.allowed("child")
    policy.completed("parent", 302, now=.3)
    assert not policy.agents["parent"].active
    assert policy.allowed("child")
    assert policy.used_tokens == 400


def test_observed_join_yield_allows_child_without_aging_or_future_graph():
    policy = AgentWorkingSet(500)
    policy.arrive("parent", 300, now=0.)
    policy.completed("parent", 302, now=.1)
    policy.block("parent", "join", now=.2)
    policy.arrive("child", 300, now=.3)
    assert policy.allowed("child")
    assert policy.agents["parent"].blocked == "join"


def test_context_growth_rebalances_at_completion_without_interrupting_peers():
    policy = AgentWorkingSet(1000)
    policy.arrive("a", 400, now=0.)
    policy.arrive("b", 300, now=.1)
    policy.begin("a")
    policy.begin("b")
    policy.completed("a", 600, now=.2)
    assert policy.used_tokens <= policy.capacity
    assert policy.allowed("b") and policy.agents["b"].in_flight
    assert not policy.agents["a"].active


def test_aging_can_admit_a_waiter_without_preempting_running_output():
    policy = AgentWorkingSet(500)
    policy.arrive("a", 300, now=0.)
    policy.completed("a", 302, now=.1)
    policy.arrive("b", 300, now=.2)
    policy.arrive("a", 310, now=.3)
    policy.begin("a")
    policy.rebalance(now=11.)
    assert not policy.allowed("b")
    policy.completed("a", 312, now=11.1)
    assert policy.allowed("b")


def test_join_restore_has_one_budgeted_opportunity_then_native_consumes_it():
    policy = AgentWorkingSet(500)
    policy.arrive("parent", 300, now=0.)
    policy.completed("parent", 302, now=.1)
    policy.block("parent", "join", now=.2)
    policy.arrive("normal", 300, now=.3)
    assert policy.reserve_restore("parent", now=.4, critical=True)
    assert policy.agents["parent"].active and not policy.allowed("parent")
    assert not policy.allowed("normal")
    policy.unblock("parent", now=.5, critical=True)
    policy.arrive("parent", 310, now=.6)
    assert policy.allowed("parent")
    policy.begin("parent")
    assert policy.restore_owner is None
    assert policy.agents["parent"].restore_until == 0.


def test_restore_expiry_does_not_renew_itself_or_destroy_pending_agent():
    policy = AgentWorkingSet(500)
    policy.arrive("a", 300, now=0.)
    assert policy.reserve_restore("a", now=.1, critical=False)
    deadline = policy.agents["a"].restore_until
    assert policy.reserve_restore("a", now=.2, critical=False)
    assert policy.agents["a"].restore_until == deadline
    policy.rebalance(now=2.)
    assert policy.restore_owner is None
    assert policy.allowed("a")


def test_expired_parent_warming_releases_capacity_without_claiming_dma_cancellation():
    policy = AgentWorkingSet(500)
    policy.arrive("parent", 300, now=0.)
    policy.completed("parent", 302, now=.1)
    policy.block("parent", "join", now=.2)
    assert policy.reserve_restore("parent", now=.3, critical=True)
    policy.arrive("child", 300, now=.4)
    policy.rebalance(now=2.)
    assert not policy.agents["parent"].active
    assert policy.allowed("child") and policy.restore_owner is None


def test_recovery_budget_does_not_evict_or_credit_a_running_agent():
    policy = AgentWorkingSet(500)
    policy.arrive("parent", 300, now=0.)
    policy.completed("parent", 302, now=.1)
    policy.block("parent", "join", now=.2)
    policy.arrive("child", 300, now=.3)
    policy.begin("child")
    assert not policy.reserve_restore("parent", now=.4, critical=True)
    assert policy.allowed("child") and policy.used_tokens == 400
    assert policy.restore_owner is None


def test_one_ordinary_recovery_consumer_and_join_supersession():
    policy = AgentWorkingSet(1000)
    for index, identity in enumerate(("a", "b", "parent")):
        policy.arrive(identity, 100, now=float(index))
    assert policy.reserve_restore("a", now=3., critical=False)
    assert not policy.reserve_restore("b", now=3.1, critical=False)
    assert policy.reserve_restore("parent", now=3.2, critical=True)
    assert policy.restore_owner == "parent"
    assert policy.agents["a"].restore_until == 0.


def test_cancel_releases_a_reserved_agent_and_activates_pending_peer():
    policy = AgentWorkingSet(500)
    policy.arrive("a", 300, now=0.)
    policy.arrive("b", 300, now=.1)
    assert policy.reserve_restore("a", now=.2, critical=False)
    policy.forget("a", now=.3)
    assert policy.allowed("b")
    assert policy.used_tokens == 400 and policy.restore_owner is None


def test_agent_budget_rejects_overlapping_calls():
    policy = AgentWorkingSet(500)
    policy.arrive("a", 100, now=0.)
    with pytest.raises(ValueError, match="concurrent"):
        policy.arrive("a", 110, now=.1)


def test_new_join_reservation_cannot_orphan_an_expired_blocked_parent():
    policy = AgentWorkingSet(500)
    for index, identity in enumerate(("a", "b")):
        policy.arrive(identity, 300, now=index * .1)
        policy.completed(identity, 302, now=index * .1 + .01)
        policy.block(identity, "join", now=index * .1 + .02)
    assert policy.reserve_restore("a", now=.3, critical=True)
    assert policy.reserve_restore("b", now=2., critical=True)
    assert not policy.agents["a"].active
    assert not policy.agents["a"].critical
    assert policy.restore_owner == "b" and policy.used_tokens == 402


@pytest.mark.parametrize("seed", (0, 21, 88))
def test_random_event_sequences_preserve_budget_and_blocked_agent_ownership(seed):
    rng = random.Random(seed)
    policy = AgentWorkingSet(1000)
    now = 0.
    for _ in range(2000):
        now += rng.uniform(.01, .5)
        identity = str(rng.randrange(18))
        item = policy.agents.get(identity)
        action = rng.randrange(7)
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
        policy.rebalance(now=now)
        assert policy.used_tokens == sum(
            item.tokens + policy.buffer_tokens
            for item in policy.agents.values() if item.active
        )
        for item in policy.agents.values():
            assert not item.in_flight or item.active
            if item.active and item.blocked and not item.in_flight:
                assert item.restore_until > now
                assert policy.restore_owner == item.identity


@pytest.mark.parametrize("capacity", (0, -1, True))
def test_capacity_requires_measured_positive_native_tokens(capacity):
    with pytest.raises(ValueError, match="capacity"):
        AgentWorkingSet(capacity)
