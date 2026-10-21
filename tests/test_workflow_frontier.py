from types import SimpleNamespace as NS

from beliefkv.control.causal_graph import ExecutionMode, InvocationState as State
from beliefkv.policy.workflow_completion import WorkflowCompletionOrder
from beliefkv.policy.workflow_frontier import observed_completion_frontier
from tests.test_audit_program_scheduling_signals import graph_fixture


def background_children(graph):
    for identity in ("child", "grandchild", "tool"):
        graph.invocations[identity].execution_mode = ExecutionMode.BACKGROUND
    return graph


def test_joined_background_tasks_belong_to_the_completion_frontier():
    graph = background_children(graph_fixture())
    frontier = observed_completion_frontier(graph, "wf")
    assert frontier.leaf_ids == {"grandchild", "tool"}
    assert frontier.invocation_ids == {"root", "child", "grandchild", "tool"}
    assert not frontier.unknown_dependency_ids
    assert WorkflowCompletionOrder(graph).width("wf") == 2


def test_detached_background_task_is_not_included_just_because_it_has_a_parent():
    graph = background_children(graph_fixture())
    graph.invocations["root"].state = State.READY
    frontier = observed_completion_frontier(graph, "wf")
    assert frontier.invocation_ids == frontier.leaf_ids == {"root"}


def test_wait_child_membership_is_sufficient_without_a_join_or_foreground_flag():
    graph = background_children(graph_fixture())
    graph.invocations["root"].state = State.WAIT_CHILD
    graph.invocations["root"].blocking_child_ids = {"child"}
    frontier = observed_completion_frontier(graph, "wf")
    assert frontier.invocation_ids == {"root", "child", "grandchild"}
    assert frontier.leaf_ids == {"grandchild"}


def test_unknown_wait_dependency_retains_fifo_instead_of_ranking_a_workflow_as_small():
    graph = background_children(graph_fixture())
    graph.joins["inner"].member_invocation_ids.add("not-yet-observed")
    policy = WorkflowCompletionOrder(graph)
    assert policy.width("wf") is None
    pairs = [(0, "r")]
    keys = {0: NS(root_workflow_id="wf")}
    assert policy.order(pairs, keys, attained={}, critical=lambda _: False) == pairs
    assert policy.focus is None


def test_event_invalidation_recomputes_the_actual_join_dependency():
    graph = background_children(graph_fixture())
    policy = WorkflowCompletionOrder(graph)
    assert policy.width("wf") == 2
    graph.invocations["tool"].state = State.DONE
    graph.joins["outer"].completed_member_ids.add("tool")
    policy.invalidate("wf")
    assert policy.width("wf") == 1
