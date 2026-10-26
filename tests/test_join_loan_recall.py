from types import SimpleNamespace as NS
from unittest.mock import Mock
import json
from pathlib import Path
import random

import pytest

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.policy.thunder_agent_migration import ThunderAgentMigration
from tests.test_agent_working_set_runtime import runtime


def loan_policy(*, recall=True):
    policy = ThunderAgentMigration(1000, retain_blocked=True, recall_join_loans=recall)
    policy.arrive("parent", 500, now=0.)
    policy.completed("parent", 500, now=.1)
    policy.block("parent", "join", now=.2)
    policy.arrive("running", 200, now=.3)
    policy.begin("running")
    policy.arrive("borrower", 400, now=.4)
    assert policy.lend_backed_blocked(lambda _: True, now=1.) == "borrower"
    return policy


def invariant(policy):
    assert policy.used_tokens == sum(
        item.tokens + policy.buffer_tokens for item in policy.agents.values() if item.active
    )
    assert policy.used_tokens <= policy.capacity
    for lender, borrowers in policy.loan_borrowers.items():
        assert lender in policy.agents and not policy.agents[lender].active
        for borrower in borrowers:
            assert policy.agents[borrower].active
            assert lender in policy.borrowed_from[borrower]
    for borrower, lenders in policy.borrowed_from.items():
        for lender in lenders:
            assert borrower in policy.loan_borrowers[lender]
    assert all(not item.in_flight or item.active for item in policy.agents.values())


def test_ready_join_gets_its_loan_back_at_borrower_request_completion():
    policy = loan_policy()
    assert policy.loan_borrowers == {"parent": {"borrower"}}
    policy.begin("borrower")
    policy.unblock("parent", now=1.1, critical=True)
    policy.arrive("parent", 510, now=1.2, critical=True)
    assert not policy.allowed("parent")
    assert policy.agents["borrower"].in_flight
    policy.completed("borrower", 410, now=1.3)
    assert policy.allowed("parent")
    assert not policy.agents["borrower"].active
    assert policy.agents["running"].active and policy.agents["running"].in_flight
    assert policy.counts["join_loan_recall_admitted"] == 1
    assert policy.counts["capacity_switch:join_loan_return"] == 1
    assert not policy.loan_borrowers and not policy.borrowed_from
    invariant(policy)


def test_eos_restore_and_join_ready_without_submitted_request_do_not_recall():
    policy = loan_policy()
    assert not policy.reserve_restore("parent", now=1.05, critical=True)
    policy.unblock("parent", now=1.1, critical=True)
    policy.rebalance(now=1.2)
    assert policy.agents["borrower"].active and not policy.agents["parent"].active
    assert not policy.counts["join_loan_recall_admitted"]
    invariant(policy)


def test_ordinary_reentry_does_not_recall_borrowed_agent():
    policy = loan_policy()
    policy.unblock("parent", now=1.1)
    policy.arrive("parent", 510, now=1.2)
    assert not policy.allowed("parent") and policy.allowed("borrower")
    assert not policy.counts["capacity_switch:join_loan_return"]
    invariant(policy)


@pytest.mark.parametrize("protection", ("critical", "restore"))
def test_borrower_with_unconsumed_join_protection_is_not_recalled(protection):
    policy = loan_policy()
    borrower = policy.agents["borrower"]
    if protection == "critical":
        borrower.critical = True
    else:
        assert policy.reserve_restore("borrower", now=1.01, critical=False)
    policy.unblock("parent", now=1.1, critical=True)
    policy.arrive("parent", 510, now=1.2, critical=True)
    assert borrower.active and not policy.allowed("parent")
    invariant(policy)


def test_failed_partial_recall_does_not_pause_a_borrower_without_admitting_parent():
    policy = loan_policy()
    policy.unblock("parent", now=1.1, critical=True)
    policy.arrive("parent", 900, now=1.2, critical=True)
    assert policy.allowed("borrower") and not policy.allowed("parent")
    assert not policy.counts["capacity_switch:join_loan_return"]
    invariant(policy)


