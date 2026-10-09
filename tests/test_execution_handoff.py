from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np
import pytest

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from beliefkv.runtime.sglang_v0520_physical import (
    PrefetchLoadStep, PhysicalActionCompleted, PhysicalReceiptError,
)
from beliefkv.runtime.sglang_v0520_observer import (
    StaticPoolHeadroomObservation, UnifiedNodeSummary,
)
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
            NS(node_id=index, full_device_tokens=1, mamba_device_present=index == 6)
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
