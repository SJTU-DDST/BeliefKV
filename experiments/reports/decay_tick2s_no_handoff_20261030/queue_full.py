"""Replace only the unstarted Full after the existing complete control."""

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
MAIN = Path("/home/longhao/experiment/BeliefKV")
HERE = MAIN / "experiments/reports/decay_tick2s_no_handoff_20261030"
OPS = MAIN.parent / "ops"
CONTROL = OPS / "prefill2048_mean120_thunder_20261029_r1.registration.json"
OLD_QUEUE = MAIN / "experiments/reports/prefill2048_mean120_20261029/queue_r2/registration.json"
CONFIGURATION = ROOT / "configs/migration/predictive_decay_tick2s_no_handoff_20261030.json"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.native_engine_variant import engine_patch
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from experiments.reports.arrival_mean120_20261029.queue_pair import complete_work, validate_cpu
from experiments.reports.prefill2048_mean120_20261029.queue_pair import method_complete, validate_native_rebinding
from scripts import run_causal_comparison as causal
from scripts.qualify_free_page_h2d_fence import check_reservation


def command_from_template(template, cpu, native_sources):
    command = list(template)
    values = {
        "--cpu-report": cpu["report"]["path"],
        "--cpu-sources": cpu["source_manifest"]["path"],
        "--engine-sources": str(native_sources),
        "--experiment-config": str(CONFIGURATION),
        "--registration": str(OPS / "decay_tick2s_position_20261030_r1_full.registration.json"),
        "--run-root": str(MAIN / "experiments/raw/decay_tick2s_position_20261030_r1_full"),
        "--comparison-registration": str(OPS / "decay_tick2s_no_handoff_20261030_r1.registration.json"),
        "--comparison-root": str(MAIN / "experiments/raw/decay_tick2s_no_handoff_20261030_r1"),
        "--port": "18883",
    }
    command[0], command[1] = sys.executable, str(ROOT / "scripts/run_position_replay_calibration.py")
    for name, value in values.items():
        if command.count(name) != 1:
            raise ValueError("require one exact qualification option: " + name)
        command[command.index(name) + 1] = value
    return command


def validate_configuration(specification, control):
    common = specification["common_environment"]
    effective = arm_environment(common, "full")
    expected = {
        "BELIEFKV_AGENT_CONTROL_INTERVAL_MS": "2000",
        "BELIEFKV_AGENT_FREE_DISPATCH_INTERVAL_MS": "0",
        "BELIEFKV_LAYER_HANDOFF": "0", "BELIEFKV_LAYER_HANDOFF_FREE_EXTENT": "0",
        "BELIEFKV_ENABLE_TOOL_PREFETCH": "0",
        "BELIEFKV_DEMAND_PREFILL_PIPELINE": "1",
    }
    if (
        specification["order"] != ["full"]
        or any(effective.get(name) != value for name, value in expected.items())
        or any(common.get(name) != value for name, value in {
            "AGENT_CONTROL_INTERVAL_MS": "2000", "BELIEFKV_AGENT_CONTROL_INTERVAL_MS": "2000",
            "LAYER_HANDOFF": "0", "BELIEFKV_LAYER_HANDOFF": "0",
            "BELIEFKV_LAYER_HANDOFF_FREE_EXTENT": "0",
        }.items())
    ):
        raise ValueError("candidate must use two-second Full control with physical handoff disabled")
    if (
        common["CHUNKED_PREFILL_SIZE"] != "2048" or common["ENABLE_MIXED_CHUNK"] != "0"
        or specification["workflow_arrivals"] != {
            "path": control["workflow_arrivals_path"],
            "sha256": sha256_file(Path(control["workflow_arrivals_path"])),
        }
        or control["source_server_parameters"]["chunked_prefill_size"] != 2048
        or control["source_server_parameters"]["enable_mixed_chunk"] is not False
    ):
        raise ValueError("candidate and existing control must share prefill and mean120 arrivals")


def verify(plan):
    authorization = check_reservation()
    if (
        git_source_state(ROOT) != plan["application"]
        or authorization["hold_sha256"] != plan["authorization"]["hold_sha256"]
    ):
        raise ValueError("frozen queue or GPU authorization changed")
    for path, digest in plan["inputs_sha256"].items():
        if sha256_file(Path(path)) != digest:
            raise ValueError("queued immutable input changed: " + path)


