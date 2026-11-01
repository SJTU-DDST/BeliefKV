"""Freeze one new-workflow Oracle attempt without choosing policy winners."""

from copy import deepcopy
from datetime import datetime, timezone
import os
from pathlib import Path
import random
from statistics import mean
import sys

ROOT = Path("/home/longhao/experiment/BeliefKV")
OPS = ROOT.parent / "ops"
REPORTS = ROOT / "experiments/reports/remaining_original_projects_20261030"
SOURCE = REPORTS / "final_oracle_original82_source_r1/composite"
TIMING = REPORTS / "final_oracle_original82_source_r1/tool_timing.json"
DECLARATION = REPORTS / "final_oracle_original82_source_r1/declaration.json"
BASE = REPORTS / "original69_initial12_mean70_prepare_r1"
HERE = REPORTS / "final_oracle_original82_traffic_r1"
REGISTRATION = OPS / "workflow_final_oracle_original82_source_20261031_r1.registration.json"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.causal_replay import evaluate_causal_plan
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.replay_arrivals import permuted_poisson_arrival_plan
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.tool_timing_overlay import load_overlay
from beliefkv.experiments.workload_mix import summary
from beliefkv.experiments.workflow_cohort import declare_cohort
from beliefkv.runtime.workflow_work_oracle import WorkflowWorkOracleArtifact
from scripts import run_causal_comparison as causal
from scripts.prepare_sustained_oracle_admission import oracle_artifact
from scripts.run_causal_gpu_replay import source_plan
from scripts.train_workflow_work import write


