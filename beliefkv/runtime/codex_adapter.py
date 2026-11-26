from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from beliefkv.core.events import (
    ContextMode,
    EventConfidence,
    ExecutionMode,
    RelationType,
    RuntimeEvent,
    RuntimeEventKind,
)
from beliefkv.predictor.taxonomy import ToolTaxonomy
from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata


class RuntimeEventSink(Protocol):
    def emit_batch(self, events: tuple[RuntimeEvent, ...]) -> None:
        ...


@dataclass(frozen=True)
class CodexThreadIdentity:
    thread_id: str
    workflow_id: str
    invocation_id: str
    context_id: str
    parent_thread_id: str | None
    agent_role: str
    agent_nickname: str | None = None


@dataclass(frozen=True)
class CodexRequestIdentity:
    thread_id: str
    turn_id: str
    metadata: BeliefKVRequestMetadata

    @property
    def request_id(self) -> str:
        return f"codex-{self.thread_id}-{self.metadata.context_epoch}"

    @property
    def response_id(self) -> str:
        return f"resp_{self.request_id}"


class CodexThreadRegistry:
    """Thread-safe mapping from Codex thread IDs to BeliefKV identities."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._identities: dict[str, CodexThreadIdentity] = {}
        self._request_epochs: dict[str, int] = {}
        self._joined_children: set[str] = set()
        self._active_child_joins: dict[str, set[str]] = {}

    def register(self, identity: CodexThreadIdentity) -> bool:
        with self._condition:
            previous = self._identities.get(identity.thread_id)
            if previous is not None:
                if previous != identity:
                    raise ValueError(
                        f"conflicting identity for Codex thread {identity.thread_id}"
                    )
                return False
            self._identities[identity.thread_id] = identity
            self._request_epochs[identity.thread_id] = 0
            self._condition.notify_all()
            return True

    def get(self, thread_id: str) -> CodexThreadIdentity | None:
        with self._condition:
            return self._identities.get(thread_id)

    def wait(self, thread_id: str, timeout_s: float = 5.0) -> CodexThreadIdentity:
        deadline = time.monotonic() + timeout_s
        with self._condition:
            while thread_id not in self._identities:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"Codex thread identity was not observed: {thread_id}"
                    )
                self._condition.wait(remaining)
            return self._identities[thread_id]

    def metadata_for_request(
        self, thread_id: str, *, timeout_s: float = 5.0
    ) -> BeliefKVRequestMetadata:
        identity = self.wait(thread_id, timeout_s)
        with self._condition:
            epoch = self._request_epochs[thread_id]
            self._request_epochs[thread_id] = epoch + 1
        parent = (
            self.wait(identity.parent_thread_id, timeout_s)
            if identity.parent_thread_id is not None
            else None
        )
        return BeliefKVRequestMetadata(
            root_workflow_id=identity.workflow_id,
            invocation_id=identity.invocation_id,
            context_id=identity.context_id,
            context_epoch=epoch,
            agent_definition_id=identity.agent_role,
            agent_instance_id=identity.thread_id,
            parent_invocation_id=parent.invocation_id if parent else None,
            parent_context_id=parent.context_id if parent else None,
            relation_type="spawn" if parent else "root",
            context_mode="fork" if parent else ("fresh" if epoch == 0 else "resume"),
            execution_mode="background" if parent else "foreground",
            return_target_id=parent.invocation_id if parent else None,
        )

    def identities(self) -> tuple[CodexThreadIdentity, ...]:
        with self._condition:
            return tuple(self._identities.values())

    def direct_child_ids(self, parent_thread_id: str) -> tuple[str, ...]:
        with self._condition:
            return tuple(
                sorted(
                    identity.thread_id
                    for identity in self._identities.values()
                    if identity.parent_thread_id == parent_thread_id
                )
            )

    def unjoined_child_ids(self, parent_thread_id: str) -> tuple[str, ...]:
        with self._condition:
            return tuple(
                child_id
                for child_id in self.direct_child_ids(parent_thread_id)
                if child_id not in self._joined_children
            )

    def mark_child_joined(self, parent_thread_id: str, child_thread_id: str) -> None:
        with self._condition:
            child = self._identities.get(child_thread_id)
            if child is None or child.parent_thread_id != parent_thread_id:
                raise ValueError(
                    f"{child_thread_id} is not a child of {parent_thread_id}"
                )
            self._joined_children.add(child_thread_id)
            self._condition.notify_all()

    def begin_child_join(
        self, parent_thread_id: str, child_thread_ids: tuple[str, ...]
    ) -> None:
        with self._condition:
            if parent_thread_id in self._active_child_joins:
                raise ValueError(f"child join already active for {parent_thread_id}")
            children = set(child_thread_ids)
            if not children:
                raise ValueError("child join must contain at least one child")
            direct_children = set(self.direct_child_ids(parent_thread_id))
            if not children <= direct_children:
                raise ValueError("child join contains a thread owned by another parent")
            self._active_child_joins[parent_thread_id] = children
            self._condition.notify_all()

    def finish_child_join(self, parent_thread_id: str) -> None:
        with self._condition:
            self._active_child_joins.pop(parent_thread_id, None)
            self._condition.notify_all()

    def child_join_active(self, parent_thread_id: str) -> bool:
        with self._condition:
            return parent_thread_id in self._active_child_joins


class CodexRuntimeAdapter:
    """Translate Codex app-server notifications into authoritative events.

    The adapter consumes only runtime IDs exposed by Codex. It never infers a
    parent/child edge from prompt text or agent names.
    """

    TERMINAL_AGENT_STATES = {"completed", "errored", "shutdown"}

    def __init__(
        self,
        sink: RuntimeEventSink,
        registry: CodexThreadRegistry | None = None,
        *,
        clock_ms: Callable[[], float] | None = None,
    ) -> None:
        self.sink = sink
        self.registry = registry or CodexThreadRegistry()
        self.clock_ms = clock_ms or (lambda: time.monotonic() * 1000.0)
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._sequence = 0
        self._last_ts_ms = 0.0
        self._terminal_threads: set[str] = set()
        self._root_outcomes: dict[str, str] = {}
        self._ended_workflows: set[str] = set()
        self._turns: dict[str, str] = {}
        self._early: dict[str, list[tuple[dict[str, Any], float]]] = {}
        self._active_requests: dict[str, CodexRequestIdentity] = {}
        self._unacknowledged_responses: dict[str, CodexRequestIdentity] = {}
        self._tool_requests: dict[tuple[str, str, str], CodexRequestIdentity] = {}
        self._pending_tool_outputs: dict[str, set[tuple[str, str, str]]] = {}
        self._tool_items: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._join_by_item: dict[
            tuple[str, str, str], tuple[str, tuple[str, ...], float]
        ] = {}
        self._tools: dict[
            tuple[str, str, str], tuple[float, dict[str, Any]]
        ] = {}
        self.capture_errors: list[str] = []

    def register_root(self, thread: dict[str, Any], *, workload_id: str) -> None:
        thread_id = self._required_string(thread, "id")
        if thread.get("parentThreadId") is not None:
            raise ValueError("a root Codex thread cannot have parentThreadId")
        identity = CodexThreadIdentity(
            thread_id=thread_id,
            workflow_id=f"codex:{workload_id}:{thread_id}",
            invocation_id=f"codex-invocation:{thread_id}",
            context_id=f"codex-context:{thread_id}",
            parent_thread_id=None,
            agent_role=str(thread.get("agentRole") or "codex-parent"),
            agent_nickname=thread.get("agentNickname"),
        )
        with self._lock:
            if self.registry.get(thread_id) is not None:
                self.registry.register(identity)  # Check a duplicate for consistency.
                return
            ts_ms = self._timestamp()
            self._emit(
                (
                    self._event(
                        RuntimeEventKind.WORKFLOW_START,
                        identity.workflow_id,
                        ts_ms=ts_ms,
                        attributes={
                            "source": "codex_app_server",
                            "codex_thread_id": thread_id,
                            "workload_id": workload_id,
                        },
                    ),
                    self._invocation_create(identity, ts_ms),
                )
            )
            self._publish_identity(identity)

    def handle_notification(
        self, message: dict[str, Any], *, observed_ts_ms: float | None = None
    ) -> None:
        observed_ts_ms = self.clock_ms() if observed_ts_ms is None else observed_ts_ms
        with self._condition:
            self._handle_notification(message, observed_ts_ms)
            self._condition.notify_all()

    def _handle_notification(self, message: dict[str, Any], observed_ts_ms: float) -> None:
        method = str(message.get("method", ""))
        params = message.get("params")
        if not isinstance(params, dict):
            return
        if method == "thread/started":
            thread = params.get("thread")
            if isinstance(thread, dict) and (parent := thread.get("parentThreadId")):
                if self.registry.get(parent) is None:
                    self._early.setdefault(parent, []).append((message, observed_ts_ms))
                else:
                    self._register_child(thread, observed_ts_ms)
            return
        if method not in {
            "turn/started", "turn/completed", "thread/closed",
            "item/started", "item/completed", "rawResponse/completed",
            "rawResponseItem/completed",
        }:
            return
        thread_id = self._required_string(params, "threadId")
        if self.registry.get(thread_id) is None:
            self._early.setdefault(thread_id, []).append((message, observed_ts_ms))
            return
        if method in {"turn/started", "turn/completed"}:
            turn = params["turn"]
            turn_id = self._required_string(turn, "id")
            self._observe_turn(thread_id, turn_id)
            if method == "turn/completed":
                outcome = self._required_string(turn, "status")
                if outcome not in {"completed", "failed", "interrupted"}:
                    raise ValueError(f"unexpected Codex terminal turn status: {outcome}")
                self._return_thread(
                    thread_id, outcome=outcome, observed_ts_ms=observed_ts_ms,
                    source="turn/completed",
                )
        elif method == "thread/closed":
            self._return_thread(
                thread_id, outcome="shutdown", observed_ts_ms=observed_ts_ms,
                source="thread/closed",
            )
        elif method == "rawResponse/completed":
            response_id = self._required_string(params, "responseId")
            request = self._unacknowledged_responses.pop(response_id, None)
            if request is None or (thread_id, params.get("turnId")) != (
                request.thread_id, request.turn_id
            ):
                self.capture_errors.append(f"unmatched Codex response acknowledgement: {response_id}")
        elif method == "rawResponseItem/completed":
            self._handle_raw_tool_item(params, observed_ts_ms, message.get("emittedAtMs"))
        else:
            self._handle_item(method, params, observed_ts_ms)

    def _publish_identity(self, identity: CodexThreadIdentity) -> None:
        self.registry.register(identity)
        for message, observed in self._early.pop(identity.thread_id, ()):
            self._handle_notification(message, observed)

    def _observe_turn(self, thread_id: str, turn_id: str) -> None:
        if self._turns.setdefault(thread_id, turn_id) != turn_id:
            raise ValueError(f"multiple Codex turns for single-turn capture thread {thread_id}")

    def request_identity(
        self, thread_id: str, turn_id: str, parent_thread_id: str | None
    ) -> CodexRequestIdentity:
        identity = self.registry.wait(thread_id)
        with self._condition:
            if parent_thread_id != identity.parent_thread_id:
                raise ValueError(f"Responses parent disagrees with Codex SPAWN: {thread_id}")
            # The HTTP request and stdio notification travel on separate transports.
            if not self._condition.wait_for(
                lambda: thread_id in self._terminal_threads or (
                    not self._pending_tool_outputs.get(thread_id)
                    and not self.registry.child_join_active(thread_id)
                ),
                timeout=5.0,
            ):
                raise TimeoutError(f"Codex tool output was not observed before reentry: {thread_id}")
            if thread_id in self._terminal_threads or thread_id in self._active_requests:
                raise ValueError(f"unavailable Codex request owner: {thread_id}")
            self._observe_turn(thread_id, turn_id)
            return CodexRequestIdentity(
                thread_id, turn_id, self.registry.metadata_for_request(thread_id)
            )

    def submit_request(self, request: CodexRequestIdentity, **attributes: Any) -> None:
        with self._lock:
            thread_id = request.thread_id
            if thread_id in self._terminal_threads or thread_id in self._active_requests:
                raise ValueError(f"unavailable Codex request owner: {thread_id}")
            self._active_requests[thread_id] = request
            self._emit((self._request_event(RuntimeEventKind.LLM_SUBMIT, request, attributes),))

    def complete_request(
        self, request: CodexRequestIdentity, *, tool_call_ids: tuple[str, ...], **attributes: Any
    ) -> bool:
        with self._lock:
            if self._active_requests.get(request.thread_id) != request:
                return False
            for call_id in tool_call_ids:
                key = (request.thread_id, request.turn_id, call_id)
                if key in self._tool_requests:
                    raise ValueError(f"duplicate Codex tool call identity: {key}")
                self._tool_requests[key] = request
                self._pending_tool_outputs.setdefault(request.thread_id, set()).add(key)
            self._unacknowledged_responses[request.response_id] = request
            self._emit((self._request_event(
                RuntimeEventKind.LLM_RESULT, request,
                {"tool_call_ids": tool_call_ids, **attributes},
            ),))
            del self._active_requests[request.thread_id]
            return True

    def censor_request(self, request: CodexRequestIdentity, *, reason: str) -> None:
        with self._lock:
            if self._active_requests.get(request.thread_id) == request:
                self._emit((self._request_event(
                    RuntimeEventKind.CALL_CENSORED, request, {"reason": reason},
                ),))
                del self._active_requests[request.thread_id]

    def _request_event(
        self, kind: RuntimeEventKind, request: CodexRequestIdentity, attributes: dict[str, Any]
    ) -> RuntimeEvent:
        meta = request.metadata
        return self._event(
            kind, meta.root_workflow_id, ts_ms=self._timestamp(),
            invocation_id=meta.invocation_id, context_id=meta.context_id,
            context_epoch=meta.context_epoch, confidence=EventConfidence.OBSERVED_EXACT,
            attributes={
                "source": "codex_responses_bridge",
                "request_id": request.request_id,
                "response_id": request.response_id,
                "codex_thread_id": request.thread_id,
                "codex_turn_id": request.turn_id,
                **attributes,
            },
        )

    def active_turns(self) -> dict[str, str]:
        with self._lock:
            return {
                thread_id: turn_id for thread_id, turn_id in self._turns.items()
                if thread_id not in self._terminal_threads
            }

    def unfinished_threads(self) -> set[str]:
        with self._lock:
            return {item.thread_id for item in self.registry.identities()} - self._terminal_threads

    def missing_acknowledgements(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._unacknowledged_responses)

    def finish_incomplete_workflows(self, *, outcome: str) -> None:
        """Close roots after app-server failure without fabricating child success."""

        with self._condition:
            for identity in reversed(self.registry.identities()):
                self._return_thread(
                    identity.thread_id, outcome=outcome, observed_ts_ms=self.clock_ms(),
                    source="capture_teardown", confidence=EventConfidence.DECLARED_RUNTIME,
                )
            self._condition.notify_all()

    def _register_child(self, thread: dict[str, Any], observed_ts_ms: float) -> None:
        thread_id = self._required_string(thread, "id")
        parent_thread_id = self._required_string(thread, "parentThreadId")
        self._register_child_identity(
            thread_id,
            parent_thread_id,
            agent_role=str(thread.get("agentRole") or "codex-subagent"),
            agent_nickname=thread.get("agentNickname"),
            source="codex_thread_started",
            observed_ts_ms=observed_ts_ms,
        )

    def _register_child_identity(
        self,
        thread_id: str,
        parent_thread_id: str,
        *,
        agent_role: str,
        agent_nickname: str | None,
        source: str,
        observed_ts_ms: float,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        existing = self.registry.get(thread_id)
        if existing is not None:
            if existing.parent_thread_id != parent_thread_id:
                raise ValueError(f"conflicting parent for Codex thread {thread_id}")
            return
        parent = self.registry.get(parent_thread_id)
        if parent is None or parent_thread_id in self._terminal_threads:
            raise ValueError(f"unavailable Codex spawn parent: {parent_thread_id}")
        identity = CodexThreadIdentity(
            thread_id=thread_id,
            workflow_id=parent.workflow_id,
            invocation_id=f"codex-invocation:{thread_id}",
            context_id=f"codex-context:{thread_id}",
            parent_thread_id=parent_thread_id,
            agent_role=agent_role,
            agent_nickname=agent_nickname,
        )
        ts_ms = self._timestamp(observed_ts_ms)
        self._emit(
            (
                self._invocation_create(identity, ts_ms),
                self._event(
                    RuntimeEventKind.SPAWN,
                    identity.workflow_id,
                    ts_ms=ts_ms,
                    invocation_id=parent.invocation_id,
                    target_invocation_id=identity.invocation_id,
                    context_id=parent.context_id,
                    target_context_id=identity.context_id,
                    execution_mode=ExecutionMode.BACKGROUND,
                    return_target_id=parent.invocation_id,
                    confidence=EventConfidence.OBSERVED_EXACT,
                    attributes={
                        "source": source,
                        "child_thread_id": thread_id,
                        **(attributes or {}),
                    },
                ),
            )
        )
        self._publish_identity(identity)

    def _handle_item(
        self, method: str, params: dict[str, Any], observed_ts_ms: float
    ) -> None:
        item = params.get("item")
        if not isinstance(item, dict):
            return
        thread_id = str(params.get("threadId", ""))
        identity = self.registry.get(thread_id)
        if identity is None or thread_id in self._terminal_threads:
            return
        item_id = self._required_string(item, "id")
        turn_id = str(params.get("turnId") or "")
        if turn_id:
            self._observe_turn(thread_id, turn_id)
        key = (thread_id, turn_id, item_id)
        item_type = item.get("type")
        if item_type == "collabAgentToolCall":
            self._handle_collab_item(method, identity, key, item, params, observed_ts_ms)
        elif item_type in {"commandExecution", "mcpToolCall", "dynamicToolCall"}:
            tool_name = "exec_command" if item_type == "commandExecution" else str(item["tool"])
            self._handle_typed_tool_item(
                method, identity, key, tool_name, item, params, observed_ts_ms
            )

    def _handle_collab_item(
        self,
        method: str,
        identity: CodexThreadIdentity,
        key: tuple[str, str, str],
        item: dict[str, Any],
        params: dict[str, Any],
        observed_ts_ms: float,
    ) -> None:
        tool = str(item.get("tool", ""))
        if item.get("senderThreadId", identity.thread_id) != identity.thread_id:
            raise ValueError(f"Codex collaboration sender disagrees with notification: {key}")
        receiver_ids = tuple(dict.fromkeys(item.get("receiverThreadIds", ())))
        binding = self._item_binding(key)
        if tool != "wait":
            tool_name = {
                "spawnAgent": "spawn_agent",
                "sendInput": "send_input",
                "resumeAgent": "resume_agent",
                "closeAgent": "close_agent",
                "sendMessage": "send_message",
                "followupTask": "followup_task",
                "interruptAgent": "interrupt_agent",
                "listAgents": "list_agents",
            }.get(tool, tool)
            if method == "item/completed" and tool_name == "spawn_agent":
                if item.get("status") == "completed":
                    for receiver_id in receiver_ids:
                        self._register_child_identity(
                            receiver_id, identity.thread_id,
                            agent_role="codex-subagent", agent_nickname=None,
                            source="codex_collab_spawn_completed",
                            observed_ts_ms=observed_ts_ms, attributes=binding,
                        )
            self._handle_typed_tool_item(
                method, identity, key, tool_name, item, params, observed_ts_ms
            )
            if method == "item/completed" and tool_name == "close_agent":
                for child_id, state in (item.get("agentsStates") or {}).items():
                    child = self.registry.get(child_id)
                    if (
                        child is not None and child.parent_thread_id == identity.thread_id
                        and state.get("status") == "shutdown"
                    ):
                        self._return_thread(
                            child_id, outcome="shutdown", observed_ts_ms=observed_ts_ms,
                            source="close_agent",
                        )
            return

        item_id = key[-1]
        if method == "item/started":
            if not receiver_ids or any(
                (child := self.registry.get(receiver_id)) is None
                or child.parent_thread_id != identity.thread_id
                for receiver_id in receiver_ids
            ):
                self.capture_errors.append(f"Codex wait has unknown or foreign members: {key}")
                return
            members = tuple(self.registry.get(child_id).invocation_id for child_id in receiver_ids)
            self.registry.begin_child_join(identity.thread_id, receiver_ids)
            join_id = "codex-join:" + ":".join(key if key[1] else (item_id,))
            self._join_by_item[key] = (join_id, receiver_ids, observed_ts_ms)
            ts_ms = self._timestamp(observed_ts_ms)
            self._emit(
                (
                    self._event(
                        RuntimeEventKind.JOIN_CREATE,
                        identity.workflow_id,
                        ts_ms=ts_ms,
                        invocation_id=identity.invocation_id,
                        context_id=identity.context_id,
                        context_epoch=self._item_epoch(key),
                        join_id=join_id,
                        member_invocation_ids=members,
                        confidence=EventConfidence.OBSERVED_EXACT,
                        attributes={"mode": "any", **binding},
                    ),
                    self._event(
                        RuntimeEventKind.JOIN_WAIT,
                        identity.workflow_id,
                        ts_ms=ts_ms,
                        invocation_id=identity.invocation_id,
                        context_id=identity.context_id,
                        context_epoch=self._item_epoch(key),
                        join_id=join_id,
                        member_invocation_ids=members,
                        confidence=EventConfidence.OBSERVED_EXACT,
                        attributes={
                            **binding,
                            "source_started_at_ms": params.get("startedAtMs"),
                            **self._clock_attributes(ts_ms, observed_ts_ms),
                        },
                    ),
                )
            )
        else:
            join = self._join_by_item.pop(key, None)
            if join is None:
                return
            join_id, joined_threads, started = join
            self.registry.finish_child_join(identity.thread_id)
            states = item.get("agentsStates") or {}
            if set(states) != set(receiver_ids) or not set(receiver_ids) <= set(joined_threads):
                self.capture_errors.append(f"Codex wait result disagrees with membership: {key}")
                return
            completed_members: list[str] = []
            for child_id in joined_threads:
                state = states.get(child_id, {})
                status = state.get("status")
                if status not in self.TERMINAL_AGENT_STATES:
                    continue
                child = self.registry.get(child_id)
                completed_members.append(child.invocation_id)
                self.registry.mark_child_joined(identity.thread_id, child_id)
                self._return_thread(
                    child_id, outcome=status, observed_ts_ms=observed_ts_ms,
                    source="wait_agent", message_present=bool(state.get("message")),
                )
            if not completed_members and (states or item.get("status") != "completed"):
                self.capture_errors.append(f"Codex wait failed without a terminal member: {key}")
                return
            completion_kind = (
                RuntimeEventKind.JOIN_SATISFIED
                if completed_members
                else RuntimeEventKind.JOIN_TIMEOUT
            )
            ts_ms = self._timestamp(observed_ts_ms)
            self._emit(
                (
                    self._event(
                        completion_kind,
                        identity.workflow_id,
                        ts_ms=ts_ms,
                        invocation_id=identity.invocation_id,
                        context_id=identity.context_id,
                        context_epoch=self._item_epoch(key),
                        join_id=join_id,
                        member_invocation_ids=tuple(completed_members),
                        confidence=EventConfidence.OBSERVED_EXACT,
                        attributes={
                            **binding,
                            "status": item.get("status"),
                            "completion_reason": (
                                "terminal_member" if completed_members else "timeout"
                            ),
                            "duration_ms": observed_ts_ms - started,
                            "source_started_at_ms": item.get("startedAtMs"),
                            "source_completed_at_ms": params.get("completedAtMs"),
                            **self._clock_attributes(ts_ms, observed_ts_ms),
                        },
                    ),
                )
            )

    def _item_binding(self, key: tuple[str, str, str]) -> dict[str, Any]:
        request = self._tool_requests.get(key)
        return {
            "codex_thread_id": key[0],
            "codex_turn_id": key[1] or None,
            "codex_item_id": key[2],
            **(
                {"request_id": request.request_id, "response_id": request.response_id}
                if request is not None else {}
            ),
        }

    def _item_epoch(self, key: tuple[str, str, str]) -> int | None:
        request = self._tool_requests.get(key)
        return request.metadata.context_epoch if request is not None else None

    def _handle_typed_tool_item(
        self,
        method: str,
        identity: CodexThreadIdentity,
        key: tuple[str, str, str],
        tool_name: str,
        item: dict[str, Any],
        params: dict[str, Any],
        observed_ts_ms: float,
    ) -> None:
        if key not in self._tool_requests:
            self._handle_tool_item(method, identity, key, tool_name, item, params, observed_ts_ms)
            return
        if key not in self._pending_tool_outputs.get(key[0], ()):
            # A yielded command's process may exit after the tool has returned.
            return
        details = self._tool_items.setdefault(key, {})
        details.update(
            codex_tool_source=item.get("source"),
            codex_process_id=item.get("processId"),
            native_item_status=item.get("status"),
        )
        if method == "item/started":
            details["native_item_started_at_ms"] = params.get("startedAtMs")
        else:
            details.update(
                native_item_completed_at_ms=params.get("completedAtMs"),
                runtime_duration_ms=item.get("durationMs"),
                returncode=item.get("exitCode"),
            )

    def _handle_raw_tool_item(
        self, params: dict[str, Any], observed_ts_ms: float, emitted_at_ms: int | None
    ) -> None:
        item = params["item"]
        item_type = item.get("type")
        if item_type not in {"function_call", "function_call_output"}:
            return
        thread_id = params["threadId"]
        key = (thread_id, params["turnId"], item["call_id"])
        if key not in self._pending_tool_outputs.get(thread_id, ()):
            return
        identity = self.registry.get(thread_id)
        if item_type == "function_call":
            tool_name = item["name"]
            if tool_name == "wait_agent":
                return
            try:
                arguments = json.loads(item["arguments"])
            except (TypeError, ValueError):
                arguments = {}
            command = arguments.get("cmd", "") if isinstance(arguments, dict) else ""
            self._handle_tool_item(
                "item/started", identity, key, tool_name,
                {"command": command, "status": "call_observed", "source": "rawResponseItem/completed"},
                {"startedAtMs": emitted_at_ms}, observed_ts_ms,
            )
        else:
            self._pending_tool_outputs[thread_id].remove(key)
            if key not in self._tools:
                return
            tool_name = self._tools[key][1]["tool_name"]
            self._handle_tool_item(
                "item/completed", identity, key, tool_name,
                {"aggregatedOutput": item.get("output"), "status": "output_observed"},
                {"completedAtMs": emitted_at_ms}, observed_ts_ms,
            )

    def _handle_tool_item(
        self,
        method: str,
        identity: CodexThreadIdentity,
        key: tuple[str, str, str],
        tool_name: str,
        item: dict[str, Any],
        params: dict[str, Any],
        observed_ts_ms: float,
    ) -> None:
        ts_ms = self._timestamp(observed_ts_ms)
        clock = self._clock_attributes(ts_ms, observed_ts_ms)
        common = {
            **self._item_binding(key),
            "tool_call_id": key[-1],
            "tool_run_id": "codex-tool:" + ":".join(key),
            "tool_name": tool_name,
            "status": item.get("status"),
        }
        if method == "item/started":
            if key in self._tools:
                raise ValueError(f"duplicate Codex tool start: {key}")
            command = str(item.get("command", ""))
            normalized = ToolTaxonomy().normalize(tool_name, command)
            source = {
                "source_started_at_ms": params.get("startedAtMs"),
                "codex_tool_source": item.get("source"),
                "codex_process_id": item.get("processId"),
                "tool_family": normalized.family,
                "backend_class": normalized.backend_class,
                "tool_name": tool_name,
            }
            self._tools[key] = (observed_ts_ms, source)
            self._emit(
                (
                    self._event(
                        RuntimeEventKind.TOOL_START,
                        identity.workflow_id,
                        ts_ms=ts_ms,
                        invocation_id=identity.invocation_id,
                        context_id=identity.context_id,
                        context_epoch=self._item_epoch(key),
                        confidence=EventConfidence.OBSERVED_EXACT,
                        attributes={
                            **common,
                            **source,
                            **clock,
                            "command_chars": len(command),
                        },
                    ),
                )
            )
        elif key in self._tools:
            started, source = self._tools.pop(key)
            output = str(item.get("aggregatedOutput") or "")
            self._emit(
                (
                    self._event(
                        RuntimeEventKind.TOOL_END,
                        identity.workflow_id,
                        ts_ms=ts_ms,
                        invocation_id=identity.invocation_id,
                        context_id=identity.context_id,
                        context_epoch=self._item_epoch(key),
                        confidence=EventConfidence.OBSERVED_EXACT,
                        attributes={
                            **common,
                            **source,
                            **clock,
                            "duration_ms": observed_ts_ms - started,
                            "runtime_duration_ms": item.get("durationMs"),
                            "returncode": item.get("exitCode"),
                            "output_chars": len(output),
                            "source_completed_at_ms": params.get("completedAtMs"),
                            **self._tool_items.pop(key, {}),
                        },
                    ),
                )
            )

    def _return_thread(
        self,
        thread_id: str,
        *,
        outcome: str,
        observed_ts_ms: float,
        source: str,
        message_present: bool | None = None,
        confidence: EventConfidence = EventConfidence.OBSERVED_EXACT,
    ) -> None:
        with self._lock:
            if thread_id in self._terminal_threads:
                return
            identity = self.registry.get(thread_id)
            if identity is None:
                return
            active_request = self._active_requests.get(thread_id)
            if active_request is not None:
                self.censor_request(active_request, reason=outcome)
            open_tools = [key for key in self._tools if key[0] == thread_id]
            open_joins = [key for key in self._join_by_item if key[0] == thread_id]
            pending_outputs = self._pending_tool_outputs.pop(thread_id, ())
            for key in pending_outputs:
                self._tool_items.pop(key, None)
            for key in open_tools:
                del self._tools[key]
            for key in open_joins:
                del self._join_by_item[key]
            self.registry.finish_child_join(thread_id)
            if outcome == "completed" and (
                active_request or open_joins or open_tools or pending_outputs
            ):
                self.capture_errors.append(f"Codex turn completed with open work: {thread_id}")
            self._terminal_threads.add(thread_id)
            if identity.parent_thread_id is None:
                self._root_outcomes[identity.workflow_id] = outcome
            ts_ms = self._timestamp(observed_ts_ms)
            events = [
                self._event(
                    RuntimeEventKind.RETURN if outcome == "completed"
                    else RuntimeEventKind.INVOCATION_CANCEL,
                    identity.workflow_id,
                    ts_ms=ts_ms,
                    invocation_id=identity.invocation_id,
                    context_id=identity.context_id,
                    confidence=confidence,
                    attributes={
                        "outcome": outcome,
                        "terminal_source": source,
                        "message_present": message_present,
                        "codex_thread_id": thread_id,
                        "codex_turn_id": self._turns.get(thread_id),
                        "open_tool_call_ids": [key[-1] for key in open_tools],
                        "open_join_item_ids": [key[-1] for key in open_joins],
                    },
                )
            ]
            workflow_threads = {
                item.thread_id
                for item in self.registry.identities()
                if item.workflow_id == identity.workflow_id
            }
            if (
                identity.workflow_id in self._root_outcomes
                and identity.workflow_id not in self._ended_workflows
                and workflow_threads <= self._terminal_threads
            ):
                self._ended_workflows.add(identity.workflow_id)
                events.append(
                    self._event(
                        RuntimeEventKind.WORKFLOW_END,
                        identity.workflow_id,
                        ts_ms=ts_ms,
                        confidence=confidence,
                        attributes={
                            "outcome": self._root_outcomes[identity.workflow_id]
                        },
                    )
                )
            self._emit(tuple(events))

    def _invocation_create(
        self, identity: CodexThreadIdentity, ts_ms: float
    ) -> RuntimeEvent:
        parent = (
            self.registry.get(identity.parent_thread_id)
            if identity.parent_thread_id is not None
            else None
        )
        return self._event(
            RuntimeEventKind.INVOCATION_CREATE,
            identity.workflow_id,
            ts_ms=ts_ms,
            invocation_id=identity.invocation_id,
            context_id=identity.context_id,
            context_epoch=0,
            parent_invocation_id=parent.invocation_id if parent else None,
            parent_context_id=parent.context_id if parent else None,
            agent_definition_id=identity.agent_role,
            agent_instance_id=identity.thread_id,
            relation_type=RelationType.SPAWN if parent else RelationType.ROOT,
            context_mode=ContextMode.FORK if parent else ContextMode.FRESH,
            execution_mode=(
                ExecutionMode.BACKGROUND if parent else ExecutionMode.FOREGROUND
            ),
            return_target_id=parent.invocation_id if parent else None,
            confidence=EventConfidence.OBSERVED_EXACT,
            attributes={
                "persistent": True,
                "source": "codex_app_server",
                "agent_nickname": identity.agent_nickname,
            },
        )

    def _timestamp(self, observed_ts_ms: float | None = None) -> float:
        with self._lock:
            now = self.clock_ms() if observed_ts_ms is None else observed_ts_ms
            self._last_ts_ms = max(self._last_ts_ms, now)
            return self._last_ts_ms

    @staticmethod
    def _clock_attributes(ts_ms: float, observed_ts_ms: float) -> dict[str, float]:
        if ts_ms > observed_ts_ms:
            return {
                "callback_observed_ts_ms": observed_ts_ms,
                "callback_publication_adjustment_ms": ts_ms - observed_ts_ms,
            }
        return {}

    def _event(
        self,
        kind: RuntimeEventKind,
        workflow_id: str,
        *,
        ts_ms: float,
        confidence: EventConfidence = EventConfidence.DECLARED_RUNTIME,
        **kwargs: Any,
    ) -> RuntimeEvent:
        with self._lock:
            self._sequence += 1
            event_id = f"{workflow_id}:runtime:{self._sequence:09d}"
        return RuntimeEvent(
            event_id=event_id,
            ts_ms=ts_ms,
            kind=kind,
            workflow_id=workflow_id,
            confidence=confidence,
            **kwargs,
        )

    def _emit(self, events: tuple[RuntimeEvent, ...]) -> None:
        self.sink.emit_batch(events)

    @staticmethod
    def _required_string(value: dict[str, Any], field: str) -> str:
        result = value.get(field)
        if not isinstance(result, str) or not result:
            raise ValueError(f"Codex payload is missing {field}")
        return result


class MultiplexRuntimeEventSink:
    def __init__(self, *sinks: RuntimeEventSink) -> None:
        self.sinks = sinks

    def emit_batch(self, events: tuple[RuntimeEvent, ...]) -> None:
        for sink in self.sinks:
            sink.emit_batch(events)
