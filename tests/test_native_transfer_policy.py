from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from beliefkv.runtime.native_transfer_policy import (
    native_residency_budget, transfer_start_window,
)
from beliefkv.runtime.native_transfer_service import NativeServiceEstimate
from beliefkv.runtime.sglang_v0520_observer import StaticPoolHeadroomObservation
from beliefkv.runtime.sglang_v0520_physical import PrefetchLoadStep, ShadowBackupStep
from beliefkv.runtime.sglang_v0520_physical import (
    ActionLocalPrefetchCandidate, ActionLocalShadowCandidate, ContextSessionAnchors,
    SessionH2DOpportunity,
)
from beliefkv.runtime.native_transfer_service import NativeServiceSample
from tests.test_tool_predictive_transfers import (
    locked_runtime, submit_restored_request, tool_runtime,
)
from tests.test_sglang_v0520_runtime import final_stage_runtime, req, select


def capacity(**overrides):
    return {
        "running_requests": 40, "max_running_requests": 48,
        "available_request_rows": 20, "prefill_slots": 16,
        "page_size": 16, "input_token_reserve": 8192,
        "full_free_tokens": 300_000, "mamba_free_slots": 40,
        "full_bytes_per_token": 20480, "mamba_bytes_per_slot": 64389120,
        "protected_bytes": 0,
        **overrides,
    }


def test_budget_expands_beyond_legacy_limits_only_with_real_capacity():
    budget = native_residency_budget(**capacity())
    assert budget.request_slots == 8
    assert budget.byte_limit > 1024 ** 3
    assert budget.reserve_full_tokens == 8192 + 16 * 48
    assert budget.reserve_mamba_slots == 8


@pytest.mark.parametrize("changes, slots", [
    ({"available_request_rows": 0}, 0),
    ({"prefill_slots": 0}, 0),
    ({"available_request_rows": 3}, 3),
    ({"running_requests": 48}, 1),
])
def test_budget_obeys_rows_and_next_batch_with_one_full_batch_frontier(changes, slots):
    assert native_residency_budget(**capacity(**changes)).request_slots == slots


def test_budget_preserves_running_growth_and_new_prefill_space():
    budget = native_residency_budget(**capacity(
        full_free_tokens=10, mamba_free_slots=1, protected_bytes=4096,
    ))
    assert budget.byte_limit == 4096


def test_start_window_includes_submit_delay_without_scaling_it_by_bytes():
    estimate = NativeServiceEstimate(50., 120., 80., 8, "matched_pool_shape_and_size")
    window = transfer_start_window(
        estimate, max_lead_ms=1000., observation_spacing_ms=100.,
    )
    assert window.service_ms == 120.
    assert window.enqueue_ms == 80.
    assert window.horizon_ms == 300.


def test_large_transfer_retains_maximum_lead_and_unsupported_service_is_skipped():
    supported = NativeServiceEstimate(500., 950., 20., 8, "matched_pool_shape_and_size")
    assert transfer_start_window(
        supported, max_lead_ms=1000., observation_spacing_ms=100.,
    ).horizon_ms == 1000.
    unsupported = NativeServiceEstimate(500., 950., 80., 8, "matched_pool_shape_and_size")
    assert transfer_start_window(
        unsupported, max_lead_ms=1000., observation_spacing_ms=100.,
    ) is None


def test_runtime_reads_native_limits_instead_of_treating_idle_memory_as_slots():
    runtime, *_ = locked_runtime()
    runtime._native_cache.req_to_token_pool = NS(available_size=lambda: 6)
    runtime._native_cache.host_pool_group.entry_map["mamba"].host_pool.size_per_token = 64
    runtime._observe_native_admission_capacity(
        running_batch=NS(reqs=[NS()] * 46),
        adder=NS(
            max_running_requests=48, max_prefill_bs=8, prefill_max_requests=4,
            can_run_list=[NS()], page_size=16, rem_input_tokens=100,
        ),
    )
    with patch(
        "beliefkv.runtime.sglang_v0520_runtime.observe_static_full_mamba_headroom",
        return_value=StaticPoolHeadroomObservation(
            True, device_full_free_tokens=10_000, device_mamba_free_slots=10,
        ),
    ):
        budget = runtime._current_residency_budget()
    assert budget.source == "native_next_prefill"
    assert budget.request_slots == 2
    assert budget.reserve_full_tokens == 100 + 16 * 48


def test_unobservable_capacity_does_not_keep_an_old_expanded_budget():
    runtime, *_ = locked_runtime()
    from beliefkv.runtime.native_transfer_policy import PrefetchResidencyBudget
    runtime._residency_budget = PrefetchResidencyBudget(
        32, 10 * 1024 ** 3, source="native_next_prefill",
    )
    runtime._native_max_running, runtime._native_prefill_slots = 48, 32
    budget = runtime._current_residency_budget()
    assert budget.request_slots == 4
    assert budget.byte_limit == 1024 ** 3
    assert budget.source == "legacy_capacity_unavailable"


