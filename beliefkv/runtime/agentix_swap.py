"""Agentix preemption over native FULL checkpoints, D2H ACKs and Host locks.

The scheduler must drain its real forward/result pipeline before prepare and
commit. This module supplies physical mechanisms, not victim or JOIN policy.
Current scope matches the common FULL-only, page-size-one AgentX data plane.
"""

from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps

from beliefkv.runtime.paper_prefix_transfers import NativePrefixTransfers


def _cache_authority(method):
    @wraps(method)
    def invoke(self, *args, **kwargs):
        with self._authority():
            return method(self, *args, **kwargs)
    return invoke


@dataclass(frozen=True)
class _RequestProof:
    request: object
    rid: str
    input_ids: object
    output_ids: object
    input_count: int
    output_count: int
    parameters: object
    namespace: tuple
    metadata: tuple
    row: int
    committed: int
    attempt: int


@dataclass(frozen=True)
class _HostPin:
    proof: _RequestProof
    leaf: int
    creation: int | float
    snapshot: object
    lease: str


def _metadata(req):
    data = getattr(req, "beliefkv_metadata", None)
    if type(data) is not dict:
        raise ValueError("Agentix swap requires an observed request identity")
    return tuple(data.get(name) for name in (
        "root_workflow_id", "invocation_id", "context_id", "context_epoch",
    ))


