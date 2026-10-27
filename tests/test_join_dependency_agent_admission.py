from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.control.causal_graph import InvocationState
from beliefkv.core.events import EventConfidence, RelationType, RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.paper_baselines import environment as paper_environment
from beliefkv.policy.thunder_agent_migration import ThunderAgentMigration
from tests.test_agent_working_set_runtime import runtime
from tests.test_full_only_native_lifecycle import anchors, full_native
from tests.test_internal_comparison import observed


def waiting_join(live, *, mode="all", confidence=EventConfidence.OBSERVED_EXACT):
    live.on_events((
        observed(0, Kind.WORKFLOW_START),
        observed(1, Kind.INVOCATION_CREATE, invocation_id="parent", context_id="parent-context"),
        observed(2, Kind.INVOCATION_CREATE, invocation_id="a", context_id="a-context",
                 parent_invocation_id="parent", relation_type=RelationType.SPAWN),
        observed(3, Kind.INVOCATION_CREATE, invocation_id="b", context_id="b-context",
                 parent_invocation_id="parent", relation_type=RelationType.SPAWN),
        observed(4, Kind.JOIN_CREATE, join_id="join", member_invocation_ids=("a", "b"),
                 attributes={"mode": mode}, confidence=confidence),
        observed(5, Kind.JOIN_WAIT, invocation_id="parent", join_id="join",
                 confidence=confidence),
    ))


def congested(dependency=None, observer=None):
    policy = ThunderAgentMigration(
        1200, retain_blocked=True, recall_join_loans=True,
        admission_dependency=dependency, observe_lending=observer,
    )
    policy.arrive("parent", 400, now=0.)
    policy.completed("parent", 402, now=.1)
    policy.block("parent", "join", now=.2)
    policy.arrive("guardian", 200, now=.3)
    policy.begin("guardian")
    policy.arrive("ordinary", 400, now=.4)
    # Match V261's already-served ordinary continuation.
    policy.agents["ordinary"].steps = 2
    policy.arrive("a", 400, now=.5)
    return policy


@pytest.mark.parametrize("path", ("backed", "idle", "periodic"))
@pytest.mark.parametrize("enabled", (False, True))
def test_v261_join_blocker_precedes_ordinary_continuation_at_every_admission_path(
    monkeypatch, tmp_path, path, enabled,
):
    live = runtime(
        monkeypatch, tmp_path, strategy="thunderagent_retained",
        join_dependency_admission=enabled,
    )
    try:
        waiting_join(live)
        live.on_events((observed(6, Kind.RETURN, invocation_id="b"),))
        dependency = live._agent_admission._admission_dependency if enabled else None
        policy = congested(dependency)
        assert live._agent_admission._admission_dependency("a") == 0
        if path == "backed":
            policy.lend_backed_blocked(lambda _: True, now=1.)
        elif path == "idle":
            policy.agents["guardian"].in_flight = False
            policy.agents["guardian"].pending = False
            policy.agents["guardian"].blocked = "long_tool"
            policy._resume_if_idle(1.)
        else:
            policy._pause(policy.agents["parent"], 1.)
            policy._resume()
        assert policy.allowed("a") is enabled
        assert policy.allowed("ordinary") is not enabled
        assert policy.used_tokens <= policy.capacity
        if path != "idle":
            assert policy.agents["guardian"].in_flight
        assert policy.agents["parent"].blocked == "join"
    finally:
        live.close()


@pytest.mark.parametrize("mode", ("all", "any"))
@pytest.mark.parametrize("confidence", (EventConfidence.OBSERVED_EXACT, EventConfidence.INFERRED))
def test_only_observed_blocking_all_join_members_receive_admission_rank(
    monkeypatch, tmp_path, mode, confidence,
):
    live = runtime(monkeypatch, tmp_path, strategy="thunderagent_retained")
    try:
        waiting_join(live, mode=mode, confidence=confidence)
        rank = live._agent_admission._admission_dependency
        expected = 1 if mode == "all" and confidence is EventConfidence.OBSERVED_EXACT else 2
        assert rank("a") == rank("b") == expected
        assert rank("unknown") == 2
        live.on_events((observed(6, Kind.RETURN, invocation_id="b"),))
        assert rank("a") == (0 if expected == 1 else 2)
        assert rank("b") == 2
    finally:
        live.close()


