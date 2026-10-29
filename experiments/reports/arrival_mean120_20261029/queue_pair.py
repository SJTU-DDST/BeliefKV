"""Queue the matched mean120 ThunderAgent/Full pair after actual owned exit."""

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
MAIN = Path("/home/longhao/experiment/BeliefKV")
HERE = MAIN / "experiments/reports/arrival_mean120_20261029"
OPS = MAIN.parent / "ops"
ORDER = ("thunderagent_acting_decay", "full")
PREVIOUS = {
    ORDER[0]: OPS / "warm_start_thunder_20261029_r1.registration.json",
    ORDER[1]: OPS / "warm_start_free_extent_20261029_r1.registration.json",
}
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.native_engine_variant import engine_patch
from beliefkv.experiments.replay_arrivals import validate_arrival_plan
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts import run_causal_comparison as causal
from scripts.qualify_free_page_h2d_fence import check_reservation


def validate_pair(specifications, identities):
    contracts = [item["workflow_arrivals"] for item in specifications.values()]
    if len(identities) != 18 or len(set(identities)) != 18 or contracts[0] != contracts[1]:
        raise ValueError("both complete methods must share the same arrival contract")
    path = Path(contracts[0]["path"])
    if sha256_file(path) != contracts[0]["sha256"]:
        raise ValueError("arrival table changed")
    arrivals = validate_arrival_plan(causal.load(path), identities)
    if (
        arrivals["initial_workflows"] != 10 or arrivals["rate_per_second"] != 1 / 120
        or arrivals["seed"] != 21
    ):
        raise ValueError("expected ten initial roots and eight mean120 seed21 arrivals")
    for label, specification in specifications.items():
        if specification["order"] != [label]:
            raise ValueError("the requested arm order changed")
        previous_configuration = Path(specification["arrival_experiment"]["previous_configuration"])
        old = causal.load(ROOT / previous_configuration.relative_to(MAIN))
        if specification["common_environment"] != old["common_environment"]:
            raise ValueError("arrival-only experiment changed serving or method policy")
    return arrivals


def validate_references(plans):
    left, right = (plans[label] for label in ORDER)
    if any(
        left[name] != right[name] for name in (
            "source_arm", "source_plan", "workflow_ids", "source_capacity", "source_server_parameters",
            "token_source_sha256", "sampling_mode", "max_running_requests",
            "stream_response_metrics", "request_timeout_s",
        )
    ) or (
        {key: value for key, value in left["tool_timing_overlay"].items() if key != "path"}
        != {key: value for key, value in right["tool_timing_overlay"].items() if key != "path"}
    ):
        raise ValueError("previous paired methods have different work or serving contracts")


def validate_cpu(path):
    cpu = causal.load(path)
    if (
        cpu["returncode"] or cpu["failed_error_skipped_cases"]
        or not cpu["all_retained_literal_identities_present"]
        or not cpu["sources_unchanged_during_execution"]
        or sha256_file(Path(cpu["source_manifest"]["path"])) != cpu["source_manifest"]["sha256"]
        or sha256_file(Path(cpu["report"]["path"])) != cpu["report"]["sha256"]
    ):
        raise ValueError("source-bound CPU qualification is incomplete")
    causal.comparison.verify_cpu_snapshot(ROOT, causal.load(Path(cpu["source_manifest"]["path"])))
    return cpu


def complete_work(path):
    previous = causal.load(path)
    if not causal.predecessor_ready({**previous, "after_registration": str(path)}):
        return None
    work = causal.predecessor_work({**previous, "after_registration": str(path)})
    for label in previous["order"]:
        arm = Path(previous["run_root"]) / label
        cleanup = causal.load(arm / "owned_process_cleanup.json")
        drain = causal.load(arm / "telemetry_drain.json")
        idle = drain["drained_status"]
        if (
            cleanup["remaining_processes"] or not cleanup.get("finished_at")
            or any(causal.reservation.alive(row) for row in cleanup["identities"])
            or not drain["previous_zero_snapshot_ts_ms"] < idle["snapshot_ts_ms"]
        ):
            raise ValueError("predecessor fresh drain or actual owned cleanup is incomplete")
    if (len(work), sum(row[0] for row in work.values()), sum(row[1] for row in work.values())) != (
        3674, 145565966, 549671,
    ):
        raise ValueError("predecessor did not retain the complete original work")
    return work


