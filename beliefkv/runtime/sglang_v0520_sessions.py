"""Opt-in client-side lifetime for native v0.5.20 radix session references."""

from __future__ import annotations

import hashlib
import json
import threading
import urllib.request
import uuid
from collections.abc import Callable

from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata


class NativeRadixSessionLeases:
    """One native session per (workflow, context, epoch), closed at terminal.

    The server must be started with --enable-session-radix-cache. This only
    biases native eviction; it is not a tool-wait pin or a predictive transfer.
    """

    def __init__(self, close_session: Callable[[str], None]) -> None:
        self._close_session = close_session
        self._namespace = uuid.uuid4().hex
        self._lock = threading.RLock()
        self._active: dict[tuple[str, str], tuple[int, str]] = {}
        self._terminal: set[tuple[str, str]] = set()
        self._terminal_workflows: set[str] = set()

    def for_request(self, metadata: BeliefKVRequestMetadata) -> str | None:
        context = (metadata.root_workflow_id, metadata.context_id)
        with self._lock:
            if (
                context in self._terminal
                or metadata.root_workflow_id in self._terminal_workflows
            ):
                raise RuntimeError("native radix session requested after context terminal")
            if not metadata.full_prompt_replay_guaranteed:
                return None
            previous = self._active.get(context)
            if previous is not None:
                previous_epoch, previous_id = previous
                if metadata.context_epoch < previous_epoch:
                    raise RuntimeError("native radix session epoch regressed")
                if metadata.context_epoch == previous_epoch:
                    return previous_id
                # Do not mint a new session until the old reference is released.
                self._close_session(previous_id)
            digest = hashlib.sha256(
                f"{self._namespace}:{context[0]}:{context[1]}:"
                f"{metadata.context_epoch}".encode("utf-8")
            ).hexdigest()[:32]
            session_id = f"beliefkv-{digest}"
            self._active[context] = (metadata.context_epoch, session_id)
            return session_id

    def retire(self, workflow_id: str, context_id: str) -> None:
        context = (workflow_id, context_id)
        with self._lock:
            self._terminal.add(context)
            active = self._active.get(context)
            if active is not None:
                # Failed close remains retryable; never silently forget a ref.
                self._close_session(active[1])
                del self._active[context]

    def retire_workflow(self, workflow_id: str) -> None:
        with self._lock:
            self._terminal_workflows.add(workflow_id)
            contexts = [key for key in self._active if key[0] == workflow_id]
        for _, context_id in contexts:
            self.retire(workflow_id, context_id)


def close_native_radix_session(
    server_root_url: str, session_id: str, *, timeout_s: float = 2.0
) -> None:
    """Call v0.5.20's native close endpoint after the final request completes."""
    if not server_root_url.startswith(("http://", "https://")):
        raise ValueError("invalid native session server URL")
    if not session_id.startswith("beliefkv-") or timeout_s <= 0:
        raise ValueError("invalid native session close")
    root = server_root_url.rstrip("/").removesuffix("/v1")
    request = urllib.request.Request(
        f"{root}/close_session",
        data=json.dumps({"session_id": session_id}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        if response.status != 200:
            raise RuntimeError(f"native session close failed: HTTP {response.status}")
