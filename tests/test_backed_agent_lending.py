from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.policy.thunder_agent_migration import ThunderAgentMigration
from beliefkv.runtime.sglang_v0520_physical import ActionLocalShadowCandidate, ContextSessionAnchors
from beliefkv.runtime.sglang_v0520_observer import UnifiedNodeSummary
from tests.test_agent_working_set_runtime import runtime
from tests.test_full_only_native_lifecycle import anchors, full_native


def congested_policy():
    policy = ThunderAgentMigration(1000, retain_blocked=True)
    policy.arrive("parent", 500, now=0.)
    policy.completed("parent", 500, now=.1)
    policy.block("parent", "join", now=.2)
    policy.arrive("running", 200, now=.3)
    policy.begin("running")
    policy.arrive("waiting", 400, now=.4)
    assert not policy.allowed("waiting")
    return policy


def test_backed_blocked_parent_can_lend_while_a_peer_keeps_computing():
    policy = congested_policy()
    proof = Mock(return_value=True)
    assert policy.lend_backed_blocked(proof, now=1.) == "waiting"
    proof.assert_called_once_with("parent")
    assert policy.agents["running"].active and policy.agents["running"].in_flight
    assert not policy.agents["parent"].active and policy.allowed("waiting")
    assert policy.used_tokens <= policy.capacity
    assert policy.counts["backed_wait_admitted"] == 1


def test_missing_backing_proof_keeps_blocked_eligibility():
    policy = congested_policy()
    assert policy.lend_backed_blocked(lambda _: False, now=1.) is None
    assert policy.agents["parent"].active and not policy.allowed("waiting")


@pytest.mark.parametrize("reason", ("pending", "in_flight", "critical", "restore"))
def test_ready_running_and_restore_protected_owners_cannot_lend(reason):
    policy = congested_policy()
    owner = policy.agents["parent"]
    if reason == "pending":
        owner.blocked, owner.pending = None, True
    elif reason == "in_flight":
        owner.in_flight = True
    elif reason == "critical":
        owner.critical = True
    else:
        owner.restore_until = 2.
    proof = Mock(return_value=True)
    assert policy.lend_backed_blocked(proof, now=1.) is None
    proof.assert_not_called()
    assert owner.active and not policy.allowed("waiting")


def test_insufficient_backed_budget_never_pauses_a_partial_victim_set():
    policy = congested_policy()
    policy.agents["waiting"].tokens = 2000
    assert policy.lend_backed_blocked(lambda _: True, now=1.) is None
    assert policy.agents["parent"].active
    assert policy.counts["paused"] == 0


def test_backing_queries_are_bounded_and_reused_across_selected_candidates():
    policy = ThunderAgentMigration(5000, retain_blocked=True)
    for index in range(9):
        name = f"blocked-{index}"
        policy.arrive(name, 200, now=index / 100.)
        policy.completed(name, 200, now=index / 100. + .001)
        policy.block(name, "join", now=index / 100. + .002)
    policy.arrive("running", 2000, now=.2)
    policy.begin("running")
    for index in range(9):
        policy.arrive(f"waiting-{index}", 4000, now=.3 + index / 100.)
    proof = Mock(return_value=False)
    assert policy.lend_backed_blocked(proof, now=1.) is None
    assert proof.call_count == 8
    assert len({call.args[0] for call in proof.call_args_list}) == 8
    assert all(policy.agents[f"blocked-{index}"].active for index in range(9))


def test_one_loan_preserves_normal_demand_recovery_and_future_join_reservation():
    policy = congested_policy()
    policy.lend_backed_blocked(lambda _: True, now=1.)
    policy.unblock("parent", now=1.1, critical=True)
    assert not policy.reserve_restore("parent", now=1.2, critical=True)
    assert policy.allowed("waiting") and policy.agents["running"].in_flight
    policy.begin("waiting")
    policy.completed("waiting", 400, now=1.3)
    policy.forget("waiting", now=1.4)
    assert policy.reserve_restore("parent", now=1.5, critical=True)
    policy.arrive("parent", 510, now=1.6)
    policy.begin("parent")
    assert policy.restore_owner is None and policy.agents["parent"].in_flight


def test_new_lending_is_full_only_and_declaration_is_checked():
    common = {"AGENT_ADMISSION": "thunderagent_retained", "AGENT_BLOCKED_LENDING": "backed_demand"}
    assert arm_environment(common, "full")["BELIEFKV_AGENT_BLOCKED_LENDING"] == "backed_demand"
    assert arm_environment(common, "scheduler_only")["BELIEFKV_AGENT_BLOCKED_LENDING"] == "idle"
    assert arm_environment(common, "native_matched")["BELIEFKV_AGENT_BLOCKED_LENDING"] == "idle"
    with pytest.raises(ValueError, match="retained"):
        arm_environment({**common, "AGENT_ADMISSION": "working_set"}, "full")


