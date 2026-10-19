"""Fail-closed accounting for v0.5.20 native FULL/MAMBA transfer commits.

This module observes completed native ACKs; it does not initiate transfers or
authorize eviction. The caller must supply the live context epochs at each ACK.
Native receipts contain pool token counts and total bytes, not per-pool bytes.
Pool byte accounting therefore uses the caller's frozen per-token pool sizes.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace
from math import isfinite
from time import monotonic
from typing import Callable, KeysView, Mapping

from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from beliefkv.runtime.sglang_v0520_observer import (
    StaticPoolHeadroomObservation,
    UnifiedNodeSummary,
    observe_static_full_mamba_headroom,
    observe_unified_node_closure,
    normalize_native_creation_time,
)


class PhysicalReceiptError(ValueError):
    """A command cannot be credited from the observed native ACK."""


@dataclass(frozen=True)
class ContextSessionAnchors:
    """Session leaf provenance only; shared ownership is not established."""

    key: PrefillCandidateKey
    component_leaves: tuple[tuple[int, tuple[tuple[int, int | float], ...]], ...]
    captured_monotonic_s: float
    reusable_input_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class _PreparePrefixNode:
    node_id: int
    parent_id: int | None
    key_tokens: int
    full_device_tokens: int
    full_host_tokens: int
    mamba_device_present: bool
    mamba_host_present: bool


@dataclass(frozen=True)
class ActionLocalShadowCandidate:
    """Bounded, read-only FULL/MAMBA closure; not an action certificate."""

    anchors: ContextSessionAnchors
    nodes: tuple[UnifiedNodeSummary, ...]
    missing_full_host_tokens: int
    missing_mamba_host_nodes: int


@dataclass(frozen=True)
class ActionLocalPrefetchCandidate:
    """Bounded host-backed FULL/MAMBA closure; not native load authorization."""

    anchors: ContextSessionAnchors
    nodes: tuple[UnifiedNodeSummary, ...]
    missing_full_device_tokens: int
    missing_mamba_device_nodes: int


@dataclass(frozen=True)
class PrefetchCheckpointPlan:
    """Complete restore cost from one immutable action-local snapshot."""

    steps: tuple[PrefetchLoadStep, ...]
    full_tokens: int
    mamba_slots: int


@dataclass(frozen=True)
class SessionH2DOpportunity:
    """Safe-point evidence, never an allocator reservation or action permit."""

    anchors: ContextSessionAnchors
    headroom: StaticPoolHeadroomObservation
    step: PrefetchLoadStep | None
    required_full_tokens: int
    required_mamba_slots: int
    fits_current_free_lists: bool | None
    no_step_reason: str | None = None
    host_backed_full_missing_device_tokens: int | None = None
    host_backed_mamba_missing_device_nodes: int | None = None
    unbacked_full_nodes: int | None = None
    unbacked_mamba_leaves: int | None = None
    blocked_detail: str | None = None
    steps: tuple[PrefetchLoadStep, ...] = ()
    candidate: ActionLocalPrefetchCandidate | None = field(
        default=None, compare=False, repr=False,
    )
    deferred_mamba_slots: int = 0
    capacity_budget: dict[str, int | str] | None = field(
        default=None, compare=False, repr=False,
    )
    partial_full_step: PrefetchLoadStep | None = None
    partial_full_tokens: int = 0
    capacity_reclaim_probe: bool = False
    capacity_reclaim: dict | None = field(
        default=None, compare=False, repr=False,
    )
    enqueue_refresh_required: bool = field(default=False, compare=False, repr=False)
    checkpoint_plan: PrefetchCheckpointPlan | None = field(
        default=None, compare=False, repr=False,
    )


def _prefetch_block_detail(candidate: ActionLocalPrefetchCandidate) -> str:
    """Explain an observed rejection without relaxing the native selector."""
    nodes = {node.node_id: node for node in candidate.nodes}
    if len(nodes) != len(candidate.nodes):
        return "duplicate_closure_node"
    try:
        components = dict(candidate.anchors.component_leaves)
        full_leaves = dict(components[0])
        mamba_leaves = dict(components[2])
    except (KeyError, TypeError, ValueError):
        return "invalid_component_leaves"
    full_paths: set[int] = set()
    for leaf_id in full_leaves:
        current = leaf_id
        visited: set[int] = set()
        while current is not None:
            if current not in nodes or current in visited:
                return "invalid_full_ancestry"
            visited.add(current)
            full_paths.add(current)
            current = nodes[current].parent_id
    if any(leaf_id not in full_paths for leaf_id in mamba_leaves):
        return "mamba_leaf_outside_full_path"
    if set(nodes) != full_paths:
        return "closure_outside_full_path"
    for node in candidate.nodes:
        host_backed_missing_device = (
            node.full_device_tokens == 0 and node.full_host_tokens > 0
            or node.full_device_tokens > 0
            and node.mamba_host_present and not node.mamba_device_present
        )
        parent = nodes.get(node.parent_id)
        if host_backed_missing_device and parent is not None and (
            parent.full_device_tokens <= 0
            and not (
                parent.parent_id is None
                and parent.full_host_tokens == 0
                and not parent.mamba_host_present
                and not parent.mamba_device_present
            )
        ):
            return "parent_full_not_device"
    return "other_selector_invariant"


def inspect_session_h2d_opportunity(
    cache: object, anchors: ContextSessionAnchors,
    *, max_steps: int = 1, fit_current_capacity: bool = False,
    skip_empty_restore: bool = False, include_checkpoint_plan: bool = False,
) -> SessionH2DOpportunity:
    if type(max_steps) is not int or max_steps < 1:
        raise ValueError("invalid prefetch burst bound")
    headroom = observe_static_full_mamba_headroom(cache)
    if skip_empty_restore and has_host_backed_missing_copy(
        cache, anchors, input_checkpoint_only=True,
    ) is False:
        return SessionH2DOpportunity(
            anchors, headroom, None, 0, 0, None,
            no_step_reason="no_host_backed_missing_copy",
            host_backed_full_missing_device_tokens=0,
            # Historical states may still be Host-only outside the selected input.
            host_backed_mamba_missing_device_nodes=None,
        )
    capture_failures = []
    candidate = capture_action_local_shadow(
        cache, anchors, for_prefetch=True, include_non_actionable=True,
        on_failure=capture_failures.append,
    )
    steps = plan_prefetch_gpu_steps(
        candidate, max_steps=None if include_checkpoint_plan else max_steps,
    ) if candidate is not None else ()
    nodes = {node.node_id: node for node in candidate.nodes} if steps else {}
    checkpoint_plan = None
    if include_checkpoint_plan:
        checkpoint_plan = PrefetchCheckpointPlan(
            steps,
            sum(
                nodes[planned.node_id].full_host_tokens for planned in steps
                if nodes[planned.node_id].full_device_tokens == 0
            ),
            sum(
                planned.include_mamba and nodes[planned.node_id].mamba_host_present
                and not nodes[planned.node_id].mamba_device_present
                for planned in steps
            ),
        )
        steps = steps[:max_steps]
    step = steps[0] if steps else None
    no_step_reason = None
    blocked_detail = None
    unbacked_full = unbacked_mamba = None
    if candidate is not None:
        mamba_leaves = dict(dict(anchors.component_leaves).get(2, ()))
        unbacked_full = sum(
            node.parent_id is not None
            and node.full_device_tokens == 0 and node.full_host_tokens == 0
            for node in candidate.nodes
        )
        unbacked_mamba = sum(
            node.node_id in mamba_leaves
            and not node.mamba_device_present and not node.mamba_host_present
            for node in candidate.nodes
        )
    if step is None:
        if candidate is None:
            no_step_reason = "closure_unobservable"
            blocked_detail = capture_failures[0] if capture_failures else None
        elif anchors.reusable_input_tokens is not None:
            no_step_reason = "no_reusable_input_restore_step"
            blocked_detail = "input_checkpoint_unavailable_or_already_resident"
        elif candidate.missing_full_device_tokens or candidate.missing_mamba_device_nodes:
            no_step_reason = "host_backed_step_blocked"
            blocked_detail = _prefetch_block_detail(candidate)
        elif unbacked_full or unbacked_mamba:
            no_step_reason = "no_host_backed_step"
        else:
            no_step_reason = "already_device_resident"
    full_tokens = mamba_slots = 0
    if steps:
        fitting_steps = []
        for planned in steps:
            node = nodes[planned.node_id]
            next_full = full_tokens + (
                node.full_host_tokens if node.full_device_tokens == 0 else 0
            )
            next_mamba = mamba_slots + int(
                planned.include_mamba
                and node.mamba_host_present and not node.mamba_device_present
            )
            if fit_current_capacity and headroom.observable and fitting_steps and (
                next_full > headroom.device_full_free_tokens
                or next_mamba > headroom.device_mamba_free_slots
            ):
                steps = tuple(fitting_steps)
                break
            full_tokens, mamba_slots = next_full, next_mamba
            fitting_steps.append(planned)
        step = steps[0]
    fits = (
        headroom.device_full_free_tokens >= full_tokens
        and headroom.device_mamba_free_slots >= mamba_slots
        if headroom.observable and step is not None else None
    )
    if (
        checkpoint_plan is None and steps and not fit_current_capacity
        and len(steps) < max_steps
    ):
        # The bounded planner exhausted this snapshot without extra work.
        # Keep its complete input plan for post-enqueue tail capacity planning.
        checkpoint_plan = PrefetchCheckpointPlan(steps, full_tokens, mamba_slots)
    return SessionH2DOpportunity(
        anchors, headroom, step, full_tokens, mamba_slots, fits, no_step_reason,
        candidate.missing_full_device_tokens if candidate is not None else None,
        candidate.missing_mamba_device_nodes if candidate is not None else None,
        unbacked_full, unbacked_mamba,
        blocked_detail, steps, candidate,
        checkpoint_plan=checkpoint_plan,
    )


@dataclass(frozen=True)
class ShadowBackupStep:
    """One missing FULL prefix extent; native must recheck before D2H."""

    key: PrefillCandidateKey
    leaf_node_id: int
    leaf_creation_time: int | float
    node_id: int
    creation_time: int | float
    include_mamba: bool = False
    # The remaining prefix budget is an observation, not the DMA identity.
    missing_full_prefix_tokens: int = field(default=0, compare=False)


@dataclass(frozen=True, slots=True)
class PrefetchLoadStep:
    """One native H2D target bound to a FULL session leaf and its epochs."""

    key: PrefillCandidateKey
    leaf_node_id: int
    leaf_creation_time: int | float
    node_id: int
    creation_time: int | float
    include_mamba: bool = True


def _checkpoint_paths(
    anchors: ContextSessionAnchors,
    nodes: Mapping[int, UnifiedNodeSummary | _PreparePrefixNode],
    leaves: Mapping[int, int | float],
    depth: Mapping[int, int],
    *,
    ordered_nodes: list[int] | None = None,
) -> tuple[set[int], set[int]] | None:
    """Only the deepest reusable state on each current input path is needed."""
    input_limit = anchors.reusable_input_tokens
    if input_limit is None:
        return set(nodes), set(dict(dict(anchors.component_leaves).get(2, ())))
    if type(input_limit) is not int or input_limit < 0:
        return None
    if len(leaves) == 1 and ordered_nodes is not None:
        # The caller already validated this complete root-first ancestry.
        prefix = 0
        checkpoint_position = None
        for position, node_id in enumerate(ordered_nodes):
            node = nodes[node_id]
            if type(node.key_tokens) is not int or node.key_tokens < 0:
                return None
            prefix += node.key_tokens
            if (
                node.parent_id is not None and prefix <= input_limit
                and (node.mamba_device_present or node.mamba_host_present)
            ):
                checkpoint_position = position
        if checkpoint_position is None:
            return set(), set()
        return (
            set(ordered_nodes[:checkpoint_position + 1]),
            {ordered_nodes[checkpoint_position]},
        )
    prefix_lengths: dict[int, int] = {}
    if ordered_nodes is None:
        ordered_nodes = sorted(nodes, key=lambda value: (depth[value], value))
    for node_id in ordered_nodes:
        node = nodes[node_id]
        if type(node.key_tokens) is not int or node.key_tokens < 0:
            return None
        prefix_lengths[node_id] = prefix_lengths.get(node.parent_id, 0) + node.key_tokens
    paths: set[int] = set()
    checkpoints: set[int] = set()
    for leaf in leaves:
        current = leaf
        while current is not None:
            node = nodes[current]
            if (
                node.parent_id is not None
                and prefix_lengths[current] <= input_limit
                and (node.mamba_device_present or node.mamba_host_present)
            ):
                checkpoints.add(current)
                while current is not None:
                    paths.add(current)
                    current = nodes[current].parent_id
                break
            current = node.parent_id
    return paths, checkpoints


def inspect_session_residency_hint(cache: object, request: object) -> dict | None:
    """Read session residency for ordering; input token contents remain unverified."""
    try:
        refs, tree = cache.session_refs, cache.tree_core
        session_id = refs.session_id_for_req(request)
        anchors = refs.snapshot_latest_session_leaf_anchors(
            session_id, getattr(request, "session_generation", None), max_leaves=8,
        )
        if anchors is None or tree.is_eagle:
            return None
        components = dict(anchors)
        leaves = components.get(0, ())
        if not leaves or len(dict(leaves)) != len(leaves) or len(leaves) > 8:
            return None
        root, lookup = tree.root_node, tree.node_by_id
        limit = max(0, len(request.origin_input_ids) - 1)
        namespace = (
            getattr(request, "extra_key", None), getattr(request, "cache_salt", None),
        )
        states = {} if len(leaves) > 1 else None
        best_prefix = device_prefix = best_device = best_missing = 0
        best_mamba = None
        for leaf_id, created in leaves:
            leaf = lookup(leaf_id)
            if (
                leaf.id != leaf_id
                or normalize_native_creation_time(leaf.creation_time)
                != normalize_native_creation_time(created)
            ):
                return None
            chain, current, seen = [], leaf, set()
            prefix = missing = 0
            device_prefix = 0
            full_device = full_available = True
            pending = pending_load = False
            while current is not root:
                if current is None or current.id in seen or lookup(current.id) is not current:
                    return None
                seen.add(current.id)
                prior = states.get(current.id) if states is not None else None
                if prior is not None:
                    if prior is False:
                        chain.clear()
                    else:
                        (
                            prefix, missing, device_prefix, full_device,
                            full_available, pending, pending_load,
                        ) = prior
                    break
                chain.append(current)
                current = current.parent
            for node in reversed(chain):
                key = node.key
                if (
                    getattr(key, "is_bigram", False)
                    or (key.extra_key, key.cache_salt) != namespace
                ):
                    return None
                prefix += len(key)
                if prefix > limit:
                    if states is not None:
                        states[node.id] = False
                    break
                full, state = node.component_data[0], node.component_data[2]
                full_device = full_device and full.value is not None
                full_available = full_available and (
                    full.value is not None or full.host_value is not None
                )
                if full.value is None and full.host_value is not None:
                    shape = getattr(full.host_value, "shape", None)
                    missing += int(shape[0]) if shape is not None else len(full.host_value)
                pending = pending or (
                    node.write_through_pending_id is not None
                    or node.load_back_pending_id is not None
                )
                pending_load = pending_load or node.load_back_pending_id is not None
                if state.value is not None or state.host_value is not None:
                    ready = full_device and state.value is not None and not pending_load
                    if ready:
                        device_prefix = prefix
                    if full_available and (ready or not pending) and prefix > best_prefix:
                        best_prefix = prefix
                        best_device = device_prefix
                        best_missing = missing
                        best_mamba = int(state.value is None)
                if states is not None:
                    states[node.id] = (
                        prefix, missing, device_prefix, full_device,
                        full_available, pending, pending_load,
                    )
        if best_prefix <= 0:
            return None
        return {
            "checkpoint_tokens": best_prefix, "device_checkpoint_tokens": best_device,
            "missing_full_tokens": best_missing, "missing_mamba_slots": best_mamba,
            "input_prefix_verified": False,
        }
    except (AttributeError, IndexError, KeyError, TypeError, ValueError, RuntimeError):
        return None


def has_host_backed_missing_copy(
    cache: object, anchors: ContextSessionAnchors, *,
    input_checkpoint_only: bool = False,
) -> bool | None:
    """Reject empty restores; optionally ignore unselected historical states."""
    if type(input_checkpoint_only) is not bool:
        return None
    try:
        input_limit = anchors.reusable_input_tokens if input_checkpoint_only else None
        if input_limit is not None and (type(input_limit) is not int or input_limit < 0):
            return None
        components = dict(anchors.component_leaves)
        if (
            len(components) != len(anchors.component_leaves)
            or set(components) != {0, 2}
            or not components[0] or not components[2]
            or sum(map(len, components.values())) > 8
        ):
            return None
        tree = cache.tree_core
        leaves = {}
        for component_leaves in components.values():
            for node_id, created in component_leaves:
                if type(node_id) is not int or node_id < 0:
                    return None
                node = tree.node_by_id(node_id)
                if (
                    node.id != node_id
                    or normalize_native_creation_time(node.creation_time) != created
                    or node_id in leaves and leaves[node_id] is not node
                ):
                    return None
                leaves[node_id] = node
        checked = {}
        missing_state = False
        single_leaf = components[0][0][0] if len(components[0]) == 1 else None
        single_path_nodes = None
        for leaf_id, node in leaves.items():
            path = set()
            while node is not None:
                node_id = node.id
                if (
                    type(node_id) is not int or node_id < 0 or node_id in path
                    or tree.node_by_id(node_id) is not node
                ):
                    return None
                prior = checked.get(node_id)
                if prior is not None:
                    if prior is not node:
                        return None
                    break
                path.add(node_id)
                full, state = node.component_data[0], node.component_data[2]
                if (full.value is None or len(full.value) == 0) and (
                    full.host_value is not None and len(full.host_value) > 0
                ):
                    return True
                if state.value is None and state.host_value is not None:
                    if not input_checkpoint_only or input_limit is None:
                        return True
                    missing_state = True
                checked[node_id] = node
                node = node.parent
            if leaf_id == single_leaf:
                single_path_nodes = len(checked)
        if not missing_state:
            return False
        # FULL is resident everywhere. Only the deepest input-compatible state
        # on each FULL path can produce a restore; older states are irrelevant.
        if single_path_nodes == len(checked):
            prefix, selected_missing = 0, False
            for node in reversed(checked.values()):
                prefix += len(node.key)
                if prefix > input_limit:
                    break
                state = node.component_data[2]
                if node.parent is not None and (
                    state.value is not None or state.host_value is not None
                ):
                    selected_missing = state.value is None
            return selected_missing
        checkpoints = {}
        for leaf_id, _ in components[0]:
            chain, node = [], checked[leaf_id]
            while node is not None and node.id not in checkpoints:
                chain.append(node)
                node = node.parent
            prefix, selected_missing = checkpoints[node.id] if node is not None else (0, False)
            for node in reversed(chain):
                prefix += len(node.key)
                state = node.component_data[2]
                if node.parent is not None and prefix <= input_limit and (
                    state.value is not None or state.host_value is not None
                ):
                    selected_missing = state.value is None
                checkpoints[node.id] = prefix, selected_missing
            if selected_missing:
                return True
        return False
    except (AttributeError, IndexError, KeyError, TypeError, ValueError, RuntimeError):
        return None


def missing_prepare_full_prefix_tokens(
    cache: object, anchors: ContextSessionAnchors, *, max_nodes: int | None = None,
    backed_nodes: list[tuple[int, int | float]] | None = None,
) -> int | None:
    """Report missing tokens and backed candidates when new backup is still needed."""
    if (
        (max_nodes is not None and (type(max_nodes) is not int or max_nodes <= 0))
        or type(anchors.reusable_input_tokens) is not int
        or anchors.reusable_input_tokens < 0
    ):
        return None
    try:
        components = dict(anchors.component_leaves)
        if (
            len(components) != len(anchors.component_leaves)
            or set(components) != {0, 2}
            or not components[0] or not components[2]
            or sum(map(len, components.values())) > 8
        ):
            return None
        leaves = dict(components[0])
        if len(leaves) != len(components[0]):
            return None
        for component_leaves in components.values():
            for node_id, created in component_leaves:
                if type(node_id) is not int or node_id < 0:
                    return None
                node = cache.tree_core.node_by_id(node_id)
                if node.id != node_id or normalize_native_creation_time(node.creation_time) != created:
                    return None
        nodes: dict[int, _PreparePrefixNode] = {}
        native_backed = {}
        device_leaves = getattr(cache.tree_core, "evictable_device_leaves", ())
        depth: dict[int, int] = {}
        for leaf in leaves:
            current = cache.tree_core.node_by_id(leaf)
            chain: list[int] = []
            seen: set[int] = set()
            while current is not None:
                node_id = current.id
                if (
                    type(node_id) is not int or node_id < 0
                    or node_id in seen
                    or (max_nodes is not None and len(chain) >= max_nodes)
                ):
                    return None
                seen.add(node_id)
                chain.append(node_id)
                parent = current.parent
                parent_id = None if parent is None else parent.id
                if parent_id is not None and (type(parent_id) is not int or parent_id < 0):
                    return None
                full, state = current.component_data[0], current.component_data[2]
                summary = _PreparePrefixNode(
                    node_id, parent_id, len(current.key),
                    0 if full.value is None else len(full.value),
                    0 if full.host_value is None else len(full.host_value),
                    state.value is not None, state.host_value is not None,
                )
                if node_id in nodes and nodes[node_id] != summary:
                    return None
                nodes[node_id] = summary
                if backed_nodes is not None and (
                    summary.full_device_tokens > 0
                    and summary.full_host_tokens >= summary.full_device_tokens
                    and current in device_leaves
                    or summary.mamba_device_present and summary.mamba_host_present
                ):
                    native_backed[node_id] = current
                if max_nodes is not None and len(nodes) > max_nodes:
                    return None
                current = parent
            for distance, node_id in enumerate(reversed(chain)):
                depth[node_id] = distance
        selected = _checkpoint_paths(anchors, nodes, leaves, depth)
        if selected is None:
            return None
        missing = sum(
            max(nodes[node_id].full_device_tokens - nodes[node_id].full_host_tokens, 0)
            for node_id in selected[0]
        )
        if missing and backed_nodes is not None:
            identities = []
            for node_id, native in native_backed.items():
                if node_id not in selected[0]:
                    continue
                if cache.tree_core.node_by_id(node_id) is not native:
                    return None
                identities.append((
                    node_id, normalize_native_creation_time(native.creation_time),
                ))
            backed_nodes.extend(identities)
        return missing
    except (AttributeError, IndexError, KeyError, TypeError, ValueError, RuntimeError):
        return None


def next_prefetch_gpu_step(
    candidate: ActionLocalPrefetchCandidate,
) -> PrefetchLoadStep | None:
    steps = plan_prefetch_gpu_steps(candidate, max_steps=1)
    return steps[0] if steps else None


def plan_prefetch_gpu_steps(
    candidate: ActionLocalPrefetchCandidate, *, max_steps: int | None = None,
) -> tuple[PrefetchLoadStep, ...]:
    """Restore FULL ancestors and the reusable input checkpoint root-first.

    Later steps may depend only on earlier steps in this same native burst.
    Native must revalidate session, ancestry, pool capacity and node creation
    before enqueue. Generated output need not survive chat-template round trips.
    """
    if max_steps is not None and (type(max_steps) is not int or max_steps < 1):
        raise ValueError("invalid prefetch burst bound")
    anchors = getattr(candidate, "anchors", None)
    if anchors is None:
        return ()
    if (
        type(anchors.key) is not PrefillCandidateKey
        or type(anchors.key.session_id) is not str
        or not anchors.key.session_id
        or type(anchors.key.session_generation) is not int
        or anchors.key.session_generation < 0
        or type(anchors.component_leaves) is not tuple
        or type(getattr(candidate, "nodes", None)) is not tuple
    ):
        return ()
    try:
        by_component = dict(anchors.component_leaves)
        nodes = {node.node_id: node for node in candidate.nodes}
        full_leaves = dict(by_component[0])
        mamba_leaves = dict(by_component[2])
    except (AttributeError, KeyError, TypeError, ValueError):
        return ()
    if len(by_component) != len(anchors.component_leaves) or set(by_component) != {0, 2}:
        return ()
    if len(nodes) != len(candidate.nodes):
        return ()
    for node in nodes.values():
        if (
            type(node.node_id) is not int
            or node.node_id < 0
            or (
                node.parent_id is not None
                and (type(node.parent_id) is not int or node.parent_id < 0)
            )
            or type(node.creation_time) not in (int, float)
            or node.creation_time < 0
            or (type(node.creation_time) is float and not isfinite(node.creation_time))
            or type(node.full_device_tokens) is not int
            or node.full_device_tokens < 0
            or type(node.full_host_tokens) is not int
            or node.full_host_tokens < 0
            or type(node.mamba_device_present) is not bool
            or type(node.mamba_host_present) is not bool
            or node.pending_write_id is not None
            or node.pending_load_id is not None
        ):
            return ()
    if (
        not full_leaves
        or not mamba_leaves
        or len(full_leaves) != len(by_component[0])
        or len(mamba_leaves) != len(by_component[2])
        or sum(map(len, by_component.values())) > 8
        or any(
            type(leaf) is not int
            or leaf < 0
            or type(created) not in (int, float)
            or created < 0
            or (type(created) is float and not isfinite(created))
            for leaves in (full_leaves, mamba_leaves)
            for leaf, created in leaves.items()
        )
    ):
        return ()
    paths: set[int] = set()
    provenance: dict[int, int] = {}
    depth: dict[int, int] = {}
    root_id = None
    single_leaf = next(iter(full_leaves)) if len(full_leaves) == 1 else None
    ordered_nodes = None
    for leaf, created in sorted(full_leaves.items()):
        if leaf not in nodes or nodes[leaf].creation_time != created:
            return ()
        current = leaf
        chain: list[int] = []
        seen: set[int] = set()
        while current is not None:
            if current not in nodes or current in seen:
                return ()
            seen.add(current)
            chain.append(current)
            current = nodes[current].parent_id
        if root_id is None:
            root_id = chain[-1]
        elif root_id != chain[-1]:
            return ()
        if single_leaf is not None:
            # One validated ancestry already has the required root-first order.
            paths = seen
            ordered_nodes = chain[::-1]
        else:
            for distance, node_id in enumerate(reversed(chain)):
                paths.add(node_id)
                provenance.setdefault(node_id, leaf)
                depth[node_id] = distance
    if (
        # Every path member was resolved in nodes, so cardinality proves coverage.
        len(nodes) != len(paths)
        or any(
            leaf not in paths or nodes[leaf].creation_time != created
            for leaf, created in mamba_leaves.items()
        )
    ):
        return ()
    if ordered_nodes is None:
        ordered_nodes = sorted(paths, key=lambda value: (depth[value], value))
    selected = _checkpoint_paths(
        anchors, nodes, full_leaves, depth, ordered_nodes=ordered_nodes,
    )
    if selected is None:
        return ()
    eligible_paths, checkpoint_nodes = selected
    steps: list[PrefetchLoadStep] = []
    restored_full: set[int] = set()
    for node_id in ordered_nodes:
        if node_id not in eligible_paths:
            continue
        node = nodes[node_id]
        parent = nodes.get(node.parent_id)
        if (
            parent is not None and parent.full_device_tokens <= 0
            and parent.node_id not in restored_full and not (
                parent.parent_id is None
                and parent.full_host_tokens == 0
                and not parent.mamba_host_present
                and not parent.mamba_device_present
            )
        ):
            continue
        if (
            node.full_device_tokens == 0 and node.full_host_tokens > 0
            or node.full_device_tokens > 0
            and node_id in checkpoint_nodes
            and node.mamba_host_present and not node.mamba_device_present
        ):
            source_leaf = single_leaf if single_leaf is not None else provenance[node_id]
            steps.append(PrefetchLoadStep(
                key=anchors.key,
                leaf_node_id=source_leaf,
                leaf_creation_time=full_leaves[source_leaf],
                node_id=node_id,
                creation_time=node.creation_time,
                include_mamba=node_id in checkpoint_nodes,
            ))
            if node.full_device_tokens == 0:
                restored_full.add(node_id)
            if len(steps) == max_steps:
                break
    return tuple(steps)


def revalidate_prefetch_subset(
    opportunity: SessionH2DOpportunity, selected: tuple[PrefetchLoadStep, ...],
) -> SessionH2DOpportunity | None:
    """Retain only selected targets from a fresh ancestor-first capacity plan."""
    candidate = getattr(opportunity, "candidate", None)
    if candidate is None or not selected or not opportunity.steps:
        return None
    current_steps = opportunity.steps
    partial = opportunity.partial_full_step
    if partial is not None:
        if current_steps[-1] != replace(partial, include_mamba=False):
            return None
        current_steps = current_steps[:-1]
    elif opportunity.fits_current_free_lists is not True:
        return None
    if not current_steps:
        return None
    nodes = {node.node_id: node for node in candidate.nodes}
    selected_positions = {step.node_id: position for position, step in enumerate(selected)}
    if len(selected_positions) != len(selected):
        return None
    result = []
    previous_position = -1
    full = mamba = deferred = 0
    for offset, current in enumerate(current_steps):
        position = selected_positions.get(current.node_id)
        if position is None and previous_position == len(selected) - 1:
            # Fresh planning can extend past the original frontier after an
            # earlier selected extent becomes resident. Keep only that frontier.
            for tail in current_steps[offset:]:
                node = nodes.get(tail.node_id)
                if node is None or node.creation_time != tail.creation_time:
                    return None
                deferred += int(
                    tail.include_mamba and node.mamba_host_present
                    and not node.mamba_device_present
                )
            break
        if position is None or position <= previous_position:
            return None
        for skipped in selected[previous_position + 1:position]:
            node = nodes.get(skipped.node_id)
            if (
                node is None or node.creation_time != skipped.creation_time
                or node.full_device_tokens <= 0
                or skipped.include_mamba and not node.mamba_device_present
            ):
                return None
        prior = selected[position]
        if replace(current, include_mamba=prior.include_mamba) != prior:
            return None
        node = nodes.get(current.node_id)
        if node is None or node.creation_time != current.creation_time:
            return None
        include_mamba = prior.include_mamba and current.include_mamba
        missing_full = node.full_host_tokens if node.full_device_tokens == 0 else 0
        missing_mamba = int(node.mamba_host_present and not node.mamba_device_present)
        deferred += int(current.include_mamba and not include_mamba) * missing_mamba
        if missing_full or include_mamba and missing_mamba:
            result.append(
                current if current.include_mamba == include_mamba
                else replace(current, include_mamba=include_mamba)
            )
            full += missing_full
            mamba += int(include_mamba) * missing_mamba
        previous_position = position
    headroom = opportunity.headroom
    if (
        not result or not headroom.observable
        or full > headroom.device_full_free_tokens
        or mamba > headroom.device_mamba_free_slots
    ):
        return None
    return replace(
        opportunity, step=result[0], steps=tuple(result),
        required_full_tokens=full, required_mamba_slots=mamba,
        deferred_mamba_slots=opportunity.deferred_mamba_slots + deferred,
        partial_full_step=None, partial_full_tokens=0,
        fits_current_free_lists=True,
    )


def next_shadow_backup_step(
    candidate: ActionLocalShadowCandidate,
    *,
    reclaim_tail_leaves: frozenset[int] = frozenset(),
) -> ShadowBackupStep | None:
    steps = shadow_backup_steps(
        candidate, max_steps=1, reclaim_tail_leaves=reclaim_tail_leaves,
    )
    return steps[0] if steps else None


def shadow_backup_steps(
    candidate: ActionLocalShadowCandidate, *, max_steps: int = 8,
    reclaim_tail_leaves: frozenset[int] = frozenset(),
) -> tuple[ShadowBackupStep, ...]:
    """Plan one ancestor-first burst of missing FULL extents on one session path.

    Existing Host extents are retained. Mamba snapshots stay with native
    demand write-back. The runtime may additionally prove idle device leaves
    whose FULL tail must be backed before native can reclaim their ancestors.
    """
    if type(max_steps) is not int or not 1 <= max_steps <= 16:
        raise ValueError("shadow burst must contain one to sixteen extents")
    nodes = {node.node_id: node for node in candidate.nodes}
    if len(nodes) != len(candidate.nodes):
        return ()
    leaves = dict(dict(candidate.anchors.component_leaves).get(0, ()))
    if not leaves or not leaves.keys() <= nodes.keys():
        return ()
    if not any(
        node.full_device_tokens > node.full_host_tokens for node in nodes.values()
    ):
        return ()
    paths: set[int] = set()
    provenance: dict[int, int] = {}
    depth: dict[int, int] = {}
    single_leaf_path = None
    ordered_nodes = None
    for leaf in sorted(leaves):
        current = leaf
        chain: list[int] = []
        seen: set[int] = set()
        while current is not None:
            if current not in nodes or current in seen:
                return ()
            seen.add(current)
            chain.append(current)
            current = nodes[current].parent_id
        if len(leaves) == 1 and len(seen) == len(nodes):
            paths = seen
            single_leaf_path = leaf
            ordered_nodes = chain[::-1]
        else:
            for distance, node_id in enumerate(reversed(chain)):
                paths.add(node_id)
                provenance.setdefault(node_id, leaf)
                depth[node_id] = distance
    if ordered_nodes is None:
        ordered_nodes = sorted(paths, key=lambda value: (depth[value], value))
    selected = _checkpoint_paths(
        candidate.anchors, nodes, leaves, depth,
        ordered_nodes=ordered_nodes if len(paths) == len(nodes) else None,
    )
    if selected is None:
        return ()
    eligible_paths, _ = selected
    for leaf in leaves.keys() & reclaim_tail_leaves:
        if nodes[leaf].full_session_leaf_count != 1:
            continue
        current, tail = leaf, set()
        while current not in eligible_paths:
            node = nodes[current]
            if (
                node.parent_id is None or node.full_device_tokens <= 0
                or node.full_session_refs != 1 or node.mamba_session_refs > 1
                or node.full_device_locks or node.full_host_locks
                or node.mamba_device_locks or node.mamba_host_locks
                or node.pending_write_id is not None
                or node.pending_load_id is not None
            ):
                break
            tail.add(current)
            current = node.parent_id
        else:
            eligible_paths.update(tail)
    steps = []
    staged_full = set()
    leaf_id = None
    remaining = sum(
        max(nodes[current].full_device_tokens - nodes[current].full_host_tokens, 0)
        for current in eligible_paths
    )
    # Native queues each predecessor before its child; only this burst may
    # use those unsettled writes as ancestors.
    for node_id in ordered_nodes:
        node = nodes[node_id]
        if node_id not in eligible_paths or (
            leaf_id is not None
            and (single_leaf_path if single_leaf_path is not None else provenance[node_id]) != leaf_id
        ):
            continue
        parent = nodes.get(node.parent_id)
        if (
            node.pending_write_id is not None
            or node.pending_load_id is not None
            or node.full_device_tokens <= 0
            or (
                parent is not None
                and (
                    parent.pending_write_id is not None
                    or parent.pending_load_id is not None
                    or parent.full_device_tokens > parent.full_host_tokens
                    and parent.node_id not in staged_full
                )
            )
        ):
            continue
        if node.full_device_tokens > node.full_host_tokens:
            leaf_id = single_leaf_path if single_leaf_path is not None else provenance[node_id]
            steps.append(ShadowBackupStep(
                key=candidate.anchors.key,
                leaf_node_id=leaf_id,
                leaf_creation_time=leaves[leaf_id],
                node_id=node_id,
                creation_time=node.creation_time,
                include_mamba=False,
                missing_full_prefix_tokens=remaining,
            ))
            staged_full.add(node_id)
            remaining -= node.full_device_tokens - node.full_host_tokens
            if len(steps) == max_steps:
                break
    return tuple(steps)


def _capture_rejected(reason: str, on_failure: Callable[[str], None] | None) -> None:
    if on_failure is not None:
        on_failure(reason)


def capture_action_local_shadow(
    cache: object,
    anchors: ContextSessionAnchors,
    *,
    max_nodes: int | None = None,
    for_prefetch: bool = False,
    include_non_actionable: bool = False,
    on_failure: Callable[[str], None] | None = None,
) -> ActionLocalShadowCandidate | ActionLocalPrefetchCandidate | None:
    """Inspect one context's leaves, optionally reporting its rejection reason."""
    if max_nodes is not None and (type(max_nodes) is not int or max_nodes <= 0):
        raise ValueError("invalid shadow closure bound")
    if type(for_prefetch) is not bool or type(include_non_actionable) is not bool:
        raise ValueError("invalid prefetch capture mode")
    by_component = dict(anchors.component_leaves)
    if (
        len(by_component) != len(anchors.component_leaves)
        or set(by_component) != {0, 2}
        or not by_component[0]
        or not by_component[2]
        or sum(map(len, by_component.values())) > 8
    ):
        return _capture_rejected("invalid_component_leaves", on_failure)
    nodes: dict[int, UnifiedNodeSummary] = {}
    observed_leaves: dict[int, int | float] = {}
    for component_leaves in by_component.values():
        for node_id, created in component_leaves:
            if type(node_id) is not int or node_id < 0:
                return _capture_rejected("invalid_leaf_node_id", on_failure)
            if node_id in observed_leaves:
                if observed_leaves[node_id] != created:
                    return _capture_rejected("inconsistent_leaf_creation_time", on_failure)
                continue
            # A later Mamba anchor may already be on the captured FULL path.
            captured = nodes.get(node_id)
            if captured is not None:
                if captured.creation_time != created:
                    return _capture_rejected("replaced_leaf_anchor", on_failure)
                observed_leaves[node_id] = created
                continue
            observation = observe_unified_node_closure(
                cache, node_id, max_nodes=max_nodes
            )
            if not observation.observable:
                return _capture_rejected(
                    observation.reason or "node_closure_unobservable", on_failure,
                )
            if not observation.nodes:
                return _capture_rejected("empty_node_closure", on_failure)
            if observation.nodes[0].node_id != node_id:
                return _capture_rejected("resolved_leaf_identity_mismatch", on_failure)
            if observation.nodes[0].creation_time != created:
                return _capture_rejected("replaced_leaf_anchor", on_failure)
            observed_leaves[node_id] = created
            for node in observation.nodes:
                if node.node_id in nodes and nodes[node.node_id] != node:
                    return _capture_rejected("inconsistent_shared_ancestry", on_failure)
                nodes[node.node_id] = node
                if max_nodes is not None and len(nodes) > max_nodes:
                    return _capture_rejected("combined_node_closure_exceeds_bound", on_failure)
    missing_full = missing_mamba = 0
    for node in nodes.values():
        if node.parent_id is not None and node.parent_id not in nodes:
            return _capture_rejected("node_parent_outside_closure", on_failure)
        if node.pending_write_id is not None:
            return _capture_rejected("pending_write_transfer", on_failure)
        if node.pending_load_id is not None:
            return _capture_rejected("pending_load_transfer", on_failure)
        if for_prefetch:
            if node.full_device_tokens == 0 and node.full_host_tokens > 0:
                missing_full += node.full_host_tokens
            if (
                node.full_device_tokens > 0
                and node.mamba_host_present and not node.mamba_device_present
            ):
                missing_mamba += 1
        else:
            missing_full += max(node.full_device_tokens - node.full_host_tokens, 0)
            missing_mamba += node.mamba_device_present and not node.mamba_host_present
    if for_prefetch:
        if not missing_full and not missing_mamba and not include_non_actionable:
            return _capture_rejected("no_missing_device_copy", on_failure)
        candidate = ActionLocalPrefetchCandidate(
            anchors=anchors,
            nodes=tuple(sorted(nodes.values(), key=lambda node: node.node_id)),
            missing_full_device_tokens=missing_full,
            missing_mamba_device_nodes=missing_mamba,
        )
        if not include_non_actionable and next_prefetch_gpu_step(candidate) is None:
            return _capture_rejected("no_eligible_prefetch_step", on_failure)
        return candidate
    if not missing_full and not include_non_actionable:
        return _capture_rejected("no_missing_full_host_copy", on_failure)
    return ActionLocalShadowCandidate(
        anchors=anchors,
        nodes=tuple(sorted(nodes.values(), key=lambda node: node.node_id)),
        missing_full_host_tokens=missing_full,
        missing_mamba_host_nodes=missing_mamba,
    )


