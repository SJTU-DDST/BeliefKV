"""Opt-in resource advice on existing root calls, after complete tool groups."""

from __future__ import annotations

import asyncio
import json
import time

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from beliefkv.runtime.planning_advice import AdviceRead, CAPACITY_FIELDS, PlanningAdviceReader


MARKER = "beliefkv_planning_advice"
DELEGATION_TOOLS = frozenset(("task", "parallel_tasks"))
PLANNING_CUE = (
    "Plan the remaining work while preserving every required result and verification. "
    "Group checks sharing the same evidence into bounded assignments when useful; "
    "split independent work when it benefits execution. Keep dependent integration "
    "in the parent. Avoid duplicate investigation. Current rows and KV counters "
    "are observations, not reserved capacity or a prediction of useful parallel speedup. "
    "The next-batch reserve is a one-batch safety allowance, not a future-growth guarantee. "
    "The serving system rechecks admission when requests arrive."
)


def planning_boundary(messages):
    if not messages:
        return None
    last = messages[-1]
    if MARKER in getattr(last, "additional_kwargs", {}):
        return None
    if isinstance(last, HumanMessage):
        return "arrival_or_new_stage"
    if not isinstance(last, ToolMessage):
        return None
    tools = []
    for message in reversed(messages):
        if isinstance(message, ToolMessage):
            tools.append(message)
        else:
            origin = message
            break
    else:
        return None
    if not isinstance(origin, AIMessage) or not origin.tool_calls:
        return None
    expected = {call["id"] for call in origin.tool_calls}
    if expected != {message.tool_call_id for message in tools}:
        return None
    succeeded = {message.tool_call_id for message in tools if message.status != "error"}
    if any(call["name"] in DELEGATION_TOOLS and call["id"] in succeeded for call in origin.tool_calls):
        return "completed_delegation_group"
    return None


class PlanningAdviceMiddleware(AgentMiddleware):
    def __init__(self, *, mode, reader=None, audit=None):
        super().__init__()
        if mode not in ("static", "hardware") or (mode == "hardware" and reader is None):
            raise ValueError("planning advice requires static mode or a hardware reader")
        self.mode, self.reader, self.audit = mode, reader, audit
        self._last_observed_ms = None
        self._last_snapshot_id = None

    def _read(self):
        return self.reader.read() if self.mode == "hardware" else AdviceRead(None, "static")

    def _update(self, phase):
        started = time.perf_counter()
        read = self._read()
        payload = {"resource_observation": "unavailable", "service_cost_supported": False}
        snapshot_id = None
        if read.snapshot is not None:
            snapshot = read.snapshot
            payload["resource_observation"] = "fresh"
            snapshot_id = f"{snapshot['server_id']}:{snapshot['revision']}"
            payload.update({key: snapshot["capacity"][key] for key in CAPACITY_FIELDS})
            self._last_observed_ms = snapshot["observed_monotonic_ms"]
        else:
            self._last_observed_ms = None
        self._last_snapshot_id = snapshot_id
        content = PLANNING_CUE + "\nServing observation: " + json.dumps(payload, separators=(",", ":"))
        metadata = {
            "mode": self.mode, "phase": phase, "status": read.status,
            "snapshot_id": snapshot_id, "advice_only": True,
        }
        if self.audit is not None:
            self.audit.emit(
                "planning_advice_consumed", **metadata, input_chars=len(content),
                read_render_ms=(time.perf_counter() - started) * 1000.,
            )
        return {"messages": [HumanMessage(content=content, additional_kwargs={MARKER: metadata})]}

    def before_model(self, state, runtime):
        phase = planning_boundary(state["messages"])
        return self._update(phase) if phase is not None else None

    async def abefore_model(self, state, runtime):
        phase = planning_boundary(state["messages"])
        return await asyncio.to_thread(self._update, phase) if phase is not None else None

    def _record_submit(self):
        if self.audit is None:
            return
        read = self._read()
        now_ms = time.monotonic() * 1000.
        self.audit.emit(
            "planning_advice_at_delegate", mode=self.mode,
            consumed_snapshot_id=self._last_snapshot_id,
            consumed_observation_age_ms=(
                now_ms - self._last_observed_ms if self._last_observed_ms is not None else None
            ),
            current_status=read.status, native_admission_required=True,
        )

    def wrap_tool_call(self, request, handler):
        if request.tool_call["name"] in DELEGATION_TOOLS:
            self._record_submit()
        return handler(request)

    async def awrap_tool_call(self, request, handler):
        if request.tool_call["name"] in DELEGATION_TOOLS:
            await asyncio.to_thread(self._record_submit)
        return await handler(request)


def middleware_for_config(config, *, audit=None):
    if config.planning_advice_mode == "off":
        return None
    reader = (
        PlanningAdviceReader(
            config.planning_advice_file, run_id=config.planning_advice_run_id,
            model_path=config.planning_advice_model_path,
        ) if config.planning_advice_mode == "hardware" else None
    )
    return PlanningAdviceMiddleware(mode=config.planning_advice_mode, reader=reader, audit=audit)
