"""Bounded, read-only capacity census for the v0.5.20 FULL+MAMBA stack.

These are addressable ceilings, NOT simultaneously usable capacity or an
authorization to issue physical commands. Capacity reads are scalar; usage
reads allocation counters, and node closure reads one tree ancestry without
materializing transfer indices. The allocator's availability methods memoize
state and are outside this observer's contract. Call at a scheduler safe
point for a coherent snapshot; concurrent pool updates are not synchronized.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
import operator
import time

from numpy import float64


def normalize_native_creation_time(value: object) -> int | float:
    """Preserve native node identity in a form accepted by the H2D primitive."""
    value_type = type(value)
    if value_type is float64:
        value = float(value)
        value_type = float
    if (
        value_type not in (int, float)
        or value < 0
        or (value_type is float and not isfinite(value))
    ):
        raise ValueError("invalid native node creation time")
    return value


_UNSUPPORTED = (
    "device_available",
    "host_available",
    "device_occupancy",
    "host_occupancy",
    "evictable_or_protected",
    "per_component_device_occupancy_bytes",
    "physical_commands",
)
_CAPACITY_FIELDS = (
    "device_full_tokens",
    "device_mamba_slots",
    "shared_device_bytes",
    "device_full_ceiling_bytes",
    "device_mamba_ceiling_bytes",
    "host_full_tokens",
    "host_mamba_slots",
    "host_full_bytes",
    "host_mamba_bytes",
    "host_total_bytes",
)


@dataclass(frozen=True)
class UnifiedCapacityObservation:
    observable: bool
    physical_actions_supported: bool = False
    device_full_tokens: int | None = None
    device_mamba_slots: int | None = None
    shared_device_bytes: int | None = None
    device_full_ceiling_bytes: int | None = None
    device_mamba_ceiling_bytes: int | None = None
    host_full_tokens: int | None = None
    host_mamba_slots: int | None = None
    host_full_bytes: int | None = None
    host_mamba_bytes: int | None = None
    host_total_bytes: int | None = None
    missing_metrics: tuple[str, ...] = _CAPACITY_FIELDS
    unsupported_metrics: tuple[str, ...] = _UNSUPPORTED
    reason: str | None = None


@dataclass(frozen=True)
class UnifiedUsageObservation:
    observable: bool
    device_full_live_tokens: int | None = None
    device_mamba_live_slots: int | None = None
    host_full_used_tokens: int | None = None
    host_mamba_used_slots: int | None = None
    physical_actions_supported: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class StaticHostUsageObservation:
    observable: bool
    host_full_used_tokens: int | None = None
    host_full_used_bytes: int | None = None
    host_mamba_used_slots: int | None = None
    host_mamba_used_bytes: int | None = None
    reason: str | None = None


@dataclass(frozen=True)
class StaticPoolHeadroomObservation:
    """Unreserved free-list counts; not an H2D/D2H capacity reservation."""

    observable: bool
    device_full_free_tokens: int | None = None
    device_mamba_free_slots: int | None = None
    host_full_free_tokens: int | None = None
    host_mamba_free_slots: int | None = None
    physical_actions_supported: bool = False
    reason: str | None = None
    full_frees_deferred: bool = False
    mamba_batch_reserved: bool = False


@dataclass(frozen=True, slots=True)
class UnifiedNodeSummary:
    node_id: int
    parent_id: int | None
    creation_time: int | float
    full_device_tokens: int
    full_host_tokens: int
    mamba_device_present: bool
    mamba_host_present: bool
    full_device_locks: int
    full_host_locks: int
    mamba_device_locks: int
    mamba_host_locks: int
    full_session_refs: int
    mamba_session_refs: int
    full_session_leaf_count: int
    mamba_session_leaf_count: int
    pending_write_id: int | None
    pending_load_id: int | None
    key_tokens: int | None = None

    def to_record(self) -> dict[str, int | float | bool | None]:
        """Serialize the scalar observation without recursive dataclass copies."""
        return {
            "node_id": self.node_id,
            "parent_id": self.parent_id,
            "creation_time": self.creation_time,
            "full_device_tokens": self.full_device_tokens,
            "full_host_tokens": self.full_host_tokens,
            "mamba_device_present": self.mamba_device_present,
            "mamba_host_present": self.mamba_host_present,
            "full_device_locks": self.full_device_locks,
            "full_host_locks": self.full_host_locks,
            "mamba_device_locks": self.mamba_device_locks,
            "mamba_host_locks": self.mamba_host_locks,
            "full_session_refs": self.full_session_refs,
            "mamba_session_refs": self.mamba_session_refs,
            "full_session_leaf_count": self.full_session_leaf_count,
            "mamba_session_leaf_count": self.mamba_session_leaf_count,
            "pending_write_id": self.pending_write_id,
            "pending_load_id": self.pending_load_id,
            "key_tokens": self.key_tokens,
        }


@dataclass(frozen=True)
class UnifiedNodeClosureObservation:
    observable: bool
    nodes: tuple[UnifiedNodeSummary, ...] = ()
    captured_monotonic_s: float | None = None
    physical_actions_supported: bool = False
    reason: str | None = None


def _positive(value: object, label: str) -> int:
    try:
        integer = operator.index(value) if type(value) is not bool else 0
    except TypeError as exc:
        raise ValueError(f"invalid {label}") from exc
    if integer <= 0:
        raise ValueError(f"invalid {label}")
    return integer


def _bounded(value: object, ceiling: int, label: str) -> int:
    if type(value) is not int or not 0 <= value <= ceiling:
        raise ValueError(f"invalid {label}")
    return value


def _bounded_index_length(indices: object, ceiling: int, label: str) -> int:
    shape = getattr(indices, "shape", None)
    length = shape[0] if shape else len(indices)
    if type(length) is not int or not 0 <= length <= ceiling:
        raise ValueError(f"invalid {label}")
    return length


def _named(value: object, name: str) -> object:
    if type(value).__name__ != name:
        raise ValueError(
            f"expected {name}, got {type(value).__module__}.{type(value).__name__}"
        )
    return value


def _device_ceiling(pool: object, buffer: object, name: str) -> tuple[int, int]:
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
    allocatable_pages = pool.num_pages - min_page
    return (
        allocatable_pages * logical_page,
        allocatable_pages * physical_page * entry_bytes,
    )


def _static_full_mamba_pool_geometry(
    cache: object, *, allow_without_host: bool = False,
) -> dict[str, int | str]:
    """Validate pool ownership and ceilings without traversing tensor storage."""
    _named(cache, "UnifiedRadixCache")
    if tuple((component.name, component.value) for component in cache.tree_components) == (
        ("FULL", 0),
    ):
        return _static_full_only_pool_geometry(cache, allow_without_host=allow_without_host)
    if cache.disable is not False or tuple(
        (component.name, component.value) for component in cache.tree_components
    ) != (("FULL", 0), ("MAMBA", 2)):
        raise ValueError("expected active FULL+MAMBA radix cache")
    allocator = cache.token_to_kv_pool_allocator
    if type(allocator).__name__ not in (
        "TokenToKVPoolAllocator", "PagedTokenToKVPoolAllocator"
    ):
        raise ValueError(f"unexpected static allocator: {type(allocator).__name__}")
    req = _named(cache.req_to_token_pool, "HybridReqToTokenPool")
    hybrid = _named(allocator._kvcache, "HybridLinearKVPool")
    full = _named(hybrid.full_kv_pool, "MHATokenToKVPool")
    mamba = _named(req.mamba_pool, "MambaPool")
    if hybrid.mamba_pool is not mamba or full.device != mamba.device:
        raise ValueError("static FULL/MAMBA pools are not the linked pair")
    full_tokens = _positive(allocator.size, "static FULL tokens")
    mamba_slots = _positive(mamba.size, "static MAMBA slots")
    if full.size != full_tokens:
        raise ValueError("static FULL allocator and physical pool disagree")
    if type(mamba.mamba_cache).__name__ != "State":
        raise ValueError("unknown static MAMBA state layout")
    if allow_without_host and cache.cache_controller is None:
        if cache.host_pool_group is not None:
            raise ValueError("Host pools exist without a cache controller")
        return {
            "pool_layout": "static_separate_full_mamba",
            "device_full_tokens": full_tokens,
            "device_mamba_slots": mamba_slots,
            "host_full_tokens": 0,
            "host_mamba_slots": 0,
            "host_full_bytes": 0,
            "host_mamba_bytes": 0,
            "host_total_bytes": 0,
        }
    group = _named(cache.host_pool_group, "HostPoolGroup")
    if cache.cache_controller.mem_pool_host is not group or set(group.entry_map) != {
        "kv", "mamba",
    }:
        raise ValueError("missing static FULL/MAMBA Host pools")
    full_entry, mamba_entry = group.entry_map["kv"], group.entry_map["mamba"]
    host_full = _named(full_entry.host_pool, "MHATokenToKVPoolHost")
    host_mamba = _named(mamba_entry.host_pool, "MambaPoolHost")
    if (
        full_entry.device_pool is not full
        or mamba_entry.device_pool is not mamba
        or host_full.device_pool is not full
        or host_mamba.device_pool is not mamba
        or group.anchor_entry is not full_entry
    ):
        raise ValueError("static Host/Device pool ownership differs")
    host_full_tokens = _positive(host_full.size, "Host FULL tokens")
    host_mamba_slots = _positive(host_mamba.size, "Host MAMBA slots")
    host_full_bytes = _positive(
        host_full_tokens * host_full.size_per_token, "Host FULL bytes"
    )
    host_mamba_bytes = _positive(
        host_mamba_slots * host_mamba.size_per_token, "Host MAMBA bytes"
    )
    return {
        "pool_layout": "static_separate_full_mamba",
        "device_full_tokens": full_tokens,
        "device_mamba_slots": mamba_slots,
        "host_full_tokens": host_full_tokens,
        "host_mamba_slots": host_mamba_slots,
        "host_full_bytes": host_full_bytes,
        "host_mamba_bytes": host_mamba_bytes,
        "host_total_bytes": host_full_bytes + host_mamba_bytes,
    }


def _static_full_only_pool_geometry(
    cache: object, *, allow_without_host: bool = False,
) -> dict[str, int | str]:
    if cache.disable is not False:
        raise ValueError("expected active FULL radix cache")
    allocator = cache.token_to_kv_pool_allocator
    if type(allocator).__name__ not in (
        "TokenToKVPoolAllocator", "PagedTokenToKVPoolAllocator",
    ):
        raise ValueError("unexpected static FULL allocator")
    full = _named(allocator._kvcache, "MHATokenToKVPool")
    tokens = _positive(allocator.size, "static FULL tokens")
    if full.size != tokens:
        raise ValueError("static FULL allocator and physical pool disagree")
    result = {
        "pool_layout": "static_full_only",
        "device_full_tokens": tokens, "device_mamba_slots": 0,
        "host_full_tokens": 0, "host_mamba_slots": 0,
        "host_full_bytes": 0, "host_mamba_bytes": 0, "host_total_bytes": 0,
    }
    if allow_without_host and cache.cache_controller is None:
        if cache.host_pool_group is not None:
            raise ValueError("Host pools exist without a cache controller")
        return result
    group = _named(cache.host_pool_group, "HostPoolGroup")
    if cache.cache_controller.mem_pool_host is not group or set(group.entry_map) != {"kv"}:
        raise ValueError("missing static FULL-only Host pool")
    entry = group.entry_map["kv"]
    host = _named(entry.host_pool, "MHATokenToKVPoolHost")
    if entry.device_pool is not full or host.device_pool is not full or group.anchor_entry is not entry:
        raise ValueError("static FULL Host/Device pool ownership differs")
    host_tokens = _positive(host.size, "Host FULL tokens")
    host_bytes = _positive(host_tokens * host.size_per_token, "Host FULL bytes")
    result.update(
        host_full_tokens=host_tokens, host_full_bytes=host_bytes, host_total_bytes=host_bytes,
    )
    return result


def observe_static_full_mamba(
    cache: object, *, allow_without_host: bool = False,
) -> dict[str, int | str]:
    """Read persistent tensor bytes for the separately allocated device pools.

    These allocations cannot lend bytes to each other. CUDA scratch and graph
    buffers consume additional GPU memory outside these pools.
    """
    capacity = _static_full_mamba_pool_geometry(
        cache, allow_without_host=allow_without_host,
    )
    pool = cache.token_to_kv_pool_allocator._kvcache
    full_only = capacity["pool_layout"] == "static_full_only"
    full = pool if full_only else pool.full_kv_pool
    full_k_bytes, full_v_bytes = full.get_kv_size_bytes()
    device_full_bytes = (
        _positive(full_k_bytes, "device FULL K bytes")
        + _positive(full_v_bytes, "device FULL V bytes")
    )
    def tensor_bytes(tensor: object) -> int:
        return int(tensor.numel()) * int(tensor.element_size())

    device_mamba_bytes = 0
    if not full_only:
        state = cache.req_to_token_pool.mamba_pool.mamba_cache
        device_mamba_bytes = _positive(
            sum(tensor_bytes(item) for item in state.conv)
            + tensor_bytes(state.temporal),
            "device MAMBA bytes",
        )
    capacity["device_full_bytes"] = device_full_bytes
    capacity["device_mamba_bytes"] = device_mamba_bytes
    capacity["device_total_bytes"] = device_full_bytes + device_mamba_bytes
    return capacity


def observe_static_full_mamba_host_usage(
    cache: object,
) -> StaticHostUsageObservation:
    """Read current allocated slots from the separate FULL and MAMBA host pools."""
    try:
        capacity = _static_full_mamba_pool_geometry(cache)
        group = cache.host_pool_group
        host_full = group.entry_map["kv"].host_pool
        mamba_entry = group.entry_map.get("mamba")
        host_mamba = mamba_entry.host_pool if mamba_entry is not None else None
        full_used = _bounded(
            capacity["host_full_tokens"] - host_full.available_size(),
            capacity["host_full_tokens"],
            "static Host FULL used tokens",
        )
        mamba_used = _bounded(
            capacity["host_mamba_slots"] - host_mamba.available_size() if host_mamba is not None else 0,
            capacity["host_mamba_slots"],
            "static Host MAMBA used slots",
        )
        return StaticHostUsageObservation(
            observable=True,
            host_full_used_tokens=full_used,
            host_full_used_bytes=full_used * host_full.size_per_token,
            host_mamba_used_slots=mamba_used,
            host_mamba_used_bytes=mamba_used * host_mamba.size_per_token if host_mamba is not None else 0,
        )
    except (AttributeError, KeyError, TypeError, ValueError, RuntimeError) as exc:
        return StaticHostUsageObservation(observable=False, reason=str(exc))


def observe_static_full_mamba_headroom(
    cache: object,
) -> StaticPoolHeadroomObservation:
    """Sample validated static free lists without a tensor census or LRU access.

    Static FULL availability excludes pending free_group tensors. Static Mamba
    preallocation removes slots from free_slots before opening _alloc_iter.
    Read the remaining free lists without releasing or counting either group.
    Only native can recheck ownership and allocate the target slots.
    """
    try:
        capacity = _static_full_mamba_pool_geometry(cache)
        full = cache.token_to_kv_pool_allocator
        entries = cache.host_pool_group.entry_map
        full_host = _named(entries["kv"].host_pool, "MHATokenToKVPoolHost")
        if capacity["pool_layout"] == "static_full_only":
            return StaticPoolHeadroomObservation(
                observable=True,
                device_full_free_tokens=_bounded(
                    full.available_size(), capacity["device_full_tokens"], "static FULL free tokens",
                ),
                device_mamba_free_slots=0,
                host_full_free_tokens=_bounded(
                    full_host.available_size(), capacity["host_full_tokens"], "static Host FULL free tokens",
                ),
                host_mamba_free_slots=0,
                full_frees_deferred=full.free_group is not None,
            )
        _named(full, "TokenToKVPoolAllocator")
        mamba = _named(cache.req_to_token_pool.mamba_allocator, "MambaSlotAllocator")
        if mamba.size != capacity["device_mamba_slots"]:
            raise ValueError("static MAMBA allocator and physical pool disagree")
        mamba_host = _named(entries["mamba"].host_pool, "MambaPoolHost")
        return StaticPoolHeadroomObservation(
            observable=True,
            device_full_free_tokens=_bounded(
                full.available_size(), capacity["device_full_tokens"],
                "static FULL free tokens",
            ),
            device_mamba_free_slots=_bounded(
                mamba.available_size(), capacity["device_mamba_slots"],
                "static MAMBA free slots",
            ),
            host_full_free_tokens=_bounded(
                full_host.available_size(), capacity["host_full_tokens"],
                "static Host FULL free tokens",
            ),
            host_mamba_free_slots=_bounded(
                mamba_host.available_size(), capacity["host_mamba_slots"],
                "static Host MAMBA free slots",
            ),
            full_frees_deferred=full.free_group is not None,
            mamba_batch_reserved=mamba._alloc_iter is not None,
        )
    except (AttributeError, KeyError, TypeError, ValueError, RuntimeError) as exc:
        return StaticPoolHeadroomObservation(observable=False, reason=str(exc))


def observe_unified_full_mamba(cache: object) -> UnifiedCapacityObservation:
    """Inspect only the vendored UnifiedRadixCache/UnifiedMamba pair.

    A missing HiCache attachment, extra component, inconsistent geometry, or
    unknown layout yields no partial capacity data. Host numbers are logical
    tokens/slots (FULL is widened by host DCP); device ceilings share bytes.
    Byte ceilings are individually addressable, not additive on the device.
    Host bytes represent both distinct physical pool allocations, including
    page alignment, not the CLI size or DCP-widened logical token count.
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
        full_tokens, full_device_bytes = _device_ceiling(full, buffer, "full")
        mamba_slots, mamba_device_bytes = _device_ceiling(mamba, buffer, "mamba")
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
        full_host_bytes = (
            _positive(host_full.size_per_token, "host FULL row bytes") * host_full.size
        )
        mamba_host_bytes = (
            _positive(host_mamba.size_per_token, "host MAMBA slot bytes")
            * host_mamba.size
        )
        return UnifiedCapacityObservation(
            observable=True,
            device_full_tokens=full_tokens,
            device_mamba_slots=mamba_slots,
            shared_device_bytes=total_bytes,
            device_full_ceiling_bytes=full_device_bytes,
            device_mamba_ceiling_bytes=mamba_device_bytes,
            host_full_tokens=full_host_tokens,
            host_mamba_slots=mamba_host_slots,
            host_full_bytes=full_host_bytes,
            host_mamba_bytes=mamba_host_bytes,
            host_total_bytes=full_host_bytes + mamba_host_bytes,
            missing_metrics=(),
        )
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        return UnifiedCapacityObservation(observable=False, reason=str(exc))