def main():
    if HERE.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve final traffic declarations and keep nice19 CPU-only")
    declaration = causal.load(DECLARATION)
    population, base = causal.load(SOURCE / "composite_source.json"), causal.load(BASE / "full.json")
    graph, expected, evidence = source_plan(SOURCE)
    transformed, _ = load_overlay(graph, TIMING)
    external = evaluate_causal_plan(transformed, dict.fromkeys(transformed.request_usage, 0.))["workflow_elapsed_ms"]
    rows = {row["workflow_id"]: {**row, "fixed_external_path_s": external[row["workflow_id"]] / 1000.}
            for row in population["selected_workflows"]}
    new_source = str((ROOT / "experiments/raw/workflow_remaining_node19_source_20261031_node19r1/native_capture").resolve())
    fields = ("model_work_units", "initial_prompt_tokens", "fixed_external_path_s")
    overall = {name: mean(row[name] for row in rows.values()) for name in fields}
    trials = []
    for seed in range(61, 257):
        ids = sorted(rows)
        random.Random(seed).shuffle(ids)
        groups = [ids[:12], ids[12:30], ids[30:]]
        averages = [{name: mean(rows[key][name] for key in ids) for name in fields} for ids in groups]
        model_ratio = summary([rows[key]["model_work_units"] for key in groups[1]])["p80_p20"]
        new_measured = sum(rows[key]["source_capture"] == new_source for key in groups[1])
        initial_tokens = sum(rows[key]["initial_prompt_tokens"] for key in groups[0])
        checks = {
            "model_group_means_within_25_percent": all(
                .75 <= group[fields[0]] / overall[fields[0]] <= 1.25 for group in averages
            ),
            "context_group_means_within_35_percent": all(
                .65 <= group[fields[1]] / overall[fields[1]] <= 1.35 for group in averages
            ),
            "external_group_means_within_40_percent": all(
                .6 <= group[fields[2]] / overall[fields[2]] <= 1.4 for group in averages
            ),
            "measured_model_work_ratio_at_least_four": model_ratio >= 4.,
            "measured_includes_four_new_original_workflows": new_measured >= 4,
            "initial_context_between_one_and_one_point_five_KV_pools": 805198 <= initial_tokens <= 1207797,
        }
        trials.append({"seed": seed, "checks": checks, "group_means": averages,
                       "measured_model_ratio": model_ratio, "new_measured": new_measured,
                       "initial_prompt_tokens": initial_tokens})
        if all(checks.values()):
            break
    else:
        write(HERE / "static_selection_rejected.json", {"trials": trials, "GPU_action": False})
        raise ValueError("no declared static balanced permutation qualifies; no GPU job registered")
    arrivals = permuted_poisson_arrival_plan(
        rows, rate_per_second=1. / 70., seed=35, permutation_seed=seed, initial_workflows=12,
    )
    offsets = [row["offset_seconds"] for row in arrivals["arrival_schedule"]]
    cohort = declare_cohort(
        arrivals, warmup=12, measured=18, throughput_start_s=offsets[12],
        throughput_end_s=offsets[29], stop_after_measured=True,
    )
    for name, value in (("arrivals.json", arrivals), ("analysis_cohort.json", cohort)):
        write(HERE / name, value)
    old = causal.load(BASE / "control_oracle.json")
    profile = old["admission_cost_profile"]
    artifact = oracle_artifact(
        SOURCE, TIMING, old["model_seconds_per_work_token"], profile["costs"], profile["calibration"],
        combination=False, coalescing_window_ms=2000,
    )
    artifact["remaining_work_ranking_enabled"] = False
    artifact_path = HERE / "control_oracle.json"
    WorkflowWorkOracleArtifact(artifact).validate_source(SOURCE, graph, evidence)
    write(artifact_path, artifact)
    old_timing = causal.load(REPORTS / "composite69_tool_timing_r1.json")
    timing = causal.load(TIMING)
    previous = {tuple(row["tool_identity"]): row for row in old_timing["entries"]}
    overlap = [row for row in timing["entries"] if tuple(row["tool_identity"]) in previous]
    if any(previous[tuple(row["tool_identity"])] != row for row in overlap):
        raise ValueError("existing exact tool timing entries changed")
    prior = Path(timing["offline_prior"]["path"])
    if sha256_file(prior) != old_timing["offline_prior"]["sha256"]:
        raise ValueError("offline tool predictor changed")
    common = base["common_environment"]
    common.update(ROOT_COUNT=str(len(rows)), WORKLOAD_MANIFEST=str(SOURCE / "workload_manifest.json"))
    common.pop("CLIENT_ROOT_CONCURRENCY", None)
    for name, value in (
        ("WORKFLOW_WORK_MODEL", str(artifact_path)),
        ("WORKFLOW_WORK_MODEL_SHA256", sha256_file(artifact_path)),
    ):
        common[name] = common["BELIEFKV_" + name] = value
    common["BELIEFKV_TOOL_TIMING_PRIOR"] = str(prior)
    base.update(
        source_registration=str(REGISTRATION), status="last_new_workflow_oracle_nonranking_control",
        workflow_arrivals={"path": str(HERE / "arrivals.json"), "sha256": sha256_file(HERE / "arrivals.json")},
        tool_timing_overlay={**base["tool_timing_overlay"], "path": str(TIMING), "sha256": sha256_file(TIMING)},
        gpu_execution={"registered": False, "requires_fresh_position_and_sampling": True},
        candidate_provenance={
            "base_nonranking_configuration": str(BASE / "full.json"),
            "same_nonranking_policy_and_disabled_features": True,
            "new_workflow_final_oracle_attempt": True, "no_more_source_collection": True,
            "permutation_selected_without_policy_outcomes": True,
        },
    )
    base["workflow_work_qualification"].update(
        artifact=str(artifact_path), artifact_sha256=sha256_file(artifact_path),
        remaining_work_ranking_enabled=False,
    )
    base["workload_acceptance"].update(
        minimum_model_work_p80_p20=3.,
        actual_model_work_p80_p20=population["population"]["model_work_units"]["p80_p20"],
        formal_workload_accepted=False,
    )
    write(HERE / "full.json", base)
    constructors = {}
    for arm in ("full", "native_matched"):
        settings = arm_environment(common, arm)
        for name in causal.SERVER_FIELDS:
            if name in settings:
                settings["BELIEFKV_" + name] = settings[name]
        constructors[arm] = causal.preflight_runtime(settings, common["MODEL_PATH"])
    write(HERE / "preparation.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(), "projects": len(rows),
        "requests": len(expected), "initial": 12, "warmup": 12, "measured": 18,
        "background": len(rows) - 30, "arrival_mean_s": 70., "arrival_seed": 35,
        "permutation_seed": seed, "static_trials": trials,
        "new_measured_workflows": new_measured, "measured_work_ratio": model_ratio,
        "initial_prompt_tokens_not_native_fit": initial_tokens, "last_arrival_s": offsets[-1],
        "unchanged_old_timing_entries": len(overlap),
        "constructor_preflights": constructors,
        "final_oracle_gate": {"mean_JCT_reduction": .05, "throughput_fraction": .98},
        "fallback_if_gate_fails": "original56_initial12_mean70_completed_nonranking_control",
        "declaration": declaration,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            DECLARATION, SOURCE / "composite_source.json", TIMING, prior, BASE / "full.json",
            artifact_path, HERE / "full.json", HERE / "arrivals.json", HERE / "analysis_cohort.json",
            Path(__file__),
        )},
        "GPU_action": False, "formal_workload_accepted": False, "performance_accepted": False,
    })
    print({
        "projects": len(rows), "requests": len(expected), "initial12_mean70": True,
        "new_measured_workflows": new_measured, "measured_work_ratio": model_ratio,
        "permutation_seed": seed, "last_arrival_s": offsets[-1],
        "old_tool_times_preserved": len(overlap), "GPU_action": False,
    }, flush=True)


if __name__ == "__main__":
    main()
