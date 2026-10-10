from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from beliefkv.control.causal_graph import InvocationState
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


def test_demand_residency_can_pin_existing_cache_without_spending_prefill_reserve():
    values = capacity(
        full_free_tokens=10, mamba_free_slots=1,
        evictable_full_tokens=20_000, evictable_mamba_slots=12,
    )
    budget = native_residency_budget(**values)
    assert budget.request_slots == 8
    assert budget.byte_limit == (
        (20_010 - budget.reserve_full_tokens) * values["full_bytes_per_token"]
        + (13 - budget.reserve_mamba_slots) * values["mamba_bytes_per_slot"]
    )
    assert budget.evictable_full_tokens == 20_000
    assert budget.evictable_mamba_slots == 12
    assert native_residency_budget(**{
        **values, "evictable_full_tokens": 0, "evictable_mamba_slots": 0,
    }).byte_limit == 0


@pytest.mark.parametrize("evictable_full", [10_000, -1, 50_000])
def test_runtime_uses_validated_evictable_capacity_only_for_demand_handoff(evictable_full):
    from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
    runtime = NativeAdmissionRuntime()
    runtime._native_max_running, runtime._native_prefill_slots = 48, 8
    runtime._native_page_size, runtime._native_input_reserve = 16, 100
    runtime._native_running_batch = NS(reqs=[NS()] * 46)
    runtime._native_cache = NS(
        req_to_token_pool=NS(
            available_size=lambda: 5, mamba_pool=NS(size=10),
        ),
        token_to_kv_pool_allocator=NS(size=40_000),
        host_pool_group=NS(entry_map={
            "kv": NS(host_pool=NS(size_per_token=10)),
            "mamba": NS(host_pool=NS(size_per_token=64)),
        }),
        full_evictable_size=lambda: evictable_full,
        mamba_evictable_size=lambda: 4,
    )
    with patch(
        "beliefkv.runtime.sglang_v0520_runtime.observe_static_full_mamba_headroom",
        return_value=StaticPoolHeadroomObservation(
            True, device_full_free_tokens=10, device_mamba_free_slots=0,
        ),
    ):
        assert runtime._current_residency_budget().byte_limit == 0
        demand = runtime._current_residency_budget(include_evictable=True)
        assert demand.request_slots == 2
        if evictable_full == 10_000:
            assert demand.byte_limit == (10_010 - 868) * 10 + 2 * 64
            assert demand.evictable_full_tokens == 10_000
        else:
            assert demand.byte_limit == 0
            assert demand.evictable_full_tokens == 0
        assert runtime._current_residency_budget().evictable_full_tokens == 0
    runtime.close()


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


def issue_restore_with_budget(runtime, key, budget):
    cache = runtime._native_cache
    unit = cache.host_pool_group.entry_map["kv"].host_pool.size_per_token
    cache.cache_controller = NS(
        mem_pool_host=cache.host_pool_group,
        _num_tokens_by_pool=lambda _: {"kv": 1},
        _transfer_num_bytes=lambda _: unit,
    )

    def native_load(**kwargs):
        accepted = kwargs["beliefkv_before_enqueue"](NS(
            beliefkv_command_id=kwargs["beliefkv_command_id"],
            node_ids=[1], device_indices=(0,), host_indices=(1,), pool_transfers=[],
        ))
        return NS(issued=accepted, node_id=1 if accepted else None)

    cache.prefetch_gpu_session_node = native_load
    step = PrefetchLoadStep(key, 1, 2, 1, 2)
    with patch.object(runtime, "refreshed_prefetch_gpu_step", return_value=step), \
        patch.object(runtime, "_current_residency_budget", return_value=budget):
        command = runtime.issue_prefetch_gpu_step(step)
    return command, NS(
        direction="h2d", status="completed", node_ids=(1,),
        num_tokens_by_pool=(("kv", 1),),
        child_commits=(NS(
            command_id=command, anchor_node_id=1, published_node_ids=(1,),
            num_tokens_by_pool=(("kv", 1),), num_bytes=unit,
        ),),
    )


