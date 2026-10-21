from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from beliefkv.control.causal_graph import InvocationState as State
from beliefkv.runtime.native_transfer_policy import PrefetchResidencyBudget
from tests.test_execution_handoff import runtime_with_requests


def fixture(monkeypatch):
    monkeypatch.setenv("BELIEFKV_ENABLE_TOOL_PREFETCH", "1")
    monkeypatch.setenv("BELIEFKV_TOOL_RESTORE_MODE", "execution")
    monkeypatch.setenv("BELIEFKV_ENABLE_EXECUTION_HANDOFF", "0")
    runtime, requests, cache = runtime_with_requests(monkeypatch)
    runtime.enable_execution_handoff = False
    cache.prefetch_gpu_session_nodes = Mock()
    cache.session_refs = NS(snapshot_latest_session_leaf_anchors=Mock(return_value=((0, ((1, 1.),)),)))
    runtime._observe_native_admission_capacity = Mock()
    runtime._current_residency_budget = Mock(return_value=PrefetchResidencyBudget(1, 1024**3))
    runtime.plan_native_prefill = Mock(return_value=NS(prioritized=(runtime.visible[requests[0].rid],)))
    return runtime, requests, cache


def test_execution_mode_removes_tool_forecast_restore_but_keeps_prepare(monkeypatch):
    runtime, _, _ = fixture(monkeypatch)
    try:
        assert runtime.enable_frontier_restore and not runtime.enable_tool_prefetch
        assert runtime.enable_prepare_host
        runtime._wait_prefetch_opportunity = Mock(side_effect=AssertionError("no speculative planning"))
        runtime.dispatch_tool_prefetch()
        assert runtime._tool_ticket is None
    finally:
        runtime.close()


def test_first_context_request_skips_duplicate_long_input_matching(monkeypatch):
    runtime, requests, cache = fixture(monkeypatch)
    cache.session_refs.snapshot_latest_session_leaf_anchors.return_value = None
    try:
        runtime.dispatch_frontier_restore(requests, running_batch=None)
        cache.inspect_beliefkv_reentry.assert_not_called()
        assert runtime._execution_handoff is None
    finally:
        runtime.close()


def test_eos_arriving_during_selection_revokes_frontier_enqueue_authority(monkeypatch):
    from beliefkv.runtime.sglang_v0520_runtime import _ExecutionHandoffTicket
    import time

    runtime, requests, _ = fixture(monkeypatch)
    key = runtime.visible[requests[0].rid]
    ticket = _ExecutionHandoffTicket(key, requests[0], time.monotonic() + 2.)
    try:
        assert runtime._live_execution_handoff(ticket)
        runtime._eos_prefetch_keys["child"] = key
        assert not runtime._live_execution_handoff(ticket)
        runtime._eos_prefetch_keys.clear()
        runtime._confirmed_join_tickets["join"] = NS(key=key)
        assert not runtime._live_execution_handoff(ticket)
        runtime._confirmed_join_tickets.clear()
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()


def test_frontier_issues_once_without_persistent_ticket_or_foreground_reclaim(monkeypatch):
    runtime, requests, cache = fixture(monkeypatch)
    key = runtime.visible[requests[0].rid]
    cache.reclaim_beliefkv_handoff_capacity = Mock(side_effect=AssertionError("no demand reclaim"))
    runtime._issue_handoff_prefetch_steps = Mock(side_effect=lambda ticket, steps: (
        ("p1",) if runtime._live_execution_handoff(ticket) else ()
    ))
    opportunity = NS(steps=("first", "second"), fits_current_free_lists=True)
    try:
        with patch(
            "beliefkv.runtime.sglang_v0520_runtime.inspect_session_h2d_opportunity",
            return_value=opportunity,
        ):
            runtime.dispatch_frontier_restore(requests, running_batch=None)
            runtime._execution_handoff_next_ms = 0.
            runtime.dispatch_frontier_restore(requests, running_batch=None)
        runtime._issue_handoff_prefetch_steps.assert_called_once()
        assert runtime._execution_handoff is None
        assert runtime._frontier_probe_after_ms[key] == float("inf")
        assert runtime.counts["frontier_restore_beneficiaries"] == 1
    finally:
        runtime.close()


