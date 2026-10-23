"""Root-scoped logical subagent sessions, independent of physical KV residency."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re
import threading
from typing import Any, Mapping


class SubagentSessionError(ValueError):
    pass


def _has_unresolved_work(report: str, completion: object) -> bool:
    if hasattr(completion, "model_dump"):
        completion = completion.model_dump(include={"status", "unresolved"})
    if isinstance(completion, Mapping):
        unresolved = completion.get("unresolved")
        if completion.get("status") in {"complete", "blocked"} and isinstance(
            unresolved, (list, tuple),
        ):
            return completion["status"] == "blocked" or bool(unresolved)
    # Unstructured reports are only a fallback signal. Ignore common explicit
    # negations rather than treating a field name or "no blockers" as evidence.
    for phrase in re.split(r"[\n.!?;]+", report):
        for match in re.finditer(
            r"\b(?:unresolved|follow[- ]up|remaining|failing|blocked)\b", phrase,
        ):
            before = phrase[max(0, match.start() - 64):match.start()]
            after = phrase[match.end():match.end() + 64]
            if re.search(r"\b(?:no|not|without|zero|0)\s+(?:\w+\s+){0,2}$", before):
                continue
            if re.match(r"\s*(?:[:=]\s*)?(?:none\b|0\b|\[\]|not\s+(?:needed|required)\b)", after):
                continue
            return True
    return False


def reuse_features(
    description: str, result: str = "", *, reuse_count: int = 0,
    completion: object = None,
) -> dict:
    """Bounded boundary-only features; no future delegation or model inference."""
    scope = description[:8192].casefold()
    report = result[-8192:].casefold()
    words = frozenset(re.findall(r"[a-z_][a-z_0-9./-]{2,63}", scope))
    # Stable hashes keep source/task text out of the server control stream.
    scope_ids = sorted({
        int.from_bytes(hashlib.blake2s(word.encode(), digest_size=4).digest(), "big")
        for word in sorted(words)[:128]
    })
    return {
        "scope_ids": scope_ids,
        "unresolved": _has_unresolved_work(report, completion),
        "implementation": any(word in scope for word in ("implement", "fix", "refactor")),
        "verification": any(word in scope for word in ("test", "review", "verify")),
        "reuse_count": reuse_count,
    }


@dataclass
class SubagentSession:
    handle: str
    context_id: str
    owner_context_id: str
    role: str
    epoch: int = -1
    invocation_id: str | None = None
    active_call: str | None = None
    reuse_count: int = 0
    closed: bool = False
    state: dict[str, Any] = field(default_factory=dict, repr=False)
    features: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SubagentActivation:
    call_id: str
    invocation_id: str
    context_id: str
    handle: str
    role: str
    initial_epoch: int
    resumed: bool
    description_sha256: str


class ReusableSubagentRegistry:
    """One writer per handle; each activation is a new causal invocation.

    The stored graph message list is the source of truth after a physical KV
    miss. No transcript is recovered from (or changed by) a cache decision.
    """

    def __init__(self, workflow_id: str, *, max_sessions: int = 256) -> None:
        if not workflow_id or max_sessions <= 0:
            raise ValueError("invalid reusable session registry")
        self.workflow_id = workflow_id
        self.max_sessions = max_sessions
        self._lock = threading.RLock()
        self._sessions: dict[str, SubagentSession] = {}
        self._activations: dict[str, SubagentActivation] = {}
        self._by_invocation: dict[str, SubagentSession] = {}
        self._claimed: set[str] = set()
        self._ended = False

    def reserve(
        self, *, call_id: str, invocation_id: str, owner_context_id: str,
        role: str, description: str, agent_id: str | None = None,
    ) -> SubagentActivation:
        with self._lock:
            if self._ended or call_id in self._activations:
                raise SubagentSessionError("workflow ended or task call already reserved")
            resumed = agent_id is not None
            if resumed:
                session = self._sessions.get(agent_id)
                if session is None or session.closed:
                    raise SubagentSessionError("unknown or closed agent_id")
                if session.owner_context_id != owner_context_id or session.role != role:
                    raise SubagentSessionError("agent_id belongs to another parent or role")
                if session.active_call is not None:
                    raise SubagentSessionError("agent_id already has an active invocation")
                session.reuse_count += 1
            else:
                if len(self._sessions) >= self.max_sessions:
                    raise SubagentSessionError("workflow subagent session limit reached")
                digest = hashlib.sha256(
                    f"{self.workflow_id}:{owner_context_id}:{call_id}".encode()
                ).hexdigest()[:24]
                namespace = hashlib.sha256(self.workflow_id.encode()).hexdigest()[:8]
                handle = f"agent-{namespace}-{len(self._sessions):04x}"
                session = SubagentSession(
                    handle, f"deepagents-context:reusable:{digest}", owner_context_id, role,
                )
                self._sessions[handle] = session
            activation = SubagentActivation(
                call_id, invocation_id, session.context_id, session.handle, role,
                session.epoch + 1, resumed,
                hashlib.sha256(description.encode()).hexdigest(),
            )
            session.active_call = call_id
            session.invocation_id = invocation_id
            session.features = {
                **reuse_features(description, reuse_count=session.reuse_count),
                "role": role,
            }
            self._activations[call_id] = activation
            self._by_invocation[invocation_id] = session
            return activation

    def activation(self, call_id: str) -> SubagentActivation | None:
        with self._lock:
            return self._activations.get(call_id)

    def claim(
        self, call_id: str, *, role: str, description: str, agent_id: str | None,
    ) -> tuple[SubagentActivation, dict[str, Any]]:
        with self._lock:
            activation = self._activations.get(call_id)
            if (
                activation is None or self._ended or call_id in self._claimed
                or activation.role != role
                or activation.description_sha256 != hashlib.sha256(description.encode()).hexdigest()
                or (agent_id is not None) != activation.resumed
                or activation.resumed and agent_id != activation.handle
            ):
                raise SubagentSessionError("task has no matching reserved activation")
            session = self._sessions[activation.handle]
            if session.closed or session.active_call != call_id:
                raise SubagentSessionError("activation is no longer active")
            self._claimed.add(call_id)
            # Messages are immutable graph values; copy the list, not its text.
            state = dict(session.state)
            state["messages"] = list(state.get("messages", ()))
            return activation, state

    def note_epoch(self, invocation_id: str, epoch: int) -> None:
        with self._lock:
            session = self._by_invocation.get(invocation_id)
            if session is not None and session.invocation_id == invocation_id and session.active_call:
                session.epoch = max(session.epoch, epoch)

    def complete(self, call_id: str, result: Mapping[str, Any], report: str) -> None:
        with self._lock:
            activation = self._activations[call_id]
            session = self._sessions[activation.handle]
            if session.closed or session.active_call != call_id or call_id not in self._claimed:
                raise SubagentSessionError("cannot commit an inactive activation")
            messages = result.get("messages")
            if not isinstance(messages, (list, tuple)) or not messages:
                raise SubagentSessionError("subagent returned no private transcript")
            session.state = {
                key: value for key, value in result.items()
                if key not in {"structured_response", "remaining_steps", "jump_to"}
                and not key.startswith(("guard_", "protocol_"))
            }
            session.state["messages"] = list(messages)
            session.features = {
                **session.features,
                "unresolved": reuse_features(
                    "", report, completion=result.get("structured_response"),
                )["unresolved"],
                "censored": bool(
                    result.get("guard_ever_intervened")
                    or result.get("guard_forcing_completion")
                    or result.get("protocol_repair_failed")
                ),
            }
            session.epoch = max(session.epoch, activation.initial_epoch)
            session.active_call = None
            self._claimed.discard(call_id)

    def return_attributes(self, call_id: str) -> dict:
        with self._lock:
            activation = self._activations.get(call_id)
            if activation is None:
                return {}
            session = self._sessions[activation.handle]
            if session.closed or session.active_call is not None or not session.state:
                return {}
            return {
                "reusable_subagent": True, "agent_handle": session.handle,
                "context_retired": False, "reuse_features": dict(session.features),
            }

    def abort(self, call_id: str) -> None:
        with self._lock:
            activation = self._activations.get(call_id)
            if activation is not None:
                session = self._sessions[activation.handle]
                session.closed = True
                session.active_call = None
                session.state.clear()
            self._claimed.discard(call_id)

    def close(self, handle: str, *, owner_context_id: str) -> SubagentSession:
        with self._lock:
            session = self._sessions.get(handle)
            if session is None or session.closed or session.owner_context_id != owner_context_id:
                raise SubagentSessionError("unknown or closed agent_id for this parent")
            if session.active_call is not None:
                raise SubagentSessionError("cannot close an active subagent")
            session.closed = True
            session.state.clear()
            return session

    def finish(self) -> None:
        with self._lock:
            self._ended = True
            self._claimed.clear()
            for session in self._sessions.values():
                session.closed = True
                session.active_call = None
                session.state.clear()
