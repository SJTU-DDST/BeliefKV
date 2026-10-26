import json
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.policy.thunder_agent_migration import ThunderAgentMigration
from beliefkv.runtime.agent_working_set import AgentAdmission
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_join_loan_recall import (
    invariant, loan_policy,
    test_random_blocking_lending_recall_and_retirement_preserve_live_eligibility as random_lifecycle,
)


def borrowers(*, selection="cold_fit", observe=None):
    policy = ThunderAgentMigration(
        3000, retain_blocked=True, recall_join_loans=True,
        recall_selection=selection, observe_recall=observe,
    )
    policy.arrive("parent", 1500, now=0.)
    policy.completed("parent", 1500, now=.1)
    policy.block("parent", "join", now=.2)
    policy.arrive("running", 300, now=.3)
    policy.begin("running")
    policy.arrive("large", 1000, now=.4)
    assert policy.lend_backed_blocked(lambda _: True, now=1.) == "large"
    policy.arrive("fitting", 500, now=1.01)
    policy.arrive("small", 300, now=1.02)
    assert policy.loan_borrowers == {"parent": {"large", "fitting", "small"}}
    invariant(policy)
    return policy


def recall(policy, *, tokens=900):
    policy.unblock("parent", now=1.1, critical=True)
    policy.arrive("parent", tokens, now=1.2, critical=True)
    invariant(policy)


@pytest.mark.parametrize("selection,paused", (("largest", "large"), ("cold_fit", "fitting")))
def test_one_fitting_borrower_can_cover_the_gap_without_pausing_the_largest_agent(selection, paused):
    observed = []
    policy = borrowers(selection=selection, observe=observed.append)
    recall(policy)
    assert policy.allowed("parent")
    assert [name for name in ("large", "fitting", "small") if not policy.agents[name].active] == [paused]
    assert policy.agents["running"].in_flight and policy.agents["running"].active
    assert observed[0]["needed_tokens"] == 500
    assert observed[0]["selected_credit_tokens"] == (1100 if selection == "largest" else 600)
    assert observed[0]["selected_invocation_ids"] == [paused]
    assert not policy.loan_borrowers


@pytest.mark.parametrize("reason", ("join", "long_tool", "elapsed_tool"))
def test_blocked_borrower_is_selected_before_a_ready_agent_even_if_it_is_larger(reason):
    policy = borrowers()
    policy.begin("large")
    policy.completed("large", 1000, now=1.03)
    policy.block("large", reason, now=1.04)
    recall(policy)
    assert not policy.agents["large"].active
    assert policy.allowed("fitting") and policy.allowed("small")


def test_between_request_borrower_precedes_a_submitted_ready_agent():
    policy = borrowers()
    policy.begin("large")
    policy.completed("large", 1000, now=1.03)
    recall(policy)
    assert not policy.agents["large"].active
    assert policy.allowed("fitting") and policy.allowed("small")


def test_multiple_borrowers_are_selected_only_when_no_single_one_covers_the_gap():
    observed = []
    policy = borrowers(observe=observed.append)
    recall(policy, tokens=1600)
    assert policy.allowed("parent")
    assert observed[0]["needed_tokens"] == 1200
    assert observed[0]["selected_credit_tokens"] == 1500
    assert observed[0]["selected_invocation_ids"] == ["large", "small"]
    assert policy.allowed("fitting")


def test_ready_sole_join_blocker_is_kept_before_an_ordinary_borrower():
    policy = borrowers()
    policy.recall_urgent = lambda identity: identity == "fitting"
    recall(policy)
    assert policy.allowed("fitting")
    assert not policy.agents["large"].active


def test_sole_join_blocker_is_a_last_resort_rather_than_an_unbounded_reservation():
    policy = loan_policy(selection="cold_fit")
    policy.recall_urgent = lambda _: True
    policy.unblock("parent", now=1.1, critical=True)
    policy.arrive("parent", 510, now=1.2, critical=True)
    assert policy.allowed("parent")
    assert not policy.agents["borrower"].active
    invariant(policy)


def test_blocked_sole_child_can_lend_while_it_has_no_runnable_model_work():
    policy = borrowers()
    policy.recall_urgent = lambda identity: identity == "large"
    policy.begin("large")
    policy.completed("large", 1000, now=1.03)
    policy.block("large", "long_tool", now=1.04)
    recall(policy)
    assert not policy.agents["large"].active
    assert policy.allowed("fitting")


@pytest.mark.parametrize("protection", ("running", "critical", "restore"))
def test_cold_selection_keeps_all_existing_native_and_restore_exclusions(protection):
    policy = borrowers()
    target = policy.agents["fitting"]
    if protection == "running":
        policy.begin("fitting")
    elif protection == "critical":
        target.critical = True
    else:
        target.restore_until = 2.
    recall(policy)
    assert target.active
    assert not policy.agents["large"].active