@pytest.mark.parametrize("initial", ("resident", "missing_anchors", "unavailable_observation"))
def test_temporary_negative_probe_can_become_a_restore_opportunity_without_a_new_request(monkeypatch, initial):
    runtime, requests, cache = fixture(monkeypatch)
    key = runtime.visible[requests[0].rid]
    inspect = cache.inspect_beliefkv_reentry.side_effect
    snapshot = cache.session_refs.snapshot_latest_session_leaf_anchors.return_value
    if initial == "resident":
        cache.inspect_beliefkv_reentry.side_effect = lambda _: {
            "missing_full_tokens": 0, "missing_mamba_slots": 0,
        }
    elif initial == "missing_anchors":
        cache.session_refs.snapshot_latest_session_leaf_anchors.return_value = None
    else:
        cache.inspect_beliefkv_reentry.side_effect = lambda _: None
    runtime._issue_handoff_prefetch_steps = Mock(return_value=("p1",))
    try:
        with patch(
            "beliefkv.runtime.sglang_v0520_runtime.inspect_session_h2d_opportunity",
            return_value=NS(steps=("first",), fits_current_free_lists=True),
        ):
            with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=10.):
                runtime.dispatch_frontier_restore(requests[:1], running_batch=None)
            assert runtime._frontier_probe_after_ms[key] == 10200.
            runtime._issue_handoff_prefetch_steps.assert_not_called()
            cache.inspect_beliefkv_reentry.side_effect = inspect
            cache.session_refs.snapshot_latest_session_leaf_anchors.return_value = snapshot
            with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=10.051):
                runtime.dispatch_frontier_restore(requests[:1], running_batch=None)
            runtime._issue_handoff_prefetch_steps.assert_not_called()
            assert runtime.plan_native_prefill.call_count == 1
            with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=10.2):
                runtime.dispatch_frontier_restore(requests[:1], running_batch=None)
        runtime._issue_handoff_prefetch_steps.assert_called_once()
        assert runtime.plan_native_prefill.call_count == 2
        assert runtime._execution_handoff is None
        assert runtime._frontier_probe_after_ms[key] == float("inf")
        assert runtime.counts["frontier_restore_backoff_skipped_plans"] == 1
    finally:
        runtime.close()


def test_a_due_candidate_prevents_a_whole_queue_backoff_skip(monkeypatch):
    runtime, requests, cache = fixture(monkeypatch)
    keys = tuple(runtime.visible[req.rid] for req in requests)
    runtime._frontier_probe_after_ms[keys[0]] = float("inf")
    runtime.plan_native_prefill.return_value = NS(prioritized=(keys[2],))
    cache.session_refs.snapshot_latest_session_leaf_anchors.return_value = None
    try:
        runtime.dispatch_frontier_restore(requests, running_batch=None)
        runtime.plan_native_prefill.assert_called_once()
        assert keys[2] in runtime._frontier_probe_after_ms
    finally:
        runtime.close()


def test_a_full_running_batch_rejects_before_reading_a_backed_off_queue(monkeypatch):
    runtime, requests, _ = fixture(monkeypatch)
    runtime._frontier_probe_after_ms[runtime.visible[requests[0].rid]] = float("inf")
    runtime._current_residency_budget.return_value = PrefetchResidencyBudget(0, 0)

    class UnreadQueue:
        def __len__(self):
            return 1

        def __iter__(self):
            raise AssertionError("no candidate scan without an admission slot")

    try:
        runtime.dispatch_frontier_restore(UnreadQueue(), running_batch=None)
        runtime.plan_native_prefill.assert_not_called()
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()


