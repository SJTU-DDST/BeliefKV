from types import SimpleNamespace as NS
import time
from unittest.mock import Mock, patch

import numpy as np
import pytest

from beliefkv.control.causal_graph import InvocationState
from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from beliefkv.runtime.sglang_v0520_runtime import _ExecutionHandoffTicket
from beliefkv.runtime.sglang_v0520_runtime import _PrefetchServiceLease
from beliefkv.runtime.sglang_v0520_physical import (
    PrefetchLoadStep, PhysicalActionCompleted, PhysicalReceiptError,
)
from beliefkv.runtime.sglang_v0520_observer import (
    StaticPoolHeadroomObservation, UnifiedNodeSummary,
)
from beliefkv.runtime.native_transfer_policy import PrefetchResidencyBudget
from tests.test_sglang_v0520_runtime import req, select


def runtime_with_requests(monkeypatch):
    monkeypatch.setenv("BELIEFKV_ENABLE_EXECUTION_HANDOFF", "1")
    runtime = NativeAdmissionRuntime()
    requests = [req(name) for name in ("cold", "warm", "parent")]
    runtime.on_events((RuntimeEvent("start", 0., RuntimeEventKind.WORKFLOW_START, "wf"),))
    for seq, request in enumerate(requests, 1):
        request.session_id, request.session_generation = f"s-{request.rid}", 1
        request.origin_input_ids = [10, 11, 12]
        runtime.register_visible_request(request)
        runtime.on_events((RuntimeEvent(
            str(seq), float(seq), RuntimeEventKind.INVOCATION_CREATE, "wf",
            invocation_id=request.rid, context_id=f"ctx-{request.rid}",
            agent_definition_id="agent", agent_instance_id=request.rid,
        ),))
    observations = {
        "component_leaves": ((0, ((1, 2),)), (2, ((1, 2),))),
        "reusable_input_tokens": 2, "checkpoint_tokens": 2,
        "device_checkpoint_tokens": 0, "missing_full_tokens": 2,
        "missing_mamba_slots": 1,
    }
    cache = NS(
        inspect_beliefkv_reentry=Mock(side_effect=lambda request: {
            **observations,
            **({"device_checkpoint_tokens": 2, "missing_full_tokens": 0,
                "missing_mamba_slots": 0} if request.rid == "warm" else {}),
        }),
    )
    runtime.attach_native_cache(cache)
    return runtime, requests, cache


def test_warm_first_preserves_untagged_positions_and_ordinary_aging(monkeypatch):
    runtime, (cold, warm, _), _ = runtime_with_requests(monkeypatch)
    plain = NS(rid="plain", beliefkv_metadata=None)
    assert select(runtime, [cold, plain, warm]).candidates == (warm, plain, cold)
    runtime._visible_since["cold"] -= 11.
    assert select(runtime, [cold, plain, warm]).candidates == (cold, plain, warm)


def test_warm_first_does_not_override_causal_parent_message_priority(monkeypatch):
    runtime, (cold, warm, parent), cache = runtime_with_requests(monkeypatch)
    runtime.graph.invocations["parent"].pending_messages = 1
    assert select(runtime, [cold, warm, parent]).candidates[0] is parent
    assert [call.args[0] for call in cache.inspect_beliefkv_reentry.call_args_list] == [
        cold, warm,
    ]
    assert runtime._request_reentry_anchors(parent) is not None
    assert cache.inspect_beliefkv_reentry.call_args.args == (parent,)


@pytest.mark.parametrize("cold_full_tokens,expected", [
    (10000, ("parent", "warm", "cold")),
    (100, ("parent", "cold", "warm")),
])
def test_resident_order_compares_full_and_mamba_bytes_without_requiring_device_state(
    monkeypatch, cold_full_tokens, expected,
):
    runtime, (cold, warm, parent), cache = runtime_with_requests(monkeypatch)
    runtime.enable_execution_handoff = False
    cache.host_pool_group = NS(entry_map={
        "kv": NS(host_pool=NS(size_per_token=20480)),
        "mamba": NS(host_pool=NS(size_per_token=64389120)),
    })
    runtime.attach_native_cache(cache)
    observed = {
        "cold": {"missing_full_tokens": cold_full_tokens, "missing_mamba_slots": 0,
                 "device_checkpoint_tokens": 256},
        "warm": {"missing_full_tokens": 0, "missing_mamba_slots": 1,
                 "device_checkpoint_tokens": 0},
        "parent": {"missing_full_tokens": 0, "missing_mamba_slots": 0,
                   "device_checkpoint_tokens": 1024},
    }
    cache.inspect_beliefkv_reentry.side_effect = lambda request: {
        "checkpoint_tokens": 40000, **observed[request.rid],
    }
    plain = NS(rid="plain", beliefkv_metadata=None)
    actual = select(runtime, [cold, plain, warm, parent]).candidates
    assert actual[1] is plain
    assert tuple(request.rid for request in actual if request is not plain) == expected
    assert cache.inspect_beliefkv_reentry.call_count == 3
    assert not runtime._prefetch_service_leases
    assert runtime.physical_ledger.pending_count == 0
    runtime._visible_since[cold.rid] -= 11.
    assert select(runtime, [cold, warm, parent]).candidates[0] is cold
    runtime.close()


def test_restore_byte_order_preserves_parent_causal_priority(monkeypatch):
    runtime, (cold, warm, parent), cache = runtime_with_requests(monkeypatch)
    cache.host_pool_group = NS(entry_map={
        "kv": NS(host_pool=NS(size_per_token=20480)),
        "mamba": NS(host_pool=NS(size_per_token=64389120)),
    })
    runtime.attach_native_cache(cache)
    runtime.graph.invocations["parent"].pending_messages = 1
    assert select(runtime, [cold, warm, parent]).candidates[0] is parent
    assert runtime._prefetch_priority_promoted is None
    runtime.close()


@pytest.mark.parametrize("full_unit,mamba_unit", [(None, 10), (True, 10), (10, -1)])
def test_unobservable_pool_units_keep_original_residency_order(
    monkeypatch, full_unit, mamba_unit,
):
    runtime, (cold, warm, _), cache = runtime_with_requests(monkeypatch)
    runtime.enable_execution_handoff = False
    cache.host_pool_group = NS(entry_map={
        "kv": NS(host_pool=NS(size_per_token=full_unit)),
        "mamba": NS(host_pool=NS(size_per_token=mamba_unit)),
    })
    runtime.attach_native_cache(cache)
    cache.inspect_beliefkv_reentry.side_effect = lambda request: {
        "checkpoint_tokens": 40000,
        "missing_full_tokens": 10000 if request is cold else 0,
        "missing_mamba_slots": int(request is warm),
        "device_checkpoint_tokens": 256 if request is cold else 0,
    }
    assert runtime._residency_pool_bytes is None
    assert select(runtime, [cold, warm]).candidates == (cold, warm)
    runtime.close()


def test_model_round_retains_other_join_ranks_and_consumes_messages(monkeypatch):
    runtime, (cold, warm, parent), _ = runtime_with_requests(monkeypatch)
    runtime.enable_resident_first = False
    runtime.on_events((
        RuntimeEvent(
            "join-create", 10., RuntimeEventKind.JOIN_CREATE, "wf",
            join_id="join", member_invocation_ids=("cold",),
        ),
        RuntimeEvent(
            "join-wait", 11., RuntimeEventKind.JOIN_WAIT, "wf",
            invocation_id="parent", join_id="join",
        ),
    ))
    runtime.graph.invocations["warm"].pending_messages = 1
    assert select(runtime, [warm, cold, parent]).candidates[0] is cold
    assert runtime.frontier.admission_rank("warm") == (2, 0)
    with patch.object(runtime.frontier, "_classification", wraps=runtime.frontier._classification) as classify:
        runtime.on_events((RuntimeEvent(
            "submit", 12., RuntimeEventKind.LLM_SUBMIT, "wf",
            invocation_id="warm", context_id="ctx-warm", context_epoch=0,
            attributes={"messages_consumed": 1},
        ),))
        assert select(runtime, [warm, cold, parent]).candidates[0] is cold
        assert runtime.frontier.admission_rank("warm") == (3, 0)
        assert [call.args[0].invocation_id for call in classify.call_args_list] == ["warm"]
    runtime.close()


@pytest.mark.parametrize("kind", (
    RuntimeEventKind.LLM_RESULT,
    RuntimeEventKind.TOOL_START,
    RuntimeEventKind.TOOL_END,
))
def test_local_round_recomputes_only_the_changed_invocation(monkeypatch, kind):
    runtime, requests, _ = runtime_with_requests(monkeypatch)
    runtime.enable_resident_first = False
    select(runtime, requests)
    before = {request.rid: runtime.frontier.admission_rank(request.rid) for request in requests}
    with patch.object(runtime.frontier, "_classification", wraps=runtime.frontier._classification) as classify:
        runtime.on_events((RuntimeEvent(
            "local-round", 10., kind, "wf", invocation_id="warm",
            context_id="ctx-warm", context_epoch=0,
            attributes={"tool_name": "execute", "tool_run_id": "run"},
        ),))
        after = {request.rid: runtime.frontier.admission_rank(request.rid) for request in requests}
        assert after == before
        assert [call.args[0].invocation_id for call in classify.call_args_list] == ["warm"]
    runtime.close()


def test_wait_child_state_change_invalidates_descendant_depth(monkeypatch):
    runtime, (cold, warm, parent), _ = runtime_with_requests(monkeypatch)
    runtime.enable_resident_first = False
    runtime.on_events((RuntimeEvent(
        "call", 10., RuntimeEventKind.CALL, "wf",
        invocation_id="parent", target_invocation_id="cold",
    ),))
    assert select(runtime, [warm, cold, parent]).candidates[0] is cold
    assert runtime.frontier.admission_rank("cold") == (1, -1)
    runtime.on_events((RuntimeEvent(
        "ancestor-tool", 11., RuntimeEventKind.TOOL_START, "wf",
        invocation_id="parent", context_id="ctx-parent", context_epoch=0,
        attributes={"tool_name": "execute", "tool_run_id": "run"},
    ),))
    assert select(runtime, [warm, cold, parent]).candidates[0] is warm
    assert runtime.frontier.admission_rank("cold") == (3, 0)
    runtime.close()


