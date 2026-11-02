"""Check the complete native interleaved Host allocation and optional CUDA registration."""

import argparse
from datetime import datetime, timezone
import gc
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments import host_numa
from beliefkv.experiments.native_engine_variant import FREE_EXTENT_TREE, engine_patch
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from beliefkv.runtime.host_registration_numa import LinuxMemoryPolicy
from scripts.preflight_qwen35_host_pool import check_host_pool
from scripts.qualify_free_page_h2d_fence import check_reservation
from scripts.run_causal_comparison import qualified, reservation
from scripts.train_workflow_work import write


def configuration(size_gb, node, memory_nodes):
    common = {
        "HOST_NUMA_NODE": str(node), "HOST_NUMA_MEMORY_POLICY": "interleave",
        "HOST_NUMA_MEMORY_NODES": memory_nodes, "HOST_NUMA_RECLAIM_POLICY": "clean_file",
        "HICACHE_SIZE_GB": str(size_gb), "MODEL_CACHE_LAYOUT": "full_only",
    }
    host_numa.configuration(common)
    return common


def copy_offsets(pool_bytes, address, ranges):
    offsets = {0, pool_bytes - 128}
    if not ranges or ranges[0][0] != address:
        raise ValueError("native registration does not start at the actual Host buffer")
    cursor = address
    for pointer, length in ranges:
        if pointer != cursor or length <= 0:
            raise ValueError("native registration has a hole, overlap or empty range")
        offsets.add(max(0, pointer - address - 64))
        cursor += length
    if cursor != address + pool_bytes:
        raise ValueError("native registration does not cover the complete Host buffer")
    return sorted(offset for offset in offsets if offset + 128 <= pool_bytes)


