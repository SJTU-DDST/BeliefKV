"""Conditionally continue a completed capacity Oracle with the prepared tool-path method."""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


DIRECTORY_ENV = "BELIEFKV_TOOL_PATH_FOLLOWUP_DIRECTORY"
FINAL = {
    "oracle_complete_no_promising_gain",
    "oracle_complete_separate_learned_preparation_required",
}


def load(path):
    return json.loads(Path(path).read_bytes())


def write(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_pins(pins):
    for name, digest in pins.items():
        if sha(name) != digest:
            raise ValueError("followup input changed: " + name)


def module_at(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def same_owner(expected, actual):
    return bool(
        actual and actual.get("state") not in ("Z", "X")
        and all(actual.get(name) == expected.get(name) for name in ("pid", "start_ticks", "argv"))
    )


def review_ready(state, expected):
    if any(state["process"].get(name) != expected.get(name) for name in ("pid", "start_ticks")):
        raise ValueError("the preceding review owner changed")
    if state.get("status") in FINAL:
        if state.get("measured_completed") != 18:
            raise ValueError("all18 natural completions are required")
        return True
    if state.get("status", "").startswith("stopped"):
        raise ValueError("preceding review failed")
    return False


def promising(comparison, choices, *, allow_tail_diagnostic=False):
    checks = comparison["checks"]
    return (
        checks["fixed_measured_mean_JCT_gain"] is True
        and checks["common_window_output_throughput_noninferior"] is True
        and (checks["background_covers_both_measured_tails"] is True or allow_tail_diagnostic)
        and choices.get("complete_measured_execution_verified") is True
        and choices["counts"]["selected_set_changes"] > 0
        and choices["counts"]["newly_selected_with_bounded_useful_service"] > 0
    )


def choice_command(command, root, stage, control, cohort, learned_reference):
    original = str(root / "scripts/audit_workflow_combination_choices.py")
    if original not in command:
        return command
    if command.count(original) != 1 or "--complete" not in command:
        raise ValueError("require one complete Oracle choice audit")
    result = list(command)
    for flag, expected in (
        ("--registration", stage["registration"]), ("--control", control),
        ("--cohort", cohort), ("--policy-kind", "oracle"),
        ("--model-work-reference", learned_reference),
    ):
        if result.count(flag) != 1 or result[result.index(flag) + 1] != expected:
            raise ValueError("the audit changed the declared method or fixed work: " + flag)
    result[result.index(original)] = str(root / "scripts/audit_ranked_oracle_service.py")
    for flag in ("--policy-kind", "--model-work-reference"):
        index = result.index(flag)
        del result[index:index + 2]
    return result


class AuditCalls:
    def __init__(self, original, root, plan):
        self.original, self.root, self.plan = original, root, plan

    def __getattr__(self, name):
        return getattr(self.original, name)

    def Popen(self, command, **kwargs):
        command = choice_command(
            command, self.root, self.plan["stages"]["oracle"],
            self.plan["stages"]["full"]["registration"], self.plan["cohort"],
            self.plan["stages"]["learned"]["configuration"],
        )
        return self.original.Popen(command, **kwargs)


def make_plan(previous, ready, output, configuration):
    if (
        previous["stages"]["full"].get("reuse_completed_execution") is not True
        or ready["scope"].get("same_fixed133_initial20_mean60_Host400_fixed18") is not True
        or ready["previous_Oracle_registration"] != previous["stages"]["oracle"]["registration"]
        or ready.get("registrar_was_called") is not False
    ):
        raise ValueError("require the unchanged, unregistered prepared followup and completed Full")
    plan = deepcopy(previous)
    stage = plan["stages"]["oracle"]
    stage.update(
        configuration=str(configuration), registration=ready["registration"],
        run_root=ready["run_root"], review=str(output / "oracle_review"),
        existing_watcher=None, port=19078, command=list(ready["register_command"]),
    )
    stage.pop("reuse_completed_execution", None)
    command = stage["command"]
    if command.count("--experiment-config") != 1:
        raise ValueError("require one explicit experiment configuration")
    command[command.index("--experiment-config") + 1] = str(configuration)
    previous_directory = Path(ready["previous_existing_queue"]).parent
    plan.update(
        previous_execution_plan=str(previous_directory / "execution_plan.json"),
        previous_registration=ready["previous_Oracle_registration"],
        previous_memory_resident_owner=str(previous_directory / "resident_owner.json"),
        source_repair="global_ready_remaining_model_tool_critical_path",
        only_next_GPU_stage="oracle",
        additional_boundary_preemption_rolled_back=True,
        original_predictive_tool_replacement_preserved=True,
        global_tool_path_method=True,
        no_automatic_learned_GPU_stage=True,
        require_ranked_gain_before_following_stages=True,
    )
    plan["configurations"]["oracle"] = str(configuration)
    return plan


def followup_configuration(prepared):
    result = deepcopy(prepared)
    result["comparison_contract"].update(
        tool_boundary_oracle_only_followup=True,
        legacy_oracle_only_flag_does_not_enable_tool_boundary_preemption=True,
        oracle_only_followup_reason="one_global_tool_path_method_then_review_before_learned",
    )
    return result


def wait_previous(plan, directory, owner, reservation, *, completed):
    previous = Path(plan["previous_execution_plan"])
    resident = Path(plan["previous_memory_resident_owner"])
    while True:
        actual = reservation.process(owner["pid"])
        if not same_owner(owner, actual):
            raise ValueError("the live preceding CPU owner exited or changed identity")
        failure = previous.parent / "bootstrap_error.json"
        if failure.exists():
            raise ValueError("the preceding launch failed: " + str(load(failure)))
        if previous.exists():
            earlier = load(previous)
            if (
                earlier["stages"]["oracle"]["registration"] != plan["previous_registration"]
                or earlier["stages"]["full"]["registration"]
                != plan["stages"]["full"]["registration"]
                or earlier["cohort"] != plan["cohort"]
                or earlier["qualified_commit"] != plan["qualified_commit"]
            ):
                raise ValueError("the preceding published execution plan changed")
            if not completed:
                return earlier
            state_path = previous.parent / "state.json"
            if state_path.exists() and review_ready(load(state_path), owner) and resident.exists():
                holding = load(resident)
                if (
                    holding.get("status") != "holding_memory_bridge_between_experiments"
                    or Path(holding["release_file"]).exists()
                    or not same_owner(holding["owner"], actual)
                ):
                    raise ValueError("the completed owner did not retain the declared idle bridge")
                return load(state_path)
        write(directory / "continuation_wait.json", {
            "status": "waiting_previous_complete_fixed18_review" if completed
            else "waiting_previous_execution_plan_publication",
            "process": reservation.process(os.getpid()), "previous_owner": actual,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "GPU_registration_created": False, "goal_complete": False,
        })
        time.sleep(2)


def coordinate(directory):
    intent, plan = load(directory / "launch_intent.json"), load(directory / "plan.json")
    verify_pins(intent["inputs_sha256"])
    verify_pins(plan["inputs_sha256"])
    root = Path(plan["operator"])
    previous_owner = intent["previous_coordinator"]
    queue = module_at("tool_path_adoption_queue", Path(intent["adapter_source"]) / "capacity_queue.py")
    causal, campaign, handoff = queue.causal, queue.base.campaign, queue.base.handoff
    wait_previous(plan, directory, previous_owner, causal.reservation, completed=False)
    queue.HERE = queue.base.HERE = handoff.HERE = directory
    handoff.await_current = lambda previous: wait_previous(
        plan, directory, previous_owner, causal.reservation, completed=True,
    )
    campaign.LABELS = ("full", "oracle")
    campaign.stage_promising = promising
    campaign.subprocess = AuditCalls(subprocess, root, plan)
    legacy_supervisor = queue.SOURCE / "supervisor.py"
    original_watcher = queue.base.BaseWatcher

    class ToolPathWatcher(original_watcher):
        def __init__(self, original, registration, supervisor, prepared):
            if Path(supervisor) != legacy_supervisor:
                raise ValueError("unexpected inherited supervisor")

            def declared_preparation():
                prepared()
                path = directory / "preparation.json"
                record = load(path)
                record["global_tool_path_method"] = True
                record["inputs_sha256"].update(intent["inputs_sha256"])
                write(path, record)

            super().__init__(original, registration, Path(__file__), declared_preparation)

        def Popen(self, command, **kwargs):
            kwargs["env"] = {**kwargs.get("env", os.environ), DIRECTORY_ENV: str(directory)}
            return super().Popen(command, **kwargs)

    queue.QueueWatcher = ToolPathWatcher
    queue.watch()


def supervise(registration, directory):
    plan = load(directory / "plan.json")
    bounded = Path(plan["tool_path_adapter_source"]).parent
    adapter = module_at("tool_path_fast_supervisor", bounded / "fast_start_r1/supervisor.py")
    transfer = module_at("tool_path_bridge_transfer", bounded / "bridge_handoff_r1/transfer.py")
    adapter.HERE, adapter.__file__ = directory, str(Path(__file__).resolve())
    adapter.Bridge = transfer.adopted_factory(adapter.Bridge, load(directory / "bridge_transfer.json"))
    adapter.watch(registration)


def launch(args):
    directory = args.output_dir.resolve()
    prepared = args.preparation_dir.resolve()
    previous_directory = args.previous_queue_dir.resolve()
    if directory.exists():
        raise FileExistsError("preserve previous followup launch state")
    evidence = load(prepared / "preparation.json")
    ready = load(prepared / "followup_readiness.json")
    verify_pins({**evidence["inputs_sha256"], **evidence["outputs_sha256"], **ready["inputs_sha256"]})
    root = Path(evidence["operator"])
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    if commit != evidence["operator_commit"] or subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=root, text=True,
    ).strip():
        raise ValueError("the qualified operator changed")
    report = ET.parse(args.cpu_report).getroot()
    totals = {
        name: sum(int(item.get(name, 0)) for item in report.iter("testsuite"))
        for name in ("tests", "failures", "errors", "skipped")
    }
    if totals["tests"] < 12 or any(totals[name] for name in ("failures", "errors", "skipped")):
        raise ValueError("require the complete focused CPU launcher checks")
    previous = load(previous_directory / "plan.json")
    owner = load(previous_directory / "bootstrap_owner.json")
    if Path(ready["previous_existing_queue"]) != previous_directory / "plan.json":
        raise ValueError("followup was prepared for another preceding queue")
    configuration = followup_configuration(load(prepared / "oracle.json"))
    directory.mkdir(parents=True)
    configuration_path = directory / "oracle.json"
    write(configuration_path, configuration)
    plan = make_plan(previous, ready, directory, configuration_path)
    adapter_source = prepared.parent.parent
    plan["tool_path_adapter_source"] = str(adapter_source)
    if any(Path(plan["stages"]["oracle"][name]).exists() for name in ("registration", "run_root")):
        raise ValueError("the tool-path method was already registered")
    paths = (
        Path(__file__).resolve(), args.cpu_report.resolve(),
        Path(__file__).resolve().parents[1] / "tests/test_tool_path_followup.py",
        prepared / "preparation.json", prepared / "followup_readiness.json",
        previous_directory / "plan.json", previous_directory / "bootstrap_owner.json",
        adapter_source / "capacity_queue.py", adapter_source / "capacity_coordinator.py",
        adapter_source / "coordinate.py", configuration_path,
    )
    plan["inputs_sha256"].update({str(path): sha(path) for path in paths})
    verify_pins(plan["inputs_sha256"])
    write(directory / "plan.json", plan)
    write(directory / "launch_intent.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "previous_coordinator": owner, "adapter_source": str(adapter_source),
        "previous_promising_result_skips_followup": True,
        "only_next_GPU_stage": "one_global_tool_path_oracle",
        "new_GPU_registration_before_previous_complete_review": False,
        "reuse_completed_Full": True,
        "inputs_sha256": {
            **plan["inputs_sha256"], str(directory / "plan.json"): sha(directory / "plan.json"),
        },
    })
    sys.path.insert(0, str(root))
    from scripts import run_causal_comparison as causal
    from scripts.review_matched_work_ranking import verify_ranking_only_pair
    verify_ranking_only_pair(load(Path(plan["stages"]["full"]["configuration"])), configuration)
    if not same_owner(owner, causal.reservation.process(owner["pid"])):
        raise ValueError("the preceding queue is not actually live")
    with (directory / "coordinator.log").open("x") as stream:
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--coordinate", "--output-dir", str(directory)],
            cwd=root, env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999"},
            stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True,
        )
    process = causal.reservation.process(child.pid)
    write(directory / "bootstrap_owner.json", process)
    return process


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--coordinate", action="store_true")
    parser.add_argument("--watch", type=Path)
    for name in ("preparation-dir", "previous-queue-dir", "output-dir", "cpu-report"):
        parser.add_argument("--" + name, type=Path)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or os.getpriority(os.PRIO_PROCESS, 0) != 0:
        raise ValueError("keep coordination nice0 and CUDA hidden")
    if args.watch:
        supervise(args.watch, Path(os.environ[DIRECTORY_ENV]))
    elif args.coordinate:
        try:
            coordinate(args.output_dir)
        except BaseException as error:
            write(args.output_dir / "bootstrap_error.json", {
                "error": repr(error), "observed_at": datetime.now(timezone.utc).isoformat(),
                "goal_complete": False,
            })
            raise
    elif args.launch:
        print(json.dumps(launch(args)), flush=True)
    else:
        parser.error("choose launch, coordinate or watch")


if __name__ == "__main__":
    main()
