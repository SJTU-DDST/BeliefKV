"""Delay initial KV materialization when useful state exhausts both tiers."""

from array import array
from collections import OrderedDict
from dataclasses import asdict, dataclass
from heapq import heapify, heappop, heappush
from itertools import islice
import os
import time
from types import SimpleNamespace

from beliefkv.control.causal_graph import InvocationState, JoinMode
from beliefkv.core.events import RelationType
from beliefkv.runtime.sglang_v0520_admission import _request_key


PRESSURE_POLICIES = ("pressure_v1", "pressure_v2")
NODE_CENSUS_BOUND = "native_node_census_bound"


@dataclass(frozen=True)
class MaterializationHeadroom:
    observable: bool
    reason: str = ""
    census_complete: bool = False
    host_capacity_tokens: int = 0
    host_free_tokens: int = 0
    device_free_tokens: int = 0
    useful_host_only_tokens: int | None = None
    reclaimable_host_tokens: int = 0
    pinned_host_tokens: int = 0
    untracked_host_tokens: int = 0
    inspected_nodes: int = 0

    @property
    def available_tokens(self):
        # Duplicate Host copies and unreferenced cache are still reclaimed by
        # native, never freed or locked by this advisory controller.
        return (
            self.host_free_tokens + self.reclaimable_host_tokens
            + self.device_free_tokens
        )


def observe_materialization_headroom(
    cache, *, max_nodes=8192, required_free_tokens=None, protected_host_nodes=(),
):
    """Count reclaimable native extents, or use only observed free slots at the scan bound."""
    try:
        if (
            getattr(cache, "tree_components", ()) != (0,)
            or not getattr(cache, "is_write_back", False)
            or getattr(cache, "host_memory_mode", None) != "cache"
            or getattr(cache, "buffer_pipeline", None) is not None
        ):
            return MaterializationHeadroom(False, "requires_full_only_write_back_cache")
        tree = cache.tree_core
        pool = cache.host_pool_group.entry_map["kv"].host_pool
        capacity, free = pool.size, pool.available_size()
        device_free = cache.token_to_kv_pool_allocator.available_size()
        if (
            type(capacity) is not int or capacity <= 0
            or type(free) is not int or not 0 <= free <= capacity
            or type(device_free) is not int or device_free < 0
        ):
            return MaterializationHeadroom(False, "invalid_pool_geometry")
        if (
            type(required_free_tokens) is int and required_free_tokens >= 0
            and free + device_free >= required_free_tokens
        ):
            return MaterializationHeadroom(
                True, "raw_free_capacity_suffices", host_capacity_tokens=capacity,
                host_free_tokens=free, device_free_tokens=device_free,
            )
        nodes = tree._node_arena
        if type(nodes) is not dict:
            return MaterializationHeadroom(False, "invalid_native_node_census")
        if len(nodes) > max_nodes:
            return MaterializationHeadroom(
                True, NODE_CENSUS_BOUND, host_capacity_tokens=capacity,
                host_free_tokens=free, device_free_tokens=device_free,
            )
        protected = set(protected_host_nodes)
        useful = reclaimable = pinned = tracked = 0
        discardable = {}
        duplicates = tree.full_host_duplicates
        for node in nodes.values():
            if node is tree.root_node:
                continue
            full = node.component_data[0]
            host = full.host_value
            if host is None:
                continue
            size = len(host)
            tracked += size
            pending = (
                node.write_through_pending_id is not None
                or node.load_back_pending_id is not None
            )
            if full.value is None and (
                full.session_ref > 0 or full.host_lock_ref > 0 or full.lock_ref > 0
            ):
                useful += size
            elif (
                not pending and full.host_lock_ref == 0
                and node.id not in protected
                and (
                    full.value is None and full.session_ref == 0 and full.lock_ref == 0
                    or full.value is not None
                    and len(full.value) == size
                    and duplicates.get(node.id) is node
                    and tree._can_reclaim_full_host_duplicate(node)
                )
            ):
                if full.value is None:
                    discardable[node.id] = node
                else:
                    reclaimable += size
            else:
                pinned += size
        # Host-only ancestors cannot be reclaimed through a locked, protected
        # or live descendant. Native eviction removes leaves before ancestors.
        children_left = {
            identity: len(node.children) for identity, node in discardable.items()
        }
        leaves = [
            node for identity, node in discardable.items() if children_left[identity] == 0
        ]
        freed = set()
        while leaves:
            node = leaves.pop()
            freed.add(node.id)
            reclaimable += len(node.component_data[0].host_value)
            parent = node.parent
            if parent is not None and discardable.get(parent.id) is parent:
                children_left[parent.id] -= 1
                if children_left[parent.id] == 0:
                    leaves.append(parent)
        pinned += sum(
            len(node.component_data[0].host_value)
            for identity, node in discardable.items() if identity not in freed
        )
        used = capacity - free
        if tracked > used:
            return MaterializationHeadroom(False, "native_host_accounting_mismatch")
        return MaterializationHeadroom(
            True, census_complete=True, host_capacity_tokens=capacity, host_free_tokens=free,
            device_free_tokens=device_free, useful_host_only_tokens=useful,
            reclaimable_host_tokens=reclaimable, pinned_host_tokens=pinned,
            untracked_host_tokens=used - tracked, inspected_nodes=len(nodes),
        )
    except (AttributeError, IndexError, KeyError, RuntimeError, TypeError, ValueError) as exc:
        return MaterializationHeadroom(False, "native_observation_unavailable:" + type(exc).__name__)


@dataclass
class _InitialRequest:
    key: object
    request: object
    arrived: float
    root_entry: bool = False
    permitted: bool = False
    started: bool = False
    progress: bool = False
    retry_after: float = 0.


