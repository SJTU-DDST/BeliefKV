"""Opt-in client-side lifetime for native v0.5.20 radix session references."""

from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.request
import uuid
from collections.abc import Callable
from concurrent.futures import Executor, Future

from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata


class NativeRadixSessionLeases:
    """One native session per (workflow, context, compaction), closed at terminal.

    The server must be started with --enable-session-radix-cache. This only
    biases native eviction; it is not a tool-wait pin or a predictive transfer.
    """

    def __init__(
        self, close_session: Callable[[str], None], *,
        lifecycle_observer: Callable[[dict], None] | None = None,
        close_executor: Executor | None = None,
    ) -> None:
        self._close_session = close_session
        self._lifecycle_observer = lifecycle_observer
        self._close_executor = close_executor
        self._namespace = uuid.uuid4().hex
        self._lock = threading.RLock()
        self._active: dict[tuple[str, str], tuple[int, str]] = {}
        self._pending: dict[tuple[str, str], Future[None]] = {}
        self._last_epoch: dict[tuple[str, str], int] = {}
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
            last_epoch = self._last_epoch.get(context, -1)
            if metadata.context_epoch < last_epoch:
                raise RuntimeError("native radix session epoch regressed")
            previous = self._active.get(context)
            if previous is not None:
                self._last_epoch[context] = metadata.context_epoch
                self._active[context] = (metadata.context_epoch, previous[1])
                return previous[1]
            digest = hashlib.sha256(
                f"{self._namespace}:{context[0]}:{context[1]}:"
                f"{metadata.context_epoch}".encode("utf-8")
            ).hexdigest()[:32]
            session_id = f"beliefkv-{digest}"
            self._active[context] = (metadata.context_epoch, session_id)
            self._last_epoch[context] = metadata.context_epoch
            return session_id

    def compact(self, workflow_id: str, context_id: str, new_epoch: int) -> None:
        context = (workflow_id, context_id)
        with self._lock:
            if context in self._terminal or workflow_id in self._terminal_workflows:
                raise RuntimeError("native radix session compacted after context terminal")
            if new_epoch <= self._last_epoch.get(context, -1):
                raise RuntimeError("native radix compaction must advance context epoch")
            active = self._active.get(context)
            if active is not None:
                self._close_session(active[1])
                del self._active[context]
            self._last_epoch[context] = new_epoch

    def retire(self, workflow_id: str, context_id: str) -> None:
        context = (workflow_id, context_id)
        with self._lock:
            self._terminal.add(context)
            active = self._active.get(context)
            if active is None:
                return
            if self._close_executor is None:
                self._close_retired(context, active)
                return
            pending = self._pending.get(context)
            if pending is not None and not pending.done():
                return
            queued_at = time.monotonic() * 1000.
            if self._lifecycle_observer is not None:
                self._lifecycle_observer({
                    "event": "native_session_retire_queued",
                    "workflow_id": workflow_id, "context_id": context_id,
                    "context_epoch": active[0], "session_id": active[1],
                    "close_enqueue_monotonic_ms": queued_at,
                })
            self._pending[context] = self._close_executor.submit(
                self._close_retired, context, active, queued_at,
            )

    def _close_retired(
        self, context: tuple[str, str], active: tuple[int, str],
        queued_at: float | None = None,
    ) -> None:
        start = time.monotonic() * 1000.
        record = {
            "workflow_id": context[0], "context_id": context[1],
            "context_epoch": active[0], "session_id": active[1],
            "close_start_monotonic_ms": start,
        }
        if queued_at is not None:
            record.update(
                close_enqueue_monotonic_ms=queued_at,
                enqueue_to_close_start_ms=start - queued_at,
            )
        if self._lifecycle_observer is not None:
            self._lifecycle_observer({**record, "event": "native_session_retire_start"})
        try:
            self._close_session(active[1])
        except Exception as error:
            if self._lifecycle_observer is not None:
                self._lifecycle_observer({
                    **record, "event": "native_session_retire_failed",
                    "close_elapsed_ms": time.monotonic() * 1000. - start,
                    "error_type": type(error).__name__,
                })
            raise
        elapsed = time.monotonic() * 1000. - start
        if self._lifecycle_observer is not None:
            self._lifecycle_observer({
                **record, "event": "native_session_retire_complete",
                "close_elapsed_ms": elapsed,
                "enqueue_to_http_complete_ms": (
                    start - queued_at + elapsed if queued_at is not None else elapsed
                ),
                "semantics": (
                    "HTTP dispatch accepted; scheduler reference release unacknowledged; "
                    "not physical cache reclamation"
                ),
            })
        with self._lock:
            if self._active.get(context) == active:
                del self._active[context]

    def drain(self) -> None:
        """Finish queued terminal dispatches before closing the lifecycle audit."""
        # Retain failed identities and retry once at workflow cleanup.
        for attempt in range(2):
            with self._lock:
                pending = tuple(self._pending.items())
            failures = []
            for context, future in pending:
                try:
                    future.result()
                except Exception as error:
                    failures.append((context, error))
            if not failures:
                with self._lock:
                    for context, future in pending:
                        if self._pending.get(context) is future:
                            del self._pending[context]
                return
            if attempt:
                raise failures[0][1]
            for context, _ in failures:
                self.retire(*context)

    def retire_workflow(self, workflow_id: str) -> None:
        with self._lock:
            self._terminal_workflows.add(workflow_id)
            contexts = [
                key for key in self._active
                if key[0] == workflow_id and key not in self._pending
            ]
        for _, context_id in contexts:
            self.retire(workflow_id, context_id)


def close_native_radix_session(
    server_root_url: str, session_id: str, *, timeout_s: float = 30.0
) -> None:
    """Dispatch a native close; HTTP success does not acknowledge scheduler release."""
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
