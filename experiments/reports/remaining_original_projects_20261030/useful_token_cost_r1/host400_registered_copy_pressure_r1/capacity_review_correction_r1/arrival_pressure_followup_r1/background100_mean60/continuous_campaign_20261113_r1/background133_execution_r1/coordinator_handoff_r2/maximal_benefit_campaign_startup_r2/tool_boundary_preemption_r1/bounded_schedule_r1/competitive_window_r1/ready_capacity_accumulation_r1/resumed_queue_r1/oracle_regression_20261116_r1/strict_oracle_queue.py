"""Queue the qualified strict Oracle after the existing materialization campaign."""

import argparse
import contextlib
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
SOURCE = BASE.parents[1]
QUEUE = HERE / "strict_oracle_queue_r1"
ROOT = Path("/home/longhao/experiment/.worktrees/beliefkv-strict-workflow-priority-20261116")
MATERIALIZATION = Path(
    "/home/longhao/experiment/.worktrees/beliefkv-subagent-materialization-pressure-20261116/"
    "experiments/reports/subagent_materialization_20261116/gpu_campaign_r2"
)
PREVIOUS_PREPARED = BASE.parent / "strict_priority_review_r1/prepared_r1"
PREPARED = HERE / "strict_prepared_r2"
CPU = BASE.parent / "strict_priority_review_r1/bound_cpu_r1/evidence.json"
HELPER = Path("/home/longhao/experiment/.worktrees/beliefkv-fast-resource-wait-20261115")
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.native_engine_variant import engine_patch
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts import run_causal_comparison as causal
from scripts import run_continuous_matched_campaign as campaign
from scripts.finish_model_work_oracle import verify_pins

load, write = campaign.load, campaign.write


def predecessor_ready(plan, *, alive=causal.reservation.alive):
    directory = Path(plan["materialization_campaign"])
    state = load(directory / "state.json")
    expected = plan["materialization_owner"]
    if any(state["process"].get(key) != expected[key] for key in ("pid", "start_ticks")):
        raise ValueError("the existing materialization owner changed")
    if state.get("status", "").startswith("stopped"):
        raise RuntimeError("preserve and investigate the preceding materialization campaign")
    if state.get("status") != "complete_four_matched_arms" or alive(expected):
        return False
    if state.get("completed_stages") != [
        "control", "pressure", "thunderagent_acting_decay", "native_matched",
    ]:
        raise ValueError("preceding campaign lacks its four complete arms")
    predecessor = load(directory / "plan.json")
    cohort_digest = sha256_file(Path(predecessor["cohort"]))
    for label in state["completed_stages"]:
        proof = load(directory / (label + "_execution_proof.json"))
        if (
            proof.get("actual_owned_exit_verified") is not True
            or proof.get("protocol") != "all_fixed_measured_workflows_completed"
            or proof.get("measured_requests") != 2991
            or proof.get("measured_output_tokens") != 362157
            or proof.get("cohort_sha256") != cohort_digest
        ):
            raise ValueError("preceding measured native work or exit is incomplete")
        registration = Path(predecessor["stages"][label]["registration"])
        certificate = Path(proof["certificate"])
        if (
            sha256_file(registration) != proof["registration_sha256"]
            or sha256_file(certificate) != proof["certificate_sha256"]
        ):
            raise ValueError("preceding completion evidence changed")
        result = load(certificate)
        if not all(result.get(key) is True for key in (
            "complete_measured_native_work_verified", "actual_owned_exit_verified",
            "fresh_idle_drain_verified",
        )):
            raise ValueError("preceding arm lacks complete work, fresh drain and real exit")
    return True


def apply_acceptance(module):
    original = module.evaluate_pair_cohorts

    def evaluate(*args, **kwargs):
        kwargs["minimum_jct_gain"] = .02
        return original(*args, **kwargs)

    module.evaluate_pair_cohorts = evaluate


