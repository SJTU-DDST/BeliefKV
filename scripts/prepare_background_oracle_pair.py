"""Prepare an unregistered, matched Full/Oracle pair for composed original traces."""

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.replay_arrivals import validate_arrival_plan
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workflow_cohort import validate_cohort
from beliefkv.runtime.workflow_work_oracle import WorkflowWorkOracleArtifact
from scripts.prepare_sustained_oracle_admission import oracle_artifact
from scripts.train_workflow_work import write


MODEL_FIELDS = (
    "WORKFLOW_WORK_MODEL", "BELIEFKV_WORKFLOW_WORK_MODEL",
    "WORKFLOW_WORK_MODEL_SHA256", "BELIEFKV_WORKFLOW_WORK_MODEL_SHA256",
)


def verify_pair(control, ranked):
    normalized = []
    effective = []
    artifacts = []
    for config, enabled in ((control, False), (ranked, True)):
        raw = deepcopy(config)
        common = raw["common_environment"]
        qualification = raw["workflow_work_qualification"]
        if (
            common["BELIEFKV_WORKFLOW_WORK_MODEL"] != common["WORKFLOW_WORK_MODEL"]
            or common["BELIEFKV_WORKFLOW_WORK_MODEL_SHA256"] != common["WORKFLOW_WORK_MODEL_SHA256"]
            or qualification["artifact"] != common["WORKFLOW_WORK_MODEL"]
            or qualification["artifact_sha256"] != common["WORKFLOW_WORK_MODEL_SHA256"]
        ):
            raise ValueError("oracle aliases and qualification must bind the same artifact")
        artifact = WorkflowWorkOracleArtifact.load(
            common["WORKFLOW_WORK_MODEL"], common["WORKFLOW_WORK_MODEL_SHA256"],
        ).raw
        if (
            artifact.get("remaining_work_ranking_enabled") is not enabled
            or qualification["remaining_work_ranking_enabled"] is not enabled
            or raw["comparison_contract"]["workflow_work_ranking"] is not enabled
            or config.get("source_registration") is not None
            or config["gpu_execution"].get("registered") is not False
        ):
            raise ValueError("require explicit ranking-only, unregistered preparation")
        artifact = deepcopy(artifact)
        artifact.pop("remaining_work_ranking_enabled")
        artifacts.append(artifact)
        env = arm_environment(common, "full")
        effective.append({key: value for key, value in env.items() if key not in MODEL_FIELDS})
        for key in MODEL_FIELDS:
            common.pop(key)
        raw["workflow_work_qualification"] = {
            key: value for key, value in raw["workflow_work_qualification"].items()
            if key not in ("artifact", "artifact_sha256", "remaining_work_ranking_enabled")
        }
        raw["comparison_contract"].pop("workflow_work_ranking")
        normalized.append(raw)
    if artifacts[0] != artifacts[1] or normalized[0] != normalized[1] or effective[0] != effective[1]:
        raise ValueError("matched pair changes more than remaining-work ranking")
    return {
        "configuration_changes_only_rank_artifact_and_explicit_rank_flags": True,
        "artifacts_differ_only_in_remaining_work_ranking_enabled": True,
        "effective_environments_equal_except_artifact_paths_and_hashes": True,
        "same_progress_bookkeeping_control_windows_and_capacity_policy": True,
        "source_registration_pending": True, "GPU_action": False,
    }


