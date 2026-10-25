"""Declared external-policy arms, isolated from BeliefKV scheduling and KV rules."""

from __future__ import annotations

from beliefkv.experiments.internal_comparison import arm_environment


RUNTIME_ARMS = {"thunderagent": "thunderagent_public_single_backend"}
DISABLED_POLICIES = (
    "custom_kv_management", "prepare_host", "background_prepare",
    "terminal_child_reclaim", "tool_prefetch", "final_stage_prefetch",
    "execution_frontier_restore", "frontier_cold_reclaim",
    "execution_handoff", "borrowable_parent_headroom", "resident_first",
    "join_parent_priority", "join_last_child_priority", "final_stage_priority",
    "tool_continuity",
)


def environment(common: dict[str, str], label: str) -> dict[str, str]:
    if label not in RUNTIME_ARMS:
        raise ValueError("paper policy lacks a qualified runtime adapter")
    settings = arm_environment(common, "scheduler_only")
    settings.update({
        "BELIEFKV_PAPER_BASELINE": RUNTIME_ARMS[label],
        "BELIEFKV_ENABLE_RESIDENT_FIRST": "0",
        "BELIEFKV_ENABLE_JOIN_PARENT_PRIORITY": "0",
        "BELIEFKV_ENABLE_JOIN_LAST_CHILD_PRIORITY": "0",
        "JOIN_LAST_CHILD_PRIORITY": "0",
        "BELIEFKV_ENABLE_FINAL_STAGE_PRIORITY": "0",
        "BELIEFKV_ENABLE_TOOL_CONTINUITY": "0",
        "TOOL_CONTINUITY": "0",
        "BELIEFKV_TOOL_CONTINUITY_GRACE_MS": "0",
        "TOOL_CONTINUITY_GRACE_MS": "0",
        "BELIEFKV_WORKFLOW_ATTAINED_WORK": "0",
        "BELIEFKV_SUBAGENT_RETENTION": "off",
    })
    return settings


def validate_state(label: str, state: dict) -> None:
    if label not in RUNTIME_ARMS:
        raise ValueError("unknown external baseline")
    if state.get("internal_comparison_mode") != "scheduler_only":
        raise ValueError("external baseline requires the isolated common runtime")
    if any(state.get(name) is not False for name in DISABLED_POLICIES):
        raise ValueError("BeliefKV policy leaked into the external baseline")
    declared = state.get("paper_baseline") or {}
    if (
        declared.get("policy") != RUNTIME_ARMS[label]
        or declared.get("program_identity") != "invocation_id"
        or declared.get("future_information_used") is not False
        or declared.get("native_capacity_authority_preserved") is not True
    ):
        raise ValueError("effective paper baseline identity or information contract differs")