def test_runtime_pressure_releases_speculative_before_ready_restore():
    from dataclasses import replace
    runtime, _, _, action, _, _ = locked_runtime()
    ready = replace(
        runtime._prefetch_service_leases[action.command_id],
        command_id="ready", demand_ready=True, acknowledged_at=0.,
    )
    runtime._prefetch_service_leases["ready"] = ready
    runtime.on_prefill_candidate_result(req("other"), admitted=False, result="NO_TOKEN")
    assert action.command_id not in runtime._prefetch_service_leases
    assert "ready" in runtime._prefetch_service_leases


def test_dynamic_restore_priority_still_gives_ordinary_demand_a_turn():
    from beliefkv.runtime.native_transfer_policy import PrefetchResidencyBudget
    runtime, *_ = locked_runtime()
    restored = submit_restored_request(runtime)
    other = req("other")
    runtime.register_visible_request(other)
    runtime._residency_budget = PrefetchResidencyBudget(
        1, 1024 ** 3, source="native_next_prefill",
    )
    with patch.object(runtime, "_causal_rank", side_effect=lambda key, index, ranks: (0, 0, index)):
        assert select(runtime, [other, restored]).candidates[0] is restored
        runtime.on_prefill_candidate_result(restored, admitted=True, result="ok")
        assert select(runtime, [other, restored]).candidates[0] is other
        runtime.on_prefill_candidate_result(other, admitted=True, result="ok")
        assert select(runtime, [other, restored]).candidates[0] is restored


def prepare_cache(runtime):
    def full_state(tokens):
        return NS(
            value=[0] * tokens, host_value=None, lock_ref=0, session_ref=1,
        )
    nodes = {
        11: NS(component_data={
            0: full_state(100), 2: full_state(1),
        }),
        12: NS(component_data={
            0: full_state(5), 2: full_state(1),
        }),
    }
    runtime._native_cache = NS(
        tree_core=NS(node_by_id=nodes.__getitem__),
        host_pool_group=NS(entry_map={
            "kv": NS(host_pool=NS(size_per_token=10)),
            "mamba": NS(host_pool=NS(size_per_token=100)),
        }),
    )
    return StaticPoolHeadroomObservation(
        True, host_full_free_tokens=1000, host_mamba_free_slots=10,
    )


def test_prepare_prefers_more_full_relief_and_less_copy_for_mamba_pressure():
    runtime, hint = tool_runtime()
    headroom = prepare_cache(runtime)
    large = ShadowBackupStep(hint.key, 11, 4, 11, 4)
    small = ShadowBackupStep(hint.key, 12, 4, 12, 4)
    ranks = [
        runtime._prepare_step_rank(step, headroom, full_pressure=True, mamba_pressure=False)
        for step in (large, small)
    ]
    assert ranks[0] < ranks[1]
    ranks = [
        runtime._prepare_step_rank(step, headroom, full_pressure=False, mamba_pressure=True)
        for step in (large, small)
    ]
    assert ranks[1] < ranks[0]


def test_prepare_rejects_insufficient_host_space_without_evicting_to_make_backup():
    runtime, hint = tool_runtime()
    prepare_cache(runtime)
    step = ShadowBackupStep(hint.key, 11, 4, 11, 4)
    assert runtime._prepare_step_rank(
        step, NS(host_full_free_tokens=20, host_mamba_free_slots=10),
        full_pressure=True, mamba_pressure=True,
    ) is None
    assert runtime.counts["prepare_candidate_no_host_capacity"] == 1


def test_prepare_rejects_window_too_short_to_copy_then_prefetch():
    runtime, hint = tool_runtime()
    headroom = prepare_cache(runtime)
    runtime._native_service_samples.extend([
        NativeServiceSample(1100, 300., "d2h", "hybrid", 50.),
    ] * 3)
    step = ShadowBackupStep(hint.key, 11, 4, 11, 4)
    assert runtime._prepare_step_rank(
        step, headroom, full_pressure=True, mamba_pressure=True,
    ) is None
    assert runtime.counts["prepare_candidate_short_wait_window"] == 1


def test_backed_full_without_mamba_is_available_for_pressure_demotion():
    runtime = final_stage_runtime()
    key = runtime.context_sessions["ctx-parent"]
    anchors = NS(reusable_input_tokens=100)
    node = NS(
        node_id=11, creation_time=4, parent_id=None, key_tokens=10,
        full_device_tokens=10, full_host_tokens=10,
        mamba_device_present=False, mamba_host_present=False,
    )
    with patch.object(runtime, "snapshot_session_anchors", return_value=anchors), patch(
        "beliefkv.runtime.sglang_v0520_runtime.capture_action_local_shadow",
        return_value=NS(nodes=(node,)),
    ):
        runtime._register_backed_pressure_nodes(key)
    assert runtime._parent_pressure_candidates[11] == (key, 4)


