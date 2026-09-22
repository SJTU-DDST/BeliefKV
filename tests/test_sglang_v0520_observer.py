"""CPU-only stand-ins for the inspected v0.5.20 UnifiedMamba pool wiring."""

from __future__ import annotations

from enum import IntEnum
from types import SimpleNamespace as NS

import pytest

from beliefkv.runtime.sglang_v0520_observer import (
    observe_unified_full_mamba,
    observe_unified_full_mamba_usage,
    observe_unified_node_closure,
)


class ComponentType(IntEnum):
    FULL = 0
    MAMBA = 2
    SWA = 1


class UnifiedRadixCache(NS):
    pass


class UnifiedMambaTokenToKVPoolAllocator(NS):
    pass


class UnifiedHybridReqToTokenPool(NS):
    pass


class UnifiedMambaSlotAllocator(NS):
    pass


class UnifiedKVPool(NS):
    pass


class MultiEndedAllocator(NS):
    pass


class HostPoolGroup(NS):
    pass


class MHATokenToKVPoolHost(NS):
    pass


class MambaPoolHost(NS):
    pass


class UnifiedTreeNode(NS):
    pass


class UnifiedTreeCore(NS):
    pass


class ComponentData(NS):
    pass


def _never(*args, **kwargs):
    raise AssertionError("observer must not call runtime methods")


def _cache() -> UnifiedRadixCache:
    buffer = UnifiedKVPool(
        total_bytes=800, _specs_by_name={"full": NS(), "mamba": NS()}
    )
    full_device = object()
    mamba_device = object()
    # 800 bytes / 8 bytes per FULL row = 100 physical rows; 800/20
    # = 40 MAMBA slots. The reserved sink consumes one FULL page (4 rows)
    # and two MAMBA slots. FULL DCP widening is x2 logical tokens per row.
    full = MultiEndedAllocator(
        unified_buffer=buffer,
        sub_pool_name="full",
        max_slots=100,
        entry_bytes=8,
        pool_page_size=4,
        page_size=8,
        num_pages=25,
        min_slot_index=2,
        min_page_index=1,
        _kvcache=full_device,
        available_size=_never,
        schedulable_available_size=_never,
    )
    mamba = MultiEndedAllocator(
        unified_buffer=buffer,
        sub_pool_name="mamba",
        max_slots=40,
        entry_bytes=20,
        pool_page_size=1,
        page_size=1,
        num_pages=40,
        min_slot_index=2,
        min_page_index=2,
        _kvcache=mamba_device,
        available_size=_never,
        schedulable_available_size=_never,
    )
    allocator = UnifiedMambaTokenToKVPoolAllocator(
        unified_buffer=buffer,
        full_attn_allocator=full,
        mamba_allocator=mamba,
        _kvcache=NS(full_kv_pool=full_device),
        available_size=_never,
        full_available_size=_never,
    )
    requests = UnifiedHybridReqToTokenPool(
        _unified_buffer=buffer,
        mamba_pool=mamba_device,
        mamba_allocator=UnifiedMambaSlotAllocator(
            _multi_ended_allocator=mamba, _max_size=39, available_size=_never
        ),
    )
    host_full = MHATokenToKVPoolHost(
        size=64, dcp_size=2, page_size=4, device_pool=full_device,
        size_per_token=8, available_size=_never,
    )
    host_mamba = MambaPoolHost(
        size=21, page_size=1, device_pool=mamba_device,
        size_per_token=20, available_size=_never,
    )
    kv_entry = NS(
        device_pool=full_device, host_pool=host_full, is_primary_index_anchor=True
    )
    mamba_entry = NS(
        device_pool=mamba_device, host_pool=host_mamba, is_primary_index_anchor=False
    )
    group = HostPoolGroup(
        entry_map={"kv": kv_entry, "mamba": mamba_entry},
        anchor_entry=kv_entry,
        size=host_full.size,
        available_size=_never,
    )
    return UnifiedRadixCache(
        disable=False,
        is_swa_enabled=False,
        is_mamba_enabled=True,
        host_memory_mode="cache",
        tree_components=(ComponentType.FULL, ComponentType.MAMBA),
        token_to_kv_pool_allocator=allocator,
        req_to_token_pool=requests,
        host_pool_group=group,
        cache_controller=NS(mem_pool_host=group),
        evictable_size=_never,
        total_size=_never,
    )


