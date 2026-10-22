"""Reuse only a current, call-local native budget during tool selection."""

from dataclasses import replace
from unittest.mock import patch

import pytest

from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.benchmark_wait_prefetch_dispatch_cpu import dispatch_fixture


@pytest.mark.parametrize("depth", (4, 16, 64))
def test_tool_selection_reads_an_unchanged_native_budget_once(depth):
    runtime, key, _, _, actions, _, _, _, now = dispatch_fixture(
        NativeAdmissionRuntime, source="tool_wait", depth=depth, native_budget=True,
    )
    try:
        with patch("time.monotonic", lambda: now), patch.object(
            runtime, "_current_residency_budget", wraps=runtime._current_residency_budget,
        ) as budget:
            opportunity = runtime._roll_tool_prefetch()
        assert budget.call_count == 1
        assert runtime._tool_ticket.key == key
        assert opportunity.steps
        assert opportunity.capacity_budget is None
        assert not actions
        assert not runtime.physical_ledger.pending_count
        assert not runtime.physical_disabled
    finally:
        runtime.close()


def test_changed_headroom_requires_a_new_tool_selection_budget():
    runtime, key, cache, _, actions, _, _, _, now = dispatch_fixture(
        NativeAdmissionRuntime, source="tool_wait", depth=4, native_budget=True,
    )
    inspect = runtime.inspect_context_h2d_opportunity
    old_headroom = []

    def changed(**kwargs):
        old_headroom.append(runtime._residency_headroom)
        cache.token_to_kv_pool_allocator.available_size = lambda: 1_000_000
        return inspect(**kwargs)

    try:
        with patch("time.monotonic", lambda: now), patch.object(
            runtime, "inspect_context_h2d_opportunity", side_effect=changed,
        ), patch.object(
            runtime, "_current_residency_budget", wraps=runtime._current_residency_budget,
        ) as budget:
            opportunity = runtime._roll_tool_prefetch()
        assert budget.call_count == 2
        assert old_headroom[0].device_full_free_tokens == 2_000_000
        assert opportunity.headroom == replace(
            old_headroom[0], device_full_free_tokens=1_000_000,
        )
        assert runtime._residency_headroom == opportunity.headroom
        assert runtime._tool_ticket.key == key
        assert not actions
    finally:
        runtime.close()


def test_tool_selection_does_not_share_a_budget_with_a_later_call():
    runtime, key, cache, _, actions, _, _, _, now = dispatch_fixture(
        NativeAdmissionRuntime, source="tool_wait", depth=4, native_budget=True,
    )
    try:
        with patch("time.monotonic", lambda: now), patch.object(
            runtime, "_current_residency_budget", wraps=runtime._current_residency_budget,
        ) as budget:
            assert runtime._roll_tool_prefetch() is not None
        assert budget.call_count == 1
        runtime._tool_ticket = None
        runtime._tool_opportunity_cache.clear()
        runtime._tool_prefetch_next_ms = 0.
        cache.token_to_kv_pool_allocator.available_size = lambda: 1_000_000
        with patch("time.monotonic", lambda: now), patch.object(
            runtime, "_current_residency_budget", wraps=runtime._current_residency_budget,
        ) as budget:
            opportunity = runtime._roll_tool_prefetch()
        assert budget.call_count == 1
        assert opportunity.headroom.device_full_free_tokens == 1_000_000
        assert runtime._tool_ticket.key == key
        assert not actions
    finally:
        runtime.close()
