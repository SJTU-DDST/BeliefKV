"""Verify a real native Host allocation on one NUMA node without using CUDA."""

import argparse
from datetime import datetime, timezone
import gc
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.host_numa import inspect_mapping
from beliefkv.experiments.native_engine_variant import FREE_EXTENT_TREE, engine_patch
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts.preflight_qwen35_host_pool import check_host_pool
from scripts.train_workflow_work import write


def run(engine, capacity_source, output, *, node=1):
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or output.exists():
        raise ValueError("hide CUDA and preserve previous physical allocation proofs")
    if os.environ.get("SGLANG_HUGEPAGE_SIZE") not in (None, ""):
        raise ValueError("the component requires the declared base-page Host allocator")
    source = git_source_state(ROOT)
    if source["dirty"]:
        raise ValueError("use a clean committed allocation qualifier")
    native = git_source_state(engine, canonical_patch=engine_patch(ROOT, FREE_EXTENT_TREE))
    if native["canonical_patch"]["actual_tree"] != FREE_EXTENT_TREE:
        raise ValueError("use the existing qualified native allocator")
    capacity = json.loads(capacity_source.read_bytes())["capacity"]
    if (
        capacity.get("host_full_bytes") != 200000077824
        or capacity.get("host_total_bytes") != capacity["host_full_bytes"]
        or capacity.get("host_mamba_bytes") != 0
    ):
        raise ValueError("require the same complete 200 GB FULL-only Host geometry")
    sys.path.insert(0, str(engine / "python"))
    import torch
    from sglang.srt.mem_cache.storage.mmap import alloc_mmap
    allocator = engine / "python/sglang/srt/mem_cache/storage/mmap/mmap_allocator.py"
    paths = (
        Path(__file__), capacity_source, allocator,
        ROOT / "beliefkv/experiments/host_numa.py",
        ROOT / "scripts/preflight_qwen35_host_pool.py",
    )
    pins = {str(path.resolve()): sha256_file(path) for path in paths}
    if torch.cuda.is_initialized():
        raise ValueError("the CPU allocator qualifier must not initialize CUDA")
    budget, available = check_host_pool(
        node, 200, memory_policy="bind", reclaim_policy="clean_file",
    )
    begin = time.monotonic()
    pool = alloc_mmap((capacity["host_full_bytes"],), torch.uint8)
    allocated = time.monotonic()
    proc = Path("/proc") / str(os.getpid())
    pages = os.sysconf("SC_PAGE_SIZE")
    placement = inspect_mapping(
        (proc / "maps").read_text(), (proc / "numa_maps").read_text(),
        expected_bytes=capacity["host_full_bytes"], node=node, page_bytes=pages,
    )
    if int(placement["mapping"]["start_address"], 16) != pool.data_ptr():
        raise ValueError("the observed mapping is not the actual native tensor")
    sentinels = (0, pages, capacity["host_full_bytes"] // 2, capacity["host_full_bytes"] - 1)
    for index, offset in enumerate(sentinels):
        pool[offset] = index + 11
    if [int(pool[offset]) for offset in sentinels] != list(range(11, 15)):
        raise ValueError("native Host tensor canary bytes differ")
    address = placement["mapping"]["start_address"]
    del pool
    gc.collect()
    remaining = [
        line for line in (proc / "maps").read_text().splitlines()
        if line.split()[0].split("-")[0] == address
    ]
    if remaining or torch.cuda.is_initialized():
        raise ValueError("allocation was not released or CUDA was unexpectedly initialized")
    if any(sha256_file(Path(name)) != digest for name, digest in pins.items()):
        raise ValueError("a bound allocation input changed")
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "operator": source, "native": native, "placement": placement,
        "native_tensor_address_matches_mapping": True,
        "declared_decimal_pool_budget_bytes": budget,
        "actual_native_allocation_bytes": capacity["host_full_bytes"],
        "native_allocator_tensor_dtype": "uint8 byte buffer with unchanged total pool bytes",
        "estimated_available_before_allocation_bytes": available,
        "allocation_seconds": allocated - begin,
        "allocation_and_verification_seconds": time.monotonic() - begin,
        "sparse_canary_bytes_verified": True, "allocation_mapping_released": True,
        "inputs_sha256": pins,
        "GPU_action": False, "CUDA_initialized": False,
        "CUDA_Host_registration_or_DMA_qualified": False,
        "model_or_workflow_performance_qualified": False,
        "scope": [
            "Real complete native mmap allocation, prefaulted by the unchanged native allocator.",
            "Actual strictly local physical pages and the exact tensor address are verified.",
            "No drop_caches, page migration, GPU allocation, Host registration or transfer is issued.",
            "The 200 GB capacity is unchanged; GPU registration and model startup still require qualification.",
        ],
    }
    write(output, result)
    print(json.dumps({
        "actual_bytes": result["actual_native_allocation_bytes"],
        "allocation_seconds": result["allocation_seconds"],
        "local_node": node, "remote_bytes": placement["remote_pool_bytes"],
        "released": True, "GPU_action": False,
    }), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--capacity-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--node", type=int, default=1)
    args = parser.parse_args()
    run(args.engine, args.capacity_source, args.output, node=args.node)


if __name__ == "__main__":
    main()
