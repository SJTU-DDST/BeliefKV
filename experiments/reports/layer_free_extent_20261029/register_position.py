"""Bind actual model proofs before fresh position calibration and a matched Full."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OPS = ROOT.parent / "ops"
CONTROL = OPS / "warm_start_thunder_20261029_r1.registration.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application", type=Path, required=True)
    parser.add_argument("--cpu-evidence", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18861)
    parser.add_argument("--attempt", choices=("r1", "r2", "r3"), default="r1")
    args = parser.parse_args()
    application = args.application.resolve()
    sys.path.insert(0, str(application))
    from beliefkv.experiments.native_engine_variant import FREE_EXTENT_TREE, engine_patch
    from beliefkv.experiments.source_provenance import git_source_state, sha256_file
    from scripts import run_causal_comparison as causal
    from scripts import run_causal_source_capture as capture
    from scripts.qualify_free_page_h2d_fence import check_reservation
    from scripts.qualify_layer_handoff_model import (
        compare_probe_outputs, validate_free_extent_model,
    )

    output = HERE / f"position_entry_{args.attempt}.json"
    native_sources = HERE / f"native_position_{args.attempt}.sources.json"
    registration = OPS / f"free_extent_position_20261029_{args.attempt}.registration.json"
    run = ROOT / f"experiments/raw/free_extent_position_20261029_{args.attempt}"
    full_registration = OPS / f"warm_start_free_extent_20261029_{args.attempt}.registration.json"
    full_run = ROOT / f"experiments/raw/warm_start_free_extent_20261029_{args.attempt}"
    if any(path.exists() for path in (output, native_sources, registration, run, full_registration, full_run)):
        raise FileExistsError("preserve previous qualification and comparison attempts")
    inputs = {}

    def load(path):
        inputs[str(path.resolve())] = sha256_file(path)
        return causal.load(path)

    cpu = load(args.cpu_evidence)
    manifest = load(Path(cpu["source_manifest"]["path"]))
    if (
        cpu["returncode"] or cpu["failed_error_skipped_cases"]
        or not cpu["all_retained_literal_identities_present"]
        or not cpu["sources_unchanged_during_execution"]
        or sha256_file(Path(cpu["source_manifest"]["path"])) != cpu["source_manifest"]["sha256"]
        or sha256_file(Path(cpu["report"]["path"])) != cpu["report"]["sha256"]
    ):
        raise ValueError("current source-bound CPU execution is incomplete")
    causal.comparison.verify_cpu_snapshot(application, manifest)
    app = git_source_state(application)
    if app["dirty"]:
        raise ValueError("position calibration must start from clean frozen application sources")
    model = load(args.model)
    if (
        model.get("status") != "unforced_layer_handoff_model_qualified"
        or model["operator_sha256"] != sha256_file(application / "scripts/qualify_layer_handoff_model.py")
        or not model["fixture"]["free_extent_restore_required"]
        or not model["fixture"]["independent_references_repeat_prefix_and_initial_generation"]
        or compare_probe_outputs(model["model"]["requests"], require_free_extent=True)
        != model["model"]["maximum_logprob_delta"]
    ):
        raise ValueError("actual free-extent model and independent histories are unqualified")
    if len(model["model"]["handoffs"]) != 1:
        raise ValueError("numerical fixture must have exactly one actual layer handoff")
    validate_free_extent_model(model["model"]["handoffs"][0])
    cleanup = load(args.model.parent / "owned_process_cleanup.json")
    if cleanup["remaining_processes"] or any(
        causal.reservation.alive(row) for row in cleanup["identities"]
    ):
        raise ValueError("model-owned processes still exist")
    component = load(HERE / "gpu_components_r1/report.json")
    if sha256_file(HERE / "gpu_components_r1/report.json") != model["component_sha256"]:
        raise ValueError("model component evidence changed")
    native = Path("/home/longhao/experiment/sglang-layer-free-extent-20261029")
    current_native = git_source_state(native)
    if current_native != cpu["native_source"] or current_native != model["native_engine"]:
        raise ValueError("model and CPU native bytes differ")
    native_cpu = load(HERE / "native_cpu_r1/evidence.json")
    if native_cpu["returncode"] or native_cpu["native_source"] != current_native:
        raise ValueError("native CPU execution differs from the GPU/model source")
    prepared = ROOT.parent / ".worktrees/sglang-free-extent-registration-20261029"
    patch = engine_patch(application, FREE_EXTENT_TREE)
    prepared_state = git_source_state(prepared, canonical_patch=patch)
    if prepared_state["canonical_patch"]["actual_tree"] != FREE_EXTENT_TREE:
        raise ValueError("prepared native tree differs")
    previous = load(CONTROL)
    if not causal.predecessor_ready({**previous, "after_registration": str(CONTROL)}):
        raise ValueError("the complete new-arrival control still owns live processes")
    causal.predecessor_work({**previous, "after_registration": str(CONTROL)})
    causal.comparison.verify_sources(previous)
    authorization = check_reservation()
    if os.getpriority(os.PRIO_PROCESS, os.getpid()) != 0 or not causal.qualified.resources(args.port)["ready"]:
        raise ValueError("use nice0 and current idle resources")
    if Path(f"/tmp/bkv-causal-{args.port}.sock").exists():
        raise ValueError("use a fresh position event socket")
    causal.reservation.write_json(native_sources, prepared_state)
    native_evidence = capture.engine_evidence(prepared, Path(native_cpu["report"]["path"]), native_sources)
    configuration = application / "experiments/reports/layer_free_extent_20261029/full_warm_start.json"
    spec = load(configuration)
    if spec["workflow_arrivals"]["sha256"] != sha256_file(Path(previous["workflow_arrivals_path"])):
        raise ValueError("Full and control arrival tables differ")
    command = [
        sys.executable, str(application / "scripts/run_position_replay_calibration.py"),
        "--source-registration", str(OPS / "v230_locobench_parallel_retry_source_20261026.registration.json"),
        "--reference", str(OPS / "v128_eos_seeded_modules_resume_20261022.registration.json"),
        "--cpu-report", cpu["report"]["path"], "--cpu-sources", cpu["source_manifest"]["path"],
        "--engine", str(prepared), "--engine-report", native_evidence["report"],
        "--engine-sources", str(native_sources), "--registration", str(registration),
        "--run-root", str(run), "--comparison-registration", str(full_registration),
        "--comparison-root", str(full_run), "--port", str(args.port),
        "--experiment-config", str(configuration),
    ]
    if any(sha256_file(Path(path)) != digest for path, digest in inputs.items()):
        raise ValueError("qualification inputs changed before position registration")
    completed = subprocess.run(
        command, cwd=application, capture_output=True, text=True,
        env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21"},
    )
    causal.reservation.write_json(output, {
        "observed_at": datetime.now(timezone.utc).isoformat(), "application": app,
        "authorization": authorization, "command": command, "returncode": completed.returncode,
        "stdout": completed.stdout, "stderr": completed.stderr, "inputs_sha256": inputs,
        "operator_sha256": sha256_file(Path(__file__)), "native_evidence": native_evidence,
        "same_complete_work_and_arrivals_as_thunder": True,
        "registration": str(registration), "comparison_registration": str(full_registration),
        "performance_accepted": False,
    })
    print(completed.stdout, end="")
    if completed.returncode:
        print(completed.stderr, end="", file=sys.stderr)
        raise RuntimeError("position registration rejected; preserve the actual attempt")


if __name__ == "__main__":
    main()