def test_terminal_model_return_does_not_retain_join_dependency_cache(monkeypatch):
    runtime, (cold, warm, parent), _ = runtime_with_requests(monkeypatch)
    runtime.enable_resident_first = False
    runtime.on_events((
        RuntimeEvent(
            "join-create", 10., RuntimeEventKind.JOIN_CREATE, "wf",
            join_id="join", member_invocation_ids=("cold",),
        ),
        RuntimeEvent(
            "join-wait", 11., RuntimeEventKind.JOIN_WAIT, "wf",
            invocation_id="parent", join_id="join",
        ),
    ))
    assert select(runtime, [warm, parent, cold]).candidates[0] is cold
    assert runtime.frontier.admission_rank("cold") == (0, 0)
    with patch.object(runtime.frontier, "_classification", wraps=runtime.frontier._classification) as classify:
        runtime.on_events((RuntimeEvent(
            "terminal-result", 12., RuntimeEventKind.LLM_RESULT, "wf",
            invocation_id="cold", context_id="ctx-cold", context_epoch=0,
            attributes={"terminal": True},
        ),))
        assert runtime.graph.joins["join"].satisfied
        assert select(runtime, [warm, parent, cold]).candidates[0] is warm
        assert runtime.frontier.admission_rank("cold") == (3, 0)
        assert {call.args[0].invocation_id for call in classify.call_args_list} == {
            "cold", "warm", "parent",
        }
    runtime.close()


def test_new_message_invalidates_rank_even_after_a_retained_model_round(monkeypatch):
    runtime, (cold, warm, parent), _ = runtime_with_requests(monkeypatch)
    runtime.enable_resident_first = False
    assert select(runtime, [cold, warm, parent]).candidates[0] is cold
    runtime.on_events((RuntimeEvent(
        "model-result", 10., RuntimeEventKind.LLM_RESULT, "wf",
        invocation_id="cold", context_id="ctx-cold", context_epoch=0,
    ),))
    assert select(runtime, [cold, warm, parent]).candidates[0] is cold
    runtime.on_events((RuntimeEvent(
        "message", 11., RuntimeEventKind.MESSAGE, "wf",
        invocation_id="cold", target_invocation_id="warm",
    ),))
    assert select(runtime, [cold, warm, parent]).candidates[0] is warm
    runtime.close()


def test_single_waiting_request_skips_sort_probe_but_handoff_reads_checkpoint(monkeypatch):
    runtime, (cold, _, _), cache = runtime_with_requests(monkeypatch)
    runtime._residency_scan_cursor = 8
    assert select(runtime, [cold]).candidates == (cold,)
    assert runtime._residency_scan_cursor == 0
    cache.inspect_beliefkv_reentry.assert_not_called()
    assert runtime._request_reentry_anchors(cold) is not None
    cache.inspect_beliefkv_reentry.assert_called_once_with(cold)


def test_reentry_anchor_reads_live_identity_once_and_rejects_changed_binding(monkeypatch):
    runtime, (cold, _, _), cache = runtime_with_requests(monkeypatch)
    from beliefkv.runtime import sglang_v0520_runtime as runtime_module

    with patch.object(runtime_module, "_request_key", wraps=runtime_module._request_key) as read:
        assert runtime._request_reentry_anchors(cold) is not None
        assert read.call_count == 1
        cold.session_generation += 1
        assert runtime._request_reentry_anchors(cold) is None
        assert read.call_count == 2
    cache.inspect_beliefkv_reentry.assert_called_once_with(cold)


def test_cached_causal_group_counts_keep_aging_live_without_mutation(monkeypatch):
    runtime, (cold, warm, parent), cache = runtime_with_requests(monkeypatch)
    runtime.graph.invocations[parent.rid].pending_messages = 1
    clock = [time.monotonic()]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    runtime.scheduler_step([cold, warm, parent])
    assert select(runtime, [cold, warm, parent]).candidates == (parent, warm, cold)
    groups = runtime._prefill_causal_cache[3]
    before = groups.copy()
    assert cache.inspect_beliefkv_reentry.call_count == 2

    runtime._visible_since[cold.rid] = clock[0] - 11.
    clock[0] += .06
    runtime.scheduler_step([cold, warm, parent])
    assert select(runtime, [cold, warm, parent]).candidates == (cold, parent, warm)
    assert runtime._prefill_causal_cache[3] is groups
    assert groups == before
    assert cache.inspect_beliefkv_reentry.call_count == 2

    runtime._visible_since[cold.rid] = clock[0]
    clock[0] += .06
    runtime.scheduler_step([cold, warm, parent])
    assert select(runtime, [cold, warm, parent]).candidates == (parent, warm, cold)
    assert groups == before
    assert cache.inspect_beliefkv_reentry.call_count == 4
    runtime.close()


def test_aged_arrival_order_needs_no_residency_inspection(monkeypatch):
    runtime, requests, cache = runtime_with_requests(monkeypatch)
    for request in requests:
        runtime._visible_since[request.rid] -= 11.
    assert select(runtime, requests).candidates == tuple(requests)
    cache.inspect_beliefkv_reentry.assert_not_called()
    runtime.close()


def test_aged_head_skips_inspection_but_younger_requests_keep_warm_first(monkeypatch):
    runtime, (cold, warm, parent), cache = runtime_with_requests(monkeypatch)
    runtime._visible_since[cold.rid] -= 11.
    assert select(runtime, [cold, parent, warm]).candidates == (cold, warm, parent)
    assert [call.args[0].rid for call in cache.inspect_beliefkv_reentry.call_args_list] == [
        parent.rid, warm.rid,
    ]
    runtime.close()


def test_selected_aged_handoff_still_reads_its_live_checkpoint(monkeypatch):
    runtime, requests, cache = runtime_with_requests(monkeypatch)
    for request in requests:
        runtime._visible_since[request.rid] -= 11.
    cold = requests[0]
    step = PrefetchLoadStep(runtime.visible[cold.rid], 1, 2, 1, 2)
    with patch(
        "beliefkv.runtime.sglang_v0520_runtime.inspect_session_h2d_opportunity",
        return_value=NS(step=step, fits_current_free_lists=True),
    ), patch.object(runtime, "issue_prefetch_gpu_step", return_value="load") as issue:
        runtime.dispatch_execution_handoff(requests, running_batch=None)
    cache.inspect_beliefkv_reentry.assert_called_once_with(cold)
    issue.assert_called_once_with(step, source="execution_handoff")
    assert runtime._execution_handoff.key == runtime.visible[cold.rid]
    runtime.close()


def test_exhausted_handoff_skips_capacity_and_planning_but_admission_stays_live(monkeypatch):
    runtime, requests, cache = runtime_with_requests(monkeypatch)
    runtime._execution_handoff_attempted.update(runtime.visible.values())
    with patch.object(runtime, "plan_native_prefill", wraps=runtime.plan_native_prefill) as plan, \
        patch.object(runtime, "_current_residency_budget",
                     wraps=runtime._current_residency_budget) as budget:
        runtime.dispatch_execution_handoff(requests, running_batch=None)
        plan.assert_not_called()
        budget.assert_not_called()
        cache.inspect_beliefkv_reentry.assert_not_called()
        assert runtime.counts["execution_handoff_candidates_already_attempted"] == 1
        assert select(runtime, requests).candidates[0].rid == "warm"
        assert plan.call_count == 1
    runtime.close()


def test_exhausted_handoff_rechecks_request_binding(monkeypatch):
    runtime, requests, _ = runtime_with_requests(monkeypatch)
    runtime._execution_handoff_attempted.update(runtime.visible.values())
    assert runtime._handoff_candidates_already_attempted(requests)
    requests[0].session_generation += 1
    assert not runtime._handoff_candidates_already_attempted(requests)
    runtime.close()


def test_exhausted_handoff_new_candidate_and_rearmed_parent_are_not_skipped(monkeypatch):
    runtime, requests, cache = runtime_with_requests(monkeypatch)
    runtime._execution_handoff_attempted.update(runtime.visible.values())
    cold = requests[0]
    key = runtime.visible[cold.rid]
    runtime._execution_handoff_attempted.remove(key)
    step = PrefetchLoadStep(key, 1, 2, 1, 2)
    with patch(
        "beliefkv.runtime.sglang_v0520_runtime.inspect_session_h2d_opportunity",
        return_value=NS(step=step, fits_current_free_lists=True),
    ), patch.object(runtime, "issue_prefetch_gpu_step", return_value="load") as issue:
        runtime.dispatch_execution_handoff(requests, running_batch=None)
    issue.assert_called_once_with(step, source="execution_handoff")
    assert runtime._execution_handoff.key == key
    assert runtime.counts["execution_handoff_candidates_already_attempted"] == 0
    assert runtime._handoff_candidates_already_attempted(requests)
    cache.inspect_beliefkv_reentry.assert_any_call(cold)
    runtime.close()


def test_exhausted_handoff_preserves_tagged_planning_bound(monkeypatch):
    runtime, requests, _ = runtime_with_requests(monkeypatch)
    cold, _, parent = requests
    runtime._execution_handoff_attempted.add(runtime.visible[cold.rid])
    plain = NS(rid="plain", beliefkv_metadata=None)
    assert runtime._handoff_candidates_already_attempted([plain] * 700)
    assert not runtime._handoff_candidates_already_attempted([plain] * 700 + [parent])
    assert runtime._handoff_candidates_already_attempted([cold] * 512 + [parent])
    cold.beliefkv_metadata = {}
    assert not runtime._handoff_candidates_already_attempted([cold])
    runtime.close()


