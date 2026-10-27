"""Scope CPU-issued native direct copies to FULL-only idle PREPARE."""

from contextlib import contextmanager


@contextmanager
def idle_prepare_backend(cache, mode, counts):
    if mode == "native":
        yield False
        return
    if mode != "direct":
        raise ValueError("idle prepare backend must be native or direct")
    controller = getattr(cache, "cache_controller", None)
    engine = getattr(controller, "l2_transfer_engine", None)
    group = getattr(controller, "mem_pool_host", None)
    entries = getattr(group, "entry_map", {})
    supported = (
        getattr(cache, "tree_components", ()) == (0,)
        and controller is not None and engine is not None
        and controller.io_backend == engine.io_backend == "kernel"
        and getattr(group, "layout", None) == "layer_first"
        and len(entries) == 1
        and all(getattr(entry.host_pool, "layout", None) == "layer_first"
                for entry in entries.values())
        and not getattr(controller, "enable_storage", False)
    )
    if not supported:
        counts["idle_prepare_direct_unsupported"] += 1
        yield False
        return
    if any(getattr(controller, name) for name in (
        "write_queue", "load_queue", "ack_write_queue", "ack_load_queue",
    )):
        counts["idle_prepare_direct_busy"] += 1
        yield False
        return
    # PREPARE synchronously enqueues one bounded batch on the scheduler thread.
    # Keep native stream ordering, ACKs, Host allocation and all cache locks.
    controller.io_backend = engine.io_backend = "direct"
    counts["idle_prepare_direct_scopes"] += 1
    try:
        yield True
    finally:
        controller.io_backend = engine.io_backend = "kernel"
