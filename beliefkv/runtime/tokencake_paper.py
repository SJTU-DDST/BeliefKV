"""TokenCake paper temporal policy on the separately declared observed graph.

Algorithm 1 uses observed decode throughput and a fixed serving generation
budget. Equations 3-4 grant real token slots; this is not partial-layer loading.
The public artifact's release-only temporal policy remains a separate adapter.
"""

from dataclasses import dataclass, field
import math

from beliefkv.policy.tokencake import (
    Benefit, DecisionReason, UploadCandidate, WaitingWork,
    first_fit_work, paper_upload_reservations, request_score,
)
from beliefkv.policy.tokencake_lifecycle import TokenCakePaperLifecycleRegistry
from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.runtime.sglang_v0520_admission import _request_key
from beliefkv.runtime.tokencake_capacity import NativeCapacityNotReady
from beliefkv.runtime.tokencake_lifecycle import TokenCakeNativeLifecycle


@dataclass
class _Checkpoint:
    prefix: object
    metadata: object
    finish_at: float
    expires_at: float
    created_at: float
    host_ready: bool = False
    finished: bool = False
    host_leases: set = field(default_factory=set)
    retry_step: int = 0


class TokenCakePaperLifecycle(TokenCakeNativeLifecycle):
    registry_type = TokenCakePaperLifecycleRegistry

    def __init__(self, adapter, **settings):
        super().__init__(adapter, **settings)
        self.checkpoints = {}
        self.decode_rate = None
        self._decode_at = None
        self._decode_outputs = {}
        self.last_first_fit = None

    def observe_decode(self, batch):
        mode = getattr(batch, "forward_mode", None)
        if mode is None or not mode.is_decode():
            return
        current = {
            req.rid: len(req.output_ids) for req in getattr(batch, "reqs", ())
        }
        now = self.adapter.clock()
        produced = sum(
            max(0, count - self._decode_outputs[rid])
            for rid, count in current.items() if rid in self._decode_outputs
        )
        if produced and self._decode_at is not None and now > self._decode_at:
            sample = produced / (now - self._decode_at)
            first_sample = self.decode_rate is None
            self.decode_rate = sample if self.decode_rate is None else .5 * sample + .5 * self.decode_rate
            self.counts["decode_throughput_samples"] += 1
            if first_sample:
                for record in self.registry.records.values():
                    record.backoff_signature = None
                    record.next_evaluation_step = 0
        self._decode_at, self._decode_outputs = now, current

    def _waiting_work(self):
        ledger = self.adapter.capacity
        waiting = self.adapter._ordered([
            item.request for rid, item in self.adapter.requests.items()
            if rid not in ledger.requests and not self.runtime._terminal(item.key)
        ])
        result = []
        for req in waiting:
            try:
                probe = ledger.probe(req)
            except NativeCapacityNotReady:
                continue
            shared = sum(count for identity, count in probe.extents if identity in ledger.extents)
            computed = sum(count for _, count in probe.extents) + probe.host_prefix_tokens
            uncached = max(0, len(req.origin_input_ids) + len(req.output_ids) - computed)
            incremental = max(0, computed - shared) + min(
                uncached, self.chunked_prefill_tokens, self.prefill_token_budget,
            )
            item = self.adapter.requests[req.rid]
            eligible = (
                len(ledger.requests) < self.running_limit
                and self.cache.req_to_token_pool.available_size() > 0
                and ledger._capacity_view(probe.external_prefix_tokens).can_admit(
                    item.metadata.agent_type, ledger.admission_demand(probe),
                    score=self.adapter.scores[req.rid],
                )
            )
            result.append(WaitingWork(
                req.rid, incremental,
                uncached + max(0, self.adapter.generation_budget - len(req.output_ids)),
                eligible,
            ))
        return result

    def _benefit(self, record, pressure, **values):
        if pressure.usage < self.settings.min_gpu_usage:
            return Benefit(DecisionReason.LOW_PRESSURE, 0.)
        self.last_first_fit = first_fit_work(
            self._waiting_work(), freed_blocks=values["count"],
            window_seconds=values["remaining"] - values["transfer"],
            observed_tokens_per_second=self.decode_rate or 0.,
        )
        if self.last_first_fit is None:
            self.counts["paper_first_fit_rejected"] += 1
            return Benefit(DecisionReason.NO_WAITING_DEMAND, 0.)
        self.counts["paper_first_fit_selected"] += 1
        return super()._benefit(record, pressure, **values)

    def _on_backup_issued(self, identity, record):
        self.checkpoints.setdefault(identity, _Checkpoint(
            record.snapshot, record.metadata, record.started_at + record.predicted_duration,
            record.safety_deadline, record.completed_at,
        ))

    def _on_preserved(self, identity, record):
        checkpoint = self.checkpoints.get(identity)
        if checkpoint is None:
            return
        checkpoint.host_ready = True
        snapshot = checkpoint.prefix.snapshot
        self.counts["paper_offload_demoted_tokens"] += self.transfers.demote(
            snapshot, owner=snapshot.owner, max_tokens=snapshot.prefix_tokens,
        )
        self.adapter.capacity.refresh()

    def _finish(self, event):
        identity = self.tools.get((event.invocation_id, self._tool_id(event)))
        generation, checkpoint = self.generations.get(identity), self.checkpoints.get(identity)
        if generation is not None and len(generation.tool_ids) == 1 and checkpoint is not None:
            if checkpoint.host_ready:
                record = self.registry.records[identity]
                checkpoint.host_leases.update(record.retained)
                record.retained.clear()
                checkpoint.finished = True
            else:
                self._drop(identity)
        super()._finish(event)

    def _drop(self, identity):
        checkpoint = self.checkpoints.pop(identity, None)
        if checkpoint is None:
            return
        self.adapter.capacity.release_reservation(identity)
        self.registry.manager.release(checkpoint.host_leases)
        record = self.registry.records.get(identity)
        if record is not None:
            self.registry.release(record)
        self._pending.discard(identity)
        self.counts["paper_checkpoints_released"] += 1

    def _cleanup_checkpoints(self):
        now = self.adapter.clock()
        for identity, checkpoint in tuple(self.checkpoints.items()):
            owner = checkpoint.prefix.snapshot.owner
            workflow = self.runtime.graph.workflows.get(owner[0])
            context = self.runtime.graph.contexts.get(owner[1])
            record = self.registry.records.get(identity)
            if (
                now >= checkpoint.expires_at or record is None and not checkpoint.finished
                or workflow is not None and workflow.end_ts_ms is not None
                or context is not None and context.epoch != owner[2]
                or record is not None and record.terminal_at is not None and not (
                    checkpoint.finished and record.terminal_cause == "finished"
                )
            ):
                self._drop(identity)

    def events(self, events):
        super().events(events)
        for event in events:
            if event.kind in (Kind.INVOCATION_CANCEL, Kind.CONTEXT_COMPACT, Kind.CONTEXT_ADVANCE) or (
                event.kind is Kind.RETURN and event.attributes.get("context_retired") is True
            ):
                context = self._event_context(event)
                for identity, checkpoint in tuple(self.checkpoints.items()):
                    if checkpoint.prefix.snapshot.owner[:2] == context:
                        self._drop(identity)
        self._cleanup_checkpoints()

    def aborted(self, request_id, *, all_requests=False):
        super().aborted(request_id, all_requests=all_requests)
        for identity in tuple(self.checkpoints):
            if all_requests or identity.startswith(request_id):
                self._drop(identity)

    def _critical_waiting_demand(self):
        ledger = self.adapter.capacity
        total = 0
        for rid, item in self.adapter.requests.items():
            if rid in ledger.requests or item.metadata.agent_type not in ledger.window.plan.critical:
                continue
            try:
                total += ledger.admission_demand(ledger.probe(item.request))
            except NativeCapacityNotReady:
                total += max(0, ledger._target(item.request) - item.request.kv.kv_allocated_len)
        return total

    def _advance_uploads(self):
        now, block = self.adapter.clock(), self.block_tokens
        ledger = self.adapter.capacity
        ledger.refresh()
        ready, scores = {}, {}
        for identity, checkpoint in self.checkpoints.items():
            if not checkpoint.host_ready or self.step < checkpoint.retry_step:
                continue
            snapshot = checkpoint.prefix.snapshot
            path = self.transfers.resolve(snapshot, owner=snapshot.owner)
            if path is None:
                continue
            if any(
                node.load_back_pending_id is not None or node.write_through_pending_id is not None
                for node in path
            ):
                continue
            missing = sum(len(node.key) for node in path if node.component_data[0].value is None)
            lead = max(.1, 1.25 * self.estimate_transfer(missing, "h2d"))
            if not missing or not checkpoint.finished and now < checkpoint.finish_at - lead:
                continue
            scores[identity] = request_score(checkpoint.metadata, checkpoint.created_at, now)
            ready[identity] = missing, lead
        maximum = max(scores.values(), default=1)
        candidates = [
            UploadCandidate(
                identity, math.ceil(missing / block), math.ceil(missing / block),
                max(0., scores[identity]) / max(1, maximum),
                min(1., max(0., 1 - (self.checkpoints[identity].finish_at - now) / lead)),
                self.checkpoints[identity].finish_at,
            )
            for identity, (missing, lead) in ready.items()
        ]
        grants = paper_upload_reservations(
            candidates,
            gpu_free=max(0, min(
                self.cache.token_to_kv_pool_allocator.available_size(),
                ledger.window.uncommitted,
            )) // block,
            critical_waiting_demand=math.ceil(self._critical_waiting_demand() / block),
            shared_free=ledger.window.shared_available // block,
        )
        for identity, blocks in grants.items():
            checkpoint = self.checkpoints[identity]
            snapshot, observed = checkpoint.prefix.snapshot, checkpoint.prefix.key
            amount = min(ready[identity][0], blocks * block)
            if not ledger.can_reserve_prefix(
                snapshot, owner=snapshot.owner, agent_type=checkpoint.metadata.agent_type,
                score=scores[identity], new_tokens=amount,
            ):
                self.counts["paper_upload_spatial_declined"] += 1
                continue
            issued = self.transfers.restore_prefix(
                snapshot, owner=snapshot.owner, observed_key=observed, available_tokens=amount,
            )
            if not issued:
                continue
            path = self.transfers.resolve(snapshot, owner=snapshot.owner)
            last = None
            for node in path:
                if node.component_data[0].value is None:
                    break
                last = node
            partial = self.transfers.capture(last.id, owner=snapshot.owner)
            ledger.reserve_prefix(
                identity, partial, owner=snapshot.owner,
                agent_type=checkpoint.metadata.agent_type, score=scores[identity],
            )
            self.counts["paper_upload_grants"] += 1
            self.counts["paper_upload_tokens"] += sum(item[2] for item in issued)

    def consumer_admitted(self, req):
        key = _request_key(req)
        if key is None:
            return
        owner = self._owner(key)
        for identity, checkpoint in tuple(self.checkpoints.items()):
            if checkpoint.prefix.snapshot.owner != owner:
                continue
            claim = self.adapter.capacity.requests.get(req.rid)
            if claim is None:
                continue
            consumed = self.adapter.capacity.consume_reservation(identity, req)
            if not consumed:
                path = self.transfers.resolve(checkpoint.prefix.snapshot, owner=owner)
                if path is None or not {
                    (node.id, node.creation_time) for node in path
                } <= claim.prefix:
                    continue
            self._drop(identity)
            self.counts["paper_upload_consumers"] += 1

    def release_for_pressure(self, *, excluded_owner=None):
        candidates = [
            identity for identity, checkpoint in self.checkpoints.items()
            if identity in self.adapter.capacity.reservations
            and checkpoint.prefix.snapshot.owner != excluded_owner
        ]
        if not candidates:
            return False
        identity = min(candidates, key=lambda key: (
            self.checkpoints[key].metadata.importance, self.checkpoints[key].finished,
            -self.checkpoints[key].finish_at, key,
        ))
        self.adapter.capacity.release_reservation(identity)
        self.checkpoints[identity].retry_step = self.step + 8
        self.counts["paper_upload_pressure_releases"] += 1
        return True

    def tick(self):
        self._cleanup_checkpoints()
        super().tick()
        self._advance_uploads()

    def native_reset_succeeded(self):
        for identity in tuple(self.checkpoints):
            self._drop(identity)
        super().native_reset_succeeded()

    def close(self):
        for identity in tuple(self.checkpoints):
            self._drop(identity)
        super().close()
