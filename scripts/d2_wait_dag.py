"""Propagate proposed request wait savings through a fixed causal replay DAG."""

from __future__ import annotations

from collections.abc import Mapping
from graphlib import CycleError, TopologicalSorter
from sys import float_info

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.causal_replay import CausalReplayError, CausalReplayPlan, ReplayNode


def _nonnegative_ms(value: float, label: str) -> float:
    if type(value) not in (int, float) or not 0 <= value <= float_info.max:
        raise CausalReplayError(f"{label} must be finite and nonnegative")
    return value


def replay_wait_savings(
    plan: CausalReplayPlan,
    observed_node_ms: Mapping[str, float],
    request_advances_ms: Mapping[str, float],
) -> dict[str, float]:
    """Return observed minus reconstructed JCT for each workflow, in ms.

    Supply common-clock timestamps keyed by ReplayNode.node_id, including
    native request receipts. Advances are keyed by ReplayNode.request_id and
    default to zero. Roots stay fixed. An advance may reduce a receipt's
    observed duration to zero. Invalid inputs raise CausalReplayError.

    For a non-root node i, let m be its latest observed parent timestamp.
    Its reconstructed time is max(reconstructed parent) + (observed[i] - m)
    - advance[i]. Induction from the fixed roots recovers the observed times
    when all advances are zero. Writing s[p] for each parent's time saved
    yields s[i] = min(m - observed[p] + s[p]) + advance[i] over all parents.
    This form can switch the controlling parent at a JOIN and avoids
    subtracting large absolute timestamps to recover small savings. Since
    the workflow start is fixed, s at WORKFLOW_END is the JCT savings.

    The estimate holds for the supplied advances, fixed dependencies and
    fixed residual durations. Those durations can include contention from
    the observed run. Actual gains depend on whether the wait is removable
    and how the intervention changes scheduling, contention, and overlap.
    """
    for request, advance in request_advances_ms.items():
        _nonnegative_ms(advance, f"advance for request {request!r}")
    return replay_request_shifts(plan, observed_node_ms, request_advances_ms)


def replay_request_shifts(
    plan: CausalReplayPlan,
    observed_node_ms: Mapping[str, float],
    request_savings_ms: Mapping[str, float],
) -> dict[str, float]:
    """Replay signed receipt savings, including whole additional restore waits."""
    nodes: dict[str, ReplayNode] = {node.node_id: node for node in plan.nodes}
    if len(nodes) != len(plan.nodes):
        raise CausalReplayError("duplicate replay node ID")

    observed: dict[str, float] = {}
    requests: dict[str, str] = {}
    starts: dict[str, str] = {}
    ends: dict[str, str] = {}
    boundaries = {Kind.WORKFLOW_START: starts, Kind.WORKFLOW_END: ends}
    for key, node in nodes.items():
        if key not in observed_node_ms:
            raise CausalReplayError(f"missing observed timestamp for node {key!r}")
        observed[key] = _nonnegative_ms(observed_node_ms[key], f"timestamp for node {key!r}")
        if node.request_id is not None:
            if node.request_id in requests:
                raise CausalReplayError(f"duplicate replay request {node.request_id!r}")
            requests[node.request_id] = key
        if node.event is not None and node.event.kind in boundaries:
            kind = node.event.kind
            boundary = boundaries[kind]
            if node.workflow_id in boundary:
                raise CausalReplayError(f"duplicate {kind.value} for {node.workflow_id!r}")
            if kind is Kind.WORKFLOW_START and node.dependencies:
                raise CausalReplayError(f"workflow start must be a root: {key!r}")
            boundary[node.workflow_id] = key
        for parent_id in node.dependencies:
            if parent_id not in nodes:
                raise CausalReplayError(f"unknown replay predecessor {parent_id!r} for {key!r}")
            if nodes[parent_id].workflow_id != node.workflow_id:
                raise CausalReplayError(f"cross-workflow edge {parent_id!r} -> {key!r}")
    workflows = {node.workflow_id for node in nodes.values()}
    if starts.keys() != workflows or ends.keys() != workflows:
        raise CausalReplayError("each workflow needs one start and one end node")

    advances: dict[str, float] = {}
    for request, advance in request_savings_ms.items():
        if request not in requests:
            raise CausalReplayError(f"unknown request {request!r}")
        if type(advance) not in (int, float) or not -float_info.max <= advance <= float_info.max:
            raise CausalReplayError(f"shift for request {request!r} must be finite")
        advances[requests[request]] = advance

    try:
        order = tuple(TopologicalSorter(plan.dependency_graph()).static_order())
    except CycleError as error:
        raise CausalReplayError("cyclic replay dependencies") from error

    saved: dict[str, float] = {}
    for key in order:
        node = nodes[key]
        parents = node.dependencies
        advance = advances.get(key, 0)
        if not parents:
            if advance:
                raise CausalReplayError(f"cannot advance fixed root {key!r}")
            if observed[key] < observed[starts[node.workflow_id]]:
                raise CausalReplayError(f"root {key!r} precedes its workflow start")
            saved[key] = 0
            continue

        latest_parent = max(observed[parent] for parent in parents)
        if observed[key] < latest_parent:
            raise CausalReplayError(f"negative observed duration at node {key!r}")
        if advance > observed[key] - latest_parent:
            raise CausalReplayError(f"advance exceeds observed duration at node {key!r}")
        saved[key] = min(
            latest_parent - observed[parent] + saved[parent] for parent in parents
        ) + advance

    return {workflow: saved[end] for workflow, end in ends.items()}
