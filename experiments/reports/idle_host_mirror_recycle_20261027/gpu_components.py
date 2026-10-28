"""Check mirror replacement with native GPU copies and a protected GPU reader."""

from array import array
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from beliefkv.runtime.sglang_v0520_prediction import NativeToolWaitHint
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts import qualify_free_page_h2d_fence as native

SOURCES = (
    "beliefkv/runtime/idle_host_mirror_recycle.py",
    "beliefkv/runtime/sglang_v0520_runtime.py",
    "beliefkv/runtime/sglang_v0520_admission.py",
    "beliefkv/runtime/sglang_v0520_prediction.py",
    "beliefkv/experiments/internal_comparison.py",
    "scripts/qualify_free_page_h2d_fence.py",
    "configs/migration/predictive_idle_host_mirror_recycle_20261027.json",
)


@contextmanager
def settings(enabled):
    values = arm_environment({
        "IDLE_HOST_MIRROR_RECYCLE": str(int(enabled)), "AGENT_ADMISSION": "off",
        "PREPARE_HOST": "1", "IDLE_PREPARE_POLICY": "cold",
    }, "full")
    before = {key: os.environ.get(key) for key in values}
    try:
        os.environ.update(values)
        yield
    finally:
        for key, value in before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def scenario(engine, name):
    from sglang.srt.mem_cache.base_prefix_cache import InsertParams
    from sglang.srt.mem_cache.radix_cache import RadixKey

    f = native.NativeFenceFixture(engine, enabled=True)
    cache, torch, controller = f.cache, f.torch, f.controller
    module = sys.modules[type(cache).__module__]
    batch_before = module.BELIEFKV_IDLE_FULL_PREPARE_MAX_BYTES
    runtime, blocker, host_lock, device_lock = None, None, None, None
    socket_dir = TemporaryDirectory(prefix="bkv-mirror-gpu-")
    unit = f.dimensions()["bytes_per_token"]

    def insert(tokens, session):
        indices = f.allocator.alloc(len(tokens))
        if indices is None:
            raise ValueError("component Device allocation failed")
        values = torch.tensor(tokens, dtype=torch.float32, device=indices.device)[:, None, None]
        shape = f.pool.k_buffer[0].shape[1:]
        feature = torch.arange(
            shape[0] * shape[1], device=indices.device, dtype=torch.float32,
        ).reshape(1, *shape) / 512.
        for layer, (keys, vals) in enumerate(zip(f.pool.k_buffer, f.pool.v_buffer, strict=True)):
            keys[indices] = (values + feature + layer / 8.).to(keys.dtype)
            vals[indices] = (values + feature + layer / 8. + 4.).to(vals.dtype)
        node_id = cache.insert(InsertParams(
            key=RadixKey(array("q", tokens)), value=indices,
        )).last_device_node
        generation = cache.ensure_session_generation(session)
        cache.session_refs.register_session_ref(NS(
            session_id=session, session_generation=generation, session=None,
            last_node=node_id, origin_input_ids=array("q", tokens),
            output_ids=array("q"), extra_key=None,
        ))
        return cache.tree_core.node_by_id(node_id), generation

    def save(indices):
        return tuple((keys[indices].clone(), vals[indices].clone()) for keys, vals in zip(
            f.pool.k_buffer, f.pool.v_buffer, strict=True,
        ))

    def same(indices, expected):
        return all(
            torch.equal(keys[indices], old_key) and torch.equal(vals[indices], old_val)
            for (keys, vals), (old_key, old_val) in zip(
                zip(f.pool.k_buffer, f.pool.v_buffer, strict=True), expected, strict=True,
            )
        )

    try:
        cold, generation = insert([1, 2, 3, 4], "cold")
        hot, _ = insert(list(range(100, 108)), "hot")
        cold_indices, hot_indices = cold.component_data[0].value, hot.component_data[0].value
        torch.cuda.synchronize()
        cold_expected, hot_expected = save(cold_indices), save(hot_indices)
        torch.cuda.synchronize()
        # The production SPAWN wrapper reserves 64MiB, larger than this fixture.
        if not cache._prepare_beliefkv_cold_full_nodes(
            ((hot.id, hot.creation_time, 8),), read_only_backup=True,
            max_batch_bytes=8 * unit,
        ):
            raise ValueError("native Hot checkpoint did not enqueue")
        deadline = time.monotonic() + 15.
        while cache.ongoing_write_through:
            native.guard_gpu(allow_current=True)
            cache.writing_check()
            if time.monotonic() >= deadline:
                raise TimeoutError("native Hot backup ACK did not settle")
            time.sleep(.001)
        device_lock = cache.inc_lock_ref(hot.id).to_dec_params()
        old_hot_host_slots = set(hot.component_data[0].host_value.tolist())
        # Warm the exact four-token copy shape before the finite reader window.
        warm_host = controller.write(cold_indices, node_id=-1)
        if warm_host is None:
            raise ValueError("component warmup lacks Host slots")
        controller.start_writing()
        f.wait(controller.ack_write_queue[0].finish_event)
        controller.ack_write_queue.clear()
        controller.evict_host(warm_host)
        blocker = controller.mem_pool_host.alloc(controller.mem_pool_host.available_size())
        if blocker is None or controller.mem_pool_host.available_size() != 0:
            raise ValueError("native Host pool is not actually full")
        f.allocate("capacity-blocker", f.allocator.available_size())
        if f.allocator.available_size() != 0:
            raise ValueError("native Device pool is not actually full")
        module.BELIEFKV_IDLE_FULL_PREPARE_MAX_BYTES = 4 * unit
        with settings(name != "disabled"):
            runtime = NativeAdmissionRuntime(
                event_socket_path=str(Path(socket_dir.name) / "events.sock"),
            )
        runtime.on_events((
            RuntimeEvent("0", 0., Kind.WORKFLOW_START, "fixture"),
            RuntimeEvent(
                "1", 1., Kind.INVOCATION_CREATE, "fixture", invocation_id="cold",
                context_id="cold-context", agent_definition_id="fixture",
                agent_instance_id="fixture",
            ),
            RuntimeEvent(
                "2", 2., Kind.TOOL_START, "fixture", invocation_id="cold",
                attributes={"tool_family": "shell", "tool_run_id": "fixture-tool"},
            ),
        ))
        key = PrefillCandidateKey(
            "fixture-request", "fixture", "cold", "cold-context", 0, 1, "cold", generation,
        )
        runtime.context_sessions[key.context_id] = key
        runtime._context_session_changed(key.context_id)
        runtime.predictor_sha256 = "a" * 64
        now = time.monotonic() * 1000.
        runtime.tool_wait_hints[key.context_id] = NativeToolWaitHint(
            key, 60000., 120000., 240000., now, now + 60000.,
            runtime.predictor_sha256, 2.,
        )
        runtime._native_running_batch = NS(reqs=[NS(rid="protected-reader")])
        runtime.attach_native_cache(cache)
        if name == "host_locked":
            host_lock = cache.tree_core.inc_host_lock_ref(hot.id).to_dec_params()
        elif name == "tool_returned":
            runtime.on_events((RuntimeEvent(
                "3", 3., Kind.TOOL_END, "fixture", invocation_id="cold",
                attributes={"tool_run_id": "fixture-tool"},
            ),))
        read_done = torch.cuda.Event()
        with torch.cuda.stream(f.forward):
            f.forward.wait_stream(torch.cuda.current_stream())
            torch.cuda._sleep(f.writer_cycles)
            protected_read = save(hot_indices)
            read_done.record()
        if read_done.query():
            raise ValueError("finite protected reader did not remain in flight")
        native.guard_gpu(allow_current=True)
        runtime.dispatch_idle_prepare()
        reader_still_pending = not read_done.query()
        if name == "recycle" and not reader_still_pending:
            raise ValueError("fixture lost the protected reader window before release completed")
        if f.allocator.available_size() != 0:
            raise ValueError("mirror recycling freed Device slots")
        released = runtime.counts["idle_host_mirror_recycle_host_tokens"]
        positive = name == "recycle"
        if (
            released != (8 if positive else 0)
            or (hot.component_data[0].host_value is None) != positive
            or bool(cache.ongoing_write_through) != positive
            or hot.component_data[0].value is not hot_indices
            or cold.component_data[0].value is not cold_indices
        ):
            raise ValueError("mirror/backup actions differ from the declared scenario")
        pre_ack_demote_refused = not cache._can_beliefkv_full_demote(cold, full_session_ref=1)
        f.wait(read_done)
        if not same(hot_indices, hot_expected) or any(
            not torch.equal(actual, expected)
            for observed, saved in zip(protected_read, hot_expected, strict=True)
            for actual, expected in zip(observed, saved, strict=True)
        ):
            raise ValueError("Host recycling changed a protected GPU read")
        restored = False
        if positive:
            if not pre_ack_demote_refused:
                raise ValueError("unacknowledged cold backup became demotable")
            deadline = time.monotonic() + 15.
            while cache.ongoing_write_through:
                native.guard_gpu(allow_current=True)
                cache.writing_check()
                if time.monotonic() >= deadline:
                    raise TimeoutError("cold checkpoint did not ACK")
                time.sleep(.001)
            host_source = cold.component_data[0].host_value.clone()
            if len(host_source) != 4 or not set(host_source.tolist()) <= old_hot_host_slots:
                raise ValueError("cold backup did not consume the released Hot Host slots")
            if cache.reclaim_beliefkv_handoff_capacity(full_tokens=4, mamba_slots=0) != {0: 4}:
                raise ValueError("settled cold checkpoint did not release exactly its Device pages")
            for keys, vals in zip(f.pool.k_buffer, f.pool.v_buffer, strict=True):
                keys[cold_indices] = -123.
                vals[cold_indices] = -456.
            torch.cuda.synchronize()
            loaded = controller.load(host_source, node_id=-1)
            if loaded is None:
                raise ValueError("raw native restore could not allocate its four released pages")
            controller.start_loading()
            ack = controller.ack_load_queue[0]
            f.wait(ack.finish_event)
            if ack.num_bytes != 4 * unit or not same(loaded, cold_expected):
                raise ValueError("native restore did not preserve every layer's K/V bytes")
            controller.ack_load_queue.clear()
            f.allocator.free(loaded)
            restored = True
        return {
            "scenario": name, "released_host_tokens": released,
            "protected_device_tokens": 8, "cold_backup_tokens": 4 if positive else 0,
            "device_tokens_released_by_mirror_recycle": 0,
            "reader_pending_after_callback": reader_still_pending,
            "protected_all_layer_read_unchanged": True,
            "pre_ack_demote_refused": pre_ack_demote_refused,
            "raw_controller_restore_after_page_poison_verified": restored,
            "cold_backup_reused_released_hot_host_slots": positive,
            "runtime_counts": dict(runtime.counts),
        }
    finally:
        f.cuda.synchronize()
        if runtime is not None:
            runtime.close()
        socket_dir.cleanup()
        if host_lock is not None:
            cache.tree_core.dec_host_lock_ref(hot.id, host_lock)
        if device_lock is not None:
            cache.dec_lock_ref(hot.id, device_lock)
        if blocker is not None:
            controller.evict_host(blocker)
        module.BELIEFKV_IDLE_FULL_PREPARE_MAX_BYTES = batch_before
        f.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).with_suffix(".json"))
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError("preserve the original GPU component result")
    engine = Path(os.environ["SGLANG_SOURCE_CHECKOUT"]).resolve()
    patch = ROOT / "patches/sglang-v0.5.20-beliefkv-staging.patch"
    report = {"status": "failed", "performance_accepted": False}
    sources = {name: sha256_file(ROOT / name) for name in SOURCES}
    try:
        cpu_path = ROOT / "experiments/reports/idle_host_mirror_recycle_20261027/shared_cpu.evidence.json"
        cpu = json.loads(cpu_path.read_bytes())
        manifest = ROOT / "experiments/reports/idle_host_mirror_recycle_20261027/shared_cpu.sources.json"
        qualified = json.loads(manifest.read_bytes())
        if (
            sha256_file(manifest) != cpu["source_manifest"]["sha256"]
            or any(qualified.get(name) != digest for name, digest in sources.items())
        ):
            raise ValueError("GPU component sources differ from the shared CPU qualification")
        native.guard_gpu()
        if os.getpriority(os.PRIO_PROCESS, os.getpid()) != 0:
            raise ValueError("GPU component process must run at nice0")
        engine_state = git_source_state(engine, canonical_patch=patch)["canonical_patch"]
        if (
            not engine_state["matches_worktree"]
            or engine_state["actual_tree"] != "11b84f7c31986782be3fbe699bae2574ef092389"
        ):
            raise ValueError("component engine differs from the qualified common engine")
        sys.path.insert(0, str(engine / "python"))
        rows = {}
        for name in ("disabled", "host_locked", "tool_returned", "recycle"):
            native.guard_gpu(allow_current=True)
            rows[name] = scenario(engine, name)
        if any(sha256_file(ROOT / name) != digest for name, digest in sources.items()):
            raise ValueError("component source changed during qualification")
        report = {
            "status": "native_gpu_host_mirror_components_qualified",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "native_engine": engine_state, "source_sha256": sources,
            "cpu_qualification_sha256": sha256_file(cpu_path),
            "cpu_source_manifest_sha256": sha256_file(manifest),
            "operator_sha256": sha256_file(Path(__file__)),
            "owned_gpu_pid": os.getpid(), "gpu_authorization": native.check_reservation(),
            "scenarios": rows, "whole_model_qualified": False,
            "performance_accepted": False,
            "scope": [
                "Actual native GPU/Host pools, copies, locks, ACKs and per-layer K/V bytes.",
                "Fixture idle batch is scaled to four tokens, not the formal 512MiB batch.",
                "A finite synthetic GPU reader is not a model forward or a timing benchmark.",
                "Controlled native session/graph keys and long-tool hints are fixture inputs.",
                "Raw controller restoration is checked; no native model-consumer admission is claimed.",
                "The common model, workload JCT and throughput remain unqualified here.",
            ],
        }
    except BaseException as error:
        report.update(
            error_type=type(error).__name__, error=str(error),
            failed_scenario=locals().get("name"), source_sha256=sources,
            operator_sha256=sha256_file(Path(__file__)),
        )
        raise
    finally:
        with output.open("x") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write("\n")
        print(json.dumps({"status": report["status"], "output": str(output)}, indent=2))


if __name__ == "__main__":
    main()
