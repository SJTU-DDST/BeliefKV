import time
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from beliefkv.runtime.sglang_v0520_runtime import (
    NativeAdmissionRuntime, _ExecutionHandoffTicket,
)
from tests.test_execution_frontier_restore import fixture
from tests.test_execution_handoff import runtime_with_requests
from tests.test_sglang_v0520_runtime import select


def test_ordinary_frontier_and_predicted_tool_prefetch_can_coexist(monkeypatch):
    monkeypatch.setenv("BELIEFKV_ENABLE_EXECUTION_HANDOFF", "0")
    monkeypatch.setenv("BELIEFKV_ENABLE_FRONTIER_RESTORE", "1")
    monkeypatch.setenv("BELIEFKV_ENABLE_TOOL_PREFETCH", "1")
    monkeypatch.setenv("BELIEFKV_TOOL_RESTORE_MODE", "speculative")
    runtime = NativeAdmissionRuntime()
    try:
        assert runtime.enable_frontier_restore
        assert runtime.enable_tool_prefetch
        assert not runtime.enable_execution_handoff
    finally:
        runtime.close()


def test_selected_consumer_keeps_one_near_term_slot_across_residency_reordering(monkeypatch):
    runtime, (cold, warm, _parent), _cache = runtime_with_requests(monkeypatch)
    runtime.enable_frontier_restore = True
    runtime.enable_execution_handoff = False
    runtime._prefetch_priority_normal_admissions = 0
    key = runtime.visible[cold.rid]
    ticket = _ExecutionHandoffTicket(key, cold, time.monotonic() + 1.5)
    try:
        assert select(runtime, [cold, warm]).candidates[0] is warm
        runtime._frontier_restore_consumer = ticket
        assert select(runtime, [cold, warm]).candidates[0] is cold
        runtime.on_batch_selected(NS(reqs=[warm]))
        assert runtime._frontier_restore_consumer is ticket
        runtime.on_batch_selected(NS(reqs=[cold]))
        assert runtime._frontier_restore_consumer is None
        assert runtime._prefetch_priority_normal_admissions == 0
        assert runtime.counts["frontier_restore_batch_selected"] == 1
    finally:
        runtime.close()


def test_selected_consumer_cannot_cross_a_higher_causal_class(monkeypatch):
    runtime, (cold, warm, parent), _cache = runtime_with_requests(monkeypatch)
    runtime.enable_frontier_restore = True
    runtime.enable_execution_handoff = False
    runtime.graph.invocations[parent.rid].pending_messages = 1
    runtime._frontier_restore_consumer = _ExecutionHandoffTicket(
        runtime.visible[cold.rid], cold, time.monotonic() + 1.5,
    )
    try:
        assert select(runtime, [cold, warm, parent]).candidates[0] is parent
    finally:
        runtime.close()


@pytest.mark.parametrize("reason", ["expired", "eos", "identity_changed"])
def test_selected_consumer_releases_priority_when_its_authority_expires(monkeypatch, reason):
    runtime, (cold, warm, _parent), _cache = runtime_with_requests(monkeypatch)
    runtime.enable_frontier_restore = True
    runtime.enable_execution_handoff = False
    key = runtime.visible[cold.rid]
    runtime._frontier_restore_consumer = _ExecutionHandoffTicket(
        key, cold, time.monotonic() + (-1 if reason == "expired" else 1.5),
    )
    if reason == "eos":
        runtime._eos_prefetch_keys["observed-child"] = key
    elif reason == "identity_changed":
        runtime.visible.pop(cold.rid)
    try:
        assert select(runtime, [cold, warm]).candidates[0] is warm
        assert runtime._frontier_restore_consumer is None
        assert runtime.counts["frontier_restore_selection_released"] == 1
    finally:
        runtime.close()


def test_selected_restore_does_not_plan_another_consumer_before_use(monkeypatch):
    runtime, requests, _cache = fixture(monkeypatch)
    runtime._issue_handoff_prefetch_steps = lambda _ticket, _steps: ("selected-copy",)
    opportunity = NS(steps=("prefix",), fits_current_free_lists=True)
    try:
        with patch(
            "beliefkv.runtime.sglang_v0520_runtime.inspect_session_h2d_opportunity",
            return_value=opportunity,
        ):
            runtime.dispatch_frontier_restore(requests, running_batch=None)
            ticket = runtime._frontier_restore_consumer
            assert ticket is not None
            planned = runtime.plan_native_prefill.call_count
            runtime._execution_handoff_next_ms = 0.
            runtime.dispatch_frontier_restore(requests, running_batch=None)
        assert runtime._frontier_restore_consumer is ticket
        assert runtime.plan_native_prefill.call_count == planned
        assert runtime.counts["frontier_restore_selection_pending"] == 1
        runtime.on_batch_selected(NS(reqs=[ticket.request]))
        runtime.dispatch_frontier_restore(requests, running_batch=None)
        assert runtime.plan_native_prefill.call_count == planned
    finally:
        runtime.close()
