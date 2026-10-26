"""Native FULL prefix transfers for paper policies without session identifiers.

Anchors refer only to observed cached state. These primitives grant no scheduling
priority or permanent Host/HBM protection and leave all DMA fences native-owned.
"""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
import heapq
import itertools
import math
import time
import uuid

from beliefkv.runtime.sglang_v0520_physical import (
    PhysicalActionExpectation, PhysicalChildExpectation, PhysicalReceiptError,
    PhysicalTransactionLedger, normalize_native_creation_time,
)


@dataclass(frozen=True)
class PrefixSnapshot:
    owner: tuple[str, str, int]
    root_id: int
    root_creation_time: int | float
    leaf_id: int
    leaf_creation_time: int | float
    namespace: tuple[str | None, str | None]
    prefix_tokens: int


@dataclass
class _HostLease:
    snapshot: PrefixSnapshot
    deadline_s: float | None
    pins: dict = field(default_factory=dict)
    complete: bool = True


class NativePrefixTransfers:
    def __init__(self, cache, *, can_mutate, max_nodes=256, max_host_leases=512, clock=time.monotonic):
        if (
            getattr(cache, "tree_components", None) != (0,)
            or getattr(cache, "host_memory_mode", None) != "cache"
            or getattr(cache, "is_write_back", None) is not True
            or getattr(cache, "buffer_pipeline", None) is not None
            or getattr(cache, "page_size", None) != 1
            or not callable(can_mutate)
            or type(max_nodes) is not int or max_nodes <= 0
            or type(max_host_leases) is not int or max_host_leases <= 0
            or not callable(clock)
        ):
            raise ValueError("paper prefix transfers require native FULL-only write-back cache")
        self.cache, self.can_mutate, self.max_nodes = cache, can_mutate, max_nodes
        self.counts = Counter()
        self._namespace = "paper-prefix-" + uuid.uuid4().hex
        self._commands = itertools.count()
        self.receipts = PhysicalTransactionLedger(
            max_pending=4 * max_nodes, max_nodes=4 * max_nodes, max_age_s=3600.,
        )
        self.clock, self.max_host_leases = clock, max_host_leases
        self._host_leases = {}
        self._host_members = {}
        self._host_deadlines = []
        self._observer_callback = self._host_node_changed
        self._previous_observer = None
        self._host_observer_installed = False
        self._closed = False
        self._loading_batch_depth = 0
        self._previous_allocation = cache.evict_for_alloc
        self._allocation_callback = self._evict_for_alloc_after_backup
        cache.evict_for_alloc = self._allocation_callback

    def _evict_for_alloc_after_backup(self, params):
        allocator = self.cache.token_to_kv_pool_allocator
        target = allocator.available_size() + params.num_tokens
        pending_backup = bool(self.cache.ongoing_write_through)
        result = self._previous_allocation(params)
        missing = target - allocator.available_size()
        # Native write-back waits can make leaves evictable after their first
        # walk. A forced chunk allocation still needs those pages reclaimed.
        if pending_backup and missing > 0 and not self.cache.ongoing_write_through:
            from sglang.srt.mem_cache.base_prefix_cache import EvictParams

            retry = self._previous_allocation(EvictParams(num_tokens=missing))
            result.num_tokens_evicted += retry.num_tokens_evicted
            self.counts["allocation_after_backup_retry"] += 1
            self.counts["allocation_after_backup_retry_tokens"] += retry.num_tokens_evicted
        return result

    def _safe(self):
        if self._closed or not self.can_mutate():
            raise ValueError("paper prefix mutation is outside native scheduler cache authority")

    def _command(self, direction):
        return f"{self._namespace}-{direction}-{next(self._commands)}"

    def _loading_slots_ready(self):
        counter = self.cache.cache_controller.layer_done_counter
        if counter.num_counters < 3 or len(counter.events) != counter.num_counters:
            raise ValueError("paper prefetch requires the native three-counter overlap protocol")
        ready = all(
            counter.events[(counter.producer_index + offset) % counter.num_counters].finish_event.query()
            for offset in range(1, counter.num_counters + 1)
        )
        if not ready:
            self.counts["restore_native_producer_backpressure"] += 1
        return ready

    @contextmanager
    def loading_batch(self):
        self._safe()
        self._loading_batch_depth += 1
        try:
            yield
        finally:
            self._loading_batch_depth -= 1
            if not self._loading_batch_depth:
                self.start_restores()

    def start_restores(self):
        self._safe()
        queue = self.cache.cache_controller.load_queue
        if queue and all(
            getattr(operation, "beliefkv_command_id", None) is not None for operation in queue
        ):
            self.cache._start_beliefkv_prefetch_loads()

    def _before_enqueue(self, node, command, direction):
        def register(operation):
            full_tokens = len(operation.host_indices)
            size = self.cache.host_pool_group.anchor_entry.host_pool.size_per_token
            if (
                getattr(operation, "beliefkv_command_id", None) != command
                or getattr(operation, "node_ids", None) != [node.id]
                or not full_tokens or len(operation.device_indices) != full_tokens
                or getattr(operation, "pool_transfers", None)
                or type(size) is not int or size <= 0
            ):
                raise PhysicalReceiptError("paper native transfer enqueue accounting differs")
            amount = full_tokens * size
            self.receipts.register(PhysicalActionExpectation(
                command, "PREPARE_HOST" if direction == "d2h" else "PREFETCH_GPU",
                self._namespace, 0,
                (PhysicalChildExpectation(
                    node.id, (node.id,), (("kv", amount),), amount,
                    normalize_native_creation_time(node.creation_time) if direction == "d2h" else None,
                    (("kv", operation.host_indices),) if direction == "d2h" else (),
                ),),
                (("kv", size),),
            ))
            return True

        return register

    def transfer_completed(self, commit):
        """Reconcile physical receipts here, independently of Full's commands."""
        children = getattr(commit, "child_commits", ())
        if children and not any(
            type(command := getattr(child, "command_id", None)) is str
            and command.startswith(self._namespace + "-")
            for child in children
        ):
            return False
        completed = self.receipts.observe(
            commit, live_context_epochs={self._namespace: 0}, native_cache=self.cache,
        )
        self.counts["native_commands_completed"] += len(completed)
        return bool(children)

    def _node(self, identity):
        try:
            return self.cache.tree_core.node_by_id(identity)
        except (KeyError, RuntimeError, ValueError):
            return None

    def _path(self, leaf, namespace):
        root, path, visited = self.cache.tree_core.root_node, [], set()
        while leaf is not root:
            if len(path) >= self.max_nodes:
                self.counts["path_depth_declined"] += 1
                return None
            if (
                leaf is None or leaf.id in visited
                or self._node(leaf.id) is not leaf
                or (leaf.key.extra_key, leaf.key.cache_salt) != namespace
            ):
                self.counts["path_identity_declined"] += 1
                return None
            visited.add(leaf.id)
            path.append(leaf)
            leaf = leaf.parent
        path.reverse()
        return tuple(path)

    def capture(self, node_id, *, owner):
        if (
            type(owner) is not tuple or len(owner) != 3
            or any(type(value) is not str or not value for value in owner[:2])
            or type(owner[2]) is not int or owner[2] < 0
        ):
            raise ValueError("prefix ownership requires workflow, context and epoch")
        leaf, root = self._node(node_id), self.cache.tree_core.root_node
        if leaf is None or leaf is root:
            return None
        namespace = leaf.key.extra_key, leaf.key.cache_salt
        path = self._path(leaf, namespace)
        if not path:
            return None
        return PrefixSnapshot(
            owner, root.id, root.creation_time, leaf.id, leaf.creation_time, namespace,
            sum(len(node.key) for node in path),
        )

    def resolve(self, snapshot, *, owner):
        if snapshot.owner != owner:
            return None
        root, leaf = self.cache.tree_core.root_node, self._node(snapshot.leaf_id)
        if (
            root.id != snapshot.root_id or root.creation_time != snapshot.root_creation_time
            or leaf is None or leaf.creation_time != snapshot.leaf_creation_time
        ):
            return None
        path = self._path(leaf, snapshot.namespace)
        if not path or sum(len(node.key) for node in path) != snapshot.prefix_tokens:
            return None
        return path

    def status(self, snapshot, *, owner):
        path = self.resolve(snapshot, owner=owner)
        if path is None:
            return "invalid"
        if any(node.load_back_pending_id is not None for node in path):
            return "loading"
        if any(
            node.component_data[0].value is None and node.component_data[0].host_value is None
            for node in path
        ):
            return "missing"
        if any(node.component_data[0].value is None for node in path):
            return "cpu_backup"
        if any(node.write_through_pending_id is not None for node in path):
            return "offloading"
        return "gpu"

    def backup(self, snapshot, *, owner, allow_host_reclaim=False):
        from sglang.srt.mem_cache.unified_cache.cache_action import BackupKV

        self._safe()
        path = self.resolve(snapshot, owner=owner)
        if path is None or any(
            node.load_back_pending_id is not None or node.write_through_pending_id is not None
            for node in path
        ):
            return ()
        missing = [node for node in path if node.component_data[0].host_value is None]
        if any(node.component_data[0].value is None for node in missing):
            return ()
        required = sum(len(node.key) for node in missing)
        if not allow_host_reclaim and required > self.cache.cache_controller.mem_pool_host.available_size():
            self.counts["backup_host_capacity_declined"] += 1
            return ()
        guard = None
        if allow_host_reclaim and any(
            node.component_data[0].host_value is not None for node in path
        ):
            guard = self.retain_host_ready(
                snapshot, owner=owner, deadline_s=self.clock() + 60.,
            )
            if guard is None:
                self.counts["backup_host_guard_declined"] += 1
                return ()
        issued = []
        try:
            if allow_host_reclaim:
                free = self.cache.cache_controller.mem_pool_host.available_size()
                if free < required:
                    self.cache.evict_host(required - free)
                if self.cache.cache_controller.mem_pool_host.available_size() < required:
                    self.counts["backup_host_capacity_declined"] += 1
                    return ()
            for node in missing:
                command = self._command("d2h")
                count = self.cache._execute_and_commit_kv_backup(
                    BackupKV([node.id]), write_back=False, beliefkv_command_id=command,
                    no_host_reclaim=True,
                    beliefkv_before_enqueue=self._before_enqueue(node, command, "d2h"),
                    beliefkv_include_mamba=False, beliefkv_defer_writing=True,
                )
                if not count:
                    break
                issued.append((node.id, command, count))
        finally:
            if issued:
                self.cache.cache_controller.start_writing()
            if guard is not None:
                self.release_host(guard)
        self.counts["d2h_enqueued_tokens"] += sum(item[2] for item in issued)
        return tuple(issued)

    def backup_prefix(self, snapshot, *, owner, observed_key, max_new_tokens):
        """Back a bounded observed prefix without exceeding new Host capacity."""
        from sglang.srt.mem_cache.base_prefix_cache import MatchPrefixParams
        from sglang.srt.mem_cache.radix_cache import RadixKey

        self._safe()
        if type(max_new_tokens) is not int or max_new_tokens < 0:
            raise ValueError("bounded backup requires a nonnegative token budget")
        path = self.resolve(snapshot, owner=owner)
        if path is None:
            return ()
        if (
            not isinstance(observed_key, RadixKey) or observed_key.is_bigram
            or len(observed_key) < snapshot.prefix_tokens
            or (observed_key.extra_key, observed_key.cache_salt) != snapshot.namespace
        ):
            raise ValueError("bounded backup requires the observed owner's prefix key")
        offset = 0
        for node in path:
            if not node.key.is_prefix_at(observed_key, offset):
                raise ValueError("bounded backup key does not match observed cached tokens")
            offset += len(node.key)
        if any(
            node.load_back_pending_id is not None or node.write_through_pending_id is not None
            for node in path
        ):
            return ()
        budget = min(max_new_tokens, self.cache.cache_controller.mem_pool_host.available_size())
        if not budget:
            return ()
        boundary, missing = 0, 0
        for node in path:
            full = node.component_data[0]
            if full.host_value is not None:
                boundary += len(node.key)
                continue
            if full.value is None:
                return ()
            selected = min(len(node.key), budget - missing)
            boundary += selected
            missing += selected
            if selected < len(node.key) or missing == budget:
                break
        if not missing:
            return ()
        match = self.cache.match_prefix(MatchPrefixParams(key=RadixKey(
            observed_key.token_ids, observed_key.extra_key, limit=boundary,
            cache_salt=observed_key.cache_salt,
        ), cow_mamba=False))
        partial = self.capture(match.best_match_node, owner=owner)
        if partial is None or partial.prefix_tokens != boundary:
            self.counts["bounded_backup_boundary_declined"] += 1
            return ()
        issued = self.backup(partial, owner=owner)
        actual = sum(item[2] for item in issued)
        if actual > budget:
            raise ValueError("bounded backup exceeded its actual Host capacity grant")
        self.counts["bounded_d2h_enqueued_tokens"] += actual
        return issued

    def demote(self, snapshot, *, owner, max_tokens, can_demote=None):
        self._safe()
        if type(max_tokens) is not int or max_tokens < 0:
            raise ValueError("paper demotion requires a nonnegative token budget")
        if can_demote is not None and not callable(can_demote):
            raise ValueError("native demotion requires a callable owner validator")
        path = self.resolve(snapshot, owner=owner)
        if path is None:
            return 0
        before = self.cache.token_to_kv_pool_allocator.available_size()
        tracker = {}
        for node in reversed(path):
            full = node.component_data[0]
            if (
                full.value is None or full.host_value is None
                or len(node.key) + tracker.get(0, 0) > max_tokens
                or node not in self.cache.tree_core.evictable_device_leaves
                or node.load_back_pending_id is not None or node.write_through_pending_id is not None
                or node.id in self.cache.ongoing_load_back or node.id in self.cache.ongoing_write_through
                or any(component.lock_ref for component in node.component_data)
                or can_demote is not None and not can_demote(node)
            ):
                continue
            self.cache._demote(node.id, tracker)
        count = tracker.get(0, 0)
        if self.cache.token_to_kv_pool_allocator.available_size() - before != count:
            raise ValueError("native prefix demotion disagrees with unique allocator capacity")
        self.counts["demoted_tokens"] += count
        return count

    def restore(self, snapshot, *, owner, available_tokens):
        self._safe()
        if type(available_tokens) is not int or available_tokens < 0:
            raise ValueError("paper restore requires a nonnegative token budget")
        path = self.resolve(snapshot, owner=owner)
        if path is None or any(
            node.load_back_pending_id is not None or node.write_through_pending_id is not None
            for node in path
        ):
            return ()
        missing = [node for node in path if node.component_data[0].value is None]
        if any(node.component_data[0].host_value is None for node in missing):
            return ()
        required = sum(len(node.key) for node in missing)
        if required > min(available_tokens, self.cache.token_to_kv_pool_allocator.available_size()):
            self.counts["restore_device_capacity_declined"] += 1
            return ()
        queue = self.cache.cache_controller.load_queue
        if any(getattr(operation, "beliefkv_command_id", None) is None for operation in queue):
            return ()
        if not self._loading_slots_ready():
            return ()
        issued = []
        try:
            for node in missing:
                command = self._command("h2d")
                if not self.cache.load_back(
                    node.id, beliefkv_command_id=command,
                    beliefkv_before_enqueue=self._before_enqueue(node, command, "h2d"),
                    beliefkv_no_device_reclaim=True, beliefkv_include_mamba=False,
                ):
                    break
                issued.append((node.id, command, len(node.key)))
        finally:
            if issued and not self._loading_batch_depth:
                self.start_restores()
        self.counts["h2d_enqueued_tokens"] += sum(item[2] for item in issued)
        return tuple(issued)

    def restore_prefix(self, snapshot, *, owner, observed_key, available_tokens):
        """Restore at most a grant's new token slots, splitting native Host nodes."""
        from sglang.srt.mem_cache.base_prefix_cache import MatchPrefixParams
        from sglang.srt.mem_cache.radix_cache import RadixKey

        self._safe()
        if type(available_tokens) is not int or available_tokens < 0:
            raise ValueError("progressive restore requires a nonnegative token grant")
        path = self.resolve(snapshot, owner=owner)
        if path is None:
            return ()
        if (
            not isinstance(observed_key, RadixKey) or observed_key.is_bigram
            or len(observed_key) < snapshot.prefix_tokens
            or (observed_key.extra_key, observed_key.cache_salt) != snapshot.namespace
        ):
            raise ValueError("progressive restore requires the observed owner's prefix key")
        offset = 0
        for node in path:
            if not node.key.is_prefix_at(observed_key, offset):
                raise ValueError("progressive restore key does not match observed cached tokens")
            offset += len(node.key)
        if any(node.load_back_pending_id is not None for node in path):
            self.counts["progressive_restore_waiting_for_h2d_ack"] += 1
            return ()
        if any(node.write_through_pending_id is not None for node in path):
            self.counts["progressive_restore_waiting_for_d2h_ack"] += 1
            return ()
        budget = min(
            available_tokens, self.cache.token_to_kv_pool_allocator.available_size(),
        )
        if not budget:
            return ()
        boundary, missing = 0, 0
        for node in path:
            full = node.component_data[0]
            if full.value is not None:
                boundary += len(node.key)
                continue
            if full.host_value is None:
                self.counts["progressive_restore_source_gap"] += 1
                return ()
            selected = min(len(node.key), budget - missing)
            boundary += selected
            missing += selected
            if selected < len(node.key) or missing == budget:
                break
        if not missing:
            return ()
        match = self.cache.match_prefix(MatchPrefixParams(key=RadixKey(
            observed_key.token_ids, observed_key.extra_key, limit=boundary,
            cache_salt=observed_key.cache_salt,
        ), cow_mamba=False))
        partial = self.capture(match.best_match_node, owner=owner)
        if partial is None or partial.prefix_tokens != boundary:
            self.counts["progressive_restore_boundary_declined"] += 1
            return ()
        issued = self.restore(partial, owner=owner, available_tokens=budget)
        actual = sum(item[2] for item in issued)
        if actual > budget:
            raise ValueError("progressive restore exceeded its actual physical grant")
        self.counts["progressive_h2d_enqueued_tokens"] += actual
        return issued

    def _pin_host_node(self, identity, node):
        lease = self._host_leases[identity]
        if node.id in lease.pins:
            return
        if node.component_data[0].host_value is None:
            raise ValueError("native split lost protected paper Host prefix")
        receipt = self.cache.inc_host_lock_ref(node.id).to_dec_params()
        lease.pins[node.id] = node.creation_time, receipt
        self._host_members.setdefault(node.id, set()).add(identity)

    def _host_node_changed(self, node):
        if self._previous_observer is not None:
            self._previous_observer(node)
        if self._node(node.id) is not node:
            for identity in self._host_members.pop(node.id, ()):
                self._host_leases[identity].pins.pop(node.id, None)
                self.counts["protected_host_node_invalidated"] += 1
            return
        inherited = set()
        for child in node.children.values():
            inherited.update(self._host_members.get(child.id, ()))
        for identity in inherited - self._host_members.get(node.id, set()):
            self._pin_host_node(identity, node)
            self.counts["split_host_prefix_pins"] += 1

    def retain_host(self, snapshot, *, owner, deadline_s):
        """Retain every settled source extent until explicit release or expiry."""
        if deadline_s is None:
            raise ValueError("timed Host retention requires an explicit finite deadline")
        return self._retain_host(
            snapshot, owner=owner, deadline_s=deadline_s, require_complete=True,
        )

    def retain_host_ready(self, snapshot, *, owner, deadline_s):
        """Protect settled extents while missing parts of the same prefix copy."""
        if deadline_s is None:
            raise ValueError("timed Host retention requires an explicit finite deadline")
        return self._retain_host(
            snapshot, owner=owner, deadline_s=deadline_s, require_complete=False,
        )

    def retain_host_until_release(self, snapshot, *, owner, require_complete=True):
        """Own a paused request's sources until actual resumption or cancellation."""
        if type(require_complete) is not bool:
            raise ValueError("Host ownership requires an explicit completeness mode")
        return self._retain_host(
            snapshot, owner=owner, deadline_s=None, require_complete=require_complete,
        )

    def _retain_host(self, snapshot, *, owner, deadline_s, require_complete):
        self._safe()
        if deadline_s is not None and (
            not math.isfinite(deadline_s) or deadline_s <= self.clock()
        ):
            return None
        path = self.resolve(snapshot, owner=owner)
        if path is None or len(self._host_leases) >= self.max_host_leases:
            return None
        settled = tuple(node for node in path if (
            node.component_data[0].host_value is not None
            and node.write_through_pending_id is None
            and (not require_complete or node.load_back_pending_id is None)
        ))
        if not settled or require_complete and len(settled) != len(path):
            return None
        if not self._host_observer_installed:
            self._previous_observer = self.cache.tree_core.beliefkv_full_resident_observer
            self.cache.tree_core.beliefkv_full_resident_observer = self._observer_callback
            self._host_observer_installed = True
        identity = self._command("host-lease")
        self._host_leases[identity] = _HostLease(
            snapshot, deadline_s, complete=require_complete,
        )
        try:
            for node in settled:
                self._pin_host_node(identity, node)
        except BaseException:
            self.release_host(identity)
            raise
        if deadline_s is not None:
            heapq.heappush(self._host_deadlines, (deadline_s, identity))
        else:
            self.counts["host_leases_until_release"] += 1
        self.counts["host_leases_acquired"] += 1
        self.counts["complete_host_leases_acquired" if require_complete else "ready_host_leases_acquired"] += 1
        return identity

    def release_host(self, identity):
        self._safe()
        lease = self._host_leases.pop(identity, None)
        if lease is None:
            return False
        for node_id, (created, receipt) in reversed(tuple(lease.pins.items())):
            members = self._host_members[node_id]
            members.discard(identity)
            if not members:
                del self._host_members[node_id]
            node = self._node(node_id)
            if node is not None and node.creation_time == created:
                self.cache.dec_host_lock_ref(node_id, receipt)
        self.counts["host_leases_released"] += 1
        if (
            not self._host_leases
            and self.cache.tree_core.beliefkv_full_resident_observer is self._observer_callback
        ):
            self.cache.tree_core.beliefkv_full_resident_observer = self._previous_observer
            self._host_observer_installed = False
            self._previous_observer = None
        if len(self._host_deadlines) > 2 * len(self._host_leases) + self.max_host_leases:
            self._host_deadlines = [
                (lease.deadline_s, key) for key, lease in self._host_leases.items()
                if lease.deadline_s is not None
            ]
            heapq.heapify(self._host_deadlines)
        return True

    def expire_host_leases(self):
        """Work is proportional to expired entries, with bounded tombstones."""
        self._safe()
        now, expired = self.clock(), 0
        while self._host_deadlines and self._host_deadlines[0][0] <= now:
            _, identity = heapq.heappop(self._host_deadlines)
            expired += int(self.release_host(identity))
        return expired

    def close(self):
        if self.cache.evict_for_alloc is self._allocation_callback:
            self.cache.evict_for_alloc = self._previous_allocation
        for identity in tuple(self._host_leases):
            self.release_host(identity)
        if (
            self._host_observer_installed
            and self.cache.tree_core.beliefkv_full_resident_observer is self._observer_callback
        ):
            self.cache.tree_core.beliefkv_full_resident_observer = self._previous_observer
        self._host_deadlines.clear()
        self._closed = True
