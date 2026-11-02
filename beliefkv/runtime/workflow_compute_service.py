"""Opt-in warm short-input cofill and bounded full-batch decode service."""

from collections import Counter
from contextvars import ContextVar
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import time

from beliefkv.runtime.sglang_v0520_admission import _request_key
from beliefkv.runtime.short_continuation_probe import inspect_short_continuations


CONFIG_ENV = "BELIEFKV_WORKFLOW_COMPUTE_SERVICE_CONFIG"
HASH_ENV = CONFIG_ENV + "_SHA256"
SCOPE = ContextVar("beliefkv_workflow_compute_service_scope", default=None)
HOOK_ROOT = "sglang.srt.managers."


def configuration(environment=None):
    environment = os.environ if environment is None else environment
    path, digest = environment.get(CONFIG_ENV, ""), environment.get(HASH_ENV, "")
    if not path and not digest:
        return None
    if not path or not digest:
        raise ValueError("compute service requires its explicit configuration and hash")
    data = Path(path).read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("compute service configuration changed")
    raw = json.loads(data)
    expected = {
        "schema_version", "kind", "selection", "maximum_checks", "soft_budget_ms",
        "minimum_work_ratio", "burst_decode_steps", "burst_wall_ms",
        "total_decode_steps", "focus_wall_ms",
    }
    if set(raw) != expected or type(raw["schema_version"]) is not int or raw["schema_version"] != 1 or (
        raw["kind"] != "warm_workflow_compute_service_v1"
        or raw["selection"] not in ("reference", "oracle_model_work")
    ):
        raise ValueError("unsupported explicit compute service policy")
    for name, low, high in (
        ("maximum_checks", 1, 8), ("burst_decode_steps", 1, 16),
        ("total_decode_steps", 1, 128), ("burst_wall_ms", 1, 500),
        ("focus_wall_ms", 1, 5000),
    ):
        if type(raw[name]) is not int or not low <= raw[name] <= high:
            raise ValueError("compute service has an invalid bounded " + name)
    for name, low, high in (("soft_budget_ms", 0., 1.), ("minimum_work_ratio", 1., 8.)):
        value = raw[name]
        if type(value) not in (float, int) or not math.isfinite(value) or not low < value <= high:
            raise ValueError("compute service has an invalid bounded " + name)
    if raw["burst_decode_steps"] > raw["total_decode_steps"] or raw["burst_wall_ms"] > raw["focus_wall_ms"]:
        raise ValueError("compute service burst exceeds its complete focus budget")
    return {**raw, "configuration_path": str(Path(path).resolve()), "configuration_sha256": digest}


@dataclass
class _Focus:
    request: object
    key: object
    owner: object
    created: float
    burst_started: float
    burst_deferrals: int = 0
    total_deferrals: int = 0
    output_at_selection: int = 0


