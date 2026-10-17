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


def test_native_without_host_preserves_device_census_and_requires_opt_in() -> None:
    cache = _static_cache()
    expected = observe_static_full_mamba(cache)
    cache.cache_controller = cache.host_pool_group = None
    with pytest.raises((AttributeError, ValueError)):
        observe_static_full_mamba(cache)
    result = observe_static_full_mamba(cache, allow_without_host=True)
    assert {
        key: value for key, value in result.items() if key.startswith("device_")
    } == {key: value for key, value in expected.items() if key.startswith("device_")}
    assert all(value == 0 for key, value in result.items() if key.startswith("host_"))
    assert not observe_static_full_mamba_headroom(cache).observable
    assert not observe_static_full_mamba_host_usage(cache).observable


@pytest.mark.parametrize("fault", ["orphan_host_pool", "orphan_controller", "unlinked_mamba"])
def test_no_host_census_rejects_partially_attached_or_unlinked_pools(fault) -> None:
    cache = _static_cache()
    if fault == "orphan_host_pool":
        cache.cache_controller = None
    elif fault == "orphan_controller":
        cache.host_pool_group = None
    else:
        cache.cache_controller = cache.host_pool_group = None
        cache.token_to_kv_pool_allocator._kvcache.mamba_pool = object()
    with pytest.raises((AttributeError, ValueError)):
        observe_static_full_mamba(cache, allow_without_host=True)


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
    lambda cache: setattr(
        cache.req_to_token_pool.mamba_allocator, "size", 4,
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
def test_static_headroom_rejects_unphysical_free_lists(
    break_free_list,
) -> None:
    cache = _static_cache()
    break_free_list(cache)
    result = observe_static_full_mamba_headroom(cache)
    assert not result.observable
    assert result.reason
    assert result.device_full_free_tokens is None


@pytest.mark.parametrize("deferred, reserved", [
    (False, False), (True, False), (False, True), (True, True),
])
def test_static_headroom_counts_only_free_lists_during_native_groups(
    deferred, reserved,
) -> None:
    cache = _static_cache()
    pending = [NS(shape=(2,))]
    reserved_slots = iter([NS(shape=(1,))])
    full = cache.token_to_kv_pool_allocator
    mamba = cache.req_to_token_pool.mamba_allocator
    full.free_group = pending if deferred else None
    mamba._alloc_iter = reserved_slots if reserved else None
    result = observe_static_full_mamba_headroom(cache)
    assert result.observable
    assert result.device_full_free_tokens == 10
    assert result.device_mamba_free_slots == 2
    assert result.full_frees_deferred is deferred
    assert result.mamba_batch_reserved is reserved
    assert result.physical_actions_supported is False
    assert full.free_group is (pending if deferred else None)
    assert mamba._alloc_iter is (reserved_slots if reserved else None)
    assert next(reserved_slots).shape == (1,)


def test_static_host_usage_reports_full_and_mamba_independently() -> None:
    cache = _static_cache()
    full_pool = cache.host_pool_group.entry_map["kv"].host_pool
    mamba_pool = cache.host_pool_group.entry_map["mamba"].host_pool
    full_pool.size = 100
    full_pool.size_per_token = 32
    full_pool.available_size = lambda: 75
    mamba_pool.size = 10
    mamba_pool.size_per_token = 128
    mamba_pool.available_size = lambda: 4

    result = observe_static_full_mamba_host_usage(cache)

    assert result.observable
    assert (
        result.host_full_used_tokens,
        result.host_full_used_bytes,
        result.host_mamba_used_slots,
        result.host_mamba_used_bytes,
    ) == (25, 800, 6, 768)


def test_static_free_lists_remain_live_without_tensor_byte_census() -> None:
    cache = _static_cache()
    full = cache.token_to_kv_pool_allocator._kvcache.full_kv_pool
    full.get_kv_size_bytes = _never
    cache.req_to_token_pool.mamba_pool.mamba_cache.conv[0].numel = _never

    headroom = observe_static_full_mamba_headroom(cache)
    usage = observe_static_full_mamba_host_usage(cache)
    assert headroom.observable and usage.observable
    assert headroom.device_full_free_tokens == 10
    assert usage.host_full_used_tokens == 5

    cache.token_to_kv_pool_allocator.available_size = lambda: 4
    cache.host_pool_group.entry_map["kv"].host_pool.available_size = lambda: 8
    headroom = observe_static_full_mamba_headroom(cache)
    usage = observe_static_full_mamba_host_usage(cache)
    assert headroom.device_full_free_tokens == 4
    assert headroom.host_full_free_tokens == 8
    assert usage.host_full_used_tokens == 17

    cache.host_pool_group.entry_map["mamba"].device_pool = object()
    assert not observe_static_full_mamba_headroom(cache).observable
    assert not observe_static_full_mamba_host_usage(cache).observable


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


def test_static_action_closure_reads_live_geometry_without_tensor_byte_census() -> None:
    cache = _static_cache()
    root = _node(0)
    leaf = _node(5, root, host=[1])
    cache.tree_core = UnifiedTreeCore(
        node_by_id={0: root, 5: leaf}.__getitem__,
    )

    def fail_pool_read() -> None:
        raise RuntimeError("pool not ready")

    cache.token_to_kv_pool_allocator._kvcache.full_kv_pool.get_kv_size_bytes = fail_pool_read
    result = observe_unified_node_closure(cache, 5)
    assert result.observable
    assert result.nodes[0].full_host_tokens == 1
    with pytest.raises(RuntimeError, match="pool not ready"):
        observe_static_full_mamba(cache)

    leaf.component_data[0].host_value = range(26)
    result = observe_unified_node_closure(cache, 5)
    assert not result.observable
    assert "FULL host length" in result.reason

    leaf.component_data[0].host_value = [1]
    cache.token_to_kv_pool_allocator._kvcache.full_kv_pool.size = 11
    result = observe_unified_node_closure(cache, 5)
    assert not result.observable
    assert "allocator and physical pool disagree" in result.reason


@pytest.mark.parametrize("allocator_name", ("TokenToKVPoolAllocator", "PagedTokenToKVPoolAllocator"))
def test_static_closure_keeps_live_validation_on_both_allocator_layouts(allocator_name) -> None:
    cache = _static_cache()
    cache.token_to_kv_pool_allocator = type(allocator_name, (NS,), {})(
        **vars(cache.token_to_kv_pool_allocator)
    )
    root = _node(0)
    leaf = _node(5, root, device=[1], host=[2])
    cache.tree_core = UnifiedTreeCore(node_by_id={0: root, 5: leaf}.__getitem__)

    first = observe_unified_node_closure(cache, 5)
    assert first.observable
    assert first.nodes[0].full_device_tokens == 1
    leaf.component_data[0].value = None
    leaf.load_back_pending_id = 7
    second = observe_unified_node_closure(cache, 5)
    assert second.observable
    assert second.nodes[0].full_device_tokens == 0
    assert second.nodes[0].pending_load_id == 7

    cache.host_pool_group.entry_map["kv"].device_pool = object()
    rejected = observe_unified_node_closure(cache, 5)
    assert not rejected.observable
    assert rejected.nodes == ()
    assert "ownership differs" in rejected.reason


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


@pytest.mark.parametrize("depth", (65, 300))
def test_node_closure_observes_complete_valid_deep_prefix(depth) -> None:
    cache = _static_cache()
    nodes = {0: _node(0)}
    for node_id in range(1, depth + 1):
        nodes[node_id] = _node(node_id, nodes[node_id - 1], host=[node_id])
    cache.tree_core = UnifiedTreeCore(node_by_id=nodes.__getitem__)
    result = observe_unified_node_closure(cache, depth)
    assert result.observable
    assert [node.node_id for node in result.nodes] == list(range(depth, -1, -1))
    assert not observe_unified_node_closure(cache, depth, max_nodes=64).observable
    nodes[1].parent = nodes[depth]
    assert not observe_unified_node_closure(cache, depth).observable


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


@pytest.mark.parametrize("component", [None, 0, 2])
@pytest.mark.parametrize("recognized", [False, True])
def test_node_closure_validates_changed_ancestor_types(component, recognized) -> None:
    cache = _cache()
    ancestor = _node(4, _node(0), device=[1])
    leaf = _node(5, ancestor, device=[2])
    original = ancestor if component is None else ancestor.component_data[component]
    name = "UnifiedTreeNode" if component is None else "ComponentData"
    changed_type = type(name, (NS,), {}) if recognized else NS
    changed = changed_type(**vars(original))
    if component is None:
        leaf.parent = changed
    else:
        ancestor.component_data[component] = changed
    cache.tree_core = UnifiedTreeCore(node_by_id=lambda _: leaf)
    observed = observe_unified_node_closure(cache, 5)
    assert observed.observable is recognized
    if recognized:
        assert [item.node_id for item in observed.nodes] == [5, 4, 0]
        assert observed.nodes[1].full_device_tokens == 1
    else:
        assert observed.nodes == ()


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
    [(-1, None), (True, None), (0, set()), (0, {"", "valid"}), (0, ["valid"])],
)
def test_node_closure_rejects_unknown_session_reference(session_ref, session_ids) -> None:
    cache = _cache()
    node = _node(5)
    node.component_data[0].session_ref = session_ref
    node.component_data[0].session_ids = session_ids
    cache.tree_core = UnifiedTreeCore(node_by_id=lambda node_id: node)
    assert not observe_unified_node_closure(cache, 5).observable


