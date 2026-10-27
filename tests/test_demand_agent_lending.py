"""Observed blocking can lend eligibility without claiming physical KV space."""

from dataclasses import replace
import json
from pathlib import Path
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.core.events import RelationType, RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import MODES, arm_environment
from beliefkv.experiments.paper_baselines import environment as paper_environment
from beliefkv.runtime.sglang_v0520_runtime import _PrefetchServiceLease
from tests.test_agent_working_set_runtime import runtime
from tests.test_full_only_native_lifecycle import full_native
from tests.test_internal_comparison import observed
from tests.test_paper_baseline_runtime import request


@pytest.fixture
def demand_lending(monkeypatch, tmp_path, full_native):
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, writes, loads, _ = full_native
    live = runtime(
        monkeypatch, tmp_path, capacity=1024, strategy="thunderagent_retained",
        blocked_lending="backed_demand_recall", blocked_budget_proof="observed_demand",
        join_dependency_admission=True,
    )
    live._native_cache = cache
    live.on_events((
        observed(0, Kind.WORKFLOW_START),
        observed(1, Kind.INVOCATION_CREATE, invocation_id="donor", context_id="donor-context"),
        observed(2, Kind.INVOCATION_CREATE, invocation_id="hot", context_id="hot-context"),
        observed(3, Kind.INVOCATION_CREATE, invocation_id="target", context_id="target-context"),
    ))
    node, generation = insert_session(cache, list(range(256)), "donor-session")
    first = request("first", "donor", tokens=256, workflow="wf")
    first.session_id, first.session_generation = "donor-session", generation
    live.register_visible_request(first)
    live.on_prefill_candidate_result(first, admitted=True, result="CONTINUE")
    live.on_batch_completed(NS(reqs=[first]))
    live.on_events((observed(
        4, Kind.TOOL_START, invocation_id="donor",
        attributes={"tool_run_id": "tool", "tool_family": "shell"},
    ),))
    hot = request("hot", "hot", tokens=400, workflow="wf")
    live.register_visible_request(hot)
    live.on_prefill_candidate_result(hot, admitted=True, result="CONTINUE")
    live._native_running_batch = NS(reqs=[hot])
    live._native_max_running = 24
    target = request("target", "target", tokens=256, workflow="wf")
    live.register_visible_request(target)
    live._capacity_waiting_queue = [target]
    agents = live._agent_admission
    agents.tool_waits["donor"]["tool"] = time.monotonic() - 2.
    live._long_tool_wait = Mock(return_value=False)
    live._predicted_long_tool_wait = Mock(return_value=False)
    try:
        yield live, agents, cache, allocator, node, first, target, writes, loads
    finally:
        live.close()


@pytest.mark.parametrize("full_native", (1024,), indirect=True)
def test_waiting_work_acquires_eligibility_without_complete_host_or_prediction(demand_lending):
    live, agents, _, allocator, node, _, target, writes, loads = demand_lending
    before = allocator.available_size()
    assert node.component_data[0].host_value is None
    assert not agents.allows(live.visible[target.rid])
    live._agent_blocked_prefix_backed = Mock(side_effect=AssertionError("no Host path scan"))
    agents.tick()
    assert agents.allows(live.visible[target.rid])
    assert not agents.policy.agents["donor"].active
    assert agents.policy.agents["hot"].in_flight
    assert agents.policy.loan_borrowers["donor"] == {"target"}
    assert allocator.available_size() == before
    assert node.component_data[0].value is not None
    assert node.component_data[0].host_value is None
    assert not writes and not loads and not live.physical_ledger.pending_count
    assert agents.policy.used_tokens <= agents.policy.capacity_tokens
    status = agents.status()
    assert status["blocked_lending_requires_complete_host_checkpoint"] is False
    assert status["blocked_lending_grants_physical_capacity"] is False


@pytest.mark.parametrize("full_native", (1024,), indirect=True)
def test_logical_loan_does_not_satisfy_a_real_allocator_shortfall(demand_lending):
    live, agents, cache, allocator, node, _, target, writes, _ = demand_lending
    filler = allocator.alloc(allocator.available_size() - 8)
    assert filler is not None
    agents.tick()
    assert agents.allows(live.visible[target.rid])
    assert allocator.available_size() == 8
    assert allocator.alloc(256) is None
    assert not writes
    # A completed native Host checkpoint and native demotion provide the pages;
    # the logical loan alone provided none of them.
    assert cache.prepare_beliefkv_spawn_full(
        session_id="donor-session", session_generation=1,
    )["issued"]
    assert allocator.available_size() == 8
    cache.writing_check(finish_count=1)
    assert node.component_data[0].host_value is not None
    assert cache._can_beliefkv_full_demote(node, full_session_ref=1)
    cache._demote(node.id, {})
    assert allocator.available_size() == 264
    assert allocator.alloc(256) is not None
    assert allocator.available_size() == 8


