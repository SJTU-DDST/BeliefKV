"""Bind the new native-loop components before fresh model/position qualification."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OPS = ROOT.parent / "ops"
APPLICATION = ROOT.parent / ".worktrees/beliefkv-tokencake-hooks-position-entry-20261028"
ENGINE = ROOT.parent / ".worktrees/sglang-tokencake-hooks-prepared-20261028"
FAILED = OPS / "tokencake_locobench_v230_v311_20261028_r1.registration.json"
REGISTRATION = OPS / "tokencake_native_hooks_position_20261028_r1.registration.json"
COMPARISON = OPS / "tokencake_locobench_native_hooks_20261028_r1.registration.json"
RUN = ROOT / "experiments/raw/tokencake_native_hooks_position_20261028_r1"
FULL_RUN = ROOT / "experiments/raw/tokencake_locobench_native_hooks_20261028_r1"
sys.path.insert(0, str(APPLICATION))

from beliefkv.experiments.native_engine_variant import TOKENCAKE_TREE, engine_patch
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts import run_causal_comparison as causal
from scripts import run_causal_source_capture as capture
from scripts.qualify_free_page_h2d_fence import check_reservation
from scripts.qualify_paper_prefix_transfers import gpu_processes
from scripts.qualify_tokencake_runtime import validate_report


def main():
    output, native_sources = HERE / "position_entry.json", HERE / "position_native_sources.json"
    if any(path.exists() for path in (output, native_sources, REGISTRATION, COMPARISON, RUN, FULL_RUN)):
        raise FileExistsError("preserve prior new-hook position/full registrations")
    inputs = {}

    def load(path):
        inputs[str(path)] = sha256_file(path)
        return causal.load(path)

    cpu_path = HERE / "bound_cpu.json"
    cpu = load(cpu_path)
    manifest_path, report_path = (Path(cpu[name]["path"]) for name in ("source_manifest", "report"))
    manifest = load(manifest_path)
    if (
        cpu["totals"] != {"tests": 812, "errors": 0, "failures": 0, "skipped": 0}
        or cpu["all_prior_759_identities_retained"] is not True
        or inputs[str(manifest_path)] != cpu["source_manifest"]["sha256"]
        or sha256_file(report_path) != cpu["report"]["sha256"]
    ):
        raise ValueError("current source-bound 812-case CPU regression differs")
    inputs[str(report_path)] = sha256_file(report_path)
    application = git_source_state(APPLICATION)
    if application["dirty"] or sha256_file(
        APPLICATION / Path(__file__).relative_to(ROOT)
    ) != sha256_file(Path(__file__)):
        raise ValueError("fresh position entry or frozen source differs")
    causal.comparison.verify_cpu_snapshot(APPLICATION, manifest)
    component_entry = load(HERE / "gpu_components_r1.entry.json")
    if (
        component_entry["status"] != "native_scheduler_loop_components_qualified"
        or component_entry["returncode"] != 0
        or component_entry["actual_component_exit_verified"] is not True
        or component_entry["owned_gpu_process_absent"] is not True
        or causal.reservation.alive(component_entry["component_process"])
    ):
        raise ValueError("actual native-loop GPU components have not exited successfully")
    for source, digest in component_entry["inputs_sha256"].items():
        if sha256_file(Path(source)) != digest:
            raise ValueError("actual component input changed: " + source)
        inputs[source] = digest
    component_path = Path(component_entry["summary"]["path"])
    component = load(component_path)
    patch = engine_patch(APPLICATION, TOKENCAKE_TREE)
    validate_report(
        component, engine_tree=TOKENCAKE_TREE, patch_sha256=sha256_file(patch), root=APPLICATION,
    )
    if inputs[str(component_path)] != component_entry["summary"]["sha256"]:
        raise ValueError("actual component output changed")
    cleanup = load(HERE / "gpu_components_r1_cleanup/owned_process_cleanup.json")
    if cleanup.get("remaining_processes") != [] or not cleanup.get("finished_at"):
        raise ValueError("actual native-loop component cleanup is incomplete")
    old = component_entry["native_engine"]
    native = git_source_state(ENGINE, canonical_patch=patch)
    for field in set(native) - {"canonical_patch", "state_sha256"}:
        if native[field] != old[field]:
            raise ValueError("native source changed outside canonical patch pathname")
    for field in set(native["canonical_patch"]) - {"path"}:
        if native["canonical_patch"][field] != old["canonical_patch"][field]:
            raise ValueError("native patch or tree differs from actual GPU qualification")
    failed, stopped = load(FAILED), load(FAILED.with_suffix(".state.json"))
    failed_cleanup = load(
        Path(failed["run_root"]) / "tokencake_paper_observed/owned_process_cleanup.json",
    )
    if (
        stopped["status"] != "stopped_for_investigation"
        or failed_cleanup["remaining_processes"]
        or any(causal.reservation.alive(row) for row in (
            stopped["watcher"], stopped["server"], stopped["driver"],
            *failed_cleanup["identities"],
        ))
    ):
        raise ValueError("failed original TokenCake or an owned child remains live")
    authorization = check_reservation()
    if gpu_processes() or not causal.qualified.resources(18705)["ready"]:
        raise ValueError("new native-hook position resources are not free")
    causal.reservation.write_json(native_sources, native)
    inputs[str(native_sources)] = sha256_file(native_sources)
    engine_evidence = capture.engine_evidence(ENGINE, report_path, native_sources)
    spec = APPLICATION / "configs/baselines/tokencake_locobench_native_hooks_20261028.json"
    if load(spec)["order"] != ["tokencake_paper_observed"]:
        raise ValueError("new position entry selected unrelated baseline arms")
    command = [
        sys.executable, str(APPLICATION / "scripts/run_position_replay_calibration.py"),
        "--source-registration", str(OPS / "v230_locobench_parallel_retry_source_20261026.registration.json"),
        "--reference", str(OPS / "v128_eos_seeded_modules_resume_20261022.registration.json"),
        "--cpu-report", str(report_path), "--cpu-sources", str(manifest_path),
        "--engine", str(ENGINE), "--engine-report", str(report_path),
        "--engine-sources", str(native_sources),
        "--registration", str(REGISTRATION), "--run-root", str(RUN),
        "--comparison-registration", str(COMPARISON), "--comparison-root", str(FULL_RUN),
        "--port", "18705", "--source-selection", "complete",
        "--source-selection-reason",
        "Retain complete V230/V311 work and all tails while qualifying the mandatory TokenCake native hooks.",
        "--experiment-config", str(spec),
    ]
    if any(sha256_file(Path(path)) != digest for path, digest in inputs.items()):
        raise ValueError("new position qualification input changed before registration")
    result = subprocess.run(
        command, cwd=APPLICATION, capture_output=True, text=True, check=False,
        env=dict(os.environ, CUDA_VISIBLE_DEVICES="99999", PYTHONHASHSEED="21"),
    )
    record = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "application": application, "cpu_cases": 812, "engine_evidence": engine_evidence,
        "authorization": authorization, "command": command,
        "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
        "registration": str(REGISTRATION), "comparison_registration": str(COMPARISON),
        "same_complete_work_and_normal_tails": True, "model_position_results_pending": True,
        "native_and_thunderagent_reruns_registered": False,
        "inputs_sha256": inputs, "performance_accepted": False,
    }
    causal.reservation.write_json(output, record)
    print(result.stdout, end="")
    if result.returncode:
        print(result.stderr, file=sys.stderr, end="")
        raise ValueError("new native-hook position registration rejected")


if __name__ == "__main__":
    main()
