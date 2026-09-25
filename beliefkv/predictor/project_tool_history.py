"""Causal, bounded project/command timing from completed child tool calls."""

from __future__ import annotations

from collections import OrderedDict, deque
from statistics import median
from threading import RLock
from typing import Any, Mapping


class ProjectToolHistory:
    def __init__(self, *, minimum_support: int = 16, window: int = 64,
                 max_keys: int = 256) -> None:
        if min(minimum_support, window, max_keys) < 1:
            raise ValueError("project history bounds must be positive")
        self.minimum_support = minimum_support
        self.window = window
        self.max_keys = max_keys
        self._lock = RLock()
        self._open: dict[tuple[str, str], tuple[str, str, float]] = {}
        self._completed: OrderedDict[
            tuple[str, str], deque[tuple[float, float]]
        ] = OrderedDict()

    def start(
        self, workflow_id: str, project: str, attrs: Mapping[str, Any],
        ts_ms: float,
    ) -> dict[str, float | int]:
        if (
            not project or attrs.get("tool_name") != "execute"
            or attrs.get("is_child") is not True
        ):
            return {}
        call_id = str(attrs.get("tool_call_id") or "")
        command = str(attrs.get("observed_command_class") or "")
        if not call_id or not command or command == "unknown":
            return {}
        with self._lock:
            call_key = workflow_id, call_id
            if call_key in self._open:
                raise ValueError("duplicate open project tool call")
            self._open[call_key] = project, command, ts_ms
            history = self._completed.get((project, command), ())
            values = [duration for duration, end in history if end < ts_ms]
            if len(values) < self.minimum_support:
                return {}
            return {
                "project_class_duration_median_ms": float(median(values)),
                "project_class_completed_support": len(values),
            }

    def end(
        self, workflow_id: str, attrs: Mapping[str, Any], ts_ms: float,
    ) -> None:
        call_key = workflow_id, str(attrs.get("tool_call_id") or "")
        with self._lock:
            opened = self._open.pop(call_key, None)
            if opened is None:
                return
            project, command, start_ts = opened
            if ts_ms < start_ts:
                raise ValueError("project tool end precedes its start")
            if attrs.get("status") != "success":
                return
            key = project, command
            history = self._completed.setdefault(key, deque(maxlen=self.window))
            history.append((ts_ms - start_ts, ts_ms))
            self._completed.move_to_end(key)
            while len(self._completed) > self.max_keys:
                self._completed.popitem(last=False)
