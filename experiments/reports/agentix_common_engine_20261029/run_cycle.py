"""Qualify common-source Agentix bytes and unforced models before formal enablement."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
PREDECESSOR = ROOT.parent / "ops/tokencake_fragmented_capacity_20261029_r1.registration.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--port", type=int, default=18712)
    args = parser.parse_args()
    frozen = json.loads(args.frozen.read_bytes())
    application, engine = Path(frozen["checkout"]), Path(frozen["engine_checkout"])
    sys.path.insert(0, str(application))
    from beliefkv.experiments.native_engine_variant import AGENTIX_TREE
    from beliefkv.experiments.source_provenance import sha256_file
    from experiments.reports.agentix_radix_sessions_20261028 import queue_components as queue
    from scripts import run_causal_comparison as controls
    from scripts.qualify_agentix_locobench_model import completed_predecessor

    if frozen["engine_tree"] != AGENTIX_TREE or os.getpriority(os.PRIO_PROCESS, os.getpid()) != 0:
        raise ValueError("common Agentix cycle requires its reviewed tree and nice0 supervisor")
    if not 1 <= args.port <= 65533:
        raise ValueError("common Agentix cycle needs three valid distinct ports")
    model_ports = {"roomy": args.port + 1, "pressure": args.port + 2}
    if any(Path(f"/tmp/bkv-causal-{port}.sock").exists() for port in model_ports.values()):
        raise ValueError("preserve old event sockets; choose unused model qualification ports")
    output = args.output_dir.resolve()
    if output.exists():
        raise FileExistsError("preserve every common Agentix validation cycle")
    output.mkdir(parents=True)
    controls.reservation.write_json(output / "operator.json", {
        **frozen, "process": controls.reservation.process(os.getpid()),
        "operator_sha256": sha256_file(Path(__file__)),
    })
    if not completed_predecessor(PREDECESSOR, allow_failed=True):
        raise ValueError("skipped predecessor still owns live processes")
    for prior in (ROOT / "experiments/reports/agentix_locobench_model_20261029").glob(
        "**/owned_process_cleanup.json",
    ):
        cleanup = controls.load(prior)
        if cleanup["remaining_processes"] or any(
            controls.reservation.alive(row) for row in cleanup["identities"]
        ):
            raise ValueError("an earlier model/component attempt still owns live children")
    cpu = output / "cpu"
    with (output / "cpu.log").open("x") as log:
        subprocess.run([
            "nice", "-n19", sys.executable,
            str(application / "experiments/reports/agentix_common_engine_20261029/run_bound_cpu.py"),
            "--output-dir", str(cpu), "--native-patch",
            str(application / "patches/sglang-v0.5.20-beliefkv-staging.patch"),
            "--prior", str(ROOT / "experiments/reports/agentix_locobench_model_20261029/logprob_prefix_cycle_r1/cpu/native_consumer_cpu.xml"),
            "--prior", str(ROOT / "experiments/reports/kvflow_radix_session_20261028/causal_entry_bound_cpu.xml"),
        ], cwd=application, check=True, stdout=log, stderr=subprocess.STDOUT, env={
            **os.environ, "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21",
            "SGLANG_SOURCE_CHECKOUT": str(engine), "PYTHONPATH": str(engine / "python"),
        })
    evidence = controls.load(cpu / "common_engine_cpu.json")
    gpu = output / "components"
    gpu.mkdir()
    previous = controls.load(PREDECESSOR)
    registration = gpu / "registration.json"
    controls.reservation.write_json(registration, {
        **frozen, "output_dir": str(gpu), "predecessor": str(PREDECESSOR),
        "hold_file": previous["hold_file"], "port": args.port,
        "reference_environment": previous["reference_environment"],
        "cpu_manifest": evidence["source_manifest"]["path"],
        "failed_predecessor_skipped_by_user": True,
        "inputs_sha256": {
            str(path): sha256_file(path) for path in (
                cpu / "common_engine_cpu.json", Path(evidence["source_manifest"]["path"]),
                Path(evidence["report"]["path"]), PREDECESSOR,
            )
        },
    })
    queue.watch(registration)
    if controls.load(gpu / "summary.json")["status"] != "four_native_consumer_gpu_checks_qualified":
        raise ValueError("fresh common-engine physical components failed")
    for fixture, port in model_ports.items():
        with (output / (fixture + ".log")).open("x") as log:
            subprocess.run([
                sys.executable, str(application / "scripts/qualify_agentix_locobench_model.py"),
                "--predecessor", str(PREDECESSOR), "--application", str(application),
                "--engine", str(engine), "--components", str(gpu / "summary.json"),
                "--cpu-evidence", str(cpu / "common_engine_cpu.json"),
                "--output-dir", str(output / fixture), "--fixture", fixture,
                "--port", str(port), "--allow-failed-predecessor",
            ], cwd=application, check=True, stdout=log, stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONHASHSEED": "21"})
    controls.reservation.write_json(output / "summary.json", {
        "status": "common_engine_physical_and_unforced_models_qualified",
        "frozen": frozen, "cpu_evidence": str(cpu / "common_engine_cpu.json"),
        "components": str(gpu / "summary.json"),
        "models": {fixture: str(output / fixture / "summary.json") for fixture in ("roomy", "pressure")},
        "formal_agentix_enabled": False, "performance_accepted": False,
    })


if __name__ == "__main__":
    main()
