"""Register only the newly authorized warm-start ThunderAgent control."""

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OPS = ROOT.parent / "ops"
CONTROL = OPS / "v345_locobench_categorized_thunder_decay_20261027.registration.json"
REGISTRATION = OPS / "warm_start_thunder_20261029_r1.registration.json"
RUN = ROOT / "experiments/raw/warm_start_thunder_20261029_r1"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application", required=True, type=Path)
    parser.add_argument("--cpu-evidence", required=True, type=Path)
    parser.add_argument("--port", type=int, default=18851)
    args = parser.parse_args()
    application = args.application.resolve()
    sys.path.insert(0, str(application))
    from beliefkv.experiments.source_provenance import git_source_state, sha256_file
    from beliefkv.experiments.native_engine_variant import COMMON_TREE
    from scripts import run_causal_comparison as causal
    from scripts.qualify_free_page_h2d_fence import check_reservation

    output, native_sources = HERE / "registration_entry.json", HERE / "native_sources.json"
    if any(path.exists() for path in (output, native_sources, REGISTRATION, RUN)):
        raise FileExistsError("preserve earlier warm-start registrations")
    inputs = {}

    def load(path):
        inputs[str(path.resolve())] = sha256_file(path)
        return causal.load(path)

    cpu = load(args.cpu_evidence)
    sources_path, report_path = (Path(cpu[name]["path"]) for name in ("source_manifest", "report"))
    sources = load(sources_path)
    if (
        cpu["all_prior_726_identities_retained"] is not True
        or any(cpu["totals"][name] for name in ("errors", "failures", "skipped"))
        or cpu["unique_test_identities"] != cpu["totals"]["tests"]
        or inputs[str(sources_path.resolve())] != cpu["source_manifest"]["sha256"]
        or sha256_file(report_path) != cpu["report"]["sha256"]
    ):
        raise ValueError("actual warm-start CPU binding differs")
    causal.comparison.verify_cpu_snapshot(application, sources)
    app = git_source_state(application)
    if app["dirty"] or sha256_file(application / Path(__file__).relative_to(ROOT)) != sha256_file(Path(__file__)):
        raise ValueError("warm-start operator or application differs from frozen CPU qualification")
    previous = load(CONTROL)
    if not causal.predecessor_ready({**previous, "after_registration": str(CONTROL)}):
        raise ValueError("the completed ThunderAgent reference still owns live processes")
    causal.predecessor_work({**previous, "after_registration": str(CONTROL)})
    causal.comparison.verify_sources(previous)
    if previous["engine_tree"] != COMMON_TREE:
        raise ValueError("warm-start control must reuse the exact common native engine")
    engine = Path(previous["engine_checkout"])
    native = git_source_state(engine, canonical_patch=application / "patches/sglang-v0.5.20-beliefkv-staging.patch")
    old_native = load(Path(previous["engine_evidence"]["sources"]))
    for name in set(native) - {"canonical_patch", "state_sha256"}:
        if native[name] != old_native[name]:
            raise ValueError("common native source changed outside canonical pathname")
    for name in set(native["canonical_patch"]) - {"path"}:
        if native["canonical_patch"][name] != old_native["canonical_patch"][name]:
            raise ValueError("common native content changed outside canonical pathname")
    authorization = check_reservation()
    if Path(f"/tmp/bkv-causal-{args.port}.sock").exists() or not causal.qualified.resources(args.port)["ready"]:
        raise ValueError("new ThunderAgent port, event socket or resources are unavailable")
    causal.reservation.write_json(native_sources, native)
    inputs[str(native_sources.resolve())] = sha256_file(native_sources)
    specification = application / Path(__file__).relative_to(ROOT).parent / "thunderagent.json"
    config = load(specification)
    if config["order"] != ["thunderagent_acting_decay"]:
        raise ValueError("warm-start registration selected unrelated experiments")
    load(Path(config["workflow_arrivals"]["path"]))
    command = [
        sys.executable, str(application / "scripts/run_causal_comparison.py"),
        "--reference", str(OPS / "v128_eos_seeded_modules_resume_20261022.registration.json"),
        "--source-arm", previous["source_arm"],
        "--gpu-smoke", str(ROOT / "experiments/raw/locobench_free_page_position_20261027_v246/gpu_smoke.json"),
        "--sampling-calibration", str(ROOT / "experiments/raw/locobench_free_page_position_20261027_v246/sampling_calibration.json"),
        "--cpu-report", str(report_path), "--cpu-sources", str(sources_path),
        "--engine", str(engine), "--engine-report", previous["engine_evidence"]["report"],
        "--engine-sources", str(native_sources),
        "--registration", str(REGISTRATION), "--run-root", str(RUN), "--port", str(args.port),
        "--arms", "thunderagent_acting_decay", "--experiment-config", str(specification),
        "--max-running-requests", "24",
        "--sampling-mode", "position", "--stream-response-metrics",
        "--tool-duration-overlay", str(application / "experiments/reports/v311_v230_categorized_network_timing_cpu_20261027.json"),
        "--workflow-arrivals", config["workflow_arrivals"]["path"],
    ]
    if any(sha256_file(Path(path)) != digest for path, digest in inputs.items()):
        raise ValueError("warm-start input changed before registration")
    result = subprocess.run(
        command, cwd=application, check=False, capture_output=True, text=True,
        env=dict(os.environ, CUDA_VISIBLE_DEVICES="99999", PYTHONHASHSEED="21"),
    )
    causal.reservation.write_json(output, {
        "observed_at": datetime.now(timezone.utc).isoformat(), "application": app,
        "authorization": authorization, "command": command,
        "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
        "registration": str(REGISTRATION), "inputs_sha256": inputs,
        "same_complete_work_different_arrivals": True,
        "GPU_sampler_proof_reused_only_with_exact_unchanged_sources": True,
        "agentix_and_tokencake_registered": False, "performance_accepted": False,
    })
    print(result.stdout, end="")
    if result.returncode:
        print(result.stderr, file=sys.stderr, end="")
        raise ValueError("warm-start registration rejected; preserve the exact attempt")


if __name__ == "__main__":
    main()
