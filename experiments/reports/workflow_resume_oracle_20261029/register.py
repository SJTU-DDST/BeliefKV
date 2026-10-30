"""Qualify and execute one declared model/tool oracle; reuse complete controls."""

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
from experiments.reports.arrival_mean120_20261029.queue_pair import complete_work, validate_cpu
from scripts import run_causal_comparison as causal
from scripts import run_causal_source_capture as capture
from scripts.qualify_free_page_h2d_fence import check_reservation
from scripts.run_causal_gpu_replay import source_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpu-evidence", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18902)
    parser.add_argument("--configuration", type=Path, required=True)
    parser.add_argument("--progress-evidence", type=Path, required=True)
    args = parser.parse_args()
    application = git_source_state(ROOT)
    if application["dirty"] or os.getpriority(os.PRIO_PROCESS, os.getpid()) != 0:
        raise ValueError("oracle qualification requires clean frozen sources and nice0")
    cpu = validate_cpu(args.cpu_evidence)
    directory = MAIN / "experiments/reports/workflow_resume_oracle_20261029/gpu_entry_r1"
    directory.mkdir(parents=True, exist_ok=False)
    controls = [
        OPS / "prefill2048_mean120_thunder_20261029_r1.registration.json",
        OPS / "decay_tick2s_no_handoff_20261030_r1.registration.json",
        OPS / "workflow_dispatch_20261029_r1.registration.json",
    ]
    baseline = complete_work(controls[0])
    if baseline is None or any(complete_work(path) != baseline for path in controls[1:]):
        raise ValueError("complete retained controls differ or still own live processes")
    for path in controls:
        causal.comparison.verify_sources(causal.load(path))
    spec_path = args.configuration.resolve()
    specification = causal.load(spec_path)
    common = specification["common_environment"]
    oracle_path = Path(common["WORKFLOW_WORK_MODEL"])
    oracle = WorkflowWorkOracleArtifact.load(oracle_path, common["WORKFLOW_WORK_MODEL_SHA256"])
    progress = causal.load(args.progress_evidence)
    if (
        progress["artifact_sha256"] != common["WORKFLOW_WORK_MODEL_SHA256"]
        or not progress["complete_work_conserved"]
        or progress["counts"].get("unmodeled_core_event_observations", 0) != 0
        or progress["counts"]["closed_workflows"] != 18
        or progress["operator_sha256"] != sha256_file(
            ROOT / "experiments/reports/workflow_resume_oracle_20261029/qualify_progress.py"
        )
    ):
        raise ValueError("oracle actual whole-execution progress is unqualified")
    source = MAIN / "experiments/raw/locobench_parallel_retry_source_20261026_v230/native_capture"
    graph, _expected, source_evidence = source_plan(source)
    oracle.validate_source(source, graph, source_evidence)
    if (
        common["WORKFLOW_WORK_SOURCE"] != "oracle"
        or any(common[name] != "0" for name in (
            "TOOL_PREFETCH", "EXECUTION_FRONTIER_RESTORE", "LAYER_HANDOFF", "ENABLE_MIXED_CHUNK",
        ))
        or common["AGENT_PRESSURE_PAUSE_POLICY"] != "reference"
    ):
        raise ValueError("oracle changed the declared fixed non-ranking policies")
    native = Path("/home/longhao/experiment/.worktrees/sglang-free-extent-registration-20261029")
    native_state = git_source_state(native, canonical_patch=engine_patch(ROOT, FREE_EXTENT_TREE))
    if native_state["canonical_patch"]["actual_tree"] != FREE_EXTENT_TREE:
        raise ValueError("oracle changed the qualified native data plane")
    native_sources = directory / "native_sources.json"
    causal.reservation.write_json(native_sources, native_state)
    native_report = MAIN / "experiments/reports/layer_free_extent_20261029/native_cpu_r1/native_cpu.xml"
    native_evidence = capture.engine_evidence(native, native_report, native_sources)
    authorization = check_reservation()
    if not causal.qualified.resources(args.port)["ready"]:
        raise ValueError("GPU, Host or selected port is occupied")
    if Path(f"/tmp/bkv-causal-{args.port}.sock").exists():
        raise ValueError("use a fresh oracle event socket")
    registration = OPS / "workflow_model_tool_oracle_position_20261029_r1.registration.json"
    full = OPS / "workflow_model_tool_oracle_20261029_r1.registration.json"
    command = [
        sys.executable, str(ROOT / "scripts/run_position_replay_calibration.py"),
        "--source-registration", str(OPS / "v230_locobench_parallel_retry_source_20261026.registration.json"),
        "--reference", str(OPS / "v128_eos_seeded_modules_resume_20261022.registration.json"),
        "--cpu-report", cpu["report"]["path"], "--cpu-sources", cpu["source_manifest"]["path"],
        "--engine", str(native), "--engine-report", native_evidence["report"],
        "--engine-sources", str(native_sources), "--registration", str(registration),
        "--run-root", str(MAIN / "experiments/raw/workflow_model_tool_oracle_position_20261029_r1"),
        "--comparison-registration", str(full),
        "--comparison-root", str(MAIN / "experiments/raw/workflow_model_tool_oracle_20261029_r1"),
        "--port", str(args.port), "--experiment-config", str(spec_path),
    ]
    inputs = [
        args.cpu_evidence, Path(cpu["source_manifest"]["path"]), Path(cpu["report"]["path"]),
        spec_path, oracle_path, args.progress_evidence, *controls,
    ]
    pins = {str(path.resolve()): sha256_file(path) for path in inputs}
    result = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True,
        env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21"},
    )
    causal.reservation.write_json(directory / "entry.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(), "application": application,
        "authorization": authorization, "command": command, "returncode": result.returncode,
        "stdout": result.stdout, "stderr": result.stderr, "inputs_sha256": pins,
        "operator_sha256": sha256_file(Path(__file__)),
        "future_model_and_tool_information_used_for_ranking": True,
        "registration": str(registration), "comparison_registration": str(full),
        "retained_controls_complete_and_actual_exit_verified": True,
        "performance_accepted": False,
    })
    print(result.stdout, end="")
    if result.returncode:
        print(result.stderr, end="", file=sys.stderr)
        raise RuntimeError("preserve failed oracle qualification registration")


if __name__ == "__main__":
    main()
