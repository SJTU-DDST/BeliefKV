"""Compile observed workflows without freezing their original GPU service time."""

from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from graphlib import CycleError, TopologicalSorter
import math
import time
from typing import Awaitable, Callable, Iterable, Mapping

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.experiments.replay_timing import ReplayIdleClock, ReplayTimingPolicy
from beliefkv.runtime.clock_evidence import local_monotonic_clock_domain
from beliefkv.runtime.replay_token_capture import native_request_expectations


class CausalReplayError(ValueError):
    pass


@dataclass(frozen=True)
class ReplayNode:
    node_id: str
    workflow_id: str
    dependencies: tuple[str, ...]
    delay_ms: float
    event: RuntimeEvent | None = None
    request_id: str | None = None


@dataclass(frozen=True)
class CausalReplayPlan:
    nodes: tuple[ReplayNode, ...]
    request_usage: Mapping[str, tuple[int, int]]
    native_completion_boundaries_verified: bool
    omitted_diagnostic_events: int

    def dependency_graph(self) -> dict[str, tuple[str, ...]]:
        return {node.node_id: node.dependencies for node in self.nodes}

    def summary(self) -> dict:
        counts = Counter(
            node.event.kind.value if node.event else "native_request"
            for node in self.nodes
        )
        return {
            "causal_graph_compiled": True,
            "nodes": len(self.nodes),
            "dependency_edges": sum(len(node.dependencies) for node in self.nodes),
            "event_counts": dict(sorted(counts.items())),
            "workflows": counts[Kind.WORKFLOW_START.value],
            "requests": len(self.request_usage),
            "prompt_tokens": sum(prompt for prompt, _ in self.request_usage.values()),
            "output_tokens": sum(output for _, output in self.request_usage.values()),
            "native_completion_boundaries_verified": self.native_completion_boundaries_verified,
            "omitted_diagnostic_events": self.omitted_diagnostic_events,
            "gpu_replay_qualified": False,
            "performance_accepted": False,
            "scope": (
                "Causal event/request graph and native usage only. Native service "
                "is an unresolved request operation, not its original elapsed time. "
                "Exact token execution, native EOS, session equivalence and GPU "
                "performance require separate qualification."
            ),
        }


def event_node_id(event_id: str) -> str:
    return "event:" + event_id


def request_node_id(request_id: str) -> str:
    return "request:" + request_id


def _identity(value, label: str) -> str:
    if type(value) is not str or not value:
        raise CausalReplayError(f"missing {label}")
    return value


def _tool_key(event: RuntimeEvent) -> tuple[str, str, str]:
    return (
        event.workflow_id, _identity(event.invocation_id, "tool invocation"),
        _identity(
            event.attributes.get("tool_run_id") or event.attributes.get("tool_call_id"),
            "tool run identity",
        ),
    )


def _tool_wait_diagnostic(event: RuntimeEvent) -> bool:
    return (
        event.kind is Kind.TOOL_WAIT_OBSERVATION
        and event.attributes.get("source") == "deepagents_tool_wait_shadow"
    )


