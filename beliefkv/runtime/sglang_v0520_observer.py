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
import operator
import time


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


def observe_static_full_mamba(cache: object) -> dict[str, int | str]:
    """Read the default, separately allocated FULL and MAMBA device pools.

    Unlike unified-memory, these allocations cannot lend bytes to each other.
    Only persistent KV/state tensor storage is counted; CUDA scratch and graph
    buffers consume additional GPU memory outside these pools.
    """
    _named(cache, "UnifiedRadixCache")
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
    full_k_bytes, full_v_bytes = full.get_kv_size_bytes()
    device_full_bytes = (
        _positive(full_k_bytes, "device FULL K bytes")
        + _positive(full_v_bytes, "device FULL V bytes")
    )
    state = mamba.mamba_cache
    if type(state).__name__ != "State":
        raise ValueError("unknown static MAMBA state layout")

    def tensor_bytes(tensor: object) -> int:
        return int(tensor.numel()) * int(tensor.element_size())

    device_mamba_bytes = _positive(
        sum(tensor_bytes(item) for item in state.conv)
        + tensor_bytes(state.temporal),
        "device MAMBA bytes",
    )
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
        "device_full_bytes": device_full_bytes,
        "device_mamba_bytes": device_mamba_bytes,
        "device_total_bytes": device_full_bytes + device_mamba_bytes,
        "host_full_tokens": host_full_tokens,
        "host_mamba_slots": host_mamba_slots,
        "host_full_bytes": host_full_bytes,
        "host_mamba_bytes": host_mamba_bytes,
        "host_total_bytes": host_full_bytes + host_mamba_bytes,
    }


def observe_static_full_mamba_host_usage(
    cache: object,
) -> StaticHostUsageObservation:
    """Read current allocated slots from the separate FULL and MAMBA host pools."""
    try:
        capacity = observe_static_full_mamba(cache)
        group = cache.host_pool_group
        host_full = group.entry_map["kv"].host_pool
        host_mamba = group.entry_map["mamba"].host_pool
        full_used = _bounded(
            capacity["host_full_tokens"] - host_full.available_size(),
            capacity["host_full_tokens"],
            "static Host FULL used tokens",
        )
        mamba_used = _bounded(
            capacity["host_mamba_slots"] - host_mamba.available_size(),
            capacity["host_mamba_slots"],
            "static Host MAMBA used slots",
        )
        return StaticHostUsageObservation(
            observable=True,
            host_full_used_tokens=full_used,
            host_full_used_bytes=full_used * host_full.size_per_token,
            host_mamba_used_slots=mamba_used,
            host_mamba_used_bytes=mamba_used * host_mamba.size_per_token,
        )
    except (AttributeError, KeyError, TypeError, ValueError, RuntimeError) as exc:
        return StaticHostUsageObservation(observable=False, reason=str(exc))


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
    cache: object, node_id: int, *, max_nodes: int = 64
) -> UnifiedNodeClosureObservation:
    """Capture only a requested node and its ancestors, never transfer indices.

    `creation_time` and pending/lock fields form a *local fingerprint*, not
    an atomic revision. Recheck the live tree before any physical command.
    """
    capacity = observe_unified_full_mamba(cache)
    if not capacity.observable:
        return UnifiedNodeClosureObservation(observable=False, reason=capacity.reason)
    try:
        if type(node_id) is not int or node_id < 0:
            raise ValueError("invalid node ID")
        if type(max_nodes) is not int or max_nodes <= 0:
            raise ValueError("invalid closure bound")
        tree = _named(cache.tree_core, "UnifiedTreeCore")
        node = tree.node_by_id(node_id)
        visited: set[int] = set()
        summaries: list[UnifiedNodeSummary] = []
        while node is not None:
            _named(node, "UnifiedTreeNode")
            if type(node.id) is not int or node.id < 0 or node.id in visited:
                raise ValueError("invalid or cyclic node ancestry")
            if not summaries and node.id != node_id:
                raise ValueError("resolved node ID mismatch")
            if len(summaries) >= max_nodes:
                raise ValueError("node ancestry exceeds bound")
            visited.add(node.id)
            parent = node.parent
            if parent is not None:
                _named(parent, "UnifiedTreeNode")
                if type(parent.id) is not int or parent.id < 0:
                    raise ValueError("invalid parent ID")
            full = node.component_data[0]
            mamba = node.component_data[2]
            for item in (full, mamba):
                _named(item, "ComponentData")
            full_device_tokens = (
                0 if full.value is None else _bounded(len(full.value), capacity.device_full_tokens, "FULL device length")
            )
            full_host_tokens = (
                0 if full.host_value is None else _bounded(len(full.host_value), capacity.host_full_tokens, "FULL host length")
            )
            locks = tuple(
                _bounded(getattr(item, field), 2**31 - 1, "node lock count")
                for item in (full, mamba)
                for field in ("lock_ref", "host_lock_ref")
            )
            session_refs = tuple(
                _bounded(item.session_ref, 2**31 - 1, "session reference count")
                for item in (full, mamba)
            )
            session_leaves = []
            for item in (full, mamba):
                ids = item.session_ids
                if ids is not None:
                    if type(ids) is not set or not 0 < len(ids) <= 64:
                        raise ValueError("session leaf identities exceed local bound")
                    if any(
                        type(session_id) is not str or not session_id
                        for session_id in ids
                    ):
                        raise ValueError("invalid session leaf identities")
                session_leaves.append(
                    0 if ids is None else len(ids)
                )
            pending_write = node.write_through_pending_id
            pending_load = node.load_back_pending_id
            for pending in (pending_write, pending_load):
                if pending is not None and (type(pending) is not int or pending < 0):
                    raise ValueError("invalid pending transfer ID")
            summaries.append(
                UnifiedNodeSummary(
                    node_id=node.id,
                    parent_id=None if parent is None else parent.id,
                    creation_time=node.creation_time,
                    full_device_tokens=full_device_tokens,
                    full_host_tokens=full_host_tokens,
                    mamba_device_present=mamba.value is not None,
                    mamba_host_present=mamba.host_value is not None,
                    full_device_locks=locks[0],
                    full_host_locks=locks[1],
                    mamba_device_locks=locks[2],
                    mamba_host_locks=locks[3],
                    full_session_refs=session_refs[0],
                    mamba_session_refs=session_refs[1],
                    full_session_leaf_count=session_leaves[0],
                    mamba_session_leaf_count=session_leaves[1],
                    pending_write_id=pending_write,
                    pending_load_id=pending_load,
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
