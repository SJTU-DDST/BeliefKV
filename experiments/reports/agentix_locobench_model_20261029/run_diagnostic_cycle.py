"""Run frozen-source CPU, physical GPU, then actual model checks sequentially."""

import json
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PREDECESSOR = ROOT.parent / "ops/tokencake_fragmented_capacity_20261029_r1.registration.json"


def main():
    frozen = json.loads((HERE / "lifecycle_diagnostic_frozen_r2.json").read_bytes())
    application, engine = Path(frozen["checkout"]), Path(frozen["engine_checkout"])
    sys.path.insert(0, str(application))
    from beliefkv.experiments.source_provenance import sha256_file
    from experiments.reports.agentix_radix_sessions_20261028 import queue_components as queue
    from scripts import run_causal_comparison as controls
    from scripts.qualify_agentix_locobench_model import completed_predecessor

    run = HERE / "diagnostic_cycle_r1"
    if run.exists():
        raise FileExistsError("preserve every prior validation cycle")
    run.mkdir()
    controls.reservation.write_json(run / "operator.json", {
        "process": controls.reservation.process(os.getpid()),
        "source_sha256": sha256_file(Path(__file__)), **frozen,
    })
    if os.getpriority(os.PRIO_PROCESS, os.getpid()) != 0:
        raise ValueError("GPU cycle supervisor must use nice0")
    if not completed_predecessor(PREDECESSOR, allow_failed=True):
        raise ValueError("skipped predecessor still owns live processes")
    for old in HERE.glob("roomy_r*/owned_process_cleanup.json"):
        cleanup = controls.load(old)
        if cleanup["remaining_processes"] or any(
            controls.reservation.alive(row) for row in cleanup["identities"]
        ):
            raise ValueError("an earlier actual model attempt has not exited")
    cpu = run / "cpu"
    command = [
        "nice", "-n19", sys.executable,
        str(application / "experiments/reports/agentix_radix_sessions_20261028/run_native_consumer_cpu.py"),
        "--output-dir", str(cpu), "--prior",
        str(ROOT / "experiments/reports/agentix_radix_sessions_20261028/bound_cpu.xml"),
        "--native-patch", str(application / "patches/sglang-v0.5.20-beliefkv-staging.patch"),
    ]
    env = {
        **os.environ, "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21",
        "SGLANG_SOURCE_CHECKOUT": str(engine), "PYTHONPATH": str(engine / "python"),
    }
    with (run / "cpu.log").open("x") as log:
        subprocess.run(command, cwd=application, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
    evidence = controls.load(cpu / "native_consumer_cpu.json")
    if (
        evidence["totals"]["tests"] < 762 or not evidence["all_prior_752_identities_retained"]
        or any(evidence["totals"][name] for name in ("errors", "failures", "skipped"))
    ):
        raise ValueError("frozen native CPU checks failed")
    gpu = run / "components"
    gpu.mkdir()
    previous = controls.load(PREDECESSOR)
    plan = {
        **frozen, "output_dir": str(gpu), "predecessor": str(PREDECESSOR),
        "hold_file": previous["hold_file"], "port": 18710,
        "reference_environment": previous["reference_environment"],
        "cpu_manifest": evidence["source_manifest"]["path"],
        "failed_predecessor_skipped_by_user": True,
        "inputs_sha256": {
            str(path): sha256_file(path) for path in (
                cpu / "native_consumer_cpu.json", Path(evidence["source_manifest"]["path"]),
                Path(evidence["report"]["path"]), PREDECESSOR,
            )
        },
    }
    registration = gpu / "registration.json"
    controls.reservation.write_json(registration, plan)
    queue.watch(registration)
    if controls.load(gpu / "summary.json")["status"] != "four_native_consumer_gpu_checks_qualified":
        raise ValueError("physical GPU checks failed")
    with (run / "model.log").open("x") as log:
        subprocess.run([
            sys.executable, str(application / "scripts/qualify_agentix_locobench_model.py"),
            "--predecessor", str(PREDECESSOR), "--application", str(application),
            "--engine", str(engine), "--components", str(gpu / "summary.json"),
            "--cpu-evidence", str(cpu / "native_consumer_cpu.json"),
            "--output-dir", str(run / "model"), "--fixture", "roomy", "--port", "18710",
            "--allow-failed-predecessor",
        ], cwd=application, env={**os.environ, "PYTHONHASHSEED": "21"},
            stdout=log, stderr=subprocess.STDOUT, check=True)


if __name__ == "__main__":
    main()
