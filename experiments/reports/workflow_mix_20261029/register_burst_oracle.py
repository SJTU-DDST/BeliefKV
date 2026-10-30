"""Qualify and start the cached oracle after this workload's completed control."""

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
MAIN = Path("/home/longhao/experiment/BeliefKV")
OPS = MAIN.parent / "ops"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.native_engine_variant import FREE_EXTENT_TREE, engine_patch
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from beliefkv.runtime.workflow_work_oracle import WorkflowWorkOracleArtifact
from experiments.reports.arrival_mean120_20261029.queue_pair import validate_cpu
from experiments.reports.workflow_mix_20261029.prepare_burst_oracle import (
    RANK_FIELDS, validate_control,
)
from experiments.reports.workflow_mix_20261029.review_burst import complete_work
from scripts import run_causal_comparison as causal
from scripts import run_causal_source_capture as capture
from scripts.qualify_free_page_h2d_fence import check_reservation
from scripts.run_causal_gpu_replay import source_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpu-evidence", type=Path, required=True)
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument("--configuration", type=Path, required=True)
    parser.add_argument("--progress-evidence", type=Path, required=True)
    args = parser.parse_args()
    application = git_source_state(ROOT)
    if application["dirty"] or os.getpriority(os.PRIO_PROCESS, os.getpid()):
        raise ValueError("freeze clean sources and keep GPU supervisors nice0")
    cpu = validate_cpu(args.cpu_evidence)
    if complete_work(args.control) is None:
        raise ValueError("control work, drain and owned exit are incomplete")
    control = causal.load(args.control)
    causal.comparison.verify_sources(control)
    specification = causal.load(args.configuration)
    common = specification["common_environment"]
    original = causal.load(Path(control["experiment_configuration"]))
    provenance = specification["candidate_provenance"]
    review_path = Path(provenance["control_review"])
    validate_control(original, control, causal.load(review_path))
    if (
        provenance["control_registration_sha256"] != sha256_file(args.control)
        or provenance["control_review_sha256"] != sha256_file(review_path)
        or specification["source_registration"] != original["source_registration"]
        or specification["tool_timing_overlay"] != original["tool_timing_overlay"]
        or specification["workflow_arrivals"] != original["workflow_arrivals"]
        or common.keys() != original["common_environment"].keys()
        or any(
            value != original["common_environment"][name] for name, value in common.items()
            if name not in RANK_FIELDS
        )
    ):
        raise ValueError("oracle changed a fixed workload or non-ranking serving policy")
    oracle_path = Path(common["WORKFLOW_WORK_MODEL"])
    oracle = WorkflowWorkOracleArtifact.load(oracle_path, common["WORKFLOW_WORK_MODEL_SHA256"])
    graph, _expected, source_evidence = source_plan(Path(control["source_arm"]))
    oracle.validate_source(Path(control["source_arm"]), graph, source_evidence)
    progress = causal.load(args.progress_evidence)
    if (
        common["WORKFLOW_WORK_SOURCE"] != "oracle" or common["WORKFLOW_SIZE_RESUME"] != "1"
        or progress["artifact_sha256"] != common["WORKFLOW_WORK_MODEL_SHA256"]
        or progress["configuration_sha256"] != sha256_file(args.configuration)
        or progress["control_registration_sha256"] != sha256_file(args.control)
        or progress["complete_work_conserved"] is not True
        or progress["counts"]["completed_requests"] != control["source_plan"]["requests"]
        or progress["counts"]["closed_workflows"] != len(control["workflow_ids"])
        or progress["counts"].get("unmodeled_core_event_observations", 0)
        or progress["operator_sha256"] != sha256_file(
            ROOT / "experiments/reports/workflow_mix_20261029/qualify_burst_oracle.py"
        )
    ):
        raise ValueError("new-source oracle progress has not passed CPU qualification")
    authorization = check_reservation()
    port = 18905
    if not causal.qualified.resources(port)["ready"] or Path(f"/tmp/bkv-causal-{port}.sock").exists():
        raise ValueError("GPU, Host, disk, port or event socket is occupied")
    native = Path("/home/longhao/experiment/.worktrees/sglang-free-extent-registration-20261029")
    native_state = git_source_state(native, canonical_patch=engine_patch(ROOT, FREE_EXTENT_TREE))
    if native_state["canonical_patch"]["actual_tree"] != FREE_EXTENT_TREE:
        raise ValueError("oracle changed the retained native data plane")
    directory = MAIN / "experiments/reports/workflow_mix_20261029/burst_oracle_entry_r1"
    directory.mkdir(parents=True, exist_ok=False)
    native_sources = directory / "native_sources.json"
    capture.reservation.write_json(native_sources, native_state)
    proof = capture.engine_evidence(
        native, MAIN / "experiments/reports/layer_free_extent_20261029/native_cpu_r1/native_cpu.xml",
        native_sources,
    )
    position = OPS / "workflow_mix_burst_oracle_position_20261030_r1.registration.json"
    formal = OPS / "workflow_mix_burst_oracle_20261030_r1.registration.json"
    command = [
        sys.executable, str(ROOT / "scripts/run_position_replay_calibration.py"),
        "--source-registration", specification["source_registration"],
        "--reference", str(OPS / "v128_eos_seeded_modules_resume_20261022.registration.json"),
        "--cpu-report", cpu["report"]["path"], "--cpu-sources", cpu["source_manifest"]["path"],
        "--engine", str(native), "--engine-report", proof["report"],
        "--engine-sources", str(native_sources), "--registration", str(position),
        "--run-root", str(MAIN / "experiments/raw/workflow_mix_burst_oracle_position_20261030_r1"),
        "--comparison-registration", str(formal),
        "--comparison-root", str(MAIN / "experiments/raw/workflow_mix_burst_oracle_20261030_r1"),
        "--port", str(port), "--experiment-config", str(args.configuration.resolve()),
    ]
    inputs = (
        args.cpu_evidence, args.control, args.configuration, args.progress_evidence, oracle_path, review_path,
    )
    pins = {str(path.resolve()): sha256_file(path) for path in inputs}
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    capture.reservation.write_json(directory / "entry.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(), "application": application,
        "authorization": authorization, "command": command, "returncode": result.returncode,
        "stdout": result.stdout, "stderr": result.stderr, "inputs_sha256": pins,
        "operator_sha256": sha256_file(Path(__file__)),
        "position_registration": str(position), "comparison_registration": str(formal),
        "future_information_used": True, "deployable": False,
        "no_other_baseline_registered": True, "performance_accepted": False,
    })
    print(result.stdout, end="")
    if result.returncode:
        print(result.stderr, end="", file=sys.stderr)
        raise RuntimeError("preserve failed new-workload oracle entry")


if __name__ == "__main__":
    main()