def observe_unified_full_mamba_usage(cache: object) -> UnifiedUsageObservation:
    """Read CPU allocation counters at a safe point; never infer free shared bytes.

    Allocated FULL tokens and MAMBA slots use distinct virtual-id spaces.
    Host free-list lengths include pending releases; native eviction/lock
    eligibility is not represented by these counts.
    """
    capacity = observe_unified_full_mamba(cache)
    if not capacity.observable:
        return UnifiedUsageObservation(observable=False, reason=capacity.reason)
    try:
        allocator = cache.token_to_kv_pool_allocator
        full = allocator.full_attn_allocator
        mamba = allocator.mamba_allocator
        group = cache.host_pool_group
        host_full = group.entry_map["kv"].host_pool
        host_mamba = group.entry_map["mamba"].host_pool
        live_full = _bounded(
            full.allocated_count(), capacity.device_full_tokens, "device FULL live tokens"
        )
        live_mamba = _bounded(
            mamba.allocated_count(), capacity.device_mamba_slots, "device MAMBA live slots"
        )
        used_full = _bounded(
            capacity.host_full_tokens - host_full.available_size(),
            capacity.host_full_tokens,
            "host FULL used tokens",
        )
        used_mamba = _bounded(
            capacity.host_mamba_slots - host_mamba.available_size(),
            capacity.host_mamba_slots,
            "host MAMBA used slots",
        )
        return UnifiedUsageObservation(
            observable=True,
            device_full_live_tokens=live_full,
            device_mamba_live_slots=live_mamba,
            host_full_used_tokens=used_full,
            host_mamba_used_slots=used_mamba,
        )
    except (AttributeError, KeyError, TypeError, ValueError, RuntimeError) as exc:
        return UnifiedUsageObservation(observable=False, reason=str(exc))


