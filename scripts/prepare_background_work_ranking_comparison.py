"""Bind an extended background population to the retained Full/Oracle/R5 policies."""

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.replay_arrivals import validate_arrival_plan
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workflow_cohort import validate_cohort
from beliefkv.runtime.workflow_work_oracle import WorkflowWorkOracleArtifact
from scripts.finish_model_work_oracle import verify_pins, verify_same_objective
from scripts.prepare_sustained_oracle_admission import oracle_artifact
from scripts.review_learned_work_ranking import verify_learned_combination_pair
from scripts.train_workflow_work import write

LABELS = ("full", "oracle", "learned")
LABEL_FIELDS = ("requests", "workflows", "workflow_graphs", "source_binding")


def rebind_labels(original, labels):
    result = deepcopy(original)
    for name in LABEL_FIELDS:
        result[name] = deepcopy(labels[name])
    WorkflowWorkOracleArtifact(result)
    return result


def prepare(args):
    if args.output_dir.exists():
        raise FileExistsError("preserve previous background comparisons")
    previous = json.loads(args.previous_preparation.read_bytes())
    extension = json.loads(args.extension_preparation.read_bytes())
    parent = args.extension_preparation.parent
    arrivals_path, cohort_path = parent / "arrivals.json", parent / "analysis_cohort.json"
    arrivals, cohort, source, review = (
        json.loads(path.read_bytes()) for path in (
            arrivals_path, cohort_path, args.source_arm / "composite_source.json",
            args.materialization_review,
        )
    )
    validate_arrival_plan(arrivals, [row["workflow_id"] for row in source["selected_workflows"]])
    validate_cohort(cohort, arrivals)
    verify_pins(review["inputs_sha256"])
    if (
        review.get("status") != "CPU_materialization_verified"
        or review.get("graph", {}).get("original_expected_native_work_and_internal_causal_nodes_equal")
        is not True
        or review.get("tools", {}).get("original_tool_samples_and_public_prior_equal") is not True
        or cohort["measured_workflow_ids"] != previous["fixed_measured_workflows"]
        or extension["previous_measured_JCT_static_coverage_screen"][
            "candidate_schedule_covers_static_tail"
        ] is not True
    ):
        raise ValueError("require unchanged fixed measured work and verified background extension")
    for path in (arrivals_path, cohort_path, args.overlay, args.source_arm / "composite_source.json"):
        if review["inputs_sha256"].get(str(path.resolve())) != sha256_file(path):
            raise ValueError("materialization review belongs to different source or traffic")
    configs = {
        name: json.loads(Path(previous["configurations"][name]).read_bytes()) for name in LABELS
    }
    original_labels = {
        name: WorkflowWorkOracleArtifact.load(
            configs[name]["common_environment"]["WORKFLOW_WORK_MODEL"],
            configs[name]["common_environment"]["WORKFLOW_WORK_MODEL_SHA256"],
        ).raw for name in ("full", "oracle")
    }
    old = original_labels["full"]
    labels = oracle_artifact(
        args.source_arm.resolve(), args.overlay.resolve(), old["model_seconds_per_work_token"],
        old["admission_cost_profile"]["costs"], old["admission_cost_profile"]["calibration"],
        coalescing_window_ms=2000,
    )
    overlay = json.loads(args.overlay.read_bytes())
    pins = {
        str(path.resolve()): sha256_file(path) for path in (
            args.previous_preparation, args.extension_preparation, args.materialization_review,
            arrivals_path, cohort_path, args.overlay, args.source_arm / "composite_source.json",
            Path(__file__), *(Path(previous["configurations"][name]) for name in LABELS),
        )
    }
    args.output_dir.mkdir(parents=True, exist_ok=False)
    for name in ("full", "oracle"):
        path = args.output_dir / (name + "_work.json")
        raw = rebind_labels(original_labels[name], labels)
        for request, row in original_labels[name]["requests"].items():
            if raw["requests"].get(request) != row:
                raise ValueError("background labels changed original request work")
        write(path, raw)
        env = configs[name]["common_environment"]
        for key in ("WORKFLOW_WORK_MODEL", "BELIEFKV_WORKFLOW_WORK_MODEL"):
            env[key] = str(path.resolve())
            env[key + "_SHA256"] = sha256_file(path)
        qualification = configs[name]["workflow_work_qualification"]
        for key in ("artifact", "model"):
            if key in qualification:
                qualification[key] = str(path.resolve())
        for key in ("artifact_sha256", "model_sha256"):
            if key in qualification:
                qualification[key] = sha256_file(path)
    paths = {}
    for name, config in configs.items():
        env = config["common_environment"]
        env.update(
            ROOT_COUNT=str(len(source["selected_workflows"])),
            WORKLOAD_MANIFEST=str((args.source_arm / "workload_manifest.json").resolve()),
            BELIEFKV_TOOL_TIMING_PRIOR=overlay["offline_prior"]["path"],
            BELIEFKV_TOOL_TIMING_PRIOR_SHA256=overlay["offline_prior"]["sha256"],
        )
        config.update(
            source_registration=str(args.source_registration.resolve()),
            candidate_source_arm=str(args.source_arm.resolve()),
            workflow_arrivals={"path": str(arrivals_path.resolve()), "sha256": sha256_file(arrivals_path)},
            private_analysis_cohort={
                "path": str(cohort_path.resolve()), "sha256": sha256_file(cohort_path),
                "delivered_to_scheduler": False,
            },
            tool_timing_overlay={
                "path": str(args.overlay.resolve()), "sha256": sha256_file(args.overlay),
                "timing_domain": overlay["timing_domain"], "synthetic_tool_timing": True,
            },
            status="CPU_prepared_background_extension_requires_bound_source_and_position_check",
            preparation_inputs_sha256=pins,
            workload_acceptance={
                "formal_workload_accepted": False,
                "actual_model_work_p80_p20": source["population"]["model_work_units"]["p80_p20"],
                "ready_supply_capacity_competition_and_background_tail_require_execution": True,
            },
            gpu_execution={
                "registered": False, "stage": name,
                "fresh_source_bound_position_check_required": True,
                "new_independent_400GB_component_run": False,
            },
        )
        config.pop("source_CPU_binding", None)
        config["comparison_contract"]["workload_change"] = "unchanged_original83_plus17_backgrounds_mean70"
        path = args.output_dir / (name + ".json")
        write(path, config)
        paths[name] = str(path.resolve())
    verify_learned_combination_pair(configs["full"], configs["learned"])
    verify_same_objective(
        configs["learned"], configs["oracle"],
        json.loads(Path(configs["learned"]["common_environment"]["WORKFLOW_WORK_MODEL"]).read_bytes()),
        json.loads((args.output_dir / "oracle_work.json").read_bytes()),
    )
    verify_pins(pins)
    plan = {
        "schema_version": 1, "kind": "matched_background_extension_work_ranking_preparation",
        "source_registration": str(args.source_registration.resolve()),
        "source_arm": str(args.source_arm.resolve()), "configurations": paths,
        "arrivals": str(arrivals_path.resolve()), "cohort": str(cohort_path.resolve()),
        "mean_interval_seconds": 1. / arrivals["rate_per_second"],
        "initial_workflows": arrivals["initial_workflows"],
        "workflows": len(arrivals["arrival_schedule"]),
        "fixed_measured_workflows": cohort["measured_workflow_ids"],
        "minimum_model_work_p80_p20": source["minimum_model_work_p80_p20"],
        "static_completion_coverage_screen": extension["previous_measured_JCT_static_coverage_screen"],
        "retained_prediction_parameters_and_continuous_truth_scale": True,
        "original_requests_tokens_tool_samples_and_dependencies_preserved": True,
        "GPU_stage_order": list(LABELS), "GPU_action": False,
        "inputs_sha256": {
            **pins, **{str(path.resolve()): sha256_file(path) for path in (
                *(Path(value) for value in paths.values()),
                args.output_dir / "full_work.json", args.output_dir / "oracle_work.json",
                Path(configs["learned"]["common_environment"]["WORKFLOW_WORK_MODEL"]),
            )},
        },
    }
    write(args.output_dir / "preparation.json", plan)
    return {"workflows": plan["workflows"], "mean_interval_seconds": plan["mean_interval_seconds"],
            "configurations": paths, "GPU_action": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "previous-preparation", "extension-preparation", "source-arm", "source-registration",
        "overlay", "materialization-review", "output-dir",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or os.getpriority(os.PRIO_PROCESS, 0) != 19:
        raise ValueError("prepare with hidden CUDA and nice19")
    print(json.dumps(prepare(args)))


if __name__ == "__main__":
    main()