class WorkflowComputeService:
    def __init__(self, runtime, settings):
        if (
            runtime.internal_comparison_mode != "full"
            or getattr(runtime, "_paper_baseline", None) is not None
            or getattr(runtime, "physical_disabled", True)
            or getattr(getattr(runtime, "_agent_admission", None), "strategy", None)
            != "thunderagent_reference"
        ):
            raise ValueError("compute service requires explicit Full reference-agent management")
        self.runtime, self.settings = runtime, settings
        self.ranked = settings["selection"] == "oracle_model_work"
        self.work = getattr(runtime._agent_admission, "_work", None)
        if self.ranked and not callable(getattr(self.work, "cached_model_work", None)):
            raise ValueError("ranked compute service requires the bound oracle's cached model work")
        self.focus = None
        self.running_batch = None
        self.counts = Counter()

    def _record(self, event, **details):
        writer = getattr(self.runtime, "_opportunity_writer", None)
        if writer is not None:
            writer.record({
                "event": event, "ts_ms": time.time() * 1000.,
                "monotonic_s": time.monotonic(),
                "configuration_sha256": self.settings["configuration_sha256"],
                "selection": self.settings["selection"],
                "future_information_used": self.ranked,
                "physical_capacity_credit": False, **details,
            })

    def _finish(self, reason):
        focus = self.focus
        if focus is None:
            return
        self.focus = None
        self.counts["focus_finished:" + reason] += 1
        self._record(
            "workflow_compute_service_focus_finished", reason=reason,
            key=asdict(focus.key), total_prefill_deferrals=focus.total_deferrals,
            observed_output_growth=len(focus.request.output_ids) - focus.output_at_selection,
            semantics="native selection deferrals; actual service remains in native batch receipts",
        )

    def _live(self, now):
        focus = self.focus
        if focus is None:
            return False
        req, key = focus.request, focus.key
        if (
            self.runtime.visible.get(key.request_id) != key
            or _request_key(req, known_keys=self.runtime.visible) != key
            or req.finished() or getattr(req, "to_finish", None) is not None
            or self.runtime._terminal(key)
            or not self.runtime._agent_admission.allows(key)
        ):
            self._finish("request_finished_or_changed")
            return False
        if now - focus.created >= self.settings["focus_wall_ms"] / 1000.:
            self._finish("wall_budget")
            return False
        if focus.total_deferrals >= self.settings["total_decode_steps"]:
            self._finish("total_deferral_budget")
            return False
        return True

    def before_selection(self, running_batch):
        self.running_batch = running_batch
        self._live(time.monotonic())

    def defer_prefill(self, scheduler):
        now = time.monotonic()
        if not self._live(now):
            return False
        focus, running = self.focus, self.running_batch
        if (
            running is None or getattr(running, "is_prefill_only", True)
            or not any(req is focus.request for req in getattr(running, "reqs", ()))
            or not (scheduler.chunked_req is not None or scheduler.waiting_queue)
        ):
            return False
        if (
            focus.burst_deferrals >= self.settings["burst_decode_steps"]
            or now - focus.burst_started >= self.settings["burst_wall_ms"] / 1000.
        ):
            return False
        focus.burst_deferrals += 1
        focus.total_deferrals += 1
        self.counts["native_prefill_deferral_decisions"] += 1
        return True

    def prefill_selected(self, batch):
        if batch is not None and self._live(time.monotonic()):
            self.focus.burst_started = time.monotonic()
            self.focus.burst_deferrals = 0
            self.counts["focus_prefill_progress_boundaries"] += 1

    def _score(self, key):
        self.counts["cached_model_work_reads"] += 1
        value = self.work.cached_model_work(key.root_workflow_id)
        if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
            return None
        return value

    def choose(self, owner, candidates):
        owner_key = _request_key(owner, known_keys=self.runtime.visible)
        if owner_key is None or self.runtime._ready_join_parent(owner, owner_key):
            return None, "protected_or_unknown_owner"
        protected = [row for row in candidates if row["observed_ready_join_parent"]]
        if protected:
            return protected[0], "protected_join"
        if not self.ranked:
            return (candidates[0], "reference_queue") if candidates else (None, "no_candidate")
        owner_work = self._score(owner_key)
        if owner_work is None:
            return None, "owner_work_unavailable"
        ordered = []
        for index, row in enumerate(candidates):
            key = self.runtime.visible.get(row["key"]["request_id"])
            if key is None or asdict(key) != row["key"] or key.root_workflow_id == owner_key.root_workflow_id:
                continue
            work = self._score(key)
            if work is not None and work < owner_work and work * self.settings["minimum_work_ratio"] <= owner_work:
                ordered.append((work, index, row))
        return (min(ordered, key=lambda item: item[:2])[2], "shorter_workflow") if ordered else (
            None, "no_supported_shorter_workflow",
        )

    def cofill(self, original, adder, owner, scheduler, running_batch):
        if self._live(time.monotonic()):
            return original(adder, owner)
        started = time.thread_time()
        report = inspect_short_continuations(
            self.runtime, scheduler.tree_cache, scheduler.waiting_queue,
            running_batch=running_batch, adder=adder, chunked_req=owner,
            maximum_checks=self.settings["maximum_checks"],
            soft_budget_ms=self.settings["soft_budget_ms"],
        )
        self.counts["cofill_checks"] += 1
        self.counts["physical_candidate_checks"] += report["checked"]
        for reason, count in report["reasons"].items():
            self.counts["probe:" + reason] += count
        row, reason = self.choose(owner, report["candidates"])
        if row is None or (time.thread_time() - started) * 1000. > self.settings["soft_budget_ms"]:
            self.counts["cofill_skipped:" + (reason if row is None else "cpu_budget")] += 1
            return original(adder, owner)
        req = next((req for req in scheduler.waiting_queue if req.rid == row["key"]["request_id"]), None)
        key = self.runtime.visible.get(row["key"]["request_id"])
        if req is None or key is None or asdict(key) != row["key"]:
            return original(adder, owner)
        req.init_next_round_input(scheduler.tree_cache)
        if (
            _request_key(req, known_keys=self.runtime.visible) != key
            or self.runtime._executable_waiting_key(req, key=key) != key
            or not self.runtime._agent_admission.allows(key)
            or len(req.prefix_indices) != row["checkpoint_tokens"]
            or req.needs_host_load_back()
            or len(req.full_untruncated_fill_ids) - len(req.prefix_indices) != row["short_input_tokens"]
        ):
            self.counts["cofill_skipped:revalidation"] += 1
            return original(adder, owner)
        result = adder.add_one_req(req, has_chunked_req=True, truncation_align_size=None)
        if not any(member is req for member in adder.can_run_list):
            self.counts["cofill_skipped:native_refusal"] += 1
            return original(adder, owner)
        if adder.new_chunked_req is not None:
            raise RuntimeError("warm compute cofill unexpectedly created another chunk owner")
        # Do not let the later native queue loop admit this committed member twice.
        scheduler.waiting_queue.remove(req)
        retained = original(adder, owner)
        if (
            not any(member is owner for member in adder.can_run_list)
            or owner.extend_range.length < row["owner_input_tokens"]
        ):
            raise RuntimeError("native chunk owner lost its guaranteed cofill progress")
        self.runtime.on_prefill_candidate_result(req, admitted=True, result=result.name)
        now = time.monotonic()
        self.focus = _Focus(req, key, owner, now, now, output_at_selection=len(req.output_ids))
        self.counts["native_cofills_committed"] += 1
        self._record(
            "workflow_compute_service_cofill_committed", key=asdict(key),
            owner_request_id=owner.rid, choice_reason=reason,
            short_input_tokens=req.extend_range.length,
            owner_input_tokens=owner.extend_range.length,
            thread_cpu_ms=(time.thread_time() - started) * 1000.,
            native_eviction_credit_tokens=0,
            semantics="actual native PrefillAdder commit; worker execution is recorded separately",
        )
        return retained

    def status(self):
        focus = self.focus
        return {
            "configuration_sha256": self.settings["configuration_sha256"],
            "selection": self.settings["selection"], "future_information_used": self.ranked,
            "rank": "cached_remaining_model_work" if self.ranked else "reference_queue",
            "focus_request_id": focus.key.request_id if focus else None,
            "counts": dict(self.counts),
        }


