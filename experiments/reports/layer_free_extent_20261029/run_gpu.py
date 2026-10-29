"""Run one frozen component/model stage after the complete control has exited."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[3]
PREDECESSOR = ROOT.parent / "ops/warm_start_thunder_20261029_r1.registration.json"
RELATIVE = Path(__file__).relative_to(ROOT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application", type=Path, required=True)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--cpu-evidence", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--stage", choices=("components", "model"), required=True)
    parser.add_argument("--components", type=Path)
    parser.add_argument("--port", type=int, default=18855)
    args = parser.parse_args()
    args.output_dir = args.output_dir.resolve()
    args.cpu_evidence = args.cpu_evidence.resolve()
    if args.components is not None:
        args.components = args.components.resolve()
    application, engine = args.application.resolve(), args.engine.resolve()
    sys.path.insert(0, str(application))
    from beliefkv.experiments.source_provenance import git_source_state, sha256_file
    from scripts import run_causal_comparison as causal
    from scripts.qualify_free_page_h2d_fence import check_reservation, guard_gpu
    from scripts.qualify_layer_handoff_model import (
        validate_direction_groups, validate_free_extent_components,
    )
    from scripts.run_agentx_baseline import prefix_qualification_environment

    cpu = causal.load(args.cpu_evidence)
    sources_path = Path(cpu["source_manifest"]["path"])
    if (
        cpu["returncode"] or cpu["failed_error_skipped_cases"]
        or not cpu["all_retained_literal_identities_present"]
        or not cpu["sources_unchanged_during_execution"]
        or sha256_file(sources_path) != cpu["source_manifest"]["sha256"]
        or sha256_file(Path(cpu["report"]["path"])) != cpu["report"]["sha256"]
    ):
        raise ValueError("current source-bound CPU execution is incomplete or changed")
    causal.comparison.verify_cpu_snapshot(application, causal.load(sources_path))
    app, native = git_source_state(application), git_source_state(engine)
    if (
        app["dirty"] or native["dirty"] or native != cpu["native_source"]
        or sha256_file(application / RELATIVE) != sha256_file(Path(__file__))
        or os.getpriority(os.PRIO_PROCESS, os.getpid()) != 0
    ):
        raise ValueError("use the qualified clean frozen sources and nice0")
    prior = causal.load(PREDECESSOR)
    bound_prior = {**prior, "after_registration": str(PREDECESSOR)}
    if not causal.predecessor_ready(bound_prior):
        raise ValueError("the ThunderAgent control or an owned child is still live")
    causal.predecessor_work(bound_prior)
    causal.comparison.verify_sources(prior)
    authorization = check_reservation()
    if (
        not causal.qualified.resources(args.port)["ready"]
        or Path(f"/tmp/bkv-layer-model-{args.port}.sock").exists()
        or Path(f"/tmp/bkv-causal-{args.port}.sock").exists()
    ):
        raise ValueError("GPU, Host, port or model event socket is unavailable")
    guard_gpu()
    config = application / "experiments/reports/layer_free_extent_20261029/full_warm_start.json"
    spec = causal.load(config)
    inputs = {str(path): sha256_file(path) for path in (
        args.cpu_evidence, sources_path, Path(cpu["report"]["path"]),
        PREDECESSOR, config, application / RELATIVE,
    )}
    if args.stage == "components":
        command = [
            sys.executable, str(application / "scripts/qualify_layer_handoff.py"),
            "--output", str(args.output_dir / "report.json"),
            "--donor-group-layers", "8", "--consumer-group-layers", "4",
            "--include-free-extent",
        ]
        report_path = args.output_dir / "report.json"
    else:
        if args.components is None:
            raise ValueError("model stage requires the actual current component report")
        component = causal.load(args.components)
        if component.get("status") != "native_gpu_layer_handoff_components_qualified" or (
            component["native_engine"] != native
            or component["operator_sha256"] != sha256_file(application / "scripts/qualify_layer_handoff.py")
        ):
            raise ValueError("component bytes or frozen operator differ")
        validate_direction_groups(spec, component)
        validate_free_extent_components(spec, component)
        inputs[str(args.components)] = sha256_file(args.components)
        command = [
            sys.executable, str(application / "scripts/qualify_layer_handoff_model.py"),
            "--configuration", str(config), "--output-dir", str(args.output_dir / "model"),
            "--predecessor", str(PREDECESSOR), "--components", str(args.components),
            "--port", str(args.port),
        ]
        report_path = args.output_dir / "model/report.json"
    args.output_dir.mkdir(parents=True, exist_ok=False)
    evidence = {
        "status": "starting", "stage": args.stage, "application": app,
        "native_source": native, "authorization": authorization, "command": command,
        "inputs_sha256": inputs, "performance_accepted": False,
    }
    env = prefix_qualification_environment({"reference_environment": spec["common_environment"]}, os.environ)
    env.update(
        CUDA_VISIBLE_DEVICES="0", PYTHONHASHSEED="21", SGLANG_SOURCE_CHECKOUT=str(engine),
        PYTHONPATH=str(engine / "python") + os.pathsep + str(application),
    )
    process, identity = None, None
    try:
        with (args.output_dir / "run.log").open("x") as log:
            process = subprocess.Popen(
                command, cwd=application, env=env, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            )
            identity = causal.reservation.process(process.pid)
            evidence["child"] = identity
            deadline = time.monotonic() + (360. if args.stage == "components" else 1000.)
            while process.poll() is None:
                check_reservation()
                if time.monotonic() > deadline:
                    raise TimeoutError("bounded GPU stage exceeded its deadline")
                time.sleep(.25)
        expected = (
            "native_gpu_layer_handoff_components_qualified" if args.stage == "components"
            else "unforced_layer_handoff_model_qualified"
        )
        report = causal.load(report_path)
        if process.returncode or report.get("status") != expected:
            raise ValueError("actual GPU stage failed; preserve its report and log")
        if args.stage == "components":
            validate_direction_groups(spec, report)
            validate_free_extent_components(spec, report)
        causal.comparison.verify_cpu_snapshot(application, causal.load(sources_path))
        if (
            native != git_source_state(engine) or app != git_source_state(application)
            or any(sha256_file(Path(path)) != digest for path, digest in inputs.items())
        ):
            raise ValueError("sources changed during the actual GPU stage")
        evidence.update(status=expected, report={"path": str(report_path), "sha256": sha256_file(report_path)})
    except BaseException as error:
        evidence.update(status="failed", error=repr(error))
        raise
    finally:
        if process is not None and process.poll() is None:
            owned = causal.owned_processes([identity])
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=20.)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10.)
            if owned:
                causal.cleanup(args.output_dir, owned, "free_extent_gpu_stage_stop")
        evidence.update(
            observed_at=datetime.now(timezone.utc).isoformat(),
            returncode=None if process is None else process.returncode,
            actual_child_exited=process is not None and process.poll() is not None,
        )
        causal.reservation.write_json(args.output_dir / "entry.json", evidence)
    print(json.dumps({name: evidence[name] for name in ("status", "actual_child_exited", "returncode")}))


if __name__ == "__main__":
    main()
