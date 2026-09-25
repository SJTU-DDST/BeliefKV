"""Bounded, causal per-invocation history of completed identical tool calls."""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Mapping


class SameInputToolHistory:
    def __init__(self, *, limit: int = 2048) -> None:
        if limit < 1:
            raise ValueError("history limit must be positive")
        self.limit = limit
        self._open: dict[str, tuple[tuple[str, str, str, str], float]] = {}
        self._completed: OrderedDict[
            tuple[str, str, str, str], tuple[float, float, str]
        ] = OrderedDict()

    def start(
        self, workflow_id: str, invocation_id: str, attrs: Mapping[str, Any],
        ts_ms: float,
    ) -> dict[str, float | str]:
        call_id = str(attrs.get("tool_call_id") or "")
        signature = str(attrs.get("input_sha256") or "")
        tool_name = str(attrs.get("tool_name") or "")
        if not (call_id and signature and invocation_id and tool_name):
            return {}
        if call_id in self._open:
            raise ValueError("duplicate open tool call")
        key = workflow_id, invocation_id, tool_name, signature
        self._open[call_id] = key, ts_ms
        previous = self._completed.get(key)
        if previous is None or previous[1] >= ts_ms:
            return {}
        return {
            "previous_same_input_duration_ms": previous[0],
            "previous_same_input_age_ms": ts_ms - previous[1],
            "previous_same_input_status": previous[2],
        }

    def end(
        self, workflow_id: str, invocation_id: str, attrs: Mapping[str, Any],
        ts_ms: float,
    ) -> None:
        call_id = str(attrs.get("tool_call_id") or "")
        opened = self._open.pop(call_id, None)
        if opened is None:
            return
        key, start_ts_ms = opened
        if key[:2] != (workflow_id, invocation_id) or ts_ms < start_ts_ms:
            raise ValueError("tool end disagrees with start identity or clock")
        self._completed[key] = (
            ts_ms - start_ts_ms, ts_ms, str(attrs.get("status") or "unknown")
        )
        self._completed.move_to_end(key)
        if len(self._completed) > self.limit:
            self._completed.popitem(last=False)

    def discard_invocation(self, workflow_id: str, invocation_id: str) -> None:
        for key in list(self._completed):
            if key[:2] == (workflow_id, invocation_id):
                del self._completed[key]
        for call_id, (key, _) in list(self._open.items()):
            if key[:2] == (workflow_id, invocation_id):
                del self._open[call_id]
