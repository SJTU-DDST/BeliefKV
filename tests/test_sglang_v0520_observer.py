"""CPU-only stand-ins for the inspected v0.5.20 UnifiedMamba pool wiring."""

from __future__ import annotations

from enum import IntEnum
from types import SimpleNamespace as NS

import pytest

from beliefkv.runtime.sglang_v0520_observer import observe_unified_full_mamba


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
        available_size=_never,
    )
    host_mamba = MambaPoolHost(
        size=21, page_size=1, device_pool=mamba_device, available_size=_never
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
    assert len(result.missing_metrics) == 5


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
