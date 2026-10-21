"""Independent policy controls for the single-GPU internal comparisons."""

from __future__ import annotations

MODES = ("full", "join_priority_only", "scheduler_only", "kv_only", "native_matched")
ORDER = ("join_priority_only", "scheduler_only", "kv_only", "native_matched")
SCHEDULING_ONLY = frozenset(("join_priority_only", "scheduler_only"))


def arm_environment(common: dict[str, str], mode: str) -> dict[str, str]:
    if mode not in MODES:
        raise ValueError(f"unknown internal comparison: {mode}")
    custom_kv = mode in ("full", "kv_only")
    native = mode == "native_matched"
    settings = {
        **common,
        "BELIEFKV_INTERNAL_COMPARISON_MODE": mode,
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
        "BELIEFKV_ENABLE_TOOL_PREFETCH": str(int(custom_kv)),
        "BELIEFKV_ENABLE_PREPARE_HOST": str(int(custom_kv)),
        "BELIEFKV_ENABLE_EXECUTION_HANDOFF": "0",
        "BELIEFKV_ENABLE_RESIDENT_FIRST": str(int(mode in ("full", "scheduler_only"))),
        "BELIEFKV_ENABLE_FINAL_STAGE_PRIORITY": str(int(mode in ("full", "scheduler_only"))),
        "BELIEFKV_NATIVE_PREFILL_ORDER": str(int(mode in ("kv_only", "native_matched"))),
        "BELIEFKV_WORKFLOW_ATTAINED_WORK": common.get("BELIEFKV_WORKFLOW_ATTAINED_WORK", "0"),
        "BELIEFKV_EMIT_SEMANTIC_TEXT": "0",
        "SEMANTIC_REPORT_ARTIFACT": "",
        "H2D_OPPORTUNITY_SAMPLING": "0",
        "TERMINAL_CACHE_DIAGNOSTICS": "0",
        "PROFILE_SHARED_PATH": "0",
        "PREFETCH_PREFIX_DIAGNOSTICS": "0",
        "JOIN_PREFETCH_TRIGGER": "observed_eos",
    }
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


def validate_runtime(mode: str, state: dict) -> None:
    """Check observed effective controls, not just the requested environment."""
    if mode not in MODES:
        raise ValueError("unknown internal comparison")
    if mode == "native_matched":
        if state:
            raise ValueError("matched native unexpectedly enabled the custom runtime")
        return
    expected = {
        "internal_comparison_mode": mode,
        "custom_kv_management": mode in ("full", "kv_only"),
        "prepare_host": mode in ("full", "kv_only"),
        "final_stage_prefetch": mode in ("full", "kv_only"),
        "tool_prefetch": mode in ("full", "kv_only"),
        "borrowable_parent_headroom": mode in ("full", "kv_only"),
        "resident_first": mode in ("full", "scheduler_only"),
        "final_stage_priority": mode in ("full", "scheduler_only"),
        "prefill_ordering_mode": "native" if mode == "kv_only" else "workflow",
        "execution_handoff": False,
        "physical_disabled": False,
    }
    for field, value in expected.items():
        if state.get(field) != value:
            raise ValueError(f"{mode}: observed {field}={state.get(field)!r}, expected {value!r}")
    if mode in SCHEDULING_ONLY:
        counts = state.get("counts", {})
        for name in (
            "physical_registered", "native_physical_completed",
            "incremental_full_tracking_enabled", "terminal_child_reclaim_calls",
            "terminal_root_reclaim_calls",
        ):
            if counts.get(name, 0):
                raise ValueError(f"{mode}: unexpected custom KV action: {name}")
