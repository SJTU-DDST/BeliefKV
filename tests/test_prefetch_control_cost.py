from dataclasses import replace
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from beliefkv.core.events import RuntimeEventKind
from beliefkv.runtime.sglang_v0520_physical import PhysicalReceiptError, PhysicalTransactionLedger
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_sglang_v0520_physical import ack, child, expected, receipt
from tests.test_sglang_v0520_runtime import event
from tests.test_wait_prefetch_batch import batch_fixture
from scripts.benchmark_prefetch_control_cpu import join_scan_fixture


def assert_pending_indexes(ledger):
    pending = tuple(value[0] for value in ledger._pending.values())
    assert set(ledger.pending_h2d_contexts) == {
        (item.context_id, item.session_id, item.session_generation)
        for item in pending if item.action == "PREFETCH_GPU" and item.session_id is not None
    }
    for action in ("PREPARE_HOST", "PREFETCH_GPU"):
        assert ledger.pending_action_bytes(action) == sum(
            part.num_bytes for item in pending if item.action == action for part in item.children
        )


@pytest.mark.parametrize("finish", ["cancel", "expire", "invalid_ack", "ack"])
def test_physical_budget_indexes_follow_partial_ack_invalidation_and_retirement(finish):
    ledger = PhysicalTransactionLedger()
    commands = [
        replace(
            expected("load", action="PREFETCH_GPU", children=(
                child(11, (11,), 10, 0, 10), child(12, (12,), 10, 0, 10),
            )),
            session_id="session", session_generation=1,
        ),
        replace(
            expected("later-epoch", action="PREFETCH_GPU", epoch=4, children=(
                child(13, (13,), 20, 0, 20),
            )),
            session_id="session", session_generation=1,
        ),
        expected("prepare", children=(child(14, (14,), 30, 0, 30),)),
    ]
    with patch("beliefkv.runtime.sglang_v0520_physical.monotonic", return_value=1.):
        for command in commands:
            ledger.register(command)
        assert_pending_indexes(ledger)
        assert len(ledger.pending_h2d_contexts) == 1
        assert ledger.pending_action_bytes("PREFETCH_GPU") == 40
        if finish == "cancel":
            ledger.cancel_unsubmitted("load")
        else:
            assert ledger.observe(ack(
                receipt("load", published=(11,), kv=1, mamba=0, total=10),
                direction="h2d", nodes=(11,), kv=1, mamba=0,
            ), live_context_epochs={"ctx": 3}, live_context_sessions={"ctx": ("session", 1)}) == ()
            assert ledger.pending_action_bytes("PREFETCH_GPU") == 40
            assert_pending_indexes(ledger)
            if finish == "invalid_ack":
                with pytest.raises(PhysicalReceiptError):
                    ledger.observe(ack(receipt("unknown")), live_context_epochs={"ctx": 3})
            elif finish == "ack":
                ledger.observe(ack(
                    receipt("load", anchor=12, published=(12,), kv=1, mamba=0, total=10),
                    direction="h2d", nodes=(12,), kv=1, mamba=0,
                ), live_context_epochs={"ctx": 3}, live_context_sessions={"ctx": ("session", 1)})
            else:
                ledger.invalidate_context("ctx")
                assert_pending_indexes(ledger)
                with patch("beliefkv.runtime.sglang_v0520_physical.monotonic", return_value=32.):
                    assert len(ledger.expire()) == 3
        assert_pending_indexes(ledger)
        for command in tuple(ledger._pending):
            ledger.cancel_unsubmitted(command)
            assert_pending_indexes(ledger)
        assert not ledger.pending_h2d_contexts
        assert ledger.pending_action_bytes("unknown") == 0


def test_restore_budget_queries_do_not_traverse_registered_commands():
    ledger = PhysicalTransactionLedger()
    ledger.register(replace(
        expected(action="PREFETCH_GPU"), session_id="s", session_generation=1,
    ))

    class NoTraversal(dict):
        def values(self):
            raise AssertionError("repeated budget queries must not scan commands")

        def items(self):
            raise AssertionError("repeated budget queries must not scan commands")

    with patch.object(ledger, "_pending", NoTraversal(ledger._pending)):
        assert ledger.pending_action_bytes("PREFETCH_GPU") == 32
        assert ("ctx", "s", 1) in ledger.pending_h2d_contexts
        assert len(ledger.pending_h2d_contexts) == 1


