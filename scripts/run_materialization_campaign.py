"""Queue a standalone D3 run or an explicitly matched materialization campaign."""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import importlib.util
import io
import contextlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.subagent_materialization import PRESSURE_POLICIES
from scripts import run_causal_comparison as causal
from scripts.compare_reported_workflow_cohorts import WRITE_BACK_CAP_SETTING
from scripts.run_continuous_matched_campaign import command_with, verify_serving_owner, write
from scripts.finish_model_work_oracle import verify_pins, wait_observer_publication
from scripts.review_predictive_thunder_cohort import matching_serving_traffic

LABELS = ("control", "pressure", "thunderagent_acting_decay", "native_matched")
PRESSURE_ONLY = ("pressure",)
HELPER = Path("/home/longhao/experiment/.worktrees/beliefkv-fast-resource-wait-20261115")


def load(path):
    return json.loads(Path(path).read_bytes())


def canonical_work(work):
    return json.dumps(work, sort_keys=True, separators=(",", ":"))


def stage_labels(plan):
    labels = tuple(plan.get("GPU_stage_order", LABELS))
    if labels not in (LABELS, LABELS[:2], PRESSURE_ONLY) or (
        "GPU_stage_order" in plan and set(plan["stages"]) != set(labels)
    ):
        raise ValueError("select the four-arm campaign, matched Full pair, or pressure arm only")
    return labels


def uses_shared_gpu(plan):
    return plan.get("shared_gpu") is True or tuple(plan.get("GPU_stage_order", ())) == PRESSURE_ONLY


def matching_preparations(configurations, *, inherited_write_back_cap="1"):
    labels = next(
        (order for order in (LABELS, LABELS[:2]) if set(configurations) == set(order)), None,
    )
    if labels is None:
        raise ValueError("prepare the complete four-arm campaign or the matched Full pair")
    shared = None
    for label in labels:
        spec = configurations[label]
        expected_mode = label if label in LABELS[2:] else "full"
        if spec["order"] != [expected_mode]:
            raise ValueError("unexpected serving arm: " + label)
        common = spec["common_environment"]
        allowed_policies = PRESSURE_POLICIES if label == "pressure" else ("off",)
        if common["BELIEFKV_SUBAGENT_MATERIALIZATION"] not in allowed_policies:
            raise ValueError("materialization policy leaked across arms")
        if label in LABELS[:2] and (
            common["BELIEFKV_SUBAGENT_MATERIALIZATION"]
            != spec["comparison_contract"]["subagent_materialization_policy"]
            or common["BELIEFKV_SUBAGENT_MATERIALIZATION"] != spec["materialization_experiment"]["policy"]
        ):
            raise ValueError("materialization policy differs across the prepared declarations")
        identity = {
            name: spec[name] for name in (
                "source_registration", "tool_timing_overlay",
                "workflow_arrivals", "private_analysis_cohort",
            )
        }
        identity["resources"] = {
            name: common[name] for name in (
                "HICACHE_SIZE_GB", "MAX_RUNNING_REQUESTS", "DEVICE_FULL_TOKENS",
                "NATIVE_EXECUTION_CONFIG_SHA256",
            )
        }
        cap = max(1, int(common.get(WRITE_BACK_CAP_SETTING, inherited_write_back_cap)))
        identity["resources"][WRITE_BACK_CAP_SETTING] = cap
        if shared is not None:
            expected_cap = shared["resources"][WRITE_BACK_CAP_SETTING]
            if cap != expected_cap:
                raise ValueError(f"{label} native write-back cap {cap} differs from control cap {expected_cap}")
            if identity != shared:
                raise ValueError("prepared arms changed traffic, measurement or resources")
        shared = identity
    before, after = (deepcopy(configurations[label]) for label in LABELS[:2])
    for spec in (before, after):
        spec["common_environment"].pop("BELIEFKV_SUBAGENT_MATERIALIZATION")
        spec["comparison_contract"].pop("subagent_materialization_policy")
        spec["materialization_experiment"].pop("policy")
    if before != after:
        raise ValueError("Full pair changed more than initial KV materialization")
    return shared


