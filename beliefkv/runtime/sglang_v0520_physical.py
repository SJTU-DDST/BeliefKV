"""Fail-closed accounting for v0.5.20 native FULL/MAMBA transfer commits.

This module observes completed native ACKs; it does not initiate transfers or
authorize eviction. The caller must supply the live context epochs at each ACK.
Native receipts contain pool token counts and total bytes, not per-pool bytes.
Pool byte accounting therefore uses the caller's frozen per-token pool sizes.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from time import monotonic
from typing import Mapping

from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from beliefkv.runtime.sglang_v0520_observer import (
    UnifiedNodeSummary,
    observe_unified_node_closure,
)


class PhysicalReceiptError(ValueError):
    """A command cannot be credited from the observed native ACK."""


@dataclass(frozen=True)
class ContextSessionAnchors:
    """Session leaf provenance only; shared ownership is not established."""

    key: PrefillCandidateKey
    component_leaves: tuple[tuple[int, tuple[tuple[int, int | float], ...]], ...]
    captured_monotonic_s: float


@dataclass(frozen=True)
class ActionLocalShadowCandidate:
    """Bounded, read-only FULL/MAMBA closure; not an action certificate."""

    anchors: ContextSessionAnchors
    nodes: tuple[UnifiedNodeSummary, ...]
    missing_full_host_tokens: int
    missing_mamba_host_nodes: int


@dataclass(frozen=True)
class ShadowBackupStep:
    """One ancestor-first candidate; native must recheck before D2H."""

    key: PrefillCandidateKey
    leaf_node_id: int
    leaf_creation_time: int | float
    node_id: int
    creation_time: int | float


def next_shadow_backup_step(
    candidate: ActionLocalShadowCandidate,
) -> ShadowBackupStep | None:
    """Prefer the first unbacked FULL node on the session closure path.

    A single step permits partial agent-KV shadowing. Native must revalidate
    the session, ancestry, settled Host parent and capacity at the safe point.
    """
    nodes = {node.node_id: node for node in candidate.nodes}
    if len(nodes) != len(candidate.nodes):
        return None
    leaves = dict(dict(candidate.anchors.component_leaves).get(0, ()))
    if not leaves or not leaves.keys() <= nodes.keys():
        return None
    paths: set[int] = set()
    provenance: dict[int, int] = {}
    for leaf in sorted(leaves):
        current = leaf
        seen: set[int] = set()
        while current is not None:
            if current not in nodes or current in seen:
                return None
            seen.add(current)
            parent_id = nodes[current].parent_id
            if parent_id is not None and parent_id not in nodes:
                return None
            paths.add(current)
            provenance.setdefault(current, leaf)
            current = parent_id
    depth: dict[int, int] = {}
    for node_id in paths:
        current = node_id
        length = 0
        while nodes[current].parent_id is not None:
            length += 1
            current = nodes[current].parent_id
        depth[node_id] = length
    # Depth from root, so a host copy of a parent settles before its child.
    for node_id in sorted(paths, key=lambda value: (depth[value], value)):
        node = nodes[node_id]
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
                    or parent.mamba_device_present and not parent.mamba_host_present
                )
            )
        ):
            continue
        if (
            node.full_device_tokens > node.full_host_tokens
            or node.mamba_device_present and not node.mamba_host_present
        ):
            return ShadowBackupStep(
                key=candidate.anchors.key,
                leaf_node_id=provenance[node_id],
                leaf_creation_time=leaves[provenance[node_id]],
                node_id=node_id,
                creation_time=node.creation_time,
            )
    return None


def capture_action_local_shadow(
    cache: object,
    anchors: ContextSessionAnchors,
    *,
    max_nodes: int = 64,
) -> ActionLocalShadowCandidate | None:
    """Inspect only one context's session leaves, without issuing native work."""
    if type(max_nodes) is not int or not 0 < max_nodes <= 256:
        raise ValueError("invalid shadow closure bound")
    by_component = dict(anchors.component_leaves)
    if (
        len(by_component) != len(anchors.component_leaves)
        or set(by_component) != {0, 2}
        or not by_component[0]
        or not by_component[2]
        or sum(map(len, by_component.values())) > 8
    ):
        return None
    nodes: dict[int, UnifiedNodeSummary] = {}
    for component_leaves in by_component.values():
        for node_id, created in component_leaves:
            if type(node_id) is not int or node_id < 0:
                return None
            observation = observe_unified_node_closure(
                cache, node_id, max_nodes=max_nodes
            )
            if (
                not observation.observable
                or not observation.nodes
                or observation.nodes[0].node_id != node_id
                or observation.nodes[0].creation_time != created
            ):
                return None
            for node in observation.nodes:
                if node.node_id in nodes and nodes[node.node_id] != node:
                    return None
                nodes[node.node_id] = node
                if len(nodes) > max_nodes:
                    return None
    if any(
        node.parent_id is not None and node.parent_id not in nodes
        or node.pending_write_id is not None
        or node.pending_load_id is not None
        for node in nodes.values()
    ):
        return None
    missing_full = sum(
        max(node.full_device_tokens - node.full_host_tokens, 0)
        for node in nodes.values()
    )
    missing_mamba = sum(
        node.mamba_device_present and not node.mamba_host_present
        for node in nodes.values()
    )
    if not missing_full and not missing_mamba:
        return None
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


