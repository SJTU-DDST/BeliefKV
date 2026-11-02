"""Qualify the complete native local Host allocation, CUDA registration and small copies."""

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

from beliefkv.experiments.host_numa import inspect_mapping, registration_evidence
from beliefkv.experiments.native_engine_variant import FREE_EXTENT_TREE, engine_patch
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from beliefkv.runtime import host_registration_numa as registration
from scripts.preflight_qwen35_host_pool import check_host_pool
from scripts.qualify_free_page_h2d_fence import check_reservation
from scripts.run_causal_comparison import qualified, reservation
from scripts.train_workflow_work import write


def run(args):
    if (
        args.output_dir.exists()
        or os.environ.get("CUDA_VISIBLE_DEVICES") != "0"
        or os.getpriority(os.PRIO_PROCESS, 0) != 0
        or git_source_state(ROOT)["dirty"]
    ):
        raise ValueError("preserve attempts and require the clean nice0 GPU component operator")
    if os.environ.get("SGLANG_HUGEPAGE_SIZE") not in (None, ""):
        raise ValueError("qualify the unchanged base-page Host allocation")
    capacity = json.loads(args.capacity_source.read_bytes())["capacity"]
    if (
        capacity["host_full_bytes"] != 200000077824
        or capacity["host_total_bytes"] != capacity["host_full_bytes"]
        or capacity["host_mamba_bytes"] != 0
    ):
        raise ValueError("retain the exact original complete 200 GB FULL-only pool")
    native = git_source_state(args.engine, canonical_patch=engine_patch(ROOT, FREE_EXTENT_TREE))
    if native["canonical_patch"]["actual_tree"] != FREE_EXTENT_TREE:
        raise ValueError("use the unchanged qualified native allocator and registration")
    authorization, resources = check_reservation(), qualified.resources(args.port)
    if not resources["ready"]:
        raise ValueError("GPU or memory resources are unavailable")
    check_host_pool(1, 200, memory_policy="bind", reclaim_policy="clean_file")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    server = args.output_dir / "server"
    server.mkdir()
    os.environ[registration.NODE_ENV] = "1"
    os.environ["BELIEFKV_NATIVE_TELEMETRY_DIR"] = str(server)
    os.environ["SGLANG_HICACHE_HOST_REGISTER_CHUNK_GB"] = str(args.registration_chunk_gb)
    sys.path.insert(0, str(args.engine / "python"))
    import torch
    from sglang.srt.mem_cache.pool_host import common
    from sglang.srt.mem_cache.storage.mmap import alloc_mmap
    from sglang.srt.plugins.hook_registry import HookRegistry

    if registration.LinuxMemoryPolicy().read() != {"mode": 2, "nodes": [1]}:
        raise ValueError("run the component under numactl --cpunodebind=1 --membind=1")
    original = common._cuda_host_register
    registration.register()
    HookRegistry.apply_hooks()
    if (
        registration.TARGET not in HookRegistry._patched
        or common._cuda_host_register is original
    ):
        raise ValueError("the actual native registration hook was not applied")
    paths = (
        Path(__file__), args.capacity_source,
        ROOT / "beliefkv/runtime/host_registration_numa.py",
        ROOT / "beliefkv/experiments/host_numa.py",
        args.engine / "python/sglang/srt/mem_cache/storage/mmap/mmap_allocator.py",
        args.engine / "python/sglang/srt/mem_cache/pool_host/common.py",
    )
    pins = {str(path.resolve()): sha256_file(path) for path in paths}
    worker = reservation.process(os.getpid())
    entry = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "operator": git_source_state(ROOT), "native": native,
        "actual_process": worker, "hold": authorization, "resources": resources,
        "actual_Host_bytes": capacity["host_full_bytes"],
        "registration_chunk_gb": args.registration_chunk_gb,
        "inputs_sha256": pins, "GPU_action": True, "performance_accepted": False,
        "scope": "Complete native Host allocation/registration and bounded CUDA copies, without model service.",
    }
    write(args.output_dir / "entry.json", entry)
    state = {"status": "initializing_CUDA_context", "actual_process": worker}

    def publish():
        state["observed_at"] = datetime.now(timezone.utc).isoformat()
        write(args.output_dir / "state.json", state)

    publish()
    torch.cuda.init()
    owners = subprocess.check_output([
        "nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits",
    ], text=True).split()
    if owners != [str(os.getpid())]:
        raise ValueError("the component is not the sole actual GPU owner")
    pool, registered = None, False
    started = time.monotonic()
    proc = Path("/proc/self")
    try:
        state["status"] = "allocating_complete_local_Host_pool"
        publish()
        pool = alloc_mmap((capacity["host_full_bytes"],), torch.uint8)
        placement = inspect_mapping(
            (proc / "maps").read_text(), (proc / "numa_maps").read_text(),
            expected_bytes=capacity["host_full_bytes"], node=1,
            page_bytes=os.sysconf("SC_PAGE_SIZE"),
        )
        if int(placement["mapping"]["start_address"], 16) != pool.data_ptr():
            raise ValueError("the local mapping is not the native allocated tensor")
        allocation_seconds = time.monotonic() - started
        state.update(
            status="registering_complete_native_Host_pool", allocation_seconds=allocation_seconds,
            placement_before_registration=placement,
        )
        publish()
        granularity = capacity["host_full_bytes"] // capacity["host_full_tokens"] // 2
        common._cuda_host_register(pool, registration_granularity_bytes=granularity)
        registered = True
        registration_seconds = time.monotonic() - started - allocation_seconds
        state["status"] = "checking_bounded_native_Host_CUDA_copies"
        publish()
        ranges = getattr(pool, common._CUDA_HOST_REGISTERED_RANGES_ATTR)
        offsets = sorted({
            0, capacity["host_full_bytes"] - 128,
            *(max(0, ptr - pool.data_ptr() - 64) for ptr, _size in ranges[1:]),
        })
        expected = torch.arange(128, dtype=torch.uint8, device="cuda")
        for offset in offsets:
            window = pool[offset:offset + 128]
            window.copy_(expected, non_blocking=True)
            restored = window.to(device="cuda", non_blocking=True)
            if not torch.equal(restored, expected):
                raise ValueError("a registered boundary CUDA copy returned different bytes")
        del window
        torch.cuda.synchronize()
        placement_after = inspect_mapping(
            (proc / "maps").read_text(), (proc / "numa_maps").read_text(),
            expected_bytes=capacity["host_full_bytes"], node=1,
            page_bytes=os.sysconf("SC_PAGE_SIZE"),
        )
        proof = registration_evidence(
            server / registration.AUDIT_NAME, worker, placement_after["mapping"], 1,
        )
        if any(placement_after[name] != placement[name] for name in (
            "mapping", "pool_resident_bytes_by_node", "remote_pool_bytes",
        )):
            raise ValueError("Host mapping placement changed during registration or copies")
        common._cuda_host_unregister(pool)
        if getattr(pool, common._CUDA_HOST_REGISTERED_RANGES_ATTR):
            raise ValueError("native Host unregister left registered ranges")
        registered = False
        address = placement["mapping"]["start_address"]
        del pool
        pool = None
        gc.collect()
        if any(
            line.split()[0].split("-")[0] == address
            for line in (proc / "maps").read_text().splitlines()
        ):
            raise ValueError("the native Host mapping was not released")
        if any(sha256_file(Path(name)) != digest for name, digest in pins.items()):
            raise ValueError("a bound component input changed")
        state.update(
            status="complete_native_local_Host_registration_component",
            registration_seconds=registration_seconds,
            placement_after_registration_and_copies=placement_after,
            exact_registration_evidence=proof,
            checked_copy_windows=len(offsets), actual_registered_ranges=len(ranges),
            checked_H2D_and_D2H_bytes=len(offsets) * 128 * 2,
            native_Host_unregister_complete=True, native_Host_mapping_released=True,
            model_service_qualified=False, native_migration_kernels_qualified=False,
            workflow_JCT_gain_proven=False, inputs_sha256=pins,
        )
    except Exception as error:
        state.update(status="failed_preserve_native_Host_registration_component", error=repr(error))
        raise
    finally:
        if pool is not None and registered:
            common._cuda_host_unregister(pool)
        publish()
    print(json.dumps({
        "status": state["status"], "actual_Host_bytes": capacity["host_full_bytes"],
        "remote_Host_bytes": placement_after["remote_pool_bytes"],
        "checked_copy_windows": len(offsets), "native_Host_mapping_released": True,
    }), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--capacity-source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--registration-chunk-gb", type=int, default=256)
    parser.add_argument("--port", type=int, default=18978)
    args = parser.parse_args()
    if not 1 <= args.registration_chunk_gb <= 256:
        parser.error("registration chunk must be between 1 and 256 GiB")
    run(args)


if __name__ == "__main__":
    main()
