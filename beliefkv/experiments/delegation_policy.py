"""Shared workload shape settings for the live harness and experiment plans."""

NESTED_IN_GRAPH_PROFILE = "native_in_graph_nested_2to4"
DYNAMIC_NESTED_IN_GRAPH_PROFILE = "native_in_graph_dynamic_nested"
REUSABLE_NESTED_IN_GRAPH_PROFILE = "native_in_graph_reusable_nested"
SHORT_WORKSTREAM_IN_GRAPH_PROFILE = "native_in_graph_short_workstreams"
PARALLEL_WORKSTREAM_IN_GRAPH_PROFILE = "native_in_graph_parallel_workstreams"
SHORT_WORKSTREAM_IN_GRAPH_PROFILES = frozenset({
    SHORT_WORKSTREAM_IN_GRAPH_PROFILE, PARALLEL_WORKSTREAM_IN_GRAPH_PROFILE,
})
DYNAMIC_NESTED_IN_GRAPH_PROFILES = frozenset({
    DYNAMIC_NESTED_IN_GRAPH_PROFILE, REUSABLE_NESTED_IN_GRAPH_PROFILE,
    *SHORT_WORKSTREAM_IN_GRAPH_PROFILES,
})
NESTED_COORDINATOR_ROLE = "delegation-coordinator"
MINIMUM_ROOT_JOIN_MEMBERS = 2
ROOT_RETRY_TASK_FANOUT = (MINIMUM_ROOT_JOIN_MEMBERS, 4)
NESTED_IN_GRAPH_PROFILES = frozenset({
    NESTED_IN_GRAPH_PROFILE, *DYNAMIC_NESTED_IN_GRAPH_PROFILES,
})
SUBAGENT_FANOUT_PROFILES = (
    "natural",
    "parallel_analysis_2to3",
    "native_subagent_2to3",
    "native_dynamic_1to4",
    "native_in_graph_1to4",
    "native_in_graph_2to4",
    NESTED_IN_GRAPH_PROFILE,
    DYNAMIC_NESTED_IN_GRAPH_PROFILE,
    REUSABLE_NESTED_IN_GRAPH_PROFILE,
    SHORT_WORKSTREAM_IN_GRAPH_PROFILE,
    PARALLEL_WORKSTREAM_IN_GRAPH_PROFILE,
)
IN_GRAPH_SUBAGENT_PROFILES = frozenset({
    "native_in_graph_1to4", "native_in_graph_2to4", *NESTED_IN_GRAPH_PROFILES,
})
TWO_TO_FOUR_IN_GRAPH_PROFILES = frozenset({
    "native_in_graph_2to4", NESTED_IN_GRAPH_PROFILE,
})


def delegation_contract(profile: str) -> dict:
    if profile not in SUBAGENT_FANOUT_PROFILES:
        raise ValueError("unsupported subagent fan-out profile")
    nested = profile in NESTED_IN_GRAPH_PROFILES
    dynamic = profile in DYNAMIC_NESTED_IN_GRAPH_PROFILES
    in_graph = profile in IN_GRAPH_SUBAGENT_PROFILES
    bounded_initial = profile in TWO_TO_FOUR_IN_GRAPH_PROFILES
    contract = {
        "profile": profile,
        "root_round_target": 4 if nested else 3 if in_graph else None,
        "children_per_round": (
            [2, 4] if bounded_initial else [1, 4] if in_graph else None
        ),
        "maximum_spawn_depth": 2 if nested else 1,
        "depth_convention": "root=0; direct child=1; grandchild=2",
        "nested_coordinator_role": NESTED_COORDINATOR_ROLE if nested else None,
        "nested_round_target": 1 if nested else 0,
        "initial_fanout_generation_bounds": [2, 4] if bounded_initial else None,
        "nested_fanout_generation_bounds": [2, 4] if nested else None,
        "round_enforcement": (
            "prompt_and_persisted_join_phase_instructions" if in_graph else "profile_prompt"
        ),
        "terminal_rejection_for_round_count": False,
        "leaf_task_tool_enabled": False,
    }
    if dynamic:
        contract.update(
            root_round_target=None,
            children_per_round=None,
            nested_round_target=None,
            initial_fanout_generation_bounds=None,
            nested_fanout_generation_bounds=None,
            direct_child_task_tool_enabled=True,
            recommended_parallel_children=[2, 4],
            round_enforcement="model_selected_from_task_evidence",
            root_initial_tool_choice="auto",
            nested_initial_tool_choice="auto",
            fixed_role_sequence=False,
            natural_final_response_rejected=True,
            root_minimum_delegations_prompt=MINIMUM_ROOT_JOIN_MEMBERS,
            minimum_completed_root_join_members=MINIMUM_ROOT_JOIN_MEMBERS,
            root_terminal_check="require_completed_model_selected_parallel_join",
            root_retry_tool_choice="task",
            root_retry_fanout_generation_bounds=list(ROOT_RETRY_TASK_FANOUT),
            maximum_root_delegation_attempts=2,
            recommended_root_rounds=[2, 3],
            nested_delegation_required_for_independent_subproblems=True,
        )
    if profile == REUSABLE_NESTED_IN_GRAPH_PROFILE:
        contract.update(
            persistent_subagent_handles=True,
            private_history_replay=True,
            fresh_invocation_per_activation=True,
            physical_retention_affects_prompt=False,
            concurrent_writers_per_handle=1,
            reuse_api="task(description, subagent_type, agent_id=None);close_agent(agent_id)",
        )
    if profile in SHORT_WORKSTREAM_IN_GRAPH_PROFILES:
        contract.update(
            child_deliverable="one_bounded_evidence_or_change",
            child_work_budget_enforcement="soft_reminders_then_natural_report",
            follow_up_delegation="only_for_new_unresolved_deliverables",
            tool_execution="necessary_real_project_commands",
            tool_duration_target_seconds=None,
            artificial_tool_delays=False,
            repeated_validation_without_new_evidence=False,
        )
    if profile == PARALLEL_WORKSTREAM_IN_GRAPH_PROFILE:
        contract.update(
            checkpoint_assignment_protocol_version=3,
            checkpoint_assignment_fields=[
                "question", "known_paths_or_symbols", "existing_evidence",
                "finish_condition", "write_scope",
            ],
            root_owns_primary_implementation=True,
            checkpoint_task_parameters_required=True,
            parallel_checkpoint_tool="parallel_tasks",
            parallel_checkpoint_api_version=1,
            parallel_checkpoint_members=[2, 4],
            parallel_checkpoint_execution="real_concurrent_tasks_then_all_join",
            root_retry_tool_choice="parallel_tasks",
            root_retry_fanout_generation_bounds=None,
            child_supporting_write_paths_max=2,
            task_access_from_manifest=True,
            child_unrelated_failures="classify_and_handoff_without_expanding_scope",
            child_roles="bounded_locator_reproducer_editor_reviewer_and_general_purpose",
            child_report="concise_evidence_and_explicit_handoff",
            child_todo_middleware=False,
            parallel_round_instruction="submit_2_to_4_independent_assignments_in_parallel_tasks",
            singleton_rounds="allowed_for_indivisible_work_and_reported_in_source_audit",
            first_tool_choice="auto",
            parallel_round_count_enforced=False,
            model_call_limit_enforced=False,
            report_token_limit_enforced=False,
            pilot_shape_review_required=True,
        )
    return contract
