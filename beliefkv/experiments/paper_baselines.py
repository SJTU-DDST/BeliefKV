"""Declared external-policy arms, isolated from BeliefKV scheduling and KV rules."""

from __future__ import annotations

from beliefkv.experiments.internal_comparison import arm_environment


RUNTIME_ARMS = {
    "thunderagent": "thunderagent_public_single_backend",
    "continuum_public": "continuum_public_single_backend",
    "concur_observed_join": "concur_paper_observed_join",
}
CONTINUUM_SERVER_ARGUMENTS = {
    "enable_priority_scheduling": True,
    "disable_priority_preemption": True,
    "schedule_low_priority_values_first": True,
    "retraction_policy": "priority",
}
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
    if label == "concur_observed_join":
        settings.update({
            "BELIEFKV_CONCUR_INITIAL_AGENTS": common.get("ROOT_COUNT", "18"),
            "BELIEFKV_CONCUR_MAX_AGENTS": "512",
            "BELIEFKV_CONCUR_INTERVAL_MS": "1000",
        })
    return settings


def validate_state(label: str, state: dict) -> None:
    if label not in RUNTIME_ARMS:
        raise ValueError("unknown external baseline")
    if state.get("internal_comparison_mode") != "scheduler_only":
        raise ValueError("external baseline requires the isolated common runtime")
    if any(state.get(name) is not False for name in DISABLED_POLICIES):
        raise ValueError("BeliefKV policy leaked into the external baseline")
    if state.get("agent_admission") is not None:
        raise ValueError("agent working-set candidate leaked into the external baseline")
    declared = state.get("paper_baseline") or {}
    if (
        declared.get("policy") != RUNTIME_ARMS[label]
        or declared.get("program_identity") != "invocation_id"
        or declared.get("future_information_used") is not False
        or declared.get("native_capacity_authority_preserved") is not True
    ):
        raise ValueError("effective paper baseline identity or information contract differs")
    if label == "concur_observed_join" and (
        type(declared.get("initial_agents")) is not int
        or not 1 <= declared["initial_agents"] <= 512
        or declared.get("maximum_agents") != 512
        or declared.get("control_interval_ms") != 1000
        or declared.get("feedback_semantics")
        != "native_non_evictable_gpu_usage_and_new_request_gpu_hits_only"
        or declared.get("control_law") != {
            "additive_increase": 2, "multiplicative_decrease": .5,
            "low_usage": .2, "high_usage": .5, "low_hit_rate": .2,
        }
    ):
        raise ValueError("CONCUR control law or native GPU feedback contract differs")


def server_flags(label: str) -> list[str]:
    if label != "continuum_public":
        return []
    return [
        "--enable-priority-scheduling", "--disable-priority-preemption",
        "--schedule-low-priority-values-first", "--retraction-policy", "priority",
    ]


def validate_server_arguments(label: str, observed: dict) -> None:
    if label == "continuum_public" and any(
        observed.get(name) != expected for name, expected in CONTINUUM_SERVER_ARGUMENTS.items()
    ):
        raise ValueError("Continuum requires native program-order pressure retraction")