@pytest.mark.parametrize("ack_slots,ack_bytes,source,locked", [
    (0, 1024, "native_next_prefill", True),
    (0, 0, "native_next_prefill", False),
    (0, 1024, "legacy_capacity_unavailable", False),
    (1, 1024, "native_next_prefill", True),
])
def test_issued_restore_keeps_bounded_protection_across_transient_slot_loss(
    ack_slots, ack_bytes, source, locked,
):
    from beliefkv.runtime.native_transfer_policy import PrefetchResidencyBudget

    runtime, hint, _, old_action, _, observation = locked_runtime()
    runtime._release_prefetch_service_lease(old_action.command_id, "test_reissue")
    tree = runtime._native_cache.tree_core
    tree.inc_lock_ref.reset_mock()
    issued_budget = PrefetchResidencyBudget(1, 1024, source="native_next_prefill")
    command, commit = issue_restore_with_budget(runtime, hint.key, issued_budget)
    assert command is not None
    assert runtime._prefetch_issue_budgets[command] == issued_budget
    ack_budget = PrefetchResidencyBudget(ack_slots, ack_bytes, source=source)
    with patch.object(runtime, "_current_residency_budget", return_value=ack_budget), \
        patch(
            "beliefkv.runtime.sglang_v0520_observer.observe_unified_node_closure",
            return_value=observation,
        ):
        actions = runtime.on_native_transfer_commit(commit)
        assert len(actions) == 1
        if ack_slots == 0:
            assert not runtime._prefetch_slot_available(hint.key)
    lease = runtime._prefetch_service_leases[command]
    assert (lease.lock_params is not None) is locked
    assert tree.inc_lock_ref.call_count == int(locked)
    assert runtime.counts["prefetch_issued_slot_protection_preserved"] == int(
        locked and ack_slots == 0
    )
    assert not runtime._prefetch_issue_budgets
    assert runtime.graph.invocations[hint.key.invocation_id].state.value == "wait_tool"
    assert lease.expires_at - lease.acknowledged_at == pytest.approx(2.)


def test_prefetch_slot_loss_before_enqueue_leaves_no_dma_or_issue_allowance():
    from beliefkv.runtime.native_transfer_policy import PrefetchResidencyBudget

    runtime, hint, _, old_action, _, _ = locked_runtime()
    runtime._release_prefetch_service_lease(old_action.command_id, "test_reissue")
    command, _ = issue_restore_with_budget(
        runtime, hint.key, PrefetchResidencyBudget(0, 1024, source="native_next_prefill"),
    )
    assert command is None
    assert runtime.physical_ledger.pending_count == 0
    assert not runtime._prefetch_steps
    assert not runtime._prefetch_issue_budgets
    assert runtime.counts["prefetch_slot_lost_before_enqueue"] == 1


def test_issued_restore_cannot_take_another_contexts_protection_allowance():
    from dataclasses import replace
    from beliefkv.runtime.native_transfer_policy import PrefetchResidencyBudget

    runtime, hint, _, old_action, _, observation = locked_runtime()
    original = runtime._prefetch_service_leases[old_action.command_id]
    runtime._release_prefetch_service_lease(old_action.command_id, "test_reissue")
    budget = PrefetchResidencyBudget(1, 1024, source="native_next_prefill")
    command, commit = issue_restore_with_budget(runtime, hint.key, budget)
    runtime._prefetch_service_leases["other"] = replace(
        original, command_id="other", key=replace(hint.key, context_id="ctx-other"),
    )
    with patch.object(
        runtime, "_current_residency_budget",
        return_value=PrefetchResidencyBudget(0, 1024, source="native_next_prefill"),
    ), patch(
        "beliefkv.runtime.sglang_v0520_observer.observe_unified_node_closure",
        return_value=observation,
    ):
        runtime.on_native_transfer_commit(commit)
    assert runtime._prefetch_service_leases[command].lock_params is None
    assert runtime._prefetch_service_leases["other"].lock_params is original.lock_params
    assert not runtime._prefetch_issue_budgets


def test_restore_served_before_ack_does_not_retain_issued_protection_allowance():
    from beliefkv.runtime.native_transfer_policy import PrefetchResidencyBudget

    runtime, hint, _, old_action, _, _ = locked_runtime()
    runtime._release_prefetch_service_lease(old_action.command_id, "test_reissue")
    command, commit = issue_restore_with_budget(
        runtime, hint.key, PrefetchResidencyBudget(1, 1024, source="native_next_prefill"),
    )
    runtime._prefetch_first_services[command] = (123., "resumed")
    runtime.on_native_transfer_commit(commit)
    assert not runtime._prefetch_service_leases
    assert not runtime._prefetch_issue_budgets
    assert runtime.counts["prefetch_service_preceded_ledger_ack"] == 1


