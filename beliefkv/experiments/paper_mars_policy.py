"""Bind the unmodified MARS scheduling policy to the common SGLang engine."""

from __future__ import annotations

from abc import ABC, abstractmethod
import ast
from collections import defaultdict, deque
from enum import Enum, auto
from functools import lru_cache
import heapq
import logging
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace
from typing import Iterable, Iterator, Optional


AUTHOR_COMMIT = "d2e34d007ad2013b4a2513efdb9b2f9b606369f6"
AUTHOR_DIR = "vllm-continuum/vllm/v1/core/sched"
STATE = {
    "_mars_level_by_req_id", "_mars_last_scheduled_by_req_id",
    "_mars_level_quanta", "_mars_aging_interval_s", "_mars_active_window_size",
    "_mars_overflow",
}
METHODS = {
    "add_request", "schedule", "_update_after_schedule",
    "_mars_remaining_level", "_mars_effective_level", "_mars_quantum_for_level",
    "_mars_reorder_running", "_mars_on_scheduled", "_mars_admit_overflow",
}


class RequestStatus(Enum):
    WAITING = auto()
    RUNNING = auto()
    PREEMPTED = auto()
    WAITING_FOR_FSM = auto()
    WAITING_FOR_REMOTE_KVS = auto()


class Request:
    def __init__(self, native, arrival_time):
        self.native = native
        self.request_id = native.rid
        self.arrival_time = arrival_time
        self.num_prompt_tokens = len(native.origin_input_ids)
        self.job_id = (getattr(native, "beliefkv_metadata", None) or {}).get("root_workflow_id")
        self.num_computed_tokens = 0
        self.num_cached_tokens = -1
        self.num_output_placeholders = 0
        self.status = RequestStatus.WAITING
        self.has_encoder_inputs = False
        self.spec_token_ids = ()

    @property
    def num_tokens(self):
        return self.num_prompt_tokens + len(self.native.output_ids)

    @property
    def num_tokens_with_spec(self):
        return self.num_tokens


def _author_module(checkout, name):
    path = f"{AUTHOR_DIR}/{name}.py"
    source = subprocess.check_output(
        ("git", "-C", str(checkout), "show", f"{AUTHOR_COMMIT}:{path}"),
    )
    return ast.parse(source, filename=f"{AUTHOR_COMMIT}:{path}")


def _assignment_name(statement):
    if isinstance(statement, ast.AnnAssign):
        target = statement.target
    elif isinstance(statement, ast.Assign) and len(statement.targets) == 1:
        target = statement.targets[0]
    else:
        return None
    if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
        return target.attr if target.value.id == "self" else None
    return None


class _UnusedAuthorServices:
    """Services used for bookkeeping by vLLM's non-MARS integrations."""

    def request_arrives(self, _req):
        pass

    def request_evicted_from_running_queue(self, _req):
        pass

    def request_waiting_to_running(self, _req, **_record):
        pass

    def request_evicted_to_running(self, _req, **_record):
        pass

    def free(self, _req):
        pass

    def get_freed_mm_hashes(self):
        return ()


class _SchedulerBridge:
    def __init__(self, cache, *, block_size, max_tokens, max_reqs, max_model_len):
        self.policy = self.scheduling_policy.MARS
        self.waiting = self.queue_factory(self.policy)
        self.running = []
        self.requests = {}
        self.scheduler_config = SimpleNamespace(
            long_prefill_token_threshold=0, chunked_prefill_enabled=True,
        )
        self.max_num_scheduled_tokens = max_tokens
        self.max_num_running_reqs = max_reqs
        self.max_model_len = max_model_len
        self.block_size = block_size
        self.kv_cache_manager = cache
        self.kv_cache_config = SimpleNamespace(kv_cache_groups=(None,))
        self.encoder_cache_manager = _UnusedAuthorServices()
        self.max_num_encoder_input_tokens = 0
        self.tool_call_estimator = _UnusedAuthorServices()
        self.interception_recorder = _UnusedAuthorServices()
        self._continuum_program_served = defaultdict(int)
        self._continuum_program_k_history = {}
        self._continuum_evicted_at = {}
        self._continuum_queue_delay_samples = deque(maxlen=200)
        self.running_job_id_first_entry_time = {}
        self.finished_req_ids = set()
        self.pinned_requests = ()
        self.num_lookahead_tokens = 0
        self.log_stats = False
        self.connector = None
        self.lora_config = None
        self.is_encoder_decoder = False
        self._init_mars()

    def unpin_requests_regular(self):
        assert not self.pinned_requests

    def get_grammar_bitmask(self, _running, _spec_tokens):
        return (), None

    def _make_cached_request_data(self, *_args):
        return None

    def _make_forward_step_profile_meta(self, *_args):
        return None

    def retire(self, req):
        if req in self.running:
            self.running.remove(req)
        else:
            self.waiting.remove_request(req)
            if req in self._mars_overflow:
                self._mars_overflow.remove_request(req)
        rid = req.request_id
        self.requests.pop(rid)
        self._mars_level_by_req_id.pop(rid, None)
        self._mars_last_scheduled_by_req_id.pop(rid, None)
        self._continuum_evicted_at.pop(rid, None)


@lru_cache(maxsize=None)
def author_scheduler(checkout: Path):
    queue = _author_module(checkout, "request_queue")
    queue_names = {
        "SchedulingPolicy", "RequestQueue", "FCFSRequestQueue",
        "MarsRequestQueue", "create_request_queue",
    }
    definitions = [node for node in queue.body if getattr(node, "name", None) in queue_names]
    scheduler = next(
        node for node in _author_module(checkout, "scheduler").body
        if isinstance(node, ast.ClassDef) and node.name == "Scheduler"
    )
    init = next(node for node in scheduler.body if getattr(node, "name", None) == "__init__")
    statements = [node for node in init.body if _assignment_name(node) in STATE]
    methods = [node for node in scheduler.body if getattr(node, "name", None) in METHODS]
    if (
        {node.name for node in definitions} != queue_names
        or {_assignment_name(node) for node in statements} != STATE
        or {node.name for node in methods} != METHODS
    ):
        raise ValueError("MARS author source bindings changed")
    init_mars = ast.parse("def _init_mars(self): pass").body[0]
    init_mars.body = statements
    module = ast.fix_missing_locations(ast.Module(
        body=definitions + [init_mars] + methods, type_ignores=[],
    ))
    scope = {
        "ABC": ABC, "abstractmethod": abstractmethod, "deque": deque, "Enum": Enum,
        "heapq": heapq, "Iterator": Iterator, "Iterable": Iterable, "Optional": Optional,
        "Request": Request, "RequestStatus": RequestStatus, "time": time,
        "logger": logging.getLogger(__name__),
        "SchedulerOutput": SimpleNamespace,
        "NewRequestData": SimpleNamespace(from_request=lambda req, _blocks: req),
    }
    exec(compile(module, f"{AUTHOR_COMMIT}:MARS", "exec"), scope)
    bindings = {name: scope[name] for name in METHODS | {"_init_mars"}}
    bindings["scheduling_policy"] = scope["SchedulingPolicy"]
    bindings["queue_factory"] = staticmethod(scope["create_request_queue"])
    return type("MarsAuthorScheduler", (_SchedulerBridge,), bindings)