def compile_causal_replay(
    events: Iterable[RuntimeEvent], native_events: Iterable[dict],
) -> CausalReplayPlan:
    """Bind recorded dependencies; unknown or incomplete lifecycles fail closed."""
    raw = tuple(events)
    native = tuple(native_events)
    try:
        expected = native_request_expectations(native)
    except (KeyError, TypeError, ValueError) as error:
        raise CausalReplayError(str(error)) from error
    selected = tuple(
        event for event in raw
        if not (
            event.kind is Kind.STRUCTURED_ACTION
            and event.attributes.get("diagnostic_only") is True
            or _tool_wait_diagnostic(event)
        )
    )
    by_id = {}
    starts, endings, creates, terminals = {}, {}, {}, {}
    submits, results, tool_starts, tool_ends = {}, {}, {}, {}
    join_creates, join_waits, join_ends, launches = {}, {}, {}, {}
    supported = {
        Kind.WORKFLOW_START, Kind.WORKFLOW_END, Kind.INVOCATION_CREATE,
        Kind.INVOCATION_CANCEL, Kind.RETURN, Kind.SPAWN, Kind.CALL,
        Kind.JOIN_CREATE, Kind.JOIN_WAIT, Kind.JOIN_SATISFIED,
        Kind.TOOL_START, Kind.TOOL_END, Kind.LLM_SUBMIT, Kind.LLM_RESULT,
        Kind.CONTEXT_ADVANCE, Kind.CONTEXT_COMPACT, Kind.STRUCTURED_ACTION,
    }

    def unique(table, key, event, label):
        if key in table:
            raise CausalReplayError(f"duplicate {label}: {key}")
        table[key] = event

    for event in raw:
        if not math.isfinite(event.ts_ms) or event.ts_ms < 0:
            raise CausalReplayError("invalid event timestamp")
        unique(by_id, event.event_id, event, "event identity")
    for event in selected:
        if event.kind not in supported:
            raise CausalReplayError(f"unsupported causal event: {event.kind.value}")
        owner = event.workflow_id, event.invocation_id
        if event.kind is Kind.WORKFLOW_START:
            unique(starts, event.workflow_id, event, "workflow start")
        elif event.kind is Kind.WORKFLOW_END:
            unique(endings, event.workflow_id, event, "workflow end")
        elif event.kind is Kind.INVOCATION_CREATE:
            _identity(event.invocation_id, "created invocation")
            unique(creates, owner, event, "invocation creation")
        elif event.kind in (Kind.RETURN, Kind.INVOCATION_CANCEL):
            unique(terminals, owner, event, "invocation terminal")
        elif event.kind in (Kind.LLM_SUBMIT, Kind.LLM_RESULT):
            request = _identity(event.attributes.get("request_id"), "request identity")
            unique(submits if event.kind is Kind.LLM_SUBMIT else results,
                   request, event, "client request boundary")
        elif event.kind in (Kind.TOOL_START, Kind.TOOL_END):
            unique(tool_starts if event.kind is Kind.TOOL_START else tool_ends,
                   _tool_key(event), event, "tool boundary")
        elif event.kind in (Kind.SPAWN, Kind.CALL):
            target = event.workflow_id, _identity(event.target_invocation_id, "launch target")
            unique(launches, target, event, "invocation launch")
        elif event.kind in (Kind.JOIN_CREATE, Kind.JOIN_WAIT, Kind.JOIN_SATISFIED):
            join = event.workflow_id, _identity(event.join_id, "join identity")
            table = (
                join_creates if event.kind is Kind.JOIN_CREATE
                else join_waits if event.kind is Kind.JOIN_WAIT else join_ends
            )
            unique(table, join, event, "join boundary")
    if (
        not starts or starts.keys() != endings.keys()
        or creates.keys() != terminals.keys()
        or submits.keys() != results.keys() or submits.keys() != expected.keys()
        or tool_starts.keys() != tool_ends.keys()
        or join_creates.keys() != join_waits.keys()
        or join_creates.keys() != join_ends.keys()
    ):
        raise CausalReplayError("incomplete workflow, invocation, request, tool or JOIN population")
    if any(event.workflow_id not in starts for event in selected):
        raise CausalReplayError("event belongs to an unknown workflow")
    if not launches.keys() <= creates.keys():
        raise CausalReplayError("launch target was not created")

    aliases = defaultdict(list)
    for key, start in tool_starts.items():
        for identifier in {
            start.attributes.get("tool_run_id"), start.attributes.get("tool_call_id"),
        } - {None}:
            aliases[(*key[:2], identifier)].append(key)
    for observation in raw:
        if not _tool_wait_diagnostic(observation):
            continue
        candidates = [
            key for key in aliases.get(_tool_key(observation), ())
            if tool_starts[key].ts_ms <= observation.ts_ms < tool_ends[key].ts_ms
            and all(
                observation.attributes.get(name) is None
                or observation.attributes[name] in {
                    tool_starts[key].attributes.get(name), key[2],
                }
                for name in ("tool_run_id", "tool_call_id")
            )
        ]
        if len(candidates) != 1:
            raise CausalReplayError("trace-only tool observation lacks one active original tool")
        start = tool_starts[candidates[0]]
        elapsed = observation.attributes.get("tool_elapsed_ms")
        if (
            type(elapsed) not in (int, float) or not math.isfinite(elapsed)
            or abs(elapsed - (observation.ts_ms - start.ts_ms)) > 1.
        ):
            raise CausalReplayError("trace-only tool elapsed time disagrees with its start")

    native_results = {
        row["attributes"]["request_id"]: row
        for row in native if row.get("kind") == Kind.LLM_RESULT.value
    }
    reference_times = {event_node_id(event.event_id): event.ts_ms for event in selected}
    clock_domains = set()
    for request, call in expected.items():
        for kind, table in ((Kind.LLM_SUBMIT.value, submits), (Kind.LLM_RESULT.value, results)):
            event = table[request]
            identity = (event.workflow_id, event.invocation_id, event.context_id, event.context_epoch)
            if identity != call[kind][0]:
                raise CausalReplayError("client/native request identity mismatch")
        attributes = native_results[request].get("attributes") or {}
        complete = attributes.get("complete_monotonic_ms")
        domain = attributes.get("monotonic_clock_domain")
        if (
            type(complete) not in (int, float) or not math.isfinite(complete)
            or type(domain) is not str or not domain
        ):
            raise CausalReplayError("native completion lacks its paired monotonic clock")
        if not submits[request].ts_ms <= complete <= results[request].ts_ms + 1.:
            raise CausalReplayError("native completion is outside client request boundaries")
        clock_domains.add(domain)
        reference_times[request_node_id(request)] = complete
    if len(clock_domains) != 1:
        raise CausalReplayError("native completions use different monotonic clock domains")

    nodes, frontier, response_frontier = {}, {}, {}
    active_requests, active_tools, active_joins = {}, defaultdict(set), {}
    closed = set()
    context_tail = {}
    dormant_contexts, reusable_owners = {}, {}
    closed_contexts, workflow_closes = set(), defaultdict(set)
    origin = min(event.ts_ms for event in starts.values())

    def emit(event, dependencies, *, delay=None):
        key = event_node_id(event.event_id)
        dependencies = set(dependencies)
        if key in dependencies or not dependencies <= reference_times.keys():
            raise CausalReplayError("self dependency or unknown causal predecessor")
        elapsed = event.ts_ms - max(
            (reference_times[item] for item in dependencies), default=origin,
        )
        if elapsed < -1.:
            raise CausalReplayError(
                f"event precedes its causal dependency by {-elapsed:.3f}ms: {event.event_id}"
            )
        nodes[key] = ReplayNode(
            key, event.workflow_id, tuple(sorted(dependencies)),
            max(0., elapsed) if delay is None else delay, event=event,
        )
        return key

    def current(owner):
        if owner not in frontier or owner in closed:
            raise CausalReplayError("invocation is unavailable or already terminal")
        return set(frontier[owner])

    def response(owner):
        current(owner)
        return set(response_frontier[owner])

    for event in selected:
        kind, owner = event.kind, (event.workflow_id, event.invocation_id)
        workflow = event_node_id(starts[event.workflow_id].event_id)
        if kind is Kind.WORKFLOW_START:
            emit(event, ())
        elif kind is Kind.INVOCATION_CREATE:
            if event.parent_invocation_id is None:
                dependencies = {workflow}
            else:
                parent = event.workflow_id, event.parent_invocation_id
                dependencies = (
                    current(parent) if event.attributes.get("runtime_internal") is True
                    else response(parent)
                )
                launch = launches.get(owner)
                if launch is None or launch.invocation_id != event.parent_invocation_id:
                    raise CausalReplayError("child creation lacks a matching parent launch")
            if event.attributes.get("reusable_subagent") is True:
                context = event.workflow_id, _identity(event.context_id, "reusable context")
                handle = _identity(event.attributes.get("agent_handle"), "agent handle")
                if context in closed_contexts:
                    raise CausalReplayError("closed reusable context was activated")
                if event.attributes.get("resumed") is True:
                    previous = dormant_contexts.pop(context, None)
                    if (
                        previous is None or previous.attributes.get("agent_handle") != handle
                        or event.context_epoch < previous.context_epoch
                        or creates[(event.workflow_id, previous.invocation_id)].parent_context_id
                        != event.parent_context_id
                    ):
                        raise CausalReplayError("resume lacks a matching dormant context")
                    dependencies.add(event_node_id(previous.event_id))
                elif context in reusable_owners:
                    raise CausalReplayError("reusable context was recreated without resume")
                reusable_owners[context] = owner
            frontier[owner] = {emit(event, dependencies)}
            response_frontier[owner] = set(frontier[owner])
        elif kind is Kind.LLM_SUBMIT:
            dependencies = current(owner)
            if owner in active_requests or active_tools[owner] or owner in active_joins:
                raise CausalReplayError("request submitted while its invocation is blocked")
            request = event.attributes["request_id"]
            if owner in launches:
                dependencies.add(event_node_id(launches[owner].event_id))
            context = event.workflow_id, event.context_id
            previous = context_tail.get(context)
            if previous:
                dependencies.add(event_node_id(results[previous].event_id))
            key = emit(event, dependencies)
            gpu = request_node_id(request)
            nodes[gpu] = ReplayNode(gpu, event.workflow_id, (key,), 0., request_id=request)
            active_requests[owner] = request
            context_tail[context] = request
        elif kind is Kind.LLM_RESULT:
            request = event.attributes["request_id"]
            if active_requests.pop(owner, None) != request:
                raise CausalReplayError("client result does not match its active request")
            frontier[owner] = {emit(event, {request_node_id(request)})}
            response_frontier[owner] = set(frontier[owner])
        elif kind is Kind.TOOL_START:
            key = _tool_key(event)
            if owner in active_requests:
                raise CausalReplayError("tool starts before its model result")
            # Tools and spawn from the same response can run during JOIN_WAIT.
            emit(event, response(owner))
            active_tools[owner].add(key)
        elif kind is Kind.TOOL_END:
            key = _tool_key(event)
            if key not in active_tools[owner]:
                raise CausalReplayError("tool ends before it starts")
            start = tool_starts[key]
            duration = event.ts_ms - start.ts_ms
            declared = event.attributes.get("duration_ms")
            if duration < 0 or declared is not None and (
                type(declared) not in (int, float) or not math.isfinite(declared)
                or abs(declared - duration) > 1.
            ):
                raise CausalReplayError("tool duration and timestamps disagree")
            frontier[owner].add(emit(event, {event_node_id(start.event_id)}))
            active_tools[owner].remove(key)
        elif kind in (Kind.SPAWN, Kind.CALL):
            target = event.workflow_id, event.target_invocation_id
            creation = creates[target]
            if creation.parent_invocation_id != event.invocation_id:
                raise CausalReplayError("launch parent and creation parent disagree")
            dependencies = (
                current(owner) if creation.attributes.get("runtime_internal") is True
                else response(owner)
            )
            emit(event, dependencies | {event_node_id(creation.event_id)})
            if kind is Kind.CALL:
                frontier[owner].add(event_node_id(terminals[target].event_id))
        elif kind is Kind.JOIN_CREATE:
            join = event.workflow_id, event.join_id
            waiter = join_waits[join]
            if event.attributes.get("mode", "all") != "all" or not event.member_invocation_ids:
                raise CausalReplayError("only a nonempty blocking ALL JOIN is supported")
            parent = event.workflow_id, waiter.invocation_id
            dependencies = response(parent)
            for member in event.member_invocation_ids:
                child = event.workflow_id, member
                launch = launches.get(child)
                if launch is None or launch.invocation_id != waiter.invocation_id:
                    raise CausalReplayError("JOIN member does not belong to its waiting parent")
                dependencies.add(event_node_id(launch.event_id))
            emit(event, dependencies)
        elif kind is Kind.JOIN_WAIT:
            join = event.workflow_id, event.join_id
            if owner in active_joins or owner in active_requests:
                raise CausalReplayError("overlapping blocking JOIN or model request")
            emit(event, response(owner) | {event_node_id(join_creates[join].event_id)})
            frontier[owner].add(event_node_id(join_ends[join].event_id))
            active_joins[owner] = join
        elif kind is Kind.JOIN_SATISFIED:
            join = event.workflow_id, event.join_id
            waiter = event.workflow_id, join_waits[join].invocation_id
            if active_joins.pop(waiter, None) != join:
                raise CausalReplayError("JOIN satisfied before its parent waits")
            dependencies = {event_node_id(join_waits[join].event_id)}
            for member in join_creates[join].member_invocation_ids:
                terminal = terminals.get((event.workflow_id, member))
                if terminal is None or terminal.kind is not Kind.RETURN:
                    raise CausalReplayError("ALL JOIN lacks a returned member")
                dependencies.add(event_node_id(terminal.event_id))
            emit(event, dependencies)
        elif kind in (Kind.RETURN, Kind.INVOCATION_CANCEL):
            if owner in active_requests or active_tools[owner] or owner in active_joins:
                raise CausalReplayError("terminal invocation still has unresolved work")
            emit(event, current(owner))
            closed.add(owner)
            if (
                kind is Kind.RETURN and event.attributes.get("reusable_subagent") is True
                and event.attributes.get("context_retired") is False
            ):
                context = event.workflow_id, event.context_id
                if (
                    reusable_owners.get(context) != owner
                    or creates[owner].attributes.get("agent_handle")
                    != event.attributes.get("agent_handle")
                ):
                    raise CausalReplayError("dormant return lacks its reusable activation")
                dormant_contexts[context] = event
        elif kind in (Kind.CONTEXT_ADVANCE, Kind.CONTEXT_COMPACT):
            frontier[owner] = {emit(event, current(owner))}
        elif kind is Kind.STRUCTURED_ACTION:
            if event.attributes.get("subagent_context_close") is True:
                context = event.workflow_id, event.context_id
                previous = dormant_contexts.get(context)
                caller = event.workflow_id, event.attributes.get("caller_invocation_id")
                if (
                    previous is None or previous.invocation_id != event.invocation_id
                    or previous.context_epoch != event.context_epoch
                    or previous.attributes.get("agent_handle") != event.attributes.get("agent_handle")
                    or caller not in creates
                    or creates[caller].context_id != event.attributes.get("caller_context_id")
                    or creates[owner].parent_context_id != creates[caller].context_id
                    or event.attributes.get("request_id") is not None
                ):
                    raise CausalReplayError("close lacks its dormant context or calling parent")
                key = emit(event, current(caller) | {event_node_id(previous.event_id)})
                frontier[caller].add(key)
                workflow_closes[event.workflow_id].add(key)
                closed_contexts.add(context)
                del dormant_contexts[context]
                continue
            dependencies = {
                key for key in current(owner)
                if reference_times[key] <= event.ts_ms + 1.
            }
            request = event.attributes.get("request_id")
            if request is not None:
                if request not in results or (
                    results[request].workflow_id, results[request].invocation_id
                ) != owner:
                    raise CausalReplayError("structured action has a foreign request")
                dependencies = {request_node_id(request)}
            elif owner in active_requests:
                raise CausalReplayError("in-flight action lacks a native token boundary")
            key = emit(event, dependencies)
            if request is None:
                frontier[owner].add(key)
        elif kind is Kind.WORKFLOW_END:
            dependencies = {
                event_node_id(terminal.event_id) for key, terminal in terminals.items()
                if key[0] == event.workflow_id
            }
            emit(event, dependencies | workflow_closes[event.workflow_id] or {workflow})
    if active_requests or any(active_tools.values()) or active_joins:
        raise CausalReplayError("trace ends with active requests, tools or JOINs")
    try:
        tuple(TopologicalSorter({
            key: node.dependencies for key, node in nodes.items()
        }).static_order())
    except CycleError as error:
        raise CausalReplayError("cyclic replay dependencies") from error
    if any(dependency not in nodes for node in nodes.values() for dependency in node.dependencies):
        raise CausalReplayError("replay refers to an uncompiled predecessor")
    return CausalReplayPlan(
        tuple(nodes.values()),
        {request: (call["llm_submit"][1], call["llm_result"][1])
         for request, call in expected.items()},
        True, len(raw) - len(selected),
    )


