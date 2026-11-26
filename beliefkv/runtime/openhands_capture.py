"""Capture a pinned, local OpenHands Agent conversation as RuntimeEvents."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from graphlib import CycleError, TopologicalSorter
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from beliefkv.core.events import (
    ContextMode, EventConfidence, ExecutionMode, RelationType, RuntimeEvent,
    RuntimeEventKind as Kind,
)
from beliefkv.experiments.causal_replay import (
    CausalReplayError, CausalReplayPlan, compile_causal_replay, event_node_id,
)
from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata

OPENHANDS_VERSION = "1.14.0"
OPENHANDS_COMMIT = "ab83d054ee88a3e19eea2af7fc3149a0d743804f"
MARS_REFERENCE_COMMIT = "d2e34d007ad2013b4a2513efdb9b2f9b606369f6"


def require_openhands() -> dict[str, str]:
    packages = ("openhands-sdk", "openhands-tools")
    try:
        versions = {name: version(name) for name in packages}
    except PackageNotFoundError as error:
        raise RuntimeError("install configs/openhands_capture_requirements.txt") from error
    if any(value != OPENHANDS_VERSION for value in versions.values()):
        raise RuntimeError(f"OpenHands capture requires {OPENHANDS_VERSION}: {versions}")
    return versions


@dataclass(frozen=True)
class _Invocation:
    capture: OpenHandsCapture
    conversation: Any
    metadata: BeliefKVRequestMetadata
    handle: str | None

    def emit(self, kind: Kind, *, attributes=None, **fields) -> RuntimeEvent:
        meta = self.metadata
        identity = {
            "invocation_id": meta.invocation_id,
            "context_id": meta.context_id,
            "context_epoch": meta.context_epoch,
            "parent_invocation_id": meta.parent_invocation_id,
            "parent_context_id": meta.parent_context_id,
            "agent_definition_id": meta.agent_definition_id,
            "agent_instance_id": meta.agent_instance_id,
            "relation_type": RelationType(meta.relation_type),
            "context_mode": ContextMode(meta.context_mode),
            "execution_mode": ExecutionMode(meta.execution_mode),
            "return_target_id": meta.return_target_id,
        }
        return self.capture.emit(kind, **(identity | fields), attributes={
            "sdk_agent_handle": self.handle, **(attributes or {}),
        })

    def finish(self, *, interrupted=False) -> str:
        status = self.conversation.state.execution_status.value
        self.emit(
            Kind.RETURN if status == "finished" and not interrupted else Kind.INVOCATION_CANCEL,
            attributes={"sdk_status": status, "interrupted": interrupted},
        )
        return status


_CURRENT: ContextVar[_Invocation | None] = ContextVar("openhands_invocation", default=None)
_ACTION: ContextVar[Any] = ContextVar("openhands_action", default=None)
_DELEGATION: ContextVar[_Delegation | None] = ContextVar("openhands_delegation", default=None)
# Module hooks exist only while this process owns an OpenHands capture.
_HOOK_LOCK = threading.Lock()


@contextmanager
def _bind(variable, value):
    token = variable.set(value)
    try:
        yield
    finally:
        variable.reset(token)


class OpenHandsCapture:
    def __init__(
        self, workflow_id: str, *, task_id: str | None = None,
        publish: Callable[[RuntimeEvent], None] | None = None,
    ):
        self.workflow_id = workflow_id
        self.task_id = task_id
        self.events: list[RuntimeEvent] = []
        self._publish = publish
        self._event_lock = threading.Lock()
        self._seen_contexts: set[str] = set()
        self._children: list[Any] = []

    def emit(self, kind: Kind, *, attributes=None, tool_started_ms=None, **fields) -> RuntimeEvent:
        with self._event_lock:
            timestamp = time.monotonic_ns() / 1e6
            data = {"source": "openhands_sdk", **(attributes or {})}
            if tool_started_ms is not None:
                data["duration_ms"] = timestamp - tool_started_ms
            event = RuntimeEvent(
                event_id=f"{self.workflow_id}:openhands:{len(self.events) + 1}",
                ts_ms=timestamp, kind=kind, workflow_id=self.workflow_id,
                confidence=EventConfidence.OBSERVED_EXACT, attributes=data, **fields,
            )
            self.events.append(event)
            if self._publish is not None:
                self._publish(event)
            return event

    def _invocation(self, conversation, *, parent=None, handle=None, tool_run_id=None):
        context_id = str(conversation.state.id)
        with self._event_lock:
            mode = "resume" if context_id in self._seen_contexts else "fresh"
            self._seen_contexts.add(context_id)
        meta = BeliefKVRequestMetadata(
            root_workflow_id=self.workflow_id, invocation_id=f"openhands-{uuid4().hex}",
            context_id=context_id, context_epoch=0, context_mode=mode,
            agent_definition_id=type(conversation.agent).__name__,
            agent_instance_id=context_id,
            parent_invocation_id=parent.metadata.invocation_id if parent else None,
            parent_context_id=parent.metadata.context_id if parent else None,
            return_target_id=parent.metadata.invocation_id if parent else None,
            relation_type="spawn" if parent else "root",
            execution_mode="background" if parent else "foreground",
        )
        owner = _Invocation(self, conversation, meta, handle)
        owner.emit(Kind.INVOCATION_CREATE, attributes={"delegate_tool_run_id": tool_run_id})
        if parent:
            parent.emit(
                Kind.SPAWN, target_invocation_id=meta.invocation_id, target_context_id=context_id,
                execution_mode=ExecutionMode.BACKGROUND, relation_type=RelationType.SPAWN,
                return_target_id=parent.metadata.invocation_id,
                attributes={"delegate_tool_run_id": tool_run_id, "sdk_agent_handle": handle},
            )
        return owner

    def run(self, agent: Any, workspace: Path, prompt: str, *, max_iterations: int = 100) -> str:
        """Run one fresh local conversation and retain its actual completion status."""
        require_openhands()
        from openhands.sdk import Agent, Conversation

        if not isinstance(agent, Agent) or self.events:
            raise ValueError("capture requires one fresh OpenHands Agent conversation")
        if agent.llm.uses_responses_api():
            raise ValueError("OpenHands capture requires an OpenAI-compatible chat model")
        with _capture_hooks():
            conversation = Conversation(
                agent=agent, workspace=workspace, callbacks=[_on_sdk_event],
                token_callbacks=[_ignore_token], visualizer=None,
                max_iteration_per_run=max_iterations,
            )
            try:
                self.emit(Kind.WORKFLOW_START, attributes={"task_id": self.task_id})
                root = self._invocation(conversation)
                interrupted = True
                try:
                    with _bind(_CURRENT, root):
                        conversation.send_message(prompt)
                        conversation.run()
                    interrupted = False
                finally:
                    status = root.finish(interrupted=interrupted)
                    self.emit(Kind.WORKFLOW_END, attributes={"sdk_status": status})
                return status
            finally:
                for child in reversed(self._children):
                    child.close()
                self._children.clear()
                conversation.close()


def _ignore_token(_chunk):
    # OpenHands 1.14 requires a callback for streaming chat completions.
    pass


def _on_sdk_event(event):
    from openhands.sdk.event import ActionEvent
    from openhands.sdk.event.llm_convertible.observation import (
        ObservationBaseEvent, ObservationEvent, UserRejectObservation,
    )

    owner = _CURRENT.get()
    if owner is None or not isinstance(event, (ActionEvent, ObservationBaseEvent)):
        return
    attrs = {
        "diagnostic_only": True, "sdk_event_type": type(event).__name__,
        "sdk_event_id": event.id, "sdk_action_id": getattr(event, "action_id", None),
        "tool_name": event.tool_name, "tool_call_id": event.tool_call_id,
        "sdk_llm_response_id": getattr(event, "llm_response_id", None),
    }
    if isinstance(event, ActionEvent):
        attrs.update(sdk_action_id=event.id, sdk_action_validated=event.action is not None)
    elif isinstance(event, ObservationEvent):
        attrs["sdk_observation_is_error"] = event.observation.is_error
    elif isinstance(event, UserRejectObservation):
        attrs["sdk_rejection_source"] = event.rejection_source
    owner.emit(Kind.STRUCTURED_ACTION, attributes=attrs)


@dataclass
class _Delegation:
    parent: _Invocation
    tool_run_id: str
    threads: list[_DelegateThread] = field(default_factory=list)
    joined: int = 0

    def wait_fields(self) -> dict:
        return {
            "join_id": f"{self.parent.metadata.invocation_id}:delegate:{self.tool_run_id}",
            "member_invocation_ids": tuple(
                thread.child.metadata.invocation_id for thread in self.threads
            ),
        }


class _DelegateThread(threading.Thread):
    """Observe the SDK's own Thread.start/run/join calls without changing their order."""

    def __init__(self, delegation, *, target, args, name):
        super().__init__(target=target, args=args, name=name)
        self.delegation = delegation
        self.handle, self.conversation, _task, _parent_name = args

    def start(self):
        batch = self.delegation
        self.child = batch.parent.capture._invocation(
            self.conversation, parent=batch.parent, handle=self.handle,
            tool_run_id=batch.tool_run_id,
        )
        batch.threads.append(self)
        try:
            super().start()
        except BaseException:
            self.child.finish(interrupted=True)
            raise

    def run(self):
        interrupted = True
        try:
            with _bind(_CURRENT, self.child):
                super().run()
            interrupted = False
        finally:
            self.child.finish(interrupted=interrupted)

    def join(self, timeout=None):
        batch = self.delegation
        fields = batch.wait_fields()
        attrs = {"mode": "all", "delegate_tool_run_id": batch.tool_run_id}
        if batch.joined == 0:
            pending = [thread.child.metadata.invocation_id for thread in batch.threads
                       if thread.is_alive()]
            batch.parent.emit(Kind.JOIN_CREATE, attributes=attrs, **fields)
            batch.parent.emit(Kind.JOIN_WAIT, attributes={
                **attrs, "sdk_boundary": "DelegateExecutor._delegate_tasks:Thread.join",
                "live_members_at_entry": pending, "online_observable": True,
            }, **fields)
        super().join(timeout=timeout)
        batch.joined += 1
        if batch.joined == len(batch.threads):
            batch.parent.emit(Kind.JOIN_SATISFIED, attributes={
                **attrs, "sdk_boundary": "DelegateExecutor._delegate_tasks:Thread.join_return",
                "online_observable": True,
            }, **fields)