def test_discarded_restore_drops_its_issued_protection_allowance():
    from beliefkv.runtime.native_transfer_policy import PrefetchResidencyBudget

    runtime, hint, _, old_action, _, _ = locked_runtime()
    runtime._release_prefetch_service_lease(old_action.command_id, "test_reissue")
    command, _ = issue_restore_with_budget(
        runtime, hint.key, PrefetchResidencyBudget(1, 1024, source="native_next_prefill"),
    )
    runtime._discard_prefetch_tracking("physical_expired", (command,))
    assert not runtime._prefetch_issue_budgets
    assert not runtime._prefetch_steps


def test_runtime_pressure_releases_speculative_before_ready_restore():
    from dataclasses import replace
    runtime, _, _, action, _, _ = locked_runtime()
    ready = replace(
        runtime._prefetch_service_leases[action.command_id],
        command_id="ready", demand_ready=True, acknowledged_at=0.,
        key=replace(
            runtime._prefetch_service_leases[action.command_id].key,
            context_id="ctx-ready", invocation_id="ready", request_id="ready",
        ),
    )
    runtime._prefetch_service_leases["ready"] = ready
    runtime.on_prefill_candidate_result(req("other"), admitted=False, result="NO_TOKEN")
    assert action.command_id not in runtime._prefetch_service_leases
    assert "ready" in runtime._prefetch_service_leases


def test_same_request_tool_progress_keeps_restore_until_next_request_consumes_it():
    from beliefkv.core.events import RuntimeEventKind
    from tests.test_tool_predictive_transfers import event

    runtime, hint, _, action, receipt, _ = locked_runtime()
    lease = runtime._prefetch_service_leases[action.command_id]
    invocation = hint.key.invocation_id
    runtime.on_events((event(
        3, RuntimeEventKind.TOOL_END, invocation_id=invocation,
        attributes={"tool_run_id": "long"},
    ),))
    runtime.on_events((event(
        4, RuntimeEventKind.TOOL_START, invocation_id=invocation,
        attributes={"tool_run_id": "second"},
    ),))
    assert runtime.graph.invocations[invocation].updated_ts_ms != lease.wait_revision
    assert set(runtime.graph.invocations[invocation].active_tool_calls) == {"second"}
    runtime._refresh_prefetch_service_leases()
    assert action.command_id in runtime._prefetch_service_leases
    assert runtime._prefetch_service_leases[action.command_id].lock_params is not None
    assert runtime.counts["prefetch_residency_released:wait_episode_changed"] == 0
    runtime.on_events((
        event(5, RuntimeEventKind.TOOL_END, invocation_id=invocation,
              attributes={"tool_run_id": "second"}),
        event(6, RuntimeEventKind.LLM_SUBMIT, invocation_id=invocation,
              context_id=hint.key.context_id, context_epoch=1,
              attributes={"request_id": "resumed"}),
    ))
    restored = req(invocation)
    restored.rid = "resumed"
    restored.beliefkv_metadata["context_epoch"] = 1
    restored.session_id, restored.session_generation = "s", 1
    runtime.register_visible_request(restored)
    assert select(runtime, [restored]).candidates == (restored,)
    assert runtime._prefetch_service_leases[action.command_id].demand_ready
    runtime.on_batch_completed(NS(reqs=[restored]))
    assert not runtime._prefetch_service_leases
    runtime._native_cache.tree_core.dec_lock_ref.assert_called_once_with(1, receipt)