def run(args):
    common = configuration(args.size_gb, args.node, args.memory_nodes)
    selected = host_numa.configuration(common)
    if (
        args.output_dir.exists() or git_source_state(ROOT)["dirty"]
        or os.getpriority(os.PRIO_PROCESS, 0) != 0
        or os.environ.get("CUDA_VISIBLE_DEVICES") != ("99999" if args.cpu_only else "0")
        or os.environ.get("SGLANG_HUGEPAGE_SIZE") not in (None, "")
        or os.environ.get("BELIEFKV_HOST_REGISTER_NUMA_FALLBACK_NODE", "") != ""
    ):
        raise ValueError("preserve attempts and require the clean explicit interleave operator")
    cpu = json.loads(args.cpu_evidence.read_bytes())
    if cpu["returncode"] or cpu["failed_error_skipped_cases"] or any(
        cpu.get(name) is not True for name in (
            "all_retained_literal_identities_present", "all_previous_source_paths_retained",
            "sources_unchanged_during_execution",
        )
    ):
        raise ValueError("complete retained CPU qualification is required")
    for name in ("report", "source_manifest"):
        item = cpu[name]
        if sha256_file(Path(item["path"])) != item["sha256"]:
            raise ValueError("the CPU report or source manifest changed")
    sources = json.loads(Path(cpu["source_manifest"]["path"]).read_bytes())
    for name, digest in sources.items():
        if sha256_file(ROOT / name) != digest:
            raise ValueError("a CPU-qualified source changed: " + name)
    host_numa.require_source_binding(common, sources, ROOT)
    if str(Path(__file__).relative_to(ROOT)) not in sources:
        raise ValueError("the actual interleave component lacks CPU source binding")
    for name, digest in cpu["external_inputs_sha256"].items():
        if sha256_file(Path(name)) != digest:
            raise ValueError("a retained external qualification input changed")
    native = git_source_state(args.engine, canonical_patch=engine_patch(ROOT, FREE_EXTENT_TREE))
    if (
        native["canonical_patch"]["actual_tree"] != FREE_EXTENT_TREE
        or git_source_state(args.engine) != cpu["native_source"]
    ):
        raise ValueError("retain the qualified native allocation and registration")
    authorization, resources = check_reservation(), qualified.resources(args.port)
    if not resources["ready"]:
        raise ValueError("large Host qualification requires an idle experiment server")
    captured = json.loads(args.capacity_source.read_bytes())["capacity"]
    capacity = host_numa.replay_capacity(common, captured)
    _budget, available = check_host_pool(
        args.node, args.size_gb, memory_policy="interleave",
        memory_nodes=args.memory_nodes, reclaim_policy="clean_file",
    )
    if LinuxMemoryPolicy().read() != {"mode": 3, "nodes": selected["memory_nodes"]}:
        raise ValueError("run under numactl --cpunodebind=1 --interleave=0,1")
    sys.path.insert(0, str(args.engine / "python"))
    import torch
    from sglang.srt.mem_cache.pool_host import common as native_common
    from sglang.srt.mem_cache.storage.mmap import alloc_mmap

    paths = (
        Path(__file__), args.capacity_source, args.cpu_evidence,
        ROOT / "beliefkv/experiments/host_numa.py",
        ROOT / "scripts/preflight_qwen35_host_pool.py",
        args.engine / "python/sglang/srt/mem_cache/storage/mmap/mmap_allocator.py",
        args.engine / "python/sglang/srt/mem_cache/pool_host/common.py",
    )
    pins = {str(path.resolve()): sha256_file(path) for path in paths}
    args.output_dir.mkdir(parents=True, exist_ok=False)
    write(args.output_dir / "entry.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "operator": git_source_state(ROOT), "native": native, "hold": authorization,
        "resources": resources, "actual_process": reservation.process(os.getpid()),
        "configuration": selected, "capacity": capacity,
        "estimated_available_before_allocation_bytes": available, "inputs_sha256": pins,
        "GPU_action": not args.cpu_only, "model_or_workflow_performance_qualified": False,
    })
    state = {"status": "initializing", "GPU_action": not args.cpu_only}

    def publish():
        state["observed_at"] = datetime.now(timezone.utc).isoformat()
        reservation.write_json(args.output_dir / "state.json", state)

    def placement():
        proc = Path("/proc/self")
        result = host_numa.inspect_mapping(
            (proc / "maps").read_text(), (proc / "numa_maps").read_text(),
            expected_bytes=capacity["host_full_bytes"], node=args.node,
            page_bytes=os.sysconf("SC_PAGE_SIZE"), memory_nodes=selected["memory_nodes"],
        )
        if int(result["mapping"]["start_address"], 16) != pool.data_ptr():
            raise ValueError("the observed complete mapping differs from the actual native tensor")
        return result

    pool, window, registered = None, None, False
    publish()
    try:
        if not args.cpu_only:
            torch.cuda.init()
            owners = subprocess.check_output([
                "nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits",
            ], text=True).split()
            if owners != [str(os.getpid())]:
                raise ValueError("the component is not the sole actual GPU owner")
        elif torch.cuda.is_initialized():
            raise ValueError("the CPU qualification unexpectedly initialized CUDA")
        state["status"] = "allocating_complete_interleaved_native_Host_pool"
        publish()
        start = time.monotonic()
        pool = alloc_mmap((capacity["host_full_bytes"],), torch.uint8)
        state.update(allocation_seconds=time.monotonic() - start, placement_before_registration=placement())
        offsets = [0, os.sysconf("SC_PAGE_SIZE"), capacity["host_full_bytes"] // 2, capacity["host_full_bytes"] - 1]
        for index, offset in enumerate(offsets):
            pool[offset] = index + 11
        if [int(pool[offset]) for offset in offsets] != list(range(11, 15)):
            raise ValueError("native interleaved Host canary bytes differ")
        if not args.cpu_only:
            state["status"] = "registering_complete_interleaved_native_Host_pool"
            publish()
            os.environ["SGLANG_HICACHE_HOST_REGISTER_CHUNK_GB"] = str(args.registration_chunk_gb)
            start = time.monotonic()
            native_common._cuda_host_register(
                pool, registration_granularity_bytes=host_numa.FULL_BYTES_PER_TOKEN // 2,
            )
            registered = True
            state["registration_seconds"] = time.monotonic() - start
            ranges = getattr(pool, native_common._CUDA_HOST_REGISTERED_RANGES_ATTR)
            offsets = copy_offsets(capacity["host_full_bytes"], pool.data_ptr(), ranges)
            state.update(status="checking_registered_H2D_D2H_boundaries", actual_registered_ranges=len(ranges))
            publish()
            expected = torch.arange(128, dtype=torch.uint8, device="cuda")
            for offset in offsets:
                window = pool[offset:offset + 128]
                window.copy_(expected, non_blocking=True)
                restored = window.to(device="cuda", non_blocking=True)
                if not torch.equal(restored, expected):
                    raise ValueError("registered native Host boundary copy differs")
            window = None
            torch.cuda.synchronize()
            state["checked_H2D_D2H_bytes"] = len(offsets) * 128 * 2
        after = placement()
        if any(after[name] != state["placement_before_registration"][name] for name in (
            "mapping", "pool_resident_bytes_by_node", "remote_pool_bytes", "mapping_policy",
        )):
            raise ValueError("the complete balanced Host placement changed")
        state.update(
            status="complete_native_interleaved_Host_component",
            placement_after_registration_and_copies=after,
            complete_CUDA_registration_and_bounded_copies_verified=not args.cpu_only,
            CUDA_initialized=torch.cuda.is_initialized(),
            native_migration_kernels_qualified=False, workflow_JCT_gain_proven=False,
        )
    except Exception as error:
        state.update(status="failed_preserve_interleaved_Host_component", error=repr(error))
        raise
    finally:
        if pool is not None:
            address = pool.data_ptr()
            window = None
            if registered:
                native_common._cuda_host_unregister(pool)
                if getattr(pool, native_common._CUDA_HOST_REGISTERED_RANGES_ATTR):
                    raise ValueError("native unregister left registered Host ranges")
                state["native_Host_unregister_complete"] = True
            pool = None
            gc.collect()
            if any(
                int(line.split()[0].split("-")[0], 16) == address
                for line in Path("/proc/self/maps").read_text().splitlines()
            ):
                raise ValueError("the complete native Host mapping remains allocated")
            state["native_Host_mapping_released"] = True
        state["inputs_unchanged_during_execution"] = all(
            sha256_file(Path(name)) == digest for name, digest in pins.items()
        )
        publish()
    if not state["inputs_unchanged_during_execution"]:
        raise ValueError("a bound component input changed")
    print(json.dumps(state), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--capacity-source", type=Path, required=True)
    parser.add_argument("--cpu-evidence", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--size-gb", type=int, choices=(200, 400), default=400)
    parser.add_argument("--node", type=int, default=1)
    parser.add_argument("--memory-nodes", default="0,1")
    parser.add_argument("--registration-chunk-gb", type=int, default=16)
    parser.add_argument("--port", type=int, default=18980)
    parser.add_argument("--cpu-only", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.registration_chunk_gb <= 256:
        parser.error("registration chunk must be between 1 and 256 GiB")
    run(args)


if __name__ == "__main__":
    main()
