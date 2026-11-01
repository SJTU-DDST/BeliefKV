"""Start a declared final comparison arm from clean, fully bound sources."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("checkout", "configuration", "source", "cohort", "cpu", "reference-position"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    root = args.checkout.resolve()
    sys.path.insert(0, str(root))
    from beliefkv.experiments.native_engine_variant import FREE_EXTENT_TREE, engine_patch
    from beliefkv.experiments.source_provenance import git_source_state, sha256_file
    from scripts import run_causal_comparison as causal
    from scripts import run_causal_source_capture as capture
    from scripts.qualify_free_page_h2d_fence import check_reservation
    from scripts.run_position_replay_calibration import valid_comparison_order

    main_root = Path("/home/longhao/experiment/BeliefKV")
    ops = main_root.parent / "ops"
    reports = main_root / "experiments/reports/remaining_original_projects_20261030"
    here = reports / ("gpu_entry_" + args.name + "_r1")
    source, cpu = causal.load(args.source), causal.load(args.cpu)
    config, state = causal.load(args.configuration), git_source_state(root)
    if (
        here.exists() or state["dirty"] or cpu["returncode"]
        or cpu["failed_error_skipped_cases"] or not valid_comparison_order(config)
        or len(config["order"]) != 1 or os.getpriority(os.PRIO_PROCESS, 0) != 0
        or any(cpu[name] is not True for name in (
            "all_retained_literal_identities_present", "all_previous_source_paths_retained",
            "sources_unchanged_during_execution",
        ))
    ):
        raise ValueError("require one fresh arm, clean bound sources and nice0 GPU owners")
    causal.comparison.verify_sources(source)
    causal.comparison.verify_cpu_snapshot(root, causal.load(Path(cpu["source_manifest"]["path"])))
    for name in ("report", "source_manifest"):
        if sha256_file(Path(cpu[name]["path"])) != cpu[name]["sha256"]:
            raise ValueError("bound CPU evidence changed: " + name)
    for name, digest in cpu["external_inputs_sha256"].items():
        if sha256_file(Path(name)) != digest:
            raise ValueError("bound external input changed: " + name)
    previous = causal.load(args.reference_position)
    engine = Path(cpu["environment"]["SGLANG_SOURCE_CHECKOUT"])
    if git_source_state(engine) != cpu["native_source"]:
        raise ValueError("native CPU source changed")
    native = git_source_state(engine, canonical_patch=engine_patch(root, FREE_EXTENT_TREE))
    old_native = causal.load(Path(previous["engine_evidence"]["sources"]))
    old_patch, new_patch = dict(old_native["canonical_patch"]), dict(native["canonical_patch"])
    old_patch.pop("path")
    new_patch.pop("path")
    if old_patch != new_patch or any(
        old_native[key] != native[key]
        for key in old_native if key not in ("canonical_patch", "state_sha256")
    ):
        raise ValueError("common native engine tree or patch changed")
    authorization, resources = check_reservation(), causal.qualified.resources(args.port)
    if not resources["ready"] or Path(f"/tmp/bkv-causal-{args.port}.sock").exists():
        raise ValueError("fresh GPU, Host, disk, port or event socket is unavailable")
    here.mkdir(parents=True, exist_ok=False)
    native_path = here / "native_sources.json"
    config_path = here / "configuration.json"
    capture.reservation.write_json(native_path, native)
    capture.reservation.write_json(config_path, config)
    capture.engine_evidence(engine, Path(previous["engine_evidence"]["report"]), native_path)
    arm = config["order"][0]
    settings, _, _ = causal.environment(
        {"arms": {"full": config["common_environment"]}}, arm, root, engine,
        here / "cpu-no-server", args.port, root, max_running_requests=24, sampling_mode="position",
    )
    constructor = causal.preflight_runtime(settings, config["common_environment"]["MODEL_PATH"])
    name = args.name + "_20261031_r1"
    comparison = ops / (name + ".registration.json")
    command = [
        sys.executable, str(root / "scripts/run_position_replay_calibration.py"),
        "--source-registration", str(args.source.resolve()),
        "--reference", previous["reference_registration"],
        "--cpu-report", cpu["report"]["path"], "--cpu-sources", cpu["source_manifest"]["path"],
        "--engine", str(engine), "--engine-report", previous["engine_evidence"]["report"],
        "--engine-sources", str(native_path),
        "--registration", str(ops / (args.name + "_position_20261031_r1.registration.json")),
        "--run-root", str(main_root / "experiments/raw" / (args.name + "_position_20261031_r1")),
        "--comparison-registration", str(comparison),
        "--comparison-root", str(main_root / "experiments/raw" / name),
        "--port", str(args.port), "--experiment-config", str(config_path),
    ]
    result = subprocess.run(command, cwd=root, capture_output=True, text=True)
    capture.reservation.write_json(here / "entry.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(), "application": state,
        "execution_checkout": str(root), "authorization": authorization, "resources": resources,
        "constructor": constructor, "command": command, "returncode": result.returncode,
        "stdout": result.stdout, "stderr": result.stderr,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            args.source, args.cpu, args.configuration, args.cohort,
            args.reference_position, config_path, Path(__file__),
        )},
        "performance_accepted": False,
    })
    print(result.stdout, end="", flush=True)
    if result.returncode:
        print(result.stderr, file=sys.stderr, end="")
        raise RuntimeError("preserve failed GPU entry; no automatic restart")
    observer_command = [
        "nice", "-n", "19", sys.executable,
        str(ops / "arm_final_matched_cohort_20261031.py"),
        "--checkout", str(root), "--comparison", str(comparison),
        "--cohort", str(args.cohort.resolve()), "--cpu", str(args.cpu.resolve()),
        "--configuration", str(config_path), "--name", args.name,
    ]
    with (here / "observer_bootstrap.log").open("x") as log:
        observer = subprocess.Popen(
            observer_command, cwd=root,
            env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999", "OMP_NUM_THREADS": "1",
                 "OPENBLAS_NUM_THREADS": "1"},
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    capture.reservation.write_json(here / "observer_bootstrap.json", {
        "process": causal.reservation.process(observer.pid), "command": observer_command,
        "GPU_action": False,
    })
    print(json.dumps({"comparison": str(comparison), "observer_bootstrap_pid": observer.pid}),
          flush=True)


if __name__ == "__main__":
    main()
