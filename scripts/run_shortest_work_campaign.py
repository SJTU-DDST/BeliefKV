"""Continue mean60 comparisons with short-work selection and a completed Full control."""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.native_engine_variant import engine_patch
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts import run_continuous_matched_campaign as campaign
from scripts.audit_workflow_combination_choices import verify_model_work_pair
from scripts.correct_mean60_matched_campaign import DISPLAY_NAMES
from scripts.finish_model_work_oracle import verify_pins
from scripts.verify_measured_execution import verify_execution


def validate(previous, prepared):
    if (
        previous.get("mean_interval_seconds") != 60.
        or previous.get("initial_workflows") != 20 or previous.get("workflows") != 100
        or len(previous.get("fixed_measured_workflows", ())) != 18
    ):
        raise ValueError("retain the actual mean60, initial20, original100 and fixed18 workload")
    full = campaign.load(previous["stages"]["full"]["configuration"])
    oracle, learned = (campaign.load(prepared["configurations"][label]["path"]) for label in ("oracle", "learned"))
    verify_model_work_pair(full, oracle, learned)
    for config in (oracle, learned):
        artifact = campaign.load(config["common_environment"]["WORKFLOW_WORK_MODEL"])
        if artifact.get("shortest_work_first_admission") is not True or (
            config["comparison_contract"].get("shortest_work_first_admission") is not True
        ):
            raise ValueError("both work providers must explicitly select the short-work policy")
    return full, oracle, learned


def prepare(args):
    previous_path = args.previous_plan.resolve()
    previous, prepared = campaign.load(previous_path), campaign.load(args.preparation)
    validate(previous, prepared)
    verify_pins(previous["inputs_sha256"])
    verify_pins(prepared["inputs_sha256"])
    cpu = campaign.load(args.cpu_evidence)
    causal, ranking, _finish = campaign.services(ROOT)
    causal.fresh._require_clean(ROOT)
    if (
        cpu.get("returncode") != 0 or cpu.get("failed_error_skipped_cases") != 0
        or cpu.get("all_retained_literal_identities_present") is not True
        or cpu.get("sources_unchanged_during_execution") is not True
    ):
        raise ValueError("require the unchanged source-bound CPU qualification")
    causal.comparison.verify_cpu_snapshot(ROOT, campaign.load(cpu["source_manifest"]["path"]))
    old_state = campaign.load(previous_path.parent / "state.json")
    anchor = old_state["process"]
    actual = causal.reservation.process(anchor["pid"])
    lease = campaign.load(previous["device_state"])
    if (
        not causal.reservation.alive(anchor) or actual.get("state") not in ("T", "t")
        or any(actual.get(name) != anchor[name] for name in ("pid", "start_ticks"))
        or lease.get("status") != "held" or not causal.reservation.alive(lease["guard"])
        or any(lease["owner"].get(name) != anchor[name] for name in ("pid", "start_ticks"))
        or (datetime.now(timezone.utc) - datetime.fromisoformat(lease["checked_at"])).total_seconds() > 15
    ):
        raise ValueError("preserve the exact live suspended reservation anchor and fresh device guard")
    ranking.released()
    for label in ("full", "oracle"):
        verify_execution(Path(previous["stages"][label]["registration"]), Path(previous["cohort"]))
    if args.output_dir.exists():
        raise FileExistsError("preserve all previous campaign and result evidence")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    stages = deepcopy(previous["stages"])
    stages["full"]["reuse_completed_execution"] = True
    native_paths = []
    for index, label in enumerate(campaign.LABELS[1:]):
        stage = stages[label]
        stem = f"workflow_original100_host400_mean60_shortfirst_{label}_{args.run_suffix}"
        stage.update(
            registration=str(Path(stage["registration"]).with_name(stem + ".registration.json")),
            run_root=str(Path(stage["run_root"]).with_name(stem)),
            port=19051 + index, review=str(args.output_dir.resolve() / (label + "_review")),
            existing_watcher=None,
        )
        if label in ("oracle", "learned"):
            stage["configuration"] = prepared["configurations"][label]["path"]
        if any(Path(stage[name]).exists() for name in ("registration", "run_root")):
            raise FileExistsError("preserve a previously registered stage: " + label)
        command = list(stage["command"])
        engine = Path(command[command.index("--engine") + 1])
        old_native_path = Path(command[command.index("--engine-sources") + 1])
        old_native = campaign.load(old_native_path)
        native = git_source_state(engine, canonical_patch=engine_patch(
            ROOT, old_native["canonical_patch"]["actual_tree"],
        ))
        expected, current = deepcopy(old_native), deepcopy(native)
        for value in (expected, current):
            value.pop("state_sha256")
            value["canonical_patch"].pop("path")
        if expected != current:
            raise ValueError("the qualified native engine changed beyond its proof pathname")
        native_path = args.output_dir / (label + "_native_sources.json")
        campaign.write(native_path, native)
        native_paths.extend((old_native_path, native_path))
        causal.engine_evidence(engine, Path(command[command.index("--engine-report") + 1]), native_path)
        command[1] = str(ROOT / "scripts/run_causal_comparison.py")
        for flag, value in {
            "--registration": stage["registration"], "--run-root": stage["run_root"],
            "--port": stage["port"], "--experiment-config": stage["configuration"],
            "--cpu-report": cpu["report"]["path"], "--cpu-sources": cpu["source_manifest"]["path"],
            "--engine-sources": native_path,
        }.items():
            command[command.index(flag) + 1] = str(value)
        stage["command"] = command
    paths = [
        previous_path, args.preparation, args.cpu_evidence, Path(__file__),
        ROOT / "scripts/run_continuous_matched_campaign.py",
        Path(cpu["report"]["path"]), Path(cpu["source_manifest"]["path"]), *native_paths,
        *[Path(stage["configuration"]) for stage in stages.values()],
    ]
    plan = {
        **previous, "operator": str(ROOT), "stages": stages,
        "configurations": {label: stage["configuration"] for label, stage in stages.items()},
        "previous_execution_plan": str(previous_path), "previous_coordinator": anchor,
        "held_reservation_anchor": anchor,
        "require_ranked_gain_before_following_stages": True,
        "reused_complete_Full_without_GPU_rerun": True,
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "inputs_sha256": {
            **previous["inputs_sha256"], **prepared["inputs_sha256"],
            **{str(path.resolve()): sha256_file(path) for path in paths},
        },
        "goal_complete": False, "performance_accepted": False,
    }
    campaign.write(args.output_dir / "plan.json", plan)
    return args.output_dir / "plan.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", type=Path)
    for name in ("previous-plan", "preparation", "cpu-evidence", "output-dir"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--run-suffix", default="20261113_r1")
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("keep the coordinator's CUDA access hidden")
    if args.watch:
        campaign.NAMES = DISPLAY_NAMES
        campaign.watch(args.watch.resolve())
        return
    if any(getattr(args, name) is None for name in ("previous_plan", "preparation", "cpu_evidence", "output_dir")):
        parser.error("provide the completed campaign, preparation, CPU evidence and fresh output")
    path = prepare(args)
    with (args.output_dir / "campaign.log").open("x") as stream:
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--watch", str(path.resolve())],
            cwd=ROOT, env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999"},
            stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True,
        )
    from scripts.run_causal_comparison import reservation

    print(json.dumps({"plan": str(path.resolve()), "watcher": reservation.process(child.pid)}))


if __name__ == "__main__":
    main()
