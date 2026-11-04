from types import SimpleNamespace

import pytest

from beliefkv.runtime.workflow_work_oracle import scan_remaining_paths
from scripts.audit_join_branch_oracle import inspect, single_cross_workflow_swaps, target_ancestors


def node(identity, dependencies=(), *, request=None, delay=0.):
    return {
        "node_id": identity, "dependencies": list(dependencies), "delay_s": delay,
        "request_id": request,
    }


def test_branch_return_excludes_unrelated_parallel_sibling():
    graph = [
        node("root"), node("a", (0,), request="a"),
        node("b", (0,), request="b"), node("return_a", (1,)),
    ]
    prefix = target_ancestors(graph, "return_a")
    assert [row["node_id"] for row in prefix] == ["root", "a", "return_a"]
    requests = {
        "a": {"input_increment": 0, "output_tokens": 2},
        "b": {"input_increment": 0, "output_tokens": 100},
    }
    assert scan_remaining_paths(prefix, requests, {"root": 0.}, {}, 1., 0.) == (2., 2., 0.)


def test_nested_join_keeps_critical_parallel_path_instead_of_summing_children():
    graph = [
        node("root"), node("a", (0,), request="a"), node("b", (0,), request="b"),
        node("join", (1, 2)), node("return", (3,)),
    ]
    requests = {
        "a": {"input_increment": 0, "output_tokens": 2},
        "b": {"input_increment": 0, "output_tokens": 5},
    }
    prefix = target_ancestors(graph, "return")
    assert scan_remaining_paths(prefix, requests, {"root": 0.}, {}, 1., 0.) == (5., 5., 0.)


def test_active_tool_elapsed_time_is_conditioned_on_its_observed_start():
    graph = [node("start"), node("end", (0,), delay=8.), node("return", (1,))]
    prefix = target_ancestors(graph, "return")
    assert scan_remaining_paths(prefix, {}, {"start": 100.}, {}, 1., 103.) == (5., 0., 5.)


def test_completed_parent_work_is_not_charged_to_branch_again():
    graph = [
        node("parent", request="parent"), node("child", (0,), request="child"),
        node("return", (1,)),
    ]
    requests = {
        "parent": {"input_increment": 0, "output_tokens": 100},
        "child": {"input_increment": 0, "output_tokens": 2},
    }
    prefix = target_ancestors(graph, "return")
    assert scan_remaining_paths(prefix, requests, {"parent": 100.}, {}, 1., 100.) == (2., 2., 0.)


def test_current_generated_tokens_and_completed_prefill_reduce_branch_demand():
    graph = [node("request", request="request"), node("return", (0,))]
    requests = {"request": {"input_increment": 1000, "output_tokens": 10}}
    prefix = target_ancestors(graph, "return")
    assert scan_remaining_paths(
        prefix, requests, {}, {"request": 7}, 1., 0., prefill_done_after_output=True,
    ) == (3., 3., 0.)


def test_unknown_return_target_is_not_replaced_by_workflow_completion():
    with pytest.raises(ValueError, match="absent"):
        target_ancestors([node("workflow_end")], "missing_return")


def test_raw_publication_sequence_orders_events_without_parsed_sequence_attribute():
    rows = [
        {
            "event_id": identity, "workflow_id": "workflow", "kind": "workflow_start",
            "ts_ms": when, "sequence": sequence, "attributes": {},
        }
        for identity, when, sequence in (("later", 2., 3), ("first", 1., 1), ("second", 1., 2))
    ]
    observed = []
    oracle = SimpleNamespace(observe=lambda event: observed.append(event.event_id))
    inspect([{"monotonic_s": .003, "changed_JOIN_groups": []}], oracle, rows, [])
    assert observed == ["first", "second", "later"]


def test_cross_workflow_swap_keeps_every_other_actual_member_and_cardinality():
    workflows = {"a": "join_workflow", "b": "other", "c": "third", "protected": "join_workflow"}
    actual = ["a", "protected"]
    alternatives = [
        ["b", "protected"], ["c", "protected"],
        ["b"], ["b", "c", "protected"], ["a", "b"],
    ]
    assert single_cross_workflow_swaps(actual, alternatives, "a", workflows) == ["b", "c"]
    assert not single_cross_workflow_swaps(actual, alternatives, "c", workflows)


def test_sibling_identity_swap_is_not_counted_as_a_cross_workflow_competitor():
    assert not single_cross_workflow_swaps(
        ["a"], [["sibling"]], "a", {"a": "workflow", "sibling": "workflow"},
    )
