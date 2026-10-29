"""Register only KVFlow after exact CPU/component and completed-predecessor checks."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OPS = ROOT.parent / "ops"
APPLICATION = ROOT.parent / ".worktrees/beliefkv-kvflow-causal-entry-20261028"
CONTROL = OPS / "v345_locobench_categorized_thunder_decay_20261027.registration.json"
PREDECESSOR = OPS / "demand_prefill_ack_retry_20261028_r1.registration.json"
REGISTRATION = OPS / "kvflow_locobench_v230_v311_20261028_r1.registration.json"
RUN = ROOT / "experiments/raw/kvflow_locobench_v230_v311_20261028_r1"
sys.path.insert(0, str(APPLICATION))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts import run_causal_comparison as causal
from scripts.qualify_free_page_h2d_fence import check_reservation
from scripts.qualify_paper_prefix_transfers import validate_report


def main():
    output = HERE / "formal_registration_entry.json"
    native_sources = HERE / "formal_native_sources.json"
    if any(path.exists() for path in (output, native_sources, REGISTRATION, RUN)):
        raise FileExistsError("preserve previous formal registration and native bindings")
    inputs = {}

    def load(path):
        inputs[str(path)] = sha256_file(path)
        return json.loads(path.read_bytes())

    cpu_path = HERE / "causal_entry_bound_cpu.json"
    cpu = load(cpu_path)
    manifest_path, cpu_report = Path(cpu["source_manifest"]["path"]), Path(cpu["report"]["path"])
    manifest = load(manifest_path)
    if (
        cpu["totals"] != {"tests": 726, "errors": 0, "failures": 0, "skipped": 0}
        or cpu["unique_test_identities"] != 726
        or cpu["all_prior_379_identities_retained"] is not True
        or len(manifest) != 1871
        or inputs[str(manifest_path)] != cpu["source_manifest"]["sha256"]
        or sha256_file(cpu_report) != cpu["report"]["sha256"]
    ):
        raise ValueError("merged causal/paper CPU evidence differs")
    inputs[str(cpu_report)] = sha256_file(cpu_report)
    causal.comparison.verify_cpu_snapshot(APPLICATION, manifest)
    application = git_source_state(APPLICATION)
    if application["dirty"] or sha256_file(
        APPLICATION / Path(__file__).relative_to(ROOT)
    ) != sha256_file(Path(__file__)):
        raise ValueError("formal entry or frozen application differs")
    previous, control = load(PREDECESSOR), load(CONTROL)
    for path, plan in ((PREDECESSOR, previous), (CONTROL, control)):
        if not causal.predecessor_ready({**plan, "after_registration": str(path)}):
            raise ValueError("a predecessor supervisor or owned child remains live")
        causal.comparison.verify_sources(plan)
    component_entry = load(HERE / "gpu_components_r1.entry.json")
    if (
        component_entry["status"] != "native_session_prefix_components_qualified"
        or component_entry["actual_process_exited"] is not True
        or component_entry["owned_gpu_process_absent"] is not True
        or causal.reservation.alive(component_entry["component_process"])
    ):
        raise ValueError("current session prefix component is incomplete or still live")
    component_path = Path(component_entry["summary"]["path"])
    component = load(component_path)
    patch = APPLICATION / "patches/sglang-v0.5.20-beliefkv-staging.patch"
    validate_report(component, engine_tree=control["engine_tree"], patch_sha256=sha256_file(patch))
    if inputs[str(component_path)] != component_entry["summary"]["sha256"]:
        raise ValueError("actual prefix component summary changed")
    native = git_source_state(Path(control["engine_checkout"]), canonical_patch=patch)
    old_native = load(Path(control["engine_evidence"]["sources"]))
    for name in set(native) - {"canonical_patch", "state_sha256"}:
        if native[name] != old_native[name]:
            raise ValueError("common native state changed outside canonical path rebinding")
    for name in set(native["canonical_patch"]) - {"path"}:
        if native["canonical_patch"][name] != old_native["canonical_patch"][name]:
            raise ValueError("common native content changed outside canonical path rebinding")
    authorization = check_reservation()
    if not causal.qualified.resources(18701)["ready"]:
        raise ValueError("formal baseline resources or port are unavailable")
    causal.reservation.write_json(native_sources, native)
    inputs[str(native_sources)] = sha256_file(native_sources)
    command = [
        sys.executable, str(APPLICATION / "scripts/run_causal_comparison.py"),
        "--reference", str(OPS / "v128_eos_seeded_modules_resume_20261022.registration.json"),
        "--source-arm", previous["source_arm"],
        "--gpu-smoke", str(ROOT / "experiments/raw/locobench_free_page_position_20261027_v246/gpu_smoke.json"),
        "--sampling-calibration", str(ROOT / "experiments/raw/locobench_free_page_position_20261027_v246/sampling_calibration.json"),
        "--cpu-report", str(cpu_report), "--cpu-sources", str(manifest_path),
        "--engine", control["engine_checkout"],
        "--engine-report", control["engine_evidence"]["report"],
        "--engine-sources", str(native_sources),
        "--registration", str(REGISTRATION), "--run-root", str(RUN),
        "--port", "18701", "--arms", "kvflow_observed_graph",
        "--experiment-config", str(APPLICATION / "configs/baselines/kvflow_locobench_v230_v311_20261028.json"),
        "--max-running-requests", "24", "--sampling-mode", "position",
        "--stream-response-metrics",
        "--tool-duration-overlay", str(APPLICATION / "experiments/reports/v311_v230_categorized_network_timing_cpu_20261027.json"),
    ]
    if any(sha256_file(Path(path)) != digest for path, digest in inputs.items()):
        raise ValueError("entry evidence changed before formal registration")
    result = subprocess.run(
        command, cwd=APPLICATION, capture_output=True, text=True, check=False,
        env=dict(os.environ, CUDA_VISIBLE_DEVICES="99999", PYTHONHASHSEED="21"),
    )
    evidence = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "application": application, "cpu_cases": 726, "matched_cpu_source_files": len(manifest),
        "authorization": authorization, "command": command,
        "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
        "registration": str(REGISTRATION), "inputs_sha256": inputs,
        "same_complete_V230_V311_selected": True, "native_sources_rebound_path_only": True,
        "fresh_prefix_qualification_runs_before_model": True,
        "common_engine_sampling_proof_reused_if_unchanged": True,
        "native_and_V345_reruns_registered": False, "performance_accepted": False,
    }
    with output.open("x") as stream:
        json.dump(evidence, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(result.stdout, end="")
    if result.returncode:
        print(result.stderr, file=sys.stderr, end="")
        raise ValueError("formal registration rejected; preserve this attempt")


if __name__ == "__main__":
    main()
