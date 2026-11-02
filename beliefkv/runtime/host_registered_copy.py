"""Keep staged native D2H submissions inside actual Host registration ranges."""

import logging
import os

import numpy as np


ENV = "BELIEFKV_HOST_REGISTERED_COPY_SPLIT"
TARGET = "sglang.srt.mem_cache.pool_host.mha.MHATokenToKVPoolHost.backup_from_device_all_layer"
RANGES = "_sglang_cuda_host_registered_ranges"
_CUTS = "_beliefkv_registered_copy_token_cuts"
STATS = "_beliefkv_registered_copy_stats"
logger = logging.getLogger(__name__)


def token_cuts(pool):
    buffer = pool.kv_buffer
    ranges = getattr(buffer, RANGES, ())
    if len(ranges) < 2:
        return None
    cursor = buffer.data_ptr()
    for pointer, size in ranges:
        if pointer != cursor or size <= 0:
            raise ValueError("Host registrations must cover the actual buffer without gaps")
        cursor += size
    if cursor != buffer.data_ptr() + buffer.numel() * buffer.element_size():
        raise ValueError("Host registrations do not cover the complete buffer")
    cuts = set()
    for view in (pool.k_buffer, pool.v_buffer):
        stride = view.stride(0) * view.element_size()
        page_bytes = stride * pool.page_size
        begin, end = view.data_ptr(), view.data_ptr() + view.shape[0] * stride
        for pointer, size in ranges:
            boundary = pointer + size
            if begin < boundary < end:
                if (boundary - begin) % page_bytes:
                    raise ValueError("a Host registration splits a native KV page")
                cuts.add((boundary - begin) // stride)
    return np.asarray(sorted(cuts), dtype=np.int64)


def backup(original, pool, device_pool, host_indices, device_indices, io_backend):
    if (
        io_backend != "kernel" or pool.layout != "page_first"
        or not pool.can_use_write_back_jit
    ):
        return original(pool, device_pool, host_indices, device_indices, io_backend)
    if not hasattr(pool, _CUTS):
        setattr(pool, _CUTS, token_cuts(pool))
    cuts = getattr(pool, _CUTS)
    if cuts is None or not len(cuts) or not host_indices.numel():
        return original(pool, device_pool, host_indices, device_indices, io_backend)
    if host_indices.device.type != "cpu":
        # Preserve the native staged kernel's CPU-index validation.
        return original(pool, device_pool, host_indices, device_indices, io_backend)
    page = pool.page_size
    if host_indices.ndim != 1 or device_indices.ndim != 1 or (
        host_indices.numel() != device_indices.numel() or host_indices.numel() % page
    ):
        raise ValueError("registered D2H splitting requires complete paired native pages")
    pages = host_indices.numpy().reshape(-1, page)
    if page > 1 and (
        np.any(pages[:, 0] % page) or np.any(pages != pages[:, :1] + np.arange(page))
    ):
        raise ValueError("registered D2H splitting requires native contiguous page indices")
    regions = np.searchsorted(cuts, pages[:, 0], side="right")
    splits = np.flatnonzero(regions[1:] != regions[:-1]) + 1
    if not len(splits):
        return original(pool, device_pool, host_indices, device_indices, io_backend)
    stats = getattr(pool, STATS, None)
    if stats is None:
        stats = {"split_backups": 0, "native_submissions": 0, "tokens": 0}
        setattr(pool, STATS, stats)
    stats["split_backups"] += 1
    stats["native_submissions"] += len(splits) + 1
    stats["tokens"] += host_indices.numel()
    if stats["split_backups"] == 1:
        logger.info("Splitting staged D2H at actual CUDA Host registration boundaries")
    begin, result = 0, None
    # All pieces stay on the caller's stream; its original ACK follows the last.
    for end in (*splits * page, host_indices.numel()):
        result = original(
            pool, device_pool, host_indices[begin:end], device_indices[begin:end], io_backend,
        )
        begin = end
    return result


def register():
    selected = os.environ.get(ENV, "0")
    if selected not in ("0", "1"):
        raise ValueError(f"{ENV} must be 0 or 1")
    if selected == "0":
        return
    from sglang.srt.plugins.hook_registry import HookRegistry, HookType

    HookRegistry.register(TARGET, backup, HookType.AROUND)
