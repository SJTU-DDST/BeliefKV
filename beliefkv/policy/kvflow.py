"""KVFlow paper strategy components (arXiv:2507.07400, Sections 3.1-3.2).

Graph registration is an explicit application input. A replay driver's private
future trajectory is not an admissible source of this graph.
"""

from __future__ import annotations

from dataclasses import dataclass
from graphlib import TopologicalSorter
import math
from typing import Iterable, Mapping


@dataclass(frozen=True)
class StepNode:
    node_id: str
    agent_id: str
    predecessors: tuple[str, ...] = ()
    aggregation: str = "all"


class StepGraph:
    """Invocation nodes with max-plus ALL or min-plus ANY dependencies."""

    def __init__(self, nodes: Iterable[StepNode], *, information_source: str):
        if information_source not in ("application_declared", "observed_events"):
            raise ValueError("KVFlow graph must be application-declared or already observed")
        self.information_source = information_source
        nodes = tuple(nodes)
        self.nodes = {node.node_id: node for node in nodes}
        if not nodes or len(self.nodes) != len(nodes):
            raise ValueError("KVFlow requires distinct nonempty invocation nodes")
        if any(
            not isinstance(node.node_id, str) or not node.node_id
            or not isinstance(node.agent_id, str) or not node.agent_id
            or node.aggregation not in ("all", "any")
            or len(set(node.predecessors)) != len(node.predecessors)
            or not set(node.predecessors) <= self.nodes.keys()
            for node in nodes
        ):
            raise ValueError("invalid KVFlow step node or dependency")
        try:
            self.order = tuple(TopologicalSorter({
                node.node_id: node.predecessors for node in nodes
            }).static_order())
        except ValueError as error:
            raise ValueError("unroll repeated invocations before registering a KVFlow graph") from error

    def invocation_steps(self, *, completed=(), executing=(), unavailable=()):
        completed, executing, unavailable = map(set, (completed, executing, unavailable))
        if (
            not completed | executing | unavailable <= self.nodes.keys()
            or completed & executing or completed & unavailable or executing & unavailable
        ):
            raise ValueError("KVFlow observed invocation states overlap or reference unknown nodes")
        steps = {}
        for identity in self.order:
            node = self.nodes[identity]
            if identity in completed:
                steps[identity] = -1
            elif identity in unavailable:
                steps[identity] = math.inf
            elif identity in executing or not node.predecessors:
                steps[identity] = 0
            else:
                aggregate = max if node.aggregation == "all" else min
                steps[identity] = 1 + aggregate(steps[parent] for parent in node.predecessors)
        return {identity: distance for identity, distance in steps.items() if identity not in completed}

    def agent_steps(self, *, completed=(), executing=(), unavailable=()):
        steps = self.invocation_steps(
            completed=completed, executing=executing, unavailable=unavailable,
        )
        result = dict.fromkeys((node.agent_id for node in self.nodes.values()), math.inf)
        for identity, distance in steps.items():
            agent = self.nodes[identity].agent_id
            result[agent] = min(result[agent], distance)
        return result


@dataclass(frozen=True)
class CacheNode:
    node_id: int
    parent_id: int | None
    fixed_prompt_owners: tuple[str, ...] = ()
    status: str = "gpu"
    device_leaf: bool = True
    locked: bool = False
    last_access: float = 0.


def cache_priorities(nodes: Iterable[CacheNode], agent_steps: Mapping[str, int | float]):
    """Propagate nearest fixed-prompt anchors; unowned suffixes get infinity."""
    rows = tuple(nodes)
    by_id = {node.node_id: node for node in rows}
    if len(by_id) != len(rows) or any(
        type(node.node_id) is not int
        or node.parent_id is not None and node.parent_id not in by_id
        or node.status not in ("gpu", "cpu_backup", "loading", "offloading")
        or not math.isfinite(node.last_access)
        for node in rows
    ):
        raise ValueError("invalid KVFlow cache tree")
    if any(
        type(value) not in (int, float) or math.isnan(value) or value < 0
        for value in agent_steps.values()
    ):
        raise ValueError("invalid KVFlow agent distance")
    try:
        order = tuple(TopologicalSorter({
            node.node_id: () if node.parent_id is None else (node.parent_id,) for node in rows
        }).static_order())
    except ValueError as error:
        raise ValueError("cyclic KVFlow cache tree") from error
    priorities = {
        node.node_id: min(
            (agent_steps.get(owner, math.inf) for owner in node.fixed_prompt_owners),
            default=math.inf,
        )
        for node in rows
    }
    for identity in reversed(order):
        parent = by_id[identity].parent_id
        if parent is not None:
            priorities[parent] = min(priorities[parent], priorities[identity])
    return priorities