@pytest.mark.parametrize("change", ("satisfied", "stale_parent_join", "ready_parent", "tool", "foreground"))
def test_join_rank_is_revocable_and_does_not_ignore_other_parent_dependencies(
    monkeypatch, tmp_path, change,
):
    live = runtime(monkeypatch, tmp_path, strategy="thunderagent_retained")
    try:
        waiting_join(live)
        parent = live.graph.invocations["parent"]
        if change == "satisfied":
            live.graph.joins["join"].satisfied = True
        elif change == "stale_parent_join":
            parent.join_id = "later-join"
        elif change == "ready_parent":
            parent.state = InvocationState.READY
        elif change == "tool":
            parent.active_tool_calls["run"] = ("execute", 0.)
        else:
            parent.blocking_child_ids.add("foreground")
        assert live._agent_admission._admission_dependency("a") == 2
    finally:
        live.close()


def test_ready_join_parent_still_precedes_a_required_child():
    policy = congested(lambda identity: 0 if identity == "a" else 2)
    policy.agents["ordinary"].critical = True
    assert policy.lend_backed_blocked(lambda _: True, now=1.) == "ordinary"
    assert not policy.allowed("a")


@pytest.mark.parametrize("failure", ("unbacked", "oversized", "running_donor", "protected_donor"))
def test_dependency_cannot_invent_capacity_or_preempt_a_running_or_protected_donor(failure):
    policy = congested(lambda identity: 0 if identity == "a" else 2)
    if failure == "oversized":
        policy.agents["a"].tokens = 2000
    elif failure == "running_donor":
        policy.agents["parent"].in_flight = True
    elif failure == "protected_donor":
        policy.agents["parent"].restore_until = 2.
    selected = policy.lend_backed_blocked(lambda _: failure != "unbacked", now=1.)
    assert not policy.allowed("a")
    assert policy.agents["guardian"].active and policy.agents["guardian"].in_flight
    if failure == "oversized":
        assert selected == "ordinary"
    else:
        assert selected is None and policy.agents["parent"].active


def test_dependency_is_ranked_before_the_eight_target_probe_limit():
    policy = congested(lambda identity: 0 if identity == "a" else 2)
    for index in range(10):
        name = f"ordinary-{index}"
        policy.arrive(name, 350, now=.6 + index / 100.)
        policy.agents[name].steps = 2
    proof, observer = Mock(return_value=True), Mock()
    policy.observe_lending = observer
    assert policy.lend_backed_blocked(proof, now=1.) == "a"
    proof.assert_called_once_with("parent")
    detail = observer.call_args.args[0]
    assert detail["donor_invocation_ids"] == ["parent"]
    assert detail["target_join_rank"] == 0
    assert policy.loan_borrowers["parent"] == {"a"}
    # Ordinary newcomers cannot rotate the newly eligible child.
    policy.begin("a")
    policy.rebalance(now=5.1)
    assert policy.agents["a"].active and policy.agents["a"].in_flight


def test_unready_child_does_not_block_available_ordinary_work():
    policy = congested(lambda identity: 0 if identity == "a" else 2)
    policy.agents["a"].blocked = "long_tool"
    assert policy.lend_backed_blocked(lambda _: True, now=1.) == "ordinary"


def test_observed_budget_proof_uses_actual_blocking_and_keeps_stale_sessions_out(monkeypatch, tmp_path):
    live = runtime(monkeypatch, tmp_path, strategy="thunderagent_retained")
    try:
        waiting_join(live)
        key = NS(invocation_id="parent", context_id="parent-context", root_workflow_id="wf")
        live.context_sessions[key.context_id] = key
        live._terminal = Mock(return_value=False)
        assert live._agent_observed_blocked_budget_key(key)
        parent = live.graph.invocations["parent"]
        parent.state = InvocationState.WAIT_TOOL
        assert not live._agent_observed_blocked_budget_key(key)
        parent.active_tool_calls["run"] = ("execute", 0.)
        assert live._agent_observed_blocked_budget_key(key)
        live.context_sessions[key.context_id] = object()
        assert not live._agent_observed_blocked_budget_key(key)
        live.context_sessions[key.context_id] = key
        live.physical_disabled = True
        assert not live._agent_observed_blocked_budget_key(key)
    finally:
        live.close()


