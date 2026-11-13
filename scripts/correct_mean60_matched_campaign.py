"""Replace the stopped wrong-rate campaign with the requested mean60 comparison."""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import run_continuous_matched_campaign as campaign

DISPLAY_NAMES = (
    "Nonranking Full", "Continuous-work Oracle", "Learned R5", "ThunderAgent", "SGLang native",
)


def validate_mean60(plan):
    arrivals = campaign.load(plan["arrivals"])
    cohort = campaign.load(plan["cohort"])
    if (
        plan.get("mean_interval_seconds") != 60.
        or plan.get("initial_workflows") != 20
        or not math.isclose(arrivals.get("rate_per_second", 0.), 1. / 60., rel_tol=1e-12)
        or arrivals.get("initial_workflows") != 20
        or len(arrivals["arrival_schedule"]) != plan["workflows"]
        or len(cohort["measured_workflow_ids"]) != 18
        or cohort["measured_workflow_ids"] != plan["fixed_measured_workflows"]
        or cohort.get("background_submission_stops_when_cohort_finishes") is not True
        or cohort.get("cohort_identity_available_to_scheduler") is not False
    ):
        raise ValueError("require actual mean60 arrivals, initial20 and the fixed private measured18")
    expected = {
        "workflow_arrivals": {"path": plan["arrivals"], "sha256": campaign.digest(plan["arrivals"])},
        "private_analysis_cohort": {
            "path": plan["cohort"], "sha256": campaign.digest(plan["cohort"]),
            "delivered_to_scheduler": False,
        },
    }
    for label in campaign.LABELS:
        stage = plan["stages"][label]
        spec = campaign.load(stage["configuration"])
        command = stage["command"]
        if (
            any(spec.get(key) != value for key, value in expected.items())
            or command[command.index("--workflow-arrivals") + 1] != plan["arrivals"]
            or command[command.index("--experiment-config") + 1] != stage["configuration"]
            or "_mean60_" not in Path(stage["registration"]).name
            or stage.get("existing_watcher") is not None
        ):
            raise ValueError("each method must register fresh against the same real mean60 traffic")


def corrected_stage(previous, label, configuration, arrivals, output, port, suffix):
    stage = deepcopy(previous)
    old_registration = Path(previous["registration"])
    population = old_registration.name.split("_host400_", 1)[0]
    stem = f"{population}_host400_mean60_{label}_{suffix}"
    stage.update(
        registration=str(old_registration.with_name(stem + ".registration.json")),
        run_root=str(Path(previous["run_root"]).with_name(stem)),
        configuration=str(configuration), port=port,
        review=str(output / (label + "_review")),
    )
    stage["command"] = campaign.command_with(previous["command"], {
        "--registration": stage["registration"], "--run-root": stage["run_root"],
        "--experiment-config": configuration, "--workflow-arrivals": arrivals, "--port": port,
    })
    # The corrected Full needs its own run; the completed mean70 Full is never attached.
    stage["existing_watcher"] = None
    return stage