def test_prepare_reuses_backed_closure_and_does_not_register_after_parent_reentry():
    runtime = final_stage_runtime()
    runtime._clear_final_stage("join")
    key = runtime.context_sessions["ctx-parent"]
    root = NS(
        node_id=0, creation_time=1, parent_id=None, key_tokens=0,
        full_device_tokens=0, full_host_tokens=0,
        mamba_device_present=False, mamba_host_present=False,
        pending_write_id=None, pending_load_id=None,
    )
    leaf = NS(
        node_id=11, creation_time=4, parent_id=0, key_tokens=10,
        full_device_tokens=10, full_host_tokens=10,
        mamba_device_present=True, mamba_host_present=True,
        pending_write_id=None, pending_load_id=None,
    )
    anchors = ContextSessionAnchors(
        key, ((0, ((11, 4),)), (2, ((11, 4),))), 1., reusable_input_tokens=100,
    )
    candidate = ActionLocalShadowCandidate(anchors, (leaf, root), 0, 0)
    with patch.object(runtime, "capture_shadow_candidate", return_value=candidate) as capture, \
        patch.object(runtime, "snapshot_session_anchors") as snapshot:
        assert runtime.refreshed_shadow_backup_step(
            context_id=key.context_id, source="join_prepare",
        ) is None
        capture.assert_called_once()
        snapshot.assert_not_called()
        assert runtime._parent_pressure_candidates[11] == (key, 4)
        runtime._parent_pressure_candidates.clear()
        from beliefkv.control.causal_graph import InvocationState
        runtime.graph.invocations[key.invocation_id].state = InvocationState.READY
        assert runtime.refreshed_shadow_backup_step(
            context_id=key.context_id, source="join_prepare",
        ) is None
        assert not runtime._parent_pressure_candidates
        assert capture.call_count == 1


def test_pressure_maintenance_groups_contexts_and_still_expires_real_locks():
    runtime, hint, _, action, _, _ = locked_runtime()
    runtime._parent_pressure_candidates = {
        node: (hint.key, 2) for node in range(1, 12)
    }
    lease = runtime._prefetch_service_leases[action.command_id]
    with patch.object(runtime, "_long_tool_wait", return_value=True), \
        patch.object(runtime, "_refresh_prefetch_service_leases",
                     wraps=runtime._refresh_prefetch_service_leases) as refresh, \
        patch.object(runtime, "_live_parent_pressure_key",
                     wraps=runtime._live_parent_pressure_key) as live:
        runtime._prune_parent_pressure_candidates()
        refresh.assert_called_once()
        live.assert_called_once_with(hint.key)
        assert 1 not in runtime._parent_pressure_candidates
        assert len(runtime._parent_pressure_candidates) == 10
        with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
                   return_value=lease.expires_at):
            runtime._prune_parent_pressure_candidates()
        assert not runtime._prefetch_service_leases
        assert runtime.counts["prefetch_residency_released:service_window_expired"] == 1
        runtime.graph.workflows[hint.key.root_workflow_id].end_ts_ms = 1000.
        runtime._prune_parent_pressure_candidates()
        assert not runtime._parent_pressure_candidates


def test_prepare_sampling_reuses_h2d_closure_but_later_actions_read_fresh():
    runtime = final_stage_runtime()
    runtime._clear_final_stage("join")
    key = runtime.context_sessions["ctx-parent"]
    runtime._native_cache = NS(
        enable_session_radix_cache=True,
        cache_controller=NS(write_policy="write_back"),
        tree_core=NS(is_write_back=True),
    )
    anchors = ContextSessionAnchors(
        key, ((0, ((11, 4),)), (2, ((11, 4),))), 1.,
    )
    node = NS(
        node_id=11, full_device_tokens=20, full_host_tokens=0,
        mamba_device_present=True, mamba_host_present=False,
    )
    observed = ActionLocalPrefetchCandidate(anchors, (node,), 0, 0)
    h2d = SessionH2DOpportunity(
        anchors, StaticPoolHeadroomObservation(
            True, host_full_free_tokens=100, host_mamba_free_slots=1,
        ), None, 0, 0, None, candidate=observed,
    )
    step = ShadowBackupStep(key, 11, 4, 11, 4)
    row = {}
    with patch.object(runtime, "capture_shadow_candidate", return_value=None) as capture, \
        patch("beliefkv.runtime.sglang_v0520_runtime.next_shadow_backup_step",
              return_value=step):
        runtime._observe_prepare_opportunity(key, row, h2d)
        capture.assert_not_called()
        assert row["prepare_required_full_tokens"] == 20
        assert row["prepare_required_mamba_slots"] == 1
        assert row["prepare_reason"] == "fits_current_host_free_lists"
        assert runtime.refreshed_shadow_backup_step(
            context_id=key.context_id, source="join_prepare",
        ) is None
        capture.assert_called_once()


