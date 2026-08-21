from __future__ import annotations

from typing import Any, Mapping

from beliefkv.runtime.protocol import (
    CommandAck,
    CommandKind,
    CommandQueueClass,
    CommandStatus,
    ControlCommand,
    EnqueueStatus,
    PhysicalResidency,
)


def _update(runtime: Any, stage: str, *, now_ms: float, **fields: object) -> None:
    state = runtime._host_recompute_micro_gate_state
    state.update(fields)
    state["stage"] = stage
    state["last_update_ts_ms"] = now_ms
    signature = (
        stage,
        state.get("context_id"),
        state.get("offload_command_id"),
        state.get("host_drop_command_id"),
        state.get("reason"),
    )
    if signature == runtime._host_recompute_micro_gate_last_audit_signature:
        return
    runtime._host_recompute_micro_gate_last_audit_signature = signature
    runtime.audit.emit(
        "host_recompute_micro_gate_state",
        now_ms,
        **dict(state),
    )


def maybe_queue_host_recompute_offload(runtime: Any, *, now_ms: float) -> None:
    """Queue one production OFFLOAD_CONTEXT for a parked diagnostic context."""

    config = getattr(runtime, "config", None)
    if config is None or not getattr(
        config, "host_recompute_micro_gate_enabled", False
    ):
        return
    state = runtime._host_recompute_micro_gate_state
    if state.get("stage") not in {"armed", "waiting_for_parked_context"}:
        return
    if runtime.controller.has_pending_transfer_work():
        return
    if (
        runtime._pending_online_joint_residency is not None
        or runtime._pending_running_retraction_transaction is not None
    ):
        return

    obligations = runtime._restore_obligation_index().all()
    protected_contexts = {
        item.context_id for item in obligations if not item.state.terminal
    }
    candidates: list[tuple[str, int, int]] = []
    for context_id, context in runtime.controller.graph.contexts.items():
        if context.workflow_id != config.host_recompute_micro_gate_workflow_id:
            continue
        if context_id in protected_contexts:
            continue
        if (context_id, context.epoch) not in runtime._full_prompt_replay_contexts:
            continue
        if not runtime._host_cleanup_context_is_parked(context_id):
            continue
        gpu_bytes = sum(
            page.size_bytes
            for page in runtime.controller.page_index.context_pages(context_id)
            if page.residency == PhysicalResidency.GPU_ONLY
            and page.transfer_idle
            and page.sealed
        )
        if gpu_bytes >= config.host_recompute_micro_gate_min_gpu_bytes:
            candidates.append((context_id, context.epoch, gpu_bytes))

    if not candidates:
        _update(
            runtime,
            "waiting_for_parked_context",
            now_ms=now_ms,
            reason="no_replay_safe_parked_gpu_context",
        )
        return
    context_id, context_epoch, gpu_bytes = min(candidates)
    sequence = int(state.get("offload_sequence", 0)) + 1
    command = ControlCommand(
        command_id=f"host-recompute-gate-offload-{sequence}",
        kind=CommandKind.OFFLOAD_CONTEXT,
        created_ts_ms=now_ms,
        context_id=context_id,
        context_epoch=context_epoch,
        target_bytes=gpu_bytes,
        priority=4.0e9,
        queue_class=CommandQueueClass.URGENT,
        metadata={
            "reason": "host_recompute_micro_gate",
            "gate_id": config.host_recompute_micro_gate_id,
        },
    )
    outcome = runtime.controller.enqueue_control_command(command)
    if outcome.status != EnqueueStatus.ENQUEUED:
        _update(
            runtime,
            "waiting_for_parked_context",
            now_ms=now_ms,
            reason=f"offload_enqueue_{outcome.status.value}",
        )
        return
    _update(
        runtime,
        "offload_queued",
        now_ms=now_ms,
        reason="parked_replay_safe_context_selected",
        context_id=context_id,
        context_epoch=context_epoch,
        offload_command_id=command.command_id,
        offload_sequence=sequence,
        target_gpu_bytes=gpu_bytes,
    )


def advance_host_recompute_offload(
    runtime: Any,
    acks: tuple[CommandAck, ...] | list[CommandAck],
    *,
    now_ms: float,
) -> None:
    config = getattr(runtime, "config", None)
    if config is None or not getattr(
        config, "host_recompute_micro_gate_enabled", False
    ):
        return
    state = runtime._host_recompute_micro_gate_state
    command_id = state.get("offload_command_id")
    if not command_id:
        return
    for ack in acks:
        if ack.command_id != command_id:
            continue
        completed = ack.status == CommandStatus.COMPLETED and ack.actual_bytes > 0
        _update(
            runtime,
            "cpu_only_ready" if completed else "failed",
            now_ms=now_ms,
            reason=(
                "offload_completed"
                if completed
                else f"offload_{ack.status.value}:{ack.reason}"
            ),
            explicit_d2h_bytes=ack.actual_bytes,
        )


def observe_host_cleanup_terminal(
    runtime: Any,
    attribution: Mapping[str, object],
    ack: CommandAck,
    *,
    now_ms: float,
) -> None:
    config = getattr(runtime, "config", None)
    if config is None or not getattr(
        config, "host_recompute_micro_gate_enabled", False
    ):
        return
    state = runtime._host_recompute_micro_gate_state
    if attribution.get("context_id") != state.get("context_id"):
        return
    if attribution.get("mode") != "cpu_only_recompute":
        return
    completed = ack.status == CommandStatus.COMPLETED and ack.actual_bytes > 0
    _update(
        runtime,
        "host_dropped_recompute_required" if completed else "failed",
        now_ms=now_ms,
        reason=(
            "generation_safe_host_drop_completed"
            if completed
            else f"host_drop_{ack.status.value}:{ack.reason}"
        ),
        host_drop_command_id=ack.command_id,
        host_drop_bytes=ack.actual_bytes,
    )


def observe_recompute_service(
    runtime: Any,
    *,
    request_id: str,
    context_id: str,
    context_epoch: int,
    uncached_prompt_tokens: int,
    now_ms: float,
) -> None:
    config = getattr(runtime, "config", None)
    if config is None or not getattr(
        config, "host_recompute_micro_gate_enabled", False
    ):
        return
    state = runtime._host_recompute_micro_gate_state
    if state.get("stage") != "host_dropped_recompute_required":
        return
    if (
        context_id != state.get("context_id")
        or context_epoch != state.get("context_epoch")
    ):
        return
    _update(
        runtime,
        "completed",
        now_ms=now_ms,
        reason="native_prefill_service_observed",
        recompute_request_id=request_id,
        recompute_uncached_prompt_tokens=uncached_prompt_tokens,
    )