def test_insufficient_total_budget_keeps_every_borrower_and_emits_no_recall():
    observed = []
    policy = borrowers(observe=observed.append)
    recall(policy, tokens=2900)
    assert not policy.allowed("parent")
    assert all(policy.agents[name].active for name in ("large", "fitting", "small"))
    assert not observed


def test_unsubmitted_eos_and_ordinary_reentry_cannot_use_cold_fit_to_recall():
    policy = loan_policy(selection="cold_fit")
    assert not policy.reserve_restore("parent", now=1.05, critical=True)
    policy.unblock("parent", now=1.1)
    policy.arrive("parent", 510, now=1.2)
    assert not policy.allowed("parent") and policy.allowed("borrower")
    invariant(policy)


@pytest.mark.parametrize("seed", (21, 88))
def test_cold_fit_retains_dynamic_blocking_lending_recall_and_retirement_invariants(seed):
    random_lifecycle(seed, recall_selection="cold_fit")


@pytest.mark.parametrize("value", (None, "best", 1))
def test_invalid_selection_is_rejected(value):
    with pytest.raises(ValueError, match="recall selection"):
        ThunderAgentMigration(1000, retain_blocked=True, recall_join_loans=True, recall_selection=value)


def test_cold_fit_requires_recorded_lending():
    with pytest.raises(ValueError, match="recall selection"):
        ThunderAgentMigration(1000, retain_blocked=True, recall_selection="cold_fit")


@pytest.mark.parametrize("mode", ("native_matched", "join_priority_only", "scheduler_only", "kv_only"))
def test_paper_and_policy_controls_do_not_inherit_full_recall_selection(mode):
    settings = arm_environment({
        "AGENT_ADMISSION": "thunderagent_retained",
        "AGENT_BLOCKED_LENDING": "backed_demand_recall", "AGENT_RECALL_SELECTION": "cold_fit",
    }, mode)
    assert settings["AGENT_RECALL_SELECTION"] == settings["BELIEFKV_AGENT_RECALL_SELECTION"] == "largest"


def test_actual_runtime_constructor_and_agent_policy_receive_the_selection(monkeypatch, tmp_path):
    settings = arm_environment({
        "AGENT_ADMISSION": "thunderagent_retained", "AGENT_BLOCKED_LENDING": "backed_demand_recall",
        "AGENT_RECALL_SELECTION": "cold_fit", "EXECUTION_FRONTIER_RESTORE": "0",
    }, "full")
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    live = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "events.sock"))
    try:
        live.attach_native_cache(NS(
            tree_components=(0,), token_to_kv_pool_allocator=NS(size_full=3000),
        ))
        assert live.agent_recall_selection == "cold_fit"
        assert live._agent_admission.policy.recall_selection == "cold_fit"
        assert live._agent_admission.status()["recall_selection"] == "cold_fit"
        assert not live.enable_frontier_restore
        key = NS(invocation_id="only")
        live._agent_admission.latest["only"] = key
        live._last_join_child_has_waiting_parent = Mock(return_value=True)
        assert live._agent_admission.policy.recall_urgent("only") is True
        live._last_join_child_has_waiting_parent.assert_called_once_with(key)
    finally:
        live.close()


def test_actual_recall_decision_is_logged_once_after_eligibility_return():
    writer = Mock()
    runtime = NS(_opportunity_writer=writer)
    admission = AgentAdmission(
        runtime, strategy="thunderagent_retained",
        blocked_lending="backed_demand_recall", recall_selection="cold_fit",
    )
    admission.attach(NS(
        tree_components=(0,), token_to_kv_pool_allocator=NS(size_full=3000),
    ))
    policy = borrowers(observe=admission.policy.observe_recall)
    recall(policy)
    writer.record.assert_called_once()
    row = writer.record.call_args.args[0]
    assert row["event"] == "agent_loan_recall_decision"
    assert row["selection"] == "cold_fit"
    assert row["selected_credit_tokens"] >= row["needed_tokens"]
    assert all(not candidate["in_flight"] and not candidate["critical"] for candidate in row["candidates"])
    assert row["native_capacity_authority_preserved"] is True
    assert "physical" in row["semantics"]
    assert json.loads(json.dumps(row))["selected_invocation_ids"] == ["fitting"]


def test_declared_candidate_changes_only_recall_selection_in_the_current_fence_configuration():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    before = json.loads((root / "configs/migration/predictive_free_page_fence_20261026.json").read_bytes())
    after = json.loads((root / "configs/migration/predictive_cold_fit_recall_20261026.json").read_bytes())
    settings = dict(after["common_environment"])
    assert settings.pop("AGENT_RECALL_SELECTION") == "cold_fit"
    assert settings == before["common_environment"]
    assert after["gpu_execution"]["registered"] is False
    assert after["order"] == ["full", "thunderagent"]