def _counts(items: tuple[tuple[str, int], ...], *, positive: bool) -> dict[str, int]:
    if type(items) not in (tuple, list):
        raise PhysicalReceiptError("invalid pool entries")
    result: dict[str, int] = {}
    for item in items:
        if type(item) not in (tuple, list) or len(item) != 2:
            raise PhysicalReceiptError("invalid pool entry")
        name, value = item
        if (
            type(name) is not str
            or not name
            or name in result
            or type(value) is not int
            or value < (1 if positive else 0)
        ):
            raise PhysicalReceiptError("invalid or duplicate pool entry")
        result[name] = value
    return result


@dataclass(frozen=True, slots=True)
class PhysicalChildExpectation:
    anchor_node_id: int
    published_node_ids: tuple[int, ...]
    pool_bytes: tuple[tuple[str, int], ...]
    num_bytes: int
    anchor_creation_time: int | float | None = field(default=None, compare=False)
    host_destination_indices: tuple[tuple[str, object], ...] = field(
        default=(), compare=False, repr=False,
    )


def _index_values(indices: object) -> tuple[int, ...]:
    if indices is None:
        return ()
    if type(indices) is int:
        return (indices,)
    if callable(getattr(indices, "tolist", None)):
        indices = indices.tolist()
    if type(indices) is int:
        return (indices,)
    if type(indices) not in (tuple, list) or any(
        type(value) is not int or value < 0 for value in indices
    ):
        raise PhysicalReceiptError("unobservable native host indices")
    return tuple(indices)


