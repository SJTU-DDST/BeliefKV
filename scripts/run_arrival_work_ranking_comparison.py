"""Prepare and run one matched Full/continuous-truth/R5 arrival comparison."""

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
from scripts import finish_model_work_oracle as finish
from scripts import run_causal_comparison as causal
from scripts.prepare_arrival_rate_followup import (
    completion_coverage_screen, followup_configuration, followup_protocol,
)
from scripts.review_learned_work_ranking import verify_learned_combination_pair
from scripts.review_matched_work_ranking import evaluate_pair_cohorts
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution, review_matches_execution

MAIN = Path("/home/longhao/experiment/BeliefKV")
OPS = MAIN.parent / "ops"
LABELS = ("full", "oracle", "learned")
NAMES = {"full": "Nonranking Full", "oracle": "Continuous-work Oracle", "learned": "Learned R5"}


def released():
    causal.fresh.check_hold(
        causal.load(OPS / "v128_eos_seeded_modules_resume_20261022.registration.json"), released=True,
    )


def prepare(reference_path, output, mean):
    if output.exists():
        raise FileExistsError("preserve previous arrival preparations")
    reference = causal.load(reference_path)
    refs = {
        label: reference["reference"][{"oracle": "continuous_truth"}.get(label, label)]
        for label in LABELS
    }
    regs = {label: causal.load(Path(ref["registration"])) for label, ref in refs.items()}
    original = {
        label: causal.load(Path(reg["experiment_configuration"])) for label, reg in regs.items()
    }
    arrivals, cohort = (
        causal.load(Path(regs["full"]["workflow_arrivals_path"])),
        causal.load(Path(reference["cohort"])),
    )
    following, analysis = followup_protocol(arrivals, cohort, mean_interval_seconds=mean)
    output.mkdir(parents=True, exist_ok=False)
    arrival_path, cohort_path = output / "arrivals.json", output / "analysis_cohort.json"
    write(arrival_path, following)
    write(cohort_path, analysis)
    candidates = {
        label: followup_configuration(spec, arrival_path, cohort_path)
        for label, spec in original.items()
    }
    verify_learned_combination_pair(candidates["full"], candidates["learned"])
    model_path = Path(candidates["learned"]["common_environment"]["WORKFLOW_WORK_MODEL"])
    truth_path = Path(candidates["oracle"]["common_environment"]["WORKFLOW_WORK_MODEL"])
    finish.verify_same_objective(
        candidates["learned"], candidates["oracle"], causal.load(model_path), causal.load(truth_path),
    )
    inputs = [
        reference_path, Path(reference["cohort"]), Path(regs["full"]["workflow_arrivals_path"]),
        model_path, truth_path, Path(__file__), ROOT / "scripts/prepare_arrival_rate_followup.py",
    ]
    coverage, configurations = {}, {}
    for label in LABELS:
        path = output / (label + ".json")
        write(path, candidates[label])
        configurations[label] = str(path)
        review = Path(refs[label]["review"]) / "cohort.json"
        coverage[label] = completion_coverage_screen(
            arrivals, cohort, causal.load(review), following,
        )
        inputs.extend((Path(refs[label]["registration"]), review, path))
    inputs.extend((arrival_path, cohort_path))
    plan = {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "reference_execution": str(reference_path.resolve()),
        "configurations": configurations, "cohort": str(cohort_path.resolve()),
        "arrivals": str(arrival_path.resolve()), "mean_interval_seconds": mean,
        "initial_workflows": following["initial_workflows"], "workflows": len(following["arrival_schedule"]),
        "fixed_measured_workflows": analysis["measured_workflow_ids"],
        "static_completion_coverage_screen": coverage,
        "minimum_model_work_p80_p20": reference["minimum_model_work_p80_p20"],
        "GPU_stage_order": list(LABELS), "GPU_action": False,
        "tool_prefetch_enabled": False, "pressure_work_order_extension_used": False,
        "source_tasks_tokens_tools_models_and_nonarrival_controls_preserved": True,
        "static_screen_is_not_new_JCT_or_ready_supply_prediction": True,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in inputs},
    }
    write(output / "preparation.json", plan)
    return plan