@pytest.mark.parametrize("full_native", (1024,), indirect=True)
@pytest.mark.parametrize("reason", (
    "short_tool", "tool_return", "epoch", "native_generation", "closed_session",
    "no_session", "no_observed_start", "in_flight", "pending", "restore",
    "tool_ticket", "inflight_prefetch", "prefetch_lease", "disabled",
))
def test_fallback_keeps_real_identity_blocking_and_restore_guards(demand_lending, reason):
    live, agents, cache, allocator, node, first, _, writes, loads = demand_lending
    key = agents.latest["donor"]
    item = agents.policy.agents["donor"]
    if reason == "short_tool":
        agents.tool_waits["donor"]["tool"] = time.monotonic()
    elif reason == "tool_return":
        live.on_events((observed(
            5, Kind.TOOL_END, invocation_id="donor", attributes={"tool_run_id": "tool"},
        ),))
    elif reason == "epoch":
        live.graph.contexts[key.context_id].epoch += 1
    elif reason == "native_generation":
        cache.session_refs._session_generations[key.session_id] += 1
    elif reason == "closed_session":
        cache.session_refs._closed_session_ids[key.session_id] = 1
    elif reason == "no_session":
        stale = replace(key, session_id=None)
        agents.latest["donor"] = live.context_sessions[key.context_id] = stale
    elif reason == "no_observed_start":
        agents.tool_waits.clear()
    elif reason == "in_flight":
        item.in_flight = True
    elif reason == "pending":
        item.pending = True
    elif reason == "restore":
        item.restore_until = time.monotonic() + 10.
        agents.policy.restore_owner = "donor"
    elif reason == "tool_ticket":
        live._tool_ticket = NS(key=key)
    elif reason == "inflight_prefetch":
        live._tool_inflight_tickets[key] = NS(key=key)
    elif reason == "prefetch_lease":
        live._prefetch_service_leases["lease"] = _PrefetchServiceLease(
            key, "lease", node.id, node.creation_time, "tool_wait", None,
            time.monotonic(), time.monotonic() + 10., (),
        )
    else:
        live.physical_disabled = True
    before = allocator.available_size()
    assert not agents._can_lend_blocked("donor")
    assert allocator.available_size() == before
    assert node.component_data[0].value is not None
    assert not writes and not loads


@pytest.mark.parametrize("full_native", (1024,), indirect=True)
def test_no_waiting_work_preserves_blocked_agent_eligibility(demand_lending):
    _, agents, _, _, _, _, _, _, _ = demand_lending
    agents.policy.forget("target", now=time.monotonic())
    agents.tick()
    assert agents.policy.agents["donor"].active
    assert not agents.policy.counts["backed_wait_admitted"]


@pytest.mark.parametrize("full_native", (1024,), indirect=True)
def test_waiting_join_can_lend_but_background_spawn_does_not_block_parent(demand_lending):
    live, agents, _, _, _, _, _, _, _ = demand_lending
    live.on_events((
        observed(5, Kind.TOOL_END, invocation_id="donor", attributes={"tool_run_id": "tool"}),
        observed(6, Kind.INVOCATION_CREATE, invocation_id="child", context_id="child-context",
                 parent_invocation_id="donor", relation_type=RelationType.SPAWN),
        observed(7, Kind.JOIN_CREATE, join_id="join", member_invocation_ids=("child",)),
    ))
    assert not agents._can_lend_blocked("donor")
    live.on_events((observed(8, Kind.JOIN_WAIT, invocation_id="donor", join_id="join"),))
    assert agents._can_lend_blocked("donor")
    agents.tick()
    assert agents.policy.allowed("target")
    assert not agents.policy.agents["donor"].active


@pytest.mark.parametrize("mode", MODES)
def test_demand_lending_is_an_explicit_full_only_policy(mode):
    settings = arm_environment({
        "AGENT_ADMISSION": "thunderagent_retained",
        "AGENT_BLOCKED_LENDING": "backed_demand_recall",
        "AGENT_BLOCKED_BUDGET_PROOF": "observed_demand",
    }, mode)
    assert settings["BELIEFKV_AGENT_BLOCKED_BUDGET_PROOF"] == (
        "observed_demand" if mode == "full" else "predicted_pressure"
    )


@pytest.mark.parametrize("arm", ("thunderagent", "thunderagent_acting_decay"))
def test_paper_controls_do_not_receive_the_fallback(arm):
    settings = paper_environment({
        "AGENT_ADMISSION": "thunderagent_retained",
        "AGENT_BLOCKED_LENDING": "backed_demand_recall",
        "AGENT_BLOCKED_BUDGET_PROOF": "observed_demand",
    }, arm)
    assert settings["BELIEFKV_AGENT_BLOCKED_BUDGET_PROOF"] == "predicted_pressure"
    assert settings["BELIEFKV_AGENT_ADMISSION"] == "off"


def test_candidate_changes_only_logical_lending_and_keeps_the_common_workload():
    root = Path(__file__).resolve().parents[1]
    base = json.loads((root / "configs/migration/predictive_adaptive_tool_offload_20261027.json").read_bytes())
    candidate = json.loads((root / "configs/migration/predictive_demand_lending_20261027.json").read_bytes())
    assert candidate["common_environment"] == {
        **base["common_environment"], "AGENT_BLOCKED_BUDGET_PROOF": "observed_demand",
    }
    assert candidate["tool_timing_overlay"] == base["tool_timing_overlay"]
