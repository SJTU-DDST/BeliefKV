"""Actual cold-KV demotion invalidates the donor's earlier empty restore plan."""

from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from beliefkv.core.events import RuntimeEventKind
from beliefkv.runtime.native_transfer_policy import TransferStartWindow
from tests.test_tool_predictive_transfers import event
from tests.test_wait_prefetch_batch import batch_fixture


def wakeup_fixture():
    runtime, key, positive, cache, _ = batch_fixture("tool_wait", node_count=1)
    hint = runtime.tool_wait_hints[key.context_id]
    runtime.tool_wait_hints[key.context_id] = replace(
        hint, wait_p10_ms=10., wait_p50_ms=60., wait_p90_ms=120.,
    )
    nodes = positive.candidate.nodes
    candidate = replace(
        positive.candidate,
        nodes=(*nodes[:-1], NS(**{**vars(nodes[-1]), "mamba_device_present": True})),
    )
    step = replace(positive.step, include_mamba=False)
    positive = replace(
        positive, candidate=candidate, step=step, steps=(step,), required_mamba_slots=0,
    )
    empty = replace(
        positive, step=None, steps=(), required_full_tokens=0,
        no_step_reason="no_host_backed_missing_copy",
        host_backed_full_missing_device_tokens=0,
    )
    runtime._parent_pressure_candidates[step.node_id] = (key, step.creation_time)
    return runtime, key, positive, empty, cache, [hint.issued_monotonic_ms / 1000.]


@pytest.mark.parametrize("already_due", (False, True))
def test_actual_demotion_can_prefetch_before_the_old_empty_cache_expires(already_due):
    runtime, key, positive, empty, cache, clock = wakeup_fixture()
    current = [empty]
    try:
        with patch("time.monotonic", new=lambda: clock[0]), patch.object(
            runtime, "inspect_context_h2d_opportunity", side_effect=lambda **_: current[0],
        ) as inspect, patch.object(
            runtime, "_h2d_start_window", return_value=TransferStartWindow(100., 0., 200.),
        ):
            runtime.dispatch_tool_prefetch()
            assert runtime._tool_opportunity_cache[key][1] is empty
            expiry = runtime._tool_opportunity_cache[key][0]
            runtime._semantic_target_cache[key] = (expiry, False)
            cache.prefetch_gpu_session_nodes.assert_not_called()
            current[0] = positive
            clock[0] += .01
            assert clock[0] * 1000. < expiry
            if already_due:
                runtime._tool_prefetch_next_ms = 0.
            runtime._on_parent_pressure_parked(positive.step.node_id, {0: 4})
            assert key not in runtime._tool_opportunity_cache
            assert key not in runtime._semantic_target_cache
            runtime.dispatch_tool_prefetch()
        assert inspect.call_count == 2
        cache.prefetch_gpu_session_nodes.assert_called_once()
        assert runtime.physical_ledger.pending_count == 1
        assert runtime.counts["tool_prefetch_issued"] == 1
        assert runtime.counts["wait_transfer_observation_invalidated"] == 1
        assert not runtime.physical_disabled
    finally:
        runtime.close()


@pytest.mark.parametrize("freed", ({0: 4}, {2: 1}))
def test_pressure_invalidation_is_scoped_to_the_recorded_owner(freed):
    runtime, key, positive, empty, _, clock = wakeup_fixture()
    unrelated = replace(key, context_id="unrelated", session_id="other")
    deadline = clock[0] * 1000. + 100.
    for item in (key, unrelated):
        runtime._tool_opportunity_cache[item] = (deadline, empty)
        runtime._semantic_target_cache[item] = (deadline, False)
    runtime._tool_prefetch_next_ms = deadline
    try:
        runtime._on_parent_pressure_parked(positive.step.node_id, freed)
        assert key not in runtime._tool_opportunity_cache
        assert key not in runtime._semantic_target_cache
        assert runtime._tool_opportunity_cache[unrelated] == (deadline, empty)
        assert runtime._semantic_target_cache[unrelated] == (deadline, False)
        assert runtime._tool_prefetch_next_ms == 0.
    finally:
        runtime.close()


