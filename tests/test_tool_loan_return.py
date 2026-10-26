import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.control.causal_graph import InvocationState
from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.policy.thunder_agent_migration import ThunderAgentMigration
from tests.test_agent_working_set_runtime import runtime
from tests.test_internal_comparison import observed
from tests.test_join_loan_recall import invariant
from tests.test_paper_baseline_runtime import request


def tool_policy(*, enabled=True, reason="long_tool"):
    live = ThunderAgentMigration(
        1000, retain_blocked=True, recall_join_loans=True, recall_tool_loans=enabled,
    )
    live.arrive("owner", 500, now=0.)
    live.begin("owner")
    live.completed("owner", 500, now=.1)
    live.block("owner", reason, now=.2)
    live.arrive("running", 200, now=.3)
    live.begin("running")
    live.arrive("borrower", 400, now=.4)
    assert live.lend_backed_blocked(lambda _: True, now=1.) == "borrower"
    return live


def returned(live):
    live.note_tool_return("owner")
    live.unblock("owner", now=1.1)


@pytest.mark.parametrize("reason", ("long_tool", "elapsed_tool"))
def test_real_tool_successor_recalls_its_loan_at_the_borrower_request_boundary(reason):
    live = tool_policy(reason=reason)
    live.begin("borrower")
    returned(live)
    assert not live.allowed("owner")
    live.arrive("owner", 510, now=1.2)
    assert not live.allowed("owner")
    assert live.agents["borrower"].in_flight
    live.completed("borrower", 410, now=1.3)
    assert live.allowed("owner") and not live.agents["borrower"].active
    assert live.agents["running"].in_flight
    assert live.counts["tool_loan_recall_admitted"] == 1
    assert live.counts["capacity_switch:tool_loan_return"] == 1
    assert not live.loan_borrowers and not live.borrowed_from
    invariant(live)
    live.begin("owner")
    assert not live.agents["owner"].tool_return_ready


def test_tool_completion_without_a_submitted_successor_does_not_pause_anyone():
    live = tool_policy()
    returned(live)
    live.rebalance(now=5.1)
    assert live.allowed("borrower")
    assert not live.agents["owner"].active
    assert not live.counts["capacity_switch:tool_loan_return"]
    invariant(live)


def test_prediction_alone_cannot_recall_ready_capacity_for_tool_prefetch():
    live = tool_policy()
    assert not live.reserve_restore("owner", now=1.1, critical=False)
    assert live.allowed("borrower")
    assert not live.agents["owner"].tool_return_ready
    assert not live.counts["capacity_switch:tool_loan_return"]
    invariant(live)


@pytest.mark.parametrize("condition", ("disabled", "no_tool_return", "join_block"))
def test_default_or_unobserved_tool_return_preserves_the_existing_agent_policy(condition):
    live = tool_policy(
        enabled=condition != "disabled", reason="join" if condition == "join_block" else "long_tool",
    )
    if condition != "no_tool_return":
        live.note_tool_return("owner")
    live.unblock("owner", now=1.1)
    live.arrive("owner", 510, now=1.2)
    assert not live.allowed("owner") and live.allowed("borrower")
    assert not live.counts["capacity_switch:tool_loan_return"]
    invariant(live)


@pytest.mark.parametrize("protection", ("running", "join", "restore", "insufficient"))
def test_tool_return_never_partially_pauses_or_revokes_protected_borrowers(protection):
    live = tool_policy()
    if protection == "running":
        live.begin("borrower")
    elif protection == "join":
        live.agents["borrower"].critical = True
    elif protection == "restore":
        assert live.reserve_restore("borrower", now=1.05, critical=False)
    returned(live)
    live.arrive("owner", 900 if protection == "insufficient" else 510, now=1.2)
    assert not live.allowed("owner") and live.allowed("borrower")
    assert live.counts["paused"] == 1
    assert not live.counts["capacity_switch:tool_loan_return"]
    invariant(live)


def test_existing_free_capacity_ends_the_loan_without_pausing_a_ready_borrower():
    live = tool_policy()
    live.forget("running", now=1.05)
    returned(live)
    live.arrive("owner", 300, now=1.2)
    assert live.allowed("owner") and live.allowed("borrower")
    assert not live.counts["capacity_switch:tool_loan_return"]
    assert not live.loan_borrowers and not live.borrowed_from
    invariant(live)


