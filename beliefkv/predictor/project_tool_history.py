"""Causal, bounded project/command timing from completed child tool calls."""

from __future__ import annotations

from collections import OrderedDict, deque
import math
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
        self._open: dict[
            tuple[str, str], tuple[str, str, float, bool, int | None]
        ] = {}
        self._completed: OrderedDict[
            tuple[str, str], deque[tuple[float, float, int | None]]
        ] = OrderedDict()
        self._long_completed: OrderedDict[
            tuple[str, str], deque[tuple[float, float]]
        ] = OrderedDict()

    def start(
        self, workflow_id: str, project: str, attrs: Mapping[str, Any],
        ts_ms: float,
    ) -> dict[str, float | int]:
        if not project or attrs.get("is_child") is not True:
            return {}
        call_id = str(attrs.get("tool_call_id") or "")
        command = str(attrs.get("observed_command_class") or "")
        if not call_id or not command or command == "unknown":
            return {}
        with self._lock:
            call_key = workflow_id, call_id
            if call_key in self._open:
                raise ValueError("duplicate open project tool call")
            peers = sum(
                other_workflow != workflow_id
                and other_project == project and other_command == command
                and 0 <= other_start <= ts_ms - 2_000
                for (other_workflow, _), (other_project, other_command,
                                           other_start, _, _) in self._open.items()
            )
            is_execute = attrs.get("tool_name") == "execute"
            size = attrs.get("input_chars")
            size = size if type(size) is int and size > 0 else None
            self._open[call_key] = project, command, ts_ms, is_execute, size
            observed = (
                {"project_class_inflight_other_workflow_2s_peers": peers}
                if peers else {}
            )
            long_values = [
                duration for duration, end
                in self._long_completed.get((project, command), ())
                if end < ts_ms
            ]
            if len(long_values) >= 3:
                observed.update({
                    "project_long_completed_median_ms": float(median(long_values)),
                    "project_long_completed_support": len(long_values),
                })
            if not is_execute:
                return observed
            history = self._completed.get((project, command), ())
            completed = [
                (duration, end, prior_size) for duration, end, prior_size in history
                if end < ts_ms
            ]
            if size is not None:
                nearby = sorted(
                    (
                        abs(math.log(size / prior_size)), duration
                    )
                    for duration, _, prior_size in completed
                    if prior_size is not None
                )
                neighbors = [
                    duration for distance, duration in nearby[:8]
                    if distance <= math.log(2)
                ]
                if len(neighbors) >= 4:
                    observed.update({
                        "project_input_neighbor_duration_ms": float(
                            median(neighbors)
                        ),
                        "project_input_neighbor_support": len(neighbors),
                    })
            values = [duration for duration, _, _ in completed]
            if len(values) < self.minimum_support:
                return observed
            return {
                **observed,
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
            project, command, start_ts, is_execute, input_chars = opened
            if ts_ms < start_ts:
                raise ValueError("project tool end precedes its start")
            if attrs.get("status") != "success":
                return
            key = project, command
            duration = ts_ms - start_ts
            if duration >= 2_000:
                long_history = self._long_completed.setdefault(
                    key, deque(maxlen=self.window)
                )
                long_history.append((duration, ts_ms))
                self._long_completed.move_to_end(key)
                while len(self._long_completed) > self.max_keys:
                    self._long_completed.popitem(last=False)
            if not is_execute:
                return
            history = self._completed.setdefault(key, deque(maxlen=self.window))
            history.append((duration, ts_ms, input_chars))
            self._completed.move_to_end(key)
            while len(self._completed) > self.max_keys:
                self._completed.popitem(last=False)

    def discard_workflow(self, workflow_id: str) -> None:
        with self._lock:
            for key in tuple(self._open):
                if key[0] == workflow_id:
                    del self._open[key]
