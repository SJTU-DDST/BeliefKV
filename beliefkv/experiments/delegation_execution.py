"""Optional invocation concurrency control for single-level natural planning."""

from __future__ import annotations

import asyncio
from collections import deque
from concurrent.futures import Future
import threading
import time

from langchain.agents.middleware import AgentMiddleware


class _Lease:
    def __init__(self, slots):
        self._slots = slots
        self._released = False

    def release(self):
        self._slots.release(self)


class _InvocationSlots:
    """Share a bounded FIFO across sync tools and cancellable async tools."""

    def __init__(self, limit):
        if type(limit) is not int or limit <= 0:
            raise ValueError("delegation execution limit must be a positive integer")
        self.limit = limit
        self._lock = threading.Lock()
        self._leased = 0
        self._waiting = deque()

    def _request(self):
        pending = Future()
        with self._lock:
            immediate = self._leased < self.limit
            if immediate:
                self._leased += 1
            else:
                self._waiting.append(pending)
        if immediate:
            pending.set_result(_Lease(self))
        return pending

    @staticmethod
    def _abandon(pending):
        if pending.cancel():
            return

        def return_grant(completed):
            if not completed.cancelled():
                completed.result().release()

        pending.add_done_callback(return_grant)

    def acquire(self):
        pending = self._request()
        try:
            return pending.result()
        except BaseException:
            self._abandon(pending)
            raise

    async def acquire_async(self):
        pending = self._request()
        try:
            return await asyncio.wrap_future(pending)
        except BaseException:
            self._abandon(pending)
            raise

    def release(self, lease):
        selected = None
        with self._lock:
            if lease._released:
                return
            lease._released = True
            while self._waiting:
                pending = self._waiting.popleft()
                if pending.set_running_or_notify_cancel():
                    selected = pending
                    break
            if selected is None:
                self._leased -= 1
        # Future callbacks may return a cancelled caller's grant immediately.
        if selected is not None:
            selected.set_result(_Lease(self))

    def snapshot(self):
        with self._lock:
            return {
                "limit": self.limit, "leased": self._leased,
                "queued": sum(not pending.cancelled() for pending in self._waiting),
            }


class DelegationExecutionMiddleware(AgentMiddleware):
    """Limit root task lifetimes without adding messages or changing assignments."""

    def __init__(self, *, limit, audit=None):
        super().__init__()
        self._slots = _InvocationSlots(limit)
        self.audit = audit

    def _record(self, event, request, **fields):
        if self.audit is not None:
            self.audit.emit(
                event, tool_call_id=request.tool_call["id"],
                **self._slots.snapshot(), **fields,
                scope="root_task_invocation_per_workflow",
                gpu_reservation=False, planner_visible=False,
            )

    def wrap_tool_call(self, request, handler):
        if request.tool_call["name"] != "task":
            return handler(request)
        queued_at = time.perf_counter()
        lease = self._slots.acquire()
        entered_at = time.perf_counter()
        outcome = "raised"
        try:
            self._record(
                "delegation_execution_enter", request,
                wait_ms=(entered_at - queued_at) * 1000.,
            )
            result = handler(request)
            outcome = "returned"
            return result
        finally:
            lease.release()
            self._record(
                "delegation_execution_exit", request, outcome=outcome,
                handler_wall_ms=(time.perf_counter() - entered_at) * 1000.,
            )

    async def awrap_tool_call(self, request, handler):
        if request.tool_call["name"] != "task":
            return await handler(request)
        queued_at = time.perf_counter()
        lease = await self._slots.acquire_async()
        entered_at = time.perf_counter()
        outcome = "raised"
        try:
            self._record(
                "delegation_execution_enter", request,
                wait_ms=(entered_at - queued_at) * 1000.,
            )
            result = await handler(request)
            outcome = "returned"
            return result
        finally:
            lease.release()
            self._record(
                "delegation_execution_exit", request, outcome=outcome,
                handler_wall_ms=(time.perf_counter() - entered_at) * 1000.,
            )