def _closed(cache: object) -> None:
    result = observe_unified_full_mamba(cache)
    assert not result.observable
    assert result.reason
    assert result.physical_actions_supported is False
    assert all(getattr(result, name) is None for name in result.missing_metrics)
    assert len(result.missing_metrics) == 10


def test_distinct_shared_device_ceiling_and_host_dcp_capacity() -> None:
    cache = _cache()
    result = observe_unified_full_mamba(cache)
    assert result.observable
    assert result.reason is None
    assert result.missing_metrics == ()
    assert result.physical_actions_supported is False
    assert (result.device_full_tokens, result.device_mamba_slots) == (192, 38)
    assert (result.host_full_tokens, result.host_mamba_slots) == (128, 21)
    assert result.shared_device_bytes == 800
    assert (result.device_full_ceiling_bytes, result.device_mamba_ceiling_bytes) == (
        768, 760,
    )
    assert (result.host_full_bytes, result.host_mamba_bytes, result.host_total_bytes) == (
        512, 420, 932,
    )
    assert "device_available" in result.unsupported_metrics
    assert "physical_commands" in result.unsupported_metrics


@pytest.mark.parametrize(
    "break_wiring",
    [
        lambda c: setattr(c, "disable", True),
        lambda c: setattr(c, "tree_components", (ComponentType.FULL,)),
        lambda c: setattr(
            c, "tree_components",
            (ComponentType.FULL, ComponentType.SWA, ComponentType.MAMBA),
        ),
        lambda c: setattr(c, "host_pool_group", None),
        lambda c: setattr(c, "cache_controller", None),
        lambda c: setattr(c, "host_memory_mode", "buffer_only"),
        lambda c: setattr(c, "is_mamba_enabled", False),
        lambda c: setattr(
            c.host_pool_group,
            "entry_map",
            {"kv": c.host_pool_group.entry_map["kv"]},
        ),
        lambda c: c.token_to_kv_pool_allocator.unified_buffer._specs_by_name.update(
            {"swa": NS()}
        ),
        lambda c: setattr(
            c.token_to_kv_pool_allocator.unified_buffer, "total_bytes", 799
        ),
        lambda c: setattr(
            c.token_to_kv_pool_allocator.mamba_allocator, "min_page_index", 1
        ),
        lambda c: setattr(c.token_to_kv_pool_allocator.mamba_allocator, "page_size", 2),
        lambda c: setattr(c.req_to_token_pool.mamba_allocator, "_max_size", 40),
        lambda c: setattr(
            c.host_pool_group.entry_map["mamba"], "device_pool", object()
        ),
        lambda c: setattr(c.host_pool_group.entry_map["kv"].host_pool, "dcp_size", 0),
        lambda c: setattr(c.host_pool_group.entry_map["kv"].host_pool, "dcp_size", 1),
        lambda c: setattr(c.host_pool_group.entry_map["kv"].host_pool, "size_per_token", 0),
        lambda c: setattr(c.host_pool_group.entry_map["mamba"].host_pool, "size_per_token", -1),
    ],
)
def test_unknown_or_inconsistent_structures_fail_closed(break_wiring) -> None:
    cache = _cache()
    break_wiring(cache)
    _closed(cache)


def test_absent_and_legacy_inputs_fail_closed() -> None:
    _closed(None)
    _closed(NS(tree_components=(ComponentType.FULL, ComponentType.MAMBA)))
    cache = _cache()
    cache.token_to_kv_pool_allocator = NS(size=100, available_size=_never)
    _closed(cache)


