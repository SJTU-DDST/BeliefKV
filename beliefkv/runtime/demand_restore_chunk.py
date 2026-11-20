"""Stage part of an admitted native demand restore under Host pressure."""

from dataclasses import dataclass

from beliefkv.runtime.demand_restore_capacity import ensure_device_capacity_preserving_live_kv


DEFER_WRITE_ACK = "write_ack_retry_next_pass"
FULL_RESTORE_FUNDED = "full_restore_funded"


@dataclass
class _Pending:
    request: object
    key: object
    tokens: int
    remaining_tokens: int
    prefix_node: int
    prefix_receipt: object
    suffix_node: int
    suffix_receipt: object
    credit_waiter: tuple[object, tuple] | None = None


class DemandRestoreChunk:
    """Retain one partial prefix until native has committed its H2D ACK."""

    def __init__(self, cache, *, max_source_nodes):
        self.cache = cache
        self.max_source_nodes = max_source_nodes
        self.pending = None
        self.parked = None

    def continues(self, params, key):
        req = params.req
        committed = getattr(req, "_beliefkv_d3_committed_restore", None)
        if committed is None:
            return False
        prior_key, prefix, suffix, remaining = committed
        hit = params.host_hit_length
        if key != prior_key or params.best_match_node != suffix or not 0 < hit <= remaining:
            return False
        if req.last_node == prefix:
            return hit == remaining

        # A shared restore may have made additional prefix tokens resident.
        # Accept that progress only along the original path and for the exact delta.
        tree = self.cache.tree_core
        try:
            node = tree.node_by_id(req.last_node)
        except KeyError:
            return False
        advanced = remaining - hit
        for _ in range(self.max_source_nodes):
            if node.id == prefix:
                return advanced == 0
            if node is tree.root_node or len(node.key) > advanced:
                return False
            advanced -= len(node.key)
            node = node.parent
        return node.id == prefix and advanced == 0

    def start(
        self, params, key, tokens, *, device_reserve, protected_nodes,
        minimum_tokens=1, replace_pending=None,
    ):
        """Stage a slice, fund the final native load, or return a decline reason."""
        cache, req = self.cache, params.req
        tree, controller = cache.tree_core, cache.cache_controller
        continuation = self.continues(params, key)
        hit = params.host_hit_length
        if (
            self.pending is not replace_pending
            or (replace_pending is not None and (
                self.parked is not None
                or replace_pending.request is req
                or replace_pending.prefix_node in cache.ongoing_load_back
            ))
            or (getattr(req, "_beliefkv_d3_staged_restore_key", None) == key and not continuation)
            or controller.load_queue or controller.ack_load_queue
            or not 0 < tokens <= hit
            or (tokens == hit and not continuation)
        ):
            return "restore_not_available"

        suffix_id = params.best_match_node
        node = tree.node_by_id(suffix_id)
        prefix = None
        remaining = params.host_hit_length
        depth = 0
        while node is not None and node.id != req.last_node:
            if depth >= self.max_source_nodes:
                return "source_path_bound"
            if node is tree.root_node:
                return "source_prefix_mismatch"
            full = node.component_data[0]
            if full.value is not None or full.host_value is None:
                return "source_prefix_mismatch"
            if (
                not full.session_ref or full.host_lock_ref or full.lock_ref
                or node.load_back_pending_id is not None
                or node.write_through_pending_id is not None
            ):
                return "source_extent_unowned_or_busy"
            extent = len(node.key)
            if not 0 < extent <= remaining:
                return "source_prefix_mismatch"
            remaining -= extent
            # Find the cutoff from the suffix without storing the whole path.
            if prefix is None and remaining < tokens:
                prefix, prefix_tokens = node, tokens - remaining
            depth += 1
            node = node.parent
        if node is None or remaining or prefix is None:
            return "source_prefix_mismatch"

        # Attempt the native allocation once. A declined staging attempt may
        # move settled state, but may not discard any referenced checkpoint.
        allocator = cache.token_to_kv_pool_allocator
        free_before = allocator.available_size()
        funded = ensure_device_capacity_preserving_live_kv(
            cache, tokens + device_reserve, protected_nodes=protected_nodes,
            minimum_tokens=minimum_tokens + device_reserve,
            retry_pending_write=getattr(req, "_beliefkv_d3_write_ack_retry_key", None) != key,
        )
        if funded is None:
            req._beliefkv_d3_write_ack_retry_key = key
            return DEFER_WRITE_ACK
        req._beliefkv_d3_staged_restore_key = key
        req._beliefkv_d3_committed_restore = None
        if funded and tokens == hit:
            return FULL_RESTORE_FUNDED
        if not funded:
            free_after = allocator.available_size()
            page = cache.page_size
            smaller = (free_after - device_reserve) // page * page
            if free_after <= free_before or not minimum_tokens <= smaller < tokens:
                return "device_capacity_not_funded"
            # The original source walk validated every node in the shorter slice.
            trim = tokens - smaller
            while trim >= prefix_tokens:
                trim -= prefix_tokens
                prefix = prefix.parent
                prefix_tokens = len(prefix.key)
            prefix_tokens -= trim
            tokens = smaller
        prefix_id = prefix.id
        if prefix_tokens < len(prefix.key):
            split = tree.split_host_only_full_prefix(prefix.id, prefix.creation_time, prefix_tokens)
            if split is None:
                return "native_split_declined"
            prefix_id, actions = split
            cache._apply_cache_actions(actions)

        suffix_receipt = cache.inc_host_lock_ref(suffix_id).to_dec_params()
        try:
            if not cache.load_back(prefix_id, req=req):
                return "native_load_declined"
            prefix_receipt = cache.inc_lock_ref(prefix_id).to_dec_params()
            self.pending = _Pending(
                req, key, tokens, hit - tokens, prefix_id, prefix_receipt, suffix_id, suffix_receipt,
            )
            if replace_pending is not None:
                # Keep one settled producer pinned while the borrower stages its suffix.
                self.parked = req, replace_pending
        finally:
            if self.pending is replace_pending:
                cache.dec_host_lock_ref(suffix_id, suffix_receipt)

        producer = cache.ready_to_load_host_cache()
        if producer < 0 or len(controller.ack_load_queue) != 1:
            raise RuntimeError("staged demand restore did not publish its native ACK")
        cache._beliefkv_prefetch_consumer_index = producer
        ack = controller.ack_load_queue[-1]
        if tuple(ack.node_ids) != (prefix_id,) or dict(ack.num_tokens_by_pool).get("kv") != tokens:
            raise RuntimeError("staged demand restore differs from its native allocation")
        return None

    def defer_until_ack(self, req):
        pending = self.pending
        if self.parked is not None and self.parked[1].request is req:
            borrower, producer = self.parked
            if pending is not None and pending.request is borrower:
                return True
            self.parked = None
            self._commit(producer)
        if pending is None or pending.request is not req:
            return False
        if pending.prefix_node in self.cache.ongoing_load_back:
            return True
        self.pending = None
        self._commit(pending)
        return False

    def _commit(self, pending):
        pending.request._beliefkv_d3_committed_restore = (
            pending.key, pending.prefix_node, pending.suffix_node, pending.remaining_tokens,
        )
        self._release_pins(pending)

    def _release_pins(self, pending):
        self.cache.dec_lock_ref(pending.prefix_node, pending.prefix_receipt)
        self.cache.dec_host_lock_ref(pending.suffix_node, pending.suffix_receipt)

    def release_if(self, predicate):
        if self.pending is not None and predicate(self.pending):
            pending, self.pending = self.pending, None
            self._release_pins(pending)
        if self.parked is not None and predicate(self.parked[1]):
            _, producer = self.parked
            self.parked = None
            self._release_pins(producer)

    def release(self):
        self.release_if(lambda _pending: True)
