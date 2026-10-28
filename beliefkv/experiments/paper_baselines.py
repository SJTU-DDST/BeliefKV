"""Declared external-policy arms, isolated from BeliefKV scheduling and KV rules."""

from __future__ import annotations

from beliefkv.experiments.internal_comparison import arm_environment


RUNTIME_ARMS = {
    "thunderagent": "thunderagent_public_single_backend",
    "thunderagent_acting_decay": "thunderagent_public_single_backend_acting_decay",
    "continuum_public": "continuum_public_single_backend",
    "concur_observed_join": "concur_paper_observed_join",
    "kvflow_observed_graph": "kvflow_paper_observed_graph",
    "tokencake_paper_observed": "tokencake_paper_observed_metadata",
}
TOKENCAKE_ARMS = frozenset({"tokencake_paper_observed"})
TOKENCAKE_PARAMETERS = {
    "generation_budget": "BELIEFKV_TOKENCAKE_GENERATION_BUDGET",
    "context_limit": "BELIEFKV_TOKENCAKE_CONTEXT_LIMIT",
    "running_limit": "BELIEFKV_TOKENCAKE_RUNNING_LIMIT",
    "prefill_token_budget": "BELIEFKV_TOKENCAKE_PREFILL_TOKEN_BUDGET",
    "chunked_prefill_tokens": "BELIEFKV_TOKENCAKE_CHUNKED_PREFILL_TOKENS",
    "policy_block_tokens": "BELIEFKV_TOKENCAKE_POLICY_BLOCK_TOKENS",
    "eviction_window_tokens": "BELIEFKV_TOKENCAKE_EVICTION_WINDOW_TOKENS",
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


def tokencake_parameters(common):
    try:
        values = {name: int(common[variable]) for name, variable in TOKENCAKE_PARAMETERS.items()}
    except (KeyError, ValueError, TypeError) as error:
        raise ValueError("TokenCake requires all seven explicit serving parameters") from error
    if (
        any(value < (0 if name == "generation_budget" else 1) for name, value in values.items())
        or values["generation_budget"] > values["context_limit"]
        or values["chunked_prefill_tokens"] > values["prefill_token_budget"]
        or values["policy_block_tokens"] > values["context_limit"]
    ):
        raise ValueError("TokenCake serving parameters are inconsistent")
    return values


def validate_tokencake_parameters(declared, expected):
    actual = {
        "generation_budget": declared.get("generation_budget"),
        "context_limit": declared.get("context_limit"),
        **(declared.get("temporal_parameters") or {}),
    }
    if actual != expected or any(type(value) is not int for value in actual.values()):
        raise ValueError("TokenCake effective serving parameters differ from the declaration")


def isolated_environment(common: dict[str, str]) -> dict[str, str]:
    settings = arm_environment(common, "scheduler_only")
    settings.pop("BELIEFKV_PAPER_BASELINE", None)
    settings.update({
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


def environment(common: dict[str, str], label: str) -> dict[str, str]:
    if label not in RUNTIME_ARMS:
        raise ValueError("paper policy lacks a qualified runtime adapter")
    settings = isolated_environment(common)
    settings["BELIEFKV_PAPER_BASELINE"] = RUNTIME_ARMS[label]
    if label == "concur_observed_join":
        settings.update({
            "BELIEFKV_CONCUR_INITIAL_AGENTS": common.get("ROOT_COUNT", "18"),
            "BELIEFKV_CONCUR_MAX_AGENTS": "512",
            "BELIEFKV_CONCUR_INTERVAL_MS": "1000",
        })
    if label in TOKENCAKE_ARMS:
        values = tokencake_parameters(common)
        settings.update({variable: str(values[name]) for name, variable in TOKENCAKE_PARAMETERS.items()})
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
    if state.get("demand_prefill_pipeline", False) is not False:
        raise ValueError("native demand pipeline leaked into the external baseline")
    declared = state.get("paper_baseline") or {}
    if (
        declared.get("policy") != RUNTIME_ARMS[label]
        or declared.get("program_identity") != "invocation_id"
        or declared.get("future_information_used") is not False
        or declared.get("native_capacity_authority_preserved") is not True
    ):
        raise ValueError("effective paper baseline identity or information contract differs")
    if label in ("thunderagent", "thunderagent_acting_decay") and declared.get("streaming_context_accounting") != (
        "observed_native_outputs_at_arrival_and_five_second_control"
    ):
        raise ValueError("ThunderAgent must account for observed streaming context growth")
    if label == "thunderagent_acting_decay" and (
        declared.get("acting_token_decay") is not True
        or type(declared.get("decay_half_life_s")) not in (int, float)
        or declared.get("decay_half_life_s") != 1.
        or declared.get("decay_scope") != "periodic_resume_only"
        or declared.get("decay_age_source") != "observed_native_completion_monotonic"
        or declared.get("undiscounted_capacity_enforced") is not True
        or declared.get("physical_capacity_credit") is not False
    ):
        raise ValueError("ThunderAgent acting decay differs from its declared resume-only rule")
    if label == "kvflow_observed_graph" and (
        declared.get("graph_information_source") != "observed_events"
        or declared.get("fixed_prefix_source") != "completed_same_context_input_lcp"
        or declared.get("application_declared_graph_equivalence_claimed") is not False
        or declared.get("cache_identity") != "workflow_context_epoch"
        or declared.get("control_interval_ms") != 200
        or declared.get("prefetch_concurrency_limit") != 4
        or declared.get("path_checks_per_interval") != 16
        or declared.get("native_eviction_checks_per_interval") != 16
        or declared.get("path_node_limit") != 1024
        or declared.get("speculative_native_capacity_reserve")
        != "unallocated_observed_admitted_input_plus_one_decode_step"
    ):
        raise ValueError("KVFlow observed-graph or fixed-prefix information contract differs")
    if label in TOKENCAKE_ARMS and (
        declared.get("scope") != "paper_strategy_observed_metadata_native_adaptation"
        or declared.get("complete_baseline") is not True
        or declared.get("runtime_implementation_complete") is not True
        or declared.get("graph_information_source") != "observed_events_or_explicit_application_metadata"
        or declared.get("original_application_declared_graph_available") is not False
        or declared.get("generation_budget_source") != "separately_declared_serving_budget"
        or declared.get("serving_generation_budget_source") != "separately_declared_not_recorded_output_length"
        or declared.get("temporal_policy") != "paper_first_fit_and_deadline_progressive_upload"
        or declared.get("predictive_upload_enabled") is not True
        or declared.get("transfer_observer_error") is not None
    ):
        raise ValueError("TokenCake paper lifecycle or observed information contract differs")
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
