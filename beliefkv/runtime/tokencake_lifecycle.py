"""TokenCake public preservation on observed native generation/stall events.

Host ownership expires at the public release deadline. This path leaves HBM
reclamation and demand H2D to the native engine; it issues no predicted upload.
"""

from bisect import bisect_right
from collections import Counter, deque
from dataclasses import dataclass, replace
import math

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.policy.tokencake import (
    DecisionReason, OffloadSettings, Pressure, public_offload_benefit,
)
from beliefkv.policy.tokencake_lifecycle import LifecycleEvent, TokenCakeLifecycleRegistry
from beliefkv.runtime.tokencake_capacity import NativeCapacityNotReady


@dataclass(frozen=True)
class _Prefix:
    snapshot: object
    key: object


@dataclass
class _Generation:
    key: object
    declared: frozenset
    tool_ids: set


class _HostRetention:
    def __init__(self, transfers):
        self.transfers = transfers

    def retain(self, keys):
        return {
            key for key in keys
            if key in self.transfers._host_leases
            and self.transfers._host_leases[key].deadline_s > self.transfers.clock()
        }

    def release(self, keys):
        for key in keys:
            self.transfers.release_host(key)


class TokenCakeNativeLifecycle:
    registry_type = TokenCakeLifecycleRegistry

    def __init__(
        self, adapter, *, running_limit, prefill_token_budget,
        chunked_prefill_tokens, policy_block_tokens, eviction_window_tokens,
    ):
        if any(type(value) is not int or value <= 0 for value in (
            running_limit, prefill_token_budget, chunked_prefill_tokens,
            policy_block_tokens, eviction_window_tokens,
        )):
            raise ValueError("TokenCake requires declared positive native scheduling limits")
        self.adapter, self.runtime = adapter, adapter.runtime
        self.transfers, self.cache = adapter.transfers, adapter.cache
        self.running_limit, self.prefill_token_budget = running_limit, prefill_token_budget
        self.chunked_prefill_tokens = chunked_prefill_tokens
        self.block_tokens, self.eviction_window_tokens = policy_block_tokens, eviction_window_tokens
        self.registry = self.registry_type(
            manager=_HostRetention(self.transfers), clock=adapter.clock,
        )
        self.settings = OffloadSettings()
        self.generations, self.latest, self.results, self.tools = {}, {}, {}, {}
        self._pending = set()
        self._recent_stores = deque()
        self.step = 0
        self.bandwidth = {"d2h": 12e9, "h2d": 12e9}
        self.counts = Counter()

    @staticmethod
    def _owner(key):
        return key.root_workflow_id, key.context_id, key.context_epoch

    def registered(self, req, item):
        identity = item.key.request_id
        self.registry.associate(identity, item.metadata)
        context = item.key.root_workflow_id, item.key.context_id
        previous = self.generations.get(self.latest.get(context))
        if previous is not None and previous.key.context_epoch != item.key.context_epoch:
            self.registry.terminate(previous.key.request_id, "reset")
        self.latest[context] = identity
        self.generations[identity] = _Generation(
            item.key, frozenset((req.beliefkv_metadata.get("tokencake") or {}).keys()), set(),
        )

    def _capture(self, req, key):
        from sglang.srt.mem_cache.base_prefix_cache import MatchPrefixParams
        from sglang.srt.mem_cache.radix_cache import RadixKey

        req._refresh_fill_ids()
        tokens = req.full_untruncated_fill_ids
        observed = RadixKey(
            tokens, req.extra_key, cache_salt=req.cache_salt,
            limit=req._compute_max_prefix_len(len(tokens)),
        )
        match = self.cache.match_prefix(MatchPrefixParams(key=observed, cow_mamba=False))
        snapshot = self.transfers.capture(match.best_match_node, owner=self._owner(key))
        return _Prefix(snapshot, observed) if snapshot is not None else None

    def completed(self, req):
        generation = self.generations.get(req.rid)
        if generation is None:
            return
        reason = getattr(req, "finished_reason", None)
        detail = reason.to_json() if reason is not None else {}
        cause = "completed"
        if detail.get("type") == "abort":
            cause = "error" if (detail.get("status_code") or 0) >= 500 else "aborted"
        record = self.registry.records.get(req.rid)
        self.registry.generation_finished(
            req.rid, cause,
            self._capture(req, generation.key)
            if cause == "completed" and record is not None and record.terminal_at is None else None,
        )
        self.counts["generation_completions"] += 1

    def _event_context(self, event):
        invocation = self.runtime.graph.invocations.get(event.invocation_id)
        context = event.context_id or (invocation.context_id if invocation is not None else None)
        return (event.workflow_id, context) if context is not None else None

    @staticmethod
    def _tool_id(event):
        return str(event.attributes.get("tool_run_id") or event.attributes.get("tool_call_id") or "_legacy")

    def _start(self, event):
        context = self._event_context(event)
        identity = self.results.get(context) or self.latest.get(context)
        generation, record = self.generations.get(identity), self.registry.records.get(identity)
        if generation is None or record is None:
            self.counts["unbound_stall_starts"] += 1
            return
        tool = event.invocation_id, self._tool_id(event)
        old = self.tools.get(tool)
        if old is not None and old != identity:
            raise ValueError("TokenCake observed overlapping reuse of a tool identifier")
        if tool in generation.tool_ids:
            self.counts["duplicate_tool_starts"] += 1
            return
        self.tools[tool] = identity
        generation.tool_ids.add(tool)
        if len(generation.tool_ids) > 1:
            self.counts["parallel_stall_members"] += 1
            return
        # A real stall event supplies the frontend's reuse intent. Explicit
        # application exclusions remain authoritative, including false values.
        flags = {
            name: True for name in ("offload_eligible", "reusable_prefix")
            if name not in generation.declared
        }
        record.metadata = replace(record.metadata, **flags)
        estimate = event.attributes.get("estimated_duration_s")
        result = self.registry.apply(LifecycleEvent(
            "stall_started", identity,
            str(event.attributes.get("tool_family", "unknown_external")),
            estimate,
        ))
        self.counts["stall_start_" + result.disposition] += 1

    def _finish(self, event):
        tool = event.invocation_id, self._tool_id(event)
        identity = self.tools.pop(tool, None)
        generation = self.generations.get(identity)
        if generation is None:
            self.counts["unbound_stall_finishes"] += 1
            return
        generation.tool_ids.discard(tool)
        if generation.tool_ids:
            return
        result = self.registry.apply(LifecycleEvent("stall_finished", identity))
        self._pending.discard(identity)
        self.counts["stall_finish_" + result.disposition] += 1

    def events(self, events):
        self.registry.expire()
        for event in events:
            if event.kind is Kind.LLM_RESULT:
                context = self._event_context(event)
                identity = event.attributes.get("request_id")
                generation = self.generations.get(identity)
                if generation is not None and context == (
                    generation.key.root_workflow_id, generation.key.context_id,
                ):
                    self.results[context] = identity
            elif event.kind is Kind.TOOL_START:
                self._start(event)
            elif event.kind is Kind.TOOL_END:
                self._finish(event)
            elif event.kind is Kind.WORKFLOW_END:
                for identity, generation in tuple(self.generations.items()):
                    if generation.key.root_workflow_id == event.workflow_id:
                        self.registry.terminate(identity)
                for mapping in (self.latest, self.results):
                    for context in tuple(mapping):
                        if context[0] == event.workflow_id:
                            del mapping[context]
            elif event.kind in (Kind.RETURN, Kind.INVOCATION_CANCEL, Kind.CONTEXT_COMPACT, Kind.CONTEXT_ADVANCE):
                context = self._event_context(event)
                retired = event.kind in (Kind.CONTEXT_COMPACT, Kind.INVOCATION_CANCEL) or (
                    event.kind is Kind.RETURN and event.attributes.get("context_retired") is True
                )
                for identity, generation in tuple(self.generations.items()):
                    if context == (generation.key.root_workflow_id, generation.key.context_id) and (
                        retired or event.kind is Kind.CONTEXT_ADVANCE
                        and event.context_epoch is not None
                        and generation.key.context_epoch < event.context_epoch
                    ):
                        self.registry.terminate(identity, "reset" if not retired else "completed")

    def aborted(self, request_id, *, all_requests=False):
        for identity in tuple(self.generations):
            if all_requests or identity.startswith(request_id):
                self.registry.generation_finished(identity, "aborted")
                self._pending.discard(identity)

    def estimate_transfer(self, tokens, direction):
        return (
            tokens * self.cache.host_pool_group.anchor_entry.host_pool.size_per_token
            / self.bandwidth[direction]
            + math.ceil(tokens / self.block_tokens) * .000003
        )

    def waiting_pressure(self):
        ledger = self.adapter.capacity
        ledger.refresh()
        window = ledger.window
        waiting = [
            item for rid, item in self.adapter.requests.items()
            if rid not in ledger.requests and not self.runtime._terminal(item.key)
        ]
        total_demand = critical_demand = 0
        candidates = []
        for item in waiting:
            req = item.request
            remaining = max(
                0, len(req.origin_input_ids) + len(req.output_ids)
                - getattr(req.kv, "kv_committed_len", 0),
            )
            total_demand += remaining
            if item.metadata.agent_type in window.plan.critical:
                critical_demand += remaining
            try:
                probe = ledger.probe(req)
            except NativeCapacityNotReady:
                continue
            admitted = ledger.admission_demand(probe)
            if admitted > window.uncommitted:
                continue
            score = self.adapter.scores.get(req.rid, 0)
            normal = window.consumption(item.metadata.agent_type, admitted, score=score)
            if normal is None and window.consumption(
                item.metadata.agent_type, admitted, score=score, borrow_reserved=True,
            ) is None:
                continue
            gpu_prefix = sum(tokens for _, tokens in probe.extents)
            shared = sum(
                tokens for identity, tokens in probe.extents if identity in ledger.extents
            )
            computed = gpu_prefix + probe.host_prefix_tokens
            tokens = min(
                max(0, len(req.origin_input_ids) + len(req.output_ids) - computed),
                self.chunked_prefill_tokens, self.prefill_token_budget,
            )
            demand = max(0, computed - shared) + tokens
            if tokens > 0 and 0 < demand <= window.free:
                candidates.append((normal is None, demand, admitted, tokens))
        free, commitment, tokens_left = window.free, window.uncommitted, self.prefill_token_budget
        slots = min(
            self.running_limit - len(ledger.requests),
            self.cache.req_to_token_pool.available_size(),
        )
        fit = 0
        for _, demand, admitted, tokens in sorted(candidates, key=lambda row: row[0]):
            if slots <= 0:
                break
            if demand > free or admitted > commitment or tokens > tokens_left:
                continue
            slots -= 1
            free, commitment, tokens_left = free - demand, commitment - admitted, tokens_left - tokens
            fit += demand
        return Pressure(
            1 - window.free / window.total, window.free, len(waiting),
            total_demand, critical_demand, window.shared_available,
            min(self.eviction_window_tokens, fit),
        )

    def _cpu_available(self):
        protected = set(self.transfers._host_members)
        for identities in (self.cache.ongoing_load_back, self.cache.ongoing_write_through):
            for identity in identities:
                node = self.transfers._node(identity)
                visited = set()
                while node is not None and not self.cache.is_root(node.id):
                    if node.id in visited or len(visited) >= self.transfers.max_nodes:
                        raise ValueError("TokenCake native DMA ancestry is invalid")
                    visited.add(node.id)
                    if node.component_data[0].host_value is not None:
                        protected.add(node.id)
                    node = node.parent
        occupied = sum(
            len(node.key) for identity in protected
            if (node := self.transfers._node(identity)) is not None
        )
        return max(0, self.cache.host_pool_group.anchor_entry.host_pool.size - occupied)

    def _bounded_prefix(self, captured, remaining, available):
        from sglang.srt.mem_cache.base_prefix_cache import MatchPrefixParams
        from sglang.srt.mem_cache.radix_cache import RadixKey

        snapshot, owner = captured.snapshot, captured.snapshot.owner
        path = self.transfers.resolve(snapshot, owner=owner)
        if path is None:
            return None
        ends, missing, cumulative = [], [], [0]
        boundary = 0
        for node in path:
            full = node.component_data[0]
            if full.value is None and full.host_value is None:
                break
            boundary += len(node.key)
            ends.append(boundary)
            missing.append(full.host_value is None)
            cumulative.append(cumulative[-1] + (len(node.key) if missing[-1] else 0))

        def new_tokens(count):
            index = bisect_right(ends, count)
            return cumulative[index] + (
                count - (ends[index - 1] if index else 0)
                if index < len(missing) and missing[index] else 0
            )

        low, high = 0, boundary // self.block_tokens
        while low < high:
            middle = (low + high + 1) // 2
            count = middle * self.block_tokens
            fresh = new_tokens(count)
            transfer = self.estimate_transfer(fresh, "d2h") + self.estimate_transfer(count, "h2d")
            if fresh <= available and remaining - transfer >= max(.05, 2 * transfer):
                low = middle
            else:
                high = middle - 1
        boundary = low * self.block_tokens
        if not boundary:
            return None
        match = self.cache.match_prefix(MatchPrefixParams(key=RadixKey(
            captured.key.token_ids, captured.key.extra_key, cache_salt=captured.key.cache_salt,
            limit=boundary,
        ), cow_mamba=False))
        selected = self.transfers.capture(match.best_match_node, owner=owner)
        if selected is None or selected.prefix_tokens != boundary:
            return None
        return _Prefix(selected, captured.key)

    def _retain(self, record, *, complete):
        captured = record.snapshot
        method = self.transfers.retain_host if complete else self.transfers.retain_host_ready
        lease = method(
            captured.snapshot, owner=captured.snapshot.owner,
            deadline_s=record.release_deadline,
        )
        if lease is None:
            return False
        old = set(record.retained)
        if lease not in self.registry.retain_ready(record, {lease}):
            self.transfers.release_host(lease)
            return False
        record.retained.difference_update(old)
        self.registry.manager.release(old)
        return True

    def _settle(self, identity):
        record = self.registry.records.get(identity)
        if record is None or not record.owns_preservation or record.snapshot is None:
            self._pending.discard(identity)
            return
        snapshot = record.snapshot.snapshot
        path = self.transfers.resolve(snapshot, owner=snapshot.owner)
        if path is None:
            self.registry.terminate(identity, "reset")
            self._pending.discard(identity)
        elif all(
            node.component_data[0].host_value is not None
            and node.write_through_pending_id is None for node in path
        ) and self._retain(record, complete=True):
            record.pending_retention.clear()
            self._pending.discard(identity)
            self.counts["preservation_acks_settled"] += 1
            self._on_preserved(identity, record)

    def _on_backup_issued(self, identity, record):
        return None

    def _on_preserved(self, identity, record):
        return None

    def _benefit(self, record, pressure, *, count, available, fresh, remaining, transfer, churn):
        return public_offload_benefit(
            self.settings, record.metadata, pressure, blocks=count,
            cpu_available=available, cpu_needed=fresh, duration=remaining,
            transfer_time=transfer,
            agent_score=self.adapter.capacity.window.plan.scores.get(record.metadata.agent_type, 0.),
            churn_penalty=churn,
        )

    def transfer_completed(self, commit):
        if getattr(commit, "status", None) != "completed":
            return
        direction = getattr(commit, "direction", None)
        elapsed = getattr(commit, "transfer_stream_elapsed_ms", None)
        size = getattr(commit, "actual_bytes", None)
        if direction in self.bandwidth and size and elapsed is not None and elapsed > 0:
            sample = size * 1000 / elapsed
            if math.isfinite(sample) and sample > 0:
                self.bandwidth[direction] = .5 * sample + .5 * self.bandwidth[direction]
                self.counts[direction + "_bandwidth_samples"] += 1
        if direction in ("d2h", "h2d"):
            self.registry.expire()
            for identity in tuple(self._pending):
                self._settle(identity)

    def tick(self):
        self.step += 1
        self.registry.expire()
        while self._recent_stores and self.step - self._recent_stores[0] > 4:
            self._recent_stores.popleft()
        pressure = None
        for identity, record in tuple(self.registry.records.items()):
            record.pending_evaluation = False
            if (
                not record.owns_preservation or record.snapshot is None
                or record.retained or record.pending_retention
            ):
                continue
            if not record.metadata.offload_eligible or not record.metadata.reusable_prefix:
                self.counts["not_eligible"] += 1
                continue
            pressure = self.waiting_pressure() if pressure is None else pressure
            available = self._cpu_available()
            signature = (
                pressure.free, pressure.waiting_count, pressure.waiting_demand,
                pressure.critical_demand, pressure.shared_available, pressure.fit_demand, available,
            )
            if record.backoff_signature == signature and self.step < record.next_evaluation_step:
                self.counts["backoff"] += 1
                continue
            remaining = max(0., record.started_at + record.predicted_duration - self.adapter.clock())
            selected = self._bounded_prefix(record.snapshot, remaining, available)
            if selected is None:
                self.counts["empty_or_unprofitable_prefix"] += 1
                record.backoff_signature, record.next_evaluation_step = signature, self.step + 8
                continue
            path = self.transfers.resolve(selected.snapshot, owner=selected.snapshot.owner)
            fresh = sum(len(node.key) for node in path if node.component_data[0].host_value is None)
            count = selected.snapshot.prefix_tokens
            transfer = self.estimate_transfer(fresh, "d2h") + self.estimate_transfer(count, "h2d")
            churn = 1.5 if self.step - record.last_store_step <= 4 else 0.
            churn += min(4., max(0, len(self._recent_stores) - 64) / 8)
            decision = self._benefit(
                record, pressure, count=count, available=available, fresh=fresh,
                remaining=remaining, transfer=transfer, churn=churn,
            )
            self.counts[decision.reason.value] += 1
            if decision.reason is not DecisionReason.SELECTED:
                record.backoff_signature, record.next_evaluation_step = signature, self.step + 8
                continue
            self.registry.set_h2d_estimate(record, self.estimate_transfer(count, "h2d"))
            if not record.owns_preservation:
                continue
            record.snapshot = selected
            self._retain(record, complete=False)
            issued = self.transfers.backup(
                selected.snapshot, owner=selected.snapshot.owner, allow_host_reclaim=True,
            )
            path = self.transfers.resolve(selected.snapshot, owner=selected.snapshot.owner)
            if path is None:
                self.registry.terminate(identity, "reset")
                continue
            if any(node.component_data[0].host_value is None for node in path):
                # An interrupted native enqueue still owns the submitted DMA.
                # Do not claim the incomplete prefix as a preserved checkpoint.
                self.counts["partial_store_not_credited"] += 1
                if not issued:
                    self.registry.manager.release(record.retained)
                    record.retained.clear()
                record.backoff_signature, record.next_evaluation_step = signature, self.step + 8
                continue
            record.pending_retention.update(item[1] for item in issued)
            self._on_backup_issued(identity, record)
            if any(node.write_through_pending_id is not None for node in path):
                record.pending_retention.add("native_ack_pending")
                self._pending.add(identity)
            else:
                if self._retain(record, complete=True):
                    self._on_preserved(identity, record)
            record.last_store_step = self.step
            record.backoff_signature = None
            self._recent_stores.append(self.step)
            self.counts["preservation_batches"] += 1
        for identity in tuple(self.generations):
            if identity not in self.registry.records and identity not in self.registry.generations:
                self.generations.pop(identity)
        self._pending.intersection_update(self.registry.records)

    def close(self):
        for identity in tuple(self.registry.records):
            self.registry.terminate(identity, "aborted")
        self._pending.clear()
        self.generations.clear()
        self.latest.clear()
        self.results.clear()
        self.tools.clear()

    def native_reset_succeeded(self):
        for record in self.registry.records.values():
            self.registry.manager.release(record.retained)
        self.registry.external_reset_succeeded()
        self._pending.clear()
        self.counts["native_cache_resets"] += 1

    def rebind_transfers(self):
        self.transfers = self.adapter.transfers
        self.registry.manager = _HostRetention(self.transfers)
