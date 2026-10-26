import asyncio
from dataclasses import replace
import time

import pytest

from beliefkv.control.causal_graph import RuntimeCausalContextGraph
from beliefkv.core.events import RuntimeEventKind as Kind, is_unobserved_join_event
from beliefkv.experiments.agentx_replay import (
    AgentXAgent, AgentXCall, AgentXJoin, AgentXWorkflow, build_agentx_plan,
    workflow_from_agentx,
)
from beliefkv.experiments.causal_replay import (
    CausalReplayError, NativeReplayCompletion, evaluate_causal_plan, execute_causal_replay,
)
from beliefkv.runtime.clock_evidence import local_monotonic_clock_domain
from beliefkv.experiments.causal_replay_delivery import ReplayEventDelivery


def call(rid, start, api, ref, **updates):
    return AgentXCall(rid, start, api, 128, 4, "source-model", (1, 2), ref, **updates)


def parallel():
    root = AgentXAgent("root", (call("r0", 0., 2., (0,)), call("r1", 12., 2., (3,))))
    a = AgentXAgent("a", (call("a0", 2., 4., (1, 0)),), "root", "r0", 2., 6.)
    b = AgentXAgent("b", (call("b0", 2., 8., (2, 0)),), "root", "r0", 2., 10.)
    return AgentXWorkflow("w", (root, a, b), (AgentXJoin("join", "r1", ("a", "b")),), 64, "source")


def test_actual_model_service_replaces_old_service_and_join_wait_is_not_counted_twice():
    plan = build_agentx_plan((parallel(),))
    original = evaluate_causal_plan(plan, {"r0": 2000., "r1": 2000., "a0": 4000., "b0": 8000.})
    faster = evaluate_causal_plan(plan, {"r0": 2000., "r1": 2000., "a0": 4000., "b0": 4000.})
    assert original["workflow_elapsed_ms"] == {"w": 14000.}
    assert faster["workflow_elapsed_ms"] == {"w": 10000.}
    assert plan.native_completion_boundaries_verified is False


def test_anonymous_gap_does_not_expose_a_future_duration_or_tool_identity_to_policy():
    plan = build_agentx_plan((parallel(),))
    starts = [node for node in plan.nodes if node.event and node.event.kind is Kind.TOOL_START]
    assert len(starts) == 1
    assert starts[0].event.attributes == {
        "tool_run_id": "r1:anonymous-external",
        "tool_family": "agentx_unknown_external",
        "source": "agentx_unlabelled_inter_call_gap",
        "original_tool_identity_available": False,
    }
    assert not any(
        name in node.event.attributes
        for node in plan.nodes if node.event
        for name in ("recorded_api_s", "output_tokens", "predecessor_request_ids")
    )


def test_recorded_interval_predecessor_subtracts_its_old_api_from_an_external_gap():
    workflow = parallel()
    root = replace(workflow.agents[0], calls=(
        workflow.agents[0].calls[0],
        replace(workflow.agents[0].calls[1], predecessor_request_ids=("b0",)),
    ))
    plan = build_agentx_plan((replace(workflow, agents=(root, *workflow.agents[1:]), joins=()),))
    assert evaluate_causal_plan(
        plan, {"r0": 2000., "r1": 2000., "a0": 4000., "b0": 4000.},
    )["workflow_elapsed_ms"] == {"w": 10000.}


def test_background_work_is_counted_after_the_root_returns_without_a_later_join():
    workflow = parallel()
    root = replace(workflow.agents[0], calls=workflow.agents[0].calls[:1])
    plan = build_agentx_plan((replace(workflow, agents=(root, *workflow.agents[1:]), joins=()),))
    assert evaluate_causal_plan(
        plan, {"r0": 2000., "a0": 4000., "b0": 8000.},
    )["workflow_elapsed_ms"] == {"w": 10000.}


def test_existing_executor_applies_every_event_to_real_causal_state_and_drains_all_calls():
    workflow = parallel()
    plan = build_agentx_plan((workflow,))
    graph = RuntimeCausalContextGraph()
    owners = {call.request_id: agent.invocation_id for agent in workflow.agents for call in agent.calls}
    active, completed = set(), set()

    async def execute(rid, submit):
        assert owners[rid] not in active
        if rid == "r1":
            assert {"a0", "b0"} <= completed
        active.add(owners[rid])
        await asyncio.sleep(.001)
        active.remove(owners[rid])
        completed.add(rid)
        return NativeReplayCompletion(
            rid, time.monotonic() * 1000., local_monotonic_clock_domain(), 128, 4,
        )

    async def emit(event):
        graph.apply(event)

    # The fixture keeps the causal graph and omits actual external waiting.
    plan = replace(plan, nodes=tuple(replace(node, delay_ms=0.) for node in plan.nodes))
    result = asyncio.run(execute_causal_replay(plan, execute, emit))
    assert completed == {"r0", "r1", "a0", "b0"}
    assert result["completed_requests"] == 4
    assert all(agent.state.terminal for agent in graph.invocations.values())
    assert graph.joins["join"].satisfied


