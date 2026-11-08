"""Launch one declared tool-band pressure oracle with the existing fixed cohort."""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.native_engine_variant import engine_patch
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from beliefkv.runtime.workflow_work_oracle import (
    CONTINUOUS_COMBINATION_WORK_KIND, CONTINUOUS_PRESSURE_WORK_KIND, TOOL_BAND_PRESSURE_ORDER,
    WorkflowWorkOracleArtifact,
)
from scripts import finish_model_work_oracle as finish
from scripts import run_causal_comparison as causal
from scripts.train_workflow_work import write

MAIN = Path("/home/longhao/experiment/BeliefKV")
OPS = MAIN.parent / "ops"
STEM = "workflow_original83_host400_mean70_tool_band_pressure_oracle_20261108_r1"
REG = OPS / (STEM + ".registration.json")
RUN = MAIN / "experiments/raw" / STEM
PORT = 19033


def require_release():
    causal.fresh.check_hold(
        causal.load(OPS / "v128_eos_seeded_modules_resume_20261022.registration.json"),
        released=True,
    )


def verify_pressure_extension(previous, truth):
    if (
        previous.get("kind") != CONTINUOUS_COMBINATION_WORK_KIND
        or truth.get("kind") != CONTINUOUS_PRESSURE_WORK_KIND
        or truth.get("admission_order") != "r5_combination_and_tool_band_pressure"
        or truth.get("pressure_work_order") != TOOL_BAND_PRESSURE_ORDER
    ):
        raise ValueError("require the explicit continuous-work tool-band pressure extension")
    allowed = {"kind", "admission_order", "pressure_work_order"}
    keys = previous.keys() | truth.keys()
    if any(previous.get(name) != truth.get(name) for name in keys - allowed):
        raise ValueError("pressure extension changed work, training reference or ordinary selector")