def prepared_serving_parity():
    result = {}
    for label in ("oracle", "learned"):
        before, after = load(PREVIOUS_PREPARED / (label + ".json")), load(PREPARED / (label + ".json"))
        old, new = before["common_environment"], after["common_environment"]
        for name in ("WORKFLOW_WORK_MODEL_SHA256", "BELIEFKV_WORKFLOW_WORK_MODEL_SHA256"):
            if old[name] != new[name] or sha256_file(Path(new["WORKFLOW_WORK_MODEL"])) != new[name]:
                raise ValueError("fresh preparation changed the CPU-qualified work provider")
        if label == "learned":
            if (
                before["workflow_work_qualification"]["serving_artifact"] != old["WORKFLOW_WORK_MODEL"]
                or after["workflow_work_qualification"]["serving_artifact"] != new["WORKFLOW_WORK_MODEL"]
            ):
                raise ValueError("serving qualification names a different learned work provider")
            before["workflow_work_qualification"]["serving_artifact"] = (
                after["workflow_work_qualification"]["serving_artifact"]
            )
        for name in ("WORKFLOW_WORK_MODEL", "BELIEFKV_WORKFLOW_WORK_MODEL"):
            old[name] = new[name]
        if before != after:
            raise ValueError("fresh preparation changed more than its work-provider path")
        result[label] = {
            "work_provider_sha256": new["WORKFLOW_WORK_MODEL_SHA256"],
            "only_configuration_provider_path_changed": True,
        }
    return result


class RegisteredWatcher:
    def __init__(self, original, registration, prepared):
        self.original, self.registration, self.prepared = original, registration, prepared
        self.spawns = 0

    def __getattr__(self, name):
        return getattr(self.original, name)

    def Popen(self, command, **kwargs):
        frozen = Path(load(self.registration)["execution_checkout"])
        if command != [
            sys.executable, str(frozen / "scripts/run_causal_comparison.py"),
            "--watch", str(self.registration),
        ] or self.spawns:
            raise ValueError("refuse an unexpected or duplicate serving supervisor")
        self.prepared()
        self.spawns += 1
        return self.original.Popen([
            sys.executable, str(HERE / "supervisor.py"), "--watch", str(self.registration),
        ], **kwargs)