def _native_split_publication(
    child: PhysicalChildExpectation, published: tuple[int, ...], cache: object,
) -> bool:
    """Accept only a native split of the original reserved Host destinations."""
    if (
        child.published_node_ids != (child.anchor_node_id,)
        or len(published) < 2
        or published[-1] != child.anchor_node_id
        or len(set(published)) != len(published)
        or child.anchor_creation_time is None
        or not child.host_destination_indices
        or {
            pool for pool, amount in child.pool_bytes if amount
        } - {pool for pool, _ in child.host_destination_indices}
    ):
        return False
    try:
        nodes = [cache.tree_core.node_by_id(node_id) for node_id in published]
        if (
            any(node is None for node in nodes)
            or normalize_native_creation_time(nodes[-1].creation_time)
            != child.anchor_creation_time
            or any(right.parent is not left for left, right in zip(nodes, nodes[1:]))
        ):
            return False
        for pool, reserved in child.host_destination_indices:
            component = 0 if pool == "kv" else 2 if pool == "mamba" else None
            if component is None:
                return False
            actual = tuple(
                index for node in nodes
                for index in _index_values(node.component_data[component].host_value)
            )
            if actual != _index_values(reserved):
                return False
        return True
    except (AttributeError, KeyError, TypeError, ValueError, RuntimeError):
        return False


