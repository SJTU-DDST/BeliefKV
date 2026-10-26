#!/usr/bin/env python3
"""Bounded real-CUDA qualification of Agentix executor accounting, not an LLM run."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import os
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.agentix import AgentixConfig, AgentixScheduler
from beliefkv.runtime.agentix_executor import AgentixExecutor


def gpu_processes():
    result = subprocess.run([
        "nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits",
    ], check=True, capture_output=True, text=True, timeout=10.)
    return [int(value) for value in result.stdout.splitlines() if value.strip()]


def qualify():
    if gpu_processes():
        raise ValueError("GPU is occupied before the bounded executor-interface check")
    import torch

    if torch.cuda.device_count() != 1:
        raise ValueError("executor-interface qualification requires one explicit visible GPU")
    clock = lambda: time.monotonic() * 1000.
    # Fixture parameters exercise the accounting API, not paper performance.
    config = AgentixConfig("atlas", (10., 30.), (4., 8., 16.), 1000., 4)
    policy, stream = AgentixScheduler(config), torch.cuda.Stream()
    for request in ("a", "b"):
        policy.arrive(request, "workflow", now_ms=clock())
    policy.running(["a", "b"], now_ms=clock())
    executor = AgentixExecutor(policy)
    launches, outputs = [], []
    with torch.cuda.stream(stream):
        left = torch.ones((2048, 2048), device="cuda", dtype=torch.bfloat16)
        right = torch.ones_like(left)
        for membership in (("a",), ("a", "b"), ("b",)):
            token = executor.begin(
                membership, decode=True, device=torch.cuda, stream=stream, now_ms=clock(),
            )
            outputs.append(torch.mm(left, right))
            executor.end(token)
            launches.append(token)
    deadline = time.monotonic() + 30.
    while executor.pending:
        executor.poll()
        if time.monotonic() >= deadline:
            raise TimeoutError("bounded executor-interface check did not finish")
        if executor.pending:
            time.sleep(.001)
    executor.assert_drained()
    durations = [token.start.elapsed_time(token.finish) for token in launches]
    measured_a, measured_b = (
        policy.calls[request].total_model_ms for request in ("a", "b")
    )
    if (
        not all(value > 0 for value in durations)
        or abs(measured_a - sum(durations[:2])) > 1e-5
        or abs(measured_b - sum(durations[1:])) > 1e-5
        or abs(executor.total_executor_ms - sum(durations)) > 1e-5
        or os.getpid() not in gpu_processes()
    ):
        raise ValueError("actual GPU execution or request-bound interval accounting differs")
    # Reading a result occurs after event accounting drained, outside its path.
    if any(output[0, 0].item() != 2048. for output in outputs):
        raise ValueError("GPU fixture did not produce its expected matrix results")
    inherited = max(measured_a, measured_b)
    policy.complete("a", now_ms=clock())
    policy.complete("b", now_ms=clock())
    if abs(policy.service.service_ms["workflow"] - inherited) > 1e-5:
        raise ValueError("observed GPU intervals do not reach ATLAS program inheritance")
    return {
        "status": "executor_cuda_interface_qualified",
        "scope": "Three actual matrix forwards with changing request membership; not an LLM or policy performance experiment.",
        "gpu": torch.cuda.get_device_name(), "owned_gpu_pid": os.getpid(),
        "torch": torch.__version__, "cuda": torch.version.cuda,
        "fixture_only_config": asdict(config),
        "launch_memberships": [token.request_ids for token in launches],
        "measured_forward_ms": durations,
        "request_executor_ms": {"a": measured_a, "b": measured_b},
        "atlas_program_service_ms": policy.service.service_ms["workflow"],
        "executor": executor.status(),
        "actual_output_values_verified": True,
        "accounting_drained_before_result_reads": True,
        "native_llm_executor_hook_installed": False,
        "physical_preemption_or_swap_qualified": False,
        "performance_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    try:
        result = qualify()
    except BaseException as error:
        result = {
            "status": "failed", "error_type": type(error).__name__, "error": str(error),
            "performance_accepted": False,
        }
        raise
    finally:
        result["elapsed_seconds"] = time.monotonic() - started
        result["source_sha256"] = {
            name: sha256_file(ROOT / name) for name in (
                "scripts/qualify_agentix_executor.py",
                "beliefkv/policy/agentix.py", "beliefkv/runtime/agentix_executor.py",
                "beliefkv/policy/program_service.py",
            )
        }
        with (args.output_dir / "summary.json").open("x") as output:
            json.dump(result, output, indent=2, allow_nan=False)
            output.write("\n")
        print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
