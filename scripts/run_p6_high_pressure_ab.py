#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PLAN = ROOT / "configs/p6/predictive_joint_h200_high_pressure_v1/ab_plan.json"


def _path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _gpu_processes(gpu: int) -> tuple[str, ...]:
    result = subprocess.run(
        [
            "nvidia-smi",
            f"--id={gpu}",
            "--query-compute-apps=pid,used_memory,process_name",
            "--format=csv,noheader,nounits",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return tuple(line.strip() for line in result.stdout.splitlines() if line.strip())


def _wait_server(base_url: str, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 20 * 60
    last_error = "server unavailable"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"SGLang exited during startup: {process.returncode}")
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=5) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError) as error:
            last_error = str(error)
        time.sleep(5)
    raise TimeoutError(last_error)


def _prepare_server(
    plan: dict[str, object], arm: str, server_dir: Path
) -> dict[str, object]:
    artifacts = plan["artifacts"]
    profile = json.loads(_path(str(plan["runtime_profile"])).read_text())
    transfer = artifacts["transfer_service"]
    gpu_service = artifacts["gpu_service"]
    command = [
        sys.executable,
        str(ROOT / "scripts/prepare_deepagents_server_config.py"),
        "--server-dir",
        str(server_dir),
        "--performance-mode",
        "--queue-service-observer",
        "--enable-observed-admission",
        "--enable-online-joint",
        "--enable-running-retraction",
        "--subagent-fanout-profile",
        str(plan["workload"]["fanout_profile"]),
        "--transfer-service-model",
        str(_path(str(transfer["path"]))),
        "--transfer-service-hardware-key",
        str(transfer["hardware_key"]),
        "--gpu-service-model",
        str(_path(str(gpu_service["path"]))),
        "--gpu-service-hardware-key",
        str(gpu_service["hardware_key"]),
    ]
    if arm == "predictive":
        predictor = artifacts["predictor"]
        command.extend(
            [
                "--predictor-model",
                str(_path(str(predictor["path"]))),
                "--enable-predictive-risk-shadow",
                "--enable-predictive-joint-overlay",
                "--enable-shadow-transfers",
                "--predictive-prepare-canary-limit",
                str(plan["predictive_prepare_limit"]),
            ]
        )
    subprocess.run(command, cwd=ROOT, check=True)
    config = json.loads((server_dir / "beliefkv_config.json").read_text())
    return {"profile": profile, "config": config, "prepare_command": command}


def _run_arm(
    plan: dict[str, object], arm: str, output: Path, gpu: int, port: int
) -> int:
    if output.exists():
        raise FileExistsError(f"run directory already exists: {output}")
    processes = _gpu_processes(gpu)
    if processes:
        raise RuntimeError(f"GPU {gpu} is occupied: {processes}")
    output.mkdir(parents=True)
    server_dir = output / "server"
    workload_dir = output / "workloads"
    prepared = _prepare_server(plan, arm, server_dir)
    profile = prepared["profile"]
    config = prepared["config"]
    environment = {
        **os.environ,
        "CUDA_VISIBLE_DEVICES": str(gpu),
        "PORT": str(port),
        "SLEEP_ON_IDLE": "1",
    }
    base_url = f"http://127.0.0.1:{port}"
    started_at = datetime.now(timezone.utc).isoformat()
    server = subprocess.Popen(
        [
            str(ROOT / "scripts/launch_deepagents_swebench_server.sh"),
            "--runtime-profile",
            str(_path(str(plan["runtime_profile"]))),
            str(server_dir),
        ],
        cwd=ROOT,
        env=environment,
        start_new_session=True,
    )
    return_code: int | None = None
    error: str | None = None
    try:
        _wait_server(base_url, server)
        workload = plan["workload"]
        command = [
            str(_path(str(plan["agent_python"]))),
            str(ROOT / "scripts/run_deepagents_swebench.py"),
            "--mode",
            "autonomous",
            "--base-url",
            f"{base_url}/v1",
            "--model",
            str(profile["model"]["served_name"]),
            "--workload-manifest",
            str(_path(str(workload["manifest"]))),
            "--control-socket",
            str(config["runtime_event_socket_path"]),
            "--server-audit",
            str(server_dir / "runtime_audit.jsonl"),
            "--server-events",
            str(server_dir / "runtime_events.sglang.jsonl"),
            "--server-log",
            str(server_dir / "server.log"),
            "--max-workflows",
            str(workload["roots"]),
            "--concurrency",
            str(workload["roots"]),
            "--saturated-root-backlog",
            "--subagent-fanout-profile",
            str(workload["fanout_profile"]),
            "--gpu",
            str(gpu),
            "--pool-tokens",
            str(profile["capacity"]["max_total_tokens"]),
            "--max-completion-tokens",
            str(workload["max_completion_tokens"]),
            "--sampling-seed",
            str(workload["sampling_seed"]),
            "--context-window-tokens",
            str(profile["model"]["context_length"]),
            "--context-keep-tokens",
            str(workload["context_keep_tokens"]),
            "--summary-output-tokens",
            str(workload["summary_output_tokens"]),
            "--recursion-limit",
            "512",
            "--disable-activation-deadline",
            "--request-timeout",
            "7200",
            "--sandbox-preflight-command",
            "",
            "--disable-completion-gate",
            "--completion-repair-attempts",
            "0",
            "--gate",
            "system",
            "--output",
            str(workload_dir),
        ]
        _write_json(
            output / "run_contract.json",
            {
                "schema_version": 1,
                "arm": arm,
                "started_at": started_at,
                "runtime_profile": str(_path(str(plan["runtime_profile"]))),
                "workload_manifest": str(_path(str(workload["manifest"]))),
                "activation_deadline_seconds": plan["activation_deadline_seconds"],
                "prepare_command": prepared["prepare_command"],
                "workload_command": command,
            },
        )
        return_code = subprocess.run(command, cwd=ROOT, env=environment).returncode
    except BaseException as exception:
        error = f"{type(exception).__name__}: {exception}"
    finally:
        subprocess.run(
            [
                str(ROOT / "scripts/stop_deepagents_swebench_server.sh"),
                str(server_dir),
            ],
            cwd=ROOT,
            env=environment,
            check=False,
        )
    _write_json(
        output / "run_result.json",
        {
            "schema_version": 1,
            "arm": arm,
            "started_at": started_at,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "workload_return_code": return_code,
            "error": error,
        },
    )
    return 0 if return_code == 0 and error is None else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the H200 high-pressure P6 A/B.")
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--arm", choices=("baseline", "predictive", "both"), default="both")
    parser.add_argument("--gpu", type=int, default=0)
    parser.add_argument("--port", type=int, default=18000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if Path("/tmp/beliefkv-experiments.paused").exists():
        raise RuntimeError("BeliefKV experiments are paused")
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    if plan.get("frozen") is not True:
        raise ValueError("high-pressure A/B plan must be frozen")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = args.output or ROOT / "experiments/ab/p6_h200_high_pressure_v1" / stamp
    arms = ("baseline", "predictive") if args.arm == "both" else (args.arm,)
    result = 0
    for arm in arms:
        arm_result = _run_arm(plan, arm, output / arm, args.gpu, args.port)
        result |= arm_result
        if arm_result:
            break
        if arm != arms[-1]:
            time.sleep(10)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