def verify(plan):
    if git_source_state(ROOT) != plan["application"]:
        raise ValueError("frozen queue application changed")
    for path, digest in plan["inputs_sha256"].items():
        if sha256_file(Path(path)) != digest:
            raise ValueError("queued immutable input changed: " + path)
    for path in PREVIOUS.values():
        causal.comparison.verify_sources(causal.load(path))
    check_reservation()
    if sha256_file(Path(plan["authorization"]["hold_file"])) != plan["authorization"]["hold_sha256"]:
        raise causal.fresh.HoldChanged("reservation changed after queue registration")


def register(args):
    if os.getpriority(os.PRIO_PROCESS, os.getpid()) != 0:
        raise ValueError("GPU supervisors must inherit nice0")
    if git_source_state(ROOT)["dirty"]:
        raise ValueError("freeze committed application sources before queue registration")
    output = HERE / "queue_r1"
    if output.exists():
        raise FileExistsError("preserve every prior queue attempt")
    cpu = validate_cpu(args.cpu_evidence)
    specifications = {
        label: causal.load(ROOT / Path(__file__).relative_to(ROOT).parent / (label + ".json"))
        for label in ORDER
    }
    old_plans = {label: causal.load(path) for label, path in PREVIOUS.items()}
    arrivals = validate_pair(specifications, old_plans[ORDER[0]]["workflow_ids"])
    validate_references(old_plans)
    output.mkdir()
    commands, entries = {}, {}
    inputs = [
        args.cpu_evidence, Path(cpu["report"]["path"]), Path(cpu["source_manifest"]["path"]),
        Path(specifications[ORDER[0]]["workflow_arrivals"]["path"]),
        *PREVIOUS.values(),
    ]
    for ordinal, label in enumerate(ORDER):
        old = old_plans[label]
        engine = Path(old["engine_checkout"])
        native = git_source_state(engine, canonical_patch=engine_patch(ROOT, old["engine_tree"]))
        qualified_native = causal.load(Path(old["engine_evidence"]["sources"]))
        if (
            {k: v for k, v in native.items() if k not in ("canonical_patch", "state_sha256")}
            != {k: v for k, v in qualified_native.items() if k not in ("canonical_patch", "state_sha256")}
            or {k: v for k, v in native["canonical_patch"].items() if k != "path"}
            != {k: v for k, v in qualified_native["canonical_patch"].items() if k != "path"}
        ):
            raise ValueError("qualified method-specific native bytes changed")
        native_path = output / (label + ".native_sources.json")
        causal.reservation.write_json(native_path, native)
        evidence = causal.engine_evidence(engine, Path(old["engine_evidence"]["report"]), native_path)
        position = (
            MAIN / "experiments/raw/locobench_free_page_position_20261027_v246"
            if label == ORDER[0] else MAIN / "experiments/raw/free_extent_position_20261029_r1"
        )
        short = "thunder" if label == ORDER[0] else "full"
        registration = OPS / f"arrival_mean120_{short}_20261029_r1.registration.json"
        run = MAIN / f"experiments/raw/arrival_mean120_{short}_20261029_r1"
        port = 18871 + ordinal
        if any(path.exists() for path in (
            registration, registration.with_suffix(".state.json"), registration.with_suffix(".log"), run,
            Path(f"/tmp/bkv-causal-{port}.sock"),
        )):
            raise FileExistsError("use fresh arrival experiment outputs and sockets")
        specification = ROOT / Path(__file__).relative_to(ROOT).parent / (label + ".json")
        command = [
            sys.executable, str(ROOT / "scripts/run_causal_comparison.py"),
            "--reference", str(OPS / "v128_eos_seeded_modules_resume_20261022.registration.json"),
            "--source-arm", old["source_arm"],
            "--gpu-smoke", str(position / "gpu_smoke.json"),
            "--sampling-calibration", str(position / "sampling_calibration.json"),
            "--cpu-report", cpu["report"]["path"], "--cpu-sources", cpu["source_manifest"]["path"],
            "--engine", str(engine), "--engine-report", evidence["report"],
            "--engine-sources", str(native_path), "--registration", str(registration),
            "--run-root", str(run), "--port", str(port), "--arms", label,
            "--experiment-config", str(specification), "--max-running-requests", "24",
            "--sampling-mode", "position", "--stream-response-metrics",
            "--tool-duration-overlay", str(ROOT / "experiments/reports/v311_v230_categorized_network_timing_cpu_20261027.json"),
            "--workflow-arrivals", specifications[label]["workflow_arrivals"]["path"],
        ]
        commands[label] = command
        entries[label] = {"registration": str(registration), "run_root": str(run), "port": port}
        inputs.extend((
            specification, native_path, Path(evidence["report"]), Path(old["engine_evidence"]["sources"]),
            position / "gpu_smoke.json", position / "sampling_calibration.json",
        ))
    plan = {
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "application": git_source_state(ROOT), "authorization": check_reservation(),
        "initial_predecessor": str(PREVIOUS["full"]), "commands": commands, "entries": entries,
        "order": list(ORDER), "workflow_arrivals": arrivals,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in inputs},
        "automatic_retry_after_failure": False, "performance_accepted": False,
    }
    path = output / "registration.json"
    causal.reservation.write_json(path, plan)
    verify(plan)
    with (output / "watcher.log").open("x") as log:
        watcher = subprocess.Popen(
            [sys.executable, str(Path(__file__)), "--watch", str(path)],
            cwd=ROOT, env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21"},
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    print(json.dumps({"registration": str(path), "watcher": causal.reservation.process(watcher.pid)}, indent=2))


def watch(path):
    plan = causal.load(path)
    state_path = path.with_suffix(".state.json")
    if state_path.exists():
        raise FileExistsError("never resume a partial arrival queue")
    state = {
        "status": "waiting_predecessor", "watcher": causal.reservation.process(os.getpid()),
        "completed_arms": [], "performance_accepted": False,
    }
    stopped = False
    current = None

    def stop(*_):
        nonlocal stopped
        stopped = True

    def pulse():
        if stopped:
            raise InterruptedError("arrival queue stopped")
        check_reservation()
        if sha256_file(Path(plan["authorization"]["hold_file"])) != plan["authorization"]["hold_sha256"]:
            raise causal.fresh.HoldChanged("reservation changed")
        state["checked_at"] = causal.reservation.now().isoformat()
        causal.reservation.write_json(state_path, state)

    def wait_for(path):
        state["waiting_registration"] = str(path)
        while True:
            pulse()
            if path.with_suffix(".state.json").exists() and complete_work(path) is not None:
                return
            if current is not None and not causal.reservation.alive(current):
                # A successful supervisor must publish terminal state before exiting.
                if complete_work(path) is None:
                    raise ValueError("comparison supervisor exited without complete work")
                return
            time.sleep(10.)

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, stop)
    try:
        verify(plan)
        wait_for(Path(plan["initial_predecessor"]))
        for label in plan["order"]:
            pulse()
            verify(plan)
            state.update(status="registering_" + label, current_arm=label)
            causal.reservation.write_json(state_path, state)
            result = subprocess.run(
                plan["commands"][label], cwd=ROOT, capture_output=True, text=True,
                env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21"},
            )
            causal.reservation.write_json(path.parent / (label + ".entry.json"), {
                "command": plan["commands"][label], "returncode": result.returncode,
                "stdout": result.stdout, "stderr": result.stderr,
                "observed_at": causal.reservation.now().isoformat(),
            })
            if result.returncode:
                raise RuntimeError("comparison registration failed; preserve its attempt")
            published = json.loads(result.stdout)
            if published["registration"] != plan["entries"][label]["registration"]:
                raise ValueError("comparison registered a different run")
            current = published["watcher"]
            state.update(status="waiting_" + label, comparison_watcher=current)
            wait_for(Path(published["registration"]))
            current = None
            state["completed_arms"].append(label)
        state["status"] = "reviewing_complete_pair"
        pulse()
        completed = subprocess.run(
            [
                "nice", "-n", "19", sys.executable,
                str(Path(__file__).with_name("review_pair.py")),
                "--thunder", plan["entries"][ORDER[0]]["registration"],
                "--full", plan["entries"]["full"]["registration"],
                "--output-dir", str(path.parent / "complete_review"),
            ],
            cwd=ROOT, capture_output=True, text=True,
            env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999", "OMP_NUM_THREADS": "1"},
        )
        causal.reservation.write_json(path.parent / "complete_review.entry.json", {
            "returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr,
        })
        if completed.returncode:
            raise RuntimeError("completed pair review failed; preserve raw runs")
        state["status"] = "complete_pair_reviewed"
    except BaseException as error:
        state.update(status="stopped_for_investigation", error=repr(error))
        if current is not None and causal.reservation.alive(current):
            os.kill(current["pid"], signal.SIGTERM)
    finally:
        state["finished_at"] = causal.reservation.now().isoformat()
        causal.reservation.write_json(state_path, state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpu-evidence", type=Path)
    parser.add_argument("--watch", type=Path)
    args = parser.parse_args()
    if args.watch:
        watch(args.watch)
    elif args.cpu_evidence:
        register(args)
    else:
        parser.error("supply qualified CPU evidence or an immutable queue registration")


if __name__ == "__main__":
    main()