def test_aged_handoff_frontier_ignores_new_tail_without_skipping_admission(monkeypatch):
    runtime, (cold, warm, parent), cache = runtime_with_requests(monkeypatch)
    now = time.monotonic()
    runtime._visible_since[cold.rid] = now - 20.
    runtime._visible_since[warm.rid] = now - 19.
    runtime._visible_since[parent.rid] = now
    runtime._execution_handoff_attempted.update(
        runtime.visible[request.rid] for request in (cold, warm)
    )
    running = NS(reqs=[])
    with patch.object(runtime, "_current_residency_budget", return_value=PrefetchResidencyBudget(
        2, 1024 ** 3, source="native_next_prefill",
    )), patch.object(runtime, "plan_native_prefill", wraps=runtime.plan_native_prefill) as plan:
        runtime.dispatch_execution_handoff([parent, warm, cold], running_batch=running)
        plan.assert_not_called()
        cache.inspect_beliefkv_reentry.assert_not_called()
        assert runtime._native_running_batch is running
        assert runtime._execution_handoff is None
        assert runtime.counts["execution_handoff_aged_frontier_already_attempted"] == 1
        assert select(runtime, [parent, warm, cold]).candidates == (cold, warm, parent)
        assert plan.call_count == 1
    runtime.close()


@pytest.mark.parametrize("change", (
    "frontier_wider", "earlier_unattempted", "rearmed", "not_aged", "changed_session",
    "terminal_frontier",
))
def test_aged_handoff_frontier_keeps_unattempted_eligible_requests(monkeypatch, change):
    runtime, (cold, warm, parent), _ = runtime_with_requests(monkeypatch)
    now = time.monotonic()
    runtime._visible_since.update({
        cold.rid: now - 20., warm.rid: now - 19., parent.rid: now,
    })
    runtime._execution_handoff_attempted.update(
        runtime.visible[request.rid] for request in (cold, warm)
    )
    slots = 2
    if change == "frontier_wider":
        slots = 3
    elif change == "earlier_unattempted":
        runtime._visible_since[parent.rid] = now - 21.
    elif change == "rearmed":
        runtime._execution_handoff_attempted.remove(runtime.visible[cold.rid])
    elif change == "not_aged":
        runtime._visible_since[warm.rid] = now - 9.9
    elif change == "changed_session":
        parent.session_generation += 1
    else:
        runtime.graph.invocations[cold.rid].state = InvocationState.DONE
    assert not runtime._aged_handoff_frontier_already_attempted(
        [parent, warm, cold], frontier_slots=slots, now_ms=now * 1000.,
    )
    runtime.close()


@pytest.mark.parametrize("priority", ("restore", "parent_handoff"))
def test_aged_handoff_frontier_keeps_promotable_tail(monkeypatch, priority):
    runtime, (cold, warm, parent), _ = runtime_with_requests(monkeypatch)
    now = time.monotonic()
    runtime._visible_since.update({
        cold.rid: now - 20., warm.rid: now - 19., parent.rid: now,
    })
    runtime._execution_handoff_attempted.update(
        runtime.visible[request.rid] for request in (cold, warm)
    )
    key = runtime.visible[parent.rid]
    if priority == "restore":
        runtime._prefetch_service_leases["restored"] = NS(key=key, demand_ready=True)
    else:
        runtime._parent_capacity_handoffs[key.context_id] = NS()
        monkeypatch.setattr(runtime, "_live_parent_capacity_handoff", lambda _: NS())
    assert not runtime._aged_handoff_frontier_already_attempted(
        [parent, warm, cold], frontier_slots=2, now_ms=now * 1000.,
    )
    runtime._prefetch_service_leases.clear()
    runtime._parent_capacity_handoffs.clear()
    runtime.close()


def test_aged_handoff_frontier_preserves_fifo_ties_and_tagged_bound(monkeypatch):
    runtime, (cold, warm, parent), _ = runtime_with_requests(monkeypatch)
    now = time.monotonic()
    runtime._visible_since.update({request.rid: now - 20. for request in (cold, warm, parent)})
    runtime._execution_handoff_attempted.update(
        runtime.visible[request.rid] for request in (cold, warm)
    )
    plain = NS(rid="plain", beliefkv_metadata=None)
    assert runtime._aged_handoff_frontier_already_attempted(
        [plain] * 700 + [cold, warm, parent], frontier_slots=2, now_ms=now * 1000.,
    )
    assert not runtime._aged_handoff_frontier_already_attempted(
        [parent, cold, warm], frontier_slots=2, now_ms=now * 1000.,
    )
    assert runtime._aged_handoff_frontier_already_attempted(
        [cold] * 512 + [parent], frontier_slots=2, now_ms=now * 1000.,
    )
    runtime.close()


def test_capacity_bypass_only_when_native_can_try_another_unaged_request(monkeypatch):
    runtime, (cold, _, _), _ = runtime_with_requests(monkeypatch)
    assert runtime.allow_prefill_capacity_bypass(cold, native_budget_available=True, bypassed=0)
    assert not runtime.allow_prefill_capacity_bypass(cold, native_budget_available=False, bypassed=0)
    assert not runtime.allow_prefill_capacity_bypass(cold, native_budget_available=True, bypassed=8)
    runtime._visible_since[cold.rid] -= 11.
    assert not runtime.allow_prefill_capacity_bypass(cold, native_budget_available=True, bypassed=0)
    runtime._visible_since[cold.rid] += 11.
    runtime.enable_resident_first = False
    assert not runtime.allow_prefill_capacity_bypass(cold, native_budget_available=True, bypassed=0)
    assert runtime.counts["prefill_capacity_bypassed"] == 1


def test_handoff_prefetches_without_demand_prediction_and_does_not_block_warm(monkeypatch):
    runtime, (cold, warm, _), _ = runtime_with_requests(monkeypatch)
    records = []
    runtime._opportunity_writer = NS(record=records.append)
    key = runtime.visible["cold"]
    step = PrefetchLoadStep(key, 1, 2, 1, 2)
    opportunity = NS(step=step, fits_current_free_lists=True)
    with patch("beliefkv.runtime.sglang_v0520_runtime.inspect_session_h2d_opportunity",
               return_value=opportunity), \
         patch.object(runtime, "issue_prefetch_gpu_step", return_value="restore") as issue:
        runtime.dispatch_execution_handoff([cold, warm], running_batch=NS(reqs=[NS()]))
    issue.assert_called_once_with(step, source="execution_handoff")
    assert not runtime.demand_hints
    assert runtime._execution_handoff.key == key
    assert runtime.defer_prefill_for_prefetch(cold)
    assert not runtime.defer_prefill_for_prefetch(warm)
    assert records[0]["event"] == "execution_handoff_selected"
    runtime._execution_handoff.expires_at -= 10.
    assert not runtime.defer_prefill_for_prefetch(cold)


@pytest.mark.parametrize("slots", [0, 1, 2])
def test_handoff_only_restores_within_next_native_admission_slots(monkeypatch, slots):
    runtime, (cold, warm, _), _ = runtime_with_requests(monkeypatch)
    key = runtime.visible["cold"]
    step = PrefetchLoadStep(key, 1, 2, 1, 2)
    with patch.object(runtime, "_current_residency_budget", return_value=PrefetchResidencyBudget(
        slots, 1024 ** 3, source="native_next_prefill",
    )), patch(
        "beliefkv.runtime.sglang_v0520_runtime.inspect_session_h2d_opportunity",
        return_value=NS(step=step, fits_current_free_lists=True),
    ), patch.object(runtime, "issue_prefetch_gpu_step", return_value="restore") as issue:
        runtime.dispatch_execution_handoff([cold, warm], running_batch=NS(reqs=[NS()]))
    if slots == 2:
        issue.assert_called_once_with(step, source="execution_handoff")
    else:
        issue.assert_not_called()
        assert runtime._execution_handoff is None
    runtime.close()


def test_handoff_skips_empty_frontier_work_and_resumes_when_slots_return(monkeypatch):
    runtime, (cold, warm, _), cache = runtime_with_requests(monkeypatch)
    step = PrefetchLoadStep(runtime.visible["cold"], 1, 2, 1, 2)
    batch = NS(reqs=[NS()])
    with patch.object(runtime, "plan_native_prefill", wraps=runtime.plan_native_prefill) as plan, \
         patch.object(runtime, "_current_residency_budget", return_value=PrefetchResidencyBudget(
             0, 1024 ** 3, source="native_next_prefill",
         )) as budget, patch(
             "beliefkv.runtime.sglang_v0520_runtime.inspect_session_h2d_opportunity",
             return_value=NS(step=step, fits_current_free_lists=True),
         ), patch.object(runtime, "issue_prefetch_gpu_step", return_value="restore") as issue:
        runtime.dispatch_execution_handoff([cold, warm], running_batch=batch)
        assert runtime._native_running_batch is batch
        plan.assert_not_called()
        cache.inspect_beliefkv_reentry.assert_not_called()
        issue.assert_not_called()
        assert not runtime._execution_handoff_attempted
        assert runtime.counts["execution_handoff_no_frontier_slots"] == 1
        runtime._execution_handoff_next_ms = 0.
        budget.return_value = PrefetchResidencyBudget(
            2, 1024 ** 3, source="native_next_prefill",
        )
        runtime.dispatch_execution_handoff([cold, warm], running_batch=batch)
        plan.assert_called_once()
        issue.assert_called_once_with(step, source="execution_handoff")
    runtime.close()


def test_handoff_empty_queue_does_not_plan_or_observe_capacity(monkeypatch):
    runtime, _, cache = runtime_with_requests(monkeypatch)
    with patch.object(runtime, "plan_native_prefill") as plan, \
         patch.object(runtime, "_current_residency_budget") as budget:
        runtime.dispatch_execution_handoff([], running_batch=None)
    plan.assert_not_called()
    budget.assert_not_called()
    cache.inspect_beliefkv_reentry.assert_not_called()
    assert runtime.counts["execution_handoff_no_waiting_requests"] == 1
    runtime.close()