def stage_command(template, stage, preparation, cpu, native_path):
    command = list(template)
    command[1] = str(ROOT / "scripts/run_causal_comparison.py")
    for flag, value in {
        "--registration": stage["registration"], "--run-root": stage["run_root"],
        "--port": stage["port"], "--experiment-config": stage["configuration"],
        "--workflow-arrivals": preparation["arrivals"],
        "--cpu-report": cpu["report"]["path"], "--cpu-sources": cpu["source_manifest"]["path"],
        "--engine-sources": native_path,
    }.items():
        command[command.index(flag) + 1] = str(value)
    return command


def start(preparation_path, cpu_path, output, *, run_suffix="20261109_r1"):
    if output.exists():
        raise FileExistsError("preserve previous executions")
    if not run_suffix or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_" for char in run_suffix):
        raise ValueError("run suffix requires lowercase letters, digits and underscores")
    causal.fresh._require_clean(ROOT)
    prep, cpu = causal.load(preparation_path), causal.load(cpu_path)
    finish.verify_pins(prep["inputs_sha256"])
    if prep.get("mean_interval_seconds") != 60. or prep.get("workflows") != 83 or (
        prep.get("initial_workflows") != 20
    ):
        raise ValueError("this execution identity requires 83 workflows, initial20 and mean60")
    if any(row["candidate_schedule_covers_static_tail"] is not True for row in (
        prep["static_completion_coverage_screen"].values()
    )):
        raise ValueError("shortened finite arrival table does not cover the static measured tails")
    if (
        cpu.get("returncode") != 0 or cpu.get("failed_error_skipped_cases") != 0
        or cpu.get("all_retained_literal_identities_present") is not True
        or cpu.get("sources_unchanged_during_execution") is not True
    ):
        raise ValueError("require passing source-bound CPU evidence")
    causal.comparison.verify_cpu_snapshot(ROOT, causal.load(Path(cpu["source_manifest"]["path"])))
    previous = causal.load(Path(prep["reference_execution"]))
    prior_state = causal.load(Path(previous["registration"]).with_suffix(".state.json"))
    if any(causal.reservation.alive(owner) for owner in (
        prior_state["watcher"], prior_state["server"], prior_state["driver"], *prior_state.get("owned", ()),
    )):
        raise ValueError("the previous GPU experiment still owns a live process")
    previous_reg = causal.load(Path(previous["registration"]))
    certificate = causal.load(Path(previous_reg["run_root"]) / "cohort_termination/completion.json")
    if any(certificate.get(name) is not True for name in (
        "complete_measured_native_work_verified", "no_measured_workflow_truncated",
        "fresh_idle_drain_verified", "actual_owned_exit_verified",
    )):
        raise ValueError("previous experiment lacks complete measured work and owned exit")
    released()
    template = previous["command"]
    engine = Path(template[template.index("--engine") + 1])
    old_path = Path(template[template.index("--engine-sources") + 1])
    old = causal.load(old_path)
    native = git_source_state(engine, canonical_patch=engine_patch(ROOT, old["canonical_patch"]["actual_tree"]))
    old_copy, new_copy = deepcopy(old), deepcopy(native)
    for item in (old_copy, new_copy):
        item.pop("state_sha256")
        item["canonical_patch"].pop("path")
    if old_copy != new_copy:
        raise ValueError("native engine changed beyond its canonical patch pathname")
    stages = {}
    for index, label in enumerate(LABELS):
        port = 19035 + index
        stem = f"workflow_original83_host400_mean60_{label}_{run_suffix}"
        reg, run = OPS / (stem + ".registration.json"), MAIN / "experiments/raw" / stem
        if reg.exists() or run.exists() or os.path.lexists(f"/tmp/bkv-causal-{port}.sock"):
            raise FileExistsError("preserve an existing registration, run or runtime socket")
        resources = causal.qualified.resources(port)
        if not resources["idle_port"]:
            raise ValueError("comparison port is occupied")
        stages[label] = {
            "registration": str(reg), "run_root": str(run), "port": port,
            "configuration": prep["configurations"][label],
            "review": str(output.resolve() / (label + "_review")),
            "initial_resource_snapshot": resources,
        }
    output.mkdir(parents=True, exist_ok=False)
    native_path = output / "native_sources.json"
    write(native_path, native)
    causal.engine_evidence(engine, Path(template[template.index("--engine-report") + 1]), native_path)
    for label, stage in stages.items():
        stage["command"] = stage_command(template, stage, prep, cpu, native_path)
    pins = [
        preparation_path, cpu_path, Path(cpu["report"]["path"]),
        Path(cpu["source_manifest"]["path"]), Path(__file__), native_path,
    ]
    plan_path = output / "execution_plan.json"
    write(plan_path, {
        **prep, "operator": str(ROOT), "stages": stages, "GPU_action": True,
        "run_suffix": run_suffix,
        "inputs_sha256": {
            **prep["inputs_sha256"], **{str(path.resolve()): sha256_file(path) for path in pins},
        },
    })
    with (output / "execution.log").open("x") as stream:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--watch", str(plan_path)],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return {"execution_plan": str(plan_path), "process": causal.reservation.process(process.pid)}


