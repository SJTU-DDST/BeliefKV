from __future__ import annotations

from types import SimpleNamespace

import pytest

from beliefkv.control.controller import BeliefKVController
from beliefkv.core.config import BeliefKVConfig
from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.runtime.host_recompute_gate import (
    advance_host_recompute_offload,
    maybe_queue_host_recompute_offload,
    observe_host_cleanup_terminal,
    observe_recompute_service,
)
from beliefkv.runtime.protocol import (
    CommandAck,
    CommandKind,
    CommandStatus,
    PageHandle,
    PhysicalResidency,
)
from beliefkv.runtime.sglang_v052rc1 import EmbeddedSGLangRuntime


class _Audit:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def emit(self, event: str, _ts_ms: float, **fields: object) -> None:
        self.events.append((event, fields))


def _config(**changes: object) -> BeliefKVConfig:
    values: dict[str, object] = {
        "hbm_capacity_bytes": 2_000,
        "host_capacity_bytes": 1_000,
        "reserve_hbm_bytes": 100,
        "queue_service_observer_enabled": True,
        "queue_service_observer_include_runtime_batches": True,
        "host_recompute_micro_gate_enabled": True,
        "host_recompute_micro_gate_min_gpu_bytes": 100,
    }
    values.update(changes)
    return BeliefKVConfig(**values)


def _runtime() -> EmbeddedSGLangRuntime:
    config = _config()
    controller = BeliefKVController(config)
    controller.process_runtime_events(
        (
            RuntimeEvent("start", 1.0, RuntimeEventKind.WORKFLOW_START, "wf"),
            RuntimeEvent(
                "create",
                2.0,
                RuntimeEventKind.INVOCATION_CREATE,
                "wf",
                invocation_id="inv",
                context_id="ctx",
                context_epoch=0,
            ),
            RuntimeEvent(
                "wait",
                3.0,
                RuntimeEventKind.TOOL_START,
                "wf",
                invocation_id="inv",
                context_id="ctx",
                context_epoch=0,
            ),
        )
    )
    handle = PageHandle(71, 0)
    controller.page_index.register_page(
        handle,
        size_bytes=960,
        residency=PhysicalResidency.GPU_ONLY,
    )
    controller.page_index.bind_pages("ctx", 0, (handle,))
    runtime = EmbeddedSGLangRuntime.__new__(EmbeddedSGLangRuntime)
    runtime.config = config
    runtime.controller = controller
    runtime.audit = _Audit()
    runtime._pending_online_joint_residency = None
    runtime._pending_running_retraction_transaction = None
    runtime._full_prompt_replay_contexts = {("ctx", 0)}
    runtime._restore_obligation_index = lambda: SimpleNamespace(all=lambda: ())
    runtime._host_recompute_micro_gate_state = {
        "enabled": True,
        "gate_id": config.host_recompute_micro_gate_id,
        "stage": "armed",
        "workflow_id": config.host_recompute_micro_gate_workflow_id,
    }
    runtime._host_recompute_micro_gate_last_audit_signature = None
    return runtime


def test_host_recompute_gate_requires_runtime_service_observation() -> None:
    with pytest.raises(ValueError, match="runtime GPU service observation"):
        _config(queue_service_observer_enabled=False)


def test_gate_queues_normal_offload_for_parked_replay_safe_context() -> None:
    runtime = _runtime()
    runtime.config = _config(
        host_recompute_micro_gate_workflow_id="wf",
    )

    maybe_queue_host_recompute_offload(runtime, now_ms=4.0)
    tick = runtime.controller.tick(4.0, allow_reactive_transfer=False)

    assert tick.transfer is not None
    assert tick.transfer.command.kind == CommandKind.OFFLOAD_CONTEXT
    assert tick.transfer.command.context_id == "ctx"
    assert runtime._host_recompute_micro_gate_state["stage"] == "offload_queued"


def test_gate_waits_for_engine_lock_release() -> None:
    runtime = _runtime()
    runtime.config = _config(
        host_recompute_micro_gate_workflow_id="wf",
    )
    handle = next(iter(runtime.controller.page_index.pages))
    runtime.controller.page_index.pages[handle].engine_lock_ref = 1

    maybe_queue_host_recompute_offload(runtime, now_ms=4.0)
    tick = runtime.controller.tick(4.0, allow_reactive_transfer=False)

    assert tick.transfer is None
    assert runtime._host_recompute_micro_gate_state["stage"] == "waiting_for_parked_context"


def test_gate_tracks_drop_then_real_recompute_service() -> None:
    runtime = _runtime()
    runtime._host_recompute_micro_gate_state.update(
        {
            "stage": "offload_queued",
            "context_id": "ctx",
            "context_epoch": 0,
            "offload_command_id": "offload",
        }
    )
    advance_host_recompute_offload(
        runtime,
        (
            CommandAck(
                "offload",
                CommandStatus.COMPLETED,
                5.0,
                960,
            ),
        ),
        now_ms=5.0,
    )
    assert runtime._host_recompute_micro_gate_state["stage"] == "cpu_only_ready"

    observe_host_cleanup_terminal(
        runtime,
        {"context_id": "ctx", "mode": "cpu_only_recompute"},
        CommandAck("host-drop", CommandStatus.COMPLETED, 6.0, 960),
        now_ms=6.0,
    )
    assert (
        runtime._host_recompute_micro_gate_state["stage"]
        == "host_dropped_recompute_required"
    )

    observe_recompute_service(
        runtime,
        request_id="continuation",
        context_id="ctx",
        context_epoch=0,
        uncached_prompt_tokens=512,
        now_ms=7.0,
    )
    state = runtime._host_recompute_micro_gate_state
    assert state["stage"] == "completed"
    assert state["recompute_uncached_prompt_tokens"] == 512
