"""Completion dependencies observed so far, including explicitly awaited tasks."""

from __future__ import annotations

from dataclasses import dataclass

from beliefkv.control.causal_graph import ExecutionMode, InvocationState


@dataclass(frozen=True)
class ObservedCompletionFrontier:
    invocation_ids: frozenset[str]
    leaf_ids: frozenset[str]
    unknown_dependency_ids: frozenset[str]


def observed_completion_frontier(graph, workflow_id: str) -> ObservedCompletionFrontier:
    workflow = graph.workflows.get(workflow_id)
    if workflow is None:
        return ObservedCompletionFrontier(frozenset(), frozenset(), frozenset({workflow_id}))
    invocations = graph.invocations
    active = {
        identity for identity in workflow.invocation_ids
        if identity in invocations and not invocations[identity].state.terminal
    }
    foreground = {
        identity for identity in active
        if invocations[identity].execution_mode is ExecutionMode.FOREGROUND
    }
    pending, scope, leaves, unknown = list(foreground), set(), set(), set()
    joins = getattr(graph, "joins", {})
    while pending:
        identity = pending.pop()
        if identity in scope:
            continue
        scope.add(identity)
        invocation = invocations[identity]
        dependencies = invocation.child_invocation_ids & foreground
        if invocation.state is InvocationState.WAIT_CHILD:
            dependencies |= invocation.blocking_child_ids
        elif invocation.state is InvocationState.WAIT_JOIN:
            join_id = invocation.join_id
            join = joins.get(join_id)
            if (
                join is None or join.satisfied or identity not in join.waiter_invocation_ids
                or getattr(join, "workflow_id", workflow_id) != workflow_id
            ):
                unknown.add(join_id or identity)
            else:
                dependencies |= join.member_invocation_ids - join.completed_member_ids
        live = set()
        for child_id in dependencies:
            child = invocations.get(child_id)
            if child is None or getattr(child, "workflow_id", workflow_id) != workflow_id:
                unknown.add(child_id)
            elif not child.state.terminal:
                live.add(child_id)
        if not live:
            leaves.add(identity)
        pending.extend(live - scope)
    return ObservedCompletionFrontier(frozenset(scope), frozenset(leaves), frozenset(unknown))