@contextmanager
def _capture_hooks():
    from openhands.sdk import Agent, LLM
    from openhands.sdk.tool.tool import ToolDefinition
    from openhands.tools.delegate import impl as delegate
    from openhands.tools.delegate.definition import DelegateAction

    def install(stack, obj, attribute, replacement):
        original = getattr(obj, attribute)
        setattr(obj, attribute, replacement)
        stack.callback(setattr, obj, attribute, original)
        return original

    def child_conversation(**kwargs):
        owner = _CURRENT.get()
        if owner is not None:
            kwargs["callbacks"] = [*(kwargs.get("callbacks") or ()), _on_sdk_event]
        child = original_child(**kwargs)
        if owner is not None:
            with owner.capture._event_lock:
                owner.capture._children.append(child)
            action = _ACTION.get()
            owner.emit(Kind.STRUCTURED_ACTION, attributes={
                "diagnostic_only": True,
                "sdk_boundary": "DelegateExecutor._spawn_agents:LocalConversation",
                "sdk_child_context_id": str(child.state.id),
                "tool_run_id": action.id if action is not None else None,
            })
        return child

    def thread_factory(*args, **kwargs):
        delegation = _DELEGATION.get()
        if delegation is None:
            return threading.Thread(*args, **kwargs)
        return _DelegateThread(delegation, *args, **kwargs)

    def dispatch(agent, conversation, action_event, on_event):
        with _bind(_ACTION, action_event):
            return original_dispatch(agent, conversation, action_event, on_event)

    def execute_tool(tool, action, conversation=None):
        owner, sdk_event = _CURRENT.get(), _ACTION.get()
        if owner is None or sdk_event is None or action is not sdk_event.action:
            return original_tool(tool, action, conversation)
        attrs = {
            "tool_run_id": sdk_event.id, "tool_call_id": sdk_event.tool_call_id,
            "tool_name": sdk_event.tool_name, "tool_family": sdk_event.tool_name,
            "sdk_llm_response_id": sdk_event.llm_response_id,
        }
        delegation = None
        if isinstance(action, DelegateAction):
            attrs["sdk_delegate_command"] = action.command
            attrs["sdk_delegate_handles"] = list(action.ids or action.tasks or ())
            if action.command == "delegate":
                delegation = _Delegation(owner, sdk_event.id)
        start = owner.emit(Kind.TOOL_START, attributes=attrs)
        outcome = "exception"
        try:
            with _bind(_DELEGATION, delegation):
                observation = original_tool(tool, action, conversation)
            outcome = "error" if observation.is_error else "ok"
            return observation
        finally:
            owner.emit(
                Kind.TOOL_END, attributes={**attrs, "outcome": outcome},
                tool_started_ms=start.ts_ms,
            )

    def responses(llm, *args, **kwargs):
        if _CURRENT.get() is not None:
            raise ValueError("OpenHands capture requires chat completions for every invocation")
        return original_responses(llm, *args, **kwargs)

    def transport(llm, **kwargs):
        owner = _CURRENT.get()
        if owner is None:
            return original_transport(llm, **kwargs)
        extra_body = dict(kwargs.get("extra_body") or {})
        if {"rid", "beliefkv_metadata"} & extra_body.keys():
            raise ValueError("OpenHands capture owns rid and beliefkv_metadata")
        rid = f"openhands-{uuid4().hex}"
        kwargs["extra_body"] = {
            **extra_body, "rid": rid, "beliefkv_metadata": owner.metadata.to_wire(),
        }
        # A retry at the SDK layer goes through this hook and gets a new rid.
        kwargs["max_retries"] = 0
        streaming = kwargs.get("enable_streaming", False)
        if streaming:
            kwargs["stream_options"] = {**(kwargs.get("stream_options") or {}), "include_usage": True}
        details = {"request_id": rid, "stream": streaming, "model": llm.model}
        owner.emit(Kind.LLM_SUBMIT, attributes=details)
        try:
            response = original_transport(llm, **kwargs)
        except Exception as error:
            owner.emit(Kind.LLM_RESULT, attributes={**details, "error_type": type(error).__name__})
            raise
        usage = getattr(response, "usage", None)
        choices = getattr(response, "choices", ())
        provider_id = getattr(response, "id", None)
        owner.emit(Kind.LLM_RESULT, attributes={
            **details, "provider_response_id": provider_id, "rid_echoed": rid == provider_id,
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "output_tokens": getattr(usage, "completion_tokens", None),
            "provider_finish_reason": getattr(choices[0], "finish_reason", None) if choices else None,
        })
        return response

    with _HOOK_LOCK, ExitStack() as stack:
        original_dispatch = install(stack, Agent, "_execute_action_event", dispatch)
        original_tool = install(stack, ToolDefinition, "__call__", execute_tool)
        original_transport = install(stack, LLM, "_transport_call", transport)
        original_responses = install(stack, LLM, "responses", responses)
        original_child = install(stack, delegate, "LocalConversation", child_conversation)
        install(stack, delegate, "threading", SimpleNamespace(Thread=thread_factory))
        yield