def eviction_order(candidates: Iterable[CacheNode], priorities: Mapping[int, int | float]):
    """Native guards and leaf discovery remain authoritative on each removal."""
    return tuple(node.node_id for node in sorted(
        (
            node for node in candidates
            if node.device_leaf and not node.locked and node.status == "gpu"
        ),
        key=lambda node: (-priorities.get(node.node_id, math.inf), node.last_access, node.node_id),
    ))


@dataclass(frozen=True)
class PrefetchCandidate:
    agent_id: str
    missing_tokens: int
    required_tokens: int


def prefetch_candidates(
    candidates: Iterable[PrefetchCandidate], steps: Mapping[str, int | float], *,
    available_tokens: int, concurrent_limit: int, in_flight=(), lookahead_steps: int = 1,
):
    """Conservatively include possible next agents within actual capacity."""
    if (
        type(available_tokens) is not int or available_tokens < 0
        or type(concurrent_limit) is not int or concurrent_limit < 0
        or type(lookahead_steps) is not int or lookahead_steps < 0
    ):
        raise ValueError("KVFlow prefetch requires explicit nonnegative capacity and limits")
    pending, rows = set(in_flight), tuple(candidates)
    if len({row.agent_id for row in rows}) != len(rows) or any(
        not row.agent_id or type(row.missing_tokens) is not int or row.missing_tokens < 0
        or type(row.required_tokens) is not int or row.required_tokens < row.missing_tokens
        for row in rows
    ):
        raise ValueError("invalid or repeated KVFlow prefetch candidate")
    slots, selected = max(0, concurrent_limit - len(pending)), []
    for candidate in sorted(rows, key=lambda row: (steps.get(row.agent_id, math.inf), row.agent_id)):
        distance = steps.get(candidate.agent_id, math.inf)
        if (
            not slots or candidate.agent_id in pending or candidate.missing_tokens == 0
            or not 0 <= distance <= lookahead_steps
            or candidate.required_tokens > available_tokens
        ):
            continue
        selected.append(candidate.agent_id)
        available_tokens -= candidate.required_tokens
        slots -= 1
    return tuple(selected)


def request_cache_status(required_nodes: Iterable[CacheNode]):
    statuses = {node.status for node in required_nodes}
    if not statuses <= {"gpu", "cpu_backup", "loading", "offloading"}:
        raise ValueError("unknown KVFlow cache transfer status")
    if "loading" in statuses:
        return "pending"
    if "cpu_backup" in statuses:
        return "restore"
    return "ready"


class FixedPromptHistory:
    """The paper's cache-hit-history alternative to an explicit fixed boundary.

    The caller supplies a verified same-agent prior-prefix hit; this class never
    reads later prompts or interprets a request's full input as a fixed prompt.
    """

    def __init__(self):
        self.boundaries: dict[str, int] = {}

    def observe(self, agent: str, *, prompt_tokens: int, prior_prefix_hit: int | None):
        if (
            not agent or type(prompt_tokens) is not int or prompt_tokens <= 0
            or prior_prefix_hit is not None and (
                type(prior_prefix_hit) is not int or not 0 <= prior_prefix_hit <= prompt_tokens
            )
        ):
            raise ValueError("invalid same-agent fixed-prefix evidence")
        if prior_prefix_hit is not None:
            previous = self.boundaries.get(agent, prior_prefix_hit)
            self.boundaries[agent] = min(previous, prior_prefix_hit)
        return min(prompt_tokens, self.boundaries.get(agent, 0))

    def forget(self, agent: str):
        self.boundaries.pop(agent, None)