def watch():
    plan = load(QUEUE / "plan.json")
    verify_pins(plan["inputs_sha256"])
    owner = causal.reservation.process(os.getpid())
    write(QUEUE / "owner.json", owner)
    state = {
        "status": "preparing_frozen_Oracle_without_GPU",
        "process": owner, "required_average_JCT_reduction": .02,
        "GPU_model_started": False, "performance_accepted": False, "goal_complete": False,
    }

    def pulse():
        state["checked_at"] = datetime.now(timezone.utc).isoformat()
        write(QUEUE / "queue_state.json", state)

    def terminate(signum, _frame):
        raise KeyboardInterrupt("Oracle queue received signal " + str(signum))

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    supervisor = None
    try:
        with (QUEUE / "owner.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with (QUEUE / "plugin_install.log").open("x") as stream:
                subprocess.run([
                    sys.executable, "-m", "pip", "install", "--no-index", "--no-deps",
                    "--no-build-isolation", "--target", str(QUEUE / "prepared_plugin"),
                    str(ROOT / "configs/causal_replay_plugin"),
                ], cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=60)
            registration = Path(plan["stages"]["oracle"]["registration"])

            def prepared():
                write(QUEUE / "preparation.json", {
                    "registration_sha256": sha256_file(registration),
                    "frozen_serving_without_runtime_edits": True,
                    "fast_supervisor": plan["fast_supervisor"],
                })

            router = RegisteredWatcher(subprocess, registration, prepared)
            original_calls, original_argv = causal.subprocess, sys.argv
            captured = io.StringIO()
            try:
                causal.subprocess = router
                sys.argv = plan["stages"]["oracle"]["command"][1:]
                with contextlib.redirect_stdout(captured):
                    causal.main()
            finally:
                causal.subprocess, sys.argv = original_calls, original_argv
            if router.spawns != 1:
                raise ValueError("require exactly one real Oracle supervisor")
            result = json.loads(captured.getvalue())
            supervisor = result["watcher"]
            write(QUEUE / "registration_result.json", result)
            plan["stages"]["oracle"]["existing_watcher"] = supervisor
            for path in (
                registration, QUEUE / "preparation.json", QUEUE / "registration_result.json",
                *[item for item in (QUEUE / "prepared_plugin").rglob("*") if item.is_file()],
            ):
                plan["inputs_sha256"][str(path)] = sha256_file(path)
            write(QUEUE / "execution_plan.json", plan)
            state.update(
                status="waiting_existing_materialization_campaign",
                serving_supervisor=supervisor, GPU_registration_created=True,
            )
            pulse()
            while not predecessor_ready(plan):
                if not causal.reservation.alive(supervisor):
                    raise RuntimeError("queued Oracle supervisor exited before its dependency")
                pulse()
                time.sleep(.5)
            state["status"] = "following_materialization_actual_exit"
            pulse()
            campaign.LABELS, campaign.NAMES = ("full", "oracle"), ("Full", "Strict Oracle")
            _, ranking, _ = campaign.services(ROOT)
            apply_acceptance(ranking)
            campaign.watch(QUEUE / "execution_plan.json")
            outcome = load(QUEUE / "state.json")
            state.update(
                status=outcome["status"], measured_completed=outcome.get("measured_completed"),
                complete_review=outcome.get("completed_stages") == ["full", "oracle"],
            )
    except BaseException as error:
        state.update(status="stopped_preserve_Oracle_queue", error=repr(error))
        if supervisor and causal.reservation.alive(supervisor):
            os.kill(supervisor["pid"], signal.SIGTERM)
    finally:
        state["finished_at"] = datetime.now(timezone.utc).isoformat()
        pulse()


def launch():
    if QUEUE.exists():
        raise FileExistsError("preserve earlier queue attempts")
    causal.fresh._require_clean(ROOT)
    previous, cpu = load(BASE / "plan.json"), load(CPU)
    if not cpu["qualification_passed"] or cpu["candidate_commit"] != (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    ):
        raise ValueError("queue only the exact qualified strict-serving commit")
    causal.comparison.qualify_report(Path(cpu["report"]["path"]), Path(cpu["source_manifest"]["path"]))
    tests = ET.parse(HERE / "queue_cpu_r3.xml").getroot()
    if any(list(tests.iter(tag)) for tag in ("failure", "error", "skipped")):
        raise ValueError("queue CPU checks failed")
    if len(list(tests.iter("testcase"))) < 6:
        raise ValueError("require the queue ownership and two-percent acceptance checks")
    incoming = load(MATERIALIZATION / "state.json")
    if not causal.reservation.alive(incoming["process"]):
        raise ValueError("preserve the actual existing materialization queue")
    preparation = load(PREPARED / "preparation.json")
    verify_pins(preparation["inputs_sha256"])
    parity = prepared_serving_parity()
    QUEUE.mkdir()
    write(QUEUE / "prepared_serving_parity.json", parity)
    oracle = deepcopy(load(PREPARED / "oracle.json"))
    oracle["comparison_contract"]["minimum_promising_measured_mean_JCT_reduction"] = .02
    oracle["comparison_contract"]["tool_boundary_oracle_only_followup"] = True
    oracle["comparison_contract"]["two_percent_is_a_measured_acceptance_gate_not_a_guarantee"] = True
    write(QUEUE / "oracle.json", oracle)
    registration = Path("/home/longhao/experiment/ops/workflow_original133_host400_mean60_strict_oracle_20261116_r1.registration.json")
    run = Path("/home/longhao/experiment/BeliefKV/experiments/raw/workflow_original133_host400_mean60_strict_oracle_20261116_r1")
    command = campaign.command_with(previous["stages"]["oracle"]["command"], {
        "--registration": registration, "--run-root": run, "--port": 19085,
        "--experiment-config": QUEUE / "oracle.json",
        "--cpu-report": cpu["report"]["path"], "--cpu-sources": cpu["source_manifest"]["path"],
    })
    command[1] = str(ROOT / "scripts/run_causal_comparison.py")
    engine = Path(command[command.index("--engine") + 1])
    expected_tree = load(registration)["engine_tree"] if registration.exists() else (
        load(command[command.index("--engine-sources") + 1])["canonical_patch"]["actual_tree"]
    )
    native = git_source_state(engine, canonical_patch=engine_patch(ROOT, expected_tree))
    write(QUEUE / "native_sources.json", native)
    command[command.index("--engine-sources") + 1] = str(QUEUE / "native_sources.json")
    causal.engine_evidence(
        engine, Path(command[command.index("--engine-report") + 1]), QUEUE / "native_sources.json",
    )
    if registration.exists() or run.exists():
        raise FileExistsError("do not restart a registered or partial Oracle experiment")
    stages = deepcopy(previous["stages"])
    stages["full"].update(reuse_completed_execution=True)
    stages["oracle"].update(
        registration=str(registration), run_root=str(run), port=19085,
        configuration=str(QUEUE / "oracle.json"), command=command,
        review=str(QUEUE / "oracle_review"),
    )
    stages["oracle"].pop("existing_watcher", None)
    stages["learned"]["configuration"] = str(PREPARED / "learned.json")
    plan = {
        **previous, "operator": str(ROOT), "stages": stages,
        "configurations": {label: stage["configuration"] for label, stage in stages.items()},
        "GPU_stage_order": ["full", "oracle"], "required_average_JCT_reduction": .02,
        "previous_execution_plan": str(MATERIALIZATION / "plan.json"),
        "previous_coordinator": incoming["process"],
        "previous_coordinator_only_handoff": False, "device_access_mode": "shared",
        "materialization_campaign": str(MATERIALIZATION), "materialization_owner": incoming["process"],
        "require_ranked_gain_before_following_stages": True,
        "fast_supervisor": str(SOURCE.parent / "fast_start_r1/supervisor.py"),
        "same_133_Host400_mean60_fixed18_for_Oracle_and_completed_Full": True,
        "no_materialization_policy_or_tool_trace_duration_change": True,
        "GPU_model_started": False, "performance_accepted": False, "goal_complete": False,
    }
    plan.pop("held_reservation_anchor", None)
    inputs = [
        Path(__file__), HERE / "supervisor.py", HERE / "test_queue.py", HERE / "queue_cpu_r3.xml",
        CPU, PREPARED / "preparation.json", QUEUE / "oracle.json", QUEUE / "native_sources.json",
        QUEUE / "prepared_serving_parity.json",
        MATERIALIZATION / "plan.json", BASE / "complete_comparison_r2/comparison.json",
        Path(plan["fast_supervisor"]), HELPER / "beliefkv/experiments/fast_gpu_wait.py",
        HELPER / "scripts/hold_gpu_memory_between_runs.py",
    ]
    plan["inputs_sha256"] = {
        **preparation["inputs_sha256"],
        **{str(path.resolve()): sha256_file(path) for path in inputs},
    }
    write(QUEUE / "plan.json", plan)
    with (QUEUE / "coordinator.log").open("x") as stream:
        child = subprocess.Popen([
            sys.executable, str(Path(__file__).resolve()), "--watch",
        ], cwd=ROOT, env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999",
                         "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"},
            stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
            start_new_session=True)
    owner = causal.reservation.process(child.pid)
    write(QUEUE / "bootstrap_owner.json", owner)
    print(json.dumps({"watcher": owner, "GPU_model_started": False, "required_JCT_gain": .02}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or os.getpriority(os.PRIO_PROCESS, 0):
        raise ValueError("use a CUDA-hidden nice0 coordinator")
    if args.watch:
        watch()
    elif args.launch:
        launch()
    else:
        parser.error("explicit launch or watch is required")


if __name__ == "__main__":
    main()