def prepare(args):
    output = args.output_dir.resolve()
    if output.exists() or REG.exists() or RUN.exists():
        raise FileExistsError("preserve previous execution attempts")
    causal.fresh._require_clean(ROOT)
    evidence, previous = (causal.load(path) for path in (args.cpu_evidence, args.previous_execution))
    if (
        evidence.get("returncode") != 0 or evidence.get("failed_error_skipped_cases") != 0
        or evidence.get("all_retained_literal_identities_present") is not True
        or evidence.get("sources_unchanged_during_execution") is not True
    ):
        raise ValueError("pressure oracle has not passed source-bound CPU qualification")
    causal.comparison.verify_cpu_snapshot(ROOT, causal.load(Path(evidence["source_manifest"]["path"])))
    config = causal.load(args.configuration)
    artifact_path = Path(config["common_environment"]["WORKFLOW_WORK_MODEL"])
    truth = causal.load(artifact_path)
    WorkflowWorkOracleArtifact(truth)
    prior_reg = causal.load(Path(previous["registration"]))
    prior_config = causal.load(Path(prior_reg["experiment_configuration"]))
    prior_artifact = Path(prior_config["common_environment"]["WORKFLOW_WORK_MODEL"])
    verify_pressure_extension(causal.load(prior_artifact), truth)
    learned_reg = causal.load(Path(previous["reference"]["learned"]["registration"]))
    learned = causal.load(Path(learned_reg["experiment_configuration"]))
    model_path = Path(learned["common_environment"]["WORKFLOW_WORK_MODEL"])
    finish.verify_same_objective(
        learned, config, causal.load(model_path), truth, allow_tool_band_pressure=True,
    )
    prior = causal.load(Path(previous["registration"]).with_suffix(".state.json"))
    if any(causal.reservation.alive(owner) for owner in (
        prior["watcher"], prior["server"], prior["driver"], *prior.get("owned", ()),
    )):
        raise ValueError("the previous experiment still owns a process")
    prior_observer = causal.load(Path(previous["registration"]).with_name(
        Path(previous["registration"]).name.replace(".registration.json", ".cohort.registration.json")
    ))
    observed = causal.load(Path(previous["registration"]).with_name(
        Path(previous["registration"]).name.replace(".registration.json", ".cohort.registration.json")
    ).with_suffix(".state.json"))
    if observed.get("status") != "measured_cohort_review_complete":
        raise ValueError("previous continuous-work oracle has no complete cohort review")
    require_release()
    if not causal.qualified.resources(PORT)["ready"] or os.path.lexists(f"/tmp/bkv-causal-{PORT}.sock"):
        raise ValueError("GPU, memory, port or socket is occupied")
    command = list(previous["command"])
    engine = Path(command[command.index("--engine") + 1])
    old_native_path = Path(command[command.index("--engine-sources") + 1])
    old_native = causal.load(old_native_path)
    native = git_source_state(engine, canonical_patch=engine_patch(
        ROOT, old_native["canonical_patch"]["actual_tree"],
    ))

    def without_path(value):
        result = deepcopy(value)
        result.pop("state_sha256")
        result["canonical_patch"].pop("path")
        return result

    if without_path(native) != without_path(old_native):
        raise ValueError("native changes exceed the canonical proof pathname")
    output.mkdir(parents=True, exist_ok=False)
    native_path = output / "native_sources.json"
    write(native_path, native)
    causal.engine_evidence(
        engine, Path(command[command.index("--engine-report") + 1]), native_path,
    )
    command[1] = str(ROOT / "scripts/run_causal_comparison.py")
    for flag, value in {
        "--registration": REG, "--run-root": RUN, "--port": PORT,
        "--cpu-report": evidence["report"]["path"],
        "--cpu-sources": evidence["source_manifest"]["path"],
        "--experiment-config": args.configuration.resolve(), "--engine-sources": native_path,
    }.items():
        command[command.index(flag) + 1] = str(value)
    reference = {
        **previous["reference"],
        "continuous_truth": {
            "registration": previous["registration"], "review": prior_observer["analysis_output"],
        },
    }
    paths = [
        args.previous_execution, args.cpu_evidence, args.configuration, artifact_path,
        model_path, prior_artifact, Path(evidence["report"]["path"]),
        Path(evidence["source_manifest"]["path"]), native_path, old_native_path,
        Path(previous["cohort"]), Path(__file__).resolve(),
    ]
    write(output / "execution_plan.json", {
        "prepared_at": datetime.now(timezone.utc).isoformat(), "operator": str(ROOT),
        "command": command, "registration": str(REG), "run_root": str(RUN),
        "cohort": previous["cohort"], "reference": reference,
        "minimum_model_work_p80_p20": previous["minimum_model_work_p80_p20"],
        "only_new_GPU_stage": "continuous_model_work_within_tool_band_pressure",
        "pressure_work_order": TOOL_BAND_PRESSURE_ORDER,
        "ordinary_combination_selector_unchanged": True,
        "pure_prediction_error_comparison": False,
        "continuous_training_reference": truth["combination_work_reference"],
        "GPU_followup_registered": False, "goal_complete": False,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in paths},
    })
    return output / "execution_plan.json"