def oracle_complete(comparison):
    if (
        comparison.get("complete_measured_work_fresh_drain_and_actual_exit_verified") is not True
        or comparison.get("complete_measured_requests") != 2991
        or comparison.get("complete_measured_output_tokens") != 362157
    ):
        raise ValueError("finish the original Oracle measured work and review first")
    verify_pins(comparison["inputs_sha256"])


class WatcherRouter:
    def __init__(self, original, registration, evidence, prepared):
        self.original, self.registration = original, Path(registration)
        self.evidence, self.prepared, self.spawns = evidence, prepared, 0

    def __getattr__(self, name):
        return getattr(self.original, name)

    def Popen(self, command, **kwargs):
        frozen = Path(load(self.registration)["execution_checkout"])
        expected = [
            sys.executable, str(frozen / "scripts/run_causal_comparison.py"),
            "--watch", str(self.registration),
        ]
        if command != expected:
            raise ValueError("intercept only the exact registered serving supervisor")
        self.spawns += 1
        if self.spawns != 1:
            raise ValueError("a stage may start only one supervisor")
        self.prepared()
        return self.original.Popen([
            sys.executable, str(frozen / "scripts/supervise_materialization_stage.py"),
            "--watch", str(self.registration), "--evidence-dir", str(self.evidence),
        ], **kwargs)


def register_stage(stage, plan, owner):
    evidence, registration = Path(stage["evidence"]), Path(stage["registration"])
    evidence.mkdir()
    write(evidence / "owner.json", owner)
    (evidence / "prepared_plugin").symlink_to(Path(plan["plugin"]), target_is_directory=True)

    def prepared():
        write(evidence / "preparation.json", {
            "registration_sha256": sha256_file(registration),
            "fast_supervisor": plan["fast_supervisor"],
            "shared_gpu": uses_shared_gpu(plan),
            "serving_runtime_changed_by_supervisor": False,
        })

    router = WatcherRouter(subprocess, registration, evidence, prepared)
    old_calls, old_argv = causal.subprocess, sys.argv
    output = io.StringIO()
    try:
        causal.subprocess = router
        sys.argv = stage["command"][1:]
        with contextlib.redirect_stdout(output):
            causal.main()
    finally:
        causal.subprocess, sys.argv = old_calls, old_argv
    if router.spawns != 1:
        raise ValueError("the registrar did not create the declared supervisor")
    result = json.loads(output.getvalue())
    write(evidence / "registration_result.json", result)
    return result["watcher"]


def observer_plan(stage, plan, serving):
    registration = Path(stage["registration"])
    frozen = Path(serving["execution_checkout"])
    cohort = Path(plan["cohort"])
    return {
        "schema_version": 1, "comparison": str(registration), "cohort": str(cohort),
        "execution_checkout": str(frozen), "code_commit": serving["code_commit"],
        "passing_CPU_cases": serving["passing_cpu_cases"], "poll_seconds": 2,
        "minimum_model_work_p80_p20": plan["minimum_model_work_p80_p20"],
        "output_dir": str(Path(stage["run_root"]) / "cohort_termination"),
        "analysis_output": stage["review"],
        "inputs_sha256": {str(path): sha256_file(path) for path in (
            registration, cohort, frozen / "scripts/stop_after_measured_cohort.py",
            frozen / "scripts/verify_measured_execution.py",
        )},
        "GPU_followup_registered": False, "performance_accepted": False,
    }


