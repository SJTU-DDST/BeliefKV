"""Translate the pinned OpenCode server and provider evidence into a causal trace."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from graphlib import CycleError, TopologicalSorter
import hashlib
import json
import math
import re
from typing import Iterable

from beliefkv.core.events import (
    ContextMode, EventConfidence, ExecutionMode, RelationType, RuntimeEvent,
    RuntimeEventKind as Kind,
)
from beliefkv.experiments.causal_replay import (
    CausalReplayError, ReplayNode, compile_causal_replay, event_node_id, output_boundary_node_id,
    request_node_id,
)
from beliefkv.predictor.taxonomy import ToolTaxonomy
from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata


SOURCE = "opencode_serve"
_TASK_NOTIFICATION = re.compile(r'^<task id="(ses_[A-Za-z0-9]+)" state="(completed|error)">\n')
_NONTERMINAL_ASSISTANT_FINISHES = {None, "unknown", "tool-calls"}
_TOOL_TAXONOMY = ToolTaxonomy({"apply_patch": "file", "webfetch": "browser", "websearch": "search"})


class OpenCodeCaptureError(ValueError):
    pass


def sse(row, event_type):
    event = row.get("event") if row["kind"] == "opencode_sse" else None
    return event["properties"] if event and event["type"] == event_type else None


def notification(part):
    if part.get("type") == "text" and part.get("synthetic") is True:
        match = _TASK_NOTIFICATION.match(part["text"])
        if match:
            return match.groups()
    return None


def notification_hash(text):
    if _TASK_NOTIFICATION.match(text):
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
    return None


def request_metadata(root, activation, epoch, row):
    """Use the Task activation for execution and the OpenCode session for context."""
    session_id = activation.session["id"]
    parent = activation.parent
    return BeliefKVRequestMetadata(
        root_workflow_id=f"opencode:{root}",
        invocation_id=activation.invocation_id,
        context_id=f"opencode-context:{session_id}", context_epoch=epoch,
        agent_definition_id=activation.session["agent"], agent_instance_id=session_id,
        parent_invocation_id=parent.invocation_id if parent else None,
        parent_context_id=f"opencode-context:{parent.session['id']}" if parent else None,
        return_target_id=parent.invocation_id if parent else None,
        relation_type="spawn" if parent else "root",
        context_mode="fresh" if epoch == 0 else "resume",
        execution_mode="background" if activation.is_background(row) else "foreground",
    )


def task_binding(part):
    if part["tool"] != "task":
        return None
    metadata = part["state"].get("metadata") or {}
    child = metadata.get("sessionId")
    if child is None:
        return None
    if metadata.get("parentSessionId") != part["sessionID"]:
        raise OpenCodeCaptureError("Task metadata changes its parent session")
    return child, metadata.get("background") is True


@dataclass(eq=False)
class _Activation:
    session: dict
    row: dict
    invocation_id: str
    parent: _Activation | None = None
    tool_id: str | None = None
    resumed: bool = False
    background: bool = False
    promoted: dict | None = None
    done: dict | None = None
    idle: dict | None = None
    busy: dict | None = None
    settled: dict | None = None
    notified: bool = False

    def is_background(self, row):
        return self.background or (
            self.promoted is not None and self.promoted["ts_ms"] <= row["ts_ms"]
        )

    def terminal_row(self):
        if self.done is None or self.idle is None or (
            self.busy is not None and self.busy["ts_ms"] > self.idle["ts_ms"]
        ):
            return None
        return max(
            (self.done, self.idle, self.settled or self.done), key=lambda row: row["ts_ms"],
        )


class OpenCodeActivations:
    """Resolve the same ordered session/Task evidence at dispatch and at export."""

    def __init__(self):
        self.root = None
        self.sessions = {}
        self.by_session = defaultdict(list)
        self.by_tool = {}
        self.overlapping_tools = set()
        self.attempt_owners = {}
        self.assistant_owners = {}

    def current(self, session):
        history = self.by_session.get(session)
        return history[-1] if history else None

    def at(self, session, row):
        return next((
            activation for activation in reversed(self.by_session.get(session, ()))
            if activation.row["ts_ms"] <= row["ts_ms"]
        ), None)

    def observe(self, row):
        if row["kind"] == "root_session":
            info = row["session"]
            self.root = info["id"]
            self.sessions[self.root] = info
            self.by_session[self.root].append(
                _Activation(info, row, f"opencode-invocation:{self.root}"),
            )
        elif props := sse(row, "session.created"):
            info = props["info"]
            if info["id"] != self.root:
                self.sessions[info["id"]] = info
        elif props := sse(row, "message.part.updated"):
            part = props["part"]
            binding = task_binding(part) if part["type"] == "tool" else None
            if binding:
                self._bind_tool(row, part, *binding)
        elif props := sse(row, "message.updated"):
            info = props["info"]
            activation = self.current(info["sessionID"])
            if activation is None or info["role"] != "assistant":
                return
            owner = self.assistant_owners.setdefault(info["id"], activation)
            if owner is activation and (
                info["time"].get("completed") is not None
                and (info.get("error") or info.get("finish") not in _NONTERMINAL_ASSISTANT_FINISHES)
                and (activation.done is None or (
                    info["id"] != activation.done["event"]["properties"]["info"]["id"]
                ))
            ):
                activation.done = row
        elif props := sse(row, "session.status"):
            activation = self.current(props["sessionID"])
            if activation is not None and props["status"]["type"] in {"idle", "busy"}:
                setattr(activation, props["status"]["type"], row)
        elif row["kind"] == "provider_request":
            activation = self.current(row["session_id"])
            if activation is None or activation.session.get("parentID") != row["parent_session_id"]:
                raise OpenCodeCaptureError("provider session/parent headers disagree with Task binding")
            self.attempt_owners[row["request_id"]] = activation
            activation.settled = None
            activation.idle = None
        elif row["kind"] in {"provider_done", "provider_incomplete"}:
            activation = self.attempt_owners.get(row["request_id"])
            if activation is not None:
                activation.settled = row

    def _bind_tool(self, row, part, child, background):
        tool_id = part["id"]
        if tool_id in self.overlapping_tools:
            return
        if binding := self.by_tool.get(tool_id):
            if binding.session["id"] != child:
                raise OpenCodeCaptureError("Task changed its child identity")
            if background and not binding.background and binding.promoted is None:
                binding.promoted = row
            return
        session = self.sessions.get(child)
        parent = self.current(part["sessionID"])
        if session is None or parent is None or session.get("parentID") != part["sessionID"]:
            raise OpenCodeCaptureError("Task child disagrees with session.created parentID")
        previous = self.current(child)
        reused = (part["state"].get("input") or {}).get("task_id") == child
        if bool(previous) != reused:
            raise OpenCodeCaptureError("Task session reuse lacks a matching task_id")
        if previous is not None and previous.terminal_row() is None:
            self.overlapping_tools.add(tool_id)
            return
        invocation = f"opencode-invocation:{child}" + (f":{tool_id}" if reused else "")
        activation = _Activation(session, row, invocation, parent, tool_id, reused, background)
        self.by_session[child].append(activation)
        self.by_tool[tool_id] = activation


@dataclass
class _Tool:
    start: dict | None = None
    start_part: dict | None = None
    end: dict | None = None
    end_part: dict | None = None
    def see(self, row, part):
        status = part["state"]["status"]
        if status == "running" and self.start is None:
            self.start, self.start_part = row, part
        elif status in {"completed", "error"} and self.end is None:
            self.end, self.end_part = row, part


def _usage_count(value):
    return value if type(value) is int and value >= 0 else None


def _arguments_digest(arguments):
    if not isinstance(arguments, dict):
        return None
    wire = json.dumps(arguments, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(wire.encode("ascii")).hexdigest()


def _published_tool_calls(rows, attempts, settlements):
    """Use the first frame containing the complete matching JSON input."""
    partial, published = {}, {}
    for row in rows:
        if row["kind"] != "provider_tool_delta":
            continue
        rid = row["request_id"]
        if rid not in attempts or not attempts[rid]["ts_ms"] <= row["ts_ms"] <= settlements[rid]["ts_ms"]:
            raise OpenCodeCaptureError("provider tool frame is outside its request")
        for call in row["calls"]:
            entry = partial.setdefault((rid, call["index"]), {"arguments": ""})
            function = call.get("function") or {}
            for field, value in (("id", call.get("id")), ("name", function.get("name"))):
                if value is not None:
                    if entry.setdefault(field, value) != value:
                        raise OpenCodeCaptureError("provider tool delta changes its call identity")
            entry["arguments"] += function.get("arguments") or ""
            if "id" not in entry or "name" not in entry:
                continue
            try:
                digest = _arguments_digest(json.loads(entry["arguments"]))
            except ValueError:
                continue
            if digest:
                key = rid, entry["id"], entry["name"], digest
                published.setdefault(key, row)
    return published


@dataclass(frozen=True)
class OpenCodeCapture:
    events: tuple[RuntimeEvent, ...]
    provider_boundaries: tuple[dict, ...]


def build_opencode_capture(rows: Iterable[dict], clock_domain: str) -> OpenCodeCapture:
    """Build event edges using session, Task part, and provider request identities."""
    rows = tuple(rows)
    if (
        not clock_domain or not rows or any(not math.isfinite(row["ts_ms"]) for row in rows)
        or any(a["ts_ms"] > b["ts_ms"] for a, b in zip(rows, rows[1:]))
    ):
        raise OpenCodeCaptureError("protocol rows require an ordered monotonic clock")
    roots = [row for row in rows if row["kind"] == "root_session"]
    ends = [row for row in rows if row["kind"] == "capture_end"]
    if len(roots) != 1 or len(ends) != 1 or ends[0]["session_id"] != roots[0]["session"]["id"]:
        raise OpenCodeCaptureError("expected one drained OpenCode root")
    root = roots[0]["session"]["id"]
    identities = OpenCodeActivations()
    attempts, settlements, tools, assistants, notifications = {}, {}, {}, {}, {}
    for row in rows:
        if row["kind"] in {"event_stream_error", "provider_error"}:
            raise OpenCodeCaptureError(f"protocol error: {row['kind']}")
        identities.observe(row)
        if props := sse(row, "message.updated"):
            info = props["info"]
            if info["role"] == "assistant":
                assistants[info["id"]] = info
        if props := sse(row, "message.part.updated"):
            part = props["part"]
            if part["type"] == "tool":
                tools.setdefault(part["id"], _Tool()).see(row, part)
            elif notification(part):
                notifications.setdefault(part["id"], (row, part))
        table = (
            attempts if row["kind"] == "provider_request" else settlements
            if row["kind"] in {"provider_done", "provider_incomplete"} else None
        )
        if table is not None:
            if row["request_id"] in table:
                raise OpenCodeCaptureError("duplicate provider request or settlement")
            table[row["request_id"]] = row
    if identities.overlapping_tools:
        raise OpenCodeCaptureError("a Task extended an active session without a separate activation")
    if identities.sessions.keys() != identities.by_session.keys():
        raise OpenCodeCaptureError("OpenCode child session has no Task binding")
    if not attempts or attempts.keys() != settlements.keys():
        raise OpenCodeCaptureError("incomplete provider attempt population")
    published = _published_tool_calls(rows, attempts, settlements)

    events = []
    epochs = defaultdict(int)
    first_epoch, last_epoch, request_epochs = {}, {}, {}
    for rid in attempts:
        activation = identities.attempt_owners[rid]
        session = activation.session["id"]
        epoch = epochs[session]
        epochs[session] += 1
        request_epochs[rid] = last_epoch[activation] = epoch
        first_epoch.setdefault(activation, epoch)
    root_activation = identities.current(root)
    activations = [root_activation, *identities.by_tool.values()]
    if any(activation not in first_epoch for activation in activations):
        raise OpenCodeCaptureError("Task or root activation has no observed provider request")

    def emit(kind, row, activation=None, *, epoch=None, attributes=None,
             confidence=EventConfidence.OBSERVED_EXACT, **fields):
        bits = {}
        session = activation.session["id"] if activation is not None else None
        if activation is not None:
            identity = request_metadata(root, activation, epoch or 0, row)
            bits = dict(
                invocation_id=identity.invocation_id, context_id=identity.context_id,
                parent_invocation_id=identity.parent_invocation_id,
                parent_context_id=identity.parent_context_id, return_target_id=identity.return_target_id,
                agent_definition_id=identity.agent_definition_id, agent_instance_id=identity.agent_instance_id,
                relation_type=RelationType(identity.relation_type),
                execution_mode=ExecutionMode(identity.execution_mode), context_epoch=epoch,
            )
        bits.update(fields)
        event = RuntimeEvent(
            event_id=f"opencode:{root}:{len(events) + 1}", ts_ms=row["ts_ms"], kind=kind,
            workflow_id=f"opencode:{root}", confidence=confidence,
            attributes={
                "source": SOURCE, **({"opencode_session_id": session} if session else {}),
                **({"opencode_event_id": row["event"]["id"]} if row["kind"] == "opencode_sse" else {}),
                **(attributes or {}),
            },
            **bits,
        )
        events.append(event)
        return event

    emit(Kind.WORKFLOW_START, roots[0], attributes={"opencode_root_session_id": root})
    emit(Kind.INVOCATION_CREATE, roots[0], root_activation, epoch=0, context_mode=ContextMode.FRESH)
    for child in identities.by_tool.values():
        attributes = {
            "tool_run_id": child.tool_id, "background": child.background,
            "reusable_subagent": True, "agent_handle": child.session["id"], "resumed": child.resumed,
        }
        emit(
            Kind.INVOCATION_CREATE, child.row, child, epoch=first_epoch[child],
            context_mode=ContextMode.RESUME if child.resumed else ContextMode.FRESH,
            attributes=attributes,
        )
        emit(
            Kind.SPAWN, child.row, child.parent, target_invocation_id=child.invocation_id,
            target_context_id=f"opencode-context:{child.session['id']}",
            execution_mode=ExecutionMode.BACKGROUND if child.background else ExecutionMode.FOREGROUND,
            attributes={**attributes, "tool_manages_foreground_wait": True},
        )

    provider_boundaries = []
    producers = defaultdict(list)
    active_attempts = defaultdict(list)
    for rid, request in attempts.items():
        session = request["session_id"]
        activation = identities.attempt_owners[rid]
        epoch = request_epochs[rid]
        receipt = settlements[rid]
        if receipt["ts_ms"] < request["ts_ms"]:
            raise OpenCodeCaptureError("provider settlement precedes its request")
        expected = request_metadata(root, activation, epoch, request).to_wire()
        if request.get("native_metadata") is not None and request["native_metadata"] != expected:
            raise OpenCodeCaptureError("forwarded native identity disagrees with captured session")
        attrs = {
            "request_id": rid, "opencode_user_message_id": request["user_message_id"],
            "model": request["model"], "clock_boundary": "loopback_provider_bridge",
            "opencode_task_result_inputs": request.get("task_result_inputs", []),
            "provider_arrival_monotonic_ms": request.get("arrival_monotonic_ms"),
        }
        usage = receipt.get("usage") or {}
        prompt = _usage_count(usage.get("prompt_tokens"))
        output = _usage_count(usage.get("completion_tokens"))
        cached = _usage_count((usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0))
        submit = emit(
            Kind.LLM_SUBMIT, request, activation, epoch=epoch, context_mode=ContextMode(expected["context_mode"]),
            attributes={**attrs, "prompt_tokens": prompt, "cache_hit_tokens": cached},
        )
        active_attempts[activation].append((request, receipt))
        if receipt["kind"] == "provider_incomplete":
            emit(
                Kind.CALL_CENSORED, receipt, activation, epoch=epoch,
                attributes={
                    **attrs, "outcome": "stream_incomplete", "provider_usage_available": False,
                    "transport_reason": receipt.get("reason"),
                },
            )
            continue
        for call_id in receipt.get("tool_call_ids", ()):
            producers[(session, call_id)].append(request)
        result = emit(
            Kind.LLM_RESULT, receipt, activation, epoch=epoch,
            attributes={
                **attrs, "output_tokens": output, "provider_response_id": receipt.get("response_id"),
                "provider_finish_reason": receipt.get("finish_reason"),
                "provider_tool_call_ids": receipt.get("tool_call_ids", []),
            },
        )
        if prompt is not None and output is not None and receipt.get("finish_reason"):
            for boundary in (submit, result):
                record = boundary.to_dict()
                if boundary is result:
                    record["attributes"].update(
                        complete_monotonic_ms=result.ts_ms, monotonic_clock_domain=clock_domain,
                        completion_source="provider_done_at_bridge",
                    )
                provider_boundaries.append(record)

    tool_ends = {}
    tool_receipts = {}
    for tool_id, tool in tools.items():
        if tool.start is None:
            if tool.end:
                part = tool.end_part
                emit(Kind.STRUCTURED_ACTION, tool.end, identities.at(part["sessionID"], tool.end), attributes={
                    "diagnostic_only": True, "tool_run_id": tool_id,
                    "opencode_call_id": part["callID"], "outcome": "unobserved_tool_start",
                })
            continue
        if tool.end is None:
            raise OpenCodeCaptureError("running tool lacks a terminal state")
        part = tool.start_part
        end_state = tool.end_part["state"]
        session = part["sessionID"]
        activation = identities.at(session, tool.start)
        if activation is None or identities.at(session, tool.end) is not activation:
            raise OpenCodeCaptureError("tool part straddles two Task activations")
        if (part["sessionID"], part["messageID"], part["callID"], part["tool"]) != (
            tool.end_part["sessionID"], tool.end_part["messageID"], tool.end_part["callID"], tool.end_part["tool"],
        ):
            raise OpenCodeCaptureError("tool part changes identity")
        tool_input = part["state"].get("input") or {}
        command = tool_input.get("command") if part["tool"] == "bash" else None
        normalized = _TOOL_TAXONOMY.normalize(
            part["tool"], command if isinstance(command, str) else None,
        )
        attrs = {
            "tool_run_id": tool_id, "tool_call_id": tool_id, "opencode_call_id": part["callID"],
            "opencode_assistant_message_id": part["messageID"], "tool_name": part["tool"],
            "tool_family": normalized.family, "backend_class": normalized.backend_class,
        }
        candidates = [
            request for request in producers[(session, part["callID"])]
            if request["ts_ms"] <= tool.start["ts_ms"]
            and identities.attempt_owners[request["request_id"]] is activation
            and request["user_message_id"] == assistants.get(part["messageID"], {}).get("parentID")
        ]
        inflight = [
            (request, receipt) for request, receipt in active_attempts[activation]
            if request["ts_ms"] <= tool.start["ts_ms"] < receipt["ts_ms"]
        ]
        if inflight:
            digest = _arguments_digest(tool_input)
            matched = [
                (request, receipt, frame)
                for request, receipt in inflight
                if request["user_message_id"] == assistants.get(part["messageID"], {}).get("parentID")
                and (frame := published.get((request["request_id"], part["callID"], part["tool"], digest)))
                and frame["ts_ms"] <= tool.start["ts_ms"]
            ]
            if len(matched) != 1:
                raise OpenCodeCaptureError("streaming tool lacks one published matching input frame")
            request, receipt, frame = matched[0]
            output = _usage_count((frame.get("usage") or {}).get("completion_tokens"))
            final_output = _usage_count((receipt.get("usage") or {}).get("completion_tokens"))
            if not output or final_output is None or output > final_output or receipt["kind"] != "provider_done":
                raise OpenCodeCaptureError("streaming tool lacks native cumulative output evidence")
            attrs["request_id"] = request["request_id"]
            attrs["model_output_boundary"] = {"output_tokens": output, "published_ms": frame["ts_ms"]}
            tool_receipts[tool_id] = receipt
        elif candidates:
            attrs["request_id"] = candidates[-1]["request_id"]
        emit(Kind.TOOL_START, tool.start, activation, attributes=attrs)
        child = identities.by_tool.get(tool_id)
        if child and child.promoted:
            emit(Kind.STRUCTURED_ACTION, child.promoted, activation, attributes={
                **attrs, "diagnostic_only": True, "action": "task_promoted_to_background",
                "opencode_child_session_id": child.session["id"],
            })
        wall_time = end_state["time"]
        task_attrs = {}
        if child:
            task_attrs = {
                "opencode_child_invocation_id": child.invocation_id,
                "task_release": (
                    "promoted" if child.promoted else "background_launch" if child.background else "foreground"
                ),
            }
        tool_ends[tool_id] = emit(
            Kind.TOOL_END, tool.end, activation, attributes={
                **attrs, **task_attrs, "duration_ms": tool.end["ts_ms"] - tool.start["ts_ms"],
                "opencode_wall_start_ms": wall_time["start"], "opencode_wall_end_ms": wall_time["end"],
                "opencode_wall_duration_ms": wall_time["end"] - wall_time["start"],
                "outcome": "cancelled" if (end_state.get("metadata") or {}).get("interrupted") is True
                else end_state["status"],
            },
        )

    terminal_events = {}
    for activation in activations:
        row = activation.terminal_row()
        if row is None:
            raise OpenCodeCaptureError("session lacks a terminal assistant/idle observation")
        info = activation.done["event"]["properties"]["info"]
        error = (info.get("error") or {}).get("name")
        cancelled = error == "MessageAbortedError"
        terminal_events[activation] = emit(
            Kind.INVOCATION_CANCEL if cancelled else Kind.RETURN, row, activation, epoch=last_epoch[activation],
            attributes={
                "outcome": "cancelled" if cancelled else "error" if error else "completed",
                "opencode_error": error, "opencode_assistant_message_id": info["id"],
                "opencode_assistant_finish": info.get("finish"),
                "opencode_assistant_event_id": activation.done["event"]["id"],
                "opencode_idle_event_id": activation.idle["event"]["id"],
                **({
                    "reusable_subagent": True, "agent_handle": activation.session["id"],
                    "context_retired": cancelled,
                } if activation.parent is not None else {}),
            },
        )

    groups = defaultdict(list)
    for child in identities.by_tool.values():
        tool = tools[child.tool_id]
        if not child.background and not child.promoted:
            groups[(child.parent, tool.start_part["messageID"])].append((child, child.tool_id))
    for (parent, message), members in groups.items():
        start = max(
            [child.row for child, _ in members]
            + [tool_receipts[tool_id] for _, tool_id in members if tool_id in tool_receipts],
            key=lambda row: row["ts_ms"],
        )
        end = max((tools[tool_id].end for _, tool_id in members), key=lambda row: row["ts_ms"])
        if start["ts_ms"] >= end["ts_ms"]:
            continue
        join = f"opencode-task-join:{message}"
        fields = {
            "join_id": join,
            "member_invocation_ids": tuple(child.invocation_id for child, _ in members),
            "confidence": EventConfidence.DECLARED_RUNTIME,
        }
        attrs = {
            "mode": "all", "opencode_assistant_message_id": message,
            "opencode_task_parts": [tool_id for _, tool_id in members],
            "barrier": "foreground_task_returns_before_next_assistant_step",
            "wait_timestamp": (
                "model_stream_closed" if any(tool_receipts.get(part) is start for _, part in members)
                else "last_foreground_task_binding_received"
            ),
            "online_observable": False,
        }
        emit(Kind.JOIN_CREATE, start, parent, attributes=attrs, **fields)
        emit(Kind.JOIN_WAIT, start, parent, attributes=attrs, **fields)
        if all(
            terminal_events[child].kind is Kind.RETURN
            and terminal_events[child].ts_ms <= tool_ends[tool_id].ts_ms
            for child, tool_id in members
        ):
            emit(Kind.JOIN_SATISFIED, end, parent, attributes=attrs, **fields)
        else:
            emit(Kind.STRUCTURED_ACTION, end, parent, attributes={
                **attrs, "diagnostic_only": True, "action": "foreground_barrier_unsettled",
                "opencode_join_id": join,
            })

    for row, part in notifications.values():
        session, result = notification(part)
        parent = identities.at(part["sessionID"], row)
        candidates = [
            child for child in identities.by_session.get(session, ())
            if child.parent is not None and parent is not None
            and child.parent.session["id"] == parent.session["id"] and child.is_background(row)
            and terminal_events[child].ts_ms <= row["ts_ms"] and not child.notified
        ]
        if len(candidates) != 1:
            raise OpenCodeCaptureError("synthetic Task completion lacks one terminal asynchronous activation")
        child = candidates[0]
        child.notified = True
        emit(
            Kind.MESSAGE, row, child, target_invocation_id=parent.invocation_id,
            target_context_id=f"opencode-context:{parent.session['id']}",
            attributes={
                "message_id": part["messageID"], "outcome": result, "opencode_part_id": part["id"],
                "delivery": "synthetic_background_task_result",
                "content_sha256": notification_hash(part["text"]),
            },
        )

    emit(
        Kind.WORKFLOW_END, ends[0],
        attributes={"outcome": terminal_events[root_activation].attributes["outcome"], "opencode_root_session_id": root},
    )
    priority = {
        Kind.WORKFLOW_START: 0, Kind.LLM_RESULT: 1, Kind.TOOL_START: 2, Kind.INVOCATION_CREATE: 3,
        Kind.SPAWN: 4, Kind.JOIN_CREATE: 5, Kind.JOIN_WAIT: 6, Kind.WORKFLOW_END: 8,
    }
    ordered = sorted(events, key=lambda event: (event.ts_ms, priority.get(event.kind, 7)))
    return OpenCodeCapture(tuple(ordered), tuple(provider_boundaries))


def compile_opencode_replay(events: Iterable[RuntimeEvent], completions: Iterable[dict]):
    """Bind Task launches, tool release, and asynchronous result consumption."""
    events, completions = tuple(events), tuple(completions)
    messages = [event for event in events if event.kind is Kind.MESSAGE]
    plan = compile_causal_replay(
        (event for event in events if event.kind is not Kind.MESSAGE), completions,
        allow_streaming_tools=True,
    )
    nodes = {node.node_id: node for node in plan.nodes}
    terminals = {event.invocation_id: event for event in events if event.kind is Kind.RETURN}
    creates = {event.invocation_id: event for event in events if event.kind is Kind.INVOCATION_CREATE}
    starts = {event.attributes["tool_run_id"]: event for event in events if event.kind is Kind.TOOL_START}
    ends = {event.attributes["tool_run_id"]: event for event in events if event.kind is Kind.TOOL_END}
    completion_times = {
        request_node_id(row["attributes"]["request_id"]): row["attributes"]["complete_monotonic_ms"]
        for row in completions if row["kind"] == Kind.LLM_RESULT.value
    }
    for start in starts.values():
        if boundary := start.attributes.get("model_output_boundary"):
            key = output_boundary_node_id(start.attributes["request_id"], boundary["output_tokens"])
            completion_times[key] = min(completion_times.get(key, math.inf), boundary["published_ms"])

    def bind(event, predecessors):
        key = event_node_id(event.event_id)
        deps = tuple(sorted(set(nodes[key].dependencies) | set(predecessors)))
        preceding = max(
            nodes[dep].event.ts_ms if nodes[dep].event else completion_times[dep] for dep in deps
        )
        if preceding > event.ts_ms + 1.:
            raise CausalReplayError("OpenCode dependency follows its consumer")
        nodes[key] = replace(nodes[key], dependencies=deps, delay_ms=max(0., event.ts_ms - preceding))

    for event in events:
        if event.kind is Kind.SPAWN:
            start = starts.get(event.attributes.get("tool_run_id"))
            if start is None or start.invocation_id != event.invocation_id:
                raise CausalReplayError("Task spawn lacks a matching tool start")
            bind(creates[event.target_invocation_id], (event_node_id(start.event_id),))
        elif event.kind is Kind.TOOL_END and event.attributes.get("task_release") == "foreground":
            terminal = terminals.get(event.attributes["opencode_child_invocation_id"])
            if terminal is None:
                raise CausalReplayError("foreground Task result lacks a child return")
            bind(event, (event_node_id(terminal.event_id),))
        elif event.kind is Kind.JOIN_SATISFIED:
            parts = event.attributes["opencode_task_parts"]
            bind(event, (event_node_id(ends[part].event_id) for part in parts))

    assigned = Counter()
    for message in messages:
        child = terminals.get(message.invocation_id)
        digest = message.attributes.get("content_sha256")
        message_key = message.workflow_id, message.target_context_id, digest
        assigned[message_key] += 1
        target = next((
            event for event in events if event.kind is Kind.LLM_SUBMIT
            and event.invocation_id == message.target_invocation_id
            and event.ts_ms >= message.ts_ms
            and event.attributes.get("opencode_task_result_inputs", ()).count(digest) >= assigned[message_key]
        ), None)
        if (
            message.attributes.get("delivery") != "synthetic_background_task_result"
            or child is None or target is None or not child.ts_ms <= message.ts_ms <= target.ts_ms
        ):
            raise CausalReplayError("background notification lacks a timed child return and matching parent request")
        message_node = event_node_id(message.event_id)
        nodes[message_node] = ReplayNode(
            message_node, message.workflow_id, (event_node_id(child.event_id),),
            message.ts_ms - child.ts_ms, event=message,
        )
        bind(target, (message_node,))
    try:
        tuple(TopologicalSorter({key: node.dependencies for key, node in nodes.items()}).static_order())
    except CycleError as error:
        raise CausalReplayError("cyclic OpenCode Task dependencies") from error
    return replace(plan, nodes=tuple(nodes.values()))