def evaluate_causal_plan(
    plan: CausalReplayPlan, request_service_ms: Mapping[str, float],
    *, workflow_arrival_offsets_ms: Mapping[str, float] | None = None,
) -> dict:
    """CPU virtual-clock check; supplied service times are never a GPU prediction."""
    if request_service_ms.keys() != plan.request_usage.keys() or any(
        type(value) not in (int, float) or not math.isfinite(value) or value < 0
        for value in request_service_ms.values()
    ):
        raise CausalReplayError("service fixture must cover every request with finite nonnegative time")
    if workflow_arrival_offsets_ms is not None:
        from beliefkv.experiments.replay_arrivals import validate_offsets

        validate_offsets(plan, workflow_arrival_offsets_ms)
    nodes = {node.node_id: node for node in plan.nodes}
    completed, releases, workflows = {}, {}, {}
    for key in TopologicalSorter(plan.dependency_graph()).static_order():
        node = nodes[key]
        ready = max((completed[item] for item in node.dependencies), default=0.)
        ready += node.delay_ms
        if (
            workflow_arrival_offsets_ms is not None and node.event is not None
            and node.event.kind is Kind.WORKFLOW_START
        ):
            ready = workflow_arrival_offsets_ms[node.workflow_id]
        completed[key] = ready + (
            request_service_ms[node.request_id] if node.request_id is not None else 0.
        )
        if node.event:
            if node.event.kind is Kind.WORKFLOW_START:
                releases[node.workflow_id] = completed[key]
            elif node.event.kind is Kind.WORKFLOW_END:
                workflows[node.workflow_id] = completed[key] - releases[node.workflow_id]
    return {
        "node_completed_ms": completed, "workflow_elapsed_ms": workflows,
        "gpu_performance_accepted": False,
        "scope": "CPU dependency consistency with externally supplied fixture service times only",
    }


