"""Shared workload shape settings for the live harness and experiment plans."""

NESTED_IN_GRAPH_PROFILE = "native_in_graph_nested_2to4"
NESTED_COORDINATOR_ROLE = "delegation-coordinator"
SUBAGENT_FANOUT_PROFILES = (
    "natural",
    "parallel_analysis_2to3",
    "native_subagent_2to3",
    "native_dynamic_1to4",
    "native_in_graph_1to4",
    "native_in_graph_2to4",
    NESTED_IN_GRAPH_PROFILE,
)
IN_GRAPH_SUBAGENT_PROFILES = frozenset({
    "native_in_graph_1to4", "native_in_graph_2to4", NESTED_IN_GRAPH_PROFILE,
})
TWO_TO_FOUR_IN_GRAPH_PROFILES = frozenset({
    "native_in_graph_2to4", NESTED_IN_GRAPH_PROFILE,
})


def delegation_contract(profile: str) -> dict:
    if profile not in SUBAGENT_FANOUT_PROFILES:
        raise ValueError("unsupported subagent fan-out profile")
    nested = profile == NESTED_IN_GRAPH_PROFILE
    in_graph = profile in IN_GRAPH_SUBAGENT_PROFILES
    bounded_initial = profile in TWO_TO_FOUR_IN_GRAPH_PROFILES
    return {
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