@pytest.mark.parametrize("combined", [False, True])
def test_client_submit_retains_locked_prefix_until_native_arrival_without_admission(combined):
    from beliefkv.core.events import RuntimeEventKind
    from tests.test_tool_predictive_transfers import event

    runtime, hint, _, action, receipt, _ = locked_runtime()
    initial = runtime._prefetch_service_leases[action.command_id]
    rows = []
    runtime._opportunity_writer = NS(record=rows.append)
    events = (
        event(3, RuntimeEventKind.TOOL_END, invocation_id=hint.key.invocation_id,
              attributes={"tool_run_id": "long"}),
        event(4, RuntimeEventKind.LLM_SUBMIT, invocation_id=hint.key.invocation_id,
              context_id=hint.key.context_id, context_epoch=1,
              attributes={"request_id": "resumed"}),
    )
    with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
               return_value=initial.acknowledged_at + .2):
        for batch in (events,) if combined else ((events[0],), (events[1],)):
            runtime.on_events(batch)
    submitted = runtime._prefetch_service_leases[action.command_id]
    assert submitted.client_submitted_at == initial.acknowledged_at + .2
    assert submitted.expires_at == initial.acknowledged_at + 10.
    assert not submitted.demand_ready
    assert "resumed" not in runtime.visible
    assert runtime.counts["native_admitted"] == 0
    assert runtime.counts["prefetch_residency_client_submitted"] == 1
    assert next(row for row in rows if row["event"] == "prefetch_client_submitted")[
        "request_id"
    ] == "resumed"
    runtime.graph.invocations[hint.key.invocation_id].state = InvocationState.READY
    with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic",
               return_value=initial.acknowledged_at + 4.6):
        runtime._refresh_prefetch_service_leases()
        restored = req(hint.key.invocation_id)
        restored.rid = "resumed"
        restored.beliefkv_metadata["context_epoch"] = 1
        restored.session_id, restored.session_generation = "s", 1
        runtime.register_visible_request(restored)
    assert runtime._prefetch_service_leases[action.command_id].demand_ready
    assert runtime._prefetch_service_leases[action.command_id].expires_at == (
        initial.acknowledged_at + 10.
    )
    runtime.on_batch_completed(NS(reqs=[restored]))
    assert not runtime._prefetch_service_leases
    runtime._native_cache.tree_core.dec_lock_ref.assert_called_once_with(1, receipt)


@pytest.mark.parametrize("epoch,attributes,locked", [
    (0, {"request_id": "resumed"}, True),
    (1, {"request_id": ""}, True),
    (1, {"request_id": "tool"}, True),
    (1, {"request_id": "resumed", "runtime_internal": True}, True),
    (1, {"request_id": "resumed"}, False),
])
def test_client_submit_needs_next_external_request_and_existing_native_lock(
    epoch, attributes, locked,
):
    from dataclasses import replace
    from beliefkv.core.events import RuntimeEventKind
    from tests.test_tool_predictive_transfers import event

    runtime, hint, _, action, *_ = locked_runtime()
    initial = runtime._prefetch_service_leases[action.command_id]
    if not locked:
        initial = replace(initial, lock_params=None, protected_bytes=0)
        runtime._prefetch_service_leases[action.command_id] = initial
    # The original request ID is a fixture detail; use the actual lease identity.
    if attributes.get("request_id") == "tool":
        attributes = {**attributes, "request_id": hint.key.request_id}
    runtime.on_events((
        event(3, RuntimeEventKind.TOOL_END, invocation_id=hint.key.invocation_id,
              attributes={"tool_run_id": "long"}),
        event(4, RuntimeEventKind.LLM_SUBMIT, invocation_id=hint.key.invocation_id,
              context_id=hint.key.context_id, context_epoch=epoch, attributes=attributes),
    ))
    lease = runtime._prefetch_service_leases[action.command_id]
    assert lease.expires_at == initial.expires_at
    assert lease.client_submitted_at is None
    assert not lease.demand_ready
    assert runtime.counts["prefetch_residency_client_submitted"] == 0
    runtime.close()


@pytest.mark.parametrize("invalid", ["expired", "session_changed", "another_tool_pending"])
def test_client_submit_does_not_revive_or_authorize_an_invalid_restore(invalid):
    from beliefkv.core.events import RuntimeEventKind
    from tests.test_tool_predictive_transfers import event

    runtime, hint, _, action, *_ = locked_runtime()
    lease = runtime._prefetch_service_leases[action.command_id]
    when = lease.expires_at + .1 if invalid == "expired" else lease.acknowledged_at + .2
    if invalid == "session_changed":
        runtime._native_cache.session_refs._session_generations["s"] = 2
    events = [
        event(3, RuntimeEventKind.TOOL_END, invocation_id=hint.key.invocation_id,
              attributes={"tool_run_id": "long"}),
    ]
    if invalid == "another_tool_pending":
        events.append(event(
            4, RuntimeEventKind.TOOL_START, invocation_id=hint.key.invocation_id,
            attributes={"tool_run_id": "second"},
        ))
    events.append(event(
        5, RuntimeEventKind.LLM_SUBMIT, invocation_id=hint.key.invocation_id,
        context_id=hint.key.context_id, context_epoch=1,
        attributes={"request_id": "resumed"},
    ))
    with patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=when):
        runtime.on_events(tuple(events))
    assert runtime.counts["prefetch_residency_client_submitted"] == 0
    if invalid == "another_tool_pending":
        assert runtime._prefetch_service_leases[action.command_id].expires_at == lease.expires_at
    else:
        assert not runtime._prefetch_service_leases
    runtime.close()


