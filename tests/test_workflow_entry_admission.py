from unittest.mock import Mock

import pytest

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.policy.thunder_agent_migration import ThunderAgentMigration
from tests.test_agent_working_set_runtime import runtime
from tests.test_paper_baseline_runtime import request


def policy(*, capacity=10000, threshold=2):
    return ThunderAgentMigration(
        capacity, retain_blocked=True, recall_join_loans=True,
        workflow_entry_min_ready=threshold,
    )


def arrive(live, identity, workflow, *, now, tokens=200):
    live.arrive(identity, tokens, now=now, workflow_id=workflow)


def test_entry_gate_defers_only_unstarted_workflows_without_interrupting_running_work():
    live = policy()
    for index in range(2):
        arrive(live, f"root-{index}", f"workflow-{index}", now=index / 10.)
        live.begin(f"root-{index}")
    arrive(live, "new-root", "new-workflow", now=.3)
    assert not live.allowed("new-root")
    assert all(live.agents[f"root-{index}"].in_flight for index in range(2))
    assert live.counts["workflow_entry_deferred_on_arrival"] == 1
    assert live.counts["paused"] == 0
    assert live.used_tokens == 600


def test_existing_workflow_child_and_join_successor_bypass_the_new_entry_gate():
    live = policy()
    arrive(live, "parent", "started", now=0.)
    live.begin("parent")
    live.completed("parent", 210, now=.1)
    live.block("parent", "join", now=.2)
    arrive(live, "peer-a", "peer-a-workflow", now=.3)
    arrive(live, "peer-b", "peer-b-workflow", now=.4)
    arrive(live, "child", "started", now=.5)
    assert live.allowed("child")
    live._pause(live.agents["parent"], .6)
    live.unblock("parent", now=.7, critical=True)
    live.arrive("parent", 220, now=.8, workflow_id="started", critical=True)
    assert live.allowed("parent")
    assert live.reserve_restore("parent", now=.9, critical=True)


def test_child_can_follow_a_workflow_with_provisional_eligibility():
    live = policy(threshold=1)
    arrive(live, "root", "same", now=0.)
    arrive(live, "child", "same", now=.1)
    assert "same" not in live.started_workflows
    assert live.allowed("root") and live.allowed("child")


def test_periodic_resume_admits_only_one_new_workflow_after_ready_width_drops():
    live = policy(threshold=1)
    arrive(live, "active", "first", now=0.)
    live.begin("active")
    for index in range(3):
        arrive(live, f"queued-{index}", f"new-{index}", now=.1 + index / 10.)
    live.completed("active", 210, now=1.)
    live.rebalance(now=5.1)
    admitted = [name for name in live.agents if name.startswith("queued-") and live.allowed(name)]
    assert len(admitted) == 1
    assert live.agents["active"].active
    live.rebalance(now=10.2)
    assert [name for name in live.agents if name.startswith("queued-") and live.allowed(name)] == admitted


def test_gated_target_cannot_consume_a_backed_donor_or_start_native_proof():
    live = policy(capacity=1000, threshold=1)
    arrive(live, "parent", "existing", now=0., tokens=500)
    live.begin("parent")
    live.completed("parent", 500, now=.1)
    live.block("parent", "join", now=.2)
    arrive(live, "running", "existing", now=.3, tokens=200)
    live.begin("running")
    arrive(live, "new", "new-workflow", now=.4, tokens=400)
    proof = Mock(return_value=True)
    assert live.lend_backed_blocked(proof, now=1.) is None
    proof.assert_not_called()
    assert live.agents["parent"].active
    assert live.agents["running"].in_flight
    assert not live.allowed("new")
    assert not live.loan_borrowers and not live.borrowed_from
    assert live.counts["paused"] == 0


def test_started_workflow_can_borrow_backed_budget_while_new_entry_is_gated():
    live = policy(capacity=1000, threshold=1)
    arrive(live, "parent", "existing", now=0., tokens=500)
    live.begin("parent")
    live.completed("parent", 500, now=.1)
    live.block("parent", "join", now=.2)
    arrive(live, "running", "existing", now=.3, tokens=200)
    live.begin("running")
    arrive(live, "new", "new-workflow", now=.4, tokens=400)
    arrive(live, "child", "existing", now=.5, tokens=400)
    proof = Mock(return_value=True)
    assert live.lend_backed_blocked(proof, now=1.) == "child"
    proof.assert_called_once_with("parent")
    assert live.allowed("child") and not live.allowed("new")
    assert live.borrowed_from["child"] == {"parent"}


