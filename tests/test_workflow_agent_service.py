import random

import pytest

from beliefkv.policy.workflow_agent_service import WorkflowAgentMigration


def arrive(policy, agent, workflow, *, now=0., tokens=300, critical=False):
    policy.arrive(
        agent, tokens, now=now, workflow_id=workflow, critical=critical,
    )


def serve(policy, agents, *, now):
    for agent in agents:
        if not policy.agents[agent].in_flight:
            policy.begin(agent)
    policy.observe_service(agents, now=now)


def test_waiting_root_gets_a_bounded_opportunity_at_a_safe_agent_boundary():
    policy = WorkflowAgentMigration(500)
    arrive(policy, "a", "root-a")
    arrive(policy, "b", "root-b", now=.1)
    serve(policy, ["a"], now=.2)
    policy.rebalance(now=31.)
    assert policy.allowed("a") and not policy.allowed("b")
    assert policy.counts["workflow_handover_capacity_deferred"] == 1
    policy.completed("a", 302, now=31.1)
    assert policy.allowed("b") and not policy.agents["a"].active
    assert policy.counts["workflow_handover:first_service"] == 1
    assert not policy.deferred_opportunity
    assert policy.used_tokens == 400


def test_one_successful_handover_keeps_its_cooldown_across_more_completion_events():
    policy = WorkflowAgentMigration(1000)
    for identity in ("a", "b", "c", "d"):
        arrive(policy, identity, identity, now=0.)
    policy.rebalance(now=5.)
    serve(policy, ["a", "b"], now=5.1)
    policy.rebalance(now=31.)
    assert policy.deferred_opportunity
    policy.completed("a", 302, now=31.1)
    assert policy.counts["workflow_handover:first_service"] == 1
    policy.completed("b", 302, now=31.2)
    assert policy.counts["workflow_handover:first_service"] == 1


def test_ready_gap_excludes_time_with_only_tool_or_join_blocking():
    policy = WorkflowAgentMigration(1000)
    arrive(policy, "a", "root")
    serve(policy, ["a"], now=.1)
    policy.completed("a", 302, now=.2)
    policy.block("a", "long_tool", now=.3)
    policy.rebalance(now=600.)
    assert policy.service["root"].gap(600.) == 0.
    arrive(policy, "a", "root", now=601.)
    assert policy.service["root"].gap(602.) == 1.
    assert policy._debt("root", 602.) == 1. / 15.
    serve(policy, ["a"], now=603.)
    assert policy.service["root"].max_ready_gap == 2.
    assert policy.service["root"].first_service == .1


def test_descendants_share_one_first_service_and_one_batch_credit():
    policy = WorkflowAgentMigration(2000)
    arrive(policy, "child-a", "root")
    arrive(policy, "child-b", "root", now=.1)
    serve(policy, ["child-a", "child-b", "child-a"], now=.2)
    account = policy.service["root"]
    assert account.first_submit == 0. and account.first_service == .2
    assert account.service_batches == 1
    policy.completed("child-a", 302, now=.3)
    assert account.ready == {"child-b"}
    policy.forget("child-a", now=.4)
    policy.forget("child-b", now=.5)
    arrive(policy, "parent", "root", now=50.)
    assert policy.service["root"].first_service == .2
    assert policy.service["root"].gap(51.) == 1.


def test_resumption_queue_does_not_multiply_workflow_entitlement_by_fanout():
    policy = WorkflowAgentMigration(1000)
    arrive(policy, "holder", "busy", tokens=800)
    serve(policy, ["holder"], now=.1)
    for index in range(4):
        arrive(policy, "child-" + str(index), "wide", now=1. + index * .01)
    arrive(policy, "single", "narrow", now=1.1)
    policy.forget("holder", now=2.)
    policy.rebalance(now=5.2)
    assert policy.allowed("single")
    assert sum(policy.allowed("child-" + str(index)) for index in range(4)) == 1
    assert policy.used_tokens == 800


def test_newly_observed_sibling_uses_free_budget_without_waiting_five_seconds():
    policy = WorkflowAgentMigration(1000)
    arrive(policy, "first", "root")
    arrive(policy, "large-waiter", "other-root", tokens=800, now=.1)
    arrive(policy, "second", "root", now=.2)
    assert not policy.allowed("second")
    policy.observe_join("join", {"first", "second"}, now=.3)
    assert policy.allowed("first") and policy.allowed("second")
    assert policy.used_tokens == 800
    assert policy.counts["sibling_coadmitted"] == 1
    policy.forget_join("join")
    assert not policy.siblings and not policy.join_members