def test_existing_handoff_processes_ack_and_extents_with_zero_frontier_slots(monkeypatch):
    runtime, (cold, _, _), _ = runtime_with_requests(monkeypatch)
    key = runtime.visible["cold"]
    step = PrefetchLoadStep(key, 1, 2, 1, 2)
    with patch(
        "beliefkv.runtime.sglang_v0520_runtime.inspect_session_h2d_opportunity",
        return_value=NS(step=step, fits_current_free_lists=True),
    ), patch.object(runtime, "issue_prefetch_gpu_step", return_value="restore"):
        runtime.dispatch_execution_handoff([cold], running_batch=None)
    runtime.completed_physical_actions.append(PhysicalActionCompleted(
        "restore", "PREFETCH_GPU", key.context_id, key.context_epoch,
        (1,), (("kv", 100),), 100,
    ))
    next_step = PrefetchLoadStep(key, 1, 2, 2, 3)
    with patch.object(runtime, "_current_residency_budget", return_value=PrefetchResidencyBudget(
        0, 1024 ** 3, source="native_next_prefill",
    )) as budget, patch.object(runtime, "plan_native_prefill") as plan, patch(
        "beliefkv.runtime.sglang_v0520_runtime.inspect_session_h2d_opportunity",
        return_value=NS(step=next_step, fits_current_free_lists=True),
    ), patch.object(runtime, "issue_prefetch_gpu_step", return_value="next") as issue:
        runtime.dispatch_execution_handoff([cold], running_batch=None)
    budget.assert_not_called()
    plan.assert_not_called()
    issue.assert_called_once_with(next_step, source="execution_handoff")
    assert runtime._execution_handoff.issued_nodes == 2
    assert runtime.counts["execution_handoff_acked"] == 1
    runtime.close()


def test_fragmented_handoff_continues_after_sixteen_acked_extents(monkeypatch):
    runtime, (cold, _, _), cache = runtime_with_requests(monkeypatch)
    key = runtime.visible[cold.rid]
    first_commands = tuple(f"first-{index}" for index in range(16))
    ticket = _ExecutionHandoffTicket(
        key, cold, time.monotonic() + 2., command_id=first_commands[-1],
        issued_nodes=16, command_ids=first_commands,
    )
    runtime._execution_handoff = ticket
    runtime.completed_physical_actions.extend(
        PhysicalActionCompleted(command, "PREFETCH_GPU", key.context_id,
                                key.context_epoch, (index,), (("kv", 100),), 100)
        for index, command in enumerate(first_commands)
    )
    cache.prefetch_gpu_session_nodes = Mock()
    steps = tuple(PrefetchLoadStep(key, 1, 2, index, 3) for index in range(16, 20))
    commands = tuple(f"tail-{index}" for index in range(4))
    with patch(
        "beliefkv.runtime.sglang_v0520_runtime.inspect_session_h2d_opportunity",
        return_value=NS(step=steps[0], steps=steps, fits_current_free_lists=True),
    ) as inspect, patch.object(
        runtime, "_issue_handoff_prefetch_steps", return_value=commands,
    ) as issue:
        runtime.dispatch_execution_handoff([cold], running_batch=None)
    assert inspect.call_args.kwargs["max_steps"] == 16
    issue.assert_called_once_with(ticket, steps)
    assert ticket.issued_nodes == 20
    assert ticket.command_ids == commands
    assert runtime.counts["execution_handoff_acked"] == 16
    assert runtime.counts["execution_handoff_closed:node_budget"] == 0
    runtime.close()


def test_fragmented_handoff_respects_pending_capacity_and_original_expiry(monkeypatch):
    runtime, (cold, _, _), cache = runtime_with_requests(monkeypatch)
    key = runtime.visible[cold.rid]
    ticket = _ExecutionHandoffTicket(key, cold, time.monotonic() + 2., issued_nodes=32)
    runtime._execution_handoff = ticket
    runtime._execution_handoff_attempted.add(key)
    cache.prefetch_gpu_session_nodes = Mock()
    runtime.physical_ledger.max_pending = 3
    step = PrefetchLoadStep(key, 1, 2, 33, 3)
    with patch(
        "beliefkv.runtime.sglang_v0520_runtime.inspect_session_h2d_opportunity",
        return_value=NS(step=step, steps=(step,), fits_current_free_lists=True),
    ) as inspect, patch.object(
        runtime, "_issue_handoff_prefetch_steps", return_value=("tail",),
    ) as issue:
        original_expiry = ticket.expires_at
        runtime.dispatch_execution_handoff([cold], running_batch=None)
        assert inspect.call_args.kwargs["max_steps"] == 3
        assert ticket.expires_at == original_expiry
        inspect.reset_mock()
        issue.reset_mock()
        ticket.expires_at -= 10.
        runtime.dispatch_execution_handoff([cold], running_batch=None)
        inspect.assert_not_called()
        issue.assert_not_called()
    assert runtime._execution_handoff is None
    assert runtime.counts["execution_handoff_closed:expired_or_request_changed"] == 1
    runtime.close()


def test_nonoverlap_fragmented_restore_defer_still_expires_after_sixteen_extents(monkeypatch):
    runtime, (cold, warm, _), _ = runtime_with_requests(monkeypatch)
    ticket = _ExecutionHandoffTicket(
        runtime.visible[cold.rid], cold, time.monotonic() + 2., issued_nodes=16,
    )
    runtime._execution_handoff = ticket
    with patch.object(runtime, "can_prefetch_during_overlap", return_value=False):
        assert runtime.defer_prefill_for_prefetch(cold)
        assert not runtime.defer_prefill_for_prefetch(warm)
        ticket.expires_at -= 10.
        assert not runtime.defer_prefill_for_prefetch(cold)
    runtime.close()


@pytest.mark.parametrize("source", ["execution_handoff", "tool_wait"])
def test_demand_handoff_pins_restored_cache_until_service_or_allocation_pressure(
    monkeypatch, source,
):
    runtime, (cold, _, _), cache = runtime_with_requests(monkeypatch)
    key = runtime.visible[cold.rid]
    node = NS(id=1, creation_time=2, component_data={
        0: NS(value=object()), 2: NS(value=object()),
    })
    receipt = NS(node_id=1)
    cache.tree_core = NS(
        node_by_id=lambda node_id: node,
        inc_lock_ref=Mock(return_value=NS(to_dec_params=lambda: receipt)),
        dec_lock_ref=Mock(),
    )
    cache.session_refs = NS(
        _session_generations={key.session_id: key.session_generation},
        _closed_session_ids=set(),
    )
    cache.req_to_token_pool = NS(
        available_size=lambda: 8, mamba_pool=NS(size=513),
    )
    cache.token_to_kv_pool_allocator = NS(size=1_798_995)
    cache.host_pool_group = NS(entry_map={
        "kv": NS(host_pool=NS(size_per_token=20480)),
        "mamba": NS(host_pool=NS(size_per_token=64389120)),
    })
    cache.full_evictable_size = lambda: 500_000
    cache.mamba_evictable_size = lambda: 10
    runtime._native_max_running, runtime._native_prefill_slots = 48, 8
    runtime._native_running_batch = NS(reqs=[NS()] * 48)
    runtime._native_page_size, runtime._native_input_reserve = 16, 8192
    observation = NS(observable=True, nodes=(
        NS(node_id=1, creation_time=2, full_device_tokens=20_000, mamba_device_present=True),
    ))
    step = PrefetchLoadStep(key, 1, 2, 1, 2)
    action = PhysicalActionCompleted(
        "restored", "PREFETCH_GPU", key.context_id, key.context_epoch,
        (1,), (("kv", 20_000 * 20480), ("mamba", 64389120)),
        20_000 * 20480 + 64389120,
    )
    runtime._prefetch_steps[action.command_id] = (step, source, None)
    with patch(
        "beliefkv.runtime.sglang_v0520_runtime.observe_static_full_mamba_headroom",
        return_value=StaticPoolHeadroomObservation(
            True, device_full_free_tokens=0, device_mamba_free_slots=0,
        ),
    ), patch(
        "beliefkv.runtime.sglang_v0520_observer.observe_unified_node_closure",
        return_value=observation,
    ):
        runtime._register_prefetch_service_lease(action)
    lease = runtime._prefetch_service_leases[action.command_id]
    assert lease.lock_params is receipt
    assert lease.protected_bytes == action.num_bytes
    cache.tree_core.inc_lock_ref.assert_called_once_with(1)
    runtime.on_prefill_candidate_result(req("other"), admitted=False, result="NO_TOKEN")
    assert not runtime._prefetch_service_leases
    cache.tree_core.dec_lock_ref.assert_called_once_with(1, receipt)
    assert not runtime.physical_disabled
    runtime.close()


def test_handoff_extents_share_one_request_slot_and_release_together_on_pressure(monkeypatch):
    runtime, (cold, _, _), cache = runtime_with_requests(monkeypatch)
    key = runtime.visible[cold.rid]
    root = NS(id=0, creation_time=0, parent=None)
    nodes = {
        1: NS(id=1, creation_time=2, parent=root),
        2: NS(id=2, creation_time=3),
    }
    nodes[2].parent = nodes[1]
    for node in nodes.values():
        node.component_data = {0: NS(value=object()), 2: NS(value=object())}
    receipts = {node_id: NS(node_id=node_id) for node_id in nodes}
    cache.tree_core = NS(
        node_by_id=nodes.__getitem__,
        inc_lock_ref=Mock(side_effect=lambda node_id: NS(
            to_dec_params=lambda: receipts[node_id],
        )),
        dec_lock_ref=Mock(),
    )
    cache.session_refs = NS(
        _session_generations={key.session_id: key.session_generation},
        _closed_session_ids=set(),
    )
    cache.host_pool_group = NS(entry_map={
        "kv": NS(host_pool=NS(size_per_token=10)),
        "mamba": NS(host_pool=NS(size_per_token=20)),
    })
    with patch.object(
        runtime, "_current_residency_budget",
        return_value=PrefetchResidencyBudget(1, 1024 ** 3, source="native_next_prefill"),
    ), patch(
        "beliefkv.runtime.sglang_v0520_observer.observe_unified_node_closure",
        side_effect=lambda cache, node_id, **kwargs: NS(
            observable=True, nodes=[
                NS(node_id=n, creation_time=nodes[n].creation_time,
                   full_device_tokens=100, mamba_device_present=True)
                for n in range(1, node_id + 1)
            ],
        ),
    ):
        for node_id in nodes:
            step = PrefetchLoadStep(key, 2, 3, node_id, nodes[node_id].creation_time)
            action = PhysicalActionCompleted(
                f"extent-{node_id}", "PREFETCH_GPU", key.context_id, key.context_epoch,
                (node_id,), (("kv", 1000),), 1000,
            )
            runtime._prefetch_steps[action.command_id] = (step, "execution_handoff", None)
            runtime._register_prefetch_service_lease(action)
        assert runtime._prefetch_slot_available(key)
    assert cache.tree_core.inc_lock_ref.call_count == 2
    assert runtime._prefetch_service_leases["extent-1"].lock_params is None
    assert runtime._prefetch_service_leases["extent-2"].lock_params is receipts[2]
    assert runtime._prefetch_service_leases["extent-2"].protected_bytes == 2020
    runtime.on_prefill_candidate_result(req("other"), admitted=False, result="NO_TOKEN")
    assert not runtime._prefetch_service_leases
    cache.tree_core.dec_lock_ref.assert_any_call(1, receipts[1])
    cache.tree_core.dec_lock_ref.assert_any_call(2, receipts[2])
    assert cache.tree_core.dec_lock_ref.call_count == 2
    assert not runtime.physical_disabled
    runtime.close()


