from __future__ import annotations

import json

from beliefkv.control.controller import BeliefKVController
from beliefkv.core.config import BeliefKVConfig
from beliefkv.policy.lead_budget import PredictiveLeadBudgetModel
from beliefkv.runtime.protocol import (
    CommandKind,
    CommandQueueClass,
    ControlCommand,
    PageHandle,
    PhysicalResidency,
)


def test_pending_transfer_conflicts_are_closure_local() -> None:
    controller = BeliefKVController(
        BeliefKVConfig(
            hbm_capacity_bytes=1_000,
            host_capacity_bytes=1_000,
            reserve_hbm_bytes=0,
            predictor_enabled=False,
            shadow_enabled=False,
        )
    )
    controller.page_index.register_context("ctx", "wf", 0)
    handle = PageHandle(1, 0)
    controller.page_index.register_page(
        handle,
        size_bytes=100,
        residency=PhysicalResidency.GPU_ONLY,
        radix_depth=1,
    )
    controller.page_index.bind_pages("ctx", 0, (handle,))
    queued = ControlCommand(
        command_id="queued",
        kind=CommandKind.OFFLOAD_CONTEXT,
        created_ts_ms=1.0,
        context_id="ctx",
        context_epoch=0,
        target_bytes=100,
        target_handles=(handle,),
        queue_class=CommandQueueClass.URGENT,
    )
    assert controller.enqueue_control_command(queued).status.value == "enqueued"

    assert controller.pending_transfer_conflicts(
        context_id="ctx", handles=frozenset({PageHandle(2, 0)})
    )
    assert controller.pending_transfer_conflicts(
        context_id="other", handles=frozenset({handle})
    )
    assert not controller.pending_transfer_conflicts(
        context_id="other", handles=frozenset({PageHandle(2, 0)})
    )


def test_lead_budget_uses_offline_then_bounded_online_quantile(tmp_path) -> None:
    raw = {
        "schema_version": 1,
        "actions": {
            "prepare_dispatch": {
                "fallback_ms": 250.0,
                "offline_ms": 1000.0,
                "minimum_ms": 25.0,
                "maximum_ms": 1000.0,
            },
            "prefetch_dispatch": {
                "fallback_ms": 100.0,
                "offline_ms": 1000.0,
                "minimum_ms": 50.0,
                "maximum_ms": 1000.0,
            },
            "prefetch_service_readiness": {
                "fallback_ms": 100.0,
                "offline_ms": 1000.0,
                "minimum_ms": 50.0,
                "maximum_ms": 1000.0,
            },
        },
    }
    path = tmp_path / "lead.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    model = PredictiveLeadBudgetModel.load(path)

    assert model.prepare_control_lead_ms(fallback_ms=250.0) == 1000.0
    assert model.prefetch_desired_lead_ms(fallback_ms=100.0) == 2000.0

    for _ in range(8):
        model.observe_dispatch("prepare", 100.0)
        model.observe_dispatch("prefetch", 100.0)
        model.observe_service_readiness(75.0)

    assert model.prepare_control_lead_ms(fallback_ms=250.0) == 100.0
    assert model.prefetch_desired_lead_ms(fallback_ms=100.0) == 175.0

    for _ in range(8):
        model.observe_dispatch("prepare", 10_000.0)

    assert model.prepare_control_lead_ms(fallback_ms=250.0) == 1000.0