def register(args):
    application = git_source_state(ROOT)
    if application["dirty"] or args.output_dir.exists() or os.getpriority(os.PRIO_PROCESS, os.getpid()) != 0:
        raise ValueError("require clean committed sources, fresh output and nice0")
    authorization = check_reservation()
    cpu = validate_cpu(args.cpu_evidence)
    old = causal.load(OLD_QUEUE)
    old_state = causal.load(OLD_QUEUE.with_suffix(".state.json"))
    if causal.reservation.alive(old_state.get("watcher")):
        raise ValueError("superseded pair monitor still owns the pending Full launch")
    old_full = old["entries"]["full"]
    if any(Path(old_full[name]).exists() for name in ("position_registration", "comparison_registration")):
        raise ValueError("old Full already registered; never replace it silently")
    control = causal.load(CONTROL)
    causal.comparison.verify_sources(control)
    validate_configuration(causal.load(CONFIGURATION), control)
    template = old["commands"]["full"]
    engine = Path(template[template.index("--engine") + 1])
    prior_sources = Path(template[template.index("--engine-sources") + 1])
    qualified = causal.load(prior_sources)
    native = git_source_state(
        engine, canonical_patch=engine_patch(ROOT, qualified["canonical_patch"]["actual_tree"]),
    )
    validate_native_rebinding(native, qualified)
    args.output_dir.mkdir(parents=True)
    native_sources = args.output_dir / "native_sources.json"
    causal.reservation.write_json(native_sources, native)
    command = command_from_template(template, cpu, native_sources)
    evidence = causal.engine_evidence(engine, Path(command[command.index("--engine-report") + 1]), native_sources)
    for name in ("--registration", "--run-root", "--comparison-registration", "--comparison-root"):
        target = Path(command[command.index(name) + 1])
        if target.exists() or target.with_suffix(".state.json").exists():
            raise FileExistsError("preserve the previous qualification or comparison")
    entry = {
        "position_registration": command[command.index("--registration") + 1],
        "comparison_registration": command[command.index("--comparison-registration") + 1],
    }
    inputs = (
        args.cpu_evidence, Path(cpu["report"]["path"]), Path(cpu["source_manifest"]["path"]),
        OLD_QUEUE, OLD_QUEUE.with_suffix(".state.json"), CONTROL, CONFIGURATION,
        native_sources, Path(evidence["report"]), prior_sources,
    )
    path = args.output_dir / "registration.json"
    plan = {
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "application": application, "authorization": authorization,
        "control": str(CONTROL), "command": command, "entry": entry,
        "inputs_sha256": {str(source.resolve()): sha256_file(source) for source in inputs},
        "old_unregistered_full_superseded_by_user": True,
        "running_control_changed": False, "performance_accepted": False,
    }
    causal.reservation.write_json(path, plan)
    verify(plan)
    with (args.output_dir / "watcher.log").open("x") as output:
        watcher = subprocess.Popen(
            [sys.executable, str(Path(__file__)), "--watch", str(path)],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
            env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21"},
            start_new_session=True,
        )
    print({"registration": str(path), "watcher": causal.reservation.process(watcher.pid)})


def watch(path):
    plan = causal.load(path)
    state_path = path.with_suffix(".state.json")
    if state_path.exists():
        raise FileExistsError("preserve the prior queue state")
    state = {
        "status": "waiting_existing_control_complete",
        "watcher": causal.reservation.process(os.getpid()), "performance_accepted": False,
    }

    def pulse():
        verify(plan)
        state["checked_at"] = causal.reservation.now().isoformat()
        causal.reservation.write_json(state_path, state)

    try:
        while True:
            pulse()
            control_state = causal.load(Path(plan["control"]).with_suffix(".state.json"))
            if control_state["status"] == "collected_performance_review_pending":
                control_work = complete_work(Path(plan["control"]))
                if control_work is not None:
                    break
            elif not causal.reservation.alive(control_state.get("watcher")):
                raise ValueError("control exited without complete drained work")
            time.sleep(10.)
        completed = subprocess.run(
            plan["command"], cwd=ROOT, capture_output=True, text=True, timeout=180.,
            env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21"},
        )
        causal.reservation.write_json(path.parent / "position_entry.json", {
            "command": plan["command"], "returncode": completed.returncode,
            "stdout": completed.stdout, "stderr": completed.stderr,
        })
        if completed.returncode:
            raise ValueError("position registration refused; preserve this attempt")
        state["status"] = "waiting_position_then_complete_full"
        start, missing_since = time.monotonic(), None
        while True:
            pulse()
            entry = plan["entry"]
            position_state = Path(entry["position_registration"]).with_suffix(".state.json")
            comparison_state = Path(entry["comparison_registration"]).with_suffix(".state.json")
            if not position_state.exists() and time.monotonic() - start > 180.:
                raise ValueError("position supervisor did not publish state within 180 seconds")
            if Path(entry["comparison_registration"]).exists() and not comparison_state.exists():
                if missing_since is None:
                    missing_since = time.monotonic()
                elif time.monotonic() - missing_since > 180.:
                    raise ValueError("formal supervisor did not publish state within 180 seconds")
            work = method_complete(entry)
            if work is not None:
                if work != control_work:
                    raise ValueError("complete Full and control requests/tokens/stopping differ")
                break
            time.sleep(10.)
        state["status"] = "reviewing_complete_pair"
        pulse()
        with (path.parent / "reader.log").open("x") as output:
            subprocess.run([
                "nice", "-n", "19", "/home/longhao/experiment/ops/cpu_plotting_20261027/bin/python",
                str(ROOT / "experiments/reports/arrival_mean120_20261029/review_pair.py"),
                "--thunder", plan["control"], "--full", plan["entry"]["comparison_registration"],
                "--output-dir", str(path.parent / "complete_review"),
            ], cwd=ROOT, check=True, stdout=output, stderr=subprocess.STDOUT,
                env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999", "OMP_NUM_THREADS": "1"})
        state["status"] = "complete_pair_reviewed"
    except BaseException as error:
        state.update(status="stopped_no_next_gpu_action", error=repr(error))
    finally:
        state["finished_at"] = causal.reservation.now().isoformat()
        causal.reservation.write_json(state_path, state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpu-evidence", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--watch", type=Path)
    args = parser.parse_args()
    if args.watch:
        watch(args.watch)
    elif args.cpu_evidence and args.output_dir:
        register(args)
    else:
        parser.error("require current CPU evidence and fresh queue output, or --watch")


if __name__ == "__main__":
    main()