def test_descendant_lock_covers_restored_ancestor_without_recapturing_closure(monkeypatch):
    runtime, (cold, _, _), cache = runtime_with_requests(monkeypatch)
    key = runtime.visible[cold.rid]
    root = NS(id=0, creation_time=0, parent=None)
    first = NS(id=1, creation_time=2, parent=root)
    last = NS(id=2, creation_time=3, parent=first)
    nodes = {1: first, 2: last}
    for node in nodes.values():
        node.component_data = {0: NS(value=object()), 2: NS(value=object())}
    receipt = NS(node_id=2)
    cache.tree_core = NS(
        node_by_id=nodes.__getitem__,
        inc_lock_ref=Mock(return_value=NS(to_dec_params=lambda: receipt)),
        dec_lock_ref=Mock(),
    )
    cache.session_refs = NS(
        _session_generations={key.session_id: key.session_generation},
        _closed_session_ids=set(),
    )
    cache.host_pool_group = NS(entry_map={
        "kv": NS(host_pool=NS(size_per_token=10)),
        "mamba": NS(host_pool=NS(size_per_token=20)),
    })
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    observation = NS(observable=True, nodes=[
        NS(node_id=node.id, creation_time=node.creation_time,
           full_device_tokens=100, mamba_device_present=True)
        for node in (first, last)
    ])
    with patch.object(
        runtime, "_current_residency_budget",
        return_value=PrefetchResidencyBudget(1, 1024 ** 3),
    ), patch(
        "beliefkv.runtime.sglang_v0520_observer.observe_unified_node_closure",
        return_value=observation,
    ) as capture:
        for command, node in (("leaf", last), ("ancestor", first)):
            step = PrefetchLoadStep(key, 2, 3, node.id, node.creation_time)
            action = PhysicalActionCompleted(
                command, "PREFETCH_GPU", key.context_id, key.context_epoch,
                (node.id,), (("kv", 1000),), 1000,
            )
            runtime._prefetch_steps[command] = (step, "execution_handoff", None)
            runtime._register_prefetch_service_lease(action)
    capture.assert_called_once_with(cache, 2)
    cache.tree_core.inc_lock_ref.assert_called_once_with(2)
    assert records[-1]["protection_reason"] == "covered_by_existing_lock"
    assert records[-1]["protected_by_command_id"] == "leaf"
    assert runtime._prefetch_service_leases["ancestor"].protected_bytes == 0
    runtime._release_prefetch_service_lease("ancestor", "first_gpu_service")
    cache.tree_core.dec_lock_ref.assert_not_called()
    runtime._release_prefetch_service_lease("leaf", "first_gpu_service")
    cache.tree_core.dec_lock_ref.assert_called_once_with(2, receipt)
    runtime.close()


@pytest.mark.parametrize("condition,expected", (
    ("covered", "covered_by_existing_lock"),
    ("occupied", "frontier_slot_occupied"),
    ("bytes", "byte_budget_exceeded"),
    ("no_slot", "no_frontier_slot"),
    ("unobservable", "closure_unobservable"),
))
def test_restore_reports_unprotected_capacity_without_changing_native_admission(
    monkeypatch, condition, expected,
):
    runtime, (cold, warm, _), cache = runtime_with_requests(monkeypatch)
    key = runtime.visible[cold.rid]
    node = NS(id=1, creation_time=2, parent=None, component_data={
        0: NS(value=object()), 2: NS(value=object()),
    })
    cache.tree_core = NS(
        node_by_id=lambda _: node, inc_lock_ref=Mock(), dec_lock_ref=Mock(),
    )
    cache.session_refs = NS(
        _session_generations={key.session_id: key.session_generation},
        _closed_session_ids=set(),
    )
    cache.host_pool_group = NS(entry_map={
        "kv": NS(host_pool=NS(size_per_token=10)),
        "mamba": NS(host_pool=NS(size_per_token=20)),
    })
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    step = PrefetchLoadStep(key, 1, 2, 1, 2)
    action = PhysicalActionCompleted(
        "restore", "PREFETCH_GPU", key.context_id, key.context_epoch,
        (1,), (("kv", 1000),), 1000,
    )
    runtime._prefetch_steps["restore"] = (step, "execution_handoff", None)
    if condition in ("covered", "occupied"):
        existing_key = key if condition == "covered" else runtime.visible[warm.rid]
        runtime._prefetch_service_leases["prior"] = _PrefetchServiceLease(
            existing_key, "prior", 1, 2, "execution_handoff", None,
            0., float("inf"), (("kv", 1000),),
            lock_params=NS(node_id=1), protected_bytes=1020, demand_ready=True,
        )
    with patch.object(
        runtime, "_current_residency_budget",
        return_value=PrefetchResidencyBudget(
            0 if condition == "no_slot" else 1,
            500 if condition == "bytes" else 1024 ** 3,
            source="native_next_prefill",
        ),
    ), patch(
        "beliefkv.runtime.sglang_v0520_observer.observe_unified_node_closure",
        return_value=NS(observable=condition != "unobservable", nodes=(
            NS(node_id=1, creation_time=2, full_device_tokens=100, mamba_device_present=True),
        )),
    ):
        runtime._register_prefetch_service_lease(action)
    cache.tree_core.inc_lock_ref.assert_not_called()
    assert records[-1]["event"] == "prefetch_residency_registered"
    assert records[-1]["native_locked"] is False
    assert records[-1]["protection_reason"] == expected
    assert records[-1]["protected_by_command_id"] == (
        "prior" if condition == "covered" else None
    )
    assert not runtime.defer_prefill_for_prefetch(cold)
    runtime.close()


@pytest.mark.parametrize("created", [2., np.float64(2.)])
def test_handoff_uses_native_creation_time_with_real_prefix_planner(monkeypatch, created):
    runtime, (cold, _, _), cache = runtime_with_requests(monkeypatch)
    cache.inspect_beliefkv_reentry.side_effect = None
    cache.inspect_beliefkv_reentry.return_value = {
        "component_leaves": ((0, ((1, created),)), (2, ((1, created),))),
        "reusable_input_tokens": 2, "checkpoint_tokens": 2,
        "device_checkpoint_tokens": 0, "missing_full_tokens": 2,
        "missing_mamba_slots": 1,
    }

    def summary(node_id, parent_id, time, *, tokens=0, state=False):
        return UnifiedNodeSummary(
            node_id, parent_id, time, 0, tokens, False, state,
            0, 0, 0, 0, 1, 1, 1, 1, None, None, key_tokens=tokens,
        )

    closure = NS(
        observable=True, nodes=(summary(1, 0, 2., tokens=2, state=True),
                                summary(0, None, 0.)),
    )
    with patch(
        "beliefkv.runtime.sglang_v0520_physical.observe_static_full_mamba_headroom",
        return_value=StaticPoolHeadroomObservation(
            True, device_full_free_tokens=100, device_mamba_free_slots=4,
        ),
    ), patch(
        "beliefkv.runtime.sglang_v0520_physical.observe_unified_node_closure",
        return_value=closure,
    ), patch.object(runtime, "issue_prefetch_gpu_step", return_value="restore") as issue:
        runtime.dispatch_execution_handoff([cold], running_batch=NS(reqs=[NS()]))
    issue.assert_called_once()
    step = issue.call_args.args[0]
    assert step.node_id == 1
    assert type(step.leaf_creation_time) is float
    assert step.leaf_creation_time == created
    assert runtime.counts["execution_handoff_issued"] == 1
    assert runtime.counts["execution_handoff_closed:resident_or_unavailable"] == 0


def test_handoff_no_step_reports_specific_physical_rejection(monkeypatch):
    runtime, (cold, _, _), _ = runtime_with_requests(monkeypatch)
    records = []
    runtime._opportunity_writer = NS(record=records.append)
    with patch(
        "beliefkv.runtime.sglang_v0520_runtime.inspect_session_h2d_opportunity",
        return_value=NS(
            step=None, no_step_reason="closure_unobservable",
            blocked_detail="input_checkpoint_unavailable",
        ),
    ):
        runtime.dispatch_execution_handoff([cold], running_batch=None)
    assert runtime.counts["execution_handoff_no_step:closure_unobservable"] == 1
    rejected = next(row for row in records if row["event"] == "execution_handoff_no_step")
    assert rejected["blocked_detail"] == "input_checkpoint_unavailable"


def test_handoff_capacity_requires_actual_reclaim_and_is_rechecked(monkeypatch):
    runtime, (cold, warm, _), cache = runtime_with_requests(monkeypatch)
    key = runtime.visible["cold"]
    step = PrefetchLoadStep(key, 1, 2, 1, 2)
    shortage = NS(step=step, fits_current_free_lists=False,
                  required_full_tokens=2, required_mamba_slots=1,
                  candidate=NS(nodes=(NS(node_id=1), NS(node_id=9))))
    ready = NS(step=step, fits_current_free_lists=True)
    cache.reclaim_beliefkv_handoff_capacity = Mock(return_value={0: 2})
    with patch("beliefkv.runtime.sglang_v0520_runtime.inspect_session_h2d_opportunity",
               side_effect=[shortage, ready]), \
         patch.object(runtime, "_publish_parent_pressure_candidates") as publish, \
         patch.object(runtime, "issue_prefetch_gpu_step", return_value="load") as issue:
        runtime.dispatch_execution_handoff([cold, warm], running_batch=None)
    cache.reclaim_beliefkv_handoff_capacity.assert_called_once_with(
        full_tokens=2, mamba_slots=1, exclude_node_ids=(1, 9),
    )
    publish.assert_called_once_with(exclude_node_ids=(1, 9))
    assert issue.call_count == 1
    assert runtime.counts["execution_handoff_cold_reclaimed"] == 1