def compile_openhands_capture(
    events: Iterable[RuntimeEvent], native_events: Iterable[dict],
) -> CausalReplayPlan:
    """Bind server receipts and preserve SDK tool order and the delegate barrier."""
    events = tuple(events)
    if any(event.attributes.get("source") != "openhands_sdk" for event in events):
        raise CausalReplayError("expected an OpenHands capture")
    if any(event.kind is Kind.INVOCATION_CANCEL for event in events):
        raise CausalReplayError("OpenHands capture has an interrupted or unfinished invocation")
    plan = compile_causal_replay(events, native_events)
    active_tools, last_tool_end, launches, join_ends, extra_edges = {}, {}, {}, {}, {}
    for event in events:
        attrs = event.attributes
        owner = event.workflow_id, event.invocation_id
        run = (*owner, attrs.get("tool_run_id") or attrs.get("delegate_tool_run_id"))
        if event.kind is Kind.TOOL_START:
            if owner in active_tools:
                raise CausalReplayError("OpenHands Agent tool executions must be serial per invocation")
            active_tools[owner] = event
            if owner in last_tool_end:
                extra_edges[event.event_id] = last_tool_end[owner]
        elif event.kind is Kind.INVOCATION_CREATE and event.parent_invocation_id is not None:
            parent = event.workflow_id, event.parent_invocation_id
            start = active_tools.get(parent)
            if (
                start is None or start.attributes.get("sdk_delegate_command") != "delegate"
                or start.attributes["tool_run_id"] != run[-1]
            ):
                raise CausalReplayError("OpenHands child launch lacks its delegate tool")
            launches.setdefault((*parent, run[-1]), set()).add(event.invocation_id)
            extra_edges[event.event_id] = start
        elif event.kind is Kind.JOIN_SATISFIED:
            if not launches.get(run) or set(event.member_invocation_ids) != launches[run] or run in join_ends:
                raise CausalReplayError("OpenHands join does not match the launched delegate members")
            join_ends[run] = event
        elif event.kind is Kind.TOOL_END:
            active_tools.pop(owner)
            last_tool_end[owner] = event
            if run in join_ends:
                extra_edges[event.event_id] = join_ends[run]
            elif run in launches:
                raise CausalReplayError("OpenHands delegate tool ends without its observed join")

    # The common compiler models an ordinary tool as fixed elapsed time. A
    # delegate end also depends on its actual ALL join, whose service is variable.
    times = {event_node_id(event.event_id): event.ts_ms for event in events}
    nodes = []
    for node in plan.nodes:
        predecessor = extra_edges.get(node.event.event_id) if node.event else None
        if predecessor is not None:
            dependencies = tuple(sorted({*node.dependencies, event_node_id(predecessor.event_id)}))
            delay = node.event.ts_ms - max(times[key] for key in dependencies)
            if delay < 0:
                raise CausalReplayError("out-of-order OpenHands tool boundaries")
            node = replace(node, dependencies=dependencies, delay_ms=delay)
        nodes.append(node)
    bound = replace(plan, nodes=tuple(nodes))
    try:
        tuple(TopologicalSorter(bound.dependency_graph()).static_order())
    except CycleError as error:
        raise CausalReplayError("OpenHands delegate tool dependencies are cyclic") from error
    return bound
