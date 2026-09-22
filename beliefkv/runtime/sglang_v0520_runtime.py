"""Bounded semantic admission for v0.5.20's native FULL/MAMBA scheduler.

No physical action or capacity certificate is issued by this runtime.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import TYPE_CHECKING

from beliefkv.control.causal_graph import RuntimeCausalContextGraph
from beliefkv.policy.causal_frontier import CausalFrontierScheduler
from beliefkv.runtime.event_channel import RuntimeEventDatagramServer
from beliefkv.runtime.sglang_v0520_admission import (
    _request_key,
    NativePrefillPlan,
    PrefillCandidateKey,
    compile_native_prefill_plan,
)

if TYPE_CHECKING:
    from beliefkv.core.events import RuntimeEvent


class NativeAdmissionRuntime:
    """Rebind causal order to live request identities at each prefill safe point."""

    def __init__(self, *, event_socket_path: str | None = None) -> None:
        self.semantic_revision = 0
        self.graph = RuntimeCausalContextGraph(strict_timestamps=False)
        self.frontier = CausalFrontierScheduler(self.graph)
        self.visible: dict[str, PrefillCandidateKey] = {}
        self.counts: Counter[str] = Counter()
        self.event_server = (
            RuntimeEventDatagramServer(event_socket_path, self.on_events)
            if event_socket_path
            else None
        )

    def close(self) -> None:
        if self.event_server is not None:
            self.event_server.close()
            self.event_server = None

    def on_events(self, events: tuple[RuntimeEvent, ...]) -> None:
        try:
            self.graph.apply_batch(events, atomic=False)
        except Exception:
            # A partially applied batch cannot remain a scheduling authority.
            self.graph = RuntimeCausalContextGraph(strict_timestamps=False)
            self.frontier = CausalFrontierScheduler(self.graph)
            self.semantic_revision += 1
            self.counts["causal_mirror_discarded"] += 1
            raise
        else:
            self.semantic_revision += 1

    def scheduler_step(self) -> None:
        if self.event_server is not None:
            self.event_server.drain(max_messages=16)

    def register_visible_request(self, req: object) -> bool:
        key = _request_key(req)
        if key is None or self._terminal(key):
            self.counts["invalid_request_identity"] += 1
            return False
        if key.request_id in self.visible:
            raise ValueError(f"duplicate visible request: {key.request_id}")
        self.visible[key.request_id] = key
        self.semantic_revision += 1
        return True

    def _terminal(self, key: PrefillCandidateKey) -> bool:
        workflow = self.graph.workflows.get(key.root_workflow_id)
        invocation = self.graph.invocations.get(key.invocation_id)
        context = self.graph.contexts.get(key.context_id)
        return bool(
            (workflow is not None and workflow.end_ts_ms is not None)
            or (
                invocation is not None
                and invocation.workflow_id == key.root_workflow_id
                and invocation.state.terminal
            )
            or (
                context is not None
                and context.workflow_id == key.root_workflow_id
                and context.epoch > key.context_epoch
            )
        )

    def terminal_waiting_request_ids(self, native_order: Sequence[object]) -> tuple[str, ...]:
        return tuple(
            key.request_id
            for req in native_order
            if (key := _request_key(req)) is not None
            and key.request_id in self.visible
            and self._terminal(key)
        )

    def retire_terminal_request(self, request_id: str) -> None:
        if request_id in self.visible:
            del self.visible[request_id]
            self.semantic_revision += 1
            self.counts["terminal_waiting_aborted"] += 1

    def on_requests_requeued(
        self, requests: Sequence[object], *, is_retracted: bool
    ) -> None:
        for req in requests:
            key = _request_key(req)
            if key is None or key.request_id not in self.visible:
                raise ValueError("requeued request has no live tagged identity")
            self.visible[key.request_id] = key
            self.semantic_revision += 1

    def _causal_rank(
        self,
        req: object,
        index: int,
        ready_ranks: dict[str, tuple[int, int]],
    ) -> tuple[int, int, int]:
        key = _request_key(req)
        if key is None:
            return (5, 0, index)
        invocation = self.graph.invocations.get(key.invocation_id)
        context = self.graph.contexts.get(key.context_id)
        if (
            invocation is None
            or context is None
            or invocation.workflow_id != key.root_workflow_id
            or context.workflow_id != key.root_workflow_id
            or invocation.context_id != key.context_id
            or context.epoch != key.context_epoch
        ):
            return (5, 0, index)
        causal_class, depth = ready_ranks.get(key.invocation_id, (5, 0))
        return (causal_class, depth, index)

    def plan_native_prefill(
        self, native_order: Sequence[object], *, running_batch: object, adder: object
    ) -> NativePrefillPlan:
        # Only sort tagged slots; PrefillAdder retains all FULL/MAMBA decisions.
        # Unknown or stale causal state keeps native order.
        tagged = []
        for index, req in enumerate(native_order):
            if getattr(req, "beliefkv_metadata", None) is None:
                continue
            if len(tagged) == 512:
                break
            tagged.append((index, req))
        if len(native_order) > 512:
            self.counts["candidate_bound"] += 1
        workflows = {
            key.root_workflow_id
            for _, req in tagged
            if (key := _request_key(req)) is not None
            and self.visible.get(key.request_id) == key
            and key.root_workflow_id in self.graph.workflows
        }
        ready_ranks = {
            item.invocation_id: (item.score[0], -item.unblock_depth)
            for workflow_id in workflows
            for item in self.frontier.candidates(workflow_id)
        }
        ordered = sorted(
            tagged,
            key=lambda pair: self._causal_rank(pair[1], pair[0], ready_ranks),
        )
        return compile_native_prefill_plan(
            [
                req for _, req in ordered
                if (key := _request_key(req)) is not None
                and self.visible.get(key.request_id) == key
                and not self._terminal(key)
            ],
            semantic_revision=self.semantic_revision,
        )

    def on_prefill_selection(self, rejected: tuple[tuple[str, str], ...]) -> None:
        self.counts.update(reason for _, reason in rejected)

    def on_prefill_candidate_result(
        self, req: object, *, admitted: bool, result: str
    ) -> None:
        if getattr(req, "beliefkv_metadata", None) is not None:
            self.counts["native_admitted" if admitted else f"native_{result}"] += 1

    def on_batch_selected(self, batch: object) -> None:
        pass

    def on_batch_completed(self, batch: object) -> None:
        for req in batch.reqs:
            if req.rid in self.visible and req.finished():
                del self.visible[req.rid]
                self.semantic_revision += 1

    def on_abort_request(self, abort: object) -> None:
        removed = [
            rid
            for rid in self.visible
            if getattr(abort, "abort_all", False) or rid.startswith(abort.rid)
        ]
        for rid in removed:
            del self.visible[rid]
            self.semantic_revision += 1

    def running_batch_retraction_barrier_required(self, batch: object) -> bool:
        return False

    def on_running_batch_retraction_barrier_drained(self, batch: object) -> None:
        pass

    def plan_running_batch_retraction(self, batch: object) -> None:
        return None