@dataclass(frozen=True)
class PhysicalChildExpectation:
    anchor_node_id: int
    published_node_ids: tuple[int, ...]
    pool_bytes: tuple[tuple[str, int], ...]
    num_bytes: int


@dataclass(frozen=True)
class PhysicalActionExpectation:
    command_id: str
    action: str
    context_id: str
    context_epoch: int
    children: tuple[PhysicalChildExpectation, ...]
    pool_bytes_per_token: tuple[tuple[str, int], ...]
    session_id: str | None = None
    session_generation: int | None = None


def shadow_expectation_from_native_op(
    command_id: str,
    step: ShadowBackupStep,
    operation: object,
    controller: object,
) -> PhysicalActionExpectation:
    """Freeze exact native D2H accounting before the operation is enqueued."""
    if (
        type(command_id) is not str
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
        or set(counts) - {"kv", "mamba"}
        or type(full_count) is not int
        or full_count != host_count
        or counts.get("kv") != full_count
        or any(type(count) is not int or count < 0 for count in counts.values())
        or type(num_bytes) is not int
        or num_bytes <= 0
    ):
        raise PhysicalReceiptError("invalid native shadow pool counts")
    sizes = []
    pool_bytes = []
    for pool, count in sorted(counts.items()):
        entry = entries.get(pool)
        size = getattr(getattr(entry, "host_pool", None), "size_per_token", None)
        if type(size) is not int or size <= 0:
            raise PhysicalReceiptError("native shadow pool geometry changed")
        sizes.append((pool, size))
        pool_bytes.append((pool, count * size))
    if sum(amount for _, amount in pool_bytes) > num_bytes:
        raise PhysicalReceiptError("native shadow bytes smaller than pool bytes")
    return PhysicalActionExpectation(
        command_id=command_id,
        action="PREPARE_HOST",
        context_id=step.key.context_id,
        context_epoch=step.key.context_epoch,
        children=(PhysicalChildExpectation(
            anchor_node_id=step.node_id,
            published_node_ids=(step.node_id,),
            pool_bytes=tuple(pool_bytes),
            num_bytes=num_bytes,
        ),),
        pool_bytes_per_token=tuple(sizes),
        session_id=step.key.session_id,
        session_generation=step.key.session_generation,
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
        self._history: deque[str] = deque()
        self._seen: set[str] = set()
        self._history_size = history_size

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def pending_context_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(expected.context_id for expected, _, _ in self._pending.values())
        )

    def _remember(self, command_id: str) -> None:
        if len(self._history) == self._history_size:
            self._seen.remove(self._history.popleft())
        self._history.append(command_id)
        self._seen.add(command_id)

    def expire(self) -> tuple[str, ...]:
        """Drop timed-out commands without issuing any completion credit."""
        now = monotonic()
        expired = tuple(
            command_id
            for command_id, (_, started, _) in self._pending.items()
            if now - started >= self.max_age_s
        )
        for command_id in expired:
            del self._pending[command_id]
            self._remember(command_id)
        return expired

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
        sizes = _counts(expected.pool_bytes_per_token, positive=True)
        if not sizes or set(sizes) - {"kv", "mamba"}:
            raise PhysicalReceiptError("unsupported native pool")
        anchors: set[int] = set()
        published: set[int] = set()
        for child in expected.children:
            if (
                type(child.anchor_node_id) is not int
                or child.anchor_node_id < 0
                or child.anchor_node_id in anchors
                or type(child.published_node_ids) is not tuple
                or not child.published_node_ids
                or child.anchor_node_id not in child.published_node_ids
                or any(type(node) is not int or node < 0 for node in child.published_node_ids)
                or len(set(child.published_node_ids)) != len(child.published_node_ids)
                or published.intersection(child.published_node_ids)
                or type(child.pool_bytes) is not tuple
                or type(child.num_bytes) is not int
                or child.num_bytes <= 0
            ):
                raise PhysicalReceiptError("invalid or overlapping child nodes")
            amounts = _counts(child.pool_bytes, positive=False)
            if not amounts or set(amounts) - set(sizes):
                raise PhysicalReceiptError("invalid child pools")
            if any(value % sizes[pool] for pool, value in amounts.items()):
                raise PhysicalReceiptError("pool bytes not aligned to token size")
            if sum(amounts.values()) > child.num_bytes:
                raise PhysicalReceiptError("child bytes smaller than pool bytes")
            anchors.add(child.anchor_node_id)
            published.update(child.published_node_ids)
            if len(published) > self.max_nodes:
                raise PhysicalReceiptError("command exceeds node bound")
        if any(
            published.intersection(part.published_node_ids)
            for other, _, _ in self._pending.values()
            for part in other.children
        ):
            raise PhysicalReceiptError("node already owned by a pending command")
        self._pending[expected.command_id] = (expected, monotonic(), set())

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
        del self._pending[command_id]
        self._remember(command_id)

    def _reject(self, message: str) -> None:
        # An invalid merged event cannot safely be attributed to any subset.
        for command_id in tuple(self._pending):
            del self._pending[command_id]
            self._remember(command_id)
        raise PhysicalReceiptError(message)

    def observe(
        self,
        commit: object,
        *,
        live_context_epochs: Mapping[str, int],
        live_context_sessions: Mapping[str, tuple[str, int]] | None = None,
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
            )
        except PhysicalReceiptError:
            for command_id in tuple(self._pending):
                del self._pending[command_id]
                self._remember(command_id)
            raise
        except (TypeError, ValueError, AttributeError, KeyError):
            self._reject("invalid or inconsistent native child commit")

    def _observe(
        self,
        commit: object,
        *,
        live_context_epochs: Mapping[str, int],
        live_context_sessions: Mapping[str, tuple[str, int]],
    ) -> tuple[PhysicalActionCompleted, ...]:
        receipts = getattr(commit, "child_commits", ())
        nodes = getattr(commit, "node_ids", ())
        if type(nodes) not in (tuple, list) or any(type(n) is not int for n in nodes):
            self._reject("invalid merged ACK nodes")
        if len(set(nodes)) != len(nodes):
            self._reject("duplicate merged ACK node")
        if not receipts:
            if any(
                set(nodes).intersection(child.published_node_ids)
                for expected, _, _ in self._pending.values()
                for child in expected.children
            ):
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
        credited_counts: dict[str, int] = {}
        credited_nodes: set[int] = set()
        for receipt in receipts:
            command_id = getattr(receipt, "command_id", None)
            if type(command_id) is not str or command_id not in self._pending:
                self._reject("unknown or repeated command receipt")
            expected, _, already = self._pending[command_id]
            live_epoch = live_context_epochs.get(expected.context_id)
            if (
                direction != ("d2h" if expected.action == "PREPARE_HOST" else "h2d")
                or type(live_epoch) is not int
                or live_epoch != expected.context_epoch
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
                or published != child.published_node_ids
                or not set(published).issubset(nodes)
                or credited_nodes.intersection(published)
            ):
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
        if any(
            set(nodes).difference(credited_nodes).intersection(child.published_node_ids)
            for expected, _, _ in self._pending.values()
            for child in expected.children
        ):
            self._reject("merged ACK omitted expected child credit")
        if any(credited_counts.get(pool, 0) > merged_counts.get(pool, 0) for pool in credited_counts):
            self._reject("children exceed merged ACK pool counts")
        if credited_nodes == set(nodes) and credited_counts != merged_counts:
            self._reject("fully credited ACK has unmatched pool counts")
        completed: list[PhysicalActionCompleted] = []
        for command_id, anchors in by_command.items():
            expected, _, already = self._pending[command_id]
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
                        node for child in expected.children for node in child.published_node_ids
                    ),
                    pool_bytes=tuple(sorted(pool_bytes.items())),
                    num_bytes=sum(child.num_bytes for child in expected.children),
                )
            )
        for action in completed:
            del self._pending[action.command_id]
            self._remember(action.command_id)
        return tuple(completed)
