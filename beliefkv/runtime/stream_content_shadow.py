"""Bounded, diagnostic-only writer for delivered child stream observations."""

from __future__ import annotations

import json
from pathlib import Path
from queue import Empty, Full, Queue
import threading
from typing import Any


class StreamContentShadow:
    def __init__(self, path: Path, *, capacity: int = 4096) -> None:
        self.path = path
        self._queue: Queue[dict[str, Any]] = Queue(maxsize=capacity)
        self._lock = threading.Lock()
        self._dropped = 0
        self._written = 0
        self._error: str | None = None
        self._closed = False
        path.parent.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._write, daemon=True)
        self._thread.start()

    def emit(self, row: dict[str, Any]) -> None:
        with self._lock:
            if self._closed or self._error is not None:
                self._dropped += 1
                return
            try:
                self._queue.put_nowait(row)
            except Full:
                self._dropped += 1

    def _write(self) -> None:
        try:
            with self.path.open("x", encoding="utf-8") as output:
                while True:
                    with self._lock:
                        done = self._closed and self._queue.empty()
                    if done:
                        break
                    try:
                        row = self._queue.get(timeout=0.1)
                    except Empty:
                        continue
                    output.write(json.dumps(row, ensure_ascii=True) + "\n")
                    self._written += 1
        except (OSError, TypeError, ValueError) as error:
            with self._lock:
                self._error = f"{type(error).__name__}: {error}"

    def close(self) -> dict[str, Any]:
        with self._lock:
            self._closed = True
        self._thread.join()
        return {
            "written": self._written,
            "dropped": self._dropped,
            "error": self._error,
            "complete": self._dropped == 0 and self._error is None,
        }
