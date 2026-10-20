from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime


def test_no_prefetch_dispatch_keeps_both_prepare_sources(monkeypatch):
    monkeypatch.setenv("BELIEFKV_ENABLE_PREPARE_HOST", "1")
    runtime = NativeAdmissionRuntime()
    try:
        runtime.dispatch_join_prefetch = Mock()
        runtime.dispatch_tool_prefetch = Mock()
        runtime.dispatch_join_prepare = Mock()
        runtime.dispatch_tool_prepare = Mock()
        queue, running = [], NS(reqs=[])
        runtime.dispatch_after_forward(queue, running_batch=running)
        runtime.dispatch_join_prefetch.assert_not_called()
        runtime.dispatch_tool_prefetch.assert_not_called()
        runtime.dispatch_join_prepare.assert_called_once_with(
            queue, running_batch=running, _defer_pressure_publication=True,
        )
        runtime.dispatch_tool_prepare.assert_called_once_with(
            queue, running_batch=running, _defer_pressure_publication=True,
        )
        assert runtime.enable_prepare_host and runtime.enable_resident_first
        assert runtime._semantic_worker is None
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()


@pytest.mark.parametrize("ticket_field", (
    "_join_ticket", "_confirmed_join_tickets", "_tool_ticket", "_tool_inflight_tickets",
))
def test_pending_transfer_bookkeeping_is_not_skipped(ticket_field):
    runtime = NativeAdmissionRuntime()
    try:
        runtime.dispatch_join_prefetch = Mock()
        runtime.dispatch_tool_prefetch = Mock()
        runtime.dispatch_join_prepare = Mock()
        runtime.dispatch_tool_prepare = Mock()
        setattr(runtime, ticket_field, {"pending": object()} if ticket_field.endswith("tickets") else object())
        runtime.dispatch_after_forward()
        if ticket_field in ("_tool_ticket", "_tool_inflight_tickets"):
            runtime.dispatch_tool_prefetch.assert_called_once()
        else:
            runtime.dispatch_join_prefetch.assert_called_once()
    finally:
        runtime._join_ticket = runtime._tool_ticket = None
        runtime._confirmed_join_tickets.clear()
        runtime.close()


def test_no_opportunity_scan_keeps_runtime_and_terminal_observations(monkeypatch):
    monkeypatch.setenv("BELIEFKV_H2D_OPPORTUNITY_SAMPLING", "0")
    runtime = NativeAdmissionRuntime()
    writer = Mock()
    runtime._opportunity_writer = writer
    runtime._terminal_cache_diagnostics = True
    runtime._sample_h2d_opportunities = Mock()
    runtime._sample_terminal_cache = Mock()
    try:
        runtime.sample_runtime_observations(now_ms=1000.)
        runtime._sample_h2d_opportunities.assert_not_called()
        runtime._sample_terminal_cache.assert_called_once_with(now_ms=1000.)
        state = writer.record.call_args.args[0]
        assert state["event"] == "admission_runtime_state"
        assert state["monotonic_ms"] > 0
        assert state["clock_sample_span_ms"] >= 0
        assert state["h2d_opportunity_sampling"] is False
        assert state["prepare_host"] is True
        assert not state["semantic_worker_configured"]
    finally:
        runtime._opportunity_writer = None
        runtime.close()
