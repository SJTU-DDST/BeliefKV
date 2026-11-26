"""Bind the pinned MARS external admission controller to causal replay."""

from __future__ import annotations

import ast
import asyncio
import math
from pathlib import Path
import time
from types import SimpleNamespace

from beliefkv.experiments.paper_baseline_sglang import ENGINE


def _bind_author_control(checkout: Path, bindings: dict):
    # Only the enclosing testbed bindings change. The author function still
    # makes the decisions, including its tail drain and reservation accounting.
    path = checkout / "experiments/openhands/run_task_workload.py"
    tree = ast.parse(path.read_bytes(), filename=str(path))
    functions = {
        node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
        and node.name in ("_kv_blocks_for_tokens", "_process_mars_submissions")
    }
    control = functions["_process_mars_submissions"]
    state = {
        "mars_active_limit": bindings["args"].mars_window_init,
        "mars_last_control_mono": 0.,
        "mars_kv_cached_mono": 0.,
        "mars_kv_last_full_mono": 0.,
        "mars_cpu_overloaded": False,
        "mars_kv_overloaded": False,
        "mars_kv_cached": None,
        "mars_kv_last_full": None,
        "mars_kv_reliable_prev": None,
        "mars_kv_source_prev": None,
    }
    captured = {name for node in control.body if isinstance(node, ast.Nonlocal) for name in node.names}
    if captured != state.keys():
        raise ValueError("MARS author controller state bindings changed")
    control.body = [
        ast.copy_location(ast.Global(node.names), node)
        if isinstance(node, ast.Nonlocal) else node
        for node in control.body
    ]
    module = ast.fix_missing_locations(ast.Module(body=list(functions.values()), type_ignores=[]))
    scope = {**bindings, **state, "math": math, "time": time,
             "QueuedTask": SimpleNamespace, "Any": object}
    exec(compile(module, str(path), "exec"), scope)
    return scope[control.name], scope


def mars_defaults(checkout: Path):
    # The dynamic CLI replay uses these defaults with the OpenHands controller.
    path = checkout / "experiments/agent_cli/run_workload.py"
    defaults = {}
    for node in ast.walk(ast.parse(path.read_bytes(), filename=str(path))):
        if not (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument" and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str) and node.args[0].value.startswith("--mars-")
        ):
            continue
        defaults[node.args[0].value[2:].replace("-", "_")] = ast.literal_eval(
            next(keyword.value for keyword in node.keywords if keyword.arg == "default")
        )
    return SimpleNamespace(**defaults)


class MarsAdmission:
    def __init__(self, checkout, client, observe, *, device_tokens, deadline_s, engine="legacy"):
        self.client, self.observe = client, observe
        self.device_tokens, self.deadline_s = device_tokens, deadline_s
        self.engine = engine
        self.args = mars_defaults(checkout)
        self.arrivals = {}
        self.backlog = []
        self.pending = {}
        self.active = {}
        self.reserved = {}
        self.snapshot = None
        self.changed = asyncio.Event()
        self.task = None
        self.control, self.state = _bind_author_control(checkout, {
            "args": self.args, "use_mars": True, "backlog": self.backlog,
            "mars_window_min": self.args.mars_window_min,
            "futures": self.active, "future_meta": self.active,
            "pending_kv_blocks_by_request_id": self.reserved,
            # The common TraceLab overlay delivers already-running tool timers.
            "live_tool_stats": SimpleNamespace(stats=lambda: {
                "num_workers": self.args.mars_active_pool_size, "inflight": 0,
            }),
            "tool_cores": (),
            "_fetch_kv_cache_stats": lambda _url: dict(self.snapshot),
            "_fetch_metrics_gpu_cache_usage": lambda _url: None,
            "_submit_admitted_request": self._grant,
            "scheduling_policy": "mars",
            "events_writer": SimpleNamespace(write=observe),
            "now_s": time.monotonic,
        })
        self.args.base_url = str(client.base_url)

    async def refresh(self):
        path = "/server_info" if self.engine == ENGINE else "/v1/kv_cache_stats"
        response = await self.client.get(path, timeout=3.)
        response.raise_for_status()
        stats = response.json()
        if self.engine == ENGINE:
            states = stats.get("internal_states")
            if type(states) is not list or len(states) != 1 or type(states[0]) is not dict:
                raise ValueError("MARS requires one SGLang scheduler state")
            stats = states[0].get("mars_kv_cache_stats")
            if type(stats) is not dict:
                raise ValueError("MARS scheduler did not publish KV block statistics")
            if any(type(stats.get(key)) is not int or stats[key] < 0 for key in (
                "running_requests", "waiting_requests",
            )):
                raise ValueError("MARS scheduler did not publish native request counts")
        block, count, free = (stats.get(name) for name in (
            "block_size", "num_gpu_blocks", "num_free_blocks",
        ))
        if (
            any(type(value) is not int for value in (block, count, free))
            or block <= 0 or not 0 <= free < count
            or (count - 1) * block != self.device_tokens
            or stats.get("total_tokens") != self.device_tokens
            or stats.get("scheduling_policy") != "mars" or stats.get("max_num_seqs") != 24
            or self.engine == ENGINE and block != 16
        ):
            raise ValueError("MARS live policy or capacity differs from the declared launch")
        self.snapshot = stats
        return stats

    def arrived(self, workflow):
        if workflow in self.arrivals:
            raise ValueError("duplicate MARS root arrival")
        self.arrivals[workflow] = len(self.arrivals)

    def _grant(self, entry, *, kv_blocks_cost, is_long_task, admission_meta):
        workflow = entry.request_id
        self.active[workflow] = {"request_id": workflow, "is_long": is_long_task}
        if kv_blocks_cost:
            self.reserved[workflow] = kv_blocks_cost
        self.observe({
            **admission_meta, "event": "mars_admit", "workflow_id": workflow,
            "prompt_tokens_at_admission": entry.prefill_tokens_est,
            "admission_monotonic_ms": time.monotonic() * 1000.,
        })
        self.pending.pop(workflow).set_result(None)

    async def _process(self):
        try:
            while self.backlog:
                self.changed.clear()
                if (
                    self.snapshot is None or time.perf_counter() -
                    self.state["mars_kv_cached_mono"] >= self.args.mars_kv_stat_interval_s
                ):
                    await self.refresh()
                self.control()
                if self.backlog:
                    try:
                        await asyncio.wait_for(
                            self.changed.wait(), self.args.mars_control_interval_s,
                        )
                    except asyncio.TimeoutError:
                        pass
        except Exception as error:
            self.backlog.clear()
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(error)
            self.pending.clear()

    async def enter(self, workflow, *, root, prompt_tokens):
        if workflow in self.active:
            return
        if not root or workflow in self.pending or workflow not in self.arrivals:
            raise ValueError("MARS child or continuation has no admitted root")
        future = asyncio.get_running_loop().create_future()
        self.pending[workflow] = future
        self.backlog.append(SimpleNamespace(
            request_id=workflow, prefill_tokens_est=prompt_tokens,
            seq=self.arrivals[workflow],
        ))
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._process())
        self.changed.set()
        try:
            await asyncio.wait_for(asyncio.shield(future), self.deadline_s)
        finally:
            if self.pending.pop(workflow, None) is future:
                future.cancel()
                self.backlog[:] = [row for row in self.backlog if row.request_id != workflow]
                self.changed.set()

    def ended(self, workflow):
        if self.active.pop(workflow, None) is None:
            raise ValueError("MARS workflow ended without admission")
        self.reserved.pop(workflow, None)
        self.changed.set()

    async def close(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
