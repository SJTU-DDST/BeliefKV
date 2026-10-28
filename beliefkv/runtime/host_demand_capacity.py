"""Keep optional Host backups subordinate to real offload demand."""

from collections import defaultdict
from itertools import islice
import time

from beliefkv.runtime.idle_host_mirror_recycle import IdleHostMirrorRecycle


class HostDemandCapacity:
    max_candidates = 64

    def __init__(self, runtime, reserve_bytes):
        self.runtime = runtime
        self.reserve_bytes = reserve_bytes
        self.demand_bytes = 0
        self.demand_expires_ms = 0.

    def reserve(self):
        demand = self.demand_bytes if time.monotonic() * 1000. < self.demand_expires_ms else 0
        return max(self.reserve_bytes, demand)

    def optional_batch(self, maximum_bytes):
        """Cap a speculative batch without reclaiming anyone's checkpoint."""
        cache = self.runtime._native_cache
        pool = getattr(getattr(cache, "cache_controller", None), "mem_pool_host", None)
        unit, page = getattr(pool, "size_per_token", None), self.runtime._native_page_size
        if type(unit) is not int or unit <= 0 or type(page) is not int or page <= 0:
            return 0
        available = max(0, pool.available_size() * unit - self.reserve())
        return min(maximum_bytes, available) // (unit * page) * (unit * page)

    def ensure(self, required_tokens, *, protected=()):
        """Reclaim only Device-pinned mirrors for an already justified offload."""
        runtime, cache = self.runtime, self.runtime._native_cache
        controller = getattr(cache, "cache_controller", None)
        tree = getattr(cache, "tree_core", None)
        pool = getattr(controller, "mem_pool_host", None)
        if (
            runtime.physical_disabled or getattr(cache, "tree_components", ()) != (0,)
            or not getattr(cache, "is_write_back", False)
            or getattr(cache, "host_memory_mode", None) != "cache"
            or getattr(cache, "buffer_pipeline", None) is not None
            or pool is None or tree is None
            or type(required_tokens) is not int or required_tokens <= 0
        ):
            return False
        unit = pool.size_per_token
        self.demand_bytes = required_tokens * unit
        self.demand_expires_ms = time.monotonic() * 1000. + 1000.
        before = pool.available_size()
        if before >= required_tokens:
            return True
        runtime.counts["host_demand_capacity_checks"] += 1
        protected = set(protected) | {
            lease.node_id for lease in runtime._prefetch_service_leases.values()
        }
        duplicates = getattr(tree, "full_host_duplicates", {})
        selected, total = [], 0
        for node in tuple(islice(duplicates.values(), self.max_candidates)):
            runtime.counts["host_demand_mirror_checks"] += 1
            try:
                if (
                    node.id not in protected
                    and IdleHostMirrorRecycle._live_mirror(cache, node, node.creation_time)
                ):
                    selected.append((node, node.creation_time))
                    total += len(node.component_data[0].host_value)
                    if total >= required_tokens - before:
                        break
            except (AttributeError, IndexError, KeyError, RuntimeError, TypeError, ValueError):
                continue
        if total < required_tokens - before:
            runtime.counts["host_demand_insufficient_safe_mirrors"] += 1
            return False
        if any(
            not IdleHostMirrorRecycle._live_mirror(cache, node, created)
            for node, created in selected
        ):
            return False
        tracker, device_frees, host_frees = defaultdict(int), defaultdict(list), defaultdict(list)
        released = []
        for node, created in selected:
            device = node.component_data[0].value
            size = len(node.component_data[0].host_value)
            tree._release_full_host_duplicate(node, tracker, device_frees, host_frees)
            if device_frees or node.component_data[0].value is not device:
                raise RuntimeError("Host demand reclaim changed Device ownership")
            tree._update_duplicate_tracking(node)
            cache._remember_beliefkv_unbacked_full(node)
            released.append((node.id, created, size))
        cache._free_values(device_frees, host_frees)
        if cache.on_hicache_host_eviction is not None:
            try:
                cache.on_hicache_host_eviction(0, tracker)
            except Exception:
                pass
        after = pool.available_size()
        runtime.counts["host_demand_reclaimed_tokens"] += tracker[0]
        runtime.counts["host_demand_reclaimed_bytes"] += tracker[0] * unit
        if runtime._opportunity_writer is not None:
            runtime._opportunity_writer.record({
                "event": "host_demand_mirrors_reclaimed", "ts_ms": time.time() * 1000.,
                "required_host_tokens": required_tokens,
                "host_free_before": before, "host_free_after": after,
                "released_host_nodes": released, "released_host_tokens": tracker[0],
                "released_device_tokens": 0,
                "evidence": "actual_offload_demand;complete_device_pinned_mirrors;native_dma_guards",
            })
        return after >= required_tokens