def test_burst_consumers_include_all_visible_requests_with_exact_session_identity():
    from dataclasses import replace

    runtime, _, _, action, *_ = locked_runtime()
    lease = runtime._prefetch_service_leases[action.command_id]
    runtime._prefetch_service_leases["second-extent"] = replace(
        lease, command_id="second-extent", lock_params=None, protected_bytes=0,
    )
    mismatches = {
        "wrong-generation": {"session_generation": 2},
        "wrong-session": {"session_id": "another-session"},
        "wrong-epoch": {"context_epoch": lease.key.context_epoch + 2},
        "wrong-workflow": {"root_workflow_id": "another-workflow"},
        "wrong-invocation": {"invocation_id": "another-agent"},
        "wrong-context": {"context_id": "another-context"},
    }
    for rid, fields in mismatches.items():
        runtime.visible[rid] = replace(lease.key, request_id=rid, **fields)
    runtime._refresh_prefetch_service_leases(context_id=lease.key.context_id)
    assert all(not item.demand_ready for item in runtime._prefetch_service_leases.values())
    runtime.visible["matching-request"] = replace(
        lease.key, request_id="matching-request", context_epoch=lease.key.context_epoch + 1,
    )
    runtime._refresh_prefetch_service_leases(context_id=lease.key.context_id)
    assert all(item.demand_ready for item in runtime._prefetch_service_leases.values())
    assert runtime.counts["prefetch_residency_demand_ready"] == 2
    runtime.close()


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


def test_prepare_small_ancestor_requires_space_for_missing_checkpoint_prefix():
    from dataclasses import replace

    runtime, hint = tool_runtime()
    prepare_cache(runtime)
    step = ShadowBackupStep(
        hint.key, 12, 4, 12, 4, missing_full_prefix_tokens=105,
    )
    headroom = NS(host_full_free_tokens=100, host_mamba_free_slots=0)
    assert runtime._prepare_step_rank(
        step, headroom, full_pressure=True, mamba_pressure=False,
    ) is None
    assert runtime.counts["prepare_checkpoint_no_host_capacity"] == 1
    headroom.host_full_free_tokens = 105
    assert runtime._prepare_step_rank(
        step, headroom, full_pressure=True, mamba_pressure=False,
    ) is not None
    headroom.host_full_free_tokens = 5
    remaining = replace(step, missing_full_prefix_tokens=5)
    assert runtime._prepare_step_rank(
        remaining, headroom, full_pressure=True, mamba_pressure=False,
    ) is not None


