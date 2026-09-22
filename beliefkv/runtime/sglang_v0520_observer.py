"""Bounded, read-only capacity census for the v0.5.20 FULL+MAMBA stack.

These are addressable ceilings, NOT simultaneously usable capacity or an
authorization to issue physical commands. Reads are scalar field reads only:
the unified allocator's availability methods memoize state, and reclaim and
tree queries are outside this observer's contract. Call at a scheduler safe
point for a coherent snapshot; concurrent pool updates are not synchronized.
"""

from __future__ import annotations

from dataclasses import dataclass


_UNSUPPORTED = (
    "device_available",
    "host_available",
    "device_occupancy",
    "host_occupancy",
    "evictable_or_protected",
    "per_component_device_bytes",
    "physical_commands",
)
_CAPACITY_FIELDS = (
    "device_full_tokens",
    "device_mamba_slots",
    "shared_device_bytes",
    "host_full_tokens",
    "host_mamba_slots",
)


@dataclass(frozen=True)
class UnifiedCapacityObservation:
    observable: bool
    physical_actions_supported: bool = False
    device_full_tokens: int | None = None
    device_mamba_slots: int | None = None
    shared_device_bytes: int | None = None
    host_full_tokens: int | None = None
    host_mamba_slots: int | None = None
    missing_metrics: tuple[str, ...] = _CAPACITY_FIELDS
    unsupported_metrics: tuple[str, ...] = _UNSUPPORTED
    reason: str | None = None


def _positive(value: object, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"invalid {label}")
    return value


def _named(value: object, name: str) -> object:
    if type(value).__name__ != name:
        raise ValueError(f"expected {name}")
    return value


def _device_ceiling(pool: object, buffer: object, name: str) -> int:
    _named(pool, "MultiEndedAllocator")
    if pool.unified_buffer is not buffer or pool.sub_pool_name != name:
        raise ValueError(f"unrecognized {name} device pool")
    max_slots = _positive(pool.max_slots, f"{name} max_slots")
    entry_bytes = _positive(pool.entry_bytes, f"{name} entry_bytes")
    if max_slots != buffer.total_bytes // entry_bytes:
        raise ValueError(f"inconsistent {name} byte ceiling")
    physical_page = _positive(pool.pool_page_size, f"{name} pool_page_size")
    logical_page = _positive(pool.page_size, f"{name} page_size")
    min_slot = _positive(pool.min_slot_index, f"{name} min_slot_index")
    if logical_page % physical_page or pool.num_pages != max_slots // physical_page:
        raise ValueError(f"inconsistent {name} page geometry")
    min_page = (min_slot + physical_page - 1) // physical_page
    if pool.min_page_index != min_page or min_page >= pool.num_pages:
        raise ValueError(f"inconsistent {name} reserved floor")
    return (pool.num_pages - min_page) * logical_page


def observe_unified_full_mamba(cache: object) -> UnifiedCapacityObservation:
    """Inspect only the vendored UnifiedRadixCache/UnifiedMamba pair.

    A missing HiCache attachment, extra component, inconsistent geometry, or
    unknown layout yields no partial capacity data. Host numbers are logical
    tokens/slots (FULL is widened by host DCP); device ceilings share bytes.
    """
    try:
        _named(cache, "UnifiedRadixCache")
        if cache.disable is not False:
            raise ValueError("cache disabled or disable state unknown")
        components = cache.tree_components
        if (
            type(components) is not tuple
            or len(components) != 2
            or [(ct.name, ct.value) for ct in components]
            != [("FULL", 0), ("MAMBA", 2)]
        ):
            raise ValueError("expected exactly FULL+MAMBA components")

        allocator = _named(
            cache.token_to_kv_pool_allocator,
            "UnifiedMambaTokenToKVPoolAllocator",
        )
        request_pool = _named(cache.req_to_token_pool, "UnifiedHybridReqToTokenPool")
        slot_allocator = _named(
            request_pool.mamba_allocator, "UnifiedMambaSlotAllocator"
        )
        buffer = _named(allocator.unified_buffer, "UnifiedKVPool")
        total_bytes = _positive(buffer.total_bytes, "shared device bytes")
        specs = buffer._specs_by_name
        if (
            type(specs) is not dict
            or len(specs) != 2
            or "full" not in specs
            or "mamba" not in specs
        ):
            raise ValueError("expected exactly FULL+MAMBA buffer specs")
        full = allocator.full_attn_allocator
        mamba = allocator.mamba_allocator
        if (
            slot_allocator._multi_ended_allocator is not mamba
            or request_pool._unified_buffer is not buffer
            or allocator._kvcache.full_kv_pool is not full._kvcache
            or request_pool.mamba_pool is not mamba._kvcache
        ):
            raise ValueError("device pools are not the bound FULL+MAMBA pair")
        full_tokens = _device_ceiling(full, buffer, "full")
        mamba_slots = _device_ceiling(mamba, buffer, "mamba")
        if (
            mamba.pool_page_size != 1
            or mamba.page_size != 1
            or slot_allocator._max_size != mamba.max_slots - 1
        ):
            raise ValueError("unknown MAMBA slot geometry")

        group = _named(cache.host_pool_group, "HostPoolGroup")
        if (
            cache.host_memory_mode != "cache"
            or cache.is_swa_enabled is not False
            or cache.is_mamba_enabled is not True
            or cache.cache_controller is None
            or cache.cache_controller.mem_pool_host is not group
            or type(group.entry_map) is not dict
            or len(group.entry_map) != 2
            or "kv" not in group.entry_map
            or "mamba" not in group.entry_map
        ):
            raise ValueError("missing or unknown HiCache host pools")
        kv_entry, mamba_entry = group.entry_map["kv"], group.entry_map["mamba"]
        if (
            kv_entry.device_pool is not allocator._kvcache.full_kv_pool
            or mamba_entry.device_pool is not request_pool.mamba_pool
            or kv_entry.is_primary_index_anchor is not True
            or mamba_entry.is_primary_index_anchor is not False
            or group.anchor_entry is not kv_entry
        ):
            raise ValueError("host entries are not bound to the device pools")
        host_full = _named(kv_entry.host_pool, "MHATokenToKVPoolHost")
        host_mamba = _named(mamba_entry.host_pool, "MambaPoolHost")
        if (
            host_full.device_pool is not kv_entry.device_pool
            or host_mamba.device_pool is not mamba_entry.device_pool
            or group.size != host_full.size
            or host_full.page_size <= 0
            or host_full.size % host_full.page_size
            or host_full.dcp_size != full.page_size // full.pool_page_size
            or host_mamba.page_size != 1
        ):
            raise ValueError("inconsistent host pool geometry")
        full_host_tokens = (
            _positive(host_full.size, "host FULL rows")
            * _positive(host_full.dcp_size, "host FULL DCP")
        )
        mamba_host_slots = _positive(host_mamba.size, "host MAMBA slots")
        return UnifiedCapacityObservation(
            observable=True,
            device_full_tokens=full_tokens,
            device_mamba_slots=mamba_slots,
            shared_device_bytes=total_bytes,
            host_full_tokens=full_host_tokens,
            host_mamba_slots=mamba_host_slots,
            missing_metrics=(),
        )
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        return UnifiedCapacityObservation(observable=False, reason=str(exc))