def test_existing_free_budget_admits_parent_without_recalling_borrower():
    policy = loan_policy()
    policy.forget("running", now=1.05)
    policy.unblock("parent", now=1.1, critical=True)
    policy.arrive("parent", 300, now=1.2, critical=True)
    assert policy.allowed("parent") and policy.allowed("borrower")
    assert not policy.loan_borrowers and not policy.borrowed_from
    assert not policy.counts["capacity_switch:join_loan_return"]
    invariant(policy)


def test_new_admission_inherits_open_lending_period_including_unused_budget():
    policy = ThunderAgentMigration(1200, retain_blocked=True, recall_join_loans=True)
    policy.arrive("parent", 700, now=0.)
    policy.completed("parent", 700, now=.1)
    policy.block("parent", "join", now=.2)
    policy.arrive("running", 200, now=.3)
    policy.begin("running")
    policy.arrive("first", 300, now=.4)
    assert policy.lend_backed_blocked(lambda _: True, now=1.) == "first"
    policy.arrive("second", 300, now=1.05)
    assert policy.loan_borrowers == {"parent": {"first", "second"}}
    policy.unblock("parent", now=1.1, critical=True)
    policy.arrive("parent", 710, now=1.2, critical=True)
    assert policy.allowed("parent")
    assert not policy.agents["first"].active and not policy.agents["second"].active
    assert policy.counts["capacity_switch:join_loan_return"] == 2
    invariant(policy)


def test_recalled_budget_never_comes_from_a_preexisting_unrelated_ready_agent():
    policy = ThunderAgentMigration(1700, retain_blocked=True, recall_join_loans=True)
    policy.arrive("parent", 700, now=0.)
    policy.completed("parent", 700, now=.1)
    policy.block("parent", "join", now=.2)
    policy.arrive("running", 200, now=.3)
    policy.begin("running")
    policy.arrive("preexisting", 100, now=.35)
    policy.arrive("borrower", 700, now=.4)
    assert policy.lend_backed_blocked(lambda _: True, now=1.) == "borrower"
    assert policy.loan_borrowers == {"parent": {"borrower"}}
    policy.unblock("parent", now=1.1, critical=True)
    policy.arrive("parent", 710, now=1.2, critical=True)
    assert policy.allowed("parent") and policy.allowed("preexisting")
    assert not policy.agents["borrower"].active
    invariant(policy)


def test_reloan_propagates_upstream_continuity_condition_without_cycles():
    policy = loan_policy()
    policy.begin("borrower")
    policy.completed("borrower", 410, now=1.1)
    policy.block("borrower", "join", now=1.2)
    policy.arrive("descendant", 500, now=1.3)
    assert not policy.allowed("descendant")
    assert policy.lend_backed_blocked(lambda _: True, now=2.) == "descendant"
    assert policy.loan_borrowers == {
        "parent": {"descendant"}, "borrower": {"descendant"},
    }
    policy.unblock("parent", now=2.1, critical=True)
    policy.arrive("parent", 510, now=2.2, critical=True)
    assert policy.allowed("parent") and not policy.agents["descendant"].active
    assert policy.loan_borrowers == {"borrower": {"parent"}}
    invariant(policy)


@pytest.mark.parametrize("retired", ("parent", "borrower"))
def test_terminal_cleanup_removes_only_live_borrowing_relationships(retired):
    policy = loan_policy()
    policy.forget(retired, now=1.05)
    assert retired not in policy.loan_borrowers and retired not in policy.borrowed_from
    invariant(policy)
    policy.clear()
    assert not policy.loan_borrowers and not policy.borrowed_from


def test_default_lending_keeps_v233_behavior_and_does_not_recall():
    policy = loan_policy(recall=False)
    policy.unblock("parent", now=1.1, critical=True)
    policy.arrive("parent", 510, now=1.2, critical=True)
    assert not policy.allowed("parent") and policy.allowed("borrower")
    assert not policy.loan_borrowers and not policy.borrowed_from
    invariant(policy)