def test_capacity_is_not_bypassed_and_unsafe_partial_recall_still_cannot_pause_running_work():
    live = policy(capacity=500, threshold=1)
    arrive(live, "running", "started", now=0., tokens=300)
    live.begin("running")
    arrive(live, "child", "started", now=.1, tokens=300)
    live.rebalance(now=5.1)
    assert not live.allowed("child")
    assert live.agents["running"].in_flight
    assert live.used_tokens == 400


def test_original_starvation_escape_is_available_without_continuous_rotation():
    live = policy(threshold=1)
    arrive(live, "running", "old", now=0.)
    live.begin("running")
    arrive(live, "waiting", "new", now=.1)
    live.rebalance(now=1799.)
    assert not live.allowed("waiting")
    live.rebalance(now=1804.1)
    assert live.allowed("waiting") and live.agents["running"].in_flight


def test_workflow_identity_is_stable_and_state_is_released_at_last_agent_retirement():
    live = policy()
    arrive(live, "root", "workflow", now=0.)
    live.begin("root")
    live.completed("root", 210, now=.1)
    with pytest.raises(ValueError, match="change workflow"):
        arrive(live, "root", "different", now=.2)
    arrive(live, "child", "workflow", now=.3)
    live.forget("root", now=.4)
    assert live.started_workflows == {"workflow"}
    live.forget("child", now=.5)
    assert not live.started_workflows
    arrive(live, "new-root", "new", now=.6)
    live.begin("new-root")
    live.clear()
    assert not live.started_workflows and not live.agents
    assert not live.loan_borrowers and not live.borrowed_from


@pytest.mark.parametrize("value", (-1, 257, True, 1.5, "8"))
def test_constructor_rejects_invalid_entry_counts(value):
    with pytest.raises(ValueError, match="workflow entry"):
        policy(threshold=value)


@pytest.mark.parametrize("value", ("-1", "257", "1.5", " 8", "eight", "\u0668"))
def test_environment_rejects_ambiguous_or_out_of_range_entry_counts(value):
    with pytest.raises(ValueError, match="workflow entry"):
        arm_environment({
            "AGENT_ADMISSION": "thunderagent_retained",
            "AGENT_WORKFLOW_ENTRY_MIN_READY": value,
        }, "full")


@pytest.mark.parametrize("mode", ("native_matched", "scheduler_only", "kv_only", "join_priority_only"))
def test_entry_gate_is_isolated_from_native_and_other_comparison_arms(mode):
    settings = arm_environment({
        "AGENT_ADMISSION": "thunderagent_retained",
        "AGENT_WORKFLOW_ENTRY_MIN_READY": "8",
    }, mode)
    assert settings["BELIEFKV_AGENT_WORKFLOW_ENTRY_MIN_READY"] == "0"
    assert settings["AGENT_WORKFLOW_ENTRY_MIN_READY"] == "0"


def test_full_environment_preserves_explicit_gate_and_rejects_other_agent_strategies():
    settings = {
        "AGENT_ADMISSION": "thunderagent_retained",
        "AGENT_WORKFLOW_ENTRY_MIN_READY": "8",
    }
    assert arm_environment(settings, "full")["BELIEFKV_AGENT_WORKFLOW_ENTRY_MIN_READY"] == "8"
    with pytest.raises(ValueError, match="workflow entry"):
        arm_environment({**settings, "AGENT_ADMISSION": "thunderagent"}, "full")


def test_real_native_runtime_passes_workflow_identity_and_filters_new_entry(monkeypatch, tmp_path):
    live = runtime(
        monkeypatch, tmp_path, capacity=2000, strategy="thunderagent_retained",
        blocked_lending="backed_demand_recall", workflow_entry_min_ready=1,
    )
    try:
        root = request("root-one", "root", tokens=200, workflow="first-workflow")
        newcomer = request("new-one", "new", tokens=200, workflow="second-workflow")
        child = request("child-one", "child", tokens=200, workflow="first-workflow")
        live.register_visible_request(root)
        live.on_prefill_candidate_result(root, admitted=True, result="CONTINUE")
        live.register_visible_request(newcomer)
        live.register_visible_request(child)
        agents = live._agent_admission
        agents.policy.rebalance(now=agents.policy.next_control + .01)
        assert agents.allows(live.visible[root.rid])
        assert agents.allows(live.visible[child.rid])
        assert not agents.allows(live.visible[newcomer.rid])
        assert live.agent_workflow_entry_min_ready == 1
        assert agents.policy.started_workflows == {"first-workflow"}
        status = agents.status()
        assert status["workflow_entry_min_ready_agents"] == 1
        assert status["workflow_entry_scope"] == "unstarted_root_workflow_only"
        assert status["workflow_entry_started_workflows"] == 1
        plan = live.plan_native_prefill([newcomer, child], running_batch=None, adder=None)
        assert plan is not None
    finally:
        live.close()