def test_handoff_h2d_is_not_serialized_behind_unrelated_d2h(monkeypatch):
    runtime, (cold, _, _), _ = runtime_with_requests(monkeypatch)
    step = PrefetchLoadStep(runtime.visible["cold"], 1, 2, 1, 2)
    with patch.object(runtime.physical_ledger, "pending_action_count",
                      side_effect=lambda action: int(action == "PREPARE_HOST")), \
         patch("beliefkv.runtime.sglang_v0520_runtime.inspect_session_h2d_opportunity",
               return_value=NS(step=step, fits_current_free_lists=True)), \
         patch.object(runtime, "issue_prefetch_gpu_step", return_value="h2d") as issue:
        runtime.dispatch_execution_handoff([cold], running_batch=None)
    issue.assert_called_once_with(step, source="execution_handoff")


def test_handoff_cannot_reclaim_hot_capacity_or_repeat_failed_ticket(monkeypatch):
    runtime, (cold, _, _), cache = runtime_with_requests(monkeypatch)
    step = PrefetchLoadStep(runtime.visible["cold"], 1, 2, 1, 2)
    shortage = NS(step=step, fits_current_free_lists=False,
                  required_full_tokens=2, required_mamba_slots=1)
    cache.reclaim_beliefkv_handoff_capacity = Mock(return_value={})
    with patch("beliefkv.runtime.sglang_v0520_runtime.inspect_session_h2d_opportunity",
               return_value=shortage), \
         patch.object(runtime, "_publish_parent_pressure_candidates"), \
         patch.object(runtime, "issue_prefetch_gpu_step") as issue:
        runtime.dispatch_execution_handoff([cold], running_batch=None)
        issue.assert_not_called()
        assert runtime._execution_handoff is None
        assert not runtime.defer_prefill_for_prefetch(cold)
        runtime._execution_handoff_next_ms = 0.
        runtime.dispatch_execution_handoff([cold], running_batch=None)
    assert runtime._execution_handoff is None
    assert runtime.counts["execution_handoff_selected"] == 1


def test_handoff_cancelled_request_never_loads_stale_epoch(monkeypatch):
    runtime, (cold, _, _), _ = runtime_with_requests(monkeypatch)
    step = PrefetchLoadStep(runtime.visible["cold"], 1, 2, 1, 2)
    with patch("beliefkv.runtime.sglang_v0520_runtime.inspect_session_h2d_opportunity",
               return_value=NS(step=step, fits_current_free_lists=True)), \
         patch.object(runtime, "issue_prefetch_gpu_step", return_value="load"):
        runtime.dispatch_execution_handoff([cold], running_batch=None)
    runtime.graph.contexts["ctx-cold"].epoch += 1
    assert runtime.refreshed_prefetch_gpu_step(source="execution_handoff") is None
    assert not runtime.defer_prefill_for_prefetch(cold)


def test_handoff_and_resident_first_can_be_disabled(monkeypatch):
    runtime, (cold, warm, _), _ = runtime_with_requests(monkeypatch)
    runtime.enable_execution_handoff = False
    runtime.enable_resident_first = False
    assert select(runtime, [cold, warm]).candidates == (cold, warm)
    runtime.dispatch_execution_handoff([cold, warm], running_batch=None)
    assert runtime._execution_handoff is None


def test_verified_ack_keeps_logical_handoff_source_for_metrics(monkeypatch):
    runtime, (cold, _, _), _ = runtime_with_requests(monkeypatch)
    step = PrefetchLoadStep(runtime.visible["cold"], 1, 2, 1, 2)
    runtime._prefetch_steps["load"] = (step, "execution_handoff", 1.)
    action = PhysicalActionCompleted("load", "PREFETCH_GPU", "ctx-cold", 0,
                                     (1,), (("kv", 100),), 100)
    with patch.object(runtime.physical_ledger, "observe", return_value=(action,)), \
         patch.object(runtime, "_register_prefetch_service_lease"):
        completed = runtime.on_native_transfer_commit(NS())
    assert completed[0].source == "execution_handoff"
    assert runtime.completed_physical_actions[-1].source == "execution_handoff"


def test_handoff_burst_reserves_each_extent_and_uses_native_pipeline(monkeypatch):
    runtime, (cold, _, _), cache = runtime_with_requests(monkeypatch)
    key = runtime.visible["cold"]
    steps = (
        PrefetchLoadStep(key, 2, 3, 1, 2, False),
        PrefetchLoadStep(key, 2, 3, 2, 3, True),
    )
    cache.cache_controller = NS(
        mem_pool_host=NS(entry_map={
            "kv": NS(host_pool=NS(size_per_token=10)),
            "mamba": NS(host_pool=NS(size_per_token=20)),
        }),
        _num_tokens_by_pool=lambda op: {
            "kv": len(op.host_indices),
            **({"mamba": 1} if op.pool_transfers else {}),
        },
        _transfer_num_bytes=lambda op: len(op.host_indices) * 10 + (20 if op.pool_transfers else 0),
    )

    def native_burst(**kwargs):
        outcomes = []
        for node, created, include_state, command in kwargs["nodes"]:
            op = NS(
                beliefkv_command_id=command, node_ids=[node],
                host_indices=(1, 2), device_indices=(3, 4),
                pool_transfers=[NS(
                    name="mamba", indices_from_pool=None,
                    host_indices=(10,), device_indices=(11,),
                )] if include_state else None,
            )
            assert kwargs["beliefkv_before_enqueue"](op)
            outcomes.append(NS(issued=True, node_id=node, load_started=True))
        return tuple(outcomes)

    cache.prefetch_gpu_session_nodes = Mock(side_effect=native_burst)
    cache.beliefkv_prefetch_can_admit = Mock(return_value=True)
    cache.supports_beliefkv_overlap_prefetch = lambda: True
    notified = []
    runtime.prefetch_issued_callback = notified.append
    with patch(
        "beliefkv.runtime.sglang_v0520_runtime.inspect_session_h2d_opportunity",
        return_value=NS(step=steps[0], steps=steps, fits_current_free_lists=True),
    ), patch.object(runtime, "refreshed_prefetch_gpu_step") as expensive_refresh:
        runtime.dispatch_execution_handoff([cold], running_batch=None)
    expensive_refresh.assert_not_called()
    assert runtime.physical_ledger.pending_action_count("PREFETCH_GPU") == 2
    assert runtime._execution_handoff.issued_nodes == 2
    assert len(notified) == 2
    assert [dict(item.pool_bytes).get("mamba", 0) for item in notified] == [0, 20]
    assert not runtime.defer_prefill_for_prefetch(cold)
    cache.beliefkv_prefetch_can_admit.return_value = False
    assert runtime.defer_prefill_for_prefetch(cold)
    runtime.on_batch_completed(NS(reqs=[cold]))
    assert len(runtime._prefetch_first_services) == 2


def test_join_prefetch_checks_state_before_cow_across_submit_epoch(monkeypatch):
    runtime, (cold, _, _), cache = runtime_with_requests(monkeypatch)
    key = runtime.visible["cold"]
    step = PrefetchLoadStep(key, 1, 2, 1, 2)
    runtime._prefetch_steps["join"] = (step, "join_ticket", 0.)
    cache.beliefkv_prefetch_can_admit = Mock(return_value=False)
    with patch.object(runtime.physical_ledger, "is_pending", return_value=True):
        cold.beliefkv_metadata["context_epoch"] += 1
        assert runtime.defer_prefill_for_prefetch(cold)
        cache.beliefkv_prefetch_can_admit.return_value = True
        assert not runtime.defer_prefill_for_prefetch(cold)
        cold.session_generation += 1
        assert not runtime.defer_prefill_for_prefetch(cold)
    assert cache.beliefkv_prefetch_can_admit.call_count == 2


def test_service_before_software_ack_does_not_acquire_a_late_residency_lock(monkeypatch):
    runtime, (cold, _, _), _ = runtime_with_requests(monkeypatch)
    key = runtime.visible["cold"]
    runtime._prefetch_steps["load"] = (PrefetchLoadStep(key, 1, 2, 1, 2), "execution_handoff", 1.)
    runtime.on_batch_completed(NS(reqs=[cold]))
    action = PhysicalActionCompleted(
        "load", "PREFETCH_GPU", key.context_id, key.context_epoch, (1,), (("kv", 100),), 100,
    )
    runtime._register_prefetch_service_lease(action)
    assert not runtime._prefetch_service_leases
    assert runtime.counts["prefetch_service_preceded_ledger_ack"] == 1
    assert not runtime._prefetch_first_services


def test_unacknowledged_tracking_is_discarded_on_physical_failure(monkeypatch):
    runtime, (cold, _, _), _ = runtime_with_requests(monkeypatch)
    command = "load"
    runtime._prefetch_steps[command] = (
        PrefetchLoadStep(runtime.visible["cold"], 1, 2, 1, 2), "execution_handoff", 1.,
    )
    runtime._prefetch_issue_times[command] = 1.
    runtime._prefetch_first_services[command] = (2., cold.rid)
    runtime.prefetch_discarded_callback = Mock()
    with patch.object(runtime.physical_ledger, "observe", side_effect=PhysicalReceiptError("invalid")):
        assert runtime.on_native_transfer_commit(NS()) == ()
    runtime.prefetch_discarded_callback.assert_called_once_with(command, "physical_receipt_failure")
    assert not runtime._prefetch_steps
    assert not runtime._prefetch_issue_times
    assert not runtime._prefetch_first_services


