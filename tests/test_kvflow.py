import math

import pytest

from beliefkv.policy.kvflow import (
    CacheNode, FixedPromptHistory, PrefetchCandidate, StepGraph, StepNode,
    cache_priorities, eviction_order, prefetch_candidates, request_cache_status,
)


def graph(mode):
    return StepGraph((
        StepNode("plan", "planner"),
        StepNode("search", "searcher", ("plan",)),
        StepNode("e1", "executor1", ("plan",)),
        StepNode("e2", "executor2", ("search",)),
        StepNode("express", "expresser", ("e1", "e2"), mode),
    ), information_source="application_declared")


def test_paper_figure3_all_and_any_have_different_activation_distances():
    assert graph("all").agent_steps(executing=("plan",)) == {
        "planner": 0, "searcher": 1, "executor1": 1, "executor2": 2, "expresser": 3,
    }
    assert graph("any").agent_steps(executing=("plan",))["expresser"] == 2


def test_completed_steps_are_not_charged_again_and_immediately_ready_nodes_get_zero():
    steps = graph("all").agent_steps(completed=("plan", "e1"), executing=("search",))
    assert steps["planner"] == steps["executor1"] == math.inf
    assert steps["searcher"] == 0 and steps["executor2"] == 1 and steps["expresser"] == 2
    assert graph("all").agent_steps(completed=("plan", "search", "e1", "e2"))["expresser"] == 0


def test_impossible_branch_blocks_all_but_not_any_and_does_not_become_a_ready_node():
    assert graph("all").agent_steps(unavailable=("search",))["expresser"] == math.inf
    assert graph("any").agent_steps(unavailable=("search",))["expresser"] == 2


def test_recurring_agent_uses_nearest_remaining_invocation_and_workflows_are_namespaced():
    policy = StepGraph((
        StepNode("a0", "w1:a"), StepNode("b", "w1:b", ("a0",)),
        StepNode("a1", "w1:a", ("b",)), StepNode("a2", "w2:a"),
    ), information_source="observed_events")
    assert policy.agent_steps(completed=("a0",), executing=("b",)) == {
        "w1:a": 1, "w1:b": 0, "w2:a": 0,
    }


@pytest.mark.parametrize("case", ("private_future", "cycle", "duplicate", "unknown", "bad_aggregation"))
def test_invalid_graph_or_private_trace_input_is_rejected(case):
    nodes = (StepNode("a", "agent"),)
    source = "application_declared"
    if case == "private_future":
        source = "replay_driver_future_graph"
    elif case == "cycle":
        nodes = (StepNode("a", "agent", ("a",)),)
    elif case == "duplicate":
        nodes *= 2
    elif case == "unknown":
        nodes = (StepNode("a", "agent", ("missing",)),)
    else:
        nodes = (StepNode("a", "agent", aggregation="shortest"),)
    with pytest.raises(ValueError):
        StepGraph(nodes, information_source=source)


@pytest.mark.parametrize("states", (
    {"completed": ("plan",), "executing": ("plan",)},
    {"completed": ("missing",)},
    {"executing": ("plan",), "unavailable": ("plan",)},
))
def test_inconsistent_observations_do_not_silently_change_distances(states):
    with pytest.raises(ValueError):
        graph("all").agent_steps(**states)


def cache():
    return (
        CacheNode(0, None, device_leaf=False),
        CacheNode(1, 0, ("w:planner",), device_leaf=False, last_access=100.),
        CacheNode(2, 1, ("w:soon",), device_leaf=False),
        CacheNode(3, 2, last_access=200.),
        CacheNode(4, 1, ("w:later",), last_access=1.),
    )


def test_paper_tree_keeps_shared_fixed_prefix_and_evicts_new_dynamic_suffix_first():
    nodes = cache()
    priorities = cache_priorities(nodes, {"w:planner": 0, "w:soon": 1, "w:later": 3})
    assert priorities == {0: 0, 1: 0, 2: 1, 3: math.inf, 4: 3}
    assert eviction_order(nodes, priorities) == (3, 4)


def test_shared_prefix_propagates_minimum_across_workflows_not_maximum():
    nodes = (CacheNode(0, None, ("one:a", "other:a"), device_leaf=False), CacheNode(1, 0))
    assert cache_priorities(nodes, {"one:a": 5, "other:a": 1}) == {0: 1, 1: math.inf}


def test_transfer_and_native_locks_always_exclude_nodes_from_device_eviction():
    nodes = (
        CacheNode(1, None, status="loading"),
        CacheNode(2, None, status="offloading"),
        CacheNode(3, None, locked=True),
        CacheNode(4, None, status="cpu_backup"),
        CacheNode(5, None, device_leaf=False),
    )
    assert eviction_order(nodes, cache_priorities(nodes, {})) == ()


def test_equal_priorities_use_oldest_access_without_overriding_future_usage():
    nodes = (CacheNode(3, None, last_access=5.), CacheNode(1, None, last_access=6.))
    assert eviction_order(nodes, {1: 2, 3: 2}) == (3, 1)
    assert eviction_order(nodes, {1: 3, 3: 2}) == (1, 3)


def test_conditional_prefetch_includes_both_possible_branches_within_limits():
    candidates = (
        PrefetchCandidate("a", 10, 12), PrefetchCandidate("b", 10, 12),
        PrefetchCandidate("c", 10, 12),
    )
    assert prefetch_candidates(
        candidates, {"a": 1, "b": 1, "c": 2}, available_tokens=24, concurrent_limit=3,
    ) == ("a", "b")
    assert prefetch_candidates(
        candidates, {"a": 1, "b": 1, "c": 1}, available_tokens=24,
        concurrent_limit=2, in_flight=("existing",),
    ) == ("a",)


def test_capacity_accounts_for_whole_required_allocation_and_skips_nonfitting_targets():
    assert prefetch_candidates(
        (PrefetchCandidate("large", 10, 100), PrefetchCandidate("small", 10, 10)),
        {"large": 0, "small": 1}, available_tokens=10, concurrent_limit=2,
    ) == ("small",)
    assert prefetch_candidates(
        (PrefetchCandidate("resident", 0, 20), PrefetchCandidate("unknown", 1, 1)),
        {"resident": 0}, available_tokens=100, concurrent_limit=2,
    ) == ()


@pytest.mark.parametrize("statuses,expected", (
    ((), "ready"), (("gpu", "gpu"), "ready"), (("gpu", "cpu_backup"), "restore"),
    (("gpu", "loading"), "pending"), (("offloading", "cpu_backup"), "restore"),
    (("offloading", "gpu"), "ready"),
))
def test_only_actual_transfer_dependencies_block_request_service(statuses, expected):
    assert request_cache_status(
        CacheNode(index, None, status=status) for index, status in enumerate(statuses)
    ) == expected


def test_fixed_prompt_history_does_not_assume_first_full_prompt_or_use_future_suffixes():
    history = FixedPromptHistory()
    assert history.observe("w:a", prompt_tokens=1000, prior_prefix_hit=None) == 0
    assert history.observe("w:a", prompt_tokens=1200, prior_prefix_hit=900) == 900
    assert history.observe("w:a", prompt_tokens=1800, prior_prefix_hit=1200) == 900
    assert history.observe("w:a", prompt_tokens=700, prior_prefix_hit=600) == 600
    assert history.observe("other:a", prompt_tokens=1800, prior_prefix_hit=None) == 0
    history.forget("w:a")
    assert history.observe("w:a", prompt_tokens=1800, prior_prefix_hit=None) == 0