def helper_module():
    spec = importlib.util.spec_from_file_location(
        "materialization_resource_wait", HELPER / "beliefkv/experiments/fast_gpu_wait.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def completed_stage_proofs(plan):
    """Reuse only a verified completed prefix, preserving every original run."""
    labels = stage_labels(plan)
    saved = plan.get("completed_stage_proofs", {})
    if not isinstance(saved, dict) or set(saved) != set(labels[:len(saved)]):
        raise ValueError("completed stages must be an ordered campaign prefix")
    if not saved:
        return {}
    from scripts.verify_measured_execution import verify_execution, review_matches_execution

    proofs = {}
    for label in labels[:len(saved)]:
        stage = plan["stages"][label]
        previous = load(saved[label])
        fresh = verify_execution(Path(stage["registration"]), Path(plan["cohort"]))
        if any(previous.get(name) != fresh.get(name) for name in (
            "protocol", "registration_sha256", "cohort_sha256", "certificate_sha256",
            "actual_owned_exit_verified", "measured_requests", "measured_output_tokens",
        )) or canonical_work(previous.get("work")) != canonical_work(fresh["work"]):
            raise ValueError("completed stage proof changed: " + label)
        review_matches_execution(load(Path(stage["review"]) / "review.json"), fresh)
        proofs[label] = fresh
    return proofs


def watch(plan_path):
    plan, output = load(plan_path), plan_path.parent
    labels = stage_labels(plan)
    shared_gpu = uses_shared_gpu(plan)
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or os.getpriority(os.PRIO_PROCESS, 0):
        raise ValueError("require a CUDA-hidden nice0 CPU coordinator")
    if (output / "state.json").exists():
        raise FileExistsError("preserve previous queue outcomes")
    owner = causal.reservation.process(os.getpid())
    state = {
        "status": "verifying_inputs" if labels == PRESSURE_ONLY else "verifying_completed_Oracle",
        "process": owner,
        "GPU_stage_order": list(labels), "completed_stages": [],
        "shared_gpu": shared_gpu,
        "GPU_registered_stages": [], "performance_accepted": False, "goal_complete": False,
    }

    def pulse():
        state["checked_at"] = datetime.now(timezone.utc).isoformat()
        write(output / "state.json", state)

    def tick():
        pulse()
        time.sleep(.5)

    def stop(signum, _frame):
        raise KeyboardInterrupt("campaign received signal " + str(signum))

    def cpu(name, arguments):
        with (output / (name + ".log")).open("x") as stream:
            child = subprocess.Popen([
                "taskset", "-c", "0", "nice", "-n", "19", sys.executable, *map(str, arguments),
            ], cwd=ROOT, env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999",
                            "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"},
                stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT)
            state["CPU_child"] = causal.reservation.process(child.pid)
            while child.poll() is None:
                tick()
        state.pop("CPU_child", None)
        if child.returncode:
            raise RuntimeError("preserve failed CPU review: " + name)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    device = None
    proofs = {}
    try:
        verify_pins(plan["inputs_sha256"])
        if labels != PRESSURE_ONLY:
            oracle_complete(load(plan["preceding_Oracle_comparison"]))
        proofs = completed_stage_proofs(plan)
        for label, proof in proofs.items():
            path = output / (label + "_execution_proof.json")
            if not path.exists():
                write(path, proof)
        state["completed_stages"] = list(proofs)
        if not shared_gpu:
            module = helper_module()
            device = module.NvmlDevice()
        with (output / "campaign.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for label in labels:
                if label in proofs:
                    continue
                stage = plan["stages"][label]
                state.update(status="waiting_resources_" + label, current_stage=label,
                             measured_completed=None, observed_arrivals=None)
                if device is not None:
                    ignored = set()
                    for previous in plan["stages"].values():
                        for path in Path(previous["evidence"]).glob("bridge_*.state.json"):
                            bridge = load(path)
                            if bridge.get("status") == "holding" and (
                                all(bridge["owner"].get(key) == owner[key] for key in ("pid", "start_ticks"))
                                and causal.reservation.alive(bridge["process"])
                            ):
                                ignored.add(bridge["process"]["pid"])
                    wait = module.FastWait(
                        device.snapshot,
                        lambda port: module.machine_resources(port, ROOT, causal.reservation.owned_runs),
                        ignored=lambda: ignored,
                    )
                    wait(stage, state, pulse=pulse)
                    # Release only this campaign's preceding bridges after a fresh free-device check.
                    for previous in plan["stages"].values():
                        for path in Path(previous["evidence"]).glob("bridge_*.control.json"):
                            bridge = load(path)
                            if all(bridge["owner"].get(key) == owner[key] for key in ("pid", "start_ticks")):
                                write(path, {**bridge, "release": True})
                verify_pins(plan["inputs_sha256"])
                state["status"] = "registering_" + label
                pulse()
                supervisor = register_stage(stage, plan, owner)
                state.update(serving_watcher=supervisor, status="waiting_actual_replay_" + label)
                state["GPU_registered_stages"].append(label)
                registration = Path(stage["registration"])
                started = None
                while True:
                    if not causal.reservation.alive(supervisor):
                        raise RuntimeError("supervisor exited before replay: " + label)
                    path = registration.with_suffix(".state.json")
                    observed = load(path) if path.exists() else {}
                    if observed:
                        supervisor = verify_serving_owner(
                            supervisor, observed.get("watcher", {}), registration,
                        )
                        for name in ("resources", "fast_resource_wait"):
                            if name in observed:
                                state[name] = observed[name]
                    if observed.get("status") == "running_" + stage["arm"] and all(
                        observed.get(key) and causal.reservation.alive(observed[key])
                        for key in ("watcher", "server", "driver")
                    ):
                        break
                    status = observed.get("status", "")
                    if status.startswith(("stopped", "interrupted", "completed")):
                        raise RuntimeError("serving stopped before replay: " + str(observed.get("error")))
                    started = None if status.startswith("waiting_") else started or time.monotonic()
                    if started is not None and time.monotonic() - started > 1800:
                        raise TimeoutError("actual serving startup timed out")
                    state["serving_status"] = status
                    tick()
                serving = load(registration)
                observer_path = registration.with_name(
                    registration.name.replace(".registration.json", ".cohort.registration.json")
                )
                if observer_path.exists():
                    raise FileExistsError("preserve an earlier cohort observer")
                write(observer_path, observer_plan(stage, plan, serving))
                frozen = Path(serving["execution_checkout"])
                with observer_path.with_suffix(".log").open("x") as stream:
                    child = subprocess.Popen([
                        "taskset", "-c", "0", "nice", "-n", "19", sys.executable,
                        str(frozen / "scripts/stop_after_measured_cohort.py"),
                        "--watch", str(observer_path),
                    ], cwd=frozen, env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999",
                                       "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"},
                        stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                        start_new_session=True)
                observer = causal.reservation.process(child.pid)
                state.update(status="waiting_fixed_18_" + label, cohort_observer=observer)
                wait_observer_publication(observer_path.with_suffix(".state.json"), observer, pulse)
                while causal.reservation.alive(observer):
                    observed = load(observer_path.with_suffix(".state.json"))
                    state.update(measured_completed=observed.get("measured_completed"),
                                 observed_arrivals=observed.get("observed_arrivals"))
                    tick()
                if load(observer_path.with_suffix(".state.json"))["status"] != "measured_cohort_review_complete":
                    raise RuntimeError("preserve failed measured-cohort review")
                proof_path = output / (label + "_execution_proof.json")
                cpu(label + "_execution_verification", [
                    "-c",
                    "from pathlib import Path\n"
                    "import sys\n"
                    "from scripts.verify_measured_execution import verify_execution,review_matches_execution\n"
                    "from scripts.train_workflow_work import write\n"
                    "from scripts.run_causal_comparison import load\n"
                    "p=verify_execution(Path(sys.argv[1]),Path(sys.argv[2]))\n"
                    "review_matches_execution(load(Path(sys.argv[3])),p)\n"
                    "write(Path(sys.argv[4]),p)\n",
                    registration, plan["cohort"], Path(stage["review"]) / "review.json", proof_path,
                ])
                proofs[label] = load(proof_path)
                if labels != PRESSURE_ONLY:
                    if canonical_work(proofs[label]["work"]) != canonical_work(proofs["control"]["work"]):
                        raise ValueError("complete measured native work changed")
                    matching_serving_traffic(load(plan["stages"]["control"]["registration"]), serving)
                state["completed_stages"].append(label)
                state.update(status="complete_" + label, measured_completed=18)
                pulse()
                if label == "pressure" and labels == LABELS:
                    cpu("materialization_comparison", [
                        ROOT / "scripts/review_materialization_campaign.py",
                        "--plan", plan_path, "--output", output / "materialization_comparison",
                    ])
            if labels == LABELS:
                cpu("all_four_comparisons", [
                    ROOT / "scripts/review_materialization_campaign.py",
                    "--plan", plan_path, "--output", output / "all_four_comparisons", "--baselines",
                ])
                state.update(status="complete_four_matched_arms", measured_completed=18)
            elif labels == LABELS[:2]:
                cpu("materialization_comparison", [
                    ROOT / "scripts/review_materialization_campaign.py",
                    "--plan", plan_path, "--output", output / "materialization_comparison",
                ])
                state.update(status="complete_materialization_pair", measured_completed=18)
            else:
                state.update(status="complete_materialization_pressure", measured_completed=18)
    except BaseException as error:
        state.update(status="stopped_preserve_materialization_campaign", error=repr(error))
        supervisor = state.get("serving_watcher")
        if supervisor and causal.reservation.alive(supervisor):
            os.kill(supervisor["pid"], signal.SIGTERM)
    finally:
        if device is not None:
            device.close()
        state["finished_at"] = datetime.now(timezone.utc).isoformat()
        pulse()