@pytest.mark.parametrize("freed", ({}, {0: 0, 2: 0}))
def test_no_capacity_release_keeps_the_existing_cache_and_deadline(freed):
    runtime, key, positive, empty, _, clock = wakeup_fixture()
    deadline = clock[0] * 1000. + 100.
    runtime._tool_opportunity_cache[key] = (deadline, empty)
    runtime._semantic_target_cache[key] = (deadline, False)
    runtime._tool_prefetch_next_ms = deadline
    try:
        runtime._on_parent_pressure_parked(positive.step.node_id, freed)
        assert runtime._tool_opportunity_cache[key] == (deadline, empty)
        assert runtime._semantic_target_cache[key] == (deadline, False)
        assert runtime._tool_prefetch_next_ms == deadline
        assert runtime.counts["wait_transfer_observation_invalidated"] == 0
    finally:
        runtime.close()


def test_unknown_victim_does_not_invalidate_all_waiting_contexts():
    runtime, key, _, empty, _, clock = wakeup_fixture()
    deadline = clock[0] * 1000. + 100.
    runtime._tool_opportunity_cache[key] = (deadline, empty)
    runtime._tool_prefetch_next_ms = deadline
    try:
        runtime._on_parent_pressure_parked(999, {0: 4})
        assert runtime._tool_opportunity_cache[key] == (deadline, empty)
        assert runtime._tool_prefetch_next_ms == deadline
    finally:
        runtime.close()


def test_demotion_wakeup_still_rejects_a_tool_that_already_returned():
    runtime, key, positive, empty, cache, clock = wakeup_fixture()
    runtime._tool_opportunity_cache[key] = (clock[0] * 1000. + 100., empty)
    try:
        runtime._on_parent_pressure_parked(positive.step.node_id, {0: 4})
        runtime.on_events((event(
            3, RuntimeEventKind.TOOL_END, invocation_id=key.invocation_id,
            attributes={"tool_run_id": "long"},
        ),))
        with patch.object(runtime, "inspect_context_h2d_opportunity", return_value=positive):
            runtime.dispatch_tool_prefetch()
        cache.prefetch_gpu_session_nodes.assert_not_called()
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()


@pytest.mark.parametrize("barrier", ("transfer", "slot"))
def test_wakeup_keeps_transfer_and_admission_budgets_authoritative(barrier):
    runtime, key, positive, empty, cache, clock = wakeup_fixture()
    runtime._tool_opportunity_cache[key] = (clock[0] * 1000. + 100., empty)
    try:
        runtime._on_parent_pressure_parked(positive.step.node_id, {0: 4})
        with patch.object(runtime, "inspect_context_h2d_opportunity", return_value=positive), patch.object(
            runtime, "_wait_prefetch_transfer_busy", return_value=barrier == "transfer",
        ), patch.object(runtime, "_prefetch_slot_available", return_value=barrier != "slot"):
            runtime.dispatch_tool_prefetch()
        cache.prefetch_gpu_session_nodes.assert_not_called()
        assert not runtime.physical_ledger.pending_count
    finally:
        runtime.close()


def test_old_owner_demotion_does_not_rearm_a_new_session_generation():
    runtime, key, positive, empty, _, clock = wakeup_fixture()
    hint = runtime.tool_wait_hints[key.context_id]
    newer = replace(key, session_generation=key.session_generation + 1)
    runtime.tool_wait_hints[key.context_id] = replace(hint, key=newer)
    deadline = clock[0] * 1000. + 100.
    runtime._tool_opportunity_cache[key] = (deadline, empty)
    runtime._tool_opportunity_cache[newer] = (deadline, empty)
    runtime._tool_prefetch_next_ms = deadline
    try:
        runtime._on_parent_pressure_parked(positive.step.node_id, {0: 4})
        assert key not in runtime._tool_opportunity_cache
        assert runtime._tool_opportunity_cache[newer] == (deadline, empty)
        assert runtime._tool_prefetch_next_ms == deadline
    finally:
        runtime.close()
