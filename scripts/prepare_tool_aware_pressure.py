"""Prepare a matched 12-initial mean70 control/oracle pair with tool-aware pressure."""

import argparse
from copy import deepcopy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.replay_arrivals import validate_arrival_plan
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.workflow_work_oracle import WorkflowWorkOracleArtifact
from scripts.run_causal_comparison import SERVER_FIELDS, load, preflight_runtime
from scripts.train_workflow_work import write


def prepare(path, arrival_configuration, *, ranking, oracle_path, oracle_sha256):
    original = load(path)
    candidate = deepcopy(original)
    common = candidate["common_environment"]
    artifact = WorkflowWorkOracleArtifact.load(
        Path(common["WORKFLOW_WORK_MODEL"]), common["WORKFLOW_WORK_MODEL_SHA256"],
    )
    if (
        original["order"] != ["full"]
        or common.get("WORKFLOW_WORK_SOURCE") != "oracle"
        or artifact.raw.get("free_dispatch_coalescing_window_ms") != 2000
    ):
        raise ValueError("tool-aware candidate requires the declared two-second Full oracle")
    arrivals = load(arrival_configuration)
    contract = arrivals["workflow_arrivals"]
    arrival_path = Path(contract["path"])
    if sha256_file(arrival_path) != contract["sha256"]:
        raise ValueError("prepared arrival table changed")
    observed = validate_arrival_plan(load(arrival_path), artifact.workflows)
    if (
        observed["initial_workflows"] != 12 or observed["rate_per_second"] != 1 / 70
        or arrivals["source_registration"] != original["source_registration"]
        or arrivals["tool_timing_overlay"] != original["tool_timing_overlay"]
        or arrivals["common_environment"]["WORKLOAD_MANIFEST"] != common["WORKLOAD_MANIFEST"]
    ):
        raise ValueError("require twelve initial workflows and mean70 with unchanged source/tool times")
    candidate["workflow_arrivals"] = deepcopy(contract)
    for name, value in {
        "AGENT_PRESSURE_PAUSE_POLICY": "workflow_tool_aware",
        "WORKFLOW_WORK_MODEL": str(oracle_path),
        "WORKFLOW_WORK_MODEL_SHA256": oracle_sha256,
    }.items():
        common[name] = common["BELIEFKV_" + name] = value
    candidate["status"] = "cpu_tool_aware_pressure_matched_" + ("oracle" if ranking else "control")
    candidate["historical_candidate_provenance"] = candidate.get("candidate_provenance")
    candidate["candidate_provenance"] = {
        "base": str(path.resolve()), "base_sha256": sha256_file(path),
        "arrival_configuration": str(arrival_configuration.resolve()),
        "arrival_configuration_sha256": sha256_file(arrival_configuration),
        "same_tasks_tokens_tool_times_resources_and_measured_members": True,
        "cohort_definition": str(arrival_path.parent / "analysis_cohort.json"),
        "cohort_definition_sha256": sha256_file(arrival_path.parent / "analysis_cohort.json"),
        "ordinary_coalescing_window_ms": 2000,
        "event_tool_replacement_retained_as_part_of_predicted_offload": True,
        "remaining_work_ranking_enabled": ranking,
        "oracle_bookkeeping_retained_in_both_arms": True,
    }
    candidate["comparison_contract"].update({
        "workflow_pressure_victim_selection": "blocked_tool_retention_band_then_remaining_work",
        "workflow_pressure_unknown_fallback": "elapsed_wait_for_tools_context_order_for_work",
        "tool_pressure_resume_and_pause_share_retention_weight": True,
        "tool_pressure_future_tool_durations_used": False,
        "tool_pressure_complete_raw_budget_enforced": True,
        "tool_pressure_pending_pause_requires_unstarted_immutable_request": True,
        "predicted_tool_replacement": "existing_event_driven_predicted_tool_offload",
        "predicted_tool_replacement_bypasses_coalescing_window": True,
        "workflow_work_ranking": ranking,
        "coalescing_and_oracle_ranking_are_joint_change": False,
        "matched_pair_changes_only_remaining_work_ranking": True,
        "control_retains_oracle_progress_bookkeeping": not ranking,
    })
    candidate["gpu_execution"]["registered"] = False
    candidate["performance_accepted"] = False
    return candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--arrival-configuration", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve earlier candidate evidence")
    base = load(args.base)["common_environment"]
    original_artifact = load(Path(base["WORKFLOW_WORK_MODEL"]))
    entries = {}
    for label, ranking in (("control", False), ("full", True)):
        artifact_path = args.output_dir.resolve() / (label + "_oracle.json")
        write(artifact_path, {**original_artifact, "remaining_work_ranking_enabled": ranking})
        candidate = prepare(
            args.base, args.arrival_configuration, ranking=ranking,
            oracle_path=artifact_path, oracle_sha256=sha256_file(artifact_path),
        )
        common = candidate["common_environment"]
        settings = arm_environment(common, "full")
        for name in SERVER_FIELDS:
            if name in settings:
                settings["BELIEFKV_" + name] = settings[name]
        entry = preflight_runtime(settings, common["MODEL_PATH"])
        if entry["verified_configuration"]["agent_pressure_pause_policy"] != "workflow_tool_aware":
            raise ValueError("actual constructor did not bind the declared tool pressure policy")
        write(args.output_dir / (label + ".json"), candidate)
        entries[label] = {
            "preflight": entry, "remaining_work_ranking_enabled": ranking,
            "configuration_sha256": sha256_file(args.output_dir / (label + ".json")),
            "oracle_sha256": sha256_file(artifact_path),
        }
    write(args.output_dir / "entry.json", {
        "entries": entries, "generator_sha256": sha256_file(Path(__file__)),
        "GPU_experiment_started": False, "performance_accepted": False,
    })
    print(args.output_dir)


if __name__ == "__main__":
    main()