def test_prepare_rejects_window_too_short_to_copy_then_prefetch():
    runtime, hint = tool_runtime()
    headroom = prepare_cache(runtime)
    runtime._native_service_samples.extend([
        NativeServiceSample(1000, 300., "d2h", "full", 50.),
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


def test_pressure_maintenance_distinguishes_keys_with_the_same_context():
    from dataclasses import replace

    runtime = final_stage_runtime()
    runtime._clear_final_stage("join")
    key = runtime.context_sessions["ctx-parent"]
    runtime._parent_pressure_candidates = {
        1: (replace(key, attempt_id=key.attempt_id + 1), 1),
        2: (key, 2),
        3: (replace(key, context_epoch=key.context_epoch + 1), 3),
        4: (key, 4),
        5: (replace(key), 5),
    }
    try:
        runtime._prune_parent_pressure_candidates()
        assert runtime._parent_pressure_candidates == {
            2: (key, 2), 4: (key, 4), 5: (key, 5),
        }
    finally:
        runtime.close()


@pytest.mark.parametrize("case", (
    "backed", "new_backed", "submitted", "declined", "mamba_only", "no_parents",
))
def test_join_prepare_publishes_current_pressure_state_once(case):
    from beliefkv.control.causal_graph import InvocationState

    class Node(NS):
        __hash__ = object.__hash__

    runtime = final_stage_runtime()
    runtime._clear_final_stage("join")
    runtime.enable_prepare_host = True
    key = runtime.context_sessions["ctx-parent"]
    node = Node(
        id=11, creation_time=4, backuped=True,
        write_through_pending_id=None, load_back_pending_id=None,
        component_data={
            0: NS(value=[1], host_value=[1], lock_ref=0, session_ref=1),
            2: NS(value=None, host_value=None, lock_ref=0, session_ref=0),
        },
    )
    cache = NS(
        tree_core=NS(node_by_id=lambda _: node, evictable_device_leaves={node}),
        ongoing_write_through={},
        beliefkv_join_pressure_candidates=((99, 99),),
    )
    runtime._native_cache = cache
    if case != "new_backed":
        runtime._parent_pressure_candidates[11] = (key, 4)
    if case == "no_parents":
        runtime.graph.invocations[key.invocation_id].state = InvocationState.READY

    def scan(**kwargs):
        if case == "new_backed":
            runtime._parent_pressure_candidates[11] = (key, 4)
        return (
            ShadowBackupStep(key, 11, 4, 11, 4)
            if case in ("submitted", "declined") else None
        )

    def issue(step, *, source):
        if case == "submitted":
            node.write_through_pending_id = 42
            return "prepare"
        node.component_data[0].lock_ref = 1
        return None

    try:
        with patch(
            "beliefkv.runtime.sglang_v0520_runtime.observe_static_full_mamba_headroom",
            return_value=StaticPoolHeadroomObservation(
                True,
                device_full_free_tokens=100_000 if case == "mamba_only" else 0,
                device_mamba_free_slots=0 if case == "mamba_only" else 10,
                host_full_free_tokens=1000, host_mamba_free_slots=10,
            ),
        ), patch.object(runtime, "refreshed_shadow_backup_step", side_effect=scan), \
            patch.object(runtime, "_prepare_step_rank", return_value=(-1., 1.)), \
            patch.object(runtime, "issue_shadow_backup_step", side_effect=issue) as submit, \
            patch.object(runtime, "_publish_parent_pressure_candidates",
                         wraps=runtime._publish_parent_pressure_candidates) as publish:
            runtime.dispatch_join_prepare([req("child")])
            publish.assert_called_once()
            assert submit.call_count == int(case in ("submitted", "declined"))
        expected = ((11, 4),) if case in ("backed", "new_backed", "mamba_only") else ()
        assert cache.beliefkv_join_pressure_candidates == expected
        assert cache.beliefkv_join_pressure_candidates_by_component[0] == expected
    finally:
        runtime.close()


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
        assert row["prepare_required_mamba_slots"] == 0
        assert row["prepare_reason"] == "fits_current_host_free_lists"
        assert runtime.refreshed_shadow_backup_step(
            context_id=key.context_id, source="join_prepare",
        ) is None
        capture.assert_called_once()


def test_prepare_occupied_cache_without_next_service_demand_does_no_scan():
    runtime = final_stage_runtime()
    runtime._clear_final_stage("join")
    runtime.enable_prepare_host = True
    runtime.attach_native_cache(NS())
    with patch(
        "beliefkv.runtime.sglang_v0520_runtime.observe_static_full_mamba_headroom",
        return_value=StaticPoolHeadroomObservation(
            True, device_full_free_tokens=0, device_mamba_free_slots=0,
        ),
    ), patch.object(runtime, "refreshed_shadow_backup_step") as scan, \
        patch.object(runtime, "_prune_parent_pressure_candidates") as maintain:
        runtime.dispatch_join_prepare([], running_batch=NS(reqs=[]))
        scan.assert_not_called()
        maintain.assert_not_called()
    assert runtime.counts["prepare_no_service_demand"] == 1


def test_prepare_pressure_tracks_next_slots_and_active_page_growth():
    runtime, *_ = locked_runtime()
    runtime._native_max_running, runtime._native_page_size = 48, 16
    headroom = StaticPoolHeadroomObservation(
        True, device_full_free_tokens=65536, device_mamba_free_slots=4,
    )
    assert runtime._prepare_pressure([NS()] * 156, NS(reqs=[NS()] * 48), headroom) == (
        False, False,
    )
    growth = StaticPoolHeadroomObservation(
        True, device_full_free_tokens=767, device_mamba_free_slots=0,
    )
    assert runtime._prepare_pressure([], NS(reqs=[NS()] * 48), growth) == (True, False)


def test_backed_prepare_rechecks_after_backoff_and_new_epoch_is_immediate():
    from dataclasses import replace
    runtime = final_stage_runtime()
    runtime._clear_final_stage("join")
    runtime.enable_prepare_host = True
    runtime.attach_native_cache(NS())
    with patch(
        "beliefkv.runtime.sglang_v0520_runtime.observe_static_full_mamba_headroom",
        return_value=StaticPoolHeadroomObservation(
            True, device_full_free_tokens=0, device_mamba_free_slots=0,
        ),
    ), patch.object(runtime, "refreshed_shadow_backup_step", return_value=None) as scan, \
        patch("beliefkv.runtime.sglang_v0520_runtime.time.monotonic", return_value=10.) as clock:
        runtime.dispatch_join_prepare([req("child")])
        key = runtime.context_sessions["ctx-parent"]
        assert scan.call_count == 1
        clock.return_value = 10.1
        runtime.dispatch_join_prepare([req("child")])
        assert scan.call_count == 1
        assert runtime._prepare_probe_due(replace(key, context_epoch=key.context_epoch + 1), 10100.)
        clock.return_value = 11.1
        runtime.dispatch_join_prepare([req("child")])
        assert scan.call_count == 2


def test_pressure_publication_indexes_full_leaves_and_mamba_independently():
    class Node(NS):
        __hash__ = object.__hash__

    runtime, _ = tool_runtime()
    nodes = {
        number: Node(
            id=number, creation_time=number, backuped=True,
            write_through_pending_id=None, load_back_pending_id=None,
            component_data=[
                NS(value=[1], host_value=[1], lock_ref=0, session_ref=1),
                NS(value=None, host_value=None, lock_ref=0, session_ref=0),
                NS(value=[1], host_value=[1], lock_ref=0, session_ref=1),
            ],
        )
        for number in range(1, 12)
    }
    runtime._native_cache = NS(
        tree_core=NS(node_by_id=nodes.__getitem__, evictable_device_leaves={nodes[11]}),
        ongoing_write_through={},
    )
    runtime._parent_pressure_candidates = {
        number: (None, number) for number in nodes
    }
    runtime._publish_parent_pressure_candidates()
    cache = runtime._native_cache
    assert cache.beliefkv_join_pressure_candidates[:8] == tuple((n, n) for n in range(1, 9))
    assert cache.beliefkv_join_pressure_candidates_by_component[0] == ((11, 11),)
    assert cache.beliefkv_join_pressure_candidates_by_component[2] == tuple((n, n) for n in nodes)


@pytest.mark.parametrize("blocked", ("none", "locked", "shared", "swa_unbacked", "pending"))
def test_full_only_pressure_candidate_can_save_missing_mamba_at_actual_eviction(blocked):
    class Node(NS):
        __hash__ = object.__hash__

    runtime, _ = tool_runtime()
    node = Node(
        id=1, creation_time=1, backuped=True,
        write_through_pending_id=None, load_back_pending_id=None,
        component_data=[
            NS(value=[1], host_value=[1], lock_ref=0, session_ref=1),
            NS(value=None, host_value=None, lock_ref=0, session_ref=0),
            NS(value=[1], host_value=None, lock_ref=0, session_ref=1),
        ],
    )
    if blocked == "locked":
        node.component_data[2].lock_ref = 1
    elif blocked == "shared":
        node.component_data[2].session_ref = 2
    elif blocked == "swa_unbacked":
        node.component_data[1].value = [1]
    elif blocked == "pending":
        node.write_through_pending_id = "d2h"
    runtime._native_cache = NS(
        tree_core=NS(node_by_id=lambda _: node, evictable_device_leaves={node}),
        ongoing_write_through={},
    )
    runtime._parent_pressure_candidates = {1: (None, 1)}
    runtime._publish_parent_pressure_candidates()
    assert runtime._native_cache.beliefkv_join_pressure_candidates_by_component[0] == (
        ((1, 1),) if blocked == "none" else ()
    )
    assert runtime._native_cache.beliefkv_join_pressure_candidates_by_component[2] == ()
    assert node.component_data[2].host_value is None


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