@pytest.mark.parametrize("value", (1, None, "yes"))
def test_invalid_recall_declaration_is_rejected(value):
    with pytest.raises(ValueError, match="JOIN loan recall"):
        ThunderAgentMigration(1000, retain_blocked=True, recall_join_loans=value)


def test_recall_requires_retained_admission():
    with pytest.raises(ValueError, match="JOIN loan recall"):
        ThunderAgentMigration(1000, recall_join_loans=True)


@pytest.mark.parametrize("mode", ("native_matched", "scheduler_only", "join_priority_only"))
def test_new_lending_condition_is_isolated_from_baseline_arms(mode):
    settings = arm_environment({
        "AGENT_ADMISSION": "thunderagent_retained",
        "AGENT_BLOCKED_LENDING": "backed_demand_recall",
    }, mode)
    assert settings["BELIEFKV_AGENT_BLOCKED_LENDING"] == "idle"


def test_runtime_attaches_recall_policy_without_changing_native_authority(monkeypatch, tmp_path):
    live = runtime(
        monkeypatch, tmp_path, capacity=1000, strategy="thunderagent_retained",
        blocked_lending="backed_demand_recall",
    )
    try:
        assert live._agent_admission.policy.recall_join_loans
        agents = live._agent_admission
        agents.policy = loan_policy()
        agents.latest["parent"] = NS(context_id="parent")
        live._agent_blocked_prefix_backed = Mock(return_value=True)
        agents.tick(now=1.)
        status = agents.status()
        assert status["join_loan_recall"] is True
        assert status["join_loan_borrowers"] == {"parent": ["borrower"]}
        assert status["native_capacity_authority_preserved"] is True
    finally:
        live.close()


def test_candidate_changes_only_lending_condition_in_v233_runtime_configuration():
    root = Path(__file__).resolve().parents[1]
    before = json.loads((root / "configs/migration/predictive_backed_agent_lending_20261026.json").read_bytes())
    after = json.loads((root / "configs/migration/predictive_join_loan_return_20261026.json").read_bytes())
    previous, candidate = before["common_environment"], dict(after["common_environment"])
    assert candidate.pop("AGENT_BLOCKED_LENDING") == "backed_demand_recall"
    expected = dict(previous)
    expected.pop("AGENT_BLOCKED_LENDING")
    assert candidate == expected
    assert after["order"] == ["full"]
    assert after["native_reference"]["repeat_full_native_replay"] is False


def test_free_readmission_keeps_open_lending_condition_until_owner_resumes():
    policy = loan_policy()
    policy._pause(policy.agents["borrower"], 1.1)
    assert policy.loan_borrowers == {"parent": set()}
    policy.rebalance(now=5.1)
    assert policy.loan_borrowers == {"parent": {"borrower"}}
    invariant(policy)


@pytest.mark.parametrize("seed", (0, 21, 88))
def test_random_blocking_lending_recall_and_retirement_preserve_live_eligibility(seed):
    rng = random.Random(seed)
    policy = ThunderAgentMigration(1500, retain_blocked=True, recall_join_loans=True)
    now = 0.
    for _ in range(2000):
        now += rng.uniform(.01, .4)
        identity = str(rng.randrange(14))
        item = policy.agents.get(identity)
        action = rng.randrange(7)
        if item is None:
            policy.arrive(identity, rng.randrange(50, 500), now=now)
        elif item.in_flight:
            policy.completed(identity, item.tokens, now=now)
        elif action == 0 and policy.allowed(identity):
            policy.begin(identity)
        elif action == 1 and not item.pending:
            policy.block(identity, "join", now=now)
        elif action == 2 and item.blocked:
            policy.unblock(identity, now=now, critical=True)
            if not item.pending:
                policy.arrive(identity, item.tokens, now=now)
        elif action == 3 and not item.pending and not item.blocked:
            policy.arrive(identity, item.tokens, now=now)
        elif action == 4:
            policy.lend_backed_blocked(lambda _: rng.random() > .2, now=now)
        elif action == 5:
            policy.forget(identity, now=now)
        policy.rebalance(now=now)
        invariant(policy)
