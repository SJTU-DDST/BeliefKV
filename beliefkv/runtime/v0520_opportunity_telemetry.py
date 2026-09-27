"""Opt-in, bounded safe-point observations for native admission.

Records are evidence of instantaneous headroom, never transfer authorization.
"""

from __future__ import annotations

import atexit
from collections import Counter
import json
from pathlib import Path
from queue import Full, Queue
from threading import Thread
from typing import Any


class NativeOpportunityTelemetry:
    def __init__(self, directory: str | Path) -> None:
        path = Path(directory).resolve()
        path.mkdir(parents=True, exist_ok=True)
        self.path = path / "admission_opportunities.jsonl"
        self.status_path = path / "admission_opportunities_status.json"
        if self.path.exists() or self.status_path.exists():
            raise RuntimeError("admission opportunity evidence already exists")
        self._queue: Queue[dict[str, Any] | None] = Queue(maxsize=4096)
        self._counts: Counter[str] = Counter()
        self._error: BaseException | None = None
        self._closed = False
        self._writer = Thread(target=self._write, name="admission-opportunity", daemon=True)
        self._writer.start()
        atexit.register(self.close)

    def record(self, row: dict[str, Any]) -> None:
        if self._closed or self._error is not None:
            raise RuntimeError(f"admission opportunity writer unavailable: {self._error}")
        try:
            self._queue.put_nowait(row)
        except Full as exc:
            self._error = exc
            raise RuntimeError("admission opportunity evidence queue overflow") from exc
        self._counts[row["event"]] += 1

    def _write(self) -> None:
        try:
            with self.path.open("x", encoding="utf-8") as stream:
                while (row := self._queue.get()) is not None:
                    stream.write(json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n")
        except BaseException as exc:
            self._error = exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._queue.put(None, timeout=2)
        except Full as exc:
            self._error = self._error or exc
        self._writer.join(timeout=2)
        if self._writer.is_alive():
            self._error = self._error or RuntimeError("writer did not drain before close")
        self.status_path.write_text(
            json.dumps({
                "schema_version": 1,
                "complete": self._error is None,
                "error": str(self._error) if self._error else None,
                "enqueued": dict(self._counts),
            }, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
