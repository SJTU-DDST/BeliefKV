"""Private causal plans for a separately named AgentX structural replay.

Recorded API durations locate external gaps. Actual model execution remains an
unresolved operation; no old GPU service time is replayed as a timer.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from graphlib import TopologicalSorter
import hashlib
import json
import math
from pathlib import Path
from typing import Callable, Iterable

from beliefkv.core.events import (
    ContextMode, EventConfidence, ExecutionMode, RelationType,
    RuntimeEvent, RuntimeEventKind as Kind,
)
from beliefkv.experiments.causal_replay import (
    CausalReplayError, CausalReplayPlan, ReplayNode, event_node_id, request_node_id,
)
from beliefkv.experiments.replay_timing import ReplayTimingPolicy


@dataclass(frozen=True)
class AgentXCall:
    request_id: str
    recorded_start_s: float
    recorded_api_s: float
    input_tokens: int
    output_tokens: int
    source_model: str
    hash_ids: tuple[int, ...]
    source_reference: tuple[int, ...]
    predecessor_request_ids: tuple[str, ...] = ()

    @property
    def recorded_end_s(self):
        return self.recorded_start_s + self.recorded_api_s


@dataclass(frozen=True)
class AgentXAgent:
    invocation_id: str
    calls: tuple[AgentXCall, ...]
    parent_invocation_id: str | None = None
    spawn_after_request_id: str | None = None
    recorded_spawn_s: float = 0.
    recorded_terminal_s: float | None = None
    source_kind: str = "main"
    context_lifetime: str = "unknown"

    @property
    def terminal_s(self):
        return (
            self.calls[-1].recorded_end_s
            if self.recorded_terminal_s is None else self.recorded_terminal_s
        )


@dataclass(frozen=True)
class AgentXJoin:
    join_id: str
    before_request_id: str
    member_invocation_ids: tuple[str, ...]


@dataclass(frozen=True)
class AgentXWorkflow:
    workflow_id: str
    agents: tuple[AgentXAgent, ...]
    joins: tuple[AgentXJoin, ...]
    block_size: int
    source_trace_id: str


def workflow_from_agentx(
    raw: dict, partition: Callable, interval_predecessors: Callable,
) -> AgentXWorkflow:
    """Retain every call using pinned-author chain and interval-order helpers.

    The public harness flattens inferred overflow chains onto the root. This
    adapter keeps that declared mapping and does not claim original nested DAGs.
    """
    if raw.get("hash_id_scope") != "local":
        raise CausalReplayError("AgentX hashes must remain scoped to one source tree")
    trace = raw["id"]
    wid, root = "agentx:" + trace, "agentx:" + trace + ":root"
    entries = raw["requests"]
    top_pairs = [
        (index, row) for index, row in enumerate(entries) if row["type"] in ("n", "s")
    ]
    top = dict(top_pairs)
    main, workers = partition(top_pairs)
    if not main:
        raise CausalReplayError("AgentX trace has no retained main chain")
    joins = {}
    agents = []
    scope_agents = {}

    def make_call(row, reference):
        rid = wid + ":request:" + ":".join(map(str, reference))
        return AgentXCall(
            rid, row["t"], row["api_time"], row["in"], row["out"],
            row["model"], tuple(row.get("hash_ids", ())), tuple(reference),
        )

    def emitter(outer):
        preceding = [index for index in main if index < outer]
        if not preceding:
            raise CausalReplayError("AgentX child has no preceding root call")
        return make_call(top[max(preceding)], (max(preceding),)).request_id

    def join_before(outer, end, members):
        following = next((
            index for index in main if index > outer and top[index]["t"] + 1e-6 >= end
        ), None)
        if following is not None:
            joins.setdefault(following, []).extend(members)

    agents.append(AgentXAgent(
        root, tuple(make_call(top[index], (index,)) for index in main),
    ))
    scope_agents[trace] = [root]
    for number, worker in enumerate(workers):
        indices = worker["indices"]
        iid = wid + ":flat:" + str(number)
        calls = tuple(make_call(top[index], (index,)) for index in indices)
        if not calls:
            raise CausalReplayError("empty inferred AgentX stream")
        agent = AgentXAgent(
            iid, calls, root, emitter(indices[0]), calls[0].recorded_start_s,
            max(call.recorded_end_s for call in calls),
            "auxiliary" if worker["auxiliary"] else "inferred_flat_worker",
        )
        agents.append(agent)
        scope_agents[trace].append(iid)
        join_before(indices[0], agent.terminal_s, (iid,))
    for outer, entry in enumerate(entries):
        if entry["type"] != "subagent":
            continue
        if entry["status"] != "completed" or not entry["requests"]:
            raise CausalReplayError("unobserved or incomplete AgentX child cannot be replay-qualified")
        normalized = [
            {**row, "t": (
                entry["t"] + row["t"] if row["t"] + 1e-6 < entry["t"] else row["t"]
            )}
            for row in entry["requests"]
        ]
        child_main, child_workers = partition(list(enumerate(normalized)))
        terminal = (
            entry["t"] + entry["duration_ms"] / 1000.
            if entry.get("duration_ms") is not None
            else max(row["t"] + row["api_time"] for row in normalized)
        )
        members = []
        for number, indices in enumerate((
            child_main, *(worker["indices"] for worker in child_workers),
        )):
            iid = wid + ":subagent:" + entry["agent_id"] + ":chain:" + str(number)
            calls = tuple(make_call(normalized[index], (outer, index)) for index in indices)
            worker = child_workers[number - 1] if number else None
            agents.append(AgentXAgent(
                iid, calls, root, emitter(outer), entry["t"],
                max(call.recorded_end_s for call in calls) if number else terminal,
                "explicit_subagent" if number == 0 else
                "auxiliary" if worker["auxiliary"] else "inferred_child_overflow",
                # Role/status and the last observed call do not establish closure.
                context_lifetime=entry.get("context_lifetime", "unknown"),
            ))
            members.append(iid)
        scope_agents[trace + ":subagent:" + str(outer)] = members
        join_before(outer, terminal, members)

    by_id = {agent.invocation_id: agent for agent in agents}
    prerequisites = {}
    for members in scope_agents.values():
        prerequisites.update(interval_predecessors([
            (iid, call) for iid in members for call in by_id[iid].calls
        ]))
    agents = [
        replace(agent, calls=tuple(
            replace(call, predecessor_request_ids=tuple(prerequisites.get(call.request_id, ())))
            for call in agent.calls
        ))
        for agent in agents
    ]
    expected = {
        *((index,) for index, _ in top_pairs),
        *((outer, index) for outer, entry in enumerate(entries) if entry["type"] == "subagent"
          for index, _ in enumerate(entry["requests"])),
    }
    actual = [call.source_reference for agent in agents for call in agent.calls]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise CausalReplayError("AgentX partition dropped or duplicated source calls")
    return AgentXWorkflow(
        wid, tuple(agents), tuple(
            AgentXJoin(
                wid + ":join-before:" + str(index),
                make_call(top[index], (index,)).request_id, tuple(members),
            )
            for index, members in sorted(joins.items())
        ), raw["block_size"], trace,
    )


def context_id(invocation):
    return invocation + ":context"


def _finite_nonnegative(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def build_agentx_plan(workflows: Iterable[AgentXWorkflow]) -> CausalReplayPlan:
    """Compile all published calls; expose only events that become executable.

    Positive unlabelled inter-call gaps use explicitly anonymous external phases.
    They are not claims about original tool commands or semantic categories.
    """
    nodes, usage = {}, {}
    workflow_ids, invocation_ids = set(), set()

    def add_event(workflow, suffix, kind, dependencies, *, invocation=None, delay_s=0., **kwargs):
        if not _finite_nonnegative(delay_s):
            raise CausalReplayError("invalid AgentX event delay")
        event = RuntimeEvent(
            event_id=workflow + ":" + suffix, ts_ms=0., kind=kind,
            workflow_id=workflow, invocation_id=invocation,
            context_id=context_id(invocation) if invocation else None,
            context_epoch=0 if invocation else None,
            confidence=EventConfidence.INFERRED,
            **kwargs,
        )
        key = event_node_id(event.event_id)
        if key in nodes:
            raise CausalReplayError("duplicate AgentX event")
        nodes[key] = ReplayNode(
            key, workflow, tuple(sorted(set(dependencies))), delay_s * 1000., event=event,
        )
        return key

    for workflow in workflows:
        wid = workflow.workflow_id
        if not wid or wid in workflow_ids or not workflow.agents or workflow.block_size <= 0:
            raise CausalReplayError("unique nonempty AgentX workflow required")
        workflow_ids.add(wid)
        agents = {agent.invocation_id: agent for agent in workflow.agents}
        roots = [agent for agent in workflow.agents if agent.parent_invocation_id is None]
        if len(agents) != len(workflow.agents) or len(roots) != 1 or invocation_ids & agents.keys():
            raise CausalReplayError("unique agents and exactly one root required")
        invocation_ids.update(agents)
        calls, owners, references = {}, {}, set()
        for agent in workflow.agents:
            if not agent.calls or not agent.invocation_id:
                raise CausalReplayError("empty AgentX agent cannot count as model-served")
            if agent.context_lifetime not in ("temporary", "persistent", "unknown"):
                raise CausalReplayError("AgentX context lifetime must be temporary, persistent or unknown")
            if (
                not _finite_nonnegative(agent.recorded_spawn_s)
                or not _finite_nonnegative(agent.terminal_s)
                or agent.terminal_s + 1e-6 < agent.calls[-1].recorded_end_s
            ):
                raise CausalReplayError("AgentX terminal precedes its recorded calls")
            for call in agent.calls:
                if (
                    not call.request_id or call.request_id in calls or call.request_id in usage
                    or call.source_reference in references
                    or not _finite_nonnegative(call.recorded_start_s)
                    or not _finite_nonnegative(call.recorded_api_s)
                    or type(call.input_tokens) is not int or call.input_tokens <= 0
                    or type(call.output_tokens) is not int or call.output_tokens < 0
                    or not call.source_model
                    or any(type(value) is not int or value < 0 for value in call.hash_ids)
                    or len(call.hash_ids) * workflow.block_size >= call.input_tokens + workflow.block_size
                ):
                    raise CausalReplayError("invalid or duplicate AgentX call")
                calls[call.request_id], owners[call.request_id] = call, agent
                references.add(call.source_reference)
                usage[call.request_id] = call.input_tokens, call.output_tokens
            for previous, current in zip(agent.calls, agent.calls[1:]):
                if current.recorded_start_s < previous.recorded_start_s:
                    raise CausalReplayError("AgentX chain is not time ordered")
        for call in calls.values():
            if (
                not set(call.predecessor_request_ids) <= calls.keys()
                or call.request_id in call.predecessor_request_ids
            ):
                raise CausalReplayError("AgentX interval frontier references an unknown or self call")

        by_request = {}
        joined_members = set()
        for join in workflow.joins:
            if (
                not join.join_id or join.before_request_id not in calls
                or join.before_request_id in by_request or not join.member_invocation_ids
                or len(set(join.member_invocation_ids)) != len(join.member_invocation_ids)
                or not set(join.member_invocation_ids) <= agents.keys()
                or joined_members & set(join.member_invocation_ids)
                or owners[join.before_request_id].invocation_id in join.member_invocation_ids
            ):
                raise CausalReplayError("invalid, repeated or self-referential AgentX JOIN")
            by_request[join.before_request_id] = join
            joined_members.update(join.member_invocation_ids)

        wf_start = add_event(wid, "start", Kind.WORKFLOW_START, ())
        created, launched, returns, results = {}, {}, {}, {}
        for agent in workflow.agents:
            iid = agent.invocation_id
            created[iid] = event_node_id(wid + ":create:" + iid)
            launched[iid] = event_node_id(wid + ":spawn:" + iid)
            returns[iid] = event_node_id(wid + ":return:" + iid)
            for call in agent.calls:
                results[call.request_id] = event_node_id(wid + ":result:" + call.request_id)
        child_launches = {iid: [] for iid in agents}
        for agent in workflow.agents:
            if agent.parent_invocation_id is not None:
                parent = agents.get(agent.parent_invocation_id)
                emitter = agent.spawn_after_request_id
                if parent is None or emitter not in calls or owners[emitter] != parent:
                    raise CausalReplayError("AgentX child lacks its actual parent/emitter")
                child_launches[parent.invocation_id].append(launched[agent.invocation_id])

        for agent in workflow.agents:
            iid, parent = agent.invocation_id, agent.parent_invocation_id
            attributes = {
                "source": "agentx_structural_replay", "source_kind": agent.source_kind,
                "context_lifetime": agent.context_lifetime,
                "persistent": agent.context_lifetime != "temporary",
            }
            if parent is None:
                create_dependencies, create_delay = (wf_start,), 0.
            else:
                emitter = calls[agent.spawn_after_request_id]
                create_dependencies = (results[emitter.request_id],)
                create_delay = max(0., agent.recorded_spawn_s - emitter.recorded_end_s)
            add_event(
                wid, "create:" + iid, Kind.INVOCATION_CREATE, create_dependencies,
                invocation=iid, delay_s=create_delay,
                parent_invocation_id=parent,
                parent_context_id=context_id(parent) if parent else None,
                relation_type=RelationType.SPAWN if parent else RelationType.ROOT,
                context_mode=ContextMode.FRESH,
                execution_mode=ExecutionMode.BACKGROUND if parent else ExecutionMode.FOREGROUND,
                agent_definition_id="agentx:" + agent.source_kind,
                agent_instance_id=iid, return_target_id=parent, attributes=attributes,
            )
            if parent is not None:
                add_event(
                    wid, "spawn:" + iid, Kind.SPAWN, (created[iid],),
                    invocation=parent, target_invocation_id=iid,
                    target_context_id=context_id(iid),
                    execution_mode=ExecutionMode.BACKGROUND, attributes=attributes,
                )
            previous = None
            for call in agent.calls:
                dependencies = (
                    results[previous.request_id] if previous else
                    launched[iid] if parent else created[iid],
                )
                reference_end = previous.recorded_end_s if previous else agent.recorded_spawn_s
                if (join := by_request.get(call.request_id)) is not None:
                    members = join.member_invocation_ids
                    join_evidence = {
                        "source": "agentx_timestamp_inferred_join",
                        "online_observable": False,
                    }
                    join_create = add_event(
                        wid, "join-create:" + join.join_id, Kind.JOIN_CREATE,
                        (*dependencies, *(launched[member] for member in members)),
                        join_id=join.join_id, member_invocation_ids=members,
                        attributes={"mode": "all", **join_evidence},
                    )
                    wait = add_event(
                        wid, "join-wait:" + join.join_id, Kind.JOIN_WAIT, (join_create,),
                        invocation=iid, join_id=join.join_id,
                        attributes=join_evidence,
                    )
                    satisfied = add_event(
                        wid, "join-satisfied:" + join.join_id, Kind.JOIN_SATISFIED,
                        (wait, *(returns[member] for member in members)), join_id=join.join_id,
                        attributes=join_evidence,
                    )
                    dependencies = (satisfied,)
                    reference_end = max(reference_end, *(agents[member].terminal_s for member in members))
                predecessors = call.predecessor_request_ids
                if predecessors:
                    reference_end = max(
                        reference_end, *(calls[rid].recorded_end_s for rid in predecessors),
                    )
                gap = max(0., call.recorded_start_s - reference_end)
                if (gap or predecessors) and previous is not None:
                    tool = call.request_id + ":anonymous-external"
                    start = add_event(
                        wid, "external-start:" + call.request_id, Kind.TOOL_START,
                        dependencies, invocation=iid, attributes={
                            "tool_run_id": tool, "tool_family": "agentx_unknown_external",
                            "source": "agentx_unlabelled_inter_call_gap",
                            "original_tool_identity_available": False,
                        },
                    )
                    dependencies = (add_event(
                        wid, "external-end:" + call.request_id, Kind.TOOL_END,
                        (start, *(results[rid] for rid in predecessors)),
                        invocation=iid, delay_s=gap, attributes={"tool_run_id": tool},
                    ),)
                    gap = 0.
                elif predecessors:
                    dependencies = (*dependencies, *(results[rid] for rid in predecessors))
                submit = add_event(
                    wid, "submit:" + call.request_id, Kind.LLM_SUBMIT, dependencies,
                    invocation=iid, delay_s=gap,
                    attributes={"request_id": call.request_id, "sampling_seed": 21},
                )
                native = request_node_id(call.request_id)
                nodes[native] = ReplayNode(
                    native, wid, (submit,), 0., request_id=call.request_id,
                )
                add_event(
                    wid, "result:" + call.request_id, Kind.LLM_RESULT, (native,),
                    invocation=iid, attributes={"request_id": call.request_id},
                )
                previous = call
            add_event(
                wid, "return:" + iid, Kind.RETURN,
                (results[previous.request_id], *child_launches[iid]),
                invocation=iid,
                delay_s=max(0., agent.terminal_s - previous.recorded_end_s),
                attributes={
                    "context_retired": agent.context_lifetime == "temporary",
                    "context_lifetime": agent.context_lifetime,
                    "source": "agentx_structural_terminal",
                },
            )
        add_event(wid, "end", Kind.WORKFLOW_END, tuple(returns.values()))

    if not nodes:
        raise CausalReplayError("empty AgentX population")
    if any(dependency not in nodes for node in nodes.values() for dependency in node.dependencies):
        raise CausalReplayError("AgentX plan references an unknown dependency")
    try:
        tuple(TopologicalSorter({
            key: node.dependencies for key, node in nodes.items()
        }).static_order())
    except ValueError as error:
        raise CausalReplayError("cyclic AgentX causal plan") from error
    return CausalReplayPlan(tuple(nodes.values()), usage, False, 0)


def load_agentx_preparation(directory: Path):
    """Load a pinned external source without a natural/native source capture."""
    report = json.loads((directory / "preparation.json").read_bytes())
    if (
        report.get("workload_kind") != "agentx_whole_public_trace_structural_adaptation"
        or report.get("requests_removed_from_retained_sessions") != 0
        or report.get("native_source_capture_required") is not False
    ):
        raise CausalReplayError("AgentX source must retain complete published sessions")
    files = {}
    for name in ("plan.json", "workflows.json", "timing.json"):
        raw = (directory / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != report["artifacts"][name]:
            raise CausalReplayError("prepared AgentX source changed: " + name)
        files[name] = json.loads(raw)
    data = files["plan.json"]
    plan = CausalReplayPlan(
        tuple(ReplayNode(
            row["node_id"], row["workflow_id"], tuple(row["dependencies"]), row["delay_ms"],
            RuntimeEvent.from_dict(row["event"]) if row["event"] is not None else None,
            row["request_id"],
        ) for row in data["nodes"]),
        {request: tuple(usage) for request, usage in data["request_usage"].items()},
        False, 0,
    )
    if (
        plan.summary()["requests"] != report["summary"]["requests"]
        or plan.summary()["workflows"] != report["roots"]
        or files["timing.json"].get("per_turn_delay_clamping") is not False
    ):
        raise CausalReplayError("AgentX manifest and execution population differ")
    timing = ReplayTimingPolicy(**files["timing.json"]["policy"])
    return plan, timing, files["workflows.json"]["workflows"], report
