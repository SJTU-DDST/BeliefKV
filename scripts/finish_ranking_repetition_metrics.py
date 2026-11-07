"""Finish CPU metrics and figures after an existing repetition and its GPU hold settle."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent / "ops"))
import run_background83_frozen_replication_20261107 as repeat


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def hold_verified(hold, marker, campaign):
    return (
        hold.get("status") == "user_requested_GPU_hold_after_frozen_repetition"
        and hold.get("automatic_resume_allowed") is False
        and hold.get("resume_requires_explicit_user_release") is True
        and hold.get("post_repetition_hold", {}).get("campaign") == str(campaign)
        and marker.get("campaign") == str(campaign)
        and marker.get("automatic_resume_allowed") is False
        and marker.get("resume_requires_explicit_user_release") is True
    )


def readiness(parent_alive, source_alive, source_state, comparisons, held):
    if parent_alive or source_alive:
        return "waiting_registered_owners"
    if source_state.get("status") != "complete_repeated_choice_and_path_audits" or (
        set(source_state.get("completed_methods", ())) != {"oracle", "learned"}
    ):
        return "incomplete_repetition_preserved"
    if set(comparisons) != {"oracle", "learned"} or any(
        result.get("complete_measured_work_fresh_drain_and_actual_exit") is not True
        for result in comparisons.values()
    ):
        return "incomplete_repetition_preserved"
    return "ready" if held else "waiting_GPU_hold_proof"


def require_cpu():
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or os.getpriority(os.PRIO_PROCESS, 0) != 19:
        raise ValueError("postprocessing requires hidden CUDA and nice19")


def register(campaign_path, service_path, hold_request, output):
    require_cpu()
    campaign_path, service_path, hold_request, output = (
        path.resolve() for path in (campaign_path, service_path, hold_request, output)
    )
    if output.exists():
        raise FileExistsError("preserve earlier CPU postprocessing")
    campaign, service, request = (
        repeat.load(path) for path in (campaign_path, service_path, hold_request)
    )
    parent_state = repeat.load(campaign_path.with_suffix(".state.json"))
    service_state = repeat.load(service_path.parent / "state.json")
    guard_state = repeat.load(hold_request.with_suffix(".state.json"))
    owners = [parent_state["process"], service_state["process"], guard_state["process"]]
    if not all(repeat.causal.reservation.alive(owner) for owner in owners):
        raise ValueError("attach only to the actual live campaign, service observer and hold guard")
    if (
        service["parent"] != str(campaign_path) or request["campaign"] != str(campaign_path)
        or set(campaign["stages"]) != {"full", "oracle", "learned"}
    ):
        raise ValueError("CPU postprocessing must describe one unchanged registered repetition")
    repeat.campaign.verify_pins(campaign["inputs_sha256"])
    repeat.campaign.verify_pins(service["inputs_sha256"])
    analysis = Path(service["analysis_root"])
    paths = [
        campaign_path, service_path, hold_request, Path(__file__).resolve(),
        analysis / "scripts/audit_measured_request_interactivity.py",
        analysis / "scripts/plot_workflow_cohort_comparison.py",
    ]
    output.mkdir(parents=True, exist_ok=False)
    plan_path = output / "plan.json"
    repeat.write(plan_path, {
        "registered_at": timestamp(), "campaign": str(campaign_path),
        "service_plan": str(service_path), "hold_request": str(hold_request),
        "analysis_root": str(analysis), "campaign_process": owners[0],
        "service_process": owners[1], "hold_guard_process": owners[2],
        "hold_file": str(ROOT.parent / "ops/gpu_hold_until_20261022_0600.json"),
        "pause_marker": "/tmp/beliefkv-experiments.paused",
        "negative_complete_comparisons_postprocessed_too": True,
        "GPU_action": False, "GPU_followup_registered": False,
        "inputs_sha256": {str(path): repeat.campaign.sha256_file(path) for path in paths},
    })
    with (output / "watch.log").open("x") as stream:
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--watch", str(plan_path)],
            cwd=analysis, env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999",
                              "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
                              "MKL_NUM_THREADS": "1"},
            stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    print(json.dumps({
        "plan": str(plan_path), "process": repeat.causal.reservation.process(child.pid),
        "GPU_action": False,
    }), flush=True)


def watch(plan_path):
    require_cpu()
    plan, output = repeat.load(plan_path), plan_path.parent
    campaign = repeat.load(Path(plan["campaign"]))
    service_path = Path(plan["service_plan"])
    campaign_path = Path(plan["campaign"])
    state_path = output / "state.json"
    if state_path.exists():
        raise FileExistsError("preserve a previous postprocessing execution")
    state = {
        "status": "waiting_registered_owners", "process": repeat.causal.reservation.process(os.getpid()),
        "GPU_action": False, "goal_complete": False,
    }

    def pulse():
        state["checked_at"] = timestamp()
        repeat.causal.reservation.write_json(state_path, state)

    def cpu(name, command):
        repeat.campaign.verify_pins(plan["inputs_sha256"])
        with (output / (name + ".log")).open("x") as stream:
            child = subprocess.Popen(
                command, cwd=plan["analysis_root"], stdin=subprocess.DEVNULL,
                stdout=stream, stderr=subprocess.STDOUT,
            )
            state.update(status="running_" + name, CPU_child=repeat.causal.reservation.process(child.pid))
            while child.poll() is None:
                pulse()
                time.sleep(2.)
        if child.returncode:
            raise RuntimeError("preserve failed CPU postprocessing: " + name)

    try:
        while True:
            repeat.campaign.verify_pins(plan["inputs_sha256"])
            service_state = repeat.load(service_path.parent / "state.json")
            comparisons = {}
            for label in ("oracle", "learned"):
                path = Path(campaign["execution_directory"]) / (label + "_comparison.json")
                if path.exists() and path.read_bytes().endswith(b"\n"):
                    comparisons[label] = repeat.load(path)
            hold_path, marker_path = Path(plan["hold_file"]), Path(plan["pause_marker"])
            held = hold_verified(
                repeat.load(hold_path),
                repeat.load(marker_path) if marker_path.exists() else {},
                campaign_path,
            )
            state["status"] = readiness(
                repeat.causal.reservation.alive(plan["campaign_process"]),
                repeat.causal.reservation.alive(plan["service_process"]),
                service_state, comparisons, held,
            )
            pulse()
            if state["status"] == "incomplete_repetition_preserved":
                return
            if state["status"] == "ready":
                break
            if state["status"] == "waiting_GPU_hold_proof" and not repeat.causal.reservation.alive(
                plan["hold_guard_process"],
            ):
                raise ValueError("hold guard exited without the requested hold proof; preserve the reservation")
            time.sleep(10.)
        for result in comparisons.values():
            repeat.campaign.verify_pins(result["inputs_sha256"])
        analysis = Path(plan["analysis_root"])
        learned = campaign["stages"]["learned"]
        metrics_path = output / "learned_interactivity.json"
        cpu("learned_interactivity", [
            sys.executable, str(analysis / "scripts/audit_measured_request_interactivity.py"),
            "--registration", learned["registration"], "--cohort", campaign["cohort"],
            "--output", str(metrics_path),
        ])
        metrics = repeat.load(metrics_path)
        if metrics["complete_measured_execution_verified"] is not True or (
            metrics["pooled_requests"]["requests"] != campaign["measured_requests_per_stage"]
            or metrics["measured_workflows"] != campaign["measured_workflows"]
        ):
            raise ValueError("postprocessing lost fixed complete measured work")
        figure_dir = Path(campaign["execution_directory"]) / "figures"
        expected_reports = {
            str((Path(campaign["stages"][label]["review"]) / "cohort.json").resolve()):
                repeat.campaign.sha256_file(Path(campaign["stages"][label]["review"]) / "cohort.json")
            for label in ("full", "oracle", "learned")
        }
        if figure_dir.exists():
            provenance = repeat.load(figure_dir / "provenance.json")
            if any(provenance["inputs_sha256"].get(path) != value for path, value in expected_reports.items()):
                raise ValueError("existing figures describe different complete measured reports")
            repeat.campaign.verify_pins(provenance["inputs_sha256"])
            state["existing_complete_figures_reused"] = True
        else:
            figure_dir = output / "figures"
            labels = {"full": "Nonranking Full", "oracle": "Oracle", "learned": "Learned R5"}
            command = [sys.executable, str(analysis / "scripts/plot_workflow_cohort_comparison.py")]
            for label in ("full", "oracle", "learned"):
                command.extend(("--report", labels[label] + "=" + str(
                    Path(campaign["stages"][label]["review"]) / "cohort.json",
                )))
            command.extend(("--output-dir", str(figure_dir)))
            cpu("complete_figures", command)
        if not hold_verified(
            repeat.load(Path(plan["hold_file"])), repeat.load(Path(plan["pause_marker"])), campaign_path,
        ):
            raise ValueError("metrics completed but the GPU hold changed; preserve results for review")
        state.update(
            status="complete_repetition_metrics_and_hold_verified",
            learned_interactivity=str(metrics_path), complete_figures=str(figure_dir),
            complete_negative_results_preserved=True, requested_GPU_hold_verified=True,
            GPU_hold_verified_at=timestamp(),
            original_and_repeated_targets={
                label: repeat.load(Path(campaign["execution_directory"]) / (label + "_repeatability.json"))[
                    "both_pass"
                ] for label in ("oracle", "learned")
            },
        )
    except BaseException as error:
        state.update(status="stopped_preserve_repetition_postprocessing", error=repr(error))
    pulse()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", type=Path)
    for name in ("campaign", "service-plan", "hold-request", "output-dir"):
        parser.add_argument("--" + name, type=Path)
    args = parser.parse_args()
    if args.watch is not None:
        if any(value is not None for value in (
            args.campaign, args.service_plan, args.hold_request, args.output_dir,
        )):
            parser.error("watch a single registered CPU plan")
        watch(args.watch.resolve())
    elif any(value is None for value in (
        args.campaign, args.service_plan, args.hold_request, args.output_dir,
    )):
        parser.error("registration requires the campaign, service plan, hold request and output directory")
    else:
        register(args.campaign, args.service_plan, args.hold_request, args.output_dir)


if __name__ == "__main__":
    main()
