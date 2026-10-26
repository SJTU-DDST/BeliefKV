#!/usr/bin/env python3
"""Check native H2D fence overlap and reclaimed-page writers on bounded real pools."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import time
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from beliefkv.experiments.prepare_ack_engine import prepare_ack_engine_compatibility
from scripts import run_causal_comparison as controls
from scripts.qualify_paper_prefix_transfers import gpu_processes


EXPERIMENTS = ROOT.parent.parent if ROOT.parent.name == ".worktrees" else ROOT.parent
HOLD = EXPERIMENTS / "ops/gpu_hold_until_20261022_0600.json"
NOT_BEFORE = datetime(2026, 10, 27, 11, tzinfo=ZoneInfo("Asia/Shanghai"))
CONFIG = "configs/migration/predictive_free_page_fence_20261026.json"
SOURCES = (
    "scripts/qualify_free_page_h2d_fence.py",
    "beliefkv/experiments/free_page_h2d_fence.py",
    "beliefkv/experiments/prepare_ack_engine.py",
    "beliefkv/experiments/source_provenance.py", CONFIG,
)
SCENARIOS = (
    "controlled_free", "native_free", "disabled_free", "reclaimed",
    "grouped_reclaimed", "mixed_proof", "merged_free", "renewed_free",
    "negative_control",
)
EARLY = frozenset(("controlled_free", "native_free", "merged_free", "renewed_free"))
RECLAIMED = frozenset(("reclaimed", "grouped_reclaimed", "mixed_proof", "negative_control"))
REQUIRED = (
    "native_and_controlled_loads_overlap_unrelated_writer",
    "default_off_preserves_forward_fence",
    "reclaimed_and_grouped_pages_wait_for_old_writer",
    "mixed_proof_batch_preserves_forward_fence",
    "renewed_boundary_covers_previous_writer",
    "all_safe_restored_layers_preserve_keys_and_values",
    "unsafe_fence_removal_is_detected_by_actual_bytes",
    "native_ack_bytes_and_capacity_reconciled",
)


def guard_gpu(*, allow_current=False):
    at = controls.reservation.now()
    if at < NOT_BEFORE:
        raise controls.fresh.HoldChanged("GPU qualification is reserved until 2026-10-27 11:00 Asia/Shanghai")
    controls.fresh.check_hold({"hold_file": str(HOLD)}, released=True, at=at)
    pids = gpu_processes()
    if any(not allow_current or pid != os.getpid() for pid in pids):
        raise ValueError("GPU is occupied by a process outside this component qualification")


class NativeFenceFixture:
    """Native pools/controller; synthetic KV and a finite writer, without a model."""

    size, layers, tokens = 128, 2, 8
    writer_cycles = 1_000_000_000

    def __init__(self, engine, *, enabled):
        sys.path.insert(0, str(engine / "python"))
        import torch
        from sglang.srt.mem_cache.allocator import TokenToKVPoolAllocator
        from sglang.srt.mem_cache.cache_init_params import CacheInitParams
        from sglang.srt.mem_cache.memory_pool import MHATokenToKVPool, ReqToTokenPool
        from sglang.srt.mem_cache.unified_cache.component_type import ComponentType
        from sglang.srt.mem_cache.unified_radix_cache import UnifiedRadixCache
        from sglang.srt.server_args import ServerArgs, set_global_server_args_for_scheduler

        self.torch, self.cuda, self.device = torch, torch.cuda, "cuda"
        settings = ServerArgs(
            model_path="dummy", page_size=1, hicache_ratio=2.,
            hicache_io_backend="kernel", hicache_mem_layout="page_first",
            hicache_write_policy="write_back",
        )
        set_global_server_args_for_scheduler(settings)
        self.pool = MHATokenToKVPool(
            size=self.size, page_size=1, head_num=2, head_dim=64, dtype=torch.bfloat16,
            layer_num=self.layers, device=self.device, enable_memory_saver=False,
        )
        self.rows = ReqToTokenPool(
            size=4, max_context_len=64, device=self.device, enable_memory_saver=False,
        )
        self.allocator = TokenToKVPoolAllocator(
            size=self.size, dtype=torch.bfloat16, device=self.device,
            kvcache=self.pool, need_sort=False,
        )
        params = CacheInitParams(
            disable=False, req_to_token_pool=self.rows, token_to_kv_pool_allocator=self.allocator,
            page_size=1, tree_components=(ComponentType.FULL,), enable_session_radix_cache=True,
        )
        previous = os.environ.get("BELIEFKV_FREE_PAGE_H2D_FENCE")
        try:
            os.environ["BELIEFKV_FREE_PAGE_H2D_FENCE"] = str(int(enabled))
            self.cache = UnifiedRadixCache(params)
            self.cache.init_hicache(settings, params)
        finally:
            if previous is None:
                os.environ.pop("BELIEFKV_FREE_PAGE_H2D_FENCE", None)
            else:
                os.environ["BELIEFKV_FREE_PAGE_H2D_FENCE"] = previous
        self.controller = self.cache.cache_controller
        self.forward = self.cuda.Stream()
        self.controller.load_fence_stream = self.forward
        self.initialize(enabled=enabled)

    def initialize(self, *, enabled):
        if self.controller.beliefkv_free_page_fence_enabled is not enabled:
            raise ValueError("native controller did not accept the declared fence mode")
        self.owned, self.loaded, self.commands = {}, [], []
        self.host_source = None
        self.writer_done = None
        self.initial_host_free = self.controller.mem_pool_host.available_size()
        self.sequence = 0
        if self.dimensions() != {
            "device_tokens": 128, "host_tokens": 257, "layers": 2, "bytes_per_token": 1024,
        }:
            raise ValueError("native fence qualification pool dimensions differ")

    def dimensions(self):
        size = sum(
            (key[0].numel() * key.element_size() + value[0].numel() * value.element_size())
            for key, value in zip(self.pool.k_buffer, self.pool.v_buffer, strict=True)
        )
        return {
            "device_tokens": self.allocator.size,
            "host_tokens": self.controller.mem_pool_host.anchor_entry.host_pool.size,
            "layers": len(self.pool.k_buffer), "bytes_per_token": size,
        }

    def allocate(self, name, count):
        if name in self.owned:
            raise ValueError("duplicate fixture allocation")
        indices = self.allocator.alloc(count)
        if indices is None:
            raise ValueError("native fixture could not allocate its bounded slots")
        self.owned[name] = indices
        return indices

    def release(self, name, *, grouped=False):
        indices = self.owned.pop(name)
        if grouped:
            self.allocator.free_group_begin()
        self.allocator.free(indices)
        if grouped:
            self.allocator.free_group_end()

    def wait(self, event):
        deadline = time.monotonic() + 15.
        while not event.query():
            if time.monotonic() >= deadline:
                raise TimeoutError("native writer or copy did not finish within its bound")
            time.sleep(.001)

    def source_backup(self, *, pulse=lambda: None):
        pulse()
        torch = self.torch
        indices = self.allocate("source", self.tokens)
        for layer, (key, value) in enumerate(zip(self.pool.k_buffer, self.pool.v_buffer, strict=True)):
            feature = torch.arange(key[0].numel(), device=self.device, dtype=torch.float32).reshape(key.shape[1:])
            key[indices] = (feature / 128. + layer + 2.).to(key.dtype)
            value[indices] = (feature / 128. + layer + 8.).to(value.dtype)
        self.cuda.current_stream().synchronize()
        self.expected = tuple(
            (key[indices].cpu().clone(), value[indices].cpu().clone())
            for key, value in zip(self.pool.k_buffer, self.pool.v_buffer, strict=True)
        )
        pulse()
        self.host_source = self.controller.write(
            indices, node_id=1, beliefkv_command_id="fence-source-backup",
        )
        if self.host_source is None:
            raise ValueError("native D2H could not allocate its Host source")
        self.controller.start_writing()
        if len(self.controller.ack_write_queue) != 1:
            raise ValueError("native D2H did not publish exactly one controller receipt")
        ack = self.controller.ack_write_queue[0]
        self.wait(ack.finish_event)
        if ack.num_bytes != self.tokens * self.dimensions()["bytes_per_token"]:
            raise ValueError("native D2H receipt byte count differs")
        self.controller.ack_write_queue.clear()

    def load(self, *, native=False):
        self.sequence += 1
        command = None if native else "fence-load:" + str(self.sequence)
        indices = self.controller.load(
            self.host_source, node_id=self.sequence + 1, beliefkv_command_id=command,
        )
        if indices is None:
            raise ValueError("native H2D could not allocate its target")
        self.owned["load:" + str(self.sequence)] = indices
        self.loaded.append(indices)
        self.commands.append(command)
        return indices

    def submit_loads(self):
        if self.controller.start_loading() < 0 or len(self.controller.ack_load_queue) != 1:
            raise ValueError("native H2D did not publish one merged controller receipt")
        return self.controller.ack_load_queue[0]

    def restored_bytes_match(self):
        for indices in self.loaded:
            for (key, value), (expected_key, expected_value) in zip(
                zip(self.pool.k_buffer, self.pool.v_buffer, strict=True), self.expected, strict=True,
            ):
                if (
                    not self.torch.equal(key[indices].cpu(), expected_key)
                    or not self.torch.equal(value[indices].cpu(), expected_value)
                ):
                    return False
        return True

    def warmup(self, *, pulse=lambda: None):
        self.source_backup(pulse=pulse)
        # Exercise the same one- and two-operation native copy shapes before the writer.
        for count in (1, 2):
            pulse()
            for _ in range(count):
                self.load(native=True)
            ack = self.submit_loads()
            self.wait(ack.finish_event)
            if not self.restored_bytes_match():
                raise ValueError("warmup copy changed native K/V bytes")
            self.controller.ack_load_queue.clear()
            for name in tuple(self.owned):
                if name.startswith("load:"):
                    self.release(name)
            self.loaded.clear()
            self.commands.clear()
        self.controller.beliefkv_h2d_fence_counts.clear()

    def poison(self, indices):
        for key, value in zip(self.pool.k_buffer, self.pool.v_buffer, strict=True):
            key[indices] = -17.
            value[indices] = -31.

    def enqueue_writer(self, indices, *, delayed=True):
        schedule = self.cuda.current_stream()
        with self.cuda.stream(self.forward):
            self.forward.wait_stream(schedule)
            if delayed:
                self.cuda._sleep(self.writer_cycles)
            self.poison(indices)
            event = self.cuda.Event()
            event.record(stream=self.forward)
        if delayed:
            self.writer_done = event
        return event

    def drain_free_slots(self):
        count = self.allocator.available_size()
        if count:
            self.allocate("capacity-blocker", count)

    def finish(self):
        self.cuda.synchronize()
        for name in tuple(self.owned):
            self.release(name)
        if self.host_source is not None:
            self.controller.mem_pool_host.free(self.host_source)
            self.host_source = None
        self.controller.ack_load_queue.clear()
        if (
            self.allocator.available_size() != self.size
            or self.controller.mem_pool_host.available_size() != self.initial_host_free
            or self.controller.load_queue or self.controller.write_queue
            or self.controller.ack_write_queue
        ):
            raise ValueError("native fence fixture leaked capacity or pending transfers")
        return {
            "final_device_free_tokens": self.allocator.available_size(),
            "initial_host_free_tokens": self.initial_host_free,
            "final_host_free_tokens": self.controller.mem_pool_host.available_size(),
        }

    def close(self):
        self.cuda.synchronize()
        self.cache.release_host_resources()


def scenario(f, name, *, pulse=lambda: None):
    if name not in SCENARIOS:
        raise ValueError("unknown native fence qualification scenario")
    pulse()
    f.warmup(pulse=pulse)
    pulse()
    writer = f.allocate("writer", f.tokens)
    if name == "renewed_free":
        current = f.allocate("current-writer", f.tokens)
        f.drain_free_slots()
        f.enqueue_writer(writer, delayed=False)
        f.release("writer")
    else:
        current = writer
    f.controller.beliefkv_record_free_page_boundary()
    if name == "mixed_proof":
        f.load(native=True)
    if name in RECLAIMED:
        f.drain_free_slots()
    f.enqueue_writer(current)
    if name in RECLAIMED:
        f.release("writer", grouped=name == "grouped_reclaimed")
    f.load(native=name in ("native_free", "merged_free"))
    if name == "merged_free":
        f.load()
    pulse()
    original_stream = f.controller.load_fence_stream
    try:
        if name == "negative_control":
            # Deliberately unsafe only within this isolated component's owned pages.
            f.controller.load_fence_stream = None
        ack = f.submit_loads()
    finally:
        f.controller.load_fence_stream = original_stream
    f.wait(ack.finish_event)
    writer_finished = f.writer_done.query()
    f.wait(f.writer_done)
    match = f.restored_bytes_match()
    expected_overlap = name in EARLY or name == "negative_control"
    if writer_finished is expected_overlap:
        raise ValueError("actual copy/writer ordering differs from the declared fence scenario")
    if match is (name == "negative_control"):
        raise ValueError("native fence did not preserve safe bytes or detect the unsafe control")
    tokens = f.tokens * len(f.loaded)
    if ack.num_tokens != tokens or ack.num_bytes != tokens * f.dimensions()["bytes_per_token"]:
        raise ValueError("native H2D controller receipt changed copied work")
    controlled = {command for command in f.commands if command is not None}
    expected_receipts = Counter(f.commands) if controlled else Counter()
    if Counter(row.command_id for row in ack.child_receipts) != expected_receipts or any(
        row.num_bytes != f.tokens * f.dimensions()["bytes_per_token"]
        or dict(row.num_tokens_by_pool) != {"kv": f.tokens}
        for row in ack.child_receipts
    ):
        raise ValueError("native merged child receipts lost controlled copy ownership")
    same_page = True
    if name in RECLAIMED or name == "renewed_free":
        same_page = f.torch.equal(f.loaded[-1], writer)
        if not same_page:
            raise ValueError("old writer did not actually touch the reused restore pages")
    counts = dict(f.controller.beliefkv_h2d_fence_counts)
    if name in EARLY and counts.get("free_event_batches") != 1:
        raise ValueError("early copy did not use the recorded free-page event")
    if name not in EARLY and name not in ("negative_control", "disabled_free") and (
        counts.get("forward_stream_batches") != 1
    ):
        raise ValueError("unproven or mixed load did not use the original forward fence")
    result = {
        "status": "expected_corruption_detected" if name == "negative_control" else "complete",
        "writer_finished_at_copy_ack": writer_finished,
        "restored_bytes_match_after_writer_completion": match,
        "actual_reused_writer_pages": same_page if name in RECLAIMED or name == "renewed_free" else None,
        "copied_tokens": tokens, "controller_h2d_bytes": ack.num_bytes,
        "controlled_child_receipts": len(controlled), "fence_counts": counts,
        **f.finish(),
    }
    pulse()
    return result


def validate_report(report, *, engine_tree, patch_sha256):
    rows = report.get("scenarios", {})
    native = report.get("native_engine", {})
    compatibility = report.get("native_compatibility") or {}
    candidate = json.loads((ROOT / CONFIG).read_bytes())["engine_candidate"]
    if (
        report.get("status") != "native_free_page_fence_components_qualified"
        or native.get("actual_tree") != engine_tree
        or native.get("expected_tree") != engine_tree
        or native.get("matches_worktree") is not True
        or compatibility.get("policy") != "opt_in_preexisting_free_page_h2d_fence"
        or compatibility.get("source_engine_tree") != candidate["base_tree"]
        or compatibility.get("candidate_engine_tree") != engine_tree
        or compatibility.get("native_method_projection_matches") is not True
        or compatibility.get("same_proof_applies_to_native_demand") is not True
        or type(report.get("owned_gpu_pid")) is not int or report["owned_gpu_pid"] <= 0
        or type(report.get("gpu")) is not str or not report["gpu"].strip()
        or report.get("native_patch_sha256") != patch_sha256
        or report.get("source_sha256") != {name: sha256_file(ROOT / name) for name in SOURCES}
        or report.get("dimensions") != {"device_tokens": 128, "host_tokens": 257, "layers": 2, "bytes_per_token": 1024}
        or set(rows) != set(SCENARIOS)
        or report.get("whole_model_qualified") is not False
        or report.get("model_numerical_equivalence_verified") is not False
        or report.get("performance_accepted") is not False
        or any(report.get(name) is not True for name in REQUIRED)
    ):
        raise ValueError("native fence component evidence is incomplete, stale or overclaimed")
    for name, row in rows.items():
        negative = name == "negative_control"
        count = 16 if name in ("mixed_proof", "merged_free") else 8
        if (
            row.get("status") != ("expected_corruption_detected" if negative else "complete")
            or row.get("writer_finished_at_copy_ack") is not (name not in EARLY and not negative)
            or row.get("restored_bytes_match_after_writer_completion") is not (not negative)
            or row.get("copied_tokens") != count or row.get("controller_h2d_bytes") != count * 1024
            or row.get("final_device_free_tokens") != 128
            or type(row.get("initial_host_free_tokens")) is not int
            or not 0 < row["initial_host_free_tokens"] <= 257
            or row.get("initial_host_free_tokens") != row.get("final_host_free_tokens")
            or row.get("controlled_child_receipts") != (0 if name == "native_free" else 1)
            or (name in RECLAIMED or name == "renewed_free") and row.get("actual_reused_writer_pages") is not True
        ):
            raise ValueError("native fence scenario has invalid bytes, ordering, receipts or capacity")
        counts = row.get("fence_counts", {})
        if (
            not isinstance(counts, dict)
            or any(type(value) is not int or value < 0 for value in counts.values())
            or counts.get("free_boundary_events", 0) != (0 if name == "disabled_free" else 1)
            or counts.get("free_event_batches", 0) != int(name in EARLY)
            or counts.get("forward_stream_batches", 0) != int(name not in EARLY and name not in ("disabled_free", "negative_control"))
        ):
            raise ValueError("native fence scenario has inconsistent executed fence counters")


def qualify(engine, patch):
    guard_gpu()
    native = git_source_state(engine, canonical_patch=patch)["canonical_patch"]
    if not native["matches_worktree"]:
        raise ValueError("native fence engine differs from its canonical patch")
    candidate = json.loads((ROOT / CONFIG).read_bytes())["engine_candidate"]
    if native["actual_tree"] != candidate["candidate_tree"]:
        raise ValueError("native fence engine differs from the declared candidate tree")
    compatibility = prepare_ack_engine_compatibility(engine, {
        "engine_tree": candidate["base_tree"],
        "environment": {"AB_MODE": "off", "NATIVE_POLICY_BASELINE": "1", "PREPARE_HOST": "0"},
    }, candidate_tree=native["actual_tree"])
    sys.path.insert(0, str(engine / "python"))
    import torch

    if torch.cuda.device_count() != 1:
        raise ValueError("native fence qualification requires one explicit visible GPU")
    rows, dimensions = {}, None
    for name in SCENARIOS:
        guard_gpu(allow_current=True)
        fixture = NativeFenceFixture(engine, enabled=name != "disabled_free")
        try:
            dimensions = fixture.dimensions()
            rows[name] = scenario(fixture, name, pulse=lambda: guard_gpu(allow_current=True))
        finally:
            fixture.close()
    guard_gpu(allow_current=True)
    if os.getpid() not in gpu_processes():
        raise ValueError("native fence qualification did not execute on its owned GPU")
    report = {
        "status": "native_free_page_fence_components_qualified",
        "native_engine": native, "native_compatibility": compatibility,
        "native_patch_sha256": sha256_file(patch),
        "source_sha256": {name: sha256_file(ROOT / name) for name in SOURCES},
        "dimensions": dimensions, "scenarios": rows,
        **dict.fromkeys(REQUIRED, True),
        "owned_gpu_pid": os.getpid(), "gpu": torch.cuda.get_device_name(),
        "whole_model_qualified": False, "model_numerical_equivalence_verified": False,
        "performance_accepted": False,
        "scope": (
            "Real native Device/Host controller copies, all-layer synthetic K/V and a finite "
            "CUDA writer. Negative control touches only this fixture's pages. "
            "No model forward, policy lifecycle or workload performance is qualified."
        ),
    }
    validate_report(report, engine_tree=native["actual_tree"], patch_sha256=sha256_file(patch))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-checkout", type=Path, required=True)
    parser.add_argument("--canonical-patch", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        parser.error("preserve previous GPU qualification artifacts")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    report = None
    try:
        report = qualify(args.engine_checkout.resolve(), args.canonical_patch.resolve())
    except BaseException as error:
        report = {
            "status": "failed", "error_type": type(error).__name__, "error": str(error),
            "whole_model_qualified": False, "performance_accepted": False,
        }
        raise
    finally:
        report["elapsed_seconds"] = time.monotonic() - started
        with (args.output_dir / "summary.json").open("x") as output:
            json.dump(report, output, indent=2, allow_nan=False)
            output.write("\n")


if __name__ == "__main__":
    main()