@dataclass(frozen=True, slots=True)
class PhysicalActionExpectation:
    command_id: str
    action: str
    context_id: str
    context_epoch: int
    children: tuple[PhysicalChildExpectation, ...]
    pool_bytes_per_token: tuple[tuple[str, int], ...]
    session_id: str | None = None
    session_generation: int | None = None


def _prefetch_pool_name(name: object) -> str:
    value = getattr(name, "value", name)
    if type(value) is not str or not value:
        raise PhysicalReceiptError("invalid native prefetch pool name")
    return value


def _prefetch_index_count(host: object, device: object) -> int:
    if host is None or device is None:
        raise PhysicalReceiptError("unresolved native prefetch indices")
    try:
        host_count, device_count = len(host), len(device)
    except (TypeError, ValueError) as exc:
        raise PhysicalReceiptError("invalid native prefetch indices") from exc
    if type(host_count) is not int or host_count != device_count:
        raise PhysicalReceiptError("native prefetch FULL/side pool count mismatch")
    return host_count


def prefetch_expectation_from_native_op(
    command_id: str,
    step: PrefetchLoadStep,
    operation: object,
    controller: object,
) -> PhysicalActionExpectation:
    """Freeze a single-node native H2D transfer before enqueue, not ACK credit."""
    if (
        type(step) is not PrefetchLoadStep
        or type(step.include_mamba) is not bool
        or type(step.key) is not PrefillCandidateKey
        or type(command_id) is not str
        or not command_id
        or getattr(operation, "beliefkv_command_id", None) != command_id
        or getattr(operation, "node_ids", None) != [step.node_id]
        or type(step.leaf_node_id) is not int
        or step.leaf_node_id < 0
        or type(step.node_id) is not int
        or step.node_id < 0
        or type(step.leaf_creation_time) not in (int, float)
        or step.leaf_creation_time < 0
        or (type(step.leaf_creation_time) is float and not isfinite(step.leaf_creation_time))
        or type(step.creation_time) not in (int, float)
        or step.creation_time < 0
        or (type(step.creation_time) is float and not isfinite(step.creation_time))
        or type(step.key.context_id) is not str
        or not step.key.context_id
        or type(step.key.context_epoch) is not int
        or step.key.context_epoch < 0
        or type(step.key.session_id) is not str
        or not step.key.session_id
        or type(step.key.session_generation) is not int
        or step.key.session_generation < 0
    ):
        raise PhysicalReceiptError("native prefetch operation identity mismatch")

    try:
        entry_map = controller.mem_pool_host.entry_map
        if type(entry_map) is not dict:
            raise PhysicalReceiptError("native prefetch host geometry unavailable")
        entries = {}
        for raw_name, entry in entry_map.items():
            name = getattr(raw_name, "value", raw_name)
            if type(name) is not str or not name:
                raise PhysicalReceiptError("invalid native prefetch pool name")
            entries[name] = entry
        if len(entries) != len(entry_map) or "kv" not in entries:
            raise PhysicalReceiptError("native prefetch host geometry changed")
        counts = controller._num_tokens_by_pool(operation)
        num_bytes = controller._transfer_num_bytes(operation)
        full_count = _prefetch_index_count(operation.host_indices, operation.device_indices)
        transfers = operation.pool_transfers
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise PhysicalReceiptError("native prefetch accounting unavailable") from exc
    if type(transfers) is not list and transfers is not None:
        raise PhysicalReceiptError("invalid native prefetch pool transfers")
    if type(counts) is not dict:
        raise PhysicalReceiptError("invalid native prefetch pool counts")

    if not transfers:
        count = counts.get("kv")
        if len(counts) != 1 or type(count) is not int or count != full_count or not full_count:
            raise PhysicalReceiptError("native prefetch token counts mismatch")
        size = getattr(getattr(entries["kv"], "host_pool", None), "size_per_token", None)
        if type(size) is not int or size <= 0:
            raise PhysicalReceiptError("native prefetch pool geometry changed")
        expected_bytes = full_count * size
        pool_bytes = (("kv", expected_bytes),)
        pool_sizes = (("kv", size),)
    else:
        if (
            set(counts) - {"kv", "mamba"} or "kv" not in counts
            or any(type(count) is not int or count < 0 for count in counts.values())
        ):
            raise PhysicalReceiptError("invalid native prefetch pool counts")
        sizes: dict[str, int] = {}

        def size_for(name: str) -> int:
            if name not in sizes:
                size = getattr(
                    getattr(entries.get(name), "host_pool", None),
                    "size_per_token",
                    None,
                )
                if type(size) is not int or size <= 0:
                    raise PhysicalReceiptError("native prefetch pool geometry changed")
                sizes[name] = size
            return sizes[name]

        pool_counts = {"kv": full_count}
        source_indices = {"kv": (operation.host_indices, operation.device_indices)}
        derived: list[tuple[str, str, object]] = []
        for transfer in transfers:
            try:
                name = _prefetch_pool_name(transfer.name)
                source = transfer.indices_from_pool
                host_indices = transfer.host_indices
                device_indices = transfer.device_indices
            except AttributeError as exc:
                raise PhysicalReceiptError("invalid native prefetch pool transfer") from exc
            count = _prefetch_index_count(host_indices, device_indices)
            if source is None:
                if name != "mamba" or name in source_indices:
                    raise PhysicalReceiptError("unsupported native prefetch side pool")
                pool_counts[name] = count
                source_indices[name] = (host_indices, device_indices)
            else:
                if name in ("kv", "mamba") or any(
                    item[0] == name for item in derived
                ):
                    raise PhysicalReceiptError("invalid derived prefetch sidecar")
                derived.append((name, _prefetch_pool_name(source), transfer))
        if counts != pool_counts or not any(pool_counts.values()):
            raise PhysicalReceiptError("native prefetch token counts mismatch")
        if not step.include_mamba and pool_counts.get("mamba", 0):
            raise PhysicalReceiptError("historical Mamba included in FULL-only prefetch")

        pool_bytes = tuple(
            (name, count * size_for(name)) for name, count in sorted(pool_counts.items())
        )
        expected_bytes = sum(amount for _, amount in pool_bytes)
        for name, source, transfer in derived:
            indices = source_indices.get(source)
            if (
                indices is None
                or pool_counts[source] == 0
                or transfer.host_indices is not indices[0]
                or transfer.device_indices is not indices[1]
            ):
                raise PhysicalReceiptError("derived prefetch sidecar indices mismatch")
            expected_bytes += pool_counts[source] * size_for(name)
        pool_sizes = tuple((name, sizes[name]) for name in sorted(pool_counts))
    if type(num_bytes) is not int or num_bytes <= 0 or num_bytes != expected_bytes:
        raise PhysicalReceiptError("native prefetch total bytes mismatch")
    # Field order follows the fixed receipt schema.
    return PhysicalActionExpectation(
        command_id, "PREFETCH_GPU", step.key.context_id, step.key.context_epoch,
        (PhysicalChildExpectation(
            step.node_id, (step.node_id,), pool_bytes, num_bytes,
        ),),
        pool_sizes, step.key.session_id, step.key.session_generation,
    )


