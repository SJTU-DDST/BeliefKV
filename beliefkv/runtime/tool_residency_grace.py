"""One bounded native KV pin across an observed model-to-tool return gap."""

from collections import Counter
from dataclasses import dataclass
import math

from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey


@dataclass
class _Pin:
    cache: object
    key: PrefillCandidateKey
    node: object
    node_id: int
    created: int | float
    lock_params: object
    expires_at: float


class ToolResidencyGrace:
    def __init__(self, hold_ms: float):
        if (
            type(hold_ms) not in (int, float) or not math.isfinite(hold_ms)
            or not 0 < hold_ms <= 1000.
        ):
            raise ValueError("tool residency grace must be in (0, 1000] ms")
        self.hold_ms = hold_ms
        self.pin: _Pin | None = None
        self.counts: Counter[str] = Counter()

    @property
    def key(self) -> PrefillCandidateKey | None:
        return self.pin.key if self.pin is not None else None

    def retain(self, cache: object, key: PrefillCandidateKey, *, now: float) -> bool:
        if self.pin is not None or key.session_id is None or key.session_generation is None:
            return False
        try:
            leaves = cache.session_refs.snapshot_latest_session_leaf_anchors(
                key.session_id, key.session_generation, max_leaves=1,
            )
            full_leaves = tuple(dict(leaves).get(0, ())) if leaves is not None else ()
            if len(full_leaves) != 1:
                return False
            node_id, created = full_leaves[0]
            node = cache.tree_core.node_by_id(node_id)
            full = node.component_data[0]
            if (
                node.creation_time != created or full.value is None
                or full.lock_ref or not len(full.value)
                or key.session_id not in (full.session_ids or ())
                or node.write_through_pending_id is not None
                or node.load_back_pending_id is not None
            ):
                return False
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            return False
        receipt = cache.tree_core.inc_lock_ref(node_id)
        self.pin = _Pin(
            cache, key, node, node_id, created, receipt.to_dec_params(),
            now + self.hold_ms / 1000.,
        )
        self.counts["acquired"] += 1
        self.counts["resident_tokens_at_acquire"] += len(full.value)
        return True

    def release(self, reason: str) -> PrefillCandidateKey | None:
        pin = self.pin
        if pin is None:
            return None
        current = pin.cache.tree_core.node_by_id(pin.node_id)
        if current is not pin.node or current.creation_time != pin.created:
            raise RuntimeError("tool residency grace lost its native node identity")
        pin.cache.tree_core.dec_lock_ref(pin.node_id, pin.lock_params)
        self.pin = None
        self.counts["released:" + reason] += 1
        return pin.key

