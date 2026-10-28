"""Independent policy controls for the single-GPU internal comparisons."""

from __future__ import annotations

import math

MODES = ("full", "join_priority_only", "scheduler_only", "kv_only", "native_matched")
ORDER = ("join_priority_only", "scheduler_only", "kv_only", "native_matched")
SCHEDULING_ONLY = frozenset(("join_priority_only", "scheduler_only"))


def arm_environment(
    common: dict[str, str], mode: str, *, tool_prefetch: bool | None = None,
) -> dict[str, str]:
    if mode not in MODES:
        raise ValueError(f"unknown internal comparison: {mode}")
    profiling = common.get("BELIEFKV_PROFILE_SHARED_PATH", common.get("PROFILE_SHARED_PATH", "0"))
    if profiling not in ("0", "1"):
        raise ValueError("shared path profiling must be 0 or 1")
    custom_kv = mode in ("full", "kv_only")
    tool_offload = common.get(
        "BELIEFKV_ENABLE_PREDICTED_TOOL_OFFLOAD", common.get("PREDICTED_TOOL_OFFLOAD", "0"),
    )
    if tool_offload not in ("0", "1"):
        raise ValueError("predicted tool offload must be 0 or 1")
    tool_offload = tool_offload if custom_kv else "0"
    early_tool_offload = common.get(
        "BELIEFKV_TOOL_OFFLOAD_BEFORE_ADMISSION",
        common.get("TOOL_OFFLOAD_BEFORE_ADMISSION", "0"),
    )
    if early_tool_offload not in ("0", "1"):
        raise ValueError("tool offload before admission must be 0 or 1")
    early_tool_offload = early_tool_offload if mode == "full" else "0"
    layer_handoff = common.get("BELIEFKV_LAYER_HANDOFF", common.get("LAYER_HANDOFF", "0"))
    if layer_handoff not in ("0", "1"):
        raise ValueError("layer handoff must be 0 or 1")
    layer_handoff = layer_handoff if mode == "full" else "0"
    host_mirror_recycle = common.get(
        "BELIEFKV_IDLE_HOST_MIRROR_RECYCLE", common.get("IDLE_HOST_MIRROR_RECYCLE", "0"),
    )
    if host_mirror_recycle not in ("0", "1"):
        raise ValueError("idle Host mirror recycling must be 0 or 1")
    host_mirror_recycle = host_mirror_recycle if mode == "full" else "0"
    if tool_prefetch is not None and type(tool_prefetch) is not bool:
        raise ValueError("tool prefetch override must be a boolean")
    if tool_prefetch is True and not custom_kv:
        raise ValueError("tool prefetch requires custom KV management")
    protection = common.get(
        "BELIEFKV_PREFETCH_RESIDENCY_PROTECTION",
        common.get("PREFETCH_RESIDENCY_PROTECTION", "1"),
    )
    if protection not in ("0", "1"):
        raise ValueError("prefetch residency protection must be 0 or 1")
    scheduling = common.get(
        "BELIEFKV_PREFILL_SCHEDULING_POLICY",
        common.get("PREFILL_SCHEDULING_POLICY", "workflow"),
    )
    if scheduling not in ("workflow", "join_priority", "workflow_completion"):
        raise ValueError("unsupported prefill scheduling policy")
    tool_restore = common.get(
        "BELIEFKV_TOOL_RESTORE_MODE", common.get("TOOL_RESTORE_MODE", "speculative"),
    )
    if tool_restore not in ("speculative", "execution"):
        raise ValueError("unsupported tool restore mode")
    tool_setting = common.get(
        "BELIEFKV_ENABLE_TOOL_PREFETCH", common.get("TOOL_PREFETCH", "1"),
    )
    if tool_setting not in ("0", "1"):
        raise ValueError("tool prefetch setting must be 0 or 1")
    tool_prefetch_enabled = (
        custom_kv and tool_restore == "speculative"
        and (tool_prefetch if tool_prefetch is not None else tool_setting == "1")
    )
    frontier = common.get(
        "BELIEFKV_ENABLE_FRONTIER_RESTORE",
        common.get("EXECUTION_FRONTIER_RESTORE", "0"),
    )
    if frontier not in ("0", "1"):
        raise ValueError("execution frontier restore must be 0 or 1")
    frontier = str(int(
        frontier == "1" and custom_kv
    ))
    frontier_quota = common.get(
        "BELIEFKV_FRONTIER_RESTORE_ORDINARY_QUOTA",
        common.get("FRONTIER_RESTORE_ORDINARY_QUOTA", "4"),
    )
    if frontier_quota not in ("0", "4"):
        raise ValueError("frontier restore ordinary quota must be 0 or 4")
    frontier_quota = frontier_quota if frontier == "1" else "4"
    frontier_reclaim = common.get(
        "BELIEFKV_FRONTIER_COLD_RECLAIM", common.get("FRONTIER_COLD_RECLAIM", "0"),
    )
    if frontier_reclaim not in ("0", "1"):
        raise ValueError("frontier cold reclaim must be 0 or 1")
    frontier_reclaim = str(int(frontier == "1" and frontier_reclaim == "1"))
    frontier_reclaim_policy = common.get(
        "BELIEFKV_FRONTIER_COLD_RECLAIM_POLICY",
        common.get("FRONTIER_COLD_RECLAIM_POLICY", "waiting"),
    )
    if frontier_reclaim_policy not in ("waiting", "selected_queue"):
        raise ValueError("unsupported frontier cold reclaim policy")
    if frontier_reclaim == "0":
        frontier_reclaim_policy = "waiting"
    workflow_ordering = scheduling == "workflow" and mode in ("full", "scheduler_only")
    if mode == "join_priority_only":
        scheduling = "join_priority"
    last_child = common.get(
        "BELIEFKV_ENABLE_JOIN_LAST_CHILD_PRIORITY",
        common.get("JOIN_LAST_CHILD_PRIORITY", "0"),
    )
    if last_child not in ("0", "1"):
        raise ValueError("JOIN last child priority must be 0 or 1")
    last_child = str(int(
        last_child == "1" and scheduling == "join_priority"
        and mode in ("full", "scheduler_only", "join_priority_only")
    ))
    continuity = common.get(
        "BELIEFKV_ENABLE_TOOL_CONTINUITY", common.get("TOOL_CONTINUITY", "0"),
    )
    if continuity not in ("0", "1"):
        raise ValueError("tool continuity must be 0 or 1")
    continuity = str(int(
        continuity == "1" and scheduling in ("workflow", "join_priority")
        and mode in ("full", "scheduler_only", "join_priority_only")
    ))
    grace = common.get(
        "BELIEFKV_TOOL_CONTINUITY_GRACE_MS", common.get("TOOL_CONTINUITY_GRACE_MS", "0"),
    )
    if not math.isfinite(float(grace)) or not 0 <= float(grace) <= 1000.:
        raise ValueError("tool continuity grace must be in [0, 1000] ms")
    grace = grace if custom_kv and continuity == "1" else "0"
    native = mode == "native_matched"
    sessionless_prefix = common.get(
        "BELIEFKV_SESSIONLESS_PREFIX_MIGRATION", common.get("SESSIONLESS_PREFIX_MIGRATION", "0"),
    )
    if sessionless_prefix not in ("0", "1"):
        raise ValueError("sessionless prefix migration must be 0 or 1")
    agent_admission = common.get("BELIEFKV_AGENT_ADMISSION", common.get("AGENT_ADMISSION", "off"))
    if agent_admission not in (
        "off", "working_set", "thunderagent", "thunderagent_retained", "thunderagent_workflow",
        "thunderagent_reference",
    ):
        raise ValueError("unsupported agent working-set admission")
    agent_admission = agent_admission if mode == "full" else "off"
    if agent_admission == "thunderagent_reference" and sessionless_prefix == "1":
        raise ValueError("reference ThunderAgent admission requires session-bound migration")
    join_free = common.get(
        "BELIEFKV_AGENT_JOIN_FREE_ADMISSION", common.get("AGENT_JOIN_FREE_ADMISSION", "0"),
    )
    if join_free not in ("0", "1") or (
        mode == "full" and join_free == "1" and agent_admission != "thunderagent_reference"
    ):
        raise ValueError("free-capacity JOIN admission requires the reference Full core")
    join_free = join_free if agent_admission == "thunderagent_reference" else "0"
    join_handoff = common.get(
        "BELIEFKV_AGENT_JOIN_BLOCKED_HANDOFF", common.get("AGENT_JOIN_BLOCKED_HANDOFF", "0"),
    )
    if join_handoff not in ("0", "1") or (
        mode == "full" and join_handoff == "1"
        and (agent_admission != "thunderagent_reference" or join_free != "1")
    ):
        raise ValueError("JOIN blocked handoff requires reference Full free-JOIN admission")
    join_handoff = join_handoff if agent_admission == "thunderagent_reference" else "0"
    continuation_free = common.get(
        "BELIEFKV_AGENT_FREE_CONTINUATION_ADMISSION",
        common.get("AGENT_FREE_CONTINUATION_ADMISSION", "0"),
    )
    if continuation_free not in ("0", "1") or (
        mode == "full" and continuation_free == "1" and agent_admission != "thunderagent_reference"
    ):
        raise ValueError("free continuation admission requires the reference Full core")
    continuation_free = continuation_free if agent_admission == "thunderagent_reference" else "0"
    if early_tool_offload == "1" and (
        agent_admission != "thunderagent_reference" or tool_offload != "1"
        or common.get("BELIEFKV_TOOL_OFFLOAD_POLICY", "fixed") != "demand_cost"
    ):
        raise ValueError("tool offload before admission requires reference Full demand-cost offload")
    if layer_handoff == "1" and (
        early_tool_offload != "1" or agent_admission != "thunderagent_reference"
    ):
        raise ValueError("layer handoff requires reference Full pre-admission tool offload")
    siblings = common.get(
        "BELIEFKV_AGENT_COADMIT_JOIN_CHILDREN", common.get("AGENT_COADMIT_JOIN_CHILDREN", "0"),
    )
    if siblings not in ("0", "1"):
        raise ValueError("JOIN sibling admission must be 0 or 1")
    siblings = str(int(
        siblings == "1"
        and agent_admission in ("thunderagent_retained", "thunderagent_reference")
    ))
    blocked_lending = common.get(
        "BELIEFKV_AGENT_BLOCKED_LENDING", common.get("AGENT_BLOCKED_LENDING", "idle"),
    )
    if blocked_lending not in ("idle", "backed_demand", "backed_demand_recall"):
        raise ValueError("unsupported blocked agent lending policy")
    if mode == "full" and blocked_lending != "idle" and agent_admission != "thunderagent_retained":
        raise ValueError("backed blocked lending requires retained agent admission")
    blocked_lending = blocked_lending if agent_admission == "thunderagent_retained" else "idle"
    join_admission = common.get(
        "BELIEFKV_AGENT_JOIN_DEPENDENCY_ADMISSION", common.get("AGENT_JOIN_DEPENDENCY_ADMISSION", "0"),
    )
    if join_admission not in ("0", "1") or (
        mode == "full" and join_admission == "1" and agent_admission != "thunderagent_retained"
    ):
        raise ValueError("JOIN-dependent admission requires retained agent eligibility")
    join_admission = join_admission if agent_admission == "thunderagent_retained" else "0"
    budget_proof = common.get(
        "BELIEFKV_AGENT_BLOCKED_BUDGET_PROOF", common.get("AGENT_BLOCKED_BUDGET_PROOF", "predicted_pressure"),
    )
    if budget_proof not in ("predicted_pressure", "observed_blocked", "observed_demand") or (
        mode == "full" and budget_proof != "predicted_pressure" and blocked_lending == "idle"
    ):
        raise ValueError("observed blocked budget proof requires backed lending")
    budget_proof = budget_proof if mode == "full" else "predicted_pressure"
    lending_selection = common.get(
        "BELIEFKV_AGENT_LENDING_SELECTION", common.get("AGENT_LENDING_SELECTION", "largest"),
    )
    if lending_selection not in ("largest", "fit") or (
        mode == "full" and lending_selection != "largest" and (
            agent_admission != "thunderagent_retained" or blocked_lending == "idle"
            or budget_proof != "observed_demand"
        )
    ):
        raise ValueError("fitting lending requires observed-demand retained admission")
    lending_selection = lending_selection if mode == "full" else "largest"
    pressure_policy = common.get(
        "BELIEFKV_AGENT_PRESSURE_PAUSE_POLICY", common.get("AGENT_PRESSURE_PAUSE_POLICY", "reference"),
    )
    if pressure_policy not in ("reference", "projected_release") or (
        mode == "full" and pressure_policy != "reference" and agent_admission != "thunderagent_retained"
    ):
        raise ValueError("projected pressure release requires retained agent eligibility")
    pressure_policy = pressure_policy if mode == "full" else "reference"
    recall_selection = common.get(
        "BELIEFKV_AGENT_RECALL_SELECTION", common.get("AGENT_RECALL_SELECTION", "largest"),
    )
    if recall_selection not in ("largest", "cold_fit") or (
        mode == "full" and recall_selection != "largest" and blocked_lending != "backed_demand_recall"
    ):
        raise ValueError("recall selection requires recorded blocked lending")
    recall_selection = recall_selection if mode == "full" else "largest"
    tool_return = common.get(
        "BELIEFKV_AGENT_TOOL_LOAN_RETURN", common.get("AGENT_TOOL_LOAN_RETURN", "0"),
    )
    if tool_return not in ("0", "1") or (
        mode == "full" and tool_return == "1" and blocked_lending != "backed_demand_recall"
    ):
        raise ValueError("tool loan return requires recorded blocked lending")
    tool_return = tool_return if agent_admission == "thunderagent_retained" else "0"
    entry = common.get(
        "BELIEFKV_AGENT_WORKFLOW_ENTRY_MIN_READY", common.get("AGENT_WORKFLOW_ENTRY_MIN_READY", "0"),
    )
    if (
        not isinstance(entry, str) or not entry.isascii() or not entry.isdecimal()
        or not 0 <= int(entry) <= 256
        or mode == "full" and int(entry) and agent_admission != "thunderagent_retained"
    ):
        raise ValueError("workflow entry requires retained agent admission and a bounded count")
    entry = entry if agent_admission == "thunderagent_retained" else "0"
    free_fence = common.get(
        "BELIEFKV_FREE_PAGE_H2D_FENCE", common.get("FREE_PAGE_H2D_FENCE", "0"),
    )
    if free_fence not in ("0", "1"):
        raise ValueError("free-page H2D fence must be 0 or 1")
    if agent_admission in (
        "thunderagent_retained", "thunderagent_workflow", "thunderagent_reference",
    ) and frontier == "1":
        raise ValueError("retained agent admission uses native ordinary demand recovery")
    if agent_admission == "thunderagent_workflow" and scheduling != "join_priority":
        raise ValueError("workflow agent admission requires declared JOIN priority ordering")
    settings = {
        **common,
        "BELIEFKV_INTERNAL_COMPARISON_MODE": mode,
        "PREDICTED_TOOL_OFFLOAD": tool_offload,
        "BELIEFKV_ENABLE_PREDICTED_TOOL_OFFLOAD": tool_offload,
        "TOOL_OFFLOAD_BEFORE_ADMISSION": early_tool_offload,
        "BELIEFKV_TOOL_OFFLOAD_BEFORE_ADMISSION": early_tool_offload,
        "LAYER_HANDOFF": layer_handoff,
        "BELIEFKV_LAYER_HANDOFF": layer_handoff,
        **{
            "BELIEFKV_LAYER_HANDOFF_" + direction + "_LAYERS": (
                common.get("BELIEFKV_LAYER_HANDOFF_" + direction + "_LAYERS", "1")
                if layer_handoff == "1" else "1"
            ) for direction in ("D2H", "H2D")
        },
        "BELIEFKV_TOOL_OFFLOAD_MIN_SURVIVAL": (
            common.get("BELIEFKV_TOOL_OFFLOAD_MIN_SURVIVAL", ".9") if custom_kv else ".9"
        ),
        "BELIEFKV_HOST_DEMAND_RESERVE_BYTES": (
            common.get("BELIEFKV_HOST_DEMAND_RESERVE_BYTES", "0") if mode == "full" else "0"
        ),
        "IDLE_HOST_MIRROR_RECYCLE": host_mirror_recycle,
        "BELIEFKV_IDLE_HOST_MIRROR_RECYCLE": host_mirror_recycle,
        "BELIEFKV_TOOL_OFFLOAD_POLICY": common.get("BELIEFKV_TOOL_OFFLOAD_POLICY", "fixed") if custom_kv else "fixed",
        "BELIEFKV_TOOL_TIMING_PRIOR": common.get("BELIEFKV_TOOL_TIMING_PRIOR", "") if custom_kv else "",
        "BELIEFKV_TOOL_TIMING_PRIOR_SHA256": common.get("BELIEFKV_TOOL_TIMING_PRIOR_SHA256", "") if custom_kv else "",
        "BELIEFKV_IDLE_PREPARE_IO_BACKEND": common.get("BELIEFKV_IDLE_PREPARE_IO_BACKEND", "native") if custom_kv else "native",
        "BELIEFKV_AGENT_KV_HIGH_WATERMARK": (
            common.get("BELIEFKV_AGENT_KV_HIGH_WATERMARK", "1")
            if agent_admission == "thunderagent_retained" else "1"
        ),
        "AGENT_ADMISSION": agent_admission,
        "BELIEFKV_AGENT_ADMISSION": agent_admission,
        "AGENT_JOIN_FREE_ADMISSION": join_free,
        "BELIEFKV_AGENT_JOIN_FREE_ADMISSION": join_free,
        "AGENT_JOIN_BLOCKED_HANDOFF": join_handoff,
        "BELIEFKV_AGENT_JOIN_BLOCKED_HANDOFF": join_handoff,
        "AGENT_FREE_CONTINUATION_ADMISSION": continuation_free,
        "BELIEFKV_AGENT_FREE_CONTINUATION_ADMISSION": continuation_free,
        "AGENT_COADMIT_JOIN_CHILDREN": siblings,
        "BELIEFKV_AGENT_COADMIT_JOIN_CHILDREN": siblings,
        "AGENT_BLOCKED_LENDING": blocked_lending,
        "BELIEFKV_AGENT_BLOCKED_LENDING": blocked_lending,
        "AGENT_JOIN_DEPENDENCY_ADMISSION": join_admission,
        "BELIEFKV_AGENT_JOIN_DEPENDENCY_ADMISSION": join_admission,
        "AGENT_BLOCKED_BUDGET_PROOF": budget_proof,
        "BELIEFKV_AGENT_BLOCKED_BUDGET_PROOF": budget_proof,
        "AGENT_LENDING_SELECTION": lending_selection,
        "BELIEFKV_AGENT_LENDING_SELECTION": lending_selection,
        "AGENT_PRESSURE_PAUSE_POLICY": pressure_policy,
        "BELIEFKV_AGENT_PRESSURE_PAUSE_POLICY": pressure_policy,
        "AGENT_RECALL_SELECTION": recall_selection,
        "BELIEFKV_AGENT_RECALL_SELECTION": recall_selection,
        "AGENT_TOOL_LOAN_RETURN": tool_return,
        "BELIEFKV_AGENT_TOOL_LOAN_RETURN": tool_return,
        "AGENT_WORKFLOW_ENTRY_MIN_READY": entry,
        "BELIEFKV_AGENT_WORKFLOW_ENTRY_MIN_READY": entry,
        "FREE_PAGE_H2D_FENCE": free_fence,
        "BELIEFKV_FREE_PAGE_H2D_FENCE": free_fence,
        "BELIEFKV_SESSIONLESS_PREFIX_MIGRATION": sessionless_prefix if mode == "full" else "0",
        "NATIVE_POLICY_BASELINE": str(int(native)),
        "AB_MODE": "off" if native or not custom_kv else "predictive_h2d",
        "BELIEFKV_CHAT_TOKEN_BLOCK_CACHE": "1",
        "PREDICTIVE_CHAT_TOKEN_BLOCK_CACHE": "1",
        "NATIVE_CHAT_TOKEN_BLOCK_CACHE": "1",
        "CONFIRMED_JOIN_CANARY": "0",
        "PREPARE_HOST": str(int(custom_kv)),
        "ENABLE_TOOL_TIMING": str(int(custom_kv)),
        "ONLINE_TOOL_TIMING": str(int(custom_kv)),
        "BORROWABLE_PARENT_HEADROOM": str(int(custom_kv)),
        "EXECUTION_HANDOFF": "0",
        "BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH": str(int(custom_kv)),
        "BELIEFKV_ENABLE_TOOL_PREFETCH": str(int(tool_prefetch_enabled)),
        "BELIEFKV_TOOL_PREFETCH_PRIORITY_MS": common.get(
            "BELIEFKV_TOOL_PREFETCH_PRIORITY_MS", common.get("TOOL_PREFETCH_PRIORITY_MS", "0"),
        ) if tool_prefetch_enabled else "0",
        "BELIEFKV_TOOL_RESTORE_MODE": tool_restore if custom_kv else "speculative",
        "TOOL_RESTORE_MODE": tool_restore if custom_kv else "speculative",
        "EXECUTION_FRONTIER_RESTORE": frontier,
        "BELIEFKV_ENABLE_FRONTIER_RESTORE": frontier,
        "FRONTIER_RESTORE_ORDINARY_QUOTA": frontier_quota,
        "BELIEFKV_FRONTIER_RESTORE_ORDINARY_QUOTA": frontier_quota,
        "FRONTIER_COLD_RECLAIM": frontier_reclaim,
        "BELIEFKV_FRONTIER_COLD_RECLAIM": frontier_reclaim,
        "FRONTIER_COLD_RECLAIM_POLICY": frontier_reclaim_policy,
        "BELIEFKV_FRONTIER_COLD_RECLAIM_POLICY": frontier_reclaim_policy,
        "BELIEFKV_IDLE_PREPARE_POLICY": common.get(
            "BELIEFKV_IDLE_PREPARE_POLICY", common.get("IDLE_PREPARE_POLICY", "cold"),
        ),
        "BELIEFKV_RESTORE_TIERS": common.get(
            "BELIEFKV_RESTORE_TIERS", common.get("RESTORE_TIERS", "complete"),
        ) if custom_kv else "complete",
        "BELIEFKV_ENABLE_PREPARE_HOST": str(int(custom_kv)),
        "PREFETCH_RESIDENCY_PROTECTION": protection if custom_kv else "0",
        "BELIEFKV_PREFETCH_RESIDENCY_PROTECTION": protection if custom_kv else "0",
        "PREFILL_SCHEDULING_POLICY": scheduling,
        "BELIEFKV_PREFILL_SCHEDULING_POLICY": scheduling,
        "BELIEFKV_ENABLE_EXECUTION_HANDOFF": "0",
        "BELIEFKV_ENABLE_RESIDENT_FIRST": str(int(workflow_ordering)),
        "BELIEFKV_ENABLE_JOIN_PARENT_PRIORITY": str(int(
            mode in ("full", "scheduler_only", "join_priority_only")
        )),
        "JOIN_LAST_CHILD_PRIORITY": last_child,
        "BELIEFKV_ENABLE_JOIN_LAST_CHILD_PRIORITY": last_child,
        "TOOL_CONTINUITY": continuity,
        "BELIEFKV_ENABLE_TOOL_CONTINUITY": continuity,
        "TOOL_CONTINUITY_GRACE_MS": grace,
        "BELIEFKV_TOOL_CONTINUITY_GRACE_MS": grace,
        "BELIEFKV_ENABLE_FINAL_STAGE_PRIORITY": str(int(workflow_ordering)),
        "BELIEFKV_NATIVE_PREFILL_ORDER": str(int(mode in ("kv_only", "native_matched"))),
        "BELIEFKV_WORKFLOW_ATTAINED_WORK": common.get("BELIEFKV_WORKFLOW_ATTAINED_WORK", "0"),
        "BELIEFKV_EMIT_SEMANTIC_TEXT": "0",
        "SEMANTIC_REPORT_ARTIFACT": "",
        "H2D_OPPORTUNITY_SAMPLING": "0",
        "TERMINAL_CACHE_DIAGNOSTICS": "0",
        "PROFILE_SHARED_PATH": profiling,
        "BELIEFKV_PROFILE_SHARED_PATH": profiling,
        "PREFETCH_PREFIX_DIAGNOSTICS": "0",
        "JOIN_PREFETCH_TRIGGER": "observed_eos",
    }
    for name in ("BELIEFKV_ENABLE_BACKGROUND_PREPARE", "BELIEFKV_ENABLE_TERMINAL_CHILD_RECLAIM"):
        value = common.get(name, "1")
        if value not in ("0", "1"):
            raise ValueError(f"{name} must be 0 or 1")
        settings[name] = str(int(custom_kv and value == "1"))
    if not custom_kv:
        for name in (
            "BELIEFKV_TOOL_TIMING_ARTIFACT", "BELIEFKV_TOOL_TIMING_SHA256",
            "BELIEFKV_H2D_SEED", "BELIEFKV_H2D_SEED_SHA256",
            "BELIEFKV_TRANSFER_SERVICE_SEED", "BELIEFKV_TRANSFER_SERVICE_SEED_SHA256",
            "BELIEFKV_COMPLETION_LEAD_ARTIFACT", "BELIEFKV_COMPLETION_LEAD_SHA256",
            "BELIEFKV_SEMANTIC_REPORT_ARTIFACT",
        ):
            settings[name] = ""
    return settings