def _controller(scheduler):
    runtime = getattr(scheduler, "beliefkv_runtime", None)
    if runtime is None:
        raise ValueError("configured compute service has no Full runtime")
    current = getattr(runtime, "_workflow_compute_service", None)
    if current is None:
        settings = configuration()
        if settings is None:
            raise ValueError("compute service configuration disappeared after hook registration")
        if (
            getattr(scheduler, "require_mlp_sync", True)
            or getattr(getattr(scheduler, "ps", None), "tp_size", None) != 1
            or getattr(scheduler, "enable_lora", True)
            or getattr(scheduler, "dllm_config", None) is not None
            or getattr(scheduler, "is_mixed_chunk", True)
            or getattr(scheduler, "truncation_align_size", None) is not None
        ):
            raise ValueError("compute service requires nonmixed single-rank FULL-only native scheduling")
        current = runtime._workflow_compute_service = WorkflowComputeService(runtime, settings)
    return current


def before_selection(original, scheduler, running_batch, last_batch):
    controller = _controller(scheduler)
    result = original(scheduler, running_batch, last_batch)
    controller.before_selection(running_batch)
    return result


def should_defer_prefill(original, scheduler):
    return original(scheduler) or _controller(scheduler).defer_prefill(scheduler)


def raw_prefill(original, scheduler, *args, **kwargs):
    running = kwargs.get("running_batch", args[1] if len(args) > 1 else None)
    if running is None:
        raise ValueError("native prefill hook requires its actual running batch")
    controller = _controller(scheduler)
    token = SCOPE.set((controller, scheduler, running))
    try:
        result = original(scheduler, *args, **kwargs)
        controller.prefill_selected(result[0])
        return result
    finally:
        SCOPE.reset(token)


def add_chunked_req(original, adder, req):
    scope = SCOPE.get()
    if scope is None:
        return original(adder, req)
    controller, scheduler, running = scope
    if adder.tree_cache is not scheduler.tree_cache:
        return original(adder, req)
    if scheduler.chunked_req is not req:
        raise ValueError("native compute cofill scope changed")
    return controller.cofill(original, adder, req, scheduler, running)


def register():
    if configuration() is None:
        return
    from sglang.srt.plugins.hook_registry import HookRegistry, HookType

    for method, hook in (
        ("scheduler.Scheduler._beliefkv_before_batch_selection", before_selection),
        ("scheduler.Scheduler._should_defer_prefill", should_defer_prefill),
        ("scheduler.Scheduler._get_new_batch_prefill_raw", raw_prefill),
        ("schedule_policy.PrefillAdder.add_chunked_req", add_chunked_req),
    ):
        HookRegistry.register(HOOK_ROOT + method, hook, HookType.AROUND)