def watch(path):
    execution, output = causal.load(path), path.parent
    state = {
        "status": "registering_tool_band_pressure_oracle",
        "process": causal.reservation.process(os.getpid()), "goal_complete": False,
    }

    def pulse():
        state["checked_at"] = datetime.now(timezone.utc).isoformat()
        causal.reservation.write_json(output / "execution_state.json", state)

    try:
        finish.verify_pins(execution["inputs_sha256"])
        require_release()
        pulse()
        result = subprocess.run(execution["command"], cwd=ROOT, capture_output=True, text=True, timeout=240)
        write(output / "registration_result.json", {
            "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
        })
        if result.returncode:
            raise RuntimeError("pressure oracle registration refused; preserve the result")
        state["serving_watcher"] = json.loads(result.stdout)["watcher"]
        deadline = time.monotonic() + 1200.
        while True:
            if REG.with_suffix(".state.json").exists():
                serving = causal.load(REG.with_suffix(".state.json"))
                if serving["status"] == "running_full" and all(
                    causal.reservation.alive(serving[name]) for name in ("watcher", "server", "driver")
                ):
                    break
                if serving["status"].startswith(("stopped", "interrupted", "completed")):
                    raise RuntimeError("pressure oracle stopped before replay")
            if not causal.reservation.alive(state["serving_watcher"]) or time.monotonic() >= deadline:
                raise RuntimeError("pressure supervisor exited or startup exceeded 20 minutes")
            state["status"] = "waiting_actual_pressure_oracle_replay"
            pulse()
            time.sleep(5.)
        registration = causal.load(REG)
        frozen = Path(registration["execution_checkout"])
        observer_path = REG.with_name(REG.name.replace(".registration.json", ".cohort.registration.json"))
        write(observer_path, {
            "schema_version": 1, "comparison": str(REG), "cohort": execution["cohort"],
            "execution_checkout": str(frozen), "code_commit": registration["code_commit"],
            "passing_CPU_cases": registration["passing_cpu_cases"], "poll_seconds": 2,
            "minimum_model_work_p80_p20": execution["minimum_model_work_p80_p20"],
            "output_dir": str(RUN / "cohort_termination"),
            "analysis_output": str(output / "oracle_review"),
            "inputs_sha256": {str(part): sha256_file(part) for part in (
                REG, Path(execution["cohort"]), frozen / "scripts/stop_after_measured_cohort.py",
                frozen / "scripts/verify_measured_execution.py",
            )}, "GPU_followup_registered": False, "performance_accepted": False,
        })
        with observer_path.with_suffix(".log").open("x") as stream:
            observer = subprocess.Popen(
                ["taskset", "-c", "0", "nice", "-n", "19", sys.executable,
                 str(frozen / "scripts/stop_after_measured_cohort.py"), "--watch", str(observer_path)],
                cwd=frozen, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        owner = causal.reservation.process(observer.pid)
        state.update(status="waiting_fixed_18_tool_band_pressure_oracle", cohort_observer=owner)
        pulse()
        cpu = output / "complete_cpu_r1"
        cpu.mkdir(exist_ok=False)
        write(cpu / "plan.json", {
            "execution_plan": str(path), "analysis_root": str(ROOT),
            "cohort_observer": owner, "GPU_action": False,
            "inputs_sha256": {
                str(part): sha256_file(part) for part in (
                    path, REG, observer_path, Path(execution["cohort"]),
                    ROOT / "scripts/finish_model_work_oracle.py",
                    ROOT / "scripts/plot_workflow_cohort_comparison.py",
                    ROOT / "scripts/audit_workflow_combination_choices.py",
                    ROOT / "beliefkv/policy/workflow_combination.py",
                )
            },
        })
        os.setpriority(os.PRIO_PROCESS, 0, 19)
        os.sched_setaffinity(0, {0})
        finish.watch(cpu / "plan.json")
        if causal.load(cpu / "state.json")["status"] != "complete_model_work_oracle_CPU_analysis":
            raise RuntimeError("preserve the failed pressure oracle CPU analysis")
        state.update(status="complete_tool_band_pressure_oracle", measured_completed=18)
    except BaseException as error:
        state.update(status="stopped_preserve_tool_band_pressure_oracle", error=repr(error))
    pulse()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous-execution", type=Path)
    parser.add_argument("--cpu-evidence", type=Path)
    parser.add_argument("--configuration", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--watch", type=Path)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or os.getpriority(os.PRIO_PROCESS, 0) != 0:
        raise ValueError("start the coordinator at nice0 with CUDA hidden")
    if args.watch:
        watch(args.watch.resolve())
        return
    if any(value is None for value in (
        args.previous_execution, args.cpu_evidence, args.configuration, args.output_dir,
    )):
        parser.error("provide previous execution, bound CPU evidence, configuration and output")
    plan_path = prepare(args)
    with (plan_path.parent / "execution.log").open("x") as stream:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--watch", str(plan_path)],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    print(json.dumps({"plan": str(plan_path), "process": causal.reservation.process(process.pid)}))


if __name__ == "__main__":
    main()