def test_node_closure_accepts_leaf_shared_by_more_than_64_sessions() -> None:
    cache = _static_cache()
    node = _node(5)
    node.component_data[0].session_ref = 156
    node.component_data[0].session_ids = {f"session-{i}" for i in range(156)}
    cache.tree_core = UnifiedTreeCore(node_by_id=lambda _: node)
    result = observe_unified_node_closure(cache, 5)
    assert result.observable
    assert result.nodes[0].full_session_leaf_count == 156


def test_node_closure_fails_closed_on_unimplemented_tree_lookup() -> None:
    cache = _cache()

    def unavailable(_node_id):
        raise NotImplementedError("node_by_id: not yet ported")

    cache.tree_core = UnifiedTreeCore(node_by_id=unavailable)
    assert not observe_unified_node_closure(cache, 5).observable


def test_node_closure_reads_live_tensor_index_shapes_without_tensor_len(monkeypatch) -> None:
    import torch

    cache = _static_cache()
    node = _node(5, device=(1, 2, 3), host=(1, 2))
    node.component_data[0].value = torch.arange(3, dtype=torch.int64)
    node.component_data[0].host_value = torch.arange(2, dtype=torch.int64)
    cache.tree_core = UnifiedTreeCore(node_by_id=lambda _: node)

    def forbidden_len(_):
        raise AssertionError("native index shape should not use Tensor.__len__")

    monkeypatch.setattr(torch.Tensor, "__len__", forbidden_len)
    first = observe_unified_node_closure(cache, 5)
    assert first.observable
    assert first.nodes[0].full_device_tokens == 3
    assert first.nodes[0].full_host_tokens == 2
    node.component_data[0].value.resize_(4)
    second = observe_unified_node_closure(cache, 5)
    assert second.observable
    assert second.nodes[0].full_device_tokens == 4


def test_node_closure_keeps_bounds_for_tensor_index_shapes() -> None:
    import torch

    cache = _static_cache()
    node = _node(5)
    node.component_data[0].host_value = torch.empty(1_000_000, dtype=torch.int64)
    cache.tree_core = UnifiedTreeCore(node_by_id=lambda _: node)
    observed = observe_unified_node_closure(cache, 5)
    assert not observed.observable
    assert "FULL host length" in observed.reason
