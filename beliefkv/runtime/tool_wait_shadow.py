"""One bounded timer for read-only observations of surviving tool calls."""

from __future__ import annotations

import heapq
import threading
import time
from typing import Callable


class ToolWaitShadowTimer:
    def __init__(self, *, max_pending: int = 8192) -> None:
        if max_pending <= 0:
            raise ValueError("max_pending must be positive")
        self._max_pending = max_pending
        self._pending: list[tuple[float, int, Callable[[], bool]]] = []
        self._condition = threading.Condition()
        self._closed = False
        self._sequence = 0
        self.dropped = 0
        self.errors = 0
        self.published = 0
        self._worker = threading.Thread(
            target=self._run, name="beliefkv-tool-wait-shadow", daemon=True
        )
        self._worker.start()

    def schedule(self, deadline_s: float, observe: Callable[[], bool]) -> bool:
        with self._condition:
            if self._closed or len(self._pending) >= self._max_pending:
                self.dropped += 1
                return False
            self._sequence += 1
            heapq.heappush(self._pending, (deadline_s, self._sequence, observe))
            self._condition.notify()
            return True

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._closed:
                    if not self._pending:
                        self._condition.wait()
                        continue
                    remaining = self._pending[0][0] - time.monotonic()
                    if remaining > 0:
                        self._condition.wait(remaining)
                        continue
                    _, _, observe = heapq.heappop(self._pending)
                    break
                else:
                    return
            try:
                published = observe()
            except Exception:
                with self._condition:
                    self.errors += 1
            else:
                if published:
                    with self._condition:
                        self.published += 1

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._pending.clear()
            self._condition.notify_all()
        self._worker.join()

    def summary(self) -> dict[str, int]:
        with self._condition:
            return {
                "published": self.published,
                "dropped": self.dropped,
                "errors": self.errors,
                "pending": len(self._pending),
            }

    def __enter__(self) -> "ToolWaitShadowTimer":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
