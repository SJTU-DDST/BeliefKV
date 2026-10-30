"""Qualify and start the retained Full control on the new fixed burst workload."""

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
from experiments.reports.arrival_mean120_20261029.queue_pair import validate_cpu
from experiments.reports.workflow_work_training_20261029.finish_training import (
    audit_finished_source, source_ready,
)
from scripts import run_causal_comparison as causal
from scripts import run_causal_source_capture as capture
from scripts.qualify_free_page_h2d_fence import check_reservation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpu-evidence", type=Path, required=True)
    args = parser.parse_args()
    application = git_source_state(ROOT)
    if application["dirty"] or os.getpriority(os.PRIO_PROCESS, os.getpid()) != 0:
        raise ValueError("require clean frozen sources and nice0")
    cpu = validate_cpu(args.cpu_evidence)
    source_path = OPS / "workflow_mix_scope18_source_20261029_r1.registration.json"
    source, state = causal.load(source_path), causal.load(source_path.with_suffix(".state.json"))
    if not source_ready(source, state):
        raise ValueError("source still owns live processes")
    complete_source = audit_finished_source(source, state)
    specification = ROOT / "experiments/reports/workflow_mix_20261029/burst_full.json"
    config = causal.load(specification)
    if (
        config["source_registration"] != str(source_path)
        or config["order"] != ["full"]
        or config["common_environment"]["WORKFLOW_SIZE_RESUME"] != "0"
        or config["common_environment"]["TOOL_PREFETCH"] != "0"
        or config["common_environment"]["LAYER_HANDOFF"] != "0"
    ):
        raise ValueError("retained Full control changed its declared policy")
    authorization = check_reservation()
    port = 18904
    if not causal.qualified.resources(port)["ready"]:
        raise ValueError("GPU, Host, disk or selected port is occupied")
    native = Path("/home/longhao/experiment/.worktrees/sglang-free-extent-registration-20261029")
    native_state = git_source_state(native, canonical_patch=engine_patch(ROOT, FREE_EXTENT_TREE))
    if native_state["canonical_patch"]["actual_tree"] != FREE_EXTENT_TREE:
        raise ValueError("retained Full changed its native data plane")
    directory = MAIN / "experiments/reports/workflow_mix_20261029/burst_full_entry_r1"
    directory.mkdir(parents=True, exist_ok=False)
    native_sources = directory / "native_sources.json"
    capture.reservation.write_json(native_sources, native_state)
    proof = capture.engine_evidence(
        native, MAIN / "experiments/reports/layer_free_extent_20261029/native_cpu_r1/native_cpu.xml",
        native_sources,
    )
    position = OPS / "workflow_mix_burst_full_position_20261030_r1.registration.json"
    full = OPS / "workflow_mix_burst_full_20261030_r1.registration.json"
    command = [
        sys.executable, str(ROOT / "scripts/run_position_replay_calibration.py"),
        "--source-registration", str(source_path),
        "--reference", str(OPS / "v128_eos_seeded_modules_resume_20261022.registration.json"),
        "--cpu-report", cpu["report"]["path"], "--cpu-sources", cpu["source_manifest"]["path"],
        "--engine", str(native), "--engine-report", proof["report"],
        "--engine-sources", str(native_sources),
        "--registration", str(position),
        "--run-root", str(MAIN / "experiments/raw/workflow_mix_burst_full_position_20261030_full"),
        "--comparison-registration", str(full),
        "--comparison-root", str(MAIN / "experiments/raw/workflow_mix_burst_full_20261030_r1"),
        "--port", str(port), "--experiment-config", str(specification),
    ]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    capture.reservation.write_json(directory / "entry.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(), "application": application,
        "authorization": authorization, "complete_source": complete_source,
        "command": command, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
        "configuration_sha256": sha256_file(specification), "cpu_evidence_sha256": sha256_file(args.cpu_evidence),
        "operator_sha256": sha256_file(Path(__file__)), "position_registration": str(position),
        "comparison_registration": str(full), "performance_accepted": False,
    })
    print(result.stdout, end="")
    if result.returncode:
        print(result.stderr, end="", file=sys.stderr)
        raise RuntimeError("preserve the failed new-workload Full entry")


if __name__ == "__main__":
    main()