def test_runtime_maintenance_checks_proof_once_and_respects_engine_ceiling(monkeypatch, tmp_path):
    live = runtime(monkeypatch, tmp_path, capacity=1000, strategy="thunderagent_retained")
    try:
        agents = live._agent_admission
        agents.blocked_lending = "backed_demand"
        agents.policy = congested_policy()
        key = NS(context_id="parent")
        agents.latest["parent"] = key
        live._agent_blocked_prefix_backed = Mock(return_value=True)
        live._native_max_running = 1
        live._native_running_batch = NS(reqs=[object()])
        agents.tick(now=1.)
        live._agent_blocked_prefix_backed.assert_not_called()
        live._native_running_batch = NS(reqs=[])
        agents.tick(now=2.)
        live._agent_blocked_prefix_backed.assert_called_once_with(key)
        assert agents.policy.allowed("waiting")
        agents.tick(now=2.1)
        live._agent_blocked_prefix_backed.assert_called_once_with(key)
    finally:
        live.close()


def summary(node_id, parent, tokens):
    return UnifiedNodeSummary(
        node_id, parent, 1., tokens, tokens, False, False,
        0, 0, 0, 0, 1, 0, 1, 0, None, None, tokens,
    )


@pytest.mark.parametrize("change", (
    None, "unbacked", "ongoing", "pending", "missing_key_length", "insufficient_path", "branched_path",
))
def test_backing_proof_requires_complete_settled_native_path(monkeypatch, tmp_path, change):
    live = runtime(monkeypatch, tmp_path, strategy="thunderagent_retained")
    try:
        key = NS(context_id="parent", context_epoch=0)
        anchors = ContextSessionAnchors(key, ((0, ((2, 1.),)),), 0., 100)
        nodes = [summary(1, None, 50), summary(2, 1, 50)]
        if change == "unbacked":
            nodes[1] = replace(nodes[1], full_host_tokens=0)
        elif change == "pending":
            nodes[1] = replace(nodes[1], pending_write_id=7)
        elif change == "missing_key_length":
            nodes[1] = replace(nodes[1], key_tokens=None)
        elif change == "insufficient_path":
            anchors = replace(anchors, reusable_input_tokens=101)
        elif change == "branched_path":
            nodes = [summary(1, None, 20), summary(2, 1, 40), summary(3, 1, 40)]
            anchors = replace(anchors, component_leaves=((0, ((2, 1.), (3, 1.))),))
        candidate = ActionLocalShadowCandidate(anchors, tuple(nodes), 0, 0)
        live._live_parent_pressure_key = Mock(return_value=True)
        live.snapshot_session_anchors = Mock(return_value=anchors)
        live._native_cache.ongoing_write_through = {2: object()} if change == "ongoing" else {}
        capture = Mock(return_value=candidate)
        monkeypatch.setattr("beliefkv.runtime.sglang_v0520_runtime.capture_action_local_shadow", capture)
        assert live._agent_blocked_prefix_backed(key) is (change is None)
        capture.assert_called_once_with(
            live._native_cache, anchors, max_nodes=64, include_non_actionable=True,
        )
        live._live_parent_pressure_key.return_value = False
        capture.reset_mock()
        assert not live._agent_blocked_prefix_backed(key)
        capture.assert_not_called()
    finally:
        live.close()


def test_backing_lending_observes_actual_native_incremental_backup_and_demotion(
    monkeypatch, tmp_path, full_native,
):
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, _, _, _ = full_native
    live = runtime(monkeypatch, tmp_path, strategy="thunderagent_retained")
    live._native_cache = cache
    try:
        _, generation = insert_session(cache, list(range(8)), "parent")
        snapshot = anchors(cache, generation, input_tokens=8)
        live._live_parent_pressure_key = Mock(return_value=True)
        live.snapshot_session_anchors = lambda *_a, **_k: snapshot
        assert not live._agent_blocked_prefix_backed(snapshot.key)
        cache.prepare_beliefkv_spawn_full(session_id="parent", session_generation=generation)
        assert not live._agent_blocked_prefix_backed(snapshot.key)
        cache.writing_check(finish_count=1)
        assert live._agent_blocked_prefix_backed(snapshot.key)
        cache.beliefkv_cold_full_owner_validator = lambda owners: owners == (("parent", generation),)
        free = allocator.available_size()
        assert cache.reclaim_beliefkv_handoff_capacity(
            full_tokens=free + 8, mamba_slots=0,
        ) == {0: 8}
        assert live._agent_blocked_prefix_backed(snapshot.key)
        insert_session(cache, list(range(12)), "parent")
        snapshot = anchors(cache, generation, input_tokens=12)
        assert not live._agent_blocked_prefix_backed(snapshot.key)
    finally:
        live.close()
