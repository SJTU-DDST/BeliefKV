"""CPU-only stand-ins for the inspected v0.5.20 UnifiedMamba pool wiring."""

from __future__ import annotations

from enum import IntEnum
from types import SimpleNamespace as NS

import numpy as np
import pytest

from beliefkv.runtime.sglang_v0520_observer import (
    observe_static_full_mamba,
    observe_static_full_mamba_headroom,
    observe_static_full_mamba_host_usage,
    observe_unified_full_mamba,
    observe_unified_full_mamba_usage,
    observe_unified_node_closure,
)
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from beliefkv.runtime.sglang_v0520_physical import (
    ContextSessionAnchors,
    capture_action_local_shadow,
    inspect_session_h2d_opportunity,
    next_prefetch_gpu_step,
)


def _static_cache() -> object:
    class TokenToKVPoolAllocator(NS):
        pass

    class HybridReqToTokenPool(NS):
        pass

    class HybridLinearKVPool(NS):
        pass

    class MHATokenToKVPool(NS):
        pass

    class MambaPool(NS):
        pass

    class MambaSlotAllocator(NS):
        pass

    class MHATokenToKVPoolHost(NS):
        pass

    class State(NS):
        pass

    class Tensor:
        def __init__(self, count: int) -> None:
            self.count = count

        def numel(self) -> int:
            return self.count

        def element_size(self) -> int:
            return 2

    full = MHATokenToKVPool(
        device="cuda", size=12,
        get_kv_size_bytes=lambda: (np.int64(130), np.int64(130)),
    )
    mamba = MambaPool(
        device="cuda", size=3,
        mamba_cache=State(conv=[Tensor(30)], temporal=Tensor(90)),
    )
    hybrid = HybridLinearKVPool(full_kv_pool=full, mamba_pool=mamba)
    allocator = TokenToKVPoolAllocator(
        size=12, _kvcache=hybrid,
        available_size=lambda: 10, free_group=None,
    )
    req = HybridReqToTokenPool(
        mamba_pool=mamba,
        mamba_allocator=MambaSlotAllocator(
            size=3, available_size=lambda: 2, _alloc_iter=None,
        ),
    )
    host_full = MHATokenToKVPoolHost(
        size=25, size_per_token=20, device_pool=full,
        available_size=lambda: 20,
    )
    host_mamba = MambaPoolHost(
        size=6, size_per_token=80, device_pool=mamba,
        available_size=lambda: 5,
    )
    full_entry = NS(device_pool=full, host_pool=host_full)
    mamba_entry = NS(device_pool=mamba, host_pool=host_mamba)
    group = HostPoolGroup(
        entry_map={"kv": full_entry, "mamba": mamba_entry},
        anchor_entry=full_entry,
    )
    cache = UnifiedRadixCache(
        disable=False, tree_components=(ComponentType.FULL, ComponentType.MAMBA),
        token_to_kv_pool_allocator=allocator, req_to_token_pool=req,
        host_pool_group=group,
        cache_controller=NS(mem_pool_host=group),
    )
    return cache


def test_static_full_mamba_census_counts_separate_device_allocations() -> None:
    result = observe_static_full_mamba(_static_cache())
    assert result["device_total_bytes"] == 500
    assert result["device_full_tokens"] == 12
    assert result["device_mamba_slots"] == 3
    assert result["host_total_bytes"] == 980


def test_static_pool_headroom_is_read_only_and_not_a_certificate() -> None:
    cache = _static_cache()
    result = observe_static_full_mamba_headroom(cache)
    assert result.observable
    assert (
        result.device_full_free_tokens,
        result.device_mamba_free_slots,
        result.host_full_free_tokens,
        result.host_mamba_free_slots,
    ) == (10, 2, 20, 5)
    assert result.physical_actions_supported is False


@pytest.mark.parametrize("break_free_list", [
    lambda cache: setattr(cache.token_to_kv_pool_allocator, "free_group", []),
    lambda cache: setattr(
        cache.req_to_token_pool.mamba_allocator, "_alloc_iter", iter(()),
    ),
    lambda cache: setattr(
        cache.req_to_token_pool.mamba_allocator, "available_size", lambda: 4,
    ),
    lambda cache: setattr(
        cache.token_to_kv_pool_allocator, "available_size", lambda: True,
    ),
    lambda cache: setattr(
        cache.host_pool_group.entry_map["kv"].host_pool,
        "available_size", lambda: -1,
    ),
    lambda cache: setattr(
        cache.req_to_token_pool, "mamba_allocator", NS(
            size=3, available_size=lambda: 2, _alloc_iter=None,
        ),
    ),
])
def test_static_headroom_rejects_ambiguous_or_unphysical_free_lists(
    break_free_list,
) -> None:
    cache = _static_cache()
    break_free_list(cache)
    result = observe_static_full_mamba_headroom(cache)
    assert not result.observable
    assert result.reason
    assert result.device_full_free_tokens is None


def test_static_host_usage_reports_full_and_mamba_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from beliefkv.runtime import sglang_v0520_observer

    full_pool = NS(
        size_per_token=32,
        available_size=lambda: 75,
    )
    mamba_pool = NS(
        size_per_token=128,
        available_size=lambda: 4,
    )
    cache = NS(
        host_pool_group=NS(
            entry_map={
                "kv": NS(host_pool=full_pool),
                "mamba": NS(host_pool=mamba_pool),
            }
        )
    )
    monkeypatch.setattr(
        sglang_v0520_observer,
        "observe_static_full_mamba",
        lambda _: {
            "host_full_tokens": 100,
            "host_mamba_slots": 10,
        },
    )

    result = observe_static_full_mamba_host_usage(cache)

    assert result.observable
    assert (
        result.host_full_used_tokens,
        result.host_full_used_bytes,
        result.host_mamba_used_slots,
        result.host_mamba_used_bytes,
    ) == (25, 800, 6, 768)


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