def test_periodic_resident_checks_use_live_metadata_without_repeating_input_validation(monkeypatch):
    from tests.test_sglang_v0520_physical import prepare_prefix_fixture

    runtime, requests, cache = fixture(monkeypatch)
    native, anchors, nodes = prepare_prefix_fixture(backed=True)
    cache.tree_core = native.tree_core
    cache.session_refs.snapshot_latest_session_leaf_anchors.return_value = anchors.component_leaves
    runtime._issue_handoff_prefetch_steps = Mock(return_value=("p1",))
    try:
        with patch(
            "beliefkv.runtime.sglang_v0520_runtime.inspect_session_h2d_opportunity",
            return_value=NS(steps=("first",), fits_current_free_lists=True),
        ):
            for when in (10., 10.25, 10.5):
                with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=when):
                    runtime.dispatch_frontier_restore(requests[:1], running_batch=None)
            cache.inspect_beliefkv_reentry.assert_not_called()
            assert runtime.counts["frontier_restore_empty_metadata_checks"] == 3
            nodes[1].component_data[0].value = None
            with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=10.75):
                runtime.dispatch_frontier_restore(requests[:1], running_batch=None)
        cache.inspect_beliefkv_reentry.assert_called_once()
        runtime._issue_handoff_prefetch_steps.assert_called_once()
        assert runtime._execution_handoff is None
    finally:
        runtime.close()


@pytest.mark.parametrize("change", ("attempt", "session", "unknown_metadata"))
def test_changed_or_unknown_request_identity_cannot_use_a_cached_negative_shortcut(monkeypatch, change):
    runtime, requests, _ = fixture(monkeypatch)
    request = requests[0]
    key = runtime.visible[request.rid]
    runtime._frontier_probe_after_ms[key] = float("inf")
    if change == "attempt":
        request.cache_request_handle = NS(attempt_id=key.attempt_id + 1)
    elif change == "session":
        request.session_generation += 1
    else:
        request.beliefkv_metadata = {}
    runtime._issue_handoff_prefetch_steps = Mock()
    try:
        runtime.dispatch_frontier_restore([request], running_batch=None)
        runtime.plan_native_prefill.assert_called_once()
        runtime._issue_handoff_prefetch_steps.assert_not_called()
    finally:
        runtime.close()


@pytest.mark.parametrize("reason", ("no_slot", "eos", "critical", "no_capacity", "no_data", "waiting_tool"))
def test_frontier_yields_to_join_and_native_without_creating_a_wait_ticket(monkeypatch, reason):
    runtime, requests, cache = fixture(monkeypatch)
    runtime._issue_handoff_prefetch_steps = Mock()
    if reason == "no_slot":
        runtime._current_residency_budget.return_value = PrefetchResidencyBudget(0, 0)
    elif reason == "eos":
        runtime._eos_prefetch_keys["eos"] = runtime.visible[requests[0].rid]
    elif reason == "critical":
        runtime._critical_parent_demand = ("critical", 1, 1, 1.)
    elif reason == "waiting_tool":
        runtime.graph.invocations["cold"].state = State.WAIT_TOOL
    elif reason == "no_data":
        cache.inspect_beliefkv_reentry.side_effect = lambda req: {"missing_full_tokens": 0, "missing_mamba_slots": 0}
    try:
        with patch(
            "beliefkv.runtime.sglang_v0520_runtime.inspect_session_h2d_opportunity",
            return_value=NS(steps=("first",), fits_current_free_lists=False),
        ):
            runtime.dispatch_frontier_restore(requests, running_batch=None)
        runtime._issue_handoff_prefetch_steps.assert_not_called()
        assert runtime._execution_handoff is None
        assert not runtime.defer_prefill_for_prefetch(requests[0])
    finally:
        runtime.close()


@pytest.mark.parametrize("states", (
    (State.WAIT_JOIN,), (State.WAIT_TOOL,), (State.WAIT_CHILD,),
    (State.WAIT_TOOL, State.RUNNING_LLM), (State.DONE,),
))
def test_backup_selection_uses_current_blocking_state_without_a_duration_model(monkeypatch, states):
    runtime, requests, _ = fixture(monkeypatch)
    try:
        key = runtime.visible[requests[0].rid]
        inv = runtime.graph.invocations["cold"]
        runtime._long_tool_wait = Mock(side_effect=AssertionError("no forecast per node"))
        for state in states:
            inv.state = state
            assert runtime._live_backup_full_owners(((key.session_id, key.session_generation),)) is (
                state in (State.WAIT_JOIN, State.WAIT_TOOL, State.WAIT_CHILD)
            )
        assert runtime.counts["cold_full_owner_index_built"] == 1
        assert not runtime._live_backup_full_owners(((key.session_id, key.session_generation + 1),))
    finally:
        runtime.close()
