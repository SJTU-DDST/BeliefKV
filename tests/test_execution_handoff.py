from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from beliefkv.runtime.sglang_v0520_physical import PrefetchLoadStep, PhysicalActionCompleted
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
    runtime, (cold, warm, parent), _ = runtime_with_requests(monkeypatch)
    runtime.graph.invocations["parent"].pending_messages = 1
    assert select(runtime, [cold, warm, parent]).candidates[0] is parent


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


def test_handoff_capacity_requires_actual_reclaim_and_is_rechecked(monkeypatch):
    runtime, (cold, warm, _), cache = runtime_with_requests(monkeypatch)
    key = runtime.visible["cold"]
    step = PrefetchLoadStep(key, 1, 2, 1, 2)
    shortage = NS(step=step, fits_current_free_lists=False,
                  required_full_tokens=2, required_mamba_slots=1)
    ready = NS(step=step, fits_current_free_lists=True)
    cache.reclaim_beliefkv_handoff_capacity = Mock(return_value={0: 2})
    with patch("beliefkv.runtime.sglang_v0520_runtime.inspect_session_h2d_opportunity",
               side_effect=[shortage, ready]), \
         patch.object(runtime, "_publish_parent_pressure_candidates"), \
         patch.object(runtime, "issue_prefetch_gpu_step", return_value="load") as issue:
        runtime.dispatch_execution_handoff([cold, warm], running_batch=None)
    cache.reclaim_beliefkv_handoff_capacity.assert_called_once_with(full_tokens=2, mamba_slots=1)
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
