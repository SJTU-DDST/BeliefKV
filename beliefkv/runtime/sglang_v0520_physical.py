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


class PhysicalReceiptError(ValueError):
    """A command cannot be credited from the observed native ACK."""


@dataclass(frozen=True)
class ContextSessionAnchors:
    """Session leaf provenance only; shared ownership is not established."""

    key: PrefillCandidateKey
    component_leaves: tuple[tuple[int, tuple[tuple[int, int | float], ...]], ...]
    captured_monotonic_s: float


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