def test_join_recall_precedes_tool_return_when_both_wait_for_the_same_boundary():
    live = ThunderAgentMigration(
        1600, retain_blocked=True, recall_join_loans=True, recall_tool_loans=True,
    )
    for index, name in enumerate(("join-owner", "tool-owner")):
        live.arrive(name, 500, now=index / 10.)
        live.begin(name)
        live.completed(name, 500, now=index / 10. + .01)
        live.block(name, "join" if index == 0 else "long_tool", now=index / 10. + .02)
    live.arrive("running", 200, now=.3)
    live.begin("running")
    live.arrive("borrower", 1000, now=.4)
    assert live.lend_backed_blocked(lambda _: True, now=1.) == "borrower"
    assert set(live.loan_borrowers) == {"join-owner", "tool-owner"}
    live.begin("borrower")
    live.note_tool_return("tool-owner")
    live.unblock("tool-owner", now=1.1)
    live.arrive("tool-owner", 900, now=1.2)
    live.unblock("join-owner", now=1.3, critical=True)
    live.arrive("join-owner", 900, now=1.4, critical=True)
    assert not live.allowed("tool-owner") and not live.allowed("join-owner")
    live.completed("borrower", 1000, now=1.5)
    assert live.allowed("join-owner") and not live.allowed("tool-owner")
    assert live.counts["join_loan_recall_admitted"] == 1
    assert not live.counts["tool_loan_recall_admitted"]
    invariant(live)


def test_tool_return_cannot_take_budget_from_an_unrelated_preexisting_agent():
    live = ThunderAgentMigration(
        1700, retain_blocked=True, recall_join_loans=True, recall_tool_loans=True,
    )
    live.arrive("owner", 700, now=0.)
    live.completed("owner", 700, now=.1)
    live.block("owner", "long_tool", now=.2)
    live.arrive("running", 200, now=.3)
    live.begin("running")
    live.arrive("preexisting", 100, now=.35)
    live.arrive("borrower", 700, now=.4)
    assert live.lend_backed_blocked(lambda _: True, now=1.) == "borrower"
    returned(live)
    live.arrive("owner", 710, now=1.2)
    assert live.allowed("owner") and live.allowed("preexisting")
    assert not live.agents["borrower"].active
    invariant(live)


def test_a_new_blocking_dependency_invalidates_an_unconsumed_tool_return():
    live = tool_policy()
    returned(live)
    live.block("owner", "join", now=1.15)
    live.unblock("owner", now=1.16)
    live.arrive("owner", 510, now=1.2)
    assert not live.agents["owner"].tool_return_ready
    assert not live.allowed("owner") and live.allowed("borrower")


@pytest.mark.parametrize("value", (1, None, "1"))
def test_tool_return_declaration_is_explicitly_boolean(value):
    with pytest.raises(ValueError, match="tool loan return"):
        ThunderAgentMigration(
            1000, retain_blocked=True, recall_join_loans=True, recall_tool_loans=value,
        )


def test_tool_return_requires_recorded_lending():
    with pytest.raises(ValueError, match="tool loan return"):
        ThunderAgentMigration(1000, retain_blocked=True, recall_tool_loans=True)
    with pytest.raises(ValueError, match="tool loan return"):
        arm_environment({
            "AGENT_ADMISSION": "thunderagent_retained", "AGENT_TOOL_LOAN_RETURN": "1",
        }, "full")


@pytest.mark.parametrize("mode", ("native_matched", "scheduler_only", "kv_only", "join_priority_only"))
def test_other_arms_never_inherit_tool_budget_return(mode):
    settings = arm_environment({
        "AGENT_ADMISSION": "thunderagent_retained",
        "AGENT_BLOCKED_LENDING": "backed_demand_recall",
        "AGENT_TOOL_LOAN_RETURN": "1",
    }, mode)
    assert settings["AGENT_TOOL_LOAN_RETURN"] == "0"
    assert settings["BELIEFKV_AGENT_TOOL_LOAN_RETURN"] == "0"