def prepare(args):
    if args.output_dir.exists():
        raise FileExistsError("preserve previous pair preparations")
    paths = {
        name: getattr(args, name)
        for name in ("template", "calibration_oracle", "arrivals", "cohort", "overlay", "materialization_review")
    }
    paths["source_declaration"] = args.source_arm / "composite_source.json"
    pins = {str(path.resolve()): sha256_file(path) for path in paths.values()}
    documents = {key: json.loads(path.read_bytes()) for key, path in paths.items()}
    declaration, arrivals, cohort, overlay, review = (
        documents[key] for key in (
            "source_declaration", "arrivals", "cohort", "overlay", "materialization_review",
        )
    )
    identities = [row["workflow_id"] for row in declaration["selected_workflows"]]
    validate_arrival_plan(arrivals, identities)
    validate_cohort(cohort, arrivals)
    if (
        review.get("status") != "CPU_materialization_verified"
        or any(
            review.get("inputs_sha256", {}).get(str(paths[key].resolve())) != pins[str(paths[key].resolve())]
            for key in ("source_declaration", "arrivals", "cohort", "overlay")
        )
        or cohort.get("cohort_identity_available_to_scheduler") is not False
        or cohort.get("termination_rule") != "all_fixed_measured_workflows_completed"
    ):
        raise ValueError("source, traffic, timing and private complete-JCT cohort need matching CPU review")
    old = WorkflowWorkOracleArtifact(documents["calibration_oracle"]).raw
    profile = old["admission_cost_profile"]
    calibration = {
        "kind": "inherited_old_population_cost_calibration",
        "artifact": str(args.calibration_oracle.resolve()),
        "artifact_sha256": pins[str(args.calibration_oracle.resolve())],
        "original_calibration": profile["calibration"],
        "new_population_measured_calibration": False,
    }
    artifact = oracle_artifact(
        args.source_arm.resolve(), args.overlay.resolve(),
        old["model_seconds_per_work_token"], profile["costs"], calibration,
        coalescing_window_ms=2000,
    )
    template = documents["template"]
    common = deepcopy(template["common_environment"])
    common.update(
        ROOT_COUNT=str(len(identities)),
        WORKLOAD_MANIFEST=str((args.source_arm / "workload_manifest.json").resolve()),
        BELIEFKV_TOOL_OFFLOAD_INTERVAL_MS="400",
        BELIEFKV_TOOL_TIMING_PRIOR=overlay["offline_prior"]["path"],
        BELIEFKV_TOOL_TIMING_PRIOR_SHA256=overlay["offline_prior"]["sha256"],
    )
    for name, value in (
        ("WORKFLOW_SIZE_RESUME", "1"), ("WORKFLOW_WORK_SOURCE", "oracle"),
        ("WORKFLOW_COMBINATION_ADMISSION", "0"), ("AGENT_CONTROL_INTERVAL_MS", "2000"),
    ):
        common[name] = common["BELIEFKV_" + name] = value
    if any(common.get(name) != value for name, value in (
        ("HICACHE_SIZE_GB", "400"), ("MAX_RUNNING_REQUESTS", "24"),
        ("JOIN_PREFETCH_TRIGGER", "observed_eos"), ("PREPARE_HOST", "1"),
        ("PREDICTED_TOOL_OFFLOAD", "1"), ("AGENT_COADMIT_JOIN_CHILDREN", "1"),
        ("TOOL_PREFETCH", "0"), ("LAYER_HANDOFF", "0"),
    )):
        raise ValueError("template changed retained features or shared resource limits")
    prior = Path(overlay["offline_prior"]["path"])
    if sha256_file(prior) != overlay["offline_prior"]["sha256"]:
        raise ValueError("public timing prior changed")
    contract = deepcopy(template["comparison_contract"])
    contract.update(
        matched_pair_changes_only_remaining_work_ranking=True,
        reference_control_interval_ms=2000, free_dispatch_coalescing_window_ms=2000,
        admission_controller="unified_native_boundary",
        tool_offload_check_interval_ms=400,
        minimum_promising_measured_mean_JCT_reduction=.01,
    )
    base = {
        **{key: deepcopy(template[key]) for key in (
            "schema_version", "source_profile", "native_reference", "stream_response_metrics",
        )},
        "purpose": "original_background_extension_matched_ranking_diagnostic",
        "status": "CPU_prepared_source_registration_and_GPU_release_pending",
        "source_registration": None, "candidate_source_arm": str(args.source_arm.resolve()),
        "common_environment": common, "comparison_contract": contract,
        "workflow_arrivals": {"path": str(args.arrivals.resolve()), "sha256": sha256_file(args.arrivals)},
        "private_analysis_cohort": {
            "path": str(args.cohort.resolve()), "sha256": sha256_file(args.cohort),
            "delivered_to_scheduler": False,
        },
        "tool_timing_overlay": {
            "path": str(args.overlay.resolve()), "sha256": sha256_file(args.overlay),
            "timing_domain": overlay["timing_domain"], "synthetic_tool_timing": True,
        },
        "order": ["full"], "performance_accepted": False,
        "gpu_execution": {
            "registered": False, "blocked_by_active_user_hold": True,
            "automatic_resume_allowed": False,
        },
        "workload_acceptance": {
            "formal_workload_accepted": False,
            "actual_model_work_p80_p20": declaration["population"]["model_work_units"]["p80_p20"],
            "ready_supply_capacity_competition_and_background_tail_require_execution": True,
        },
        "preparation_inputs_sha256": pins,
    }
    configs = []
    for name, enabled in (("full", False), ("oracle", True)):
        raw = {**deepcopy(artifact), "remaining_work_ranking_enabled": enabled}
        path = args.output_dir / (name + "_work.json")
        write(path, raw)
        config = deepcopy(base)
        config["comparison_contract"]["workflow_work_ranking"] = enabled
        for key in ("WORKFLOW_WORK_MODEL", "BELIEFKV_WORKFLOW_WORK_MODEL"):
            config["common_environment"][key] = str(path.resolve())
        for key in ("WORKFLOW_WORK_MODEL_SHA256", "BELIEFKV_WORKFLOW_WORK_MODEL_SHA256"):
            config["common_environment"][key] = sha256_file(path)
        config["workflow_work_qualification"] = {
            "kind": raw["kind"], "artifact": str(path.resolve()),
            "artifact_sha256": sha256_file(path), "remaining_work_ranking_enabled": enabled,
            "future_information_used": True, "deployable": False,
            "inherited_cost_calibration_not_new_population_measurement": True,
        }
        configs.append(config)
    checks = verify_pair(*configs)
    if any(sha256_file(Path(name)) != digest for name, digest in pins.items()):
        raise ValueError("preparation inputs changed")
    for name, config in zip(("full", "oracle"), configs):
        write(args.output_dir / (name + ".pending.json"), config)
    report = {
        **checks, "status": base["status"], "projects": len(identities),
        "measured": len(cohort["measured_workflow_ids"]), "inputs_sha256": pins,
        "operator_sha256": sha256_file(Path(__file__)),
        "calibration": calibration, "GPU_successor_registered": False,
        "formal_workload_accepted": False, "performance_accepted": False,
    }
    write(args.output_dir / "pair_review.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "template", "calibration-oracle", "source-arm", "arrivals", "cohort",
        "overlay", "materialization-review", "output-dir",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or os.getpriority(os.PRIO_PROCESS, 0) != 19:
        parser.error("prepare with hidden CUDA and nice19")
    report = prepare(args)
    print(json.dumps({key: report[key] for key in (
        "status", "projects", "measured", "GPU_action", "source_registration_pending",
    )}))


if __name__ == "__main__":
    main()