def test_disabled_profiling_avoids_scope_construction_but_keeps_final_runtime_evidence(
    tmp_path, monkeypatch,
):
    monkeypatch.delenv("BELIEFKV_PROFILE_SHARED_PATH", raising=False)
    runtime = NativeAdmissionRuntime(opportunity_dir=str(tmp_path))
    runtime.event_server = NS(drain=Mock(), close=Mock())
    try:
        assert not runtime._hotpath_timing.enabled
        with patch.object(runtime._hotpath_timing, "measure", side_effect=AssertionError(
            "disabled profiling must not allocate context managers",
        )):
            runtime.on_events((event(0, RuntimeEventKind.WORKFLOW_START),))
            runtime.scheduler_step()
    finally:
        runtime.close()
    rows = [json.loads(line) for line in
            (tmp_path / "admission_opportunities.jsonl").read_text().splitlines()]
    final = next(row for row in rows if row.get("final"))
    assert final["profile_shared_path"] is False
    assert final["shared_path_timing"]["phases"] == {}
    assert final["physical_disabled"] is False


def test_no_predictive_candidate_skips_dispatch_without_touching_capacity_or_clock():
    runtime = NativeAdmissionRuntime()
    runtime.enable_final_stage_prefetch = runtime.enable_tool_prefetch = True
    runtime.enable_prepare_host = False
    runtime.dispatch_join_prefetch = Mock(side_effect=AssertionError("no JOIN candidate"))
    runtime.dispatch_tool_prefetch = Mock(side_effect=AssertionError("no tool candidate"))
    try:
        with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
                   side_effect=AssertionError("no periodic physical work")):
            runtime.dispatch_after_forward()
        assert not runtime.physical_disabled
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()


@pytest.mark.parametrize("sampling,expected_records", [(False, 1), (True, 2)])
def test_unchanged_plan_only_has_a_periodic_heartbeat_with_diagnostic_sampling(
    sampling, expected_records,
):
    runtime, key, opportunity, _, _ = batch_fixture("tool_wait")
    runtime.enable_h2d_opportunity_sampling = sampling
    rows = []
    runtime._opportunity_writer = NS(record=rows.append, close=lambda: None)
    try:
        for now in (1., 1.1, 2.):
            with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=now):
                runtime._record_wait_prefetch_plan(
                    "tool_wait", key, opportunity, reason="no_cold_capacity",
                )
        assert len(rows) == expected_records
        runtime._record_wait_prefetch_plan(
            "tool_wait", key, replace(opportunity, required_full_tokens=4),
            reason="no_cold_capacity",
        )
        assert len(rows) == expected_records + 1
        assert rows[-1]["planned_full_tokens"] == 4
    finally:
        runtime.close()


def test_many_live_join_stages_only_read_the_running_batch_once_per_scan():
    runtime, reads = join_scan_fixture(NativeAdmissionRuntime, workflows=156)
    try:
        assert runtime._roll_final_stage() is None
        assert reads[0] == 48
        assert runtime.counts["semantic_h2d_child_waiting_for_service"] == 108
        assert runtime._join_ticket is None
        assert not runtime.physical_ledger.pending_count
        runtime._native_running_batch.reqs.clear()
        assert runtime._roll_final_stage() is None
        assert runtime.counts["semantic_h2d_child_waiting_for_service"] == 264
    finally:
        runtime.close()


@pytest.mark.parametrize("sampling", (False, True))
def test_capacity_reclaim_diagnostics_follow_sampling_without_changing_requested_capacity(sampling):
    runtime, key, opportunity, cache, _ = batch_fixture("tool_wait")
    runtime.enable_h2d_opportunity_sampling = sampling
    runtime._opportunity_writer = NS(record=lambda row: None, close=lambda: None)
    cache.reclaim_beliefkv_handoff_capacity = Mock(return_value={})
    opportunity = replace(opportunity, fits_current_free_lists=False)
    try:
        with patch.object(runtime, "_publish_parent_pressure_candidates"), \
             patch.object(runtime, "_wait_prefetch_opportunity", return_value=opportunity):
            runtime._reclaim_wait_prefetch_capacity(key, opportunity, source="tool_wait")
        args = cache.reclaim_beliefkv_handoff_capacity.call_args.kwargs
        assert args.get("collect_diagnostics", False) is sampling
        assert args["full_tokens"] == 16
        assert args["mamba_slots"] == 1
        assert args["exclude_node_ids"] == (0, 1, 2, 3, 4)
    finally:
        runtime.close()