def test_burst_deepest_lock_covers_ancestors_and_releases_once_on_service(monkeypatch):
    runtime, (cold, _, _), cache = runtime_with_requests(monkeypatch)
    key = runtime.visible[cold.rid]
    nodes = {
        0: NS(id=0, creation_time=0, parent=None),
    }
    for node_id in range(1, 7):
        nodes[node_id] = NS(
            id=node_id, creation_time=node_id + 1, parent=nodes[node_id - 1],
            component_data=(NS(value=object()), None, NS(value=object())),
        )
    cache.session_refs = NS(_session_generations={key.session_id: key.session_generation})
    cache.tree_core = NS(
        node_by_id=nodes.__getitem__,
        inc_lock_ref=Mock(side_effect=lambda node: NS(
            to_dec_params=lambda: NS(node_id=node),
        )),
        dec_lock_ref=Mock(),
    )
    cache.host_pool_group = NS(entry_map={
        "kv": NS(host_pool=NS(size_per_token=10)),
        "mamba": NS(host_pool=NS(size_per_token=20)),
    })
    actions = []
    for node_id in range(1, 7):
        command = f"load-{node_id}"
        runtime._prefetch_steps[command] = (
            PrefetchLoadStep(key, 6, 7, node_id, node_id + 1, node_id == 6),
            "execution_handoff", 1.,
        )
        actions.append(PhysicalActionCompleted(
            command, "PREFETCH_GPU", key.context_id, key.context_epoch,
            (node_id,), (("kv", 10),), 10, "execution_handoff",
        ))
    with patch.object(runtime.physical_ledger, "observe", return_value=tuple(actions)), patch(
        "beliefkv.runtime.sglang_v0520_observer.observe_unified_node_closure",
        side_effect=lambda _, node, **kw: NS(observable=True, nodes=tuple(
                NS(node_id=index, creation_time=nodes[index].creation_time,
                   full_device_tokens=1, mamba_device_present=index == 6)
            for index in range(1, node + 1)
        )),
    ):
        runtime.on_native_transfer_commit(NS())
    cache.tree_core.inc_lock_ref.assert_called_once_with(6)
    assert len(runtime._prefetch_service_leases) == 6
    assert sum(item.protected_bytes for item in runtime._prefetch_service_leases.values()) == 80
    runtime.on_batch_completed(NS(reqs=[cold]))
    cache.tree_core.dec_lock_ref.assert_called_once()
    assert cache.tree_core.dec_lock_ref.call_args.args[0] == 6
    assert not runtime._prefetch_service_leases


def test_handoff_and_admission_share_causal_classification_but_not_residency(monkeypatch):
    runtime, (cold, warm, _), cache = runtime_with_requests(monkeypatch)
    runtime._prefill_cycle_active = True
    with patch.object(runtime.frontier, "admission_rank", wraps=runtime.frontier.admission_rank) as rank:
        first = runtime.plan_native_prefill([cold, warm], running_batch=None, adder=None)
        second = runtime.plan_native_prefill([cold, warm], running_batch=None, adder=NS())
        assert first == second
        assert rank.call_count == 2
        assert runtime.counts["admission_causal_cache_hits"] == 1
        runtime.graph._graph_version += 1
        runtime.plan_native_prefill([cold, warm], running_batch=None, adder=None)
        assert rank.call_count == 4
        runtime.plan_native_prefill([warm, cold], running_batch=None, adder=None)
        assert rank.call_count == 6
    assert cache.inspect_beliefkv_reentry.call_count == 2


def test_handoff_and_admission_keep_scan_targets_then_rotate_next_iteration(monkeypatch):
    runtime, requests, cache = runtime_with_requests(monkeypatch)
    runtime.graph.invocations["parent"].pending_messages = 0
    runtime._residency_scan_cursor = 8
    clock = [time.monotonic()]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    for index in range(3, 32):
        request = req(f"queued-{index}")
        request.session_id, request.session_generation = f"s-{request.rid}", 1
        runtime.register_visible_request(request)
        runtime.on_events((RuntimeEvent(
            f"create-{index}", float(index), RuntimeEventKind.INVOCATION_CREATE, "wf",
            invocation_id=request.rid, context_id=f"ctx-{request.rid}",
            agent_definition_id="agent", agent_instance_id=request.rid,
        ),))
        requests.append(request)
    warm = requests[18]
    cache.inspect_beliefkv_reentry.side_effect = lambda request: {
        "checkpoint_tokens": 2,
        "device_checkpoint_tokens": 2 if request is warm else 0,
        "missing_full_tokens": 0 if request is warm else 2,
        "missing_mamba_slots": int(request is not warm),
    }
    runtime.scheduler_step(requests)
    assert select(runtime, requests).candidates == tuple(requests)
    assert select(runtime, requests).candidates == tuple(requests)
    assert cache.inspect_beliefkv_reentry.call_count == 16
    assert runtime._residency_scan_cursor == 16
    clock[0] += .06
    runtime.scheduler_step(requests)
    assert select(runtime, requests).candidates[0] is warm
    assert cache.inspect_beliefkv_reentry.call_count == 32
    assert runtime._residency_scan_cursor == 24
    runtime.close()


@pytest.mark.parametrize("slots, expected_targets", ((0, 0), (1, 9), (3, 11), (8, 16), (16, 16)))
def test_residency_probes_follow_imminent_slots_and_rotate_without_caching_decisions(
    monkeypatch, slots, expected_targets,
):
    runtime, (cold, warm, _), cache = runtime_with_requests(monkeypatch)
    runtime._prefill_cycle_active = True
    runtime._residency_scan_cursor = 8
    ordered = [(index, cold) for index in range(32)]
    ordered[8] = (8, warm)
    keys = {index: runtime.visible[request.rid] for index, request in ordered}
    with patch.object(runtime, "_current_residency_budget", return_value=PrefetchResidencyBudget(
        slots, 1024 ** 3, source="native_next_prefill",
    )):
        first = runtime._resident_prefill_scan(ordered, keys)
        assert len(first) == expected_targets
        assert runtime._resident_prefill_scan(ordered, keys) is first
        assert runtime._residency_scan_cursor == (16 if slots else 8)
        assert runtime.counts["admission_residency_scan_targets"] == expected_targets
        assert runtime.counts["admission_residency_scan_reused"] == 1
        if slots:
            assert (8, warm) in first
        runtime.scheduler_step()
        second = runtime._resident_prefill_scan(ordered, keys)
        assert len(second) == expected_targets
        assert runtime._residency_scan_cursor == (24 if slots else 8)
    cache.inspect_beliefkv_reentry.assert_not_called()
    runtime.close()


def test_residency_scan_rechecks_slot_changes_within_one_cycle(monkeypatch):
    runtime, (cold, warm, parent), _ = runtime_with_requests(monkeypatch)
    runtime._prefill_cycle_active = True
    runtime._residency_scan_cursor = 8
    ordered = [(index, cold) for index in range(32)]
    ordered[2], ordered[18] = (2, warm), (18, parent)
    keys = {index: runtime.visible[request.rid] for index, request in ordered}
    budgets = [
        PrefetchResidencyBudget(slots, 1024 ** 3, source="native_next_prefill")
        for slots in (1, 3)
    ]
    with patch.object(runtime, "_current_residency_budget", side_effect=budgets):
        assert len(runtime._resident_prefill_scan(ordered, keys)) == 9
        expanded = runtime._resident_prefill_scan(ordered, keys)
        assert len(expanded) == 11
        assert (2, warm) in expanded and (18, parent) in expanded
    assert runtime.counts["admission_residency_scan_reused"] == 0
    runtime.close()


@pytest.mark.parametrize("source", ("native_next_prefill", "fallback"))
def test_full_width_residency_scan_retains_existing_rotation_and_read_counts(
    monkeypatch, source,
):
    runtime, (cold, _, _), _ = runtime_with_requests(monkeypatch)
    ordered = [(index, cold) for index in range(32)]
    keys = {index: runtime.visible[cold.rid] for index in range(32)}
    with patch.object(runtime, "_current_residency_budget", return_value=PrefetchResidencyBudget(
        8, 1024 ** 3, source=source,
    )):
        runtime._residency_scan_cursor = 0
        first = runtime._resident_prefill_scan(ordered, keys)
        assert first == ordered[:8]
        assert runtime._residency_scan_cursor == 8
        second = runtime._resident_prefill_scan(ordered, keys)
        assert second == ordered[:16]
        assert runtime._residency_scan_cursor == 16
    runtime.close()


def test_slot_bounded_scan_covers_small_queue_and_eventually_the_entire_tail(monkeypatch):
    runtime, (cold, warm, _), _ = runtime_with_requests(monkeypatch)
    with patch.object(runtime, "_current_residency_budget", return_value=PrefetchResidencyBudget(
        1, 1024 ** 3, source="native_next_prefill",
    )):
        short = [(0, cold), (1, warm)]
        keys = {index: runtime.visible[request.rid] for index, request in short}
        assert runtime._resident_prefill_scan(short, keys) == short
        ordered = [(index, cold) for index in range(32)]
        keys = {index: runtime.visible[cold.rid] for index in range(32)}
        runtime._residency_scan_cursor = 0
        observed = set()
        for _ in range(4):
            scan = runtime._resident_prefill_scan(ordered, keys)
            assert scan[0] == ordered[0] and len(scan) == 9
            assert len({index for index, _ in scan}) == 9
            observed.update(index for index, _ in scan)
        assert observed == set(range(32))
    runtime.close()


def test_slot_bounded_sort_still_rechecks_selected_handoff_and_aging(monkeypatch):
    runtime, (cold, warm, parent), cache = runtime_with_requests(monkeypatch)
    runtime._residency_scan_cursor = 2
    with patch.object(runtime, "_current_residency_budget", return_value=PrefetchResidencyBudget(
        1, 1024 ** 3, source="native_next_prefill",
    )):
        assert select(runtime, [cold, warm, parent]).candidates == (warm, cold, parent)
        assert [call.args[0].rid for call in cache.inspect_beliefkv_reentry.call_args_list] == [
            cold.rid, parent.rid, warm.rid,
        ]
        assert runtime._request_reentry_anchors(warm) is not None
        assert cache.inspect_beliefkv_reentry.call_args.args == (warm,)
        assert select(runtime, [cold, warm, parent]).candidates[0] is warm
        runtime._visible_since[cold.rid] -= 11.
        assert select(runtime, [cold, warm, parent]).candidates[0] is cold
    runtime.close()