def shadow_expectation_from_native_op(
    command_id: str,
    step: ShadowBackupStep,
    operation: object,
    controller: object,
) -> PhysicalActionExpectation:
    """Freeze exact native D2H accounting before the operation is enqueued."""
    if (
        type(step) is not ShadowBackupStep
        or type(step.include_mamba) is not bool
        or type(command_id) is not str
        or not command_id
        or getattr(operation, "beliefkv_command_id", None) != command_id
        or getattr(operation, "node_ids", None) != [step.node_id]
        or type(step.node_id) is not int
        or step.node_id < 0
        or step.key.session_id is None
        or step.key.session_generation is None
    ):
        raise PhysicalReceiptError("native shadow operation identity mismatch")
    try:
        group = controller.mem_pool_host
        entries = {
            getattr(name, "value", name): entry
            for name, entry in group.entry_map.items()
        }
        counts = controller._num_tokens_by_pool(operation)
        num_bytes = controller._transfer_num_bytes(operation)
        full_count = len(operation.device_indices)
        host_count = len(operation.host_indices)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise PhysicalReceiptError("native shadow accounting unavailable") from exc
    if (
        type(counts) is not dict
        or not counts
        or type(full_count) is not int
        or full_count != host_count
        or counts.get("kv") != full_count
        or type(num_bytes) is not int
        or num_bytes <= 0
    ):
        raise PhysicalReceiptError("invalid native shadow pool counts")
    if len(counts) == 1 and type(pool := next(iter(counts))) is str and pool == "kv":
        count = counts["kv"]
        if type(count) is not int or count < 0:
            raise PhysicalReceiptError("invalid native shadow pool counts")
        entry = entries.get("kv")
        size = getattr(getattr(entry, "host_pool", None), "size_per_token", None)
        if type(size) is not int or size <= 0:
            raise PhysicalReceiptError("native shadow pool geometry changed")
        expected_bytes = count * size
        sizes = (("kv", size),)
        pool_bytes = (("kv", expected_bytes),)
    else:
        if set(counts) - {"kv", "mamba"} or any(
            type(count) is not int or count < 0 for count in counts.values()
        ):
            raise PhysicalReceiptError("invalid native shadow pool counts")
        if not step.include_mamba and counts.get("mamba", 0):
            raise PhysicalReceiptError("historical Mamba included in FULL-only backup")
        sizes = []
        pool_bytes = []
        expected_bytes = 0
        for pool, count in sorted(counts.items()):
            entry = entries.get(pool)
            size = getattr(getattr(entry, "host_pool", None), "size_per_token", None)
            if type(size) is not int or size <= 0:
                raise PhysicalReceiptError("native shadow pool geometry changed")
            amount = count * size
            sizes.append((pool, size))
            pool_bytes.append((pool, amount))
            expected_bytes += amount
        sizes = tuple(sizes)
        pool_bytes = tuple(pool_bytes)
    if expected_bytes > num_bytes:
        raise PhysicalReceiptError("native shadow bytes smaller than pool bytes")
    return PhysicalActionExpectation(
        command_id, "PREPARE_HOST", step.key.context_id, step.key.context_epoch,
        (PhysicalChildExpectation(
            step.node_id, (step.node_id,), pool_bytes, num_bytes,
            normalize_native_creation_time(step.creation_time),
            (
                ("kv", operation.host_indices),
                *(
                    ("mamba", transfer.host_indices)
                    for transfer in getattr(operation, "pool_transfers", ()) or ()
                    if getattr(transfer.name, "value", transfer.name) == "mamba"
                    and getattr(transfer, "indices_from_pool", None) is None
                ),
            ),
        ),),
        sizes, step.key.session_id, step.key.session_generation,
    )