def observe_unified_node_closure(
    cache: object, node_id: int, *, max_nodes: int | None = None,
    reject_pending: bool = False,
) -> UnifiedNodeClosureObservation:
    """Capture one FULL/MAMBA tree path for either validated device-pool layout.

    `creation_time` and pending/lock fields form a *local fingerprint*, not
    an atomic revision. Recheck the live tree before any physical command.
    Action capture may reject at the first pending transfer; diagnostic callers
    retain complete summaries by default.
    """
    # The common static layout has its own validator; it need not first fail
    # the shared-buffer census on every action-local ancestry observation.
    allocator_name = type(getattr(cache, "token_to_kv_pool_allocator", None)).__name__
    capacity = (
        None if allocator_name in ("TokenToKVPoolAllocator", "PagedTokenToKVPoolAllocator")
        else observe_unified_full_mamba(cache)
    )
    if capacity is None or not capacity.observable:
        try:
            static = _static_full_mamba_pool_geometry(cache)
        except (AttributeError, KeyError, NotImplementedError, RuntimeError, TypeError, ValueError) as exc:
            return UnifiedNodeClosureObservation(
                observable=False,
                reason=(
                    f"unrecognized FULL/MAMBA pool layout: {exc}" if capacity is None
                    else f"unrecognized FULL/MAMBA pool layout: {capacity.reason}; {exc}"
                ),
            )
        device_full_tokens = static["device_full_tokens"]
        host_full_tokens = static["host_full_tokens"]
    else:
        device_full_tokens = capacity.device_full_tokens
        host_full_tokens = capacity.host_full_tokens
    try:
        if type(node_id) is not int or node_id < 0:
            raise ValueError("invalid node ID")
        if max_nodes is not None and (type(max_nodes) is not int or max_nodes <= 0):
            raise ValueError("invalid closure bound")
        if type(reject_pending) is not bool:
            raise ValueError("invalid pending-transfer capture mode")
        tree = _named(cache.tree_core, "UnifiedTreeCore")
        node = tree.node_by_id(node_id)
        visited: set[int] = set()
        summaries: list[UnifiedNodeSummary] = []
        node_type = component_type = None
        while node is not None:
            if type(node) is not node_type:
                _named(node, "UnifiedTreeNode")
                node_type = type(node)
            current_id = node.id
            if type(current_id) is not int or current_id < 0 or current_id in visited:
                raise ValueError("invalid or cyclic node ancestry")
            if not summaries and current_id != node_id:
                raise ValueError("resolved node ID mismatch")
            if max_nodes is not None and len(summaries) >= max_nodes:
                raise ValueError("node ancestry exceeds bound")
            visited.add(current_id)
            parent = node.parent
            parent_id = None
            if parent is not None:
                if type(parent) is not node_type:
                    _named(parent, "UnifiedTreeNode")
                parent_id = parent.id
                if type(parent_id) is not int or parent_id < 0:
                    raise ValueError("invalid parent ID")
            pending_write = node.write_through_pending_id
            pending_load = node.load_back_pending_id
            if (
                pending_write is not None
                and (type(pending_write) is not int or pending_write < 0)
                or pending_load is not None
                and (type(pending_load) is not int or pending_load < 0)
            ):
                raise ValueError("invalid pending transfer ID")
            if reject_pending and (pending_write is not None or pending_load is not None):
                # Action planning already rejects this path. Diagnostics still
                # capture it, but no action needs the remaining scalar summaries.
                return UnifiedNodeClosureObservation(
                    observable=False,
                    reason=(
                        "pending_write_transfer" if pending_write is not None
                        else "pending_load_transfer"
                    ),
                )
            components = node.component_data
            full = components[0]
            mamba = components[2]
            if type(full) is not component_type:
                _named(full, "ComponentData")
                component_type = type(full)
            if type(mamba) is not component_type:
                _named(mamba, "ComponentData")
            full_value = full.value
            if full_value is None:
                full_device_tokens = 0
            else:
                shape = getattr(full_value, "shape", None)
                full_device_tokens = shape[0] if shape else len(full_value)
                if type(full_device_tokens) is not int or not 0 <= full_device_tokens <= device_full_tokens:
                    raise ValueError("invalid FULL device length")
            full_host_value = full.host_value
            if full_host_value is None:
                full_host_tokens = 0
            else:
                shape = getattr(full_host_value, "shape", None)
                full_host_tokens = shape[0] if shape else len(full_host_value)
                if type(full_host_tokens) is not int or not 0 <= full_host_tokens <= host_full_tokens:
                    raise ValueError("invalid FULL host length")
            full_device_locks = full.lock_ref
            full_host_locks = full.host_lock_ref
            mamba_device_locks = mamba.lock_ref
            mamba_host_locks = mamba.host_lock_ref
            if (
                type(full_device_locks) is not int or not 0 <= full_device_locks <= 2**31 - 1
                or type(full_host_locks) is not int or not 0 <= full_host_locks <= 2**31 - 1
                or type(mamba_device_locks) is not int or not 0 <= mamba_device_locks <= 2**31 - 1
                or type(mamba_host_locks) is not int or not 0 <= mamba_host_locks <= 2**31 - 1
            ):
                raise ValueError("invalid node lock count")
            full_session_refs = full.session_ref
            mamba_session_refs = mamba.session_ref
            if (
                type(full_session_refs) is not int or not 0 <= full_session_refs <= 2**31 - 1
                or type(mamba_session_refs) is not int or not 0 <= mamba_session_refs <= 2**31 - 1
            ):
                raise ValueError("invalid session reference count")
            full_ids, mamba_ids = full.session_ids, mamba.session_ids
            if full_ids is None and mamba_ids is None:
                full_session_leaves = mamba_session_leaves = 0
            else:
                for ids in (full_ids, mamba_ids):
                    if ids is None:
                        continue
                    if type(ids) is not set or not ids:
                        raise ValueError("invalid session leaf identities")
                    for session_id in ids:
                        if type(session_id) is not str or not session_id:
                            raise ValueError("invalid session leaf identities")
                full_session_leaves = 0 if full_ids is None else len(full_ids)
                mamba_session_leaves = 0 if mamba_ids is None else len(mamba_ids)
            creation_time = normalize_native_creation_time(node.creation_time)
            mamba_device_present = mamba.value is not None
            mamba_host_present = mamba.host_value is not None
            key = getattr(node, "key", None)
            key_tokens = None if key is None else len(key)
            if key_tokens is not None and (
                type(key_tokens) is not int or not 0 <= key_tokens <= 2**31 - 1
            ):
                raise ValueError("invalid node key length")
            # Field order matches UnifiedNodeSummary, avoiding a per-node kwargs dict.
            summaries.append(
                UnifiedNodeSummary(
                    current_id,
                    parent_id,
                    creation_time,
                    full_device_tokens,
                    full_host_tokens,
                    mamba_device_present,
                    mamba_host_present,
                    full_device_locks,
                    full_host_locks,
                    mamba_device_locks,
                    mamba_host_locks,
                    full_session_refs,
                    mamba_session_refs,
                    full_session_leaves,
                    mamba_session_leaves,
                    pending_write,
                    pending_load,
                    key_tokens,
                )
            )
            node = parent
        return UnifiedNodeClosureObservation(
            observable=True,
            nodes=tuple(summaries),
            captured_monotonic_s=time.monotonic(),
        )
    except (AttributeError, IndexError, KeyError, NotImplementedError, TypeError, ValueError) as exc:
        return UnifiedNodeClosureObservation(observable=False, reason=str(exc))
