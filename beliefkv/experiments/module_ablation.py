"""Explicit component removals from the V124b Full development reference."""

from __future__ import annotations

from beliefkv.experiments.internal_comparison import arm_environment


ORDER = (
    "schedule_only", "kv_only", "no_tool_continuity", "no_join_prefetch",
    "no_background_prepare", "no_tool_duration_model", "no_child_reclaim",
    "no_join_priority", "complete_restore",
)


def environment(common: dict[str, str], label: str) -> dict[str, str]:
    if label not in ORDER:
        raise ValueError("unknown module ablation")
    mode = (
        "join_priority_only" if label == "schedule_only"
        else "kv_only" if label == "kv_only" else "full"
    )
    if (
        common.get("MAX_RUNNING_REQUESTS") != "8"
        or common.get("ROOT_COUNT") != "36"
        or common.get("SERVER_RANDOM_SEED") != "21"
        or common.get("SAMPLING_SEED") != "21"
        or common.get("MODEL_CONTEXT_TOKENS") != "262144"
        or common.get("PREFILL_SCHEDULING_POLICY") != "join_priority"
        or common.get("BELIEFKV_ENABLE_FRONTIER_RESTORE") != "0"
        or common.get("BELIEFKV_ENABLE_TOOL_CONTINUITY") != "1"
    ):
        raise ValueError("module reference differs from V124b")
    result = arm_environment(common, mode)
    if label == "no_tool_continuity":
        result.update(TOOL_CONTINUITY="0", BELIEFKV_ENABLE_TOOL_CONTINUITY="0")
    elif label == "no_join_prefetch":
        result.update(
            AB_MODE="reactive", BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH="0",
            BELIEFKV_ENABLE_TOOL_PREFETCH="0",
        )
    elif label == "no_background_prepare":
        result["BELIEFKV_ENABLE_BACKGROUND_PREPARE"] = "0"
    elif label == "no_tool_duration_model":
        result.update(
            ENABLE_TOOL_TIMING="0", ONLINE_TOOL_TIMING="0",
            BELIEFKV_ONLINE_TOOL_TIMING="0", BELIEFKV_TOOL_TIMING_ARTIFACT="",
            BELIEFKV_TOOL_TIMING_SHA256="", BELIEFKV_ENABLE_TOOL_PREFETCH="0",
        )
    elif label == "no_child_reclaim":
        result["BELIEFKV_ENABLE_TERMINAL_CHILD_RECLAIM"] = "0"
    elif label == "no_join_priority":
        result["BELIEFKV_ENABLE_JOIN_PARENT_PRIORITY"] = "0"
    elif label == "complete_restore":
        result.update(RESTORE_TIERS="complete", BELIEFKV_RESTORE_TIERS="complete")
    return result


def runtime_overrides(label: str) -> dict[str, bool]:
    if label not in ORDER:
        raise ValueError("unknown module ablation")
    custom = label != "schedule_only"
    result = {
        "background_prepare": custom and label != "no_background_prepare",
        "terminal_child_reclaim": custom and label != "no_child_reclaim",
    }
    if label == "no_join_prefetch":
        result["final_stage_prefetch"] = False
    elif label == "no_join_priority":
        result["join_parent_priority"] = False
    return result


def verify_activity(label: str, runtime: dict) -> None:
    """Disabled modules must not quietly perform their original work."""
    policy = runtime.get("tool_continuity_policy") or {}
    if label == "no_tool_continuity" and runtime.get("tool_continuity") is not False:
        raise ValueError("tool continuity remained enabled")
    if label == "no_tool_duration_model" and any(runtime.get(name) for name in (
        "tool_predictor_configured", "online_tool_timing", "tool_timing_only",
    )):
        raise ValueError("tool-duration inference remained enabled")
    counts = runtime.get("counts") or {}
    forbidden = (
        ("idle_full_prepare_native_calls", "spawn_full_prepare_issued_batches")
        if label == "no_background_prepare"
        else ("terminal_child_reclaim_calls",) if label == "no_child_reclaim"
        else ("join_parent_priority_admitted",) if label == "no_join_priority"
        else ()
    )
    if any(counts.get(name, 0) for name in forbidden):
        raise ValueError("disabled module still executed")
    if label == "no_tool_continuity" and policy.get("counts", {}).get("priority_admissions"):
        raise ValueError("disabled continuation still received priority")