def test_sibling_never_waits_for_a_peer_and_cannot_evict_or_overcommit():
    policy = WorkflowAgentMigration(500)
    policy.observe_join("join", {"first", "second"}, now=0.)
    arrive(policy, "first", "root", now=.1)
    assert policy.allowed("first")
    serve(policy, ["first"], now=.2)
    arrive(policy, "second", "root", now=.3)
    assert policy.allowed("first") and not policy.allowed("second")
    assert policy.used_tokens == 400 and policy.agents["first"].in_flight


def test_sibling_cannot_steal_an_overdue_roots_free_capacity():
    policy = WorkflowAgentMigration(1000)
    arrive(policy, "first", "wide", tokens=800)
    serve(policy, ["first"], now=.1)
    arrive(policy, "other", "old", now=.2)
    arrive(policy, "second", "wide", now=.3)
    # Freeze the five-second loop to exercise the JOIN event path alone.
    policy.next_control = policy.next_opportunity = 100.
    policy.completed("first", 302, now=30.)
    policy.observe_join("join", {"first", "second"}, now=31.)
    assert not policy.allowed("second")
    assert policy.service["old"].first_service is None


@pytest.mark.parametrize("protection", ("critical", "lease", "tenure"))
def test_workflow_handover_keeps_join_reservations_and_minimum_agent_tenure(protection):
    policy = WorkflowAgentMigration(500)
    arrive(policy, "owner", "a")
    arrive(policy, "waiter", "b", now=.1)
    if protection == "critical":
        policy.agents["owner"].critical = True
    elif protection == "lease":
        policy.agents["owner"].restore_until = 60.
    else:
        policy.active_since["owner"] = 30.
    policy.rebalance(now=31.)
    assert policy.allowed("owner") and not policy.allowed("waiter")
    assert policy.counts["workflow_handover_donors"] == 0


def test_ready_peer_with_its_own_service_debt_is_not_an_easy_victim():
    policy = WorkflowAgentMigration(500)
    arrive(policy, "owner", "a")
    serve(policy, ["owner"], now=.1)
    policy.requeued("owner", now=.2)
    arrive(policy, "waiter", "b", now=.3)
    policy.rebalance(now=40.)
    assert policy.allowed("owner") and not policy.allowed("waiter")


def test_service_observation_does_not_iterate_the_waiting_population():
    policy = WorkflowAgentMigration(500)
    arrive(policy, "running", "root")
    serve(policy, ["running"], now=.1)

    class NoPopulationScan(dict):
        def __iter__(self):
            raise AssertionError("decode must only touch its selected batch")

        def values(self):
            raise AssertionError("decode must only touch its selected batch")

        def items(self):
            raise AssertionError("decode must only touch its selected batch")

    policy.programs = NoPopulationScan(policy.programs)
    policy.service = NoPopulationScan(policy.service)
    policy.observe_service(("running", "unknown"), now=.2)
    assert policy.service["root"].service_batches == 2


@pytest.mark.parametrize("seed", (0, 21, 88))
def test_workflow_events_preserve_logical_ownership_and_shared_readiness(seed):
    rng, policy = random.Random(seed), WorkflowAgentMigration(2000)
    now = 0.
    for _ in range(1500):
        now += rng.uniform(.01, 1.)
        identity = str(rng.randrange(24))
        item = policy.agents.get(identity)
        action = rng.randrange(8)
        if item is None or action == 0 and not item.pending:
            arrive(policy, identity, "root-" + str(int(identity) % 6), now=now,
                   tokens=rng.randrange(50, 300))
        elif action == 1 and policy.allowed(identity):
            serve(policy, [identity], now=now)
        elif action == 2 and item.in_flight:
            policy.completed(identity, item.tokens + 1, now=now)
        elif action == 3:
            policy.block(identity, "join", now=now)
        elif action == 4:
            policy.unblock(identity, now=now)
        elif action == 5:
            policy.reserve_restore(identity, now=now, critical=True)
        elif action == 6 and not item.in_flight:
            policy.forget(identity, now=now)
        elif action == 7 and item.in_flight:
            policy.requeued(identity, now=now)
        policy.rebalance(now=now)
        assert policy.used_tokens == sum(
            agent.tokens + policy.buffer_tokens
            for agent in policy.agents.values() if agent.active
        )
        for agent in policy.agents.values():
            assert not agent.in_flight or agent.active
            workflow = policy.workflow_by_agent[agent.identity]
            assert (agent.identity in policy.service[workflow].ready) is agent.pending