@dataclass(frozen=True)
class PhysicalActionCompleted:
    command_id: str
    action: str
    context_id: str
    context_epoch: int
    node_ids: tuple[int, ...]
    pool_bytes: tuple[tuple[str, int], ...]
    num_bytes: int
    source: str | None = None


class PhysicalTransactionLedger:
    """Bounded per-command reconciliation of synchronized native child commits.

    The native emitter only attaches child_commits after the complete merged ACK
    has synchronized, the tree has finished publishing/loading, and child pool
    counts and total bytes have reconciled with that ACK. Missing child credit
    cannot be reconstructed from merged pool totals. Callers must allocate
    globally unique command IDs: the duplicate-replay history is bounded.
    """

    def __init__(
        self,
        *,
        max_pending: int = 64,
        max_children: int = 32,
        max_nodes: int = 256,
        max_age_s: float = 30.0,
        history_size: int = 128,
    ) -> None:
        if (
            type(max_pending) is not int
            or max_pending < 1
            or type(max_children) is not int
            or max_children < 1
            or type(max_nodes) is not int
            or max_nodes < 1
            or type(history_size) is not int
            or history_size < 1
            or type(max_age_s) not in (int, float)
            or not 0 < max_age_s < float("inf")
        ):
            raise ValueError("invalid ledger bounds")
        self.max_pending = max_pending
        self.max_children = max_children
        self.max_nodes = max_nodes
        self.max_age_s = max_age_s
        self._pending: dict[str, tuple[PhysicalActionExpectation, float, set[int]]] = {}
        self._pending_action_counts: dict[str, int] = {}
        self._pending_action_bytes: dict[str, int] = {}
        self._pending_h2d_contexts: dict[tuple[str, str, int], int] = {}
        self._pending_nodes: set[int] = set()
        self._context_invalidated_commands: set[str] = set()
        self._history: deque[str] = deque()
        self._seen: set[str] = set()
        self._published_by_command: dict[str, dict[int, tuple[int, ...]]] = {}
        self._history_size = history_size

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def pending_action_count(self, action: str) -> int:
        """Count a direction without serializing unrelated native transfers."""
        return self._pending_action_counts.get(action, 0)

    def pending_action_bytes(self, action: str) -> int:
        return self._pending_action_bytes.get(action, 0)

    @property
    def pending_h2d_contexts(self) -> KeysView[tuple[str, str, int]]:
        """Physical restore owners, including commands without runtime tickets."""
        return self._pending_h2d_contexts.keys()

    def is_pending(self, command_id: str) -> bool:
        return command_id in self._pending

    def pending_expectation(self, command_id: str) -> PhysicalActionExpectation | None:
        pending = self._pending.get(command_id)
        return pending[0] if pending is not None else None

    @property
    def pending_context_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(expected.context_id for expected, _, _ in self._pending.values())
        )

    @property
    def pending_h2d_sessions(self) -> tuple[tuple[str, int, str, int], ...]:
        return tuple(
            (expected.context_id, expected.context_epoch,
             expected.session_id, expected.session_generation)
            for expected, _, _ in self._pending.values()
            if expected.action == "PREFETCH_GPU"
            and expected.session_id is not None
            and expected.session_generation is not None
        )

    @property
    def pending_transfer_sessions(self) -> tuple[tuple[str, int, str, int], ...]:
        return tuple(
            (expected.context_id, expected.context_epoch,
             expected.session_id, expected.session_generation)
            for expected, _, _ in self._pending.values()
            if expected.session_id is not None and expected.session_generation is not None
        )

    def _remember(self, command_id: str) -> None:
        self._published_by_command.pop(command_id, None)
        if len(self._history) == self._history_size:
            self._seen.remove(self._history.popleft())
        self._history.append(command_id)
        self._seen.add(command_id)

    def _retire(self, command_id: str) -> None:
        expected, _, _ = self._pending.pop(command_id)
        for child in expected.children:
            self._pending_nodes.difference_update(child.published_node_ids)
        self._context_invalidated_commands.discard(command_id)
        size = self._pending_action_bytes[expected.action] - sum(
            child.num_bytes for child in expected.children
        )
        remaining = self._pending_action_counts[expected.action] - 1
        if remaining:
            self._pending_action_counts[expected.action] = remaining
            self._pending_action_bytes[expected.action] = size
        else:
            del self._pending_action_counts[expected.action]
            del self._pending_action_bytes[expected.action]
        if (
            expected.action == "PREFETCH_GPU" and expected.session_id is not None
            and expected.session_generation is not None
        ):
            owner = (expected.context_id, expected.session_id, expected.session_generation)
            refs = self._pending_h2d_contexts[owner] - 1
            if refs:
                self._pending_h2d_contexts[owner] = refs
            else:
                del self._pending_h2d_contexts[owner]
        self._remember(command_id)

    def expire(self) -> tuple[str, ...]:
        """Drop timed-out commands without issuing any completion credit."""
        if not self._pending:
            return ()
        now = monotonic()
        # One fixed age and monotonic insertion order put the earliest expiry
        # first, including after an out-of-order ACK removes an interior entry.
        expired = []
        for command_id, (_, started, _) in self._pending.items():
            if now - started < self.max_age_s:
                break
            expired.append(command_id)
        for command_id in expired:
            self._retire(command_id)
        return tuple(expired)

    def register(self, expected: PhysicalActionExpectation) -> None:
        self.expire()
        if (
            type(expected.command_id) is not str
            or not expected.command_id
            or expected.command_id in self._pending
            or expected.command_id in self._seen
            or expected.action not in ("PREPARE_HOST", "PREFETCH_GPU")
            or type(expected.context_id) is not str
            or not expected.context_id
            or type(expected.context_epoch) is not int
            or expected.context_epoch < 0
            or (
                expected.session_id is not None
                and (
                    type(expected.session_id) is not str
                    or not expected.session_id
                    or type(expected.session_generation) is not int
                    or expected.session_generation < 0
                )
            )
            or (expected.session_id is None and expected.session_generation is not None)
            or type(expected.children) is not tuple
            or not expected.children
            or len(expected.children) > self.max_children
            or type(expected.pool_bytes_per_token) is not tuple
            or len(self._pending) >= self.max_pending
        ):
            raise PhysicalReceiptError("invalid, reused or over-bound command")
        single_pool = single_unit = None
        if len(expected.pool_bytes_per_token) == 1:
            item = expected.pool_bytes_per_token[0]
            if type(item) not in (tuple, list) or len(item) != 2:
                raise PhysicalReceiptError("invalid pool entry")
            single_pool, single_unit = item
            if (
                type(single_pool) is not str or not single_pool
                or type(single_unit) is not int or single_unit <= 0
            ):
                raise PhysicalReceiptError("invalid or duplicate pool entry")
            if single_pool not in ("kv", "mamba"):
                raise PhysicalReceiptError("unsupported native pool")
            sizes = None
        else:
            sizes = _counts(expected.pool_bytes_per_token, positive=True)
            if not sizes or set(sizes) - {"kv", "mamba"}:
                raise PhysicalReceiptError("unsupported native pool")
        multiple_children = len(expected.children) > 1
        anchors: set[int] | tuple[int, ...] = set() if multiple_children else ()
        published: set[int] | tuple[int, ...] = set() if multiple_children else ()
        total_bytes = 0
        for child in expected.children:
            if (
                type(child.anchor_node_id) is not int
                or child.anchor_node_id < 0
                or child.anchor_node_id in anchors
                or type(child.published_node_ids) is not tuple
                or not child.published_node_ids
                or (
                    (
                        type(child.published_node_ids[0]) is not int
                        or child.published_node_ids[0] != child.anchor_node_id
                        or child.published_node_ids[0] in published
                    ) if len(child.published_node_ids) == 1 else (
                        child.anchor_node_id not in child.published_node_ids
                        or any(type(node) is not int or node < 0 for node in child.published_node_ids)
                        or len(set(child.published_node_ids)) != len(child.published_node_ids)
                        or multiple_children and not published.isdisjoint(child.published_node_ids)
                    )
                )
                or type(child.pool_bytes) is not tuple
                or type(child.num_bytes) is not int
                or child.num_bytes <= 0
            ):
                raise PhysicalReceiptError("invalid or overlapping child nodes")
            if not child.pool_bytes:
                raise PhysicalReceiptError("invalid child pools")
            seen_pools = 0
            pool_total = 0
            for item in child.pool_bytes:
                if type(item) not in (tuple, list) or len(item) != 2:
                    raise PhysicalReceiptError("invalid pool entry")
                pool, amount = item
                if type(pool) is not str or type(amount) is not int or amount < 0:
                    raise PhysicalReceiptError("invalid pool entry")
                unit = (
                    single_unit if pool == single_pool else None
                ) if sizes is None else sizes.get(pool)
                if unit is None:
                    raise PhysicalReceiptError("invalid child pools")
                pool_bit = 1 if pool == "kv" else 2
                if seen_pools & pool_bit:
                    raise PhysicalReceiptError("invalid or duplicate pool entry")
                seen_pools |= pool_bit
                if amount % unit:
                    raise PhysicalReceiptError("pool bytes not aligned to token size")
                pool_total += amount
            if pool_total > child.num_bytes:
                raise PhysicalReceiptError("child bytes smaller than pool bytes")
            if multiple_children:
                anchors.add(child.anchor_node_id)
                published.update(child.published_node_ids)
            else:
                published = child.published_node_ids
            total_bytes += child.num_bytes
            if len(published) > self.max_nodes:
                raise PhysicalReceiptError("command exceeds node bound")
        if not self._pending_nodes.isdisjoint(published):
            raise PhysicalReceiptError("node already owned by a pending command")
        self._pending[expected.command_id] = (expected, monotonic(), set())
        self._pending_nodes.update(published)
        self._pending_action_counts[expected.action] = (
            self._pending_action_counts.get(expected.action, 0) + 1
        )
        self._pending_action_bytes[expected.action] = (
            self._pending_action_bytes.get(expected.action, 0) + total_bytes
        )
        if (
            expected.action == "PREFETCH_GPU" and expected.session_id is not None
            and expected.session_generation is not None
        ):
            owner = (expected.context_id, expected.session_id, expected.session_generation)
            self._pending_h2d_contexts[owner] = self._pending_h2d_contexts.get(owner, 0) + 1

    def cancel_unsubmitted(self, command_id: str) -> None:
        """Release a reservation only when native confirms nothing was queued.

        A native command already submitted must stay pending until ACK or
        expiry; cancelling it here would turn its eventual receipt into an
        unknown command and poison all physical credit.
        """
        if type(command_id) is not str or command_id not in self._pending:
            raise PhysicalReceiptError("cannot cancel unknown physical command")
        expected, _, received = self._pending[command_id]
        if received:
            raise PhysicalReceiptError(
                f"cannot cancel partially acknowledged {expected.action}"
            )
        self._retire(command_id)

    def invalidate_context(self, context_id: str) -> None:
        """Keep submitted DMA pending, but forbid credit after prefix replacement."""
        self._context_invalidated_commands.update(
            command_id for command_id, (expected, _, _) in self._pending.items()
            if expected.context_id == context_id
        )

    def _reject(self, message: str) -> None:
        # An invalid merged event cannot safely be attributed to any subset.
        for command_id in tuple(self._pending):
            self._retire(command_id)
        raise PhysicalReceiptError(message)

    def observe(
        self,
        commit: object,
        *,
        live_context_epochs: Mapping[str, int],
        live_context_sessions: Mapping[str, tuple[str, int]] | None = None,
        native_cache: object | None = None,
    ) -> tuple[PhysicalActionCompleted, ...]:
        """Credit only entire commands after native completion and live revalidation.

        Untagged native ACKs are ignored unless they overlap an expected node.
        A missing or malformed tagged receipt invalidates all pending credit.
        """
        self.expire()
        try:
            return self._observe(
                commit,
                live_context_epochs=live_context_epochs,
                live_context_sessions=live_context_sessions or {},
                native_cache=native_cache,
            )
        except PhysicalReceiptError:
            for command_id in tuple(self._pending):
                self._retire(command_id)
            raise
        except (TypeError, ValueError, AttributeError, KeyError):
            self._reject("invalid or inconsistent native child commit")

    def _observe(
        self,
        commit: object,
        *,
        live_context_epochs: Mapping[str, int],
        live_context_sessions: Mapping[str, tuple[str, int]],
        native_cache: object | None,
    ) -> tuple[PhysicalActionCompleted, ...]:
        receipts = getattr(commit, "child_commits", ())
        nodes = getattr(commit, "node_ids", ())
        if type(nodes) not in (tuple, list) or any(type(n) is not int for n in nodes):
            self._reject("invalid merged ACK nodes")
        merged_nodes = set(nodes)
        if len(merged_nodes) != len(nodes):
            self._reject("duplicate merged ACK node")
        if not receipts:
            if self._pending_nodes and not self._pending_nodes.isdisjoint(nodes):
                self._reject("missing child credit for expected node")
            return ()
        if type(receipts) not in (tuple, list) or getattr(commit, "status", None) != "completed":
            self._reject("ACK is not a completed native commit")
        direction = getattr(commit, "direction", None)
        if direction not in ("d2h", "h2d"):
            self._reject("invalid transfer direction")
        merged_counts = _counts(
            getattr(commit, "num_tokens_by_pool", ()), positive=False
        )
        if set(merged_counts) - {"kv", "mamba", "swa", "c128"}:
            self._reject("unexpected merged pool")
        by_command: dict[str, set[int]] = {}
        publications: dict[str, dict[int, tuple[int, ...]]] = {}
        credited_counts: dict[str, int] = {}
        credited_nodes: set[int] = set()
        for receipt in receipts:
            command_id = getattr(receipt, "command_id", None)
            if type(command_id) is not str or command_id not in self._pending:
                self._reject("unknown or repeated command receipt")
            expected, _, already = self._pending[command_id]
            live_epoch = live_context_epochs.get(expected.context_id)
            same_session = (
                expected.session_id is not None
                and live_context_sessions.get(expected.context_id)
                == (expected.session_id, expected.session_generation)
            )
            epoch_matches = (
                live_epoch == expected.context_epoch
                or expected.action in ("PREFETCH_GPU", "PREPARE_HOST")
                and same_session and live_epoch == expected.context_epoch + 1
            )
            if (
                command_id in self._context_invalidated_commands
                or direction != ("d2h" if expected.action == "PREPARE_HOST" else "h2d")
                or type(live_epoch) is not int
                or not epoch_matches
                or (
                    expected.session_id is not None
                    and live_context_sessions.get(expected.context_id)
                    != (expected.session_id, expected.session_generation)
                )
            ):
                self._reject("direction or live context epoch/session changed")
            anchor = getattr(receipt, "anchor_node_id", None)
            child = next(
                (part for part in expected.children if part.anchor_node_id == anchor),
                None,
            )
            if child is None or anchor in already or anchor in by_command.get(command_id, set()):
                self._reject("unknown or duplicate child receipt")
            published = getattr(receipt, "published_node_ids", None)
            if (
                type(published) is not tuple
                or len(published) > self.max_nodes
            ):
                self._reject("child publication does not match native ACK")
            split_publication = published != child.published_node_ids
            if (
                split_publication and not (
                    expected.action == "PREPARE_HOST"
                    and native_cache is not None
                    and _native_split_publication(child, published, native_cache)
                )
                or not merged_nodes.issuperset(published)
                or not credited_nodes.isdisjoint(published)
            ):
                self._reject("child publication does not match native ACK")
            # register() keeps original publications disjoint across commands.
            # Only a native split introduces nodes outside that reservation.
            if split_publication:
                own_nodes = {
                    node for part in expected.children for node in part.published_node_ids
                }
                for node in published:
                    if node in self._pending_nodes and node not in own_nodes:
                        self._reject("child publication does not match native ACK")
            actual_counts = _counts(
                getattr(receipt, "num_tokens_by_pool", ()), positive=False
            )
            sizes = dict(expected.pool_bytes_per_token)
            if (
                actual_counts
                != {pool: amount // sizes[pool] for pool, amount in child.pool_bytes}
                or type(getattr(receipt, "num_bytes", None)) is not int
                or receipt.num_bytes != child.num_bytes
            ):
                self._reject("child pool or byte accounting mismatch")
            for pool, count in actual_counts.items():
                credited_counts[pool] = credited_counts.get(pool, 0) + count
            credited_nodes.update(published)
            by_command.setdefault(command_id, set()).add(anchor)
            publications.setdefault(command_id, {})[anchor] = published
        if not self._pending_nodes.isdisjoint(merged_nodes - credited_nodes):
            self._reject("merged ACK omitted expected child credit")
        if any(credited_counts.get(pool, 0) > merged_counts.get(pool, 0) for pool in credited_counts):
            self._reject("children exceed merged ACK pool counts")
        if credited_nodes == merged_nodes and credited_counts != merged_counts:
            self._reject("fully credited ACK has unmatched pool counts")
        completed: list[PhysicalActionCompleted] = []
        for command_id, anchors in by_command.items():
            expected, _, already = self._pending[command_id]
            observed = self._published_by_command.setdefault(command_id, {})
            observed.update(publications[command_id])
            already.update(anchors)
            if len(already) != len(expected.children):
                continue
            pool_bytes: dict[str, int] = {}
            for child in expected.children:
                for pool, amount in child.pool_bytes:
                    pool_bytes[pool] = pool_bytes.get(pool, 0) + amount
            completed.append(
                PhysicalActionCompleted(
                    command_id=command_id,
                    action=expected.action,
                    context_id=expected.context_id,
                    context_epoch=expected.context_epoch,
                    node_ids=tuple(
                        node for child in expected.children
                        for node in observed[child.anchor_node_id]
                    ),
                    pool_bytes=tuple(sorted(pool_bytes.items())),
                    num_bytes=sum(child.num_bytes for child in expected.children),
                )
            )
        for action in completed:
            self._retire(action.command_id)
        return tuple(completed)