@dataclass(frozen=True)
class NativeReplayCompletion:
    request_id: str
    complete_monotonic_ms: float
    clock_domain: str
    prompt_tokens: int
    output_tokens: int


async def execute_causal_replay(
    plan: CausalReplayPlan,
    execute_request: Callable[[str, RuntimeEvent], Awaitable[NativeReplayCompletion]],
    emit_event: Callable[[RuntimeEvent], Awaitable[None]],
    *,
    timing_policy: ReplayTimingPolicy | None = None,
    workflow_arrival_offsets_ms: Mapping[str, float] | None = None,
) -> dict:
    """Release only ready work; a backend must report its actual native completion."""
    nodes = {node.node_id: node for node in plan.nodes}
    if len(nodes) != len(plan.nodes) or any(
        dependency not in nodes for node in nodes.values() for dependency in node.dependencies
    ) or any(
        (node.event is None) == (node.request_id is None)
        or not math.isfinite(node.delay_ms) or node.delay_ms < 0
        for node in nodes.values()
    ):
        raise CausalReplayError("invalid replay execution graph")
    if workflow_arrival_offsets_ms is not None:
        from beliefkv.experiments.replay_arrivals import validate_offsets

        validate_offsets(plan, workflow_arrival_offsets_ms)
        if timing_policy is not None and timing_policy != ReplayTimingPolicy():
            raise CausalReplayError("external workflow arrivals cannot use idle timer compression")
    sorter = TopologicalSorter(plan.dependency_graph())
    try:
        sorter.prepare()
    except CycleError as error:
        raise CausalReplayError("cyclic replay execution graph") from error
    clock_domain = local_monotonic_clock_domain()
    if clock_domain is None:
        raise CausalReplayError("replay requires an identified local monotonic clock")
    origin = time.monotonic() * 1000.
    completed, running, dispatched, tool_started, visible_requests = {}, {}, set(), {}, {}
    deadlines = {}
    idle_clock = ReplayIdleClock(
        timing_policy or ReplayTimingPolicy(),
        {key: node.workflow_id for key, node in nodes.items()}, origin,
    )
    workflow_releases, workflow_elapsed = {}, {}
    emitted = 0

    async def execute(node):
        nonlocal emitted
        if node.request_id is not None:
            submitted = time.monotonic() * 1000.
            submit = visible_requests.pop(node.request_id)
            receipt = await execute_request(node.request_id, submit)
            now = time.monotonic() * 1000.
            if (
                not isinstance(receipt, NativeReplayCompletion)
                or receipt.request_id != node.request_id
                or receipt.clock_domain != clock_domain
                or type(receipt.complete_monotonic_ms) not in (int, float)
                or not math.isfinite(receipt.complete_monotonic_ms)
                or not submitted <= receipt.complete_monotonic_ms <= now + 1.
                or type(receipt.prompt_tokens) is not int
                or type(receipt.output_tokens) is not int
                or (receipt.prompt_tokens, receipt.output_tokens) != plan.request_usage[node.request_id]
            ):
                raise CausalReplayError("native replay receipt changes request, clock or work")
            dispatched.add(node.request_id)
            # Transport already spent part of the original delivery allowance.
            return receipt.complete_monotonic_ms
        event = node.event
        now = time.monotonic() * 1000.
        attributes = {
            name: value for name, value in event.attributes.items()
            if name not in {
                "llm_end_callback_entry_ts_ms", "stream_final_chunk_ts_ms",
                "stream_first_chunk_ts_ms",
            }
        }
        event_ts = now
        if event.kind is Kind.WORKFLOW_START and workflow_arrival_offsets_ms is not None:
            event_ts = origin + workflow_arrival_offsets_ms[event.workflow_id]
            attributes.update(
                scheduled_arrival_monotonic_ms=event_ts,
                arrival_delivery_monotonic_ms=now,
                arrival_delivery_lag_ms=now - event_ts,
            )
        if event.kind is Kind.TOOL_START:
            tool_started[_tool_key(event)] = now
        elif event.kind is Kind.TOOL_END:
            attributes["duration_ms"] = now - tool_started.pop(_tool_key(event))
        replayed = replace(event, ts_ms=event_ts, attributes=attributes)
        await emit_event(replayed)
        if event.kind is Kind.LLM_SUBMIT:
            visible_requests[event.attributes["request_id"]] = replayed
        emitted += 1
        if event.kind is Kind.WORKFLOW_START:
            workflow_releases[event.workflow_id] = event_ts
        elif event.kind is Kind.WORKFLOW_END:
            workflow_elapsed[event.workflow_id] = now - workflow_releases[event.workflow_id]
        return time.monotonic() * 1000.

    try:
        while sorter.is_active():
            for key in sorter.get_ready():
                node = nodes[key]
                deadlines[key] = max(
                    (completed[item] for item in node.dependencies), default=origin,
                ) + node.delay_ms
                if (
                    workflow_arrival_offsets_ms is not None and node.event is not None
                    and node.event.kind is Kind.WORKFLOW_START
                ):
                    deadlines[key] = origin + workflow_arrival_offsets_ms[node.workflow_id]
            now = time.monotonic() * 1000.
            busy = {nodes[key].workflow_id for key in running.values()}
            idle_clock.adjust(deadlines, busy, now)
            for key in tuple(key for key, deadline in deadlines.items() if deadline <= now):
                node = nodes[key]
                del deadlines[key]
                if node.request_id is not None:
                    idle_clock.request_started(node.workflow_id)
                task = asyncio.create_task(execute(node), name="causal_replay:" + key)
                running[task] = key
            if not running and not deadlines:
                raise CausalReplayError("replay has no runnable work")
            busy = {nodes[key].workflow_id for key in running.values()}
            timeout = (
                idle_clock.next_wakeup_ms(deadlines, busy, now) / 1000.
                if deadlines else None
            )
            if not running:
                idle_started = time.monotonic() * 1000.
                await asyncio.sleep(timeout)
                if timeout > 0:
                    idle_clock.observe_timer_idle_wait(
                        idle_started, time.monotonic() * 1000.,
                    )
                continue
            done, _ = await asyncio.wait(
                running, timeout=timeout, return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                key = running.pop(task)
                completed[key] = task.result()
                if nodes[key].request_id is not None:
                    idle_clock.request_finished(nodes[key].workflow_id, time.monotonic() * 1000.)
                sorter.done(key)
    finally:
        pending = tuple(running)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    if dispatched != set(plan.request_usage):
        raise CausalReplayError("replay request population is incomplete")
    timing = idle_clock.summary()
    timing["replay_wall_time_ms"] = time.monotonic() * 1000. - origin
    timing["no_inflight_external_timer_wait_fraction"] = (
        timing["no_inflight_external_timer_wait_ms"] / timing["replay_wall_time_ms"]
        if timing["replay_wall_time_ms"] > 0 else 0.
    )
    result = {
        "causal_execution_completed": True,
        "completed_requests": len(dispatched),
        "emitted_events": emitted,
        "workflow_elapsed_ms": workflow_elapsed,
        "replay_timing": timing,
        "exact_tokens_verified": False,
        "gpu_replay_qualified": False,
        "performance_accepted": False,
        "scope": (
            "Causal backend execution and native receipt usage only. This driver "
            "does not establish token/EOS/session fidelity or GPU performance."
        ),
    }
    if workflow_arrival_offsets_ms is not None:
        result.update(
            workflow_arrival_origin_monotonic_ms=origin,
            workflow_arrival_clock_domain=clock_domain,
        )
    return result
