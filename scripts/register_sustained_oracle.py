"""Start an explicitly qualified oracle against its accepted sustained Full."""

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MAIN = Path("/home/longhao/experiment/BeliefKV")
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.native_engine_variant import FREE_EXTENT_TREE, engine_patch
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from beliefkv.runtime.workflow_work_oracle import WorkflowWorkOracleArtifact
from experiments.reports.arrival_mean120_20261029.queue_pair import validate_cpu
from experiments.reports.workflow_mix_20261029.prepare_burst_oracle import RANK_FIELDS
from experiments.reports.workflow_mix_20261029.review_burst import complete_work
from scripts import run_causal_comparison as causal
from scripts import run_causal_source_capture as capture
from scripts.prepare_sustained_oracle_admission import validate_decision
from scripts.qualify_free_page_h2d_fence import check_reservation
from scripts.run_causal_gpu_replay import source_plan


def matching_conditions(original, candidate):
    allowed = RANK_FIELDS | {
        "WORKFLOW_COMBINATION_ADMISSION", "BELIEFKV_WORKFLOW_COMBINATION_ADMISSION",
    }
    old, new = original["common_environment"], candidate["common_environment"]
    if (
        original["order"] != ["full"] or candidate["order"] != ["full"]
        or any(original[name] != candidate[name] for name in (
            "source_registration", "tool_timing_overlay", "workflow_arrivals",
        ))
        or original.get("stream_response_metrics") != candidate.get("stream_response_metrics")
        or old.keys() - new.keys()
        or new.keys() - old.keys() - allowed
        or any(old[name] != new[name] for name in old.keys() - allowed)
        or old["WORKFLOW_SIZE_RESUME"] != "0" or new["WORKFLOW_SIZE_RESUME"] != "1"
        or new["WORKFLOW_WORK_SOURCE"] != "oracle"
        or new["WORKFLOW_COMBINATION_ADMISSION"] not in ("0", "1")
        or old["AGENT_CONTROL_INTERVAL_MS"] != "2000"
        or old["AGENT_FREE_DISPATCH_INTERVAL_MS"] != "500"
    ):
        raise ValueError("oracle changed fixed workload, serving or matched control cadence")
    if any(new.get(name) != new.get("BELIEFKV_" + name) for name in (
        "WORKFLOW_SIZE_RESUME", "WORKFLOW_WORK_SOURCE", "WORKFLOW_WORK_MODEL",
        "WORKFLOW_WORK_MODEL_SHA256", "WORKFLOW_COMBINATION_ADMISSION",
    )):
        raise ValueError("oracle policy aliases disagree")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "cpu-evidence", "control", "configuration", "progress-evidence", "entry-dir",
        "position-registration", "comparison-registration", "position-root", "comparison-root",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    if git_source_state(ROOT)["dirty"] or os.getpriority(os.PRIO_PROCESS, os.getpid()):
        raise ValueError("freeze clean qualified sources and retain a nice0 GPU supervisor")
    cpu = validate_cpu(args.cpu_evidence)
    if complete_work(args.control) is None:
        raise ValueError("wait for all matched control work, drain and actual owned exit")
    control, candidate = causal.load(args.control), causal.load(args.configuration)
    causal.comparison.verify_sources(control)
    original = causal.load(Path(control["experiment_configuration"]))
    matching_conditions(original, candidate)
    provenance = candidate["candidate_provenance"]
    review_path, decision_path = (Path(provenance[name]) for name in ("control_review", "workload_review"))
    review, decision = causal.load(review_path), causal.load(decision_path)
    if (
        provenance["control_registration_sha256"] != sha256_file(args.control)
        or provenance["control_review_sha256"] != sha256_file(review_path)
        or provenance["workload_review_sha256"] != sha256_file(decision_path)
        or review["registration_sha256"] != sha256_file(args.control)
        or review["complete_work_and_actual_exit_verified"] is not True
    ):
        raise ValueError("oracle preparation belongs to another complete control or workload decision")
    validate_decision(decision, [review_path])
    common = candidate["common_environment"]
    artifact_path = Path(common["WORKFLOW_WORK_MODEL"])
    artifact = WorkflowWorkOracleArtifact.load(artifact_path, common["WORKFLOW_WORK_MODEL_SHA256"])
    combination = common["WORKFLOW_COMBINATION_ADMISSION"] == "1"
    if artifact.raw["admission_order"] != (
        "within_reference_category" if combination else "global_ready_remaining_path"
    ):
        raise ValueError("declare pure remaining-path ranking separately from R5 combination scoring")
    graph, _expected, evidence = source_plan(Path(control["source_arm"]))
    artifact.validate_source(Path(control["source_arm"]), graph, evidence)
    progress = causal.load(args.progress_evidence)
    if (
        progress["artifact_sha256"] != common["WORKFLOW_WORK_MODEL_SHA256"]
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
        raise ValueError("complete bound oracle progress is unqualified")
    authorization = check_reservation()
    resources = causal.qualified.resources(args.port)
    if not resources["ready"] or Path(f"/tmp/bkv-causal-{args.port}.sock").exists():
        raise ValueError("GPU, Host, disk, port or event socket is occupied")
    args.entry_dir.mkdir(parents=True, exist_ok=False)
    engine = MAIN.parent / ".worktrees/sglang-free-extent-registration-20261029"
    native = git_source_state(engine, canonical_patch=engine_patch(ROOT, FREE_EXTENT_TREE))
    native_path = args.entry_dir / "native_sources.json"
    capture.reservation.write_json(native_path, native)
    native_evidence = capture.engine_evidence(
        engine, MAIN / "experiments/reports/layer_free_extent_20261029/native_cpu_r1/native_cpu.xml",
        native_path,
    )
    command = [
        sys.executable, str(ROOT / "scripts/run_position_replay_calibration.py"),
        "--source-registration", candidate["source_registration"],
        "--reference", str(MAIN.parent / "ops/v128_eos_seeded_modules_resume_20261022.registration.json"),
        "--cpu-report", cpu["report"]["path"], "--cpu-sources", cpu["source_manifest"]["path"],
        "--engine", str(engine), "--engine-report", native_evidence["report"],
        "--engine-sources", str(native_path.resolve()),
        "--registration", str(args.position_registration.resolve()), "--run-root", str(args.position_root.resolve()),
        "--comparison-registration", str(args.comparison_registration.resolve()),
        "--comparison-root", str(args.comparison_root.resolve()), "--port", str(args.port),
        "--experiment-config", str(args.configuration.resolve()),
    ]
    inputs = (args.cpu_evidence, args.control, args.configuration, args.progress_evidence,
              review_path, decision_path, artifact_path)
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    capture.reservation.write_json(args.entry_dir / "entry.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(), "application": git_source_state(ROOT),
        "authorization": authorization, "resources": resources, "command": command,
        "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in inputs},
        "operator_sha256": sha256_file(Path(__file__)),
        "position_registration": str(args.position_registration.resolve()),
        "comparison_registration": str(args.comparison_registration.resolve()),
        "future_information_used": True, "deployable": False,
        "other_baseline_registered": False, "performance_accepted": False,
    })
    print(result.stdout, end="")
    if result.returncode:
        print(result.stderr, end="", file=sys.stderr)
        raise RuntimeError("preserve failed oracle entry; never resume a partial comparison")


if __name__ == "__main__":
    main()
