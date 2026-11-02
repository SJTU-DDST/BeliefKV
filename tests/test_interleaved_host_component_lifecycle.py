"""CPU lifecycle checks with explicit allocation/NUMA/CUDA stand-ins, not hardware proof."""

import json
from pathlib import Path
from types import SimpleNamespace as NS
import sys

import pytest

from beliefkv.experiments.source_provenance import sha256_file
from scripts import qualify_interleaved_host_pool as component


@pytest.mark.parametrize("allocation_failure", (False, True))
def test_actual_component_publishes_all_phases_and_preserves_allocation_failures(
    monkeypatch, tmp_path, allocation_failure, *, gpu_mode=False,
):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0" if gpu_mode else "99999")
    monkeypatch.delenv("SGLANG_HUGEPAGE_SIZE", raising=False)
    monkeypatch.delenv("BELIEFKV_HOST_REGISTER_NUMA_FALLBACK_NODE", raising=False)
    native_state = {"dirty": False, "canonical_patch": {"actual_tree": component.FREE_EXTENT_TREE}}
    monkeypatch.setattr(component, "git_source_state", lambda *_args, **_kwargs: native_state)
    monkeypatch.setattr(component, "engine_patch", lambda *_args: None)
    monkeypatch.setattr(component.os, "getpriority", lambda *_args: 0)
    monkeypatch.setattr(component, "check_reservation", lambda: {"permitted": True})
    monkeypatch.setattr(component.qualified, "resources", lambda _port: {"ready": True})
    monkeypatch.setattr(component, "check_host_pool", lambda *_args, **_kwargs: (400 * 10**9, 500 * 10**9))
    monkeypatch.setattr(component.LinuxMemoryPolicy, "read", lambda _self: {"mode": 3, "nodes": [0, 1]})
    monkeypatch.setattr(component.host_numa, "require_source_binding", lambda *_args: None)
    monkeypatch.setattr(component.host_numa, "replay_capacity", lambda *_args: {"host_full_bytes": 16384})
    placement = {
        "mapping": {"start_address": "1234000"}, "pool_resident_bytes_by_node": {"0": 8192, "1": 8192},
        "remote_pool_bytes": 8192, "mapping_policy": "interleave:0-1",
    }
    monkeypatch.setattr(component.host_numa, "inspect_mapping", lambda *_args, **_kwargs: placement)
    pool = {}
    allocator_calls = []

    class Pool:
        def data_ptr(self):
            return 0x1234000

        def __getitem__(self, key):
            return pool[key]

        def __setitem__(self, key, value):
            pool[key] = value

    def allocate(shape, _dtype):
        allocator_calls.append(shape)
        if allocation_failure:
            raise RuntimeError("explicit allocation failure")
        return Pool()

    materialized = []

    def materialize(size, *, dtype, device):
        assert size == 1 and dtype == "standin_uint8" and device == "cuda"
        materialized.append(True)
        return object()

    monkeypatch.setitem(sys.modules, "torch", NS(
        uint8="standin_uint8", empty=materialize,
        cuda=NS(is_initialized=lambda: bool(materialized), init=lambda: None, synchronize=lambda: None),
    ))
    if gpu_mode:
        monkeypatch.setattr(component.subprocess, "check_output", lambda *_args, **_kwargs: (
            str(component.os.getpid()) if materialized else ""
        ))
    monkeypatch.setitem(sys.modules, "sglang.srt.mem_cache.pool_host", NS(common=NS()))
    monkeypatch.setitem(sys.modules, "sglang.srt.mem_cache.storage.mmap", NS(alloc_mmap=allocate))
    engine = tmp_path / "engine"
    for name in ("storage/mmap/mmap_allocator.py", "pool_host/common.py"):
        path = engine / "python/sglang/srt/mem_cache" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Explicit CPU fixture; no native hardware behavior.\n")
    report, sources, cpu, capacity = (tmp_path / name for name in (
        "report.json", "sources.json", "cpu.json", "capacity.json",
    ))
    report.write_text("{}")
    source = Path(component.__file__)
    sources.write_text(json.dumps({str(source.relative_to(component.ROOT)): sha256_file(source)}))
    cpu.write_text(json.dumps({
        "returncode": 0, "failed_error_skipped_cases": 0,
        "all_retained_literal_identities_present": True, "all_previous_source_paths_retained": True,
        "sources_unchanged_during_execution": True, "native_source": native_state,
        "report": {"path": str(report), "sha256": sha256_file(report)},
        "source_manifest": {"path": str(sources), "sha256": sha256_file(sources)},
        "external_inputs_sha256": {},
    }))
    capacity.write_text('{"capacity": {}}')
    args = NS(
        size_gb=400, node=1, memory_nodes="0,1", output_dir=tmp_path / "component",
        cpu_only=not gpu_mode, engine=engine, cpu_evidence=cpu, port=18980,
        capacity_source=capacity, registration_chunk_gb=16,
    )
    published = []
    actual_write = component.reservation.write_json

    def publish(path, value):
        published.append(value["status"])
        actual_write(path, value)

    monkeypatch.setattr(component.reservation, "write_json", publish)
    if allocation_failure:
        with pytest.raises(RuntimeError, match="explicit allocation failure"):
            component.run(args)
    else:
        component.run(args)
    state = json.loads((args.output_dir / "state.json").read_bytes())
    assert allocator_calls == [(16384,)]
    assert published[:2] == ["initializing", "allocating_complete_interleaved_native_Host_pool"]
    assert state["inputs_unchanged_during_execution"]
    assert state["GPU_action"] is gpu_mode
    assert bool(materialized) is gpu_mode
    if allocation_failure:
        assert state["status"] == "failed_preserve_interleaved_Host_component"
        assert "explicit allocation failure" in state["error"]
    else:
        assert state["status"] == "complete_native_interleaved_Host_component"
        assert not state["complete_CUDA_registration_and_bounded_copies_verified"]
    saved = (args.output_dir / "entry.json").read_bytes()
    with pytest.raises(ValueError, match="preserve attempts"):
        component.run(args)
    assert (args.output_dir / "entry.json").read_bytes() == saved


def test_visible_GPU_context_is_materialized_before_the_sole_owner_guard_and_Host_allocation(monkeypatch, tmp_path):
    test_actual_component_publishes_all_phases_and_preserves_allocation_failures(
        monkeypatch, tmp_path, True, gpu_mode=True,
    )
