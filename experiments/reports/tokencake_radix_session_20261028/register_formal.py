"""Register only TokenCake on the completed KVFlow control's complete work."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OPS = ROOT.parent / "ops"
APPLICATION = ROOT.parent / ".worktrees/beliefkv-tokencake-causal-entry-20261028"
PREDECESSOR = OPS / "kvflow_locobench_v230_v311_20261028_r1.registration.json"
COMPONENTS = OPS / "tokencake_native_sessions_20261028_r1.registration.json"
REGISTRATION = OPS / "tokencake_locobench_v230_v311_20261028_r1.registration.json"
RUN = ROOT / "experiments/raw/tokencake_locobench_v230_v311_20261028_r1"
sys.path.insert(0, str(APPLICATION))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts import run_causal_comparison as causal
from scripts.qualify_free_page_h2d_fence import check_reservation
from scripts.qualify_tokencake_runtime import validate_report


def main():
    output, native_path = HERE / "formal_registration_entry.json", HERE / "formal_native_sources.json"
    if any(path.exists() for path in (
        output, native_path, REGISTRATION, REGISTRATION.with_suffix(".state.json"), RUN,
    )):
        raise FileExistsError("preserve earlier TokenCake formal attempts")
    inputs = {}

    def load(path):
        inputs[str(path)] = sha256_file(path)
        return json.loads(path.read_bytes())

    cpu_path = HERE / "formal_bound_cpu.json"
    cpu = load(cpu_path)
    manifest_path, report_path = (Path(cpu[name]["path"]) for name in ("source_manifest", "report"))
    manifest = load(manifest_path)
    if (
        cpu["totals"] != {"tests": 759, "errors": 0, "failures": 0, "skipped": 0}
        or cpu["unique_test_identities"] != 759
        or cpu["all_prior_742_identities_retained"] is not True
        or inputs[str(manifest_path)] != cpu["source_manifest"]["sha256"]
        or sha256_file(report_path) != cpu["report"]["sha256"]
    ):
        raise ValueError("formal TokenCake source-bound CPU evidence differs")
    inputs[str(report_path)] = sha256_file(report_path)
    causal.comparison.verify_cpu_snapshot(APPLICATION, manifest)
    application = git_source_state(APPLICATION)
    if application["dirty"] or sha256_file(
        APPLICATION / Path(__file__).relative_to(ROOT)
    ) != sha256_file(Path(__file__)):
        raise ValueError("formal TokenCake operator or frozen source differs")
    previous = load(PREDECESSOR)
    if not causal.predecessor_ready({**previous, "after_registration": str(PREDECESSOR)}):
        raise ValueError("KVFlow supervisor or owned child remains live")
    causal.comparison.verify_sources(previous)
    for label in previous["order"]:
        cleanup = load(Path(previous["run_root"]) / label / "owned_process_cleanup.json")
        if (
            cleanup.get("remaining_processes") != [] or not cleanup.get("finished_at")
            or any(causal.reservation.alive(row) for row in cleanup["identities"])
        ):
            raise ValueError("KVFlow owned cleanup has not completed")
    component = load(COMPONENTS)
    state = load(COMPONENTS.with_suffix(".state.json"))
    review = load(HERE / "gpu_review.json")
    if (
        review["status"] != "twelve_native_session_components_independently_verified"
        or state["status"] != "native_session_components_qualified"
        or state["component_returncode"] != 0 or state["owned"]
        or any(causal.reservation.alive(state[name]) for name in ("watcher", "component_process"))
    ):
        raise ValueError("real native-session components have not completed and exited")
    for path, digest in review["inputs_sha256"].items():
        if sha256_file(Path(path)) != digest:
            raise ValueError("independently reviewed component evidence changed")
        inputs[path] = digest
    component_report_path = Path(state["component_report"]["path"])
    component_report = load(component_report_path)
    patch = APPLICATION / "patches/sglang-v0.5.20-beliefkv-staging.patch"
    validate_report(
        component_report, engine_tree=cpu["native_tree"], patch_sha256=sha256_file(patch),
        root=APPLICATION,
    )
    if (
        inputs[str(component_report_path)] != state["component_report"]["sha256"]
        or component["engine_tree"] != cpu["native_tree"]
        or component_report["owned_gpu_pid"] != state["component_process"]["pid"]
    ):
        raise ValueError("reviewed GPU components belong to another native execution")
    native = git_source_state(Path(previous["engine_checkout"]), canonical_patch=patch)
    old_native = load(Path(previous["engine_evidence"]["sources"]))
    for name in set(native) - {"canonical_patch", "state_sha256"}:
        if native[name] != old_native[name]:
            raise ValueError("native source changed outside canonical path binding")
    for name in set(native["canonical_patch"]) - {"path"}:
        if native["canonical_patch"][name] != old_native["canonical_patch"][name]:
            raise ValueError("native tree or patch changed outside canonical path binding")
    authorization = check_reservation()
    if not causal.qualified.resources(18703)["ready"]:
        raise ValueError("formal TokenCake GPU, Host or port is unavailable")
    causal.reservation.write_json(native_path, native)
    inputs[str(native_path)] = sha256_file(native_path)
    command = [
        sys.executable, str(APPLICATION / "scripts/run_causal_comparison.py"),
        "--reference", str(OPS / "v128_eos_seeded_modules_resume_20261022.registration.json"),
        "--source-arm", previous["source_arm"],
        "--gpu-smoke", str(ROOT / "experiments/raw/locobench_free_page_position_20261027_v246/gpu_smoke.json"),
        "--sampling-calibration", str(ROOT / "experiments/raw/locobench_free_page_position_20261027_v246/sampling_calibration.json"),
        "--cpu-report", str(report_path), "--cpu-sources", str(manifest_path),
        "--engine", previous["engine_checkout"],
        "--engine-report", previous["engine_evidence"]["report"],
        "--engine-sources", str(native_path),
        "--registration", str(REGISTRATION), "--run-root", str(RUN),
        "--port", "18703", "--arms", "tokencake_paper_observed",
        "--experiment-config", str(APPLICATION / "configs/baselines/tokencake_locobench_v230_v311_20261028.json"),
        "--max-running-requests", "24", "--sampling-mode", "position",
        "--stream-response-metrics",
        "--tool-duration-overlay", str(APPLICATION / "experiments/reports/v311_v230_categorized_network_timing_cpu_20261027.json"),
    ]
    if any(sha256_file(Path(path)) != digest for path, digest in inputs.items()):
        raise ValueError("TokenCake entry evidence changed before registration")
    result = subprocess.run(
        command, cwd=APPLICATION, capture_output=True, text=True, check=False,
        env=dict(os.environ, CUDA_VISIBLE_DEVICES="99999", PYTHONHASHSEED="21"),
    )
    evidence = {
        "observed_at": datetime.now(timezone.utc).isoformat(), "application": application,
        "cpu_cases": 759, "source_files": len(manifest), "authorization": authorization,
        "command": command, "returncode": result.returncode,
        "stdout": result.stdout, "stderr": result.stderr,
        "registration": str(REGISTRATION), "inputs_sha256": inputs,
        "same_complete_V230_V311_work_selected": True,
        "native_patch_path_only_rebound": True,
        "fresh_prefix_and_twelve_lifecycle_scenes_before_model": True,
        "unchanged_common_engine_sampling_proof_reused": True,
        "native_and_thunderagent_reruns_registered": False,
        "performance_accepted": False,
    }
    with output.open("x") as stream:
        json.dump(evidence, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(result.stdout, end="")
    if result.returncode:
        print(result.stderr, file=sys.stderr, end="")
        raise ValueError("TokenCake registration rejected; preserve its original evidence")


if __name__ == "__main__":
    main()