def watch(plan_path):
    plan, output = causal.load(plan_path), plan_path.parent
    state = {
        "status": "starting_arrival_comparison", "process": causal.reservation.process(os.getpid()),
        "GPU_registered_stages": [],
    }

    def pulse():
        state["checked_at"] = datetime.now(timezone.utc).isoformat()
        causal.reservation.write_json(output / "state.json", state)

    def wait():
        released()
        pulse()
        time.sleep(5.)

    def cpu(name, arguments):
        with (output / (name + ".log")).open("x") as stream:
            child = subprocess.Popen(
                ["taskset", "-c", "0", "nice", "-n", "19", sys.executable, *map(str, arguments)],
                cwd=ROOT, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
            )
            while child.poll() is None:
                wait()
        if child.returncode:
            raise RuntimeError("CPU analysis failed; preserve " + name)

    try:
        for label in LABELS:
            stage = plan["stages"][label]
            reg_path = Path(stage["registration"])
            state.update(status="waiting_resources_" + label, current_stage=label)
            while not causal.qualified.resources(stage["port"])["ready"]:
                wait()
            finish.verify_pins(plan["inputs_sha256"])
            released()
            launched = subprocess.run(stage["command"], cwd=ROOT, capture_output=True, text=True, timeout=240)
            write(output / (label + "_registration_result.json"), {
                "returncode": launched.returncode, "stdout": launched.stdout, "stderr": launched.stderr,
            })
            if launched.returncode:
                raise RuntimeError("preserve rejected GPU registration: " + label)
            state["GPU_registered_stages"].append(label)
            owner = json.loads(launched.stdout)["watcher"]
            deadline = time.monotonic() + 1200.
            state.update(status="waiting_actual_replay_" + label, serving_watcher=owner)
            while True:
                if reg_path.with_suffix(".state.json").exists():
                    serving = causal.load(reg_path.with_suffix(".state.json"))
                    if serving["status"] == "running_full" and all(
                        causal.reservation.alive(serving[name]) for name in ("watcher", "server", "driver")
                    ):
                        break
                    if serving["status"].startswith(("stopped", "completed", "interrupted")):
                        raise RuntimeError("stage stopped before replay: " + label)
                if not causal.reservation.alive(owner) or time.monotonic() >= deadline:
                    raise RuntimeError("serving supervisor exited or startup timed out")
                wait()
            reg = causal.load(reg_path)
            frozen = Path(reg["execution_checkout"])
            observer_path = reg_path.with_name(reg_path.name.replace(".registration.json", ".cohort.registration.json"))
            write(observer_path, {
                "schema_version": 1, "comparison": str(reg_path), "cohort": plan["cohort"],
                "execution_checkout": str(frozen), "code_commit": reg["code_commit"],
                "passing_CPU_cases": reg["passing_cpu_cases"], "poll_seconds": 2,
                "minimum_model_work_p80_p20": plan["minimum_model_work_p80_p20"],
                "output_dir": str(Path(stage["run_root"]) / "cohort_termination"),
                "analysis_output": stage["review"],
                "inputs_sha256": {str(path): sha256_file(path) for path in (
                    reg_path, Path(plan["cohort"]), frozen / "scripts/stop_after_measured_cohort.py",
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
            observer_owner = causal.reservation.process(observer.pid)
            state.update(status="waiting_fixed_18_" + label, cohort_observer=observer_owner)
            finish.wait_observer_publication(observer_path.with_suffix(".state.json"), observer_owner, pulse)
            while causal.reservation.alive(observer_owner):
                observed = causal.load(observer_path.with_suffix(".state.json"))
                state.update(
                    measured_completed=observed.get("measured_completed"),
                    observed_arrivals=observed.get("observed_arrivals"),
                )
                wait()
            observed = causal.load(observer_path.with_suffix(".state.json"))
            if observed["status"] != "measured_cohort_review_complete":
                raise RuntimeError("fixed measured cohort did not complete: " + str(observed.get("error")))
            proof_path = output / (label + "_execution_proof.json")
            cpu(label + "_execution_proof", [
                "-c",
                "from pathlib import Path\n"
                "import sys\n"
                "from scripts.verify_measured_execution import verify_execution, review_matches_execution\n"
                "from scripts.run_causal_comparison import load\n"
                "from scripts.train_workflow_work import write\n"
                "proof = verify_execution(Path(sys.argv[1]), Path(sys.argv[2]))\n"
                "review_matches_execution(load(Path(sys.argv[3])), proof)\n"
                "write(Path(sys.argv[4]), proof)\n",
                reg_path, plan["cohort"], Path(stage["review"]) / "review.json", proof_path,
            ])
            proof = causal.load(proof_path)
            if label == "full":
                control_proof = proof
                if observed.get("accepted_for_oracle_diagnostic") is not True:
                    state.update(status="control_workload_requires_review", failed_checks=observed.get("failed_checks"))
                    pulse()
                    return
            else:
                if proof["work"] != control_proof["work"]:
                    raise ValueError("complete fixed measured native work differs between methods")
                comparison = evaluate_pair_cohorts(
                    causal.load(Path(plan["stages"]["full"]["review"]) / "cohort.json"),
                    causal.load(Path(stage["review"]) / "cohort.json"),
                    causal.load(Path(plan["configurations"][label]))["comparison_contract"],
                    minimum_jct_gain=.01,
                )
                write(output / (label + "_comparison.json"), {
                    **comparison, "complete_measured_work_fresh_drain_and_actual_exit": True,
                    "one_run_does_not_establish_repeatability": True, "GPU_action": False,
                })
        state["status"] = "plotting_complete_arrival_comparison"
        cpu("figures", [
            ROOT / "scripts/plot_workflow_cohort_comparison.py",
            *[value for label in LABELS for value in (
                "--report", NAMES[label] + "=" + str(Path(plan["stages"][label]["review"]) / "cohort.json"),
            )], "--output-dir", output / "figures",
        ])
        cpu("paused_agents", [
            ROOT / "scripts/plot_paused_agent_timeline.py", "--cohort", plan["cohort"],
            *[value for label in LABELS for value in (
                "--report", NAMES[label] + "=" + plan["stages"][label]["registration"],
            )], "--output-dir", output / "paused_agents",
        ])
        state.update(status="complete_arrival_work_ranking_comparison", measured_completed=18)
    except BaseException as error:
        state.update(status="stopped_preserve_arrival_work_ranking_comparison", error=repr(error))
    pulse()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-execution", type=Path)
    parser.add_argument("--mean-interval-seconds", type=float, default=60.)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--preparation", type=Path)
    parser.add_argument("--cpu-evidence", type=Path)
    parser.add_argument("--watch", type=Path)
    parser.add_argument("--run-suffix", default="20261109_r1")
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("hide CUDA in the coordinator; the serving launcher binds its own device")
    if args.watch:
        watch(args.watch)
    elif args.preparation and args.cpu_evidence and args.output_dir:
        if os.getpriority(os.PRIO_PROCESS, 0) != 0:
            raise ValueError("keep future serving parents at nice0")
        print(json.dumps(start(
            args.preparation, args.cpu_evidence, args.output_dir, run_suffix=args.run_suffix,
        )))
    elif args.reference_execution and args.output_dir:
        print(json.dumps(prepare(args.reference_execution, args.output_dir, args.mean_interval_seconds)))
    else:
        parser.error("provide a reference and output, a preparation with CPU evidence, or --watch")


if __name__ == "__main__":
    main()