class AgentixNativeSwap:
    def __init__(self, cache, *, drained, maximum_requests=24, transfers=None):
        from sglang.srt.mem_cache.unified_cache.component_type import ComponentType

        if (
            type(cache).__name__ != "UnifiedRadixCache"
            or cache.disable is not False
            or cache.tree_components != (ComponentType.FULL,)
            or cache.page_size != 1
            or not cache.is_write_back or cache.host_memory_mode != "cache"
            or cache.cache_controller is None
            or getattr(cache, "sidecar_pool_specs", ())
        ):
            raise ValueError("Agentix swap requires the qualified FULL-only page-one HiCache")
        if not callable(drained):
            raise ValueError("Agentix swap requires a live scheduler-drain check")
        if type(maximum_requests) is not int or maximum_requests <= 0:
            raise ValueError("Agentix swap requires a positive batch bound")
        self.cache, self.drained, self.maximum_requests = cache, drained, maximum_requests
        self._native_authority, self._owns_transfers = False, transfers is None
        self.transfers = transfers or NativePrefixTransfers(
            cache, can_mutate=lambda: self._native_authority, max_nodes=1024,
        )
        if self.transfers.cache is not cache:
            raise ValueError("Agentix Host ownership must use the same native cache")
        self.pending: tuple[_RequestProof, ...] = ()
        self.paused: dict[str, _HostPin] = {}
        self._preparation = {}
        self.counts = Counter()

    @contextmanager
    def _authority(self):
        previous = self._native_authority
        self._native_authority = True
        try:
            yield
        finally:
            self._native_authority = previous

    @staticmethod
    def _owner(proof):
        workflow, _, context, epoch = proof.metadata
        return workflow, context, epoch

    def _safe(self):
        if self.drained() is not True:
            raise ValueError("Agentix swap requires the native forward/result pipeline drained")

    @staticmethod
    def _namespace(req):
        return req.extra_key, req.cache_salt, req.session_id, req.session_generation

    def _proof(self, req):
        committed = req.effective_kv_committed_len()
        if (
            req.finished() or not req.kv.holds_kv or req.kv.is_kv_released
            or req.input_embeds is not None or req.session is not None
            or req.session_id is not None
            or not req.output_ids or committed != req.seqlen - 1
            or committed <= 0 or req.kv.kv_allocated_len != committed
            or req.rid in self.paused
        ):
            raise ValueError("Agentix swap requires a settled plain running decode request")
        return _RequestProof(
            req, req.rid, req.origin_input_ids, req.output_ids,
            len(req.origin_input_ids), len(req.output_ids), req.sampling_params,
            self._namespace(req), _metadata(req), req.kv.req_pool_idx,
            committed, req.cache_request_handle.attempt_id,
        )

    def _validate_tokens(self, proof, req):
        if (
            req is not proof.request or req.rid != proof.rid
            or req.origin_input_ids is not proof.input_ids
            or req.output_ids is not proof.output_ids
            or len(req.origin_input_ids) != proof.input_count
            or len(req.output_ids) != proof.output_count
            or req.sampling_params is not proof.parameters
            or self._namespace(req) != proof.namespace
            or _metadata(req) != proof.metadata
        ):
            raise ValueError("Agentix swap request tokens, sampler or identity changed")

    def _validate_active(self, proof):
        req = proof.request
        self._validate_tokens(proof, req)
        if (
            req.finished() or not req.kv.holds_kv
            or req.kv.req_pool_idx != proof.row
            or req.effective_kv_committed_len() != proof.committed
            or req.kv.kv_allocated_len != proof.committed
            or req.cache_request_handle.attempt_id != proof.attempt
        ):
            raise ValueError("Agentix swap native allocation or attempt changed")

    def _path(self, proof):
        tree = self.cache.tree_core
        current = tree.node_by_id(proof.request.last_node)
        path, tokens = [], 0
        while current is not tree.root_node:
            path.append(current)
            tokens += len(current.key)
            current = current.parent
        if tokens != proof.committed:
            raise ValueError("Agentix swap checkpoint does not cover computed KV")
        return tuple(reversed(path))

    @_cache_authority
    def prepare(self, requests, *, running_batch):
        """Checkpoint only selected running objects, then submit one D2H burst."""
        from sglang.srt.mem_cache.unified_cache.cache_action import BackupKV

        self._safe()
        reqs = tuple(requests)
        if (
            self.pending or not 1 <= len(reqs) <= self.maximum_requests
            or len({req.rid for req in reqs}) != len(reqs)
            or any(
                not any(req is item for item in running_batch.reqs)
                for req in reqs
            )
        ):
            raise ValueError("Agentix swap requires distinct actual running victims")
        proofs = tuple(self._proof(req) for req in reqs)
        for proof in proofs:
            req = proof.request
            req._refresh_fill_ids()
            req.set_extend_range(0, proof.committed)
            self.cache.cache_unfinished_req(req, chunked=True)
        nodes = {}
        for proof in proofs:
            for node in self._path(proof):
                nodes.setdefault(node.id, node)
        missing = tuple(
            node.id for node in nodes.values()
            if node.component_data[0].host_value is None
            and node.id not in self.cache.ongoing_write_through
        )
        protection = {}
        try:
            for proof in proofs:
                snapshot = self.transfers.capture(
                    proof.request.last_node, owner=self._owner(proof),
                )
                if snapshot is None or snapshot.prefix_tokens != proof.committed:
                    raise ValueError("Agentix could not bind its complete observed checkpoint")
                lease = self.transfers.retain_host_until_release(
                    snapshot, owner=self._owner(proof), require_complete=False,
                )
                if lease is None and any(
                    node.component_data[0].host_value is not None
                    and node.write_through_pending_id is None
                    for node in self._path(proof)
                ):
                    raise ValueError("Agentix could not protect its settled Host prefix")
                protection[proof.rid] = snapshot, lease
        except BaseException:
            for _, lease in protection.values():
                if lease is not None:
                    self.transfers.release_host(lease)
            raise
        self.pending = proofs
        self._preparation = protection
        try:
            if missing:
                self.cache._execute_and_commit_kv_backup(
                    BackupKV(list(missing)), beliefkv_include_mamba=False,
                    beliefkv_defer_writing=True,
                )
        finally:
            # Native enqueue owns its locks even if a later allocation fails.
            if self.cache.cache_controller.write_queue:
                self.cache.cache_controller.start_writing()
        self.counts["prepared_requests"] += len(proofs)
        self.counts["unique_checkpoint_nodes"] += len(nodes)
        self.counts["missing_backup_nodes"] += len(missing)
        return {
            "requests": tuple(proof.rid for proof in proofs),
            "unique_checkpoint_tokens": sum(len(node.key) for node in nodes.values()),
            "missing_backup_nodes": len(missing),
            "device_capacity_released": False,
        }

    @_cache_authority
    def commit(self):
        """After native ACK processing, release rows and only safe backed extents."""
        from sglang.srt.mem_cache.common import release_kv_cache

        self._safe()
        if not self.pending:
            return None
        nodes, paths = {}, {}
        for proof in self.pending:
            self._validate_active(proof)
            path = self._path(proof)
            paths[proof.rid] = path
            for depth, node in enumerate(path):
                nodes[node.id] = depth, node
        if any(
            node.write_through_pending_id is not None
            or node.load_back_pending_id is not None
            or node.id in self.cache.ongoing_write_through
            or node.id in self.cache.ongoing_load_back
            for _, node in nodes.values()
        ):
            self.counts["waiting_native_ack"] += 1
            return None
        missing = [
            node for _, node in nodes.values() if node.component_data[0].host_value is None
        ]
        if missing:
            self.counts["backup_declined"] += 1
            available = self.cache.cache_controller.mem_pool_host.available_size()
            return {
                "status": "backup_declined", "requests": (),
                "allocator_available_increase": 0,
                "host_available_tokens": available,
                "missing_host_tokens": sum(len(node.key) for node in missing),
                "host_capacity_refused": available < len(missing[0].key),
            }
        pins = []
        try:
            for proof in self.pending:
                leaf = paths[proof.rid][-1]
                snapshot, _ = self._preparation[proof.rid]
                lease = self.transfers.retain_host_until_release(
                    snapshot, owner=self._owner(proof),
                )
                if lease is None:
                    raise ValueError("Agentix could not protect its complete paused Host prefix")
                pins.append(_HostPin(proof, leaf.id, leaf.creation_time, snapshot, lease))
        except BaseException:
            for pin in reversed(pins):
                self.transfers.release_host(pin.lease)
            raise
        for _, lease in self._preparation.values():
            if lease is not None:
                self.transfers.release_host(lease)
        self._preparation.clear()
        before = self.cache.token_to_kv_pool_allocator.available_size()
        proofs = self.pending
        for pin in pins:
            self.paused[pin.proof.rid] = pin
        for proof in proofs:
            release_kv_cache(proof.request, self.cache, is_insert=False)
            proof.request.reset_for_retract()
        tracker = {}
        for _, node in sorted(nodes.values(), key=lambda pair: pair[0], reverse=True):
            full = node.component_data[0]
            if (
                node in self.cache.tree_core.evictable_device_leaves
                and full.value is not None and full.host_value is not None
                and full.lock_ref == 0
                and node.write_through_pending_id is None
                and node.load_back_pending_id is None
            ):
                self.cache._demote(node.id, tracker)
        available = self.cache.token_to_kv_pool_allocator.available_size() - before
        self.pending = ()
        self.counts["paused_requests"] += len(proofs)
        self.counts["demoted_full_tokens"] += tracker.get(0, 0)
        self.counts["allocator_available_increase"] += available
        return {
            "status": "paused", "requests": tuple(proof.request for proof in proofs),
            "demoted_full_tokens": tracker.get(0, 0),
            "allocator_available_increase": available,
        }

    @_cache_authority
    def resumed(self, req):
        from sglang.srt.mem_cache.unified_cache.component_type import ComponentType

        pin = self.paused.get(req.rid)
        if pin is None:
            return False
        self._validate_tokens(pin.proof, req)
        if not req.kv.holds_kv or len(req.prefix_indices) < pin.proof.committed:
            raise ValueError("Agentix paused prefix was not restored at actual native admission")
        receipt = req.lock_receipt
        if (
            req.last_node != pin.leaf or receipt.node_id != pin.leaf
            or ComponentType.FULL in receipt.skipped_lock_components
            or req.kv.kv_allocated_len < pin.proof.committed
            or req.effective_kv_committed_len() < pin.proof.committed
        ):
            raise ValueError("Agentix resumption requires the actual native consumer lock and allocation")
        self.release(req.rid)
        self.counts["resumed_requests"] += 1
        return True

    @_cache_authority
    def release(self, request_id):
        pin = self.paused.get(request_id)
        if pin is None:
            return False
        leaf = self.cache.tree_core.node_by_id(pin.leaf)
        if leaf.creation_time != pin.creation:
            raise ValueError("Agentix paused Host-copy identity changed")
        if self.transfers.resolve(pin.snapshot, owner=self._owner(pin.proof)) is None:
            raise ValueError("Agentix paused Host prefix ownership changed")
        self.transfers.release_host(pin.lease)
        del self.paused[request_id]
        return True

    @_cache_authority
    def cancel_preparation(self):
        """Keep request-owned KV and native DMA locks; do not claim a preemption."""
        if self.pending:
            for _, lease in self._preparation.values():
                if lease is not None:
                    self.transfers.release_host(lease)
            self._preparation.clear()
            self.pending = ()
            self.counts["cancelled_preparations"] += 1

    @_cache_authority
    def close(self):
        for pin in self.paused.values():
            self.transfers.release_host(pin.lease)
        for _, lease in self._preparation.values():
            if lease is not None:
                self.transfers.release_host(lease)
        self.paused.clear()
        self._preparation.clear()
        self.pending = ()
        if self._owns_transfers:
            self.transfers.close()

    def status(self):
        return {
            "pending_requests": len(self.pending), "paused_host_pins": len(self.paused),
            "counts": dict(self.counts),
            "victim_selection_policy": "external_paper_scheduler",
            "request_ids_outputs_and_sampling_preserved": True,
            "host_ownership": "split_safe_complete_prefix_until_native_resumption_or_cancellation",
            "native_capacity_authority_preserved": True,
            "runtime_scheduler_hook_installed": False,
        }
