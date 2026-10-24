#!/usr/bin/env python3
"""Bounded component diagnostics after a completed, released comparison."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file


def predecessor_ready(plan, state, alive):
    if state.get("run_root") != plan["run_root"]:
        raise ValueError("comparison state belongs to another run")
    identities = [
        *state.get("owned", ()),
        *(state[name] for name in ("watcher", "server", "driver") if state.get(name)),
    ]
    if state.get("status") == "collected_performance_review_pending":
        if state.get("completed_arms") != plan["order"]:
            raise ValueError("comparison did not complete every registered arm")
        return not any(alive(identity) for identity in identities)
    if state.get("status") in ("running_full", "running_scheduler_only"):
        if not state.get("watcher") or not alive(state["watcher"]):
            raise ValueError("comparison supervisor disappeared without completion")
        return False
    raise ValueError("comparison is not a completed or live qualified run")


def validate_component_shape(config):
    text = config.get("text_config", config)
    fields = (
        "hidden_size", "linear_num_key_heads", "linear_num_value_heads",
        "linear_key_head_dim", "linear_value_head_dim",
    )
    if any(type(text.get(name)) is not int or text[name] <= 0 for name in fields):
        raise ValueError("model lacks positive GDN dimensions")
    if (
        text["linear_key_head_dim"] != 128 or text["linear_value_head_dim"] != 128
        or text["linear_num_value_heads"] % text["linear_num_key_heads"]
    ):
        raise ValueError("diagnostic requires the qualified Qwen3.5 GDN geometry")
    return {name: text[name] for name in fields}


def diagnostic_inputs(previous):
    settings = previous.get("reference_environment", {})
    cuda_home = settings.get("CUDA_HOME")
    model = previous.get("source_server_parameters", {}).get("model_path")
    if any(type(value) is not str or not Path(value).is_absolute() for value in (cuda_home, model)):
        raise ValueError("predecessor lacks its actual CUDA prefix or model path")
    return Path(cuda_home), Path(model) / "config.json"


def cuda_activities(events):
    totals, counts = defaultdict(float), defaultdict(int)
    for event in events:
        if str(event.device_type).rsplit(".", 1)[-1] != "CUDA":
            continue
        duration = event.device_time_total
        if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration < 0:
            raise ValueError("invalid CUDA activity duration")
        totals[event.name] += duration
        counts[event.name] += 1
    if not counts or sum(totals.values()) <= 0:
        raise ValueError("profiler produced no timed CUDA activities")
    return [
        {"name": name, "calls": counts[name], "total_us": total}
        for name, total in sorted(totals.items(), key=lambda item: item[1], reverse=True)
    ]


def gpu_components(plan):
    import torch

    engine = Path(plan["engine_checkout"])
    sys.path.insert(0, str(engine / "python"))
    from sglang.srt.layers.attention.linear.kernels.gdn_flashinfer import FlashInferGDNKernel
    from sglang.srt.layers.quantization.unquant import bf16_gemm_dispatch

    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise ValueError("diagnostic requires one explicitly visible GPU")
    if torch.cuda.get_device_capability() != (9, 0):
        raise ValueError("this diagnostic targets the current SM90 environment")
    module = sys.modules[FlashInferGDNKernel.__module__]
    expected = engine / "python/sglang/srt/layers/attention/linear/kernels/gdn_flashinfer.py"
    if Path(module.__file__).resolve() != expected.resolve():
        raise ValueError("diagnostic imported a different GDN implementation")
    torch.manual_seed(21)
    torch.cuda.manual_seed_all(21)
    dims = validate_component_shape(json.loads(Path(plan["model_config"]).read_bytes()))
    kernel = FlashInferGDNKernel()
    hk, hv = dims["linear_num_key_heads"], dims["linear_num_value_heads"]
    dk, dv, hidden = dims["linear_key_head_dim"], dims["linear_value_head_dim"], dims["hidden_size"]
    run = Path(plan["run_root"])

    def measure(function):
        for _ in range(5):
            function()
        torch.cuda.synchronize()
        values = []
        for _ in range(5):
            start, end = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
            wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
            start.record()
            for _ in range(16):
                function()
            end.record()
            cpu_ms = (time.thread_time_ns() - cpu) / 1e6 / 16
            end.synchronize()
            values.append({
                "gpu_stream_span_ms": start.elapsed_time(end) / 16,
                "wall_ms": (time.perf_counter_ns() - wall) / 1e6 / 16,
                "submission_thread_cpu_ms": cpu_ms,
            })
        return {
            key: {"mean": statistics.mean(row[key] for row in values),
                  "median": statistics.median(row[key] for row in values)}
            for key in values[0]
        }

    width = 2 * hk * dk + 2 * hv * dv
    wq = torch.randn(width, hidden, dtype=torch.bfloat16, device="cuda") * 0.02
    wb = torch.randn(2 * hv, hidden, dtype=torch.bfloat16, device="cuda") * 0.02
    merged = torch.cat((wq, wb))
    projections = []
    with torch.inference_mode():
        for tokens in (1, 24, 64, 256, 512, 1024, 4096):
            x = torch.randn(tokens, hidden, dtype=torch.bfloat16, device="cuda")

            def separate():
                return bf16_gemm_dispatch(x, wq, None), bf16_gemm_dispatch(x, wb, None)

            def combined():
                return bf16_gemm_dispatch(x, merged, None)

            expected_q, expected_b = separate()
            output = combined()
            torch.testing.assert_close(output[:, :width], expected_q, rtol=0.02, atol=0.02)
            torch.testing.assert_close(output[:, width:], expected_b, rtol=0.02, atol=0.02)
            projections.append({
                "tokens": tokens, "separate": measure(separate), "merged": measure(combined),
                "numerical_close": True, "rtol": 0.02, "atol": 0.02,
                "current_small_input_candidate_domain": tokens <= 1024,
            })
        prefills = []
        for tokens, checkpoints in ((1, False), (64, False), (256, True), (512, True), (4096, True)):
            q = torch.randn(1, tokens, hk, dk, dtype=torch.bfloat16, device="cuda")
            k = torch.randn_like(q)
            v = torch.randn(1, tokens, hv, dv, dtype=torch.bfloat16, device="cuda")
            g = -torch.rand(1, tokens, hv, dtype=torch.float32, device="cuda") * 0.1
            beta = torch.rand_like(g)
            state = torch.zeros(8, hv, dv, dk, dtype=torch.float32, device="cuda")
            indices = torch.tensor([3], device="cuda", dtype=torch.int32)
            starts = torch.tensor([0, tokens], device="cuda", dtype=torch.int32)
            count = tokens // 256 if checkpoints else 0
            checkpoint_args = (
                {"num_state_checkpoints": count, "state_checkpoint_every_n_tokens": 256,
                 "state_checkpoint_cu_starts": torch.tensor([0, count], device="cuda", dtype=torch.int64)}
                if count else {}
            )

            def prefill():
                return kernel.extend(
                    q, k, v, g, beta, ssm_states=state,
                    cache_indices=indices, query_start_loc=starts, **checkpoint_args,
                )

            output, _, snapshots = prefill()
            if not torch.isfinite(output).all() or not torch.isfinite(state).all():
                raise ValueError("nonfinite native GDN output/state")
            if count and (snapshots is None or not torch.isfinite(snapshots).all()):
                raise ValueError("native GDN checkpoints are missing or nonfinite")
            timing = measure(prefill)
            trace = run / f"gdn_prefill_{tokens}_checkpoints{count}.trace.json"
            with torch.profiler.profile(
                activities=[torch.profiler.ProfilerActivity.CPU, torch.profiler.ProfilerActivity.CUDA],
                record_shapes=False, with_stack=False,
            ) as profile:
                for _ in range(3):
                    prefill()
                torch.cuda.synchronize()
            profile.export_chrome_trace(str(trace))
            activities = cuda_activities(profile.events())
            prefills.append({
                "tokens": tokens, "batch_size": 1, "state_checkpoints": count,
                "timing": timing, "profile_calls": 3, "cuda_activities": activities,
                "cuda_activity_us_per_call": sum(row["total_us"] for row in activities) / 3,
                "cpu_operators": [
                    {"name": row.key, "calls": row.count, "self_cpu_us": row.self_cpu_time_total}
                    for row in sorted(
                        profile.key_averages(), key=lambda row: row.self_cpu_time_total, reverse=True,
                    )[:25]
                ],
                "trace": str(trace), "trace_sha256": sha256_file(trace),
                "finite_output_state_and_checkpoints": True,
            })
    return {
        "model_dimensions": dims, "gpu": torch.cuda.get_device_name(),
        "projections": projections, "prefills": prefills, "performance_accepted": False,
        "scope": [
            "Synthetic tensors with actual model dimensions; no model or workflow result.",
            "Projection differences are checked at BF16 tolerances, not asserted bitwise equal.",
            "GDN uses the unmodified native FlashInfer wrapper and mutates real recurrent state.",
            "Finite outputs are a smoke check, not independent GDN numerical qualification.",
            "Stream spans include dispatch gaps and dependencies; profiler CUDA activities are separate.",
            "CUDA activity totals include copies and are component sums, not workflow critical-path time.",
            "Eager component profiling does not establish full-model graph performance or kernel shares.",
        ],
    }


def watch(path):
    from scripts.run_causal_comparison import fresh, load, qualified, reservation
    import fcntl

    plan = load(path)
    run = Path(plan["run_root"])
    state_path = path.with_suffix(".state.json")
    state = {"status": "waiting_comparison", "watcher": reservation.process(os.getpid()),
             "run_root": str(run), "performance_accepted": False}
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            previous_path = Path(plan["after_registration"])
            while True:
                if sha256_file(Path(__file__)) != plan["script_sha256"]:
                    raise ValueError("diagnostic script changed after CPU qualification")
                if sha256_file(previous_path) != plan["after_registration_sha256"]:
                    raise ValueError("predecessor registration changed")
                if sha256_file(Path(plan["cpu_report"])) != plan["cpu_report_sha256"]:
                    raise ValueError("diagnostic CPU evidence changed")
                fresh.check_hold(plan, released=True)
                previous = load(previous_path)
                prior_state = load(previous_path.with_suffix(".state.json"))
                ready = predecessor_ready(previous, prior_state, reservation.alive)
                state.update(checked_at=reservation.now().isoformat(),
                             predecessor_status=prior_state["status"])
                if ready:
                    snapshot = qualified.resources(plan["resource_port"])
                    state.update(status="waiting_idle_gpu", resources=snapshot)
                    if snapshot["ready"]:
                        break
                reservation.write_json(state_path, state)
                time.sleep(10)
            comparison = load(Path(previous["run_root"]) / "causal_comparison.json")
            native = previous["native_baseline"]
            if any(
                row.get("same_request_work_verified") is not True
                or any(row.get(key) != native[key] for key in ("requests", "prompt_tokens", "output_tokens"))
                for row in comparison["results"].values()
            ) or set(comparison["results"]) != set(previous["order"]):
                raise ValueError("predecessor lacks complete equal-work results")
            engine = Path(plan["engine_checkout"])
            tree = subprocess.check_output(
                ["git", "-C", str(engine), "rev-parse", "HEAD^{tree}"], text=True,
            ).strip()
            if tree != plan["engine_tree"] or subprocess.check_output(
                ["git", "-C", str(engine), "status", "--porcelain", "--untracked-files=no"], text=True,
            ).strip():
                raise ValueError("frozen diagnostic engine changed")
            if sha256_file(Path(plan["model_config"])) != plan["model_config_sha256"]:
                raise ValueError("diagnostic model geometry changed")
            fresh.check_hold(plan, released=True)
            if not qualified.resources(plan["resource_port"])["ready"]:
                raise ValueError("GPU ownership changed before diagnostic")
            state.update(status="running_gpu_diagnostic", started_at=reservation.now().isoformat())
            reservation.write_json(state_path, state)
            result = gpu_components(plan)
            result["registration_sha256"] = sha256_file(path)
            reservation.write_json(run / "operator_diagnostic.json", result)
            state.update(status="completed_component_review_pending", finished_at=reservation.now().isoformat())
        except BaseException as error:
            state.update(status="stopped_for_investigation", error=repr(error),
                         finished_at=reservation.now().isoformat())
        finally:
            reservation.write_json(state_path, state)


def register(args):
    from scripts.run_causal_comparison import fresh, load, reservation

    for path in (args.registration, args.registration.with_suffix(".state.json"),
                 args.registration.with_suffix(".log"), args.run_root):
        if path.exists():
            raise FileExistsError("preserve existing diagnostic state")
    previous = load(args.after)
    fresh.check_hold(previous, released=True)
    cases = ET.parse(args.cpu_report).getroot()
    if any(list(cases.iter(tag)) for tag in ("failure", "error", "skipped")) or not list(cases.iter("testcase")):
        raise ValueError("diagnostic CPU qualification is not passing")
    cuda_home, model_config = diagnostic_inputs(previous)
    validate_component_shape(load(model_config))
    environment = {
        **os.environ, "CUDA_VISIBLE_DEVICES": "0",
        "CUDA_HOME": str(cuda_home), "PYTHONHASHSEED": "21",
        "PATH": str(cuda_home / "bin") + os.pathsep + os.environ["PATH"],
        "LD_LIBRARY_PATH": str(cuda_home / "lib64") + os.pathsep + os.environ.get("LD_LIBRARY_PATH", ""),
    }
    plan = {
        "after_registration": str(args.after.resolve()),
        "after_registration_sha256": sha256_file(args.after),
        "script_sha256": sha256_file(Path(__file__)), "engine_checkout": previous["engine_checkout"],
        "engine_tree": previous["engine_tree"], "model_config": str(model_config),
        "model_config_sha256": sha256_file(model_config), "hold_file": previous["hold_file"],
        "run_root": str(args.run_root.resolve()), "resource_port": 18760,
        "cpu_report": str(args.cpu_report.resolve()), "cpu_report_sha256": sha256_file(args.cpu_report),
        "passing_cpu_cases": len(list(cases.iter("testcase"))),
        "cuda_home": str(cuda_home), "performance_accepted": False,
    }
    args.run_root.mkdir(parents=True)
    reservation.write_json(args.registration, plan)
    with args.registration.with_suffix(".log").open("xb") as log:
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--watch", str(args.registration.resolve())],
            cwd=ROOT, env=environment, stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    print(json.dumps({"registration": str(args.registration), "watcher": reservation.process(child.pid),
                      "gpu_started": False, "scope": "bounded component diagnostic after the complete comparison"}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", type=Path)
    parser.add_argument("--after", type=Path)
    parser.add_argument("--registration", type=Path)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--cpu-report", type=Path)
    args = parser.parse_args()
    if args.watch:
        watch(args.watch)
    elif all((args.after, args.registration, args.run_root, args.cpu_report)):
        register(args)
    else:
        parser.error("provide --watch or the complete registration arguments")


if __name__ == "__main__":
    main()