class SubagentMaterialization:
    """A pre-materialization filter, not a new source of native admission rights."""

    interval_s = .4
    max_nodes = 8192
    max_candidates = 64
    max_dependency_depth = 64

    def __init__(self, runtime, *, include_root_entries=False):
        self.runtime = runtime
        self.include_root_entries = include_root_entries
        self.decode_growth_clip_tokens = (
            max(0, int(os.environ.get("SGLANG_CLIP_MAX_NEW_TOKENS_ESTIMATION", "4096")))
            if include_root_entries else 0
        )
        self.pending = OrderedDict()
        self.materialized = set()
        self.started_workflows = set()
        self.progress_path = []
        self.next_check = 0.
        self.next_census = 0.
        self.next_running_census = 0.
        self._native_order = ()
        self._running_batch = None
        self._adder = None
        self._native_device_eviction = None
        self._native_host_eviction = None
        self._native_demand_restore = None
        self.last_observation = MaterializationHeadroom(False, "not_sampled")
        self.last_decision = {}

    def attach(self, cache):
        self._restore_native_eviction()
        if (
            not self.include_root_entries
            or getattr(cache, "tree_components", ()) != (0,)
            or not getattr(cache, "is_write_back", False)
            or getattr(cache, "host_memory_mode", None) != "cache"
            or getattr(cache, "buffer_pipeline", None) is not None
        ):
            return
        component = getattr(cache, "components", {}).get(0)
        native_evict = getattr(cache, "_evict_device_leaf", None)
        tree = getattr(cache, "tree_core", None)
        if not (
            callable(native_evict) and hasattr(component, "is_evict_device_ongoing")
            and hasattr(tree, "full_host_duplicates")
        ):
            return
        pools = getattr(getattr(cache, "host_pool_group", None), "entry_map", {})
        pool = getattr(pools.get("kv"), "host_pool", None)
        if not callable(getattr(pool, "available_size", None)):
            return
        host_bytes_per_token = getattr(pool, "size_per_token", None)
        demand = getattr(self.runtime, "_host_demand_capacity", None)
        split_device_suffix = getattr(tree, "split_idle_device_full_suffix", None)
        apply_actions = getattr(cache, "_apply_cache_actions", None)
        page = getattr(tree, "page_size", None)
        can_split = (
            callable(split_device_suffix) and callable(apply_actions)
            and type(page) is int and page > 0
        )
        discard_leaf = getattr(tree, "discard_terminated_leaf", None)
        can_discard_cold = (
            getattr(cache, "enable_session_radix_cache", False) is True
            and callable(discard_leaf)
            and callable(getattr(cache, "_free_values", None))
            and callable(getattr(cache, "_accumulate_tracker", None))
        )

        from sglang.srt.environ import envs

        def evict_device_leaf(node_id, tracker):
            if component.is_evict_device_ongoing:
                node = tree.node_by_id(node_id)
                full = node.component_data[0]
                before = tracker[0]
                needed = component._evict_device_request_cnt - before
                if needed > 0 and full.host_value is None and full.value is not None:
                    host_free = pool.available_size()
                    size = len(full.value)
                    fits_host = size <= host_free
                    available_for_padding = host_free
                    if (
                        size > needed and can_split and demand is not None
                        and type(host_bytes_per_token) is int and host_bytes_per_token > 0
                    ):
                        unit = host_bytes_per_token
                        available_for_padding -= (demand.reserve() + unit - 1) // unit
                    if fits_host and (size <= needed or size <= available_for_padding):
                        return native_evict(node_id, tracker)
                    counts = self.runtime.counts
                    counts["subagent_materialization_writeback_pressure_checks"] += 1
                    if (
                        can_discard_cold and full.session_ref == 0 and not node.children
                        and host_free < min(size, needed)
                        and node in tree.evictable_device_leaves
                    ):
                        # The native leaf check also validates identity, locks and DMA.
                        result = discard_leaf(node_id, node.creation_time)
                        if result.is_dropped:
                            cache._free_values(result.device_frees, result.host_frees)
                            cache._accumulate_tracker(tracker, result.tracker)
                            counts["subagent_materialization_cold_device_discarded_nodes"] += 1
                            counts["subagent_materialization_cold_device_discarded_tokens"] += (
                                tracker[0] - before
                            )
                            return None
                    if not fits_host and tree.full_host_duplicates and self._use_backed_device_leaves(
                        component, tree, native_evict, tracker, needed,
                    ):
                        counts["subagent_materialization_writeback_avoided"] += 1
                        counts["subagent_materialization_writeback_avoided_device_tokens"] += (
                            tracker[0] - before
                        )
                        return None
                    if not can_split or full.lock_ref or node not in tree.evictable_device_leaves:
                        return native_evict(node_id, tracker)
                    needed = component._evict_device_request_cnt - tracker[0]
                    shortfall = (needed + page - 1) // page * page
                    chunk = self.runtime._native_chunk_input_reserve
                    if needed <= 0 or (chunk is None and host_free < shortfall):
                        return native_evict(node_id, tracker)
                    # Retain one native prefill chunk of allocation headroom to
                    # amortize decode writes, unless a smaller write avoids Host reclaim.
                    chunk_pages = (chunk + page - 1) // page * page if chunk else 0
                    write_tokens = min(size, max(shortfall, chunk_pages))
                    padding_saved = size - write_tokens if fits_host else 0
                    if shortfall < write_tokens:
                        safe_host = available_for_padding
                        if (
                            host_free < shortfall
                            and not envs.SGLANG_HICACHE_SKIP_HOST_DUPLICATE_RECLAIM.get()
                        ):
                            for mirror in islice(tree.full_host_duplicates.values(), self.max_candidates):
                                if tree._can_reclaim_full_host_duplicate(mirror):
                                    safe_host += len(mirror.component_data[0].host_value)
                                    if safe_host >= write_tokens:
                                        break
                        # Pay for required device space even under pressure.
                        # Additional padding cannot use the Host demand reserve.
                        bounded = min(write_tokens, max(shortfall, safe_host // page * page))
                        padding_saved += write_tokens - bounded
                        write_tokens = bounded
                    if write_tokens < size:
                        last_access = node.last_access_time
                        split = split_device_suffix(
                            node_id, node.creation_time, write_tokens, read_only_backup=True,
                        )
                        if split is not None:
                            node.parent.last_access_time = last_access
                            node.last_access_time = last_access
                            apply_actions(split[1])
                            counts["subagent_materialization_writeback_split_nodes"] += 1
                            counts["subagent_materialization_writeback_split_prefix_tokens"] += (
                                size - write_tokens
                            )
                            if padding_saved:
                                counts["subagent_materialization_writeback_padding_capped"] += 1
                                counts["subagent_materialization_writeback_padding_tokens_avoided"] += padding_saved
            return native_evict(node_id, tracker)

        cache._evict_device_leaf = evict_device_leaf
        self._native_device_eviction = cache, native_evict, evict_device_leaf
        self._attach_host_eviction_trimming(cache, tree, component)
        self._attach_demand_restore_pressure(cache)

    def _attach_demand_restore_pressure(self, cache):
        native_load = getattr(cache, "init_load_back", None)
        if (
            not getattr(self.runtime, "demand_prefill_pipeline_enabled", False)
            or not callable(native_load)
            or getattr(cache, "tp_world_size", None) != 1
            or getattr(cache, "pp_size", None) != 1
        ):
            return
        from sglang.srt.environ import envs

        if envs.SGLANG_HICACHE_SKIP_HOST_DUPLICATE_RECLAIM.get():
            return

        loading_check = getattr(cache, "loading_check", None)

        def init_load_back(params):
            acks = getattr(cache.cache_controller, "ack_load_queue", ())
            hit = params.host_hit_length
            if (
                acks and callable(loading_check)
                and hit >= max(1, cache.load_back_threshold)
                and cache.token_to_kv_pool_allocator.available_size() < hit
            ):
                # Only native ACKs already complete since the last poll can fund this allocation.
                ready = 0
                for ack in islice(acks, self.max_candidates):
                    if not ack.finish_event.query():
                        break
                    ready += 1
                if ready:
                    loading_check(finish_count=ready)
                    self.runtime.counts["subagent_materialization_restore_acks_reaped"] += ready
            if self._defer_demand_restore(cache, params):
                # Native OTHER ends this selection pass and submits its selected
                # loads. Returning an empty hit here would start recomputation.
                return None
            previous = getattr(cache, "_beliefkv_demand_restore_request_id", None)
            cache._beliefkv_demand_restore_request_id = getattr(params.req, "rid", None)
            try:
                return native_load(params)
            finally:
                cache._beliefkv_demand_restore_request_id = previous

        cache.init_load_back = init_load_back
        self._native_demand_restore = cache, native_load, init_load_back

    def _defer_demand_restore(self, cache, params):
        queue = cache.cache_controller.load_queue
        selected = getattr(self._adder, "can_run_list", ())
        hit = params.host_hit_length
        if (
            not queue or not selected or len(queue) > self.max_candidates
            or hit < max(1, cache.load_back_threshold)
            or params.mem_quota is not None
            or cache.token_to_kv_pool_allocator.available_size() >= hit
        ):
            return False
        pipeline = getattr(self.runtime, "_demand_prefill_pipeline", None)
        if (
            pipeline is None or pipeline.pending is not None
            or not pipeline.has_decode_work(self._running_batch)
        ):
            return False
        req_id = params.req.rid
        key = self.runtime.visible.get(req_id)
        if key is None or req_id in (
            getattr(self.runtime, "_join_priority_promoted", None),
            getattr(self.runtime, "_join_last_child_priority_promoted", None),
            getattr(self.runtime, "_final_priority_promoted", None),
        ):
            return False
        guard = getattr(self.admission, "_ready_service_guard", None)
        if guard is not None and guard.elapsed_service_timeout(key, time.monotonic()):
            return False

        tree = cache.tree_core
        selected_anchors = {req.last_node for req in selected}
        if any(
            op.beliefkv_command_id is not None or len(op.node_ids) != 1
            or op.node_ids[0] not in selected_anchors
            or op.node_ids[0] not in cache.ongoing_load_back
            for op in queue
        ):
            return False
        # A bounded walk gives a lower bound on settled mirrors available after ACK.
        credit, remaining = 0, self.max_candidates
        for op in queue:
            if remaining == 0:
                break
            anchor = op.node_ids[0]
            node = tree.node_by_id(anchor)
            while remaining and node is not tree.root_node and node.load_back_pending_id == anchor:
                remaining -= 1
                full = node.component_data[0]
                if (
                    node.write_through_pending_id is None
                    and full.host_lock_ref == int(node.id == anchor)
                    and full.value is not None and full.host_value is not None
                    and len(full.value) == len(full.host_value)
                ):
                    credit += len(full.host_value)
                node = node.parent
        if not credit:
            return False

        counts = self.runtime.counts
        cpu = time.thread_time_ns()
        headroom = observe_materialization_headroom(
            cache, max_nodes=self.max_nodes, required_free_tokens=hit,
            # Native pins this anchor before reclaiming. It cannot fund its own load.
            protected_host_nodes=(params.best_match_node,),
        )
        counts["subagent_materialization_restore_pressure_checks"] += 1
        counts["subagent_materialization_restore_pressure_check_cpu_ns"] += (
            time.thread_time_ns() - cpu
        )
        shortfall = hit - headroom.available_tokens
        if not headroom.census_complete or shortfall <= 0:
            return False
        chunk = self.runtime._native_chunk_input_reserve
        if shortfall > credit and (not chunk or credit < chunk):
            return False
        counts["subagent_materialization_restore_pressure_deferrals"] += 1
        counts["subagent_materialization_restore_pressure_shortfall_tokens"] += shortfall
        counts["subagent_materialization_restore_pressure_covered_tokens"] += min(shortfall, credit)
        writer = getattr(self.runtime, "_opportunity_writer", None)
        if writer is not None:
            writer.record({
                "event": "subagent_materialization_restore_pressure_deferred",
                "ts_ms": time.time() * 1000.,
                "key": asdict(key),
                "native_node_ids": [op.node_ids[0] for op in queue],
                "requested_restore_tokens": hit,
                "unfunded_restore_tokens": shortfall,
                "reclaimable_after_ack_tokens": credit,
            })
        return True

    def _attach_host_eviction_trimming(self, cache, tree, component):
        drive_native = getattr(component, "drive_host_eviction", None)
        split_host_prefix = getattr(tree, "split_host_only_full_prefix", None)
        apply_actions = getattr(cache, "_apply_cache_actions", None)
        page = getattr(tree, "page_size", None)
        if not (
            callable(drive_native) and callable(split_host_prefix) and callable(apply_actions)
            and callable(getattr(tree, "_evict_host_leaf", None))
            and type(page) is int and page > 0
        ):
            return

        def drive_host_eviction(num_tokens, tracker, device_frees, host_frees):
            if tracker[0] >= num_tokens:
                return drive_native(num_tokens, tracker, device_frees, host_frees)
            evict_leaf = tree._evict_host_leaf

            def evict_host_leaf(node, leaf_tracker, device_frees, host_frees):
                full = node.component_data[0]
                if leaf_tracker is tracker and full.session_ref > 0 and full.host_value is not None:
                    # Native has already credited duplicate copies and earlier
                    # victims. Keep the referenced prefix of the final leaf.
                    remaining = num_tokens - tracker[0]
                    evicted = (remaining + page - 1) // page * page
                    retained = len(full.host_value) - evicted
                    if remaining > 0 and retained > 0:
                        last_access = node.last_access_time
                        split = split_host_prefix(node.id, node.creation_time, retained)
                        if split is not None:
                            prefix_id, actions = split
                            # A capacity trim is not a cache access.
                            tree.node_by_id(prefix_id).last_access_time = last_access
                            apply_actions(actions)
                            counts = self.runtime.counts
                            counts["subagent_materialization_host_eviction_trimmed_nodes"] += 1
                            counts["subagent_materialization_host_eviction_retained_tokens"] += retained
                # A native split preserves the selected node ID for the suffix.
                return evict_leaf(node, leaf_tracker, device_frees, host_frees)

            tree._evict_host_leaf = evict_host_leaf
            try:
                return drive_native(num_tokens, tracker, device_frees, host_frees)
            finally:
                tree._evict_host_leaf = evict_leaf

        component.drive_host_eviction = drive_host_eviction
        self._native_host_eviction = component, drive_native, drive_host_eviction

    def _use_backed_device_leaves(self, component, tree, native_evict, tracker, needed):
        # Plan the entire shortfall before demoting anything. Otherwise the
        # ensuing write-back could evict the newly created Host-only copies.
        plan = self._plan_backed_device_demotions(component, tree, needed)
        if not plan:
            return False
        before = tracker[0]
        for node in plan:
            assert node in tree.evictable_device_leaves
            native_evict(node.id, tracker)
        return tracker[0] - before >= needed

    def _plan_backed_device_demotions(self, component, tree, needed):
        duplicates = tree.full_host_duplicates
        leaves = tree.evictable_device_leaves
        native_heap = component._evict_device_heap
        limit = self.max_candidates

        def settled(node):
            if not (
                duplicates.get(node.id) is node
                and tree._can_reclaim_full_host_duplicate(node)
            ):
                return False
            data = node.component_data[0]
            return len(data.value) == len(data.host_value)

        if len(duplicates) < len(native_heap):
            heap = [
                (component.session_ref_eviction_strategy(node), node)
                for node in islice(duplicates.values(), limit)
                if node in leaves and settled(node)
            ]
        else:
            heap = [
                (priority, node) for priority, node in islice(native_heap, limit)
                if node in leaves and settled(node)
            ]
        heapify(heap)
        plan, seen, planned = [], {node.id for _, node in heap}, set()
        unplanned_children = {}
        remaining_edges = limit
        total = 0
        while heap and len(plan) < limit:
            _, node = heappop(heap)
            if node.id in planned:
                continue
            plan.append(node)
            planned.add(node.id)
            total += len(node.component_data[0].value)
            if total >= needed:
                return plan
            parent = node.parent
            if (
                parent is None or parent.id in seen or not settled(parent)
                or parent.component_data[0].lock_ref
            ):
                continue
            remaining = unplanned_children.get(parent.id)
            if remaining is None:
                child_count = len(parent.children)
                if child_count > remaining_edges:
                    continue
                remaining_edges -= child_count
                remaining = sum(
                    child.id not in planned and child.component_data[0].value is not None
                    for child in parent.children.values()
                )
            else:
                remaining -= 1
            if remaining:
                unplanned_children[parent.id] = remaining
            else:
                seen.add(parent.id)
                heappush(heap, (component.session_ref_eviction_strategy(parent), parent))
        return ()

    def _restore_native_eviction(self):
        if self._native_demand_restore is not None:
            cache, native_load, init_load_back = self._native_demand_restore
            if cache.init_load_back is init_load_back:
                cache.init_load_back = native_load
            self._native_demand_restore = None
        if self._native_device_eviction is not None:
            cache, native_evict, evict_device_leaf = self._native_device_eviction
            if cache._evict_device_leaf is evict_device_leaf:
                cache._evict_device_leaf = native_evict
            self._native_device_eviction = None
        if self._native_host_eviction is not None:
            component, drive_native, drive_host_eviction = self._native_host_eviction
            if component.drive_host_eviction is drive_host_eviction:
                component.drive_host_eviction = drive_native
            self._native_host_eviction = None

    @property
    def admission(self):
        return self.runtime._agent_admission

    @staticmethod
    def _context(key):
        return key.root_workflow_id, key.context_id

    @staticmethod
    def _untouched(req):
        kv = getattr(req, "kv", None)
        return bool(
            kv is not None and getattr(kv, "req_pool_idx", False) is None
            and all(type(value := getattr(kv, name, None)) is int and value == 0
                    for name in ("kv_allocated_len", "kv_committed_len"))
            and not getattr(req, "output_ids", ())
            and not getattr(req, "retracted_stain", False)
            and callable(getattr(req, "finished", None)) and not req.finished()
        )

    def retain_rebound_prefix(self, req, key, previous):
        """Carry a verified prefix into the first request of a new session."""
        if (
            self._native_device_eviction is None
            or previous.session_id is None or key.session_id is None
            or previous.context_epoch >= key.context_epoch
            or (previous.root_workflow_id, previous.context_id) != self._context(key)
            or not self._untouched(req)
            or getattr(req, "positional_embed_overrides", None) is not None
        ):
            return
        context = self.runtime.graph.contexts.get(key.context_id)
        if context is None or context.epoch != key.context_epoch:
            return
        ids = req.origin_input_ids
        prefix_limit = getattr(req, "_compute_max_prefix_len", None)
        if not isinstance(ids, array) or ids.typecode != "q" or not callable(prefix_limit):
            return
        cache = self.runtime._native_cache
        tracker = getattr(cache, "session_refs", None)
        if (
            tracker is None or cache.tree_core.is_eagle
            or cache.tp_world_size != 1 or cache.pp_size != 1 or cache.linker is not None
        ):
            return
        released = tracker.snapshot_released_session_leaf_anchors(
            previous.session_id, previous.session_generation,
        )
        if not released or not any(leaves for _, leaves in released):
            return
        target = tracker.snapshot_session_leaf_anchors(key.session_id, key.session_generation)
        if target is None or any(leaves for _, leaves in target):
            return

        from sglang.srt.environ import envs
        from sglang.srt.mem_cache.base_prefix_cache import MatchPrefixParams
        from sglang.srt.mem_cache.radix_cache import RadixKey

        if (
            envs.SGLANG_RADIX_FORCE_MISS.get()
            or getattr(getattr(req, "session", None), "streaming", False)
        ):
            return
        limit = prefix_limit(len(ids))
        if not limit:
            return
        match = cache.match_prefix(MatchPrefixParams(
            key=RadixKey(
                ids, extra_key=req.extra_key, cache_salt=req.cache_salt, limit=limit,
            ),
        ))
        tokens = len(match.device_indices) + match.host_hit_length
        if tokens:
            # Reuse the native session's eviction priority without pinning any pages.
            tracker.register_session_ref(SimpleNamespace(
                session_id=key.session_id, session_generation=key.session_generation,
                session=None, last_node=match.best_match_node,
                beliefkv_metadata=req.beliefkv_metadata,
            ))
            self.runtime.counts["subagent_materialization_rebound_prefix_tokens"] += tokens

    def registered(self, req, key):
        a, runtime = self.admission, self.runtime
        invocation = runtime.graph.invocations.get(key.invocation_id)
        item = a.policy.programs[key.invocation_id]
        child = bool(
            invocation is not None and invocation.parent_invocation_id
            and invocation.relation_type in (RelationType.SPAWN, RelationType.CALL)
        )
        existing = self.pending.get(key.request_id)
        if existing is not None:
            if existing.key == key and child and not existing.started:
                existing.root_entry = False
            return
        if (
            self._context(key) in self.materialized or item.steps != 1 or not self._untouched(req)
        ):
            return
        root_entry = False
        if self.include_root_entries and not child and key.root_workflow_id not in self.started_workflows:
            if invocation is not None:
                root_entry = (
                    invocation.workflow_id == key.root_workflow_id
                    and invocation.context_id == key.context_id
                    and invocation.parent_invocation_id is None
                    and invocation.relation_type is RelationType.ROOT
                )
            else:
                # Ingress can precede delivery of the matching causal event.
                metadata = req.beliefkv_metadata
                root_entry = (
                    metadata.get("relation_type") == "root"
                    and metadata.get("parent_invocation_id") is None
                )
        if not (child or root_entry):
            return
        self.pending[key.request_id] = _InitialRequest(
            key, req, runtime._visible_since.get(key.request_id, time.monotonic()),
            root_entry=root_entry,
        )
        if item.active:
            a.policy._pause(item, time.monotonic())
        item.marked_for_pause = False
        runtime.semantic_revision += 1
        runtime.counts["subagent_materialization_registered"] += 1
        if root_entry:
            runtime.counts["subagent_materialization_root_registered"] += 1

    def allows(self, key):
        record = self.pending.get(key.request_id) if key is not None else None
        return record is None or record.key == key and (record.started or record.permitted)

    def held_ready_root(self, key):
        record = self.pending.get(key.request_id)
        return (
            record is not None and record.key == key and record.root_entry
            and not record.started and not record.permitted and self._live(record)
            and self._ready(key)
        )

    def _live(self, record):
        key, req = record.key, record.request
        a, runtime = self.admission, self.runtime
        return bool(
            runtime.visible.get(key.request_id) == key
            and _request_key(req, known_keys=runtime.visible) == key
            and a.latest.get(key.invocation_id) == key
            and a._live_requests.get(key.request_id) is req
            and not runtime._terminal(key)
        )

    def _ready(self, key):
        invocation = self.runtime.graph.invocations.get(key.invocation_id)
        context = self.runtime.graph.contexts.get(key.context_id)
        return bool(
            invocation is not None and invocation.workflow_id == key.root_workflow_id
            and invocation.context_id == key.context_id
            and invocation.state in (InvocationState.READY, InvocationState.RUNNING_LLM)
            and not invocation.active_tool_calls and not invocation.blocking_child_ids
            and context is not None and context.epoch == key.context_epoch
            and context.workflow_id == key.root_workflow_id
        )

    def _price(self, record):
        # Once native starts a chunk, allocated slots already reduce free HBM.
        # Continue reserving the unallocated suffix, not the entire prompt again.
        allocated = getattr(getattr(record.request, "kv", None), "kv_allocated_len", 0)
        return self._input_price(record.request, allocated if record.started else 0)

    def _input_price(self, req, allocated):
        page = self.runtime._native_page_size
        buffer = self.admission.policy.buffer_tokens
        if self.include_root_entries:
            generated = len(req.output_ids)
            maximum = getattr(getattr(req, "sampling_params", None), "max_new_tokens", None)
            growth = (
                min(max(0, maximum - generated), self.decode_growth_clip_tokens)
                if type(maximum) is int else 0
            )
            buffer = max(buffer, generated + growth)
        tokens = len(req.origin_input_ids) + buffer
        if type(allocated) is int and allocated >= 0:
            tokens = max(0, tokens - allocated)
        return (tokens + page - 1) // page * page

    def _input_commitments(self, adder):
        runtime, a = self.runtime, self.admission
        committed = {
            getattr(req, "rid", None): req for req in getattr(adder, "can_run_list", ())
        }
        pending = getattr(getattr(runtime, "_demand_prefill_pipeline", None), "pending", None)
        for member in getattr(pending, "members", ()):
            key = member.key
            if (
                runtime.visible.get(key.request_id) == key
                and a.latest.get(key.invocation_id) == key
                and a._live_requests.get(key.request_id) is member.request
            ):
                committed[key.request_id] = member.request
        return committed

    def _outstanding_inputs(self, running_batch, committed, queued, *, refresh):
        runtime, a = self.runtime, self.admission
        running = {
            getattr(req, "rid", None) for req in getattr(running_batch, "reqs", ())
        }
        outstanding = initial = ready_continuations = 0
        for rid, req in a._live_requests.items():
            key = runtime.visible.get(rid)
            if (
                key is None or a.latest.get(key.invocation_id) != key
                or _request_key(req, known_keys=runtime.visible) != key
                or runtime._terminal(key) or req.finished()
            ):
                continue
            record = self.pending.get(rid)
            selected = committed.get(rid) is req
            kv = getattr(req, "kv", None)
            allocated = getattr(kv, "kv_allocated_len", 0)
            if not (
                record is not None and record.started
                or selected or rid in running
                or getattr(kv, "req_pool_idx", None) is not None
                or type(allocated) is int and allocated > 0
            ):
                if (
                    self.include_root_entries and record is None and queued.get(rid) is req
                    and self._context(key) in self.materialized and a.allows(key)
                    and self._ready(key)
                ):
                    # A logically admitted continuation can still be waiting for
                    # the next native batch. Its reusable prefix is already in the census.
                    reentry = runtime._read_request_reentry(req, key=key, refresh=refresh)
                    prefix = reentry.get("checkpoint_tokens", 0) if reentry is not None else 0
                    if type(prefix) is not int or not 0 <= prefix < len(req.origin_input_ids):
                        prefix = 0
                    ready_continuations += self._input_price(req, prefix)
                continue
            # Staged H2D retains the native selection after clearing the adder.
            # Both selections hold their prefix before allocating new slots.
            if selected:
                allocated = max(allocated, len(getattr(req, "prefix_indices", ())))
            price = self._input_price(req, allocated)
            outstanding += price
            if record is not None and record.started:
                initial += price
        return outstanding + ready_continuations, initial, ready_continuations

    def _reserve(self, running_batch, adder):
        runtime, cache = self.runtime, self.runtime._native_cache
        page = runtime._native_page_size
        running = len(getattr(running_batch, "reqs", ()))
        selected = len(getattr(adder, "can_run_list", ()))
        reserve = page * (running + selected + 1)
        demand = getattr(runtime, "_host_demand_capacity", None)
        if demand is not None:
            unit = cache.host_pool_group.entry_map["kv"].host_pool.size_per_token
            host_reserve = (demand.reserve() + unit - 1) // unit
            reserve = reserve + host_reserve if self.include_root_entries else max(reserve, host_reserve)
        return reserve

    def _ordered(self, records):
        a = self.admission
        combination = getattr(a, "_combination", None)
        if not getattr(a, "remaining_work_ranking_enabled", True):
            return records
        if combination is None:
            if not getattr(a, "rank_before_category", False):
                return records
            # Held children need the existing rank without admission-window
            # or free-budget filters, which would reject them before release.
            by_key = {record.key: record for record in records}
            return [
                by_key[key] for key in a._order_work_keys(
                    list(by_key), self.runtime._join_priority_pending,
                )
            ]
        predictions = {}
        return sorted(
            records,
            key=lambda record: -combination._work_weight(record.key, predictions),
        )

    def _blocking_join(self, record, joins):
        a, runtime = self.admission, self.runtime
        identity = record.key.invocation_id
        for join_id in sorted(runtime._join_by_invocation.get(identity, ())):
            if join_id not in joins:
                join = runtime.graph.joins.get(join_id)
                joins[join_id] = (
                    join if join is not None and join.mode is JoinMode.ALL
                    and any(
                        a._waiting_on_join(runtime.graph.invocations.get(waiter), join)
                        for waiter in join.waiter_invocation_ids
                    ) else None
                )
            join = joins[join_id]
            if (
                join is not None and join.workflow_id == record.key.root_workflow_id
                and identity in join.member_invocation_ids and identity not in join.completed_member_ids
            ):
                return join_id
        return None

    def _pressure_order(self, records):
        runtime = self.runtime
        joins, blocking, children, roots = {}, {}, [], []
        for record in records:
            if record.root_entry:
                roots.append(record)
                continue
            child = runtime.graph.invocations[record.key.invocation_id]
            parent = runtime.graph.invocations.get(child.parent_invocation_id)
            join_id = self._blocking_join(record, joins)
            if join_id is not None or (
                parent is not None and parent.workflow_id == child.workflow_id
                and parent.state is InvocationState.WAIT_CHILD and not parent.active_tool_calls
                and child.invocation_id in parent.blocking_child_ids
            ):
                blocking[record.key] = (record, join_id)
            else:
                children.append(record)
        window = list(blocking.values())[:self.max_candidates]
        groups = {}
        for record in self._ordered([record for record, _ in window]):
            join_id = blocking[record.key][1]
            groups.setdefault((join_id, None if join_id is not None else record.key), []).append(record)
        ordered = [record for group in groups.values() for record in group]
        ordered.extend(self._ordered(children[:self.max_candidates - len(ordered)]))
        # Rotate rejected windows before applying FIFO within the next window.
        root_window = roots[:self.max_candidates - len(ordered)]
        ordered.extend(sorted(root_window, key=lambda record: record.arrived))
        return ordered

    def _progress_candidate(self, records, native_order, running_batch, adder):
        runtime = self.runtime
        if getattr(running_batch, "reqs", ()) or getattr(adder, "can_run_list", ()):
            return None
        # A real existing/root request has precedence even if it must wait for
        # its ordinary logical or physical recovery, not just if already active.
        if any(
            (key := runtime.visible.get(getattr(req, "rid", None))) is not None
            and key.request_id not in self.pending and self._ready(key)
            and not runtime._terminal(key)
            for req in native_order
        ):
            return None
        self.progress_path[:] = [
            owner for owner in self.progress_path
            if (invocation := runtime.graph.invocations.get(owner)) is not None
            and not invocation.state.terminal
        ]
        if not self.progress_path:
            return next((
                record for record in records
                if (
                    self.admission.policy.programs[record.key.invocation_id].tokens
                    + self.admission.policy.buffer_tokens
                    <= self.admission.policy.capacity_tokens
                )
            ), None)
        parent = runtime.graph.invocations[self.progress_path[-1]]
        if parent.active_tool_calls or parent.state not in (
            InvocationState.WAIT_JOIN, InvocationState.WAIT_CHILD,
        ):
            return None
        for record in records:
            item = self.admission.policy.programs[record.key.invocation_id]
            if item.tokens + self.admission.policy.buffer_tokens > self.admission.policy.capacity_tokens:
                continue
            child = runtime.graph.invocations[record.key.invocation_id]
            for _ in range(self.max_dependency_depth):
                ancestor = runtime.graph.invocations.get(child.parent_invocation_id)
                if ancestor is None or ancestor.workflow_id != parent.workflow_id:
                    break
                if (
                    child.invocation_id not in ancestor.blocking_child_ids
                    and not any(
                        (join := runtime.graph.joins.get(join_id)) is not None
                        and not join.satisfied
                        and join.workflow_id == ancestor.workflow_id
                        and ancestor.join_id == join_id
                        and ancestor.state in (InvocationState.WAIT_JOIN, InvocationState.WAIT_CHILD)
                        and child.invocation_id in join.member_invocation_ids
                        and ancestor.invocation_id in (
                            join.waiter_invocation_ids - join.released_waiter_ids
                        )
                        for join_id in runtime._join_by_invocation.get(child.invocation_id, ())
                    )
                ):
                    break
                if ancestor is parent:
                    return record
                child = ancestor
        return None

    def _publish(self, record, allowed, now):
        if record.permitted == allowed:
            return
        record.permitted = allowed
        record.retry_after = 0.
        a, runtime, key = self.admission, self.runtime, record.key
        item = a.policy.programs.get(key.invocation_id)
        if not allowed and item is not None and item.active and self._untouched(record.request):
            a.policy._pause(item, now)
            item.marked_for_pause = False
        runtime.semantic_revision += 1
        # Existing periodic/free/combination selectors retain the actual grant.
        deferred = getattr(a, "_deferred_dispatch", None)
        guard = getattr(a, "_ready_service_guard", None)
        overdue_root_release = (
            allowed and record.root_entry and guard is not None
            and guard.elapsed_service_timeout(key, now)
        )
        # Ordinary permits follow the deferred window. Reconcile revocations
        # and an overdue root's newly available capacity on this tick.
        if not allowed or deferred is None or overdue_root_release:
            a.next_tick = 0.
        remember = getattr(a, "_remember_dispatch", None)
        if remember is not None:
            remember(key)
        defer = getattr(deferred, "defer", None)
        if allowed and defer is not None:
            defer(key)
        runtime.counts[
            "subagent_materialization_permits" if allowed else "subagent_materialization_revocations"
        ] += 1

    def prepare(self, native_order, *, running_batch, adder, now=None, _starting=False):
        self._native_order = native_order
        self._running_batch = running_batch
        self._adder = adder
        if not self.pending or all(record.started for record in self.pending.values()):
            return
        now = time.monotonic() if now is None else now
        if not _starting and now < self.next_check:
            return
        self.next_check = now + self.interval_s
        cpu = time.thread_time_ns()
        runtime = self.runtime
        counts = runtime.counts
        counts["subagent_materialization_checks"] += 1
        try:
            for request_id, record in tuple(self.pending.items()):
                if not self._live(record):
                    self.pending.pop(request_id)
                    continue
                if not record.started and not self._untouched(record.request):
                    self.admitted(record.request)
            try:
                reserve = self._reserve(running_batch, adder)
            except (AttributeError, KeyError, TypeError, ValueError, ZeroDivisionError):
                reserve = 0
            committed = self._input_commitments(adder)
            committed_initial = {
                rid for rid, record in self.pending.items()
                if not record.started and committed.get(rid) is record.request
            }
            queued = {getattr(req, "rid", None): req for req in native_order}
            outstanding, initial, ready_continuations = self._outstanding_inputs(
                running_batch, committed, queued, refresh=_starting,
            )
            required = reserve + outstanding + sum(
                self._price(record) for rid, record in self.pending.items()
                if not record.started and rid not in committed_initial
            )
            raw = observe_materialization_headroom(
                runtime._native_cache, max_nodes=0, required_free_tokens=required,
            )
            if raw.reason == NODE_CENSUS_BOUND and not _starting:
                census_due = self.next_census
                if self.include_root_entries and getattr(running_batch, "reqs", ()):
                    # Capacity events can rearm the free-page check on every tick.
                    census_due = max(census_due, self.next_running_census)
                if now < census_due:
                    self.next_check = min(self.next_check, census_due)
                    counts["subagent_materialization_pressure_backoff_checks"] += 1
                    return
            self.last_observation = observation = (
                observe_materialization_headroom(
                    runtime._native_cache, max_nodes=self.max_nodes,
                    required_free_tokens=required,
                    protected_host_nodes=(
                        lease.node_id for lease in runtime._prefetch_service_leases.values()
                    ),
                ) if raw.reason == NODE_CENSUS_BOUND else raw
            )
            if observation.census_complete or observation.reason == NODE_CENSUS_BOUND:
                self.next_census = now + self.admission.control_interval_s
                self.next_running_census = now + self.interval_s
            counts["subagent_materialization_complete_censuses"] += observation.census_complete
            counts["subagent_materialization_census_nodes"] += observation.inspected_nodes
            # Unknown geometry preserves the original scheduler rather than
            # silently treating unobserved Host state as zero or deadlocking.
            reserve = reserve if observation.observable else 0
            available = observation.available_tokens - reserve - outstanding
            candidates = [
                record for record in self.pending.values()
                if not record.started and record.key.request_id in queued
                and record.key.request_id not in committed_initial
                and self._ready(record.key)
            ]
            if self.include_root_entries and observation.observable and observation.available_tokens < required:
                # A request that cannot use the remaining budget should not
                # crowd a feasible request out of the bounded ranking window.
                fitting = [
                    record for record in candidates
                    if record.progress or self._price(record) <= available
                ]
                candidates = self._pressure_order(fitting or candidates)
            else:
                candidates = self._ordered(candidates[:self.max_candidates])
            # Native selections are already charged in outstanding. Preserve
            # their permission until native commits or cancels that selection.
            allowed = set(committed_initial)
            if observation.observable:
                for record in candidates:
                    if record.progress:
                        allowed.add(record.key.request_id)
                        available -= self._price(record)
            for record in candidates:
                if record.key.request_id in allowed:
                    continue
                price = self._price(record)
                if not observation.observable or price <= available:
                    allowed.add(record.key.request_id)
                    available -= price
            progress = None
            if observation.observable and not allowed and not outstanding:
                progress = self._progress_candidate(
                    candidates, native_order, running_batch, adder,
                )
                if progress is not None:
                    progress.progress = True
                    allowed.add(progress.key.request_id)
                    owner = progress.key.invocation_id
                    if owner not in self.progress_path:
                        self.progress_path.append(owner)
                    counts["subagent_materialization_progress_exceptions"] += 1
                    counts["subagent_materialization_progress_deficit_tokens"] += max(
                        0, self._price(progress) - available,
                    )
            if not observation.observable:
                allowed.update(
                    rid for rid, record in self.pending.items()
                    if not record.started and (not record.root_entry or self._ready(record.key))
                )
            for record in self.pending.values():
                if not record.started:
                    self._publish(record, record.key.request_id in allowed, now)
            for record in candidates:
                if record.key.request_id not in allowed:
                    self.pending.move_to_end(record.key.request_id)
            counts["subagent_materialization_observation_fallbacks"] += not observation.observable
            decision = {
                "headroom": asdict(observation), "reserved_tokens": reserve,
                "outstanding_initial_tokens": initial,
                "outstanding_input_tokens": outstanding,
                "committed_initial_request_ids": sorted(committed_initial),
                "available_before_tokens": observation.available_tokens - reserve - outstanding,
                "candidates": [asdict(record.key) for record in candidates],
                "allowed": sorted(allowed),
                "held": [
                    record.key.request_id for record in self.pending.values()
                    if not record.started and not record.permitted
                ],
                "progress_exception": progress.key.request_id if progress is not None else None,
            }
            if self.include_root_entries:
                decision.update(
                    ready_continuation_tokens=ready_continuations,
                    root_candidates=[record.key.request_id for record in candidates if record.root_entry],
                    held_root_request_ids=[
                        rid for rid in decision["held"] if self.pending[rid].root_entry
                    ],
                )
            # Repeated checks remain counters; journal only a changed decision.
            if decision != self.last_decision:
                self.last_decision = decision
                writer = getattr(runtime, "_opportunity_writer", None)
                if writer is not None:
                    writer.record({
                        "event": "subagent_materialization_decision",
                        "ts_ms": time.time() * 1000., "monotonic_s": now, **decision,
                        "physical_capacity_credit": False,
                    })
        finally:
            counts["subagent_materialization_cpu_ns"] += time.thread_time_ns() - cpu

    def defer_start(self, req):
        record = self.pending.get(getattr(req, "rid", None))
        if record is None or record.started:
            return False
        if record.request is not req or not self._live(record) or not record.permitted:
            return True
        chunk = getattr(self._adder, "rem_chunk_tokens", None)
        if type(chunk) is int and chunk <= 0 and getattr(self._adder, "dllm_config", None) is None:
            self.runtime.counts["subagent_materialization_input_budget_deferrals"] += 1
            return True
        if time.monotonic() < record.retry_after:
            self.runtime.counts["subagent_materialization_native_retry_backoffs"] += 1
            return True
        # A permission waits for ordinary admission; it is not a physical lease.
        # Revalidate once at the real native start, before prefix match or H2D.
        self.runtime.counts["subagent_materialization_start_checks"] += 1
        self.prepare(
            self._native_order, running_batch=self._running_batch, adder=self._adder,
            _starting=True,
        )
        return not self.allows(record.key)

    def rejected(self, req, result):
        record = self.pending.get(getattr(req, "rid", None))
        if (
            result == "NO_TOKEN" and record is not None
            and record.request is req and not record.started
        ):
            record.retry_after = time.monotonic() + self.interval_s

    def admitted(self, req):
        key = self.runtime.visible.get(getattr(req, "rid", None))
        if key is not None:
            self.materialized.add(self._context(key))
            if self.include_root_entries:
                self.started_workflows.add(key.root_workflow_id)
        record = self.pending.get(getattr(req, "rid", None))
        if record is None or record.started:
            return
        record.started = record.permitted = True
        self.runtime.counts["subagent_materialization_started"] += 1
        wait_us = (time.monotonic() - record.arrived) * 1e6
        self.runtime.counts["subagent_materialization_wait_us"] += wait_us
        if record.root_entry:
            self.runtime.counts["subagent_materialization_root_started"] += 1
            self.runtime.counts["subagent_materialization_root_wait_us"] += wait_us

    def completed(self, completed_ids):
        for request_id in completed_ids:
            self.pending.pop(request_id, None)
        if completed_ids:
            self.capacity_changed()

    def requeued(self, requests):
        for req in requests:
            record = self.pending.get(req.rid)
            if record is not None:
                record.key = self.admission.latest[record.key.invocation_id]
                record.request = req
        self.capacity_changed()

    def capacity_changed(self):
        self.next_check = 0.
        self.next_census = 0.
        for record in self.pending.values():
            record.retry_after = 0.

    def forget(self, invocation):
        self.pending = OrderedDict(
            (rid, record) for rid, record in self.pending.items()
            if record.key.invocation_id != invocation
        )
        self.progress_path[:] = [owner for owner in self.progress_path if owner != invocation]
        self.capacity_changed()

    def workflow_ended(self, workflow):
        self.started_workflows.discard(workflow)
        self.materialized = {
            identity for identity in self.materialized if identity[0] != workflow
        }

    def close(self):
        self._restore_native_eviction()
        self.pending.clear()
        self.materialized.clear()
        self.started_workflows.clear()
        self.progress_path.clear()
        self._native_order = ()
        self._running_batch = None
        self._adder = None

    def status(self):
        return {
            "policy": (
                "useful_kv_first_root_and_child_materialization_v2" if self.include_root_entries
                else "useful_kv_first_child_materialization_v1"
            ),
            "pending_initial_requests": len(self.pending),
            "held_request_ids": [
                rid for rid, record in self.pending.items() if not record.started and not record.permitted
            ],
            **({
                "native_host_pressure_aware_write_back": self._native_device_eviction is not None,
                "native_host_eviction_prefix_trimming": self._native_host_eviction is not None,
                "decode_growth_clip_tokens": self.decode_growth_clip_tokens,
                "held_root_request_ids": [
                    rid for rid, record in self.pending.items()
                    if record.root_entry and not record.started and not record.permitted
                ],
            } if self.include_root_entries else {}),
            "interval_ms": self.interval_s * 1000., "max_census_nodes": self.max_nodes,
            "census_fallback_interval_ms": self.admission.control_interval_s * 1000.,
            "max_candidates": self.max_candidates, "last_decision": self.last_decision,
            "logical_grants_and_physical_allocation_remain_native": True,
            "task_and_workflow_arrival_unchanged": True,
        }