def test_native_float64_node_creation_time_is_normalized_for_h2d() -> None:
    cache = _static_cache()
    root = _node(0)
    parent = _node(4, root, device=[1, 2])
    leaf = _node(5, parent, device=[3], mamba_host=True)
    leaf.component_data[2].value = None
    for node in (root, parent, leaf):
        node.creation_time = np.float64(node.creation_time)
    cache.tree_core = UnifiedTreeCore(
        node_by_id=lambda node_id: {5: leaf}[node_id]
    )
    observed = observe_unified_node_closure(cache, 5)
    assert observed.observable
    assert all(type(node.creation_time) is float for node in observed.nodes)
    anchors = ContextSessionAnchors(
        key=PrefillCandidateKey("req", "wf", "parent", "ctx", 2, 0, "session", 1),
        component_leaves=((0, ((5, 15.0),)), (2, ((5, 15.0),))),
        captured_monotonic_s=1.0,
    )
    opportunity = inspect_session_h2d_opportunity(cache, anchors)
    assert opportunity.step is not None
    assert opportunity.step.node_id == 5
    assert type(opportunity.step.creation_time) is float
    assert opportunity.required_mamba_slots == 1


@pytest.mark.parametrize("created", [np.float64("nan"), np.float64("inf"), True])
def test_node_closure_rejects_invalid_creation_time(created) -> None:
    cache = _static_cache()
    node = _node(5)
    node.creation_time = created
    cache.tree_core = UnifiedTreeCore(node_by_id=lambda node_id: node)
    assert not observe_unified_node_closure(cache, 5).observable


def test_static_full_mamba_action_closure_uses_real_separate_pool_layout() -> None:
    cache = _static_cache()
    assert not observe_unified_full_mamba(cache).observable
    root = _node(0)
    parent = _node(4, root, device=[1, 2], host=[10, 11], mamba_host=True)
    leaf = _node(5, parent, host=[12])
    cache.tree_core = UnifiedTreeCore(
        node_by_id=lambda node_id: {5: leaf}[node_id]
    )
    result = observe_unified_node_closure(cache, 5)
    assert result.observable
    assert [node.node_id for node in result.nodes] == [5, 4, 0]
    assert result.nodes[0].full_host_tokens == 1
    assert result.nodes[1].mamba_host_present
    anchors = ContextSessionAnchors(
        key=PrefillCandidateKey("req", "wf", "parent", "ctx", 2, 0, "session", 1),
        component_leaves=((0, ((5, 15),)), (2, ((5, 15),))),
        captured_monotonic_s=1.0,
    )
    candidate = capture_action_local_shadow(cache, anchors, for_prefetch=True)
    assert candidate is not None
    assert candidate.missing_full_device_tokens == 1
    assert next_prefetch_gpu_step(candidate).node_id == 5
    opportunity = inspect_session_h2d_opportunity(cache, anchors)
    assert opportunity.step.node_id == 5
    assert (opportunity.required_full_tokens, opportunity.required_mamba_slots) == (
        1, 0,
    )
    assert opportunity.fits_current_free_lists is True
    assert opportunity.no_step_reason is None
    cache.token_to_kv_pool_allocator.available_size = lambda: 0
    assert inspect_session_h2d_opportunity(
        cache, anchors,
    ).fits_current_free_lists is False
    leaf.component_data[2].host_value = [1]
    cache.req_to_token_pool.mamba_allocator.available_size = lambda: 0
    opportunity = inspect_session_h2d_opportunity(cache, anchors)
    assert opportunity.required_mamba_slots == 1
    assert opportunity.fits_current_free_lists is False
    leaf.component_data[0].value = [1]
    leaf.component_data[2].value = [2]
    assert capture_action_local_shadow(cache, anchors, for_prefetch=True) is None
    resident = inspect_session_h2d_opportunity(cache, anchors)
    assert resident.step is None
    assert resident.no_step_reason == "already_device_resident"
    cache.host_pool_group.entry_map["mamba"].device_pool = object()
    assert not observe_unified_node_closure(cache, 5).observable
    assert capture_action_local_shadow(cache, anchors, for_prefetch=True) is None
    opportunity = inspect_session_h2d_opportunity(cache, anchors)
    assert not opportunity.headroom.observable
    assert opportunity.step is None
    assert opportunity.fits_current_free_lists is None
    assert opportunity.no_step_reason == "closure_unobservable"


def test_static_action_closure_rejects_capacity_and_tree_backend_mismatch() -> None:
    cache = _static_cache()
    root = _node(0)
    leaf = _node(5, root, host=list(range(26)))
    cache.tree_core = UnifiedTreeCore(
        node_by_id=lambda node_id: {5: leaf}[node_id]
    )
    assert not observe_unified_node_closure(cache, 5).observable
    leaf.component_data[0].host_value = [1]
    cache.tree_core = NS(node_by_id=lambda _: leaf)
    assert not observe_unified_node_closure(cache, 5).observable


def test_static_action_closure_fails_closed_on_allocator_census_exception() -> None:
    cache = _static_cache()

    def fail_pool_read() -> None:
        raise RuntimeError("pool not ready")

    cache.token_to_kv_pool_allocator._kvcache.full_kv_pool.get_kv_size_bytes = fail_pool_read
    result = observe_unified_node_closure(cache, 5)
    assert not result.observable
    assert "pool not ready" in result.reason


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