def prepare(previous_path, output, *, suffix):
    previous_path, output = previous_path.resolve(), output.resolve()
    previous = campaign.load(previous_path)
    causal, ranking, finish = campaign.services(Path(previous["operator"]))
    finish.verify_pins(previous["inputs_sha256"])
    if output.exists():
        raise FileExistsError("preserve previous corrections and all wrong-rate evidence")
    if not suffix or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_" for char in suffix):
        raise ValueError("run suffix requires lowercase letters, digits and underscores")
    old_state = campaign.load(previous_path.parent / "state.json")
    old_owner = old_state["process"]
    actual = causal.reservation.process(old_owner["pid"])
    if (
        not causal.reservation.alive(old_owner)
        or actual.get("state") not in ("T", "t")
        or old_state.get("completed_stages") != ["full"]
        or previous.get("mean_interval_seconds") != 70.
        or previous.get("initial_workflows") != 20
    ):
        raise ValueError("replace only the suspended wrong-rate chain after its completed Full")
    for label in old_state["GPU_registered_stages"]:
        state = campaign.load(Path(previous["stages"][label]["registration"]).with_suffix(".state.json"))
        if any(causal.reservation.alive(owner) for owner in (
            *(state.get(name) for name in ("watcher", "server", "driver")),
            *state.get("owned", ()),
        ) if owner):
            raise ValueError("wrong-rate serving and model processes must actually exit first")
    guard = campaign.load(previous["device_state"])
    if guard["status"] != "held" or not causal.reservation.alive(guard["guard"]):
        raise ValueError("preserve the actual device lease throughout correction")
    ranking.released()
    from scripts.prepare_arrival_rate_followup import (
        completion_coverage_screen, followup_configuration, followup_protocol,
    )
    from scripts.prepare_matched_background_baselines import configuration as baseline_configuration
    from scripts.verify_measured_execution import verify_execution

    old_arrivals, old_cohort = campaign.load(previous["arrivals"]), campaign.load(previous["cohort"])
    proof = verify_execution(Path(previous["stages"]["full"]["registration"]), Path(previous["cohort"]))
    old_report_path = Path(previous["stages"]["full"]["review"]) / "cohort.json"
    following, analysis = followup_protocol(old_arrivals, old_cohort, mean_interval_seconds=60.)
    coverage = completion_coverage_screen(
        old_arrivals, old_cohort, campaign.load(old_report_path), following,
    )
    output.mkdir(parents=True, exist_ok=False)
    arrivals_path, cohort_path = output / "arrivals.json", output / "analysis_cohort.json"
    campaign.write(arrivals_path, following)
    campaign.write(cohort_path, analysis)
    campaign.write(output / "previous_mean70_execution_preserved.json", {
        "proof": proof, "static_coverage_screen": coverage,
        "used_as_mean60_performance_control": False,
        "new_mean60_Full_required_for_matched_arrival_comparison": True,
    })
    configurations = {}
    for label in campaign.LABELS[:3]:
        spec = followup_configuration(
            campaign.load(previous["stages"][label]["configuration"]), arrivals_path, cohort_path,
        )
        spec["comparison_contract"]["workload_change"] = "unchanged_original83_plus17_backgrounds_mean60"
        path = output / (label + ".json")
        campaign.write(path, spec)
        configurations[label] = str(path)
    full = campaign.load(configurations["full"])
    for label in campaign.LABELS[3:]:
        spec = baseline_configuration(full, label)
        old_spec = campaign.load(previous["stages"][label]["configuration"])
        if spec["common_environment"] != old_spec["common_environment"]:
            raise ValueError("arrival correction changed a qualified baseline environment")
        path = output / (label + ".json")
        campaign.write(path, spec)
        configurations[label] = str(path)
    stages = {
        label: corrected_stage(
            previous["stages"][label], label, configurations[label], arrivals_path,
            output, port, suffix,
        )
        for label, port in zip(campaign.LABELS, range(19046, 19051), strict=True)
    }
    for label, stage in stages.items():
        if any(Path(stage[name]).exists() for name in ("registration", "run_root")):
            raise FileExistsError("preserve an existing mean60 method: " + label)
        if not causal.qualified.resources(stage["port"])["idle_port"]:
            raise ValueError("corrected comparison port is occupied: " + label)
    pins = {
        **previous["inputs_sha256"],
        **{str(path): campaign.digest(path) for path in (
            previous_path, Path(__file__).resolve(), arrivals_path, cohort_path, old_report_path,
            *(Path(path) for path in configurations.values()),
        )},
    }
    plan = {
        **previous, "stages": stages, "configurations": configurations,
        "mean_interval_seconds": 60., "arrivals": str(arrivals_path), "cohort": str(cohort_path),
        "previous_execution_plan": str(previous_path), "previous_coordinator": old_owner,
        "previous_coordinator_only_handoff": True,
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "explicit_user_correction": "No further mean70 GPU experiments; use actual mean60 arrivals.",
        "only_arrivals_and_private_analysis_window_retimed": True,
        "background_extension_preserved_from_user_authorized_tail_pressure_repair": True,
        "source_engine_sampler_resources_and_policy_environments_preserved": True,
        "previous_mean70_control_reused_as_mean60": False,
        "GPU_stage_order": list(campaign.LABELS), "inputs_sha256": pins,
        "GPU_registered_stages": [], "goal_complete": False, "performance_accepted": False,
    }
    validate_mean60(plan)
    finish.verify_pins(pins)
    campaign.write(output / "plan.json", plan)
    return plan


def register(args):
    plan = prepare(args.previous_campaign, args.output_dir, suffix=args.run_suffix)
    output = args.output_dir.resolve()
    with (output / "campaign.log").open("x") as stream:
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--watch", str(output / "plan.json")],
            cwd=plan["operator"], env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999"},
            stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True,
        )
    causal, _ranking, _finish = campaign.services(Path(plan["operator"]))
    owner = causal.reservation.process(child.pid)
    campaign.write(output / "campaign_owner.json", owner)
    print(json.dumps({"plan": str(output / "plan.json"), "watcher": owner}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", type=Path)
    parser.add_argument("--previous-campaign", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--run-suffix", default="20261113_r1")
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or os.getpriority(os.PRIO_PROCESS, 0) != 0:
        raise ValueError("keep the campaign coordinator nice0 with CUDA hidden")
    if args.watch:
        plan = campaign.load(args.watch.resolve())
        validate_mean60(plan)
        campaign.NAMES = DISPLAY_NAMES
        campaign.watch(args.watch.resolve())
    elif args.previous_campaign and args.output_dir:
        register(args)
    else:
        parser.error("provide the suspended previous campaign and a fresh correction directory")


if __name__ == "__main__":
    main()
