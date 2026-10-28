"""Make Host backup room using redundant copies of Device-pinned FULL KV."""

from collections import defaultdict
from itertools import islice
import time

from beliefkv.runtime.sglang_v0520_observer import observe_static_full_mamba_headroom


class IdleHostMirrorRecycle:
    interval_ms = 1000.
    max_candidates = 32

    def __init__(self, runtime):
        self.runtime = runtime
        self.next_ms = 0.

    @staticmethod
    def _live_cold(cache, node, created):
        full = node.component_data[0]
        return (
            cache.tree_core.node_by_id(node.id) is node
            and node.creation_time == created
            and full.value is not None and full.host_value is None
            and cache._beliefkv_causal_full_donor(node, require_host_copy=False)
        )

    @staticmethod
    def _live_mirror(cache, node, created):
        full = node.component_data[0]
        return (
            cache.tree_core.node_by_id(node.id) is node
            and node.creation_time == created
            and full.lock_ref > 0
            and full.value is not None and full.host_value is not None
            and len(full.value) == len(full.host_value) > 0
            and node.id not in cache.ongoing_write_through
            and cache.tree_core._can_reclaim_full_host_duplicate(node)
        )

    def prepare(self):
        """Return an issued batch, or None to keep ordinary background behavior."""
        runtime, now = self.runtime, time.monotonic() * 1000.
        if now < self.next_ms or runtime.physical_disabled:
            return None
        cache = runtime._native_cache
        dirty = getattr(cache, "_beliefkv_unbacked_full", None)
        tree = getattr(cache, "tree_core", None)
        duplicates = getattr(tree, "full_host_duplicates", None)
        controller = getattr(cache, "cache_controller", None)
        if (
            getattr(cache, "tree_components", ()) != (0,)
            or not getattr(cache, "is_write_back", False)
            or getattr(cache, "host_memory_mode", None) != "cache"
            or getattr(cache, "buffer_pipeline", None) is not None
            or not dirty or not callable(getattr(dirty, "move_to_end", None))
            or type(duplicates) is not dict or not duplicates or controller is None
            or not all(callable(getattr(cache, method, None)) for method in (
                "_beliefkv_causal_full_donor", "_prepare_beliefkv_cold_full_nodes",
                "_free_values", "_remember_beliefkv_unbacked_full",
            ))
            or not all(callable(getattr(tree, method, None)) for method in (
                "_can_reclaim_full_host_duplicate", "_release_full_host_duplicate",
                "_update_duplicate_tracking",
            ))
            or any(getattr(controller, name) for name in (
                "write_queue", "load_queue", "ack_write_queue", "ack_load_queue",
            ))
        ):
            return None
        pool, page = controller.mem_pool_host, tree.page_size
        unit, free = pool.size_per_token, pool.available_size()
        original = getattr(type(cache), "prepare_beliefkv_idle_full", None)
        batch_bytes = getattr(original, "__globals__", {}).get(
            "BELIEFKV_IDLE_FULL_PREPARE_MAX_BYTES",
        )
        if (
            type(unit) is not int or unit <= 0 or type(free) is not int or free < 0
            or type(page) is not int or page <= 0
            or type(batch_bytes) is not int or batch_bytes <= 0
        ):
            return None
        reserved = max(page, batch_bytes // unit // page * page)
        if free >= reserved + page:
            return None
        headroom = observe_static_full_mamba_headroom(cache)
        if not headroom.observable or not runtime._prepare_pressure(
            runtime._capacity_waiting_queue, runtime._native_running_batch, headroom,
        )[0]:
            return None
        self.next_ms = now + self.interval_ms
        runtime.counts["idle_host_mirror_recycle_checks"] += 1
        target = None
        for node_id, node in tuple(islice(dirty.items(), self.max_candidates)):
            runtime.counts["idle_host_mirror_recycle_cold_checks"] += 1
            # Empty-Host rounds otherwise return before rotating the dirty index.
            dirty.move_to_end(node_id)
            try:
                if self._live_cold(cache, node, node.creation_time):
                    target = node, node.creation_time
                    break
            except (AttributeError, IndexError, KeyError, RuntimeError, TypeError, ValueError):
                continue
        if target is None:
            runtime.counts["idle_host_mirror_recycle_no_cold_target"] += 1
            return None
        target_node, target_created = target
        copy_tokens = min(
            reserved, len(target_node.component_data[0].value) // page * page,
        )
        required = reserved + copy_tokens - free
        if copy_tokens <= 0 or required <= 0:
            return None
        protected = {
            lease.node_id for lease in runtime._prefetch_service_leases.values()
        }
        candidates = []
        for node in tuple(islice(duplicates.values(), self.max_candidates)):
            runtime.counts["idle_host_mirror_recycle_mirror_checks"] += 1
            try:
                if (
                    node.id not in protected and node is not target_node
                    and self._live_mirror(cache, node, node.creation_time)
                ):
                    candidates.append((
                        len(node.component_data[0].host_value),
                        node.id, node.creation_time, node,
                    ))
            except (AttributeError, IndexError, KeyError, RuntimeError, TypeError, ValueError):
                continue
        fitting = [item for item in candidates if item[0] >= required]
        if fitting:
            chosen = [min(fitting, key=lambda item: (item[0], item[1]))]
        else:
            chosen, total = [], 0
            for item in sorted(candidates, key=lambda item: (-item[0], item[1])):
                chosen.append(item)
                total += item[0]
                if total >= required:
                    break
            if total < required:
                runtime.counts["idle_host_mirror_recycle_insufficient_mirrors"] += 1
                return None
        if (
            runtime.physical_disabled
            or not self._live_cold(cache, target_node, target_created)
            or any(not self._live_mirror(cache, item[3], item[2]) for item in chosen)
        ):
            runtime.counts["idle_host_mirror_recycle_revalidation_failed"] += 1
            return None
        tracker, device_frees, host_frees = defaultdict(int), defaultdict(list), defaultdict(list)
        released = []
        for size, node_id, created, node in chosen:
            device = node.component_data[0].value
            tree._release_full_host_duplicate(node, tracker, device_frees, host_frees)
            if device_frees or node.component_data[0].value is not device:
                raise RuntimeError("Host mirror recycling changed Device ownership")
            tree._update_duplicate_tracking(node)
            cache._remember_beliefkv_unbacked_full(node)
            released.append((node_id, created, size))
        cache._free_values(device_frees, host_frees)
        observer = cache.on_hicache_host_eviction
        if observer is not None:
            try:
                observer(next(iter(tracker)), tracker)
            except Exception:
                pass
        actual = tracker.get(0, 0)
        runtime.counts["idle_host_mirror_recycle_host_nodes"] += len(released)
        runtime.counts["idle_host_mirror_recycle_host_tokens"] += actual
        runtime.counts["idle_host_mirror_recycle_host_bytes"] += actual * unit
        after = pool.available_size()
        if runtime._opportunity_writer is not None:
            runtime._opportunity_writer.record({
                "event": "idle_host_mirror_recycled",
                "ts_ms": time.time() * 1000.,
                "cold_node_id": target_node.id, "cold_creation_time": target_created,
                "planned_backup_tokens": copy_tokens, "host_reserved_tokens": reserved,
                "host_free_before": free, "host_free_after": after,
                "released_host_nodes": released, "released_host_tokens": actual,
                "released_host_bytes": actual * unit, "released_device_tokens": 0,
                "evidence": "current_cold_owner;device_pinned_mirror;native_host_dma_guards",
            })
        if after < reserved + copy_tokens:
            runtime.counts["idle_host_mirror_recycle_unmet_budget"] += 1
            return ()
        issued = cache._prepare_beliefkv_cold_full_nodes(
            ((target_node.id, target_created, len(target_node.component_data[0].value)),),
            idle=True, read_only_backup=True, require_cold_owners=True,
        )
        runtime.counts["idle_host_mirror_recycle_without_backup"] += not issued
        return issued
