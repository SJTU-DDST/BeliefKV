"""Capacity checks for a synchronous FULL demand restore."""

from contextlib import contextmanager


_MAX_MIRROR_PREFLIGHT = 64


class _BackupUnavailable(Exception):
    """Stop native eviction before it can drop the only copy."""


def _stop_eviction(*_args, **_kwargs):
    raise _BackupUnavailable


def _keep_host_leaves(*_args, **_kwargs):
    # Native tree eviction has already reclaimed any eligible mirrors.
    pass


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


def ensure_device_capacity_preserving_live_kv(cache, tokens: int) -> bool:
    """Try to make ``tokens`` absolute FULL slots free on the scheduler thread.

    This supports single-rank native write-back ownership. A failed attempt may
    leave completed backups, demotions, reclaimed mirrors, and radix splits in
    place. The caller allocates the available device slots.
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
    if (
        not tree.is_write_back or full.is_evict_device_ongoing
        or controller.write_queue or controller.ack_write_queue
        or cache.ongoing_write_through
        or shortfall > cache.full_evictable_size()
    ):
        return False
    host_free = controller.mem_pool_host.available_size()
    mirrors = tree.full_host_duplicates
    if host_free < shortfall and len(mirrors) <= _MAX_MIRROR_PREFLIGHT:
        # Each dual slot can either fund a device demotion or be reused for
        # a fresh backup. Count both pinned and usable mirrors as an upper bound.
        for node in mirrors.values():
            data = node.component_data[0]
            if data.value is not None and data.host_value is not None:
                host_free += len(data.host_value)
                if host_free >= shortfall:
                    break
        if host_free < shortfall:
            return False

    from sglang.srt.mem_cache.base_prefix_cache import EvictParams

    # D3 can discard a cold device leaf directly through the tree. Protect that
    # release too, including when an installed callback captured the tree method.
    with (
        _override_hook(full, "drive_host_eviction", _keep_host_leaves),
        _override_hook(cache, "_drop_subtree_no_host", _stop_eviction),
        _override_hook(tree, "_release_all_component_layers", _stop_eviction),
    ):
        try:
            cache.evict_for_alloc(EvictParams(num_tokens=shortfall))
        except _BackupUnavailable:
            return False
    return allocator.available_size() >= tokens
