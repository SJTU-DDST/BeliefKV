"""Shared workload shape settings for the live harness and experiment plans."""

NESTED_IN_GRAPH_PROFILE = "native_in_graph_nested_2to4"
DYNAMIC_NESTED_IN_GRAPH_PROFILE = "native_in_graph_dynamic_nested"
NESTED_COORDINATOR_ROLE = "delegation-coordinator"
NESTED_IN_GRAPH_PROFILES = frozenset({
    NESTED_IN_GRAPH_PROFILE, DYNAMIC_NESTED_IN_GRAPH_PROFILE,
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
    dynamic = profile == DYNAMIC_NESTED_IN_GRAPH_PROFILE
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
            natural_final_response_rejected=False,
        )
    return contract
