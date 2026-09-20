from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from beliefkv.control.causal_graph import (
    ExecutionMode,
    InvocationRecord,
    InvocationState,
    RuntimeCausalContextGraph,
)


@dataclass(frozen=True)
class FrontierCandidate:
    invocation_id: str
    workflow_id: str
    context_id: str
    causal_class: str
    unblock_depth: int
    known_downstream_count: int
    join_waiter_count: int
    score: tuple[int, int, int, int, float, str]


class CausalFrontierScheduler:
    """Select work that advances an observed workflow's causal frontier."""

    def __init__(self, graph: RuntimeCausalContextGraph) -> None:
        self.graph = graph
        self._active_descendant_cache_version: int | None = None
        self._active_descendant_cache: dict[str, int] = {}

    def candidates(self, workflow_id: str) -> list[FrontierCandidate]:
        active_descendants = self._active_descendant_counts()
        candidates = [
            self._candidate(invocation, active_descendants)
            for invocation in self.graph.ready_invocations(workflow_id)
        ]
        return sorted(candidates, key=lambda item: item.score)

    def select(self, workflow_id: str) -> FrontierCandidate | None:
        candidates = self.candidates(workflow_id)
        return candidates[0] if candidates else None

    def describe_invocation(self, invocation_id: str) -> FrontierCandidate:
        """Classify one observed invocation even while it is running."""

        return self._candidate(
            self.graph.invocations[invocation_id],
            self._active_descendant_counts(),
        )

    def _candidate(
        self,
        invocation: InvocationRecord,
        active_descendants: Mapping[str, int] | None = None,
    ) -> FrontierCandidate:
        active_descendants = active_descendants or {}
        join_waiter_count = self._join_waiter_count(invocation.invocation_id)
        join_straggler = join_waiter_count > 0
        unblock_depth = self._unblock_depth(invocation)
        known_downstream_count = max(
            0, int(active_descendants.get(invocation.invocation_id, 0))
        )
        message_ready = invocation.pending_messages > 0
        background = invocation.execution_mode == ExecutionMode.BACKGROUND

        if join_straggler:
            causal_class = "join_straggler"
            class_rank = 0
        elif unblock_depth > 0:
            causal_class = "blocking_chain"
            class_rank = 1
        elif message_ready:
            causal_class = "message_ready"
            class_rank = 2
        elif background:
            causal_class = "background"
            class_rank = 4
        else:
            causal_class = "ready"
            class_rank = 3

        return FrontierCandidate(
            invocation_id=invocation.invocation_id,
            workflow_id=invocation.workflow_id,
            context_id=invocation.context_id,
            causal_class=causal_class,
            unblock_depth=unblock_depth,
            known_downstream_count=known_downstream_count,
            join_waiter_count=join_waiter_count,
            score=(
                class_rank,
                -unblock_depth,
                -invocation.pending_messages,
                -known_downstream_count,
                invocation.updated_ts_ms,
                invocation.invocation_id,
            ),
        )

    def _join_waiter_count(self, invocation_id: str) -> int:
        waiter_count = 0
        for join in self.graph.joins.values():
            if join.satisfied or invocation_id not in join.member_invocation_ids:
                continue
            remaining = join.member_invocation_ids - join.completed_member_ids
            if remaining == {invocation_id} and join.waiter_invocation_ids:
                waiter_count += len(join.waiter_invocation_ids)
        return waiter_count

    def _unblock_depth(self, invocation: InvocationRecord) -> int:
        depth = 0
        child_id = invocation.invocation_id
        for ancestor in self.graph.ancestors(invocation.invocation_id):
            if (
                ancestor.state == InvocationState.WAIT_CHILD
                and child_id in ancestor.blocking_child_ids
                and ancestor.blocking_child_ids == {child_id}
            ):
                depth += 1
                child_id = ancestor.invocation_id
            else:
                break
        return depth

    def _active_descendant_counts(self) -> dict[str, int]:
        if self._active_descendant_cache_version == self.graph.graph_version:
            return self._active_descendant_cache
        counts: dict[str, int] = {}

        def visit(invocation_id: str, seen: set[str]) -> int:
            if invocation_id in seen:
                raise ValueError("invocation graph contains a cycle")
            if invocation_id in counts:
                return counts[invocation_id]
            local_seen = seen | {invocation_id}
            invocation = self.graph.invocations[invocation_id]
            total = 0
            for child_id in invocation.child_invocation_ids:
                child = self.graph.invocations.get(child_id)
                if child is None or child.state.terminal:
                    continue
                total += 1 + visit(child_id, local_seen)
            counts[invocation_id] = total
            return total

        for invocation_id in self.graph.invocations:
            visit(invocation_id, set())
        self._active_descendant_cache_version = self.graph.graph_version
        self._active_descendant_cache = counts
        return counts

    def ancestor_distance_to_active_leaf(self, invocation_id: str) -> int:
        invocation = self.graph.invocations[invocation_id]
        descendants = self.graph.active_descendants(invocation_id)
        if not descendants:
            return 0
        max_distance = 0
        for descendant in descendants:
            distance = 0
            current = descendant
            while current.parent_invocation_id is not None:
                distance += 1
                if current.parent_invocation_id == invocation.invocation_id:
                    max_distance = max(max_distance, distance)
                    break
                current = self.graph.invocations[current.parent_invocation_id]
        return max_distance