def test_observation_does_not_change_runtime_state() -> None:
    cache = _cache()
    allocator = cache.token_to_kv_pool_allocator
    before = (
        cache.tree_components,
        allocator.unified_buffer.total_bytes,
        allocator.full_attn_allocator.min_page_index,
        allocator.mamba_allocator.min_page_index,
        cache.host_pool_group.entry_map.copy(),
    )
    observe_unified_full_mamba(cache)
    after = (
        cache.tree_components,
        allocator.unified_buffer.total_bytes,
        allocator.full_attn_allocator.min_page_index,
        allocator.mamba_allocator.min_page_index,
        cache.host_pool_group.entry_map.copy(),
    )
    assert after == before


def test_usage_counts_do_not_confuse_independent_device_ceilings() -> None:
    cache = _cache()
    allocator = cache.token_to_kv_pool_allocator
    allocator.full_attn_allocator.allocated_count = lambda: 80
    allocator.mamba_allocator.allocated_count = lambda: 9
    cache.host_pool_group.entry_map["kv"].host_pool.available_size = lambda: 104
    cache.host_pool_group.entry_map["mamba"].host_pool.available_size = lambda: 17
    result = observe_unified_full_mamba_usage(cache)
    assert result.observable
    assert (
        result.device_full_live_tokens,
        result.device_mamba_live_slots,
        result.host_full_used_tokens,
        result.host_mamba_used_slots,
    ) == (80, 9, 24, 4)
    assert not result.physical_actions_supported
    assert not hasattr(result, "device_available_bytes")


@pytest.mark.parametrize(
    "invalid_counter",
    [
        lambda c: setattr(
            c.token_to_kv_pool_allocator.full_attn_allocator,
            "allocated_count",
            lambda: 193,
        ),
        lambda c: setattr(
            c.token_to_kv_pool_allocator.mamba_allocator,
            "allocated_count",
            lambda: -1,
        ),
        lambda c: setattr(
            c.host_pool_group.entry_map["kv"].host_pool,
            "available_size",
            lambda: 129,
        ),
        lambda c: setattr(
            c.host_pool_group.entry_map["mamba"].host_pool,
            "available_size",
            lambda: 22,
        ),
    ],
)
def test_usage_inconsistent_counters_fail_closed(invalid_counter) -> None:
    cache = _cache()
    allocator = cache.token_to_kv_pool_allocator
    allocator.full_attn_allocator.allocated_count = lambda: 1
    allocator.mamba_allocator.allocated_count = lambda: 1
    cache.host_pool_group.entry_map["kv"].host_pool.available_size = lambda: 100
    cache.host_pool_group.entry_map["mamba"].host_pool.available_size = lambda: 20
    invalid_counter(cache)
    result = observe_unified_full_mamba_usage(cache)
    assert not result.observable
    assert result.device_full_live_tokens is None
    assert result.host_full_used_tokens is None


def test_usage_does_not_run_counter_methods_on_unknown_cache() -> None:
    cache = _cache()
    cache.host_pool_group.entry_map["mamba"].device_pool = object()
    result = observe_unified_full_mamba_usage(cache)
    assert not result.observable


def _node(node_id: int, parent=None, *, device=(), host=(), mamba_host=False):
    full = ComponentData(
        value=list(device) if device else None,
        host_value=list(host) if host else None,
        lock_ref=0,
        host_lock_ref=0,
        session_ref=0,
        session_ids=None,
    )
    mamba = ComponentData(
        value=[1] if device else None,
        host_value=[1] if mamba_host else None,
        lock_ref=0,
        host_lock_ref=0,
        session_ref=0,
        session_ids=None,
    )
    return UnifiedTreeNode(
        id=node_id,
        parent=parent,
        creation_time=10 + node_id,
        component_data=[full, ComponentData(), mamba],
        write_through_pending_id=None,
        load_back_pending_id=None,
    )