def validate_runtime(
    mode: str, state: dict, *, residency_protection: bool | None = None,
    scheduling_policy: str | None = None,
    tool_restore_mode: str = "speculative",
    idle_prepare_policy: str | None = None,
    restore_tiers: str | None = None,
    join_last_child_priority: bool | None = None,
    execution_frontier_restore: bool | None = None,
    frontier_cold_reclaim: bool | None = None,
    tool_continuity: bool | None = None,
    tool_continuity_grace_ms: float | None = None,
    expected_overrides: dict[str, bool] | None = None,
    external_policy: str | None = None,
) -> None:
    """Check observed effective controls, not just the requested environment."""
    if mode not in MODES:
        raise ValueError("unknown internal comparison")
    if mode == "native_matched":
        if state:
            raise ValueError("matched native unexpectedly enabled the custom runtime")
        return
    workflow_ordering = state.get("prefill_scheduling_policy") == "workflow"
    if tool_restore_mode not in ("speculative", "execution"):
        raise ValueError("unsupported requested tool restore mode")
    expected = {
        "internal_comparison_mode": mode,
        "custom_kv_management": mode in ("full", "kv_only"),
        "prepare_host": mode in ("full", "kv_only"),
        "final_stage_prefetch": mode in ("full", "kv_only"),
        "tool_prefetch": mode in ("full", "kv_only") and tool_restore_mode == "speculative",
        "borrowable_parent_headroom": mode in ("full", "kv_only"),
        "resident_first": mode in ("full", "scheduler_only") and workflow_ordering,
        "join_parent_priority": mode in ("full", "scheduler_only", "join_priority_only"),
        "final_stage_priority": mode in ("full", "scheduler_only") and workflow_ordering,
        "prefill_ordering_mode": "native" if mode == "kv_only" else "workflow",
        "execution_handoff": False,
        "physical_disabled": False,
    }
    if tool_restore_mode == "execution":
        expected["tool_restore_mode"] = (
            tool_restore_mode if mode in ("full", "kv_only") else "speculative"
        )
    if tool_restore_mode == "execution" or execution_frontier_restore is not None:
        expected["execution_frontier_restore"] = (
            mode in ("full", "kv_only")
            and execution_frontier_restore is True
        )
    if idle_prepare_policy is not None:
        expected["idle_prepare_policy"] = idle_prepare_policy
    if frontier_cold_reclaim is not None:
        expected["frontier_cold_reclaim"] = (
            mode in ("full", "kv_only")
            and execution_frontier_restore is True and frontier_cold_reclaim
        )
    if restore_tiers is not None:
        expected["restore_tiers"] = restore_tiers
        if restore_tiers == "dependency" and mode in ("full", "kv_only"):
            expected["restore_tiers_supported"] = True
    if residency_protection is not None:
        expected["prefetch_residency_protection"] = (
            residency_protection and mode in ("full", "kv_only")
        )
    if join_last_child_priority is not None:
        expected["join_last_child_priority"] = (
            join_last_child_priority
            and mode in ("full", "scheduler_only", "join_priority_only")
        )
    if scheduling_policy is not None:
        if scheduling_policy not in ("workflow", "join_priority", "workflow_completion"):
            raise ValueError("unsupported requested scheduling policy")
        expected["prefill_scheduling_policy"] = (
            "native" if mode == "kv_only" else
            "join_priority" if mode == "join_priority_only" else scheduling_policy
        )
    if tool_continuity is not None:
        expected["tool_continuity"] = (
            tool_continuity
            and state.get("prefill_scheduling_policy") in ("workflow", "join_priority")
            and mode in ("full", "scheduler_only", "join_priority_only")
        )
    if tool_continuity_grace_ms is not None:
        expected["tool_continuity_grace_ms"] = (
            tool_continuity_grace_ms if mode == "full" and state.get("tool_continuity") else 0.
        )
    if expected_overrides:
        if not set(expected_overrides) <= {
            "join_parent_priority", "final_stage_prefetch",
            "background_prepare", "terminal_child_reclaim", "tool_prefetch",
        } or any(type(value) is not bool for value in expected_overrides.values()):
            raise ValueError("unsupported module ablation expectations")
        expected.update(expected_overrides)
    if external_policy is not None:
        from beliefkv.experiments.paper_baselines import validate_state

        validate_state(external_policy, state)
        expected.update(
            resident_first=False, join_parent_priority=False,
            final_stage_priority=False,
        )
    elif state.get("paper_baseline") is not None:
        raise ValueError("external policy was not declared for this comparison")
    for field, value in expected.items():
        if state.get(field) != value:
            raise ValueError(f"{mode}: observed {field}={state.get(field)!r}, expected {value!r}")
    if mode in SCHEDULING_ONLY:
        counts = state.get("counts", {})
        for name in (
            "physical_registered", "native_physical_completed",
            "incremental_full_tracking_enabled", "terminal_child_reclaim_calls",
            "terminal_root_reclaim_calls", "tool_wait_predictor_unavailable",
        ):
            if counts.get(name, 0):
                raise ValueError(f"{mode}: unexpected custom KV action: {name}")
    if mode == "join_priority_only" and (
        state.get("join_only_priority_ordinary_quota") != 4
        or state.get("join_only_priority_allows_aged_head_after_quota") is not True
    ):
        raise ValueError("JOIN-only priority must retain a bounded turn after aged heads")
    if mode == "join_priority_only" and any(
        state.get("counts", {}).get(name, 0) for name in (
            "prediction_maintenance_sweeps", "final_stage_accepted", "child_report_notice_observed",
        )
    ):
        raise ValueError("JOIN-only comparison is maintaining unused report predictions")
