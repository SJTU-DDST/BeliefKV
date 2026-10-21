"""Bounded workflow affinity using only the already observed execution graph."""

from __future__ import annotations

from beliefkv.control.causal_graph import ExecutionMode


class WorkflowCompletionOrder:
    def __init__(self, graph, *, quantum: int = 4):
        if type(quantum) is not int or quantum < 1:
            raise ValueError("positive workflow admission quantum required")
        self.graph = graph
        self.quantum = quantum
        self.focus: str | None = None
        self.used = 0
        self.fair_turn = False
        self._widths: dict[str, int] = {}
        self._admitted: set[str] = set()

    def invalidate(self, workflow_id: str) -> None:
        self._widths.pop(workflow_id, None)
        workflow = self.graph.workflows.get(workflow_id)
        if workflow is None or workflow.end_ts_ms is not None:
            if self.focus == workflow_id:
                self.focus, self.used = None, 0

    def width(self, workflow_id: str) -> int:
        if workflow_id not in self._widths:
            workflow = self.graph.workflows.get(workflow_id)
            invocations = self.graph.invocations
            active = {
                identity for identity in workflow.invocation_ids
                if not invocations[identity].state.terminal
                and invocations[identity].execution_mode is ExecutionMode.FOREGROUND
            } if workflow is not None else set()
            # This is the visible branch count, never a remaining-work estimate.
            self._widths[workflow_id] = sum(
                not (invocations[identity].child_invocation_ids & active)
                for identity in active
            )
        return self._widths[workflow_id]

    def order(self, ordered, keys, *, attained, critical):
        if not ordered or self.fair_turn:
            return ordered
        present = {keys[index].root_workflow_id for index, _ in ordered}
        if self.focus not in present:
            self.used = 0
            first = {}
            for position, (index, _) in enumerate(ordered):
                first.setdefault(keys[index].root_workflow_id, position)
            self.focus = min(present, key=lambda workflow: (
                self.width(workflow), attained.get(workflow, 0), first[workflow],
            ))
        selected, others = [], []
        for pair in ordered:
            (selected if keys[pair[0]].root_workflow_id == self.focus else others).append(pair)
        selected.sort(key=lambda pair: not critical(keys[pair[0]]))
        # The batch may consume every returned candidate; never leave slots idle.
        remaining = self.quantum - self.used
        return selected[:remaining] + others + selected[remaining:]

    def admitted(
        self, workflow_id: str, request_id: str, *, priority_override: bool = False,
    ) -> None:
        if request_id in self._admitted:
            return
        self._admitted.add(request_id)
        if priority_override:
            return
        if self.fair_turn:
            self.fair_turn = False
            self.focus, self.used = None, 0
        elif workflow_id == self.focus:
            self.used += 1
            if self.used >= self.quantum:
                self.fair_turn = True
                self.focus, self.used = None, 0

    def completed(self, request_id: str) -> None:
        self._admitted.discard(request_id)