def test_shared_scan_refreshes_after_physical_change_and_queue_reorder(monkeypatch):
    runtime, (cold, warm, _), cache = runtime_with_requests(monkeypatch)
    runtime.scheduler_step([cold, warm])
    assert select(runtime, [cold, warm]).candidates == (warm, cold)
    cache.inspect_beliefkv_reentry.side_effect = lambda request: {
        "checkpoint_tokens": 2,
        "device_checkpoint_tokens": 2 if request is cold else 0,
        "missing_full_tokens": 0 if request is cold else 2,
        "missing_mamba_slots": int(request is not cold),
    }
    runtime._reentry_observations.clear()
    assert select(runtime, [cold, warm]).candidates == (cold, warm)
    assert cache.inspect_beliefkv_reentry.call_count == 4
    assert runtime.counts["admission_residency_scan_reused"] == 1
    assert select(runtime, [warm, cold]).candidates == (cold, warm)
    assert runtime.counts["admission_residency_scan_reused"] == 1
    runtime._visible_since[warm.rid] -= 11.
    assert select(runtime, [cold, warm]).candidates == (warm, cold)
    runtime.close()


def test_unchanged_scheduler_iterations_keep_ranks_but_refresh_residency_and_aging(monkeypatch):
    runtime, (cold, warm, _), cache = runtime_with_requests(monkeypatch)
    clock = [time.monotonic()]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    with patch.object(runtime.frontier, "admission_rank", wraps=runtime.frontier.admission_rank) as rank:
        runtime.scheduler_step([cold, warm])
        assert select(runtime, [cold, warm]).candidates == (warm, cold)
        assert rank.call_count == 2
        cache.inspect_beliefkv_reentry.side_effect = lambda request: {
            "checkpoint_tokens": 2, "device_checkpoint_tokens": 2,
            "missing_full_tokens": 0, "missing_mamba_slots": 0,
        }
        clock[0] += .06
        runtime.scheduler_step([cold, warm])
        assert select(runtime, [cold, warm]).candidates == (cold, warm)
        assert rank.call_count == 2
        assert cache.inspect_beliefkv_reentry.call_count == 4
        cache.inspect_beliefkv_reentry.side_effect = lambda request: {
            "checkpoint_tokens": 2,
            "device_checkpoint_tokens": 2 if request is warm else 0,
            "missing_full_tokens": 0 if request is warm else 2,
            "missing_mamba_slots": int(request is not warm),
        }
        runtime._visible_since[cold.rid] = clock[0] - 11.
        clock[0] += .06
        runtime.scheduler_step([cold, warm])
        assert select(runtime, [cold, warm]).candidates == (cold, warm)
        assert rank.call_count == 2
        # The one younger request cannot change the remaining arrival order.
        assert cache.inspect_beliefkv_reentry.call_count == 4
        assert runtime._read_request_reentry(warm)["missing_full_tokens"] == 0
        assert cache.inspect_beliefkv_reentry.call_count == 5
        assert runtime.counts["admission_causal_cache_hits"] == 2
    runtime.close()


def test_cross_iteration_causal_cache_invalidates_on_graph_event(monkeypatch):
    runtime, (cold, warm, _), _ = runtime_with_requests(monkeypatch)
    with patch.object(runtime.frontier, "admission_rank", wraps=runtime.frontier.admission_rank) as rank:
        runtime.scheduler_step([cold, warm])
        assert select(runtime, [cold, warm]).candidates == (warm, cold)
        runtime.on_events((RuntimeEvent(
            "cancel-cold", 10., RuntimeEventKind.INVOCATION_CANCEL, "wf",
            invocation_id=cold.rid, context_id="ctx-cold", context_epoch=0,
        ),))
        runtime.scheduler_step([cold, warm])
        assert select(runtime, [cold, warm]).candidates == (warm,)
        assert rank.call_count == 3
        assert runtime.counts["admission_causal_cache_hits"] == 0
    runtime.close()


@pytest.mark.parametrize("kind", (
    RuntimeEventKind.STRUCTURED_ACTION,
    RuntimeEventKind.TOOL_WAIT_OBSERVATION,
    RuntimeEventKind.CALL_CENSORED,
))
def test_observation_only_event_keeps_ranks_but_reads_current_residency(monkeypatch, kind):
    runtime, (cold, warm, _), cache = runtime_with_requests(monkeypatch)
    clock = [time.monotonic()]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    with patch.object(runtime.frontier, "admission_rank", wraps=runtime.frontier.admission_rank) as rank:
        runtime.scheduler_step([cold, warm])
        assert select(runtime, [cold, warm]).candidates == (warm, cold)
        version, revision = runtime.graph.graph_version, runtime.semantic_revision
        assert rank.call_count == 2
        runtime.on_events((RuntimeEvent(
            "observation", 10., kind, "wf",
            invocation_id=cold.rid, context_id="ctx-cold", context_epoch=0,
        ),))
        assert runtime.graph.graph_version == version + 1
        assert runtime.semantic_revision == revision + 1
        cache.inspect_beliefkv_reentry.side_effect = lambda request: {
            "checkpoint_tokens": 2,
            "device_checkpoint_tokens": 2 if request is cold else 0,
            "missing_full_tokens": 0 if request is cold else 2,
            "missing_mamba_slots": int(request is not cold),
        }
        clock[0] += .06
        runtime.scheduler_step([cold, warm])
        assert select(runtime, [cold, warm]).candidates == (cold, warm)
        assert rank.call_count == 2
        assert cache.inspect_beliefkv_reentry.call_count == 4
        assert runtime.counts["admission_causal_cache_preserved_observation"] == 1
    runtime.close()


def test_observation_mixed_with_cancellation_still_invalidates_classification(monkeypatch):
    runtime, (cold, warm, _), _ = runtime_with_requests(monkeypatch)
    with patch.object(runtime.frontier, "admission_rank", wraps=runtime.frontier.admission_rank) as rank:
        runtime.scheduler_step([cold, warm])
        assert select(runtime, [cold, warm]).candidates == (warm, cold)
        runtime.on_events((
            RuntimeEvent(
                "observation", 10., RuntimeEventKind.STRUCTURED_ACTION, "wf",
                invocation_id=warm.rid, context_id="ctx-warm", context_epoch=0,
            ),
            RuntimeEvent(
                "cancel-cold", 11., RuntimeEventKind.INVOCATION_CANCEL, "wf",
                invocation_id=cold.rid, context_id="ctx-cold", context_epoch=0,
            ),
        ))
        runtime.scheduler_step([cold, warm])
        assert select(runtime, [cold, warm]).candidates == (warm,)
        assert rank.call_count == 3
        assert runtime.counts["admission_causal_cache_preserved_observation"] == 0
    runtime.close()


def test_cross_iteration_causal_cache_does_not_preserve_expired_demand_order(monkeypatch):
    from beliefkv.runtime.sglang_v0520_prediction import NativeDemandHint

    runtime, (cold, warm, _), _ = runtime_with_requests(monkeypatch)
    runtime.enable_resident_first = False
    clock = [time.monotonic()]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    now_ms = clock[0] * 1000.
    runtime.demand_hints = {
        request.rid: NativeDemandHint(
            runtime.visible[request.rid], tokens, now_ms, now_ms + lifetime, "a" * 64,
        )
        for request, tokens, lifetime in ((cold, 100, 5000.), (warm, 1, 50.))
    }
    with patch.object(runtime.frontier, "admission_rank", wraps=runtime.frontier.admission_rank) as rank:
        runtime.scheduler_step([cold, warm])
        assert select(runtime, [cold, warm]).candidates == (warm, cold)
        clock[0] += .06
        runtime.scheduler_step([cold, warm])
        assert select(runtime, [cold, warm]).candidates == (cold, warm)
        assert rank.call_count == 2
        assert runtime.counts["admission_causal_cache_hits"] == 1
    runtime.close()


def test_cross_iteration_causal_cache_still_submits_changed_prediction_inputs(monkeypatch):
    runtime, (cold, warm, _), _ = runtime_with_requests(monkeypatch)
    runtime._model_worker = NS(disabled=False, poll=lambda: (), submit=Mock(), close=lambda: None)
    runtime._tool_timing_only = False
    with patch.object(runtime.frontier, "admission_rank", wraps=runtime.frontier.admission_rank) as rank:
        runtime.scheduler_step([cold, warm])
        select(runtime, [cold, warm])
        cold.output_ids = [17]
        runtime.scheduler_step([cold, warm])
        select(runtime, [cold, warm])
        assert rank.call_count == 2
        assert runtime.counts["admission_causal_cache_hits"] == 1
        assert runtime._model_worker.submit.call_count == 2
        tasks = runtime._model_worker.submit.call_args.args[0]
        assert next(features.generated_tokens for key, features, _ in tasks
                    if key.request_id == cold.rid) == 1
    runtime.close()


@pytest.mark.parametrize("change", ("order", "object", "identity"))
def test_cross_iteration_causal_cache_checks_queue_objects_order_and_identity(monkeypatch, change):
    runtime, (cold, warm, _), _ = runtime_with_requests(monkeypatch)
    with patch.object(runtime.frontier, "admission_rank", wraps=runtime.frontier.admission_rank) as rank:
        runtime.scheduler_step([cold, warm])
        select(runtime, [cold, warm])
        queue = [cold, warm]
        if change == "order":
            queue.reverse()
        elif change == "object":
            queue[0] = NS(**vars(cold))
        else:
            cold.beliefkv_metadata = {
                **cold.beliefkv_metadata, "context_epoch": 1,
            }
        runtime.scheduler_step(queue)
        select(runtime, queue)
        assert rank.call_count == (3 if change == "identity" else 4)
        assert runtime.counts["admission_causal_cache_hits"] == 0
    runtime.close()
