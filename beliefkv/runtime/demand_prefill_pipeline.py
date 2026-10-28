"""Let resident decode progress during one native prefill batch's Host restore."""

from dataclasses import dataclass
import os
import time

from beliefkv.runtime.sglang_v0520_admission import _request_key
from beliefkv.runtime.clock_evidence import local_monotonic_clock_domain


@dataclass
class _Member:
    request: object
    key: object
    node_id: int
    receipt: object
    locked: bool = True


@dataclass
class _Pending:
    members: tuple[_Member, ...]
    consumer_index: int
    finish_event: object
    native_submit_ts_ms: float
    native_node_ids: tuple
    native_bytes: int
    native_full_tokens: int
    waiting_checks: int = 0


class DemandPrefillPipeline:
    """Own no pages or agent permits; retain the native queue and lock receipt."""

    def __init__(self, runtime, cache):
        self.runtime, self.cache = runtime, cache
        self.pending: _Pending | None = None
        self.progress_probe = os.environ.get(
            "BELIEFKV_DEMAND_PREFILL_PIPELINE_PROGRESS_PROBE", "0",
        ) == "1"

    @staticmethod
    def has_decode_work(running_batch):
        return bool(
            running_batch is not None
            and not getattr(running_batch, "is_prefill_only", True)
            and any(
                not req.finished() and getattr(req, "to_finish", None) is None
                for req in getattr(running_batch, "reqs", ())
            )
        )

    def _record(self, event, pending, **extra):
        writer = getattr(self.runtime, "_opportunity_writer", None)
        if writer is not None:
            def identity(member):
                key = member.key
                return {
                    "request_id": key.request_id, "workflow_id": key.root_workflow_id,
                    "invocation_id": key.invocation_id, "context_id": key.context_id,
                    "context_epoch": key.context_epoch, "attempt_id": key.attempt_id,
                    "session_id": key.session_id, "session_generation": key.session_generation,
                }

            writer.record({
                "event": event, "ts_ms": time.time() * 1000.,
                "monotonic_ms": time.monotonic() * 1000.,
                "monotonic_clock_domain": local_monotonic_clock_domain(),
                **identity(pending.members[0]),
                "native_batch_size": len(pending.members),
                "requests": [identity(member) for member in pending.members],
                "native_submit_ts_ms": pending.native_submit_ts_ms,
                "native_node_ids": pending.native_node_ids,
                "native_bytes": pending.native_bytes,
                "native_full_tokens": pending.native_full_tokens,
                "consumer_index": pending.consumer_index,
                **extra,
            })

    def _unlock(self, pending):
        for member in pending.members:
            if not member.locked:
                continue
            req = member.request
            if getattr(req, "lock_receipt", None) is not member.receipt:
                raise RuntimeError("staged native request lock receipt changed before handoff")
            self.cache.dec_lock_ref(member.node_id, member.receipt)
            req.lock_receipt = None
            member.locked = False

    def _live(self, pending):
        return all(
            self.runtime.visible.get(member.key.request_id) == member.key
            and _request_key(member.request, known_keys=self.runtime.visible) == member.key
            and not member.request.finished()
            and getattr(member.request, "to_finish", None) is None
            and not self.runtime._terminal(member.key)
            for member in pending.members
        )

    def _keep_decode_independent(self, running_batch):
        if getattr(running_batch, "hicache_consumer_index", -1) < 0:
            return
        resolve = getattr(self.cache, "beliefkv_prefill_load_consumer", None)
        consumer = getattr(self.cache, "_beliefkv_prefetch_consumer_index", -1)
        if callable(resolve) and type(consumer) is int and consumer >= 0 and (
            resolve(running_batch.reqs, consumer) == -1
        ):
            running_batch.hicache_consumer_index = -1
            self.runtime.counts["demand_restore_unrelated_decode_fences_cleared"] += 1

    def defer(self, waiting_queue, running_batch):
        pending = self.pending
        if pending is None:
            return False
        queued = {id(req) for req in waiting_queue}
        if not self._live(pending) or any(
            id(member.request) not in queued for member in pending.members
        ):
            self.cancel("request_no_longer_queued")
            return False
        if pending.finish_event.query():
            self._unlock(pending)
            self.pending = None
            self.runtime.counts["demand_restore_completed_before_selection"] += 1
            self._record(
                "native_demand_restore_ready", pending,
                waiting_checks=pending.waiting_checks,
            )
            return False
        admission = self.runtime._agent_admission
        if (
            not all(admission.allows(member.key) for member in pending.members)
            or not self.has_decode_work(running_batch)
        ):
            # A fresh native pass must acquire its own current lock receipt.
            # Keep the immutable transfer fence until actual admission or ACK.
            self._unlock(pending)
            return False
        self._keep_decode_independent(running_batch)
        pending.waiting_checks += 1
        self.runtime.counts["demand_restore_decode_progress_checks"] += 1
        return True

    def stage(
        self, req, *, adder, running_batch, candidate_count,
        native_load_queue_was_empty, chunked_req,
    ):
        selected = tuple(getattr(adder, "can_run_list", ()))
        if (
            self.pending is not None or not selected or candidate_count < len(selected)
            or not native_load_queue_was_empty or chunked_req is not None
            or getattr(adder, "new_chunked_req", None) is not None
            or selected[0] is not req
            or not self.has_decode_work(running_batch)
        ):
            return False
        controller = self.cache.cache_controller
        if (
            not controller.load_queue
            or getattr(controller, "_beliefkv_load_continuation", None) is not None
            or any(
                getattr(op, "beliefkv_command_id", None) is not None
                for op in controller.load_queue
            )
        ):
            return False
        members, loaded = [], 0
        for selected_req in selected:
            key = self.runtime.visible.get(getattr(selected_req, "rid", None))
            receipt = getattr(selected_req, "lock_receipt", None)
            host_hit = getattr(selected_req, "host_hit_length", 0)
            host_loaded = getattr(selected_req, "host_loaded_length", 0)
            if (
                key is None or key.session_id is None or key.session_generation is None
                or receipt is None or type(host_hit) is not int or host_hit < 0
                or type(host_loaded) is not int or host_loaded < 0
                or _request_key(selected_req, known_keys=self.runtime.visible) != key
                or not self.runtime._agent_admission.allows(key) or self.runtime._terminal(key)
                or getattr(selected_req, "beam_group", None) is not None
                or getattr(selected_req, "to_finish", None) is not None or selected_req.finished()
                or getattr(getattr(selected_req, "kv", None), "req_pool_idx", None) is not None
            ):
                return False
            loaded += host_loaded if host_hit else 0
            members.append(_Member(selected_req, key, selected_req.last_node, receipt))
        if loaded <= 0:
            return False
        before = len(controller.ack_load_queue)
        consumer = self.cache.ready_to_load_host_cache()
        if consumer < 0 or len(controller.ack_load_queue) != before + 1:
            raise RuntimeError("native demand restore did not publish one complete transfer")
        ack = controller.ack_load_queue[-1]
        if dict(ack.num_tokens_by_pool).get("kv") != loaded:
            raise RuntimeError("staged native transfer does not match the selected request")
        # Shared-prefix users still need the native layer fence after cancellation.
        self.cache._beliefkv_prefetch_consumer_index = consumer
        # This is the transfer's immutable event, not the reusable layer-ring slot.
        if ack.finish_event.query():
            return False
        pending = _Pending(
            tuple(members), consumer, ack.finish_event,
            ack.submit_ts_ms, tuple(ack.node_ids), ack.num_bytes, loaded,
        )
        self.pending = pending
        self._keep_decode_independent(running_batch)
        adder.can_run_list.clear()
        self.runtime.counts["demand_restore_staged_requests"] += len(members)
        self.runtime.counts["demand_restore_staged_native_batches"] += 1
        self._record(
            "native_demand_restore_staged", pending,
            scope="already_submitted;native_capacity_passed;still_in_native_waiting_queue",
        )
        return True

    def bind(self, batch):
        pending = self.pending
        if pending is None:
            return
        selected = {id(req) for req in batch.reqs}
        admitted = [
            member.key.request_id for member in pending.members
            if id(member.request) in selected
        ]
        if not admitted:
            return
        if any(member.locked for member in pending.members):
            raise RuntimeError("native admission reused the staged request's old lock")
        if (
            not pending.finish_event.query()
            and getattr(batch, "hicache_consumer_index", -1) < 0
        ):
            batch.hicache_consumer_index = pending.consumer_index
        self.pending = None
        self.runtime.counts["demand_restore_native_admission_handoffs"] += 1
        self._record(
            "native_demand_restore_admitted", pending,
            admitted_request_ids=admitted,
            batch_consumer_index=batch.hicache_consumer_index,
            waiting_checks=pending.waiting_checks,
        )

    def observe_completed(self, batch):
        pending = self.pending
        if not self.progress_probe or pending is None or not batch.forward_mode.is_decode():
            return
        observed = time.monotonic() * 1000.
        if pending.finish_event.query():
            return
        selected = {member.key.request_id for member in pending.members}
        completed = []
        for request in batch.reqs:
            key = _request_key(request, known_keys=self.runtime.visible)
            if key is not None and key.request_id not in selected:
                completed.append({
                    "request_id": key.request_id, "workflow_id": key.root_workflow_id,
                    "invocation_id": key.invocation_id, "context_id": key.context_id,
                    "context_epoch": key.context_epoch, "attempt_id": key.attempt_id,
                    "session_id": key.session_id, "session_generation": key.session_generation,
                    "output_tokens": len(request.output_ids),
                })
        if completed:
            self._record(
                "native_demand_restore_decode_completed_pending", pending,
                forward_iter=batch.forward_iter, batch_launch_monotonic_ms=batch.launch_ts * 1000.,
                completion_observed_monotonic_ms=observed,
                ack_pending_at_completed_batch_observation=True,
                completed_decode_requests=completed,
                scope="processed_native_decode_result_before_immutable_h2d_ack;not_kernel_timing",
            )

    def on_abort(self, recv_req, *, exact=False):
        pending = self.pending
        if pending is None:
            return
        matched = any(
            member.key.request_id == recv_req.rid if exact
            else member.key.request_id.startswith(recv_req.rid)
            for member in pending.members
        )
        if recv_req.abort_all or matched:
            self.cancel("native_abort")

    def cancel(self, reason):
        pending = self.pending
        if pending is None:
            return
        self._unlock(pending)
        self.pending = None
        self.runtime.counts["demand_restore_cancelled_requests"] += len(pending.members)
        self.runtime.counts["demand_restore_cancelled_native_batches"] += 1
        self._record("native_demand_restore_cancelled", pending, reason=reason)

    def close(self):
        self.cancel("runtime_close")


def create_pipeline(runtime, cache):
    if not getattr(runtime, "demand_prefill_pipeline_enabled", False):
        return None
    if (
        runtime.internal_comparison_mode != "full"
        or getattr(runtime, "_paper_baseline", None) is not None
        or getattr(runtime, "_agent_admission", None) is None
        or getattr(cache, "tree_components", None) != (0,)
        or getattr(cache, "buffer_pipeline", None) is not None
        or getattr(cache, "linker", None) is not None
        or getattr(cache, "enable_storage", False)
        or not getattr(cache, "enable_session_radix_cache", False)
        or getattr(cache, "host_memory_mode", None) != "cache"
        or getattr(getattr(cache, "cache_controller", None), "write_policy", None) != "write_back"
        or not callable(getattr(cache, "beliefkv_prefill_load_consumer", None))
        or not getattr(cache, "supports_beliefkv_overlap_prefetch", lambda: False)()
    ):
        raise ValueError("demand prefill pipeline requires Full agent admission and native FULL-only cache")
    result = DemandPrefillPipeline(runtime, cache)
    runtime._demand_prefill_pipeline = result
    return result