def test_actual_host_backup_and_native_demotion_can_lend_without_a_tool_prediction(
    monkeypatch, tmp_path, full_native,
):
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, _, _, _ = full_native
    live = runtime(
        monkeypatch, tmp_path, strategy="thunderagent_retained",
        blocked_lending="backed_demand_recall", blocked_budget_proof="observed_blocked",
    )
    live._native_cache = cache
    try:
        _, generation = insert_session(cache, list(range(8)), "parent")
        snapshot = anchors(cache, generation, input_tokens=8)
        live._agent_observed_blocked_budget_key = Mock(return_value=True)
        live._live_parent_pressure_key = Mock(return_value=False)
        live.snapshot_session_anchors = lambda *_a, **_k: snapshot
        assert not live._agent_blocked_prefix_backed(snapshot.key, observed_blocked=True)
        cache.prepare_beliefkv_spawn_full(session_id="parent", session_generation=generation)
        assert not live._agent_blocked_prefix_backed(snapshot.key, observed_blocked=True)
        cache.writing_check(finish_count=1)
        assert live._agent_blocked_prefix_backed(snapshot.key, observed_blocked=True)
        cache.beliefkv_cold_full_owner_validator = lambda owners: owners == (("parent", generation),)
        free = allocator.available_size()
        assert cache.reclaim_beliefkv_handoff_capacity(
            full_tokens=free + 8, mamba_slots=0,
        ) == {0: 8}
        assert live._agent_blocked_prefix_backed(snapshot.key, observed_blocked=True)
        assert not live._agent_blocked_prefix_backed(snapshot.key)
        live._live_parent_pressure_key.assert_called_once_with(snapshot.key)
        insert_session(cache, list(range(12)), "parent")
        snapshot = anchors(cache, generation, input_tokens=12)
        assert not live._agent_blocked_prefix_backed(snapshot.key, observed_blocked=True)
    finally:
        live.close()


def test_budget_status_captures_observed_decode_growth_at_the_same_snapshot(monkeypatch, tmp_path):
    from tests.test_paper_baseline_runtime import request

    live = runtime(
        monkeypatch, tmp_path, strategy="thunderagent_retained",
        join_dependency_admission=True,
    )
    try:
        req = request("one", "a", tokens=200)
        live.register_visible_request(req)
        live.on_prefill_candidate_result(req, admitted=True, result="CONTINUE")
        req.output_ids.extend(range(20))
        agents = live._agent_admission
        agents._refresh_streaming_progress()
        status = agents.status()
        assert status["active_agent_budgets"] == [{
            "invocation_id": "a", "tokens": 222, "buffer_tokens": 100,
            "pending": True, "in_flight": True, "blocked": None,
            "marked_for_pause": False,
        }]
        assert status["logical_active_tokens"] == 322
        assert status["shared_prefixes_deduplicated"] is False
    finally:
        live.close()


@pytest.mark.parametrize("mode", ("native_matched", "scheduler_only", "kv_only", "join_priority_only"))
def test_candidate_flags_do_not_leak_into_controls(mode):
    config = {
        "AGENT_ADMISSION": "thunderagent_retained", "AGENT_JOIN_DEPENDENCY_ADMISSION": "1",
        "AGENT_BLOCKED_LENDING": "backed_demand_recall",
        "AGENT_BLOCKED_BUDGET_PROOF": "observed_blocked",
    }
    settings = arm_environment(config, mode)
    assert settings["BELIEFKV_AGENT_JOIN_DEPENDENCY_ADMISSION"] == "0"
    assert settings["BELIEFKV_AGENT_BLOCKED_BUDGET_PROOF"] == "predicted_pressure"
    settings = paper_environment(config, "thunderagent")
    assert settings["BELIEFKV_AGENT_JOIN_DEPENDENCY_ADMISSION"] == "0"
    assert settings["BELIEFKV_AGENT_BLOCKED_BUDGET_PROOF"] == "predicted_pressure"


@pytest.mark.parametrize("config", (
    {"AGENT_JOIN_DEPENDENCY_ADMISSION": "yes"},
    {"AGENT_ADMISSION": "working_set", "AGENT_JOIN_DEPENDENCY_ADMISSION": "1"},
    {"AGENT_ADMISSION": "thunderagent_retained", "AGENT_BLOCKED_BUDGET_PROOF": "observed_blocked"},
))
def test_invalid_or_unqualified_candidate_declarations_are_rejected(config):
    with pytest.raises(ValueError):
        arm_environment(config, "full")
