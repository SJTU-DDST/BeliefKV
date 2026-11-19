"""Stage part of an admitted native demand restore under Host pressure."""

from dataclasses import dataclass

from beliefkv.runtime.demand_restore_capacity import ensure_device_capacity_preserving_live_kv


@dataclass
class _Pending:
    request: object
    key: object
    prefix_node: int
    prefix_receipt: object
    suffix_node: int
    suffix_receipt: object


class DemandRestoreChunk:
    """Retain one partial prefix until native has committed its H2D ACK."""

    def __init__(self, cache):
        self.cache = cache
        self.pending = None

    def start(self, params, key, tokens, *, device_reserve):
        cache, req = self.cache, params.req
        tree, controller = cache.tree_core, cache.cache_controller
        if (
            self.pending is not None
            or getattr(req, "_beliefkv_d3_staged_restore_key", None) == key
            or controller.load_queue or controller.ack_load_queue
            or not 0 < tokens < params.host_hit_length
        ):
            return False

        suffix_id = params.best_match_node
        suffix = tree.node_by_id(suffix_id)
        node = suffix
        missing = []
        while node.id != req.last_node:
            if len(missing) == 256 or node is tree.root_node:
                return False
            full = node.component_data[0]
            if (
                full.value is not None or full.host_value is None
                or not full.session_ref or full.host_lock_ref or full.lock_ref
                or node.load_back_pending_id is not None
                or node.write_through_pending_id is not None
            ):
                return False
            missing.append(node)
            node = node.parent
        if sum(len(node.key) for node in missing) != params.host_hit_length:
            return False

        remaining = tokens
        for node in reversed(missing):
            extent = len(node.key)
            if remaining <= extent:
                break
            remaining -= extent

        # Attempt the native allocation once. A declined staging attempt may
        # move settled state, but may not discard any referenced checkpoint.
        req._beliefkv_d3_staged_restore_key = key
        if not ensure_device_capacity_preserving_live_kv(cache, tokens + device_reserve):
            return False
        prefix_id = node.id
        if remaining < extent:
            split = tree.split_host_only_full_prefix(node.id, node.creation_time, remaining)
            if split is None:
                return False
            prefix_id, actions = split
            cache._apply_cache_actions(actions)

        suffix_receipt = cache.inc_host_lock_ref(suffix_id).to_dec_params()
        try:
            if not cache.load_back(prefix_id, req=req):
                return False
            prefix_receipt = cache.inc_lock_ref(prefix_id).to_dec_params()
            self.pending = _Pending(req, key, prefix_id, prefix_receipt, suffix_id, suffix_receipt)
        finally:
            if self.pending is None:
                cache.dec_host_lock_ref(suffix_id, suffix_receipt)

        producer = cache.ready_to_load_host_cache()
        if producer < 0 or len(controller.ack_load_queue) != 1:
            raise RuntimeError("staged demand restore did not publish its native ACK")
        cache._beliefkv_prefetch_consumer_index = producer
        ack = controller.ack_load_queue[-1]
        if tuple(ack.node_ids) != (prefix_id,) or dict(ack.num_tokens_by_pool).get("kv") != tokens:
            raise RuntimeError("staged demand restore differs from its native allocation")
        return True

    def defer_until_ack(self, req):
        pending = self.pending
        if pending is None or pending.request is not req:
            return False
        if pending.prefix_node in self.cache.ongoing_load_back:
            return True
        self.release()
        return False

    def release(self):
        pending = self.pending
        if pending is None:
            return
        self.pending = None
        self.cache.dec_lock_ref(pending.prefix_node, pending.prefix_receipt)
        self.cache.dec_host_lock_ref(pending.suffix_node, pending.suffix_receipt)