def test_tool_latest_start_waits_until_measured_small_transfer_window():
    runtime, hint = tool_runtime()
    runtime._native_cache = NS(cache_controller=NS(mem_pool_host=NS(entry_map={
        "kv": NS(host_pool=NS(size_per_token=10)),
        "mamba": NS(host_pool=NS(size_per_token=100)),
    })))
    runtime._native_service_samples.extend([
        NativeServiceSample(100, 20., "h2d", "full", 10.),
    ] * 3)
    opportunity = NS(
        step=PrefetchLoadStep(hint.key, 11, 4, 11, 4),
        fits_current_free_lists=True, required_full_tokens=10, required_mamba_slots=0,
    )
    with patch.object(runtime, "inspect_context_h2d_opportunity", return_value=opportunity), \
        patch.object(runtime, "refreshed_prefetch_gpu_step", return_value=opportunity.step), \
        patch.object(runtime, "issue_prefetch_gpu_step", return_value="command") as issue, \
        patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic") as clock:
        clock.return_value = hint.issued_monotonic_ms / 1000.
        runtime.dispatch_tool_prefetch()
        issue.assert_not_called()
        clock.return_value += .2
        runtime.dispatch_tool_prefetch()
        issue.assert_called_once()
    assert runtime._tool_ticket.start_window_ms == pytest.approx(130.)


def test_join_eta_does_not_treat_a_descheduled_child_as_still_receiving_service():
    from collections import deque
    runtime = final_stage_runtime()
    stage = runtime._final_stages["join"]
    stage.request_id = "child"
    runtime._native_running_batch = NS(reqs=[NS(rid="other")])
    assert not runtime._final_stage_service_available(stage)
    runtime._native_running_batch = NS(reqs=[NS(rid="child")])
    with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=10.):
        runtime._semantic_progress["child"] = deque([(9900., 100)])
        assert runtime._final_stage_service_available(stage)
        runtime._semantic_progress["child"] = deque([(9000., 100)])
        assert not runtime._final_stage_service_available(stage)


@pytest.mark.parametrize("upper, should_issue", [(400., False), (24., True), (20., False)])
def test_overtaken_work_center_uses_remaining_upper_or_waits_for_a_new_forecast(
    monkeypatch, upper, should_issue,
):
    import time
    from beliefkv.core.events import RuntimeEventKind
    from beliefkv.runtime.semantic_report_worker import SemanticReportInput, SemanticReportReply
    from tests.test_sglang_v0520_runtime import event

    monkeypatch.setenv("BELIEFKV_SEMANTIC_WORK_STATISTIC", "center")
    runtime = final_stage_runtime()
    child = req("child")
    child.session_id, child.session_generation = "child-session", 1
    child.beliefkv_metadata["context_epoch"] = 1
    runtime.on_events((event(
        7, RuntimeEventKind.LLM_SUBMIT, invocation_id="child",
        context_id="ctx-child", context_epoch=1, attributes={"request_id": "child"},
    ),))
    runtime.register_visible_request(child)
    stage = runtime._final_stages["join"]
    stage.generated_tokens, stage.tokens_per_second = 120, 40.
    runtime._semantic_worker = NS()
    runtime._semantic_forecasts["child"] = SemanticReportReply(
        SemanticReportInput(
            runtime.visible["child"], time.monotonic() * 1000.,
            100, 256, "Evidence complete.", True, 500, 1, 2,
        ), .99, 0., 4., upper, 5.,
    )
    runtime._h2d_samples.extend([(105, 200.)] * 3)
    runtime.attach_native_cache(NS(cache_controller=NS(mem_pool_host=NS(entry_map={
        "kv": NS(host_pool=NS(size_per_token=10)),
        "mamba": NS(host_pool=NS(size_per_token=5)),
    }))))
    opportunity = NS(
        step=PrefetchLoadStep(stage.key, 11, 4, 11, 4),
        fits_current_free_lists=True, required_full_tokens=10, required_mamba_slots=1,
    )
    with patch.object(runtime, "inspect_context_h2d_opportunity", return_value=opportunity), \
        patch.object(runtime, "refreshed_prefetch_gpu_step", return_value=opportunity.step), \
        patch.object(runtime, "issue_prefetch_gpu_step", return_value="command") as issue:
        runtime.dispatch_join_prefetch()
    assert bool(issue.call_count) is should_issue
    assert runtime.counts["semantic_work_forecast_overtaken"] == 1