def test_real_runtime_waits_for_all_tools_and_the_submitted_successor(monkeypatch, tmp_path):
    live = runtime(
        monkeypatch, tmp_path, capacity=1000, strategy="thunderagent_retained",
        blocked_lending="backed_demand_recall", tool_loan_return=True,
    )
    try:
        live.on_events((
            observed(0, Kind.WORKFLOW_START),
            observed(1, Kind.INVOCATION_CREATE, invocation_id="owner", context_id="owner-context"),
        ))
        owner = request("owner-one", "owner", tokens=498, workflow="wf")
        live.register_visible_request(owner)
        live.on_prefill_candidate_result(owner, admitted=True, result="CONTINUE")
        live.on_batch_completed(NS(reqs=[owner]))
        live.on_events((
            observed(2, Kind.TOOL_START, invocation_id="owner", attributes={"tool_run_id": "long"}),
            observed(3, Kind.TOOL_START, invocation_id="owner", attributes={"tool_run_id": "other"}),
        ))
        running = request("running-one", "running", tokens=200, workflow="wf")
        live.register_visible_request(running)
        live.on_prefill_candidate_result(running, admitted=True, result="CONTINUE")
        borrower = request("borrower-one", "borrower", tokens=400, workflow="wf")
        live.register_visible_request(borrower)
        agents = live._agent_admission
        live._long_tool_wait = Mock(return_value=True)
        live._agent_blocked_prefix_backed = Mock(return_value=True)
        agents.tick(now=time.monotonic() + .1)
        assert agents.policy.allowed("borrower")
        live.on_prefill_candidate_result(borrower, admitted=True, result="CONTINUE")
        live.on_events((observed(
            4, Kind.TOOL_END, invocation_id="owner", attributes={"tool_run_id": "other"},
        ),))
        assert not agents.policy.agents["owner"].tool_return_ready
        assert agents.policy.agents["owner"].blocked == "long_tool"
        live.on_events((observed(
            5, Kind.TOOL_END, invocation_id="owner", attributes={"tool_run_id": "long"},
        ),))
        assert agents.policy.agents["owner"].tool_return_ready
        assert not agents.policy.agents["owner"].active
        successor = request("owner-two", "owner", tokens=510, workflow="wf")
        live.register_visible_request(successor)
        assert not agents.allows(live.visible[successor.rid])
        assert agents.policy.agents["borrower"].in_flight
        live.on_batch_completed(NS(reqs=[borrower]))
        assert agents.allows(live.visible[successor.rid])
        assert not agents.policy.agents["borrower"].active
        assert agents.status()["tool_loan_return"] is True
        assert live.agent_tool_loan_return is True
        assert agents.status()["ordinary_restore_reservation_ms"] == 0
        assert not agents.claim_restore(agents.latest["owner"], source="tool_wait")
        invariant(agents.policy)
    finally:
        live.close()


@pytest.mark.parametrize("tool_loan_return", (False, True))
def test_tool_completion_cannot_clear_an_unfinished_join_dependency(
    monkeypatch, tmp_path, tool_loan_return,
):
    live = runtime(
        monkeypatch, tmp_path, capacity=2000, strategy="thunderagent_retained",
        blocked_lending="backed_demand_recall", tool_loan_return=tool_loan_return,
    )
    try:
        live.on_events((
            observed(0, Kind.WORKFLOW_START),
            observed(1, Kind.INVOCATION_CREATE, invocation_id="parent", context_id="parent-context"),
            observed(2, Kind.INVOCATION_CREATE, invocation_id="child", context_id="child-context",
                     parent_invocation_id="parent"),
        ))
        first = request("parent-one", "parent", tokens=200, workflow="wf")
        live.register_visible_request(first)
        live.on_batch_completed(NS(reqs=[first]))
        live.on_events((
            observed(3, Kind.TOOL_START, invocation_id="parent", attributes={"tool_run_id": "parallel"}),
            observed(4, Kind.JOIN_CREATE, join_id="join", member_invocation_ids=("child",)),
            observed(5, Kind.JOIN_WAIT, invocation_id="parent", join_id="join"),
        ))
        agents = live._agent_admission
        assert agents.policy.agents["parent"].blocked == "join"
        live.on_events((observed(
            6, Kind.TOOL_END, invocation_id="parent", attributes={"tool_run_id": "parallel"},
        ),))
        assert live.graph.invocations["parent"].state is InvocationState.WAIT_JOIN
        assert agents.policy.agents["parent"].blocked == "join"
        assert not agents.policy.agents["parent"].tool_return_ready
    finally:
        live.close()