def test_node_closure_captures_only_requested_ancestry() -> None:
    cache = _cache()
    root = _node(0)
    ancestor = _node(4, root, device=[1, 2], host=[10, 11], mamba_host=True)
    leaf = _node(5, ancestor, device=[3])
    leaf.component_data[0].lock_ref = 2
    leaf.component_data[0].session_ref = 2
    leaf.component_data[0].session_ids = {"agent-a"}
    leaf.component_data[2].session_ref = 1
    leaf.component_data[2].session_ids = {"agent-b"}
    leaf.write_through_pending_id = 5
    cache.tree_core = UnifiedTreeCore(node_by_id=lambda node_id: {5: leaf}[node_id])
    result = observe_unified_node_closure(cache, 5)
    assert result.observable
    assert [item.node_id for item in result.nodes] == [5, 4, 0]
    assert result.nodes[0].parent_id == 4
    assert result.nodes[0].full_device_tokens == 1
    assert result.nodes[0].full_device_locks == 2
    assert result.nodes[0].full_session_refs == 2
    assert result.nodes[0].mamba_session_refs == 1
    assert result.nodes[0].full_session_leaf_count == 1
    assert result.nodes[0].mamba_session_leaf_count == 1
    assert result.nodes[0].pending_write_id == 5
    assert result.nodes[1].full_host_tokens == 2
    assert result.nodes[1].mamba_host_present
    assert result.captured_monotonic_s is not None
    assert not result.physical_actions_supported


def test_node_closure_rejects_cycle_excess_depth_and_absent_id() -> None:
    cache = _cache()
    root = _node(1)
    leaf = _node(2, root, device=[1])
    cache.tree_core = UnifiedTreeCore(node_by_id=lambda node_id: {2: leaf}[node_id])
    assert not observe_unified_node_closure(cache, 2, max_nodes=1).observable
    assert not observe_unified_node_closure(cache, 99).observable
    assert not observe_unified_node_closure(cache, 2, max_nodes=0).observable
    root.parent = leaf
    assert not observe_unified_node_closure(cache, 2).observable


def test_node_closure_fails_closed_on_unphysical_lengths() -> None:
    cache = _cache()
    oversized = _node(9, device=range(193))
    cache.tree_core = UnifiedTreeCore(node_by_id=lambda node_id: oversized)
    result = observe_unified_node_closure(cache, 9)
    assert not result.observable
    assert result.nodes == ()


def test_node_closure_rejects_unknown_tree_or_mismatched_node() -> None:
    cache = _cache()
    cache.tree_core = NS(node_by_id=lambda node_id: _node(node_id))
    assert not observe_unified_node_closure(cache, 5).observable
    cache.tree_core = UnifiedTreeCore(node_by_id=lambda node_id: _node(6))
    assert not observe_unified_node_closure(cache, 5).observable


@pytest.mark.parametrize("field,value", [
    ("id", -1),
    ("write_through_pending_id", True),
    ("load_back_pending_id", -1),
])
def test_node_closure_rejects_invalid_identity_or_pending(field, value) -> None:
    cache = _cache()
    node = _node(5)
    setattr(node, field, value)
    cache.tree_core = UnifiedTreeCore(node_by_id=lambda node_id: node)
    assert not observe_unified_node_closure(cache, 5).observable


@pytest.mark.parametrize(
    "session_ref,session_ids",
    [(-1, None), (True, None), (0, set()), (0, {"", "valid"}), (0, {str(i) for i in range(65)})],
)
def test_node_closure_rejects_unknown_session_reference(session_ref, session_ids) -> None:
    cache = _cache()
    node = _node(5)
    node.component_data[0].session_ref = session_ref
    node.component_data[0].session_ids = session_ids
    cache.tree_core = UnifiedTreeCore(node_by_id=lambda node_id: node)
    assert not observe_unified_node_closure(cache, 5).observable


def test_node_closure_fails_closed_on_unimplemented_tree_lookup() -> None:
    cache = _cache()

    def unavailable(_node_id):
        raise NotImplementedError("node_by_id: not yet ported")

    cache.tree_core = UnifiedTreeCore(node_by_id=unavailable)
    assert not observe_unified_node_closure(cache, 5).observable
