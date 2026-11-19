"""Capacity checks for a synchronous FULL demand restore."""

from contextlib import contextmanager
from itertools import islice


_MAX_PREFLIGHT_LEAVES = 64


class _BackupUnavailable(Exception):
    """Stop native eviction before it can drop the only copy."""


def _stop_eviction(*_args, **_kwargs):
    raise _BackupUnavailable


@contextmanager
def _override_hook(owner, name, hook):
    missing = object()
    previous = vars(owner).get(name, missing)
    setattr(owner, name, hook)
    try:
        yield
    finally:
        if previous is missing:
            delattr(owner, name)
        else:
            setattr(owner, name, previous)


def _is_unreferenced_leaf(tree, node):
    data = node.component_data[0]
    if (
        node is tree.root_node or node.children
        or data.session_ref or data.lock_ref or data.host_lock_ref
        or node.write_through_pending_id is not None or node.load_back_pending_id is not None
    ):
        return False
    try:
        return tree.node_by_id(node.id) is node
    except KeyError:
        return False


def _cold_device_leaf_slots(tree, node):
    data = node.component_data[0]
    if (
        node in tree.evictable_device_leaves and data.value is not None
        and data.host_value is None and _is_unreferenced_leaf(tree, node)
    ):
        return len(data.value)
    return 0


def _cold_host_leaf_slots(tree, node):
    data = node.component_data[0]
    if (
        node in tree.evictable_host_leaves and data.value is None
        and data.host_value is not None and _is_unreferenced_leaf(tree, node)
    ):
        return len(data.host_value)
    return 0


def ensure_device_capacity_preserving_live_kv(cache, tokens: int, *, protected_nodes=()) -> bool:
    """Try to make ``tokens`` absolute FULL slots free on the scheduler thread.

    This supports single-rank native write-back ownership. A failed attempt may
    leave completed backups, demotions, reclaimed mirrors, radix splits, and
    discarded unreferenced leaves in place. The caller allocates the available slots.
    """
    controller = getattr(cache, "cache_controller", None)
    if (
        type(tokens) is not int or tokens < 0
        or getattr(cache, "tree_components", None) != (0,)
        or getattr(cache, "tp_world_size", None) != 1
        or getattr(cache, "pp_size", None) != 1
        or cache.disable or not cache.is_write_back
        or cache.host_memory_mode != "cache" or cache.enable_storage
        or cache.buffer_pipeline is not None or cache.linker is not None
        or cache.sidecar_pool_specs
        or getattr(cache, "_beliefkv_layer_handoff", None) is not None
        or getattr(controller, "write_policy", None) != "write_back"
    ):
        return False

    allocator = cache.token_to_kv_pool_allocator
    shortfall = tokens - allocator.available_size()
    if shortfall <= 0:
        return True
    tree = cache.tree_core
    full = cache.components[0]
    if not tree.is_write_back or full.is_evict_device_ongoing or controller.write_queue:
        return False
    acks = controller.ack_write_queue
    if acks:
        if len(acks) != 1 or len(cache.ongoing_write_through) != 1:
            return False
        ack = acks[0]
        if (
            len(ack.node_ids) != 1 or ack.node_ids[0] not in cache.ongoing_write_through
            or not ack.finish_event.query()
        ):
            return False
        # Publish the completed backup before testing which device pages can move.
        cache.writing_check(finish_count=1)
        shortfall = tokens - allocator.available_size()
        if shortfall <= 0:
            return True
    if (
        controller.write_queue or controller.ack_write_queue
        or cache.ongoing_write_through
        or shortfall > cache.full_evictable_size()
    ):
        return False
    # D3 installs its device-leaf callback on the cache instance.
    can_discard_cold = (
        callable(vars(cache).get("_evict_device_leaf"))
        and getattr(cache, "enable_session_radix_cache", False) is True
        and callable(getattr(tree, "discard_terminated_leaf", None))
    )
    protected = frozenset(protected_nodes)
    possible_slots = controller.mem_pool_host.available_size()
    mirrors = tree.full_host_duplicates
    cold_host_leaves = []
    if can_discard_cold and possible_slots < shortfall:
        cold_host_leaves = [
            (node, slots)
            for node in islice(tree.evictable_host_leaves, _MAX_PREFLIGHT_LEAVES)
            if node.id not in protected and (slots := _cold_host_leaf_slots(tree, node))
        ]
        possible_slots += sum(slots for _, slots in cold_host_leaves)
    if possible_slots < shortfall and len(mirrors) <= _MAX_PREFLIGHT_LEAVES:
        # Each dual slot can either fund a device demotion or be reused for
        # a fresh backup. Count both pinned and usable mirrors as an upper bound.
        for node in mirrors.values():
            data = node.component_data[0]
            if data.value is not None and data.host_value is not None:
                possible_slots += len(data.host_value)
                if possible_slots >= shortfall:
                    break
        if can_discard_cold and possible_slots < shortfall:
            for node in islice(tree.evictable_device_leaves, _MAX_PREFLIGHT_LEAVES):
                if node.id in protected:
                    continue
                possible_slots += _cold_device_leaf_slots(tree, node)
                if possible_slots >= shortfall:
                    break
        if possible_slots < shortfall:
            return False

    from sglang.srt.disaggregation.kv_events import StorageMedium
    from sglang.srt.mem_cache.base_prefix_cache import EvictParams

    native_evict_leaf = cache._evict_device_leaf
    native_release = tree._release_all_component_layers
    selected_leaf = None
    host_leaves_ordered = False

    def reclaim_cold_host(num_tokens, tracker, device_frees, host_frees):
        nonlocal host_leaves_ordered
        if not cold_host_leaves or tracker[0] >= num_tokens:
            return
        if not host_leaves_ordered:
            cold_host_leaves.sort(
                key=lambda entry: full.session_ref_eviction_strategy(entry[0]),
                reverse=True,
            )
            host_leaves_ordered = True
        # Native has already tried its dual-resident copies for this backup.
        while cold_host_leaves and tracker[0] < num_tokens:
            node, _ = cold_host_leaves.pop()
            if _cold_host_leaf_slots(tree, node):
                tree._evict_host_leaf(node, tracker, device_frees, host_frees)

    def evict_leaf(node_id, tracker):
        nonlocal selected_leaf
        if can_discard_cold and full.is_evict_device_ongoing:
            selected_leaf = tree.node_by_id(node_id)
        try:
            return native_evict_leaf(node_id, tracker)
        finally:
            selected_leaf = None

    def release_layers(node, medium, *args):
        # The attached callback may have captured discard_terminated_leaf.
        # Its native leaf validation runs before this release.
        if (
            node is not selected_leaf or medium is not StorageMedium.GPU
            or node.id in protected
            or not _cold_device_leaf_slots(tree, node)
        ):
            raise _BackupUnavailable
        return native_release(node, medium, *args)

    with (
        _override_hook(full, "drive_host_eviction", reclaim_cold_host),
        _override_hook(cache, "_evict_device_leaf", evict_leaf),
        _override_hook(cache, "_drop_subtree_no_host", _stop_eviction),
        _override_hook(tree, "drop_subtree_no_host", _stop_eviction),
        _override_hook(tree, "_release_all_component_layers", release_layers),
    ):
        try:
            cache.evict_for_alloc(EvictParams(num_tokens=shortfall))
        except _BackupUnavailable:
            return False
    return allocator.available_size() >= tokens