def prepare(args):
    if args.output.exists():
        raise FileExistsError("preserve earlier campaign evidence")
    causal.fresh._require_clean(ROOT)
    labels = LABELS
    if getattr(args, "pressure_only", False):
        labels = PRESSURE_ONLY
    elif getattr(args, "pair_only", False):
        labels = LABELS[:2]
    if labels != PRESSURE_ONLY:
        if args.oracle_comparison is None:
            raise ValueError("a matched materialization campaign requires --oracle-comparison")
        oracle_complete(load(args.oracle_comparison))
    prepared = load(args.prepared / "preparation.json")
    preparation_labels = LABELS[:2] if labels == PRESSURE_ONLY else labels
    configurations = {}
    pins = {
        str(path.resolve()): sha256_file(path)
        for path in (
            args.prepared / "preparation.json",
            args.cpu_report, args.cpu_sources, args.template_plan,
            Path(__file__), ROOT / "scripts/supervise_materialization_stage.py",
            ROOT / "scripts/review_materialization_campaign.py",
            args.fast_supervisor, HELPER / "beliefkv/experiments/fast_gpu_wait.py",
            HELPER / "scripts/hold_gpu_memory_between_runs.py",
        )
    }
    if args.oracle_comparison is not None:
        pins[str(args.oracle_comparison.resolve())] = sha256_file(args.oracle_comparison)
    for label in preparation_labels:
        binding = prepared["artifacts"][label]
        if sha256_file(Path(binding["path"])) != binding["sha256"]:
            raise ValueError("prepared configuration changed")
        configurations[label] = load(binding["path"])
        pins[binding["path"]] = binding["sha256"]
    template_plan = load(args.template_plan)
    template_labels = ("full", "pressure", "oracle") if labels == PRESSURE_ONLY else ("oracle",)
    template_stages = template_plan["stages"]
    template = next((
        template_stages[label]["command"] for label in template_labels if label in template_stages
    ), None)
    if template is None:
        raise ValueError("the template plan has no compatible serving command")
    reference_parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    reference_parser.add_argument("--reference", type=Path, required=True)
    reference_args, _ = reference_parser.parse_known_args(template[2:])
    reference_path = (ROOT / reference_args.reference).resolve()
    pins[str(reference_path)] = sha256_file(reference_path)
    reference_environment = load(reference_path)["arms"]["full"]
    shared = matching_preparations(
        configurations, inherited_write_back_cap=reference_environment.get(WRITE_BACK_CAP_SETTING, "1"),
    )
    for name in ("workflow_arrivals", "private_analysis_cohort", "tool_timing_overlay"):
        binding = shared[name]
        if sha256_file(Path(binding["path"])) != binding["sha256"]:
            raise ValueError("shared protocol changed")
        pins[binding["path"]] = binding["sha256"]
    causal.comparison.qualify_report(args.cpu_report, args.cpu_sources)
    args.output.mkdir(parents=True)
    plugin = args.output / "prepared_plugin"
    with (args.output / "plugin_install.log").open("x") as stream:
        subprocess.run([
            sys.executable, "-m", "pip", "install", "--no-index", "--no-deps",
            "--no-build-isolation", "--target", str(plugin),
            str(ROOT / "configs/causal_replay_plugin"),
        ], cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=60)
    pins.update({str(path.resolve()): sha256_file(path) for path in plugin.rglob("*") if path.is_file()})
    stages = {}
    for index, label in enumerate(labels):
        stem = "workflow_original133_host200_mean55_materialization_" + label + "_" + args.suffix
        registration = Path("/home/longhao/experiment/ops") / (stem + ".registration.json")
        run = Path("/home/longhao/experiment/BeliefKV/experiments/raw") / stem
        if registration.exists() or run.exists():
            raise FileExistsError("stage already has evidence: " + label)
        configuration = prepared["artifacts"][label]["path"]
        arm = configurations[label]["order"][0]
        command = command_with(template, {
            "--registration": registration, "--run-root": run, "--port": 19081 + index,
            "--cpu-report": args.cpu_report, "--cpu-sources": args.cpu_sources,
            "--experiment-config": configuration, "--arms": arm,
            "--workflow-arrivals": shared["workflow_arrivals"]["path"],
        })
        command[1] = str(ROOT / "scripts/run_causal_comparison.py")
        stages[label] = {
            "registration": str(registration), "run_root": str(run), "arm": arm,
            "port": 19081 + index, "configuration": configuration, "command": command,
            "review": str(args.output / (label + "_review")),
            "evidence": str(args.output / (label + "_supervision")),
        }
    plan = {
        "schema_version": 1, "operator": str(ROOT), "stages": stages,
        "GPU_stage_order": list(labels), "plugin": str(plugin),
        "shared_gpu": getattr(args, "shared_gpu", False) or labels == PRESSURE_ONLY,
        "fast_supervisor": str(args.fast_supervisor),
        "cohort": shared["private_analysis_cohort"]["path"],
        "minimum_model_work_p80_p20": template_plan["minimum_model_work_p80_p20"],
        "inputs_sha256": pins,
        "initial_workflows": 20, "workflows": 133, "mean_interval_seconds": 55.,
        "Host_GB": 200, "reuse_unchanged_exact_token_and_sampling_GPU_evidence": True,
        "actual_capacity_and_native_execution_checked_at_serving_start": True,
        "fixed18_full_JCT_and_fresh_drain_and_actual_exit_required": True,
        "no_foreign_process_termination": True, "goal_complete": False,
        "performance_accepted": False,
    }
    if args.oracle_comparison is not None:
        plan["preceding_Oracle_comparison"] = str(args.oracle_comparison)
    write(args.output / "plan.json", plan)
    with (args.output / "campaign.log").open("x") as stream:
        child = subprocess.Popen([
            sys.executable, str(Path(__file__).resolve()), "--watch",
            str(args.output / "plan.json"),
        ], cwd=ROOT, env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999",
                         "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"},
            stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
            start_new_session=True)
    owner = causal.reservation.process(child.pid)
    write(args.output / "owner.json", owner)
    return owner


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", type=Path)
    for name in (
        "prepared", "oracle-comparison", "cpu-report", "cpu-sources",
        "template-plan", "fast-supervisor", "output",
    ):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--suffix", default="20261116_r1")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--pair-only", action="store_true")
    mode.add_argument("--pressure-only", action="store_true")
    parser.add_argument("--shared-gpu", action="store_true")
    args = parser.parse_args()
    if args.watch:
        watch(args.watch.resolve())
    else:
        for key, value in vars(args).items():
            if key not in ("watch", "suffix", "pair_only", "pressure_only", "shared_gpu"):
                if value is None:
                    if args.pressure_only and key == "oracle_comparison":
                        continue
                    parser.error("provide all preparation, qualification and output paths")
                setattr(args, key, value.resolve())
        if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or os.getpriority(os.PRIO_PROCESS, 0):
            raise ValueError("queue from CUDA-hidden nice0 CPU coordination")
        print(json.dumps({"watcher": prepare(args), "GPU_model_started": False}))


if __name__ == "__main__":
    main()