def test_private_join_preserves_replay_barrier_without_online_parent_blocking():
    workflow = parallel()
    plan = build_agentx_plan((workflow,))
    plan = replace(plan, nodes=tuple(replace(node, delay_ms=0.) for node in plan.nodes))
    graph = RuntimeCausalContextGraph()
    logged, completed, observations = [], set(), []

    class Transport:
        def emit_batch(self, events):
            for event in events:
                graph.apply(event)
                if event.kind is Kind.SPAWN:
                    observations.append(graph.invocations["root"].state.value)

        def close(self):
            pass

    async def execute(rid, submit):
        if rid == "r1":
            assert {"a0", "b0"} <= completed
        await asyncio.sleep(.001)
        completed.add(rid)
        return NativeReplayCompletion(
            rid, time.monotonic() * 1000., local_monotonic_clock_domain(), 128, 4,
        )

    with ReplayEventDelivery(logged.extend, lambda _workflow: Transport()) as delivery:
        async def emit(event):
            await asyncio.to_thread(delivery.emit_batch, (event,))

        result = asyncio.run(execute_causal_replay(plan, execute, emit))
        delivery.flush()
        assert delivery.summary()["private_join_events_by_workflow"] == {"w": 3}
    assert observations == ["ready", "ready"]
    assert result["completed_requests"] == 4
    assert not graph.joins
    assert all(agent.state.terminal for agent in graph.invocations.values())
    joins = [event for event in logged if is_unobserved_join_event(event)]
    assert len(joins) == 3
    assert all(event.attributes["online_observable"] is False for event in joins)


@pytest.mark.parametrize("change", ("duplicate_call", "unknown_predecessor", "self_join", "future_spawn"))
def test_corrupt_or_cyclic_population_fails_instead_of_dropping_work(change):
    workflow = parallel()
    root, a, b = workflow.agents
    if change == "duplicate_call":
        a = replace(a, calls=(replace(a.calls[0], request_id="r0"),))
    elif change == "unknown_predecessor":
        a = replace(a, calls=(replace(a.calls[0], predecessor_request_ids=("missing",)),))
    elif change == "self_join":
        workflow = replace(workflow, joins=(AgentXJoin("join", "r1", ("root",)),))
    else:
        a = replace(a, spawn_after_request_id="r1")
    with pytest.raises(CausalReplayError):
        build_agentx_plan((replace(workflow, agents=(root, a, b)),))


def test_source_partition_cannot_omit_a_recorded_auxiliary_call():
    raw = {
        "id": "source", "hash_id_scope": "local", "block_size": 64,
        "requests": [
            {"type": "s", "t": i * 2., "api_time": 1., "in": 128, "out": 4, "model": "m"}
            for i in range(2)
        ],
    }
    with pytest.raises(CausalReplayError, match="dropped or duplicated"):
        workflow_from_agentx(raw, lambda _: ([0], []), lambda _: {})


@pytest.mark.parametrize("lifetime", ("temporary", "persistent", "unknown"))
def test_return_closes_only_explicitly_temporary_context_without_pinning_cache(lifetime):
    workflow = parallel()
    workflow = replace(
        workflow, agents=tuple(replace(agent, context_lifetime=lifetime) for agent in workflow.agents),
    )
    plan = build_agentx_plan((workflow,))
    graph = RuntimeCausalContextGraph()
    for node in plan.nodes:
        event = node.event
        if event is not None and event.kind in (Kind.WORKFLOW_START, Kind.INVOCATION_CREATE):
            graph.apply(event)
    returns = [node.event for node in plan.nodes if node.event and node.event.kind is Kind.RETURN]
    assert len(returns) == 3
    assert all(event.attributes["context_retired"] is (lifetime == "temporary") for event in returns)
    assert all(context.persistent is (lifetime != "temporary") for context in graph.contexts.values())
    # Persistence preserves context validity; no DMA/Host/HBM lock is requested.
    assert not any(
        name in node.event.attributes
        for node in plan.nodes if node.event
        for name in ("pin_host", "pin_hbm", "future_reuse", "next_use_time")
    )
    assert plan.request_usage == build_agentx_plan((parallel(),)).request_usage


def test_child_role_and_completed_status_are_not_temporary_lifetime_evidence():
    request = {"type": "s", "t": 0., "api_time": 1., "in": 128, "out": 4, "model": "m"}
    raw = {
        "id": "source", "hash_id_scope": "local", "block_size": 64,
        "requests": [
            request,
            {
                "type": "subagent", "agent_id": "a", "subagent_type": "Explore",
                "t": 1., "duration_ms": 1000, "status": "completed",
                "requests": [{**request, "t": 1.}],
            },
            {**request, "t": 3.},
        ],
    }
    workflow = workflow_from_agentx(
        raw, lambda pairs: ([index for index, _ in pairs], []), lambda _: {},
    )
    child = next(agent for agent in workflow.agents if agent.parent_invocation_id)
    assert child.context_lifetime == "unknown"
    terminal = next(
        node.event for node in build_agentx_plan((workflow,)).nodes
        if node.event and node.event.kind is Kind.RETURN and node.event.invocation_id == child.invocation_id
    )
    assert terminal.attributes["context_retired"] is False


def test_invalid_lifetime_is_rejected_before_replay():
    workflow = parallel()
    root, *children = workflow.agents
    with pytest.raises(CausalReplayError, match="context lifetime"):
        build_agentx_plan((replace(
            workflow, agents=(root, replace(children[0], context_lifetime="probably_done"), children[1]),
        ),))
