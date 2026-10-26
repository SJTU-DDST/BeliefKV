"""Unregistered Agentix native adapter, pending live LLM/GPU qualification."""

from dataclasses import asdict
import time

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.policy.agentix import AgentixScheduler
from beliefkv.runtime.agentix_capacity import AgentixHostBacking, AgentixNativeCapacity
from beliefkv.runtime.agentix_executor import AgentixWorkerExecution
from beliefkv.runtime.agentix_lifecycle import (
    AgentixCallLifecycle, AgentixNativeScheduling,
)
from beliefkv.runtime.agentix_swap import AgentixNativeSwap
from beliefkv.runtime.paper_baseline import _isolated


class AgentixAdapter:
    def __init__(
        self, runtime, config, *, context_limit, running_limit, decode_growth_tokens,
    ):
        _isolated(runtime)
        if (
            type(context_limit) is not int or context_limit <= 0
            or type(running_limit) is not int or running_limit <= 0
            or type(decode_growth_tokens) is not int
            or decode_growth_tokens < config.decode_steps_per_schedule + 2
        ):
            raise ValueError("Agentix requires declared context, batch and pipeline growth limits")
        self.runtime, self.config = runtime, config
        self.context_limit, self.running_limit = context_limit, running_limit
        self.decode_growth_tokens = decode_growth_tokens
        self.policy = AgentixScheduler(config)
        self.policy_name = f"agentix_{config.mode}_paper_native"
        self.cache = self.execution = self.swap = self.calls = self.capacity = self.controller = None
        self.backing = None
        self.qualification = None
        self.clock_ms = lambda: time.monotonic() * 1000.

    def attach(self, cache):
        if (
            self.cache is not None or cache.tree_components != (0,) or cache.page_size != 1
            or type(cache.token_to_kv_pool_allocator.size_full) is not int
            or cache.token_to_kv_pool_allocator.size_full <= 0
        ):
            raise ValueError("Agentix requires one measured native FULL-only page-one cache")
        self.cache = cache

    def bind_scheduler(self, scheduler):
        if (
            self.cache is None or self.controller is not None or scheduler.tree_cache is not self.cache
            or scheduler.server_args.context_length != self.context_limit
            or scheduler.server_args.max_running_requests != self.running_limit
        ):
            raise ValueError("Agentix native scheduler differs from the declared data plane")
        stream = scheduler.forward_stream if scheduler.enable_overlap else scheduler.schedule_stream
        self.execution = AgentixWorkerExecution(
            self.policy, device=scheduler.device_module, compute_stream=stream,
            clock_ms=self.clock_ms,
        )
        try:
            self.execution.install_scheduler(scheduler)
            self.swap = AgentixNativeSwap(
                self.cache, maximum_requests=self.running_limit,
                drained=lambda: bool(
                    self.controller is not None and self.controller._inside_hook
                    and self.controller._pipeline_drained()
                ),
            )
            self.calls = AgentixCallLifecycle(
                self.policy, self.execution, self.swap, clock_ms=self.clock_ms,
            )
            self.capacity = AgentixNativeCapacity(
                self.swap.transfers, context_limit=self.context_limit,
                decode_growth_tokens=self.decode_growth_tokens,
            )
            self.backing = AgentixHostBacking(self.capacity, self.swap)
            self.controller = AgentixNativeScheduling(
                self.calls, scheduler, resident=self.capacity.resident,
                can_fit=self.backing.can_fit, prepare_capacity=self.backing.prepare,
                eligible=self.backing.eligible, selection_committed=self.backing.selected,
                maximum_requests=self.running_limit,
            )
            self.controller.install()
        except BaseException:
            if self.controller is not None:
                self.controller.close()
            if self.swap is not None:
                self.swap.close()
            self.execution.close()
            self.execution = self.swap = self.calls = self.capacity = self.controller = None
            self.backing = None
            raise

    def _bound(self):
        if self.controller is None:
            raise ValueError("Agentix must attach its native scheduler before receiving requests")

    def registered(self, req, key):
        self._bound()
        if self.qualification is not None and (
            self.calls.counts["registered"] >= self.qualification["max_requests"]
        ):
            raise ValueError("bounded Agentix qualification cannot accept a full replay")
        self.calls.registered(req, key)

    def tick(self, **_kwargs):
        if self.calls is not None:
            self.calls.poll()

    def events(self, events):
        self._bound()
        for event in events:
            if event.kind is Kind.WORKFLOW_END:
                self.calls.retire(event.workflow_id)

    def plan(self, native_order, *, running_batch, adder):
        self._bound()
        self.calls.poll()
        return self.controller.prefill_plan(
            native_order, semantic_revision=self.runtime.semantic_revision,
        )

    def selected(self, batch):
        self._bound()
        self.calls.selected(batch)

    def completed(self, batch, completed_ids):
        self._bound()
        self.calls.completed(batch, completed_ids)

    def requeued(self, requests, *, is_retracted):
        self._bound()
        self.calls.requeued(requests, is_retracted=is_retracted)

    def aborted(self, request_id, *, all_requests=False):
        self._bound()
        self.calls.aborted(request_id, all_requests=all_requests)

    def before_release(self, req, *, reason):
        if reason in ("decode_retraction", "decode_oom_abort"):
            raise RuntimeError(
                "Agentix cannot replace checkpointed preemption with native uncheckpointed retraction"
            )

    def status(self):
        return {
            "policy": self.policy_name, "program_identity": "root_workflow_id",
            "future_information_used": False, "native_capacity_authority_preserved": True,
            "scope": "paper_scheduler_single_backend_native_adaptation_pending_gpu_qualification",
            "declared_config": asdict(self.config),
            "context_limit": self.context_limit, "running_limit": self.running_limit,
            "decode_growth_tokens": self.decode_growth_tokens,
            "policy_state": self.policy.status(),
            "execution": self.execution.status() if self.execution is not None else None,
            "calls": self.calls.status() if self.calls is not None else None,
            "capacity": self.capacity.status() if self.capacity is not None else None,
            "backing": self.backing.status() if self.backing is not None else None,
            "swap": self.swap.status() if self.swap is not None else None,
            "native_scheduling": self.controller.status() if self.controller is not None else None,
            "complete_baseline": False, "gpu_llm_qualified": False,
            "native_uncheckpointed_retraction": "rejected",
            "qualification": self.qualification,
        }

    def close(self):
        if self.controller is not None:
            self.controller.close()
            self.calls.aborted("", all_requests=True)
            self.execution.close()
            self.swap.close()
        self.execution = self.swap = self.calls = self.capacity = self.controller = None
        self.backing = None
