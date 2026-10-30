"""Register native-only training collection from a clean committed checkout."""

import argparse
from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
MAIN = Path("/home/longhao/experiment/BeliefKV")
OPS = MAIN.parent / "ops"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.native_engine_variant import engine_patch
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from experiments.reports.arrival_mean120_20261029.queue_pair import validate_cpu
from experiments.reports.prefill2048_mean120_20261029.queue_pair import validate_native_rebinding
from scripts import run_causal_source_capture as capture
from scripts.qualify_free_page_h2d_fence import check_reservation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpu-evidence", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workload", type=Path, default=ROOT / "configs/workloads/locobench_workflow_work_train8_20261029.json")
    parser.add_argument("--configuration", type=Path, default=ROOT / "configs/migration/workflow_work_train8_native_20261029.json")
    parser.add_argument("--registration", type=Path, default=OPS / "workflow_work_train8_source_20261029_r1.registration.json")
    parser.add_argument("--run-root", type=Path, default=MAIN / "experiments/raw/workflow_work_train8_source_20261029_r1")
    parser.add_argument("--port", type=int, default=18891)
    parser.add_argument("--purpose", default="predictor_training_not_performance_baseline")
    args = parser.parse_args()
    if git_source_state(ROOT)["dirty"] or os.getpriority(os.PRIO_PROCESS, os.getpid()) != 0:
        raise ValueError("native training capture requires clean committed sources and nice0")
    check_reservation()
    cpu = validate_cpu(args.cpu_evidence)
    previous = capture.load(OPS / "prefill2048_mean120_thunder_20261029_r1.registration.json")
    engine = Path(previous["engine_checkout"])
    original_sources = Path(previous["engine_evidence"]["sources"])
    qualified = capture.load(original_sources)
    current = git_source_state(
        engine, canonical_patch=engine_patch(ROOT, qualified["canonical_patch"]["actual_tree"]),
    )
    validate_native_rebinding(current, qualified)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    native_sources = args.output_dir / "native_sources.json"
    capture.reservation.write_json(native_sources, current)
    proof = capture.engine_evidence(
        engine, Path(previous["engine_evidence"]["report"]), native_sources,
    )
    capture.reservation.write_json(args.output_dir / "native_binding.json", {
        "original_sources": str(original_sources), "original_sha256": sha256_file(original_sources),
        "current_sources": str(native_sources), "evidence": proof,
        "native_bytes_and_patch_unchanged": True,
        "only_rebound_fields": ["canonical_patch.path", "state_sha256"],
    })
    command = [
        sys.executable, str(ROOT / "scripts/run_causal_source_capture.py"),
        "--reference", str(OPS / "v128_eos_seeded_modules_resume_20261022.registration.json"),
        "--cpu-report", cpu["report"]["path"],
        "--cpu-sources", cpu["source_manifest"]["path"],
        "--engine", str(engine), "--engine-report", proof["report"],
        "--engine-sources", str(native_sources),
        "--registration", str(args.registration),
        "--run-root", str(args.run_root),
        "--port", str(args.port), "--source-profile", "nested_oneshot",
        "--source-workload", str(args.workload),
        "--max-running-requests", "24", "--sampling-profile", "qwen3_coder",
        "--experiment-config", str(args.configuration),
        "--cuda-home", "/home/longhao/experiment/toolchains/cuda130_sampling_20261024/nvidia/cu13",
        "--wait-for-gpu",
    ]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=180.)
    capture.reservation.write_json(args.output_dir / "entry.json", {
        "command": command, "returncode": completed.returncode,
        "stdout": completed.stdout, "stderr": completed.stderr,
        "purpose": args.purpose,
    })
    print(completed.stdout, end="")
    if completed.returncode:
        raise RuntimeError("native training registration refused; retain entry.json")


if __name__ == "__main__":
    main()
