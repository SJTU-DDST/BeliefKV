"""Prepare same-traffic ThunderAgent/native successors without registering GPU work."""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments import host_numa, native_execution
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.paper_baselines import environment as paper_environment
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workflow_cohort import validate_cohort
from scripts import run_causal_comparison as causal
from scripts.finish_model_work_oracle import verify_pins
from scripts.prepare_mixed_chunk_comparison import SHARED_KEYS
from scripts.train_workflow_work import write

LABELS = ("thunderagent_acting_decay", "native_matched")
TRAFFIC_KEYS = (
    "source_registration", "candidate_source_arm", "workflow_arrivals", "private_analysis_cohort",
    "tool_timing_overlay", "stream_response_metrics",
)
RESOURCE_KEYS = (
    *SHARED_KEYS, "ENABLE_MIXED_CHUNK", "DEVICE_FULL_TOKENS", "DEVICE_MAMBA_SLOTS",
    "NATIVE_EXECUTION_CONFIG", "NATIVE_EXECUTION_CONFIG_SHA256",
)


def effective(common, label):
    if label not in LABELS:
        raise ValueError("prepare only acting-decay ThunderAgent or native SGLang")
    return (
        paper_environment(common, label) if label == "thunderagent_acting_decay"
        else arm_environment(common, label)
    )


def validate_configuration(full, baseline, label):
    if (
        full.get("order") != ["full"] or baseline.get("order") != [label]
        or any(full.get(name) != baseline.get(name) for name in TRAFFIC_KEYS)
        or baseline.get("gpu_execution", {}).get("registered") is not False
        or baseline.get("gpu_execution", {}).get("automatic_resume_allowed") is not False
        or baseline.get("comparison_contract", {}).get("future_information_used") is not False
        or any(name in baseline for name in ("workflow_work_qualification", "oracle_information"))
    ):
        raise ValueError("baseline changed traffic, private cohort or future-information contract")
    before, common = full["common_environment"], baseline["common_environment"]
    for name in RESOURCE_KEYS:
        if before.get(name) != common.get(name):
            raise ValueError("baseline changed shared resource: " + name)
    if host_numa.configuration(before) != host_numa.configuration(common):
        raise ValueError("baseline changed the common NUMA allocation contract")
    expected = effective(before, label)
    if common != expected or effective(common, label) != common:
        raise ValueError("baseline is not the canonical isolated policy environment")
    for prefix in ("", "BELIEFKV_"):
        if common.get(prefix + "WORKFLOW_WORK_MODEL") or common.get(
            prefix + "WORKFLOW_WORK_MODEL_SHA256"
        ):
            raise ValueError("baseline retained future model-work metadata")
    if any(common.get(name, "0") != "0" for name in (
        "BELIEFKV_ENABLE_PREDICTED_TOOL_OFFLOAD", "BELIEFKV_ENABLE_TOOL_PREFETCH",
        "BELIEFKV_ENABLE_PREPARE_HOST", "BELIEFKV_ENABLE_JOIN_PARENT_PRIORITY",
        "BELIEFKV_WORKFLOW_SIZE_RESUME", "BELIEFKV_WORKFLOW_COMBINATION_ADMISSION",
        "BELIEFKV_LAYER_HANDOFF", "BELIEFKV_DEMAND_PREFILL_PIPELINE",
    )):
        raise ValueError("predictive policies leaked into a baseline")
    if common["AGENT_CONTROL_INTERVAL_MS"] != "5000":
        raise ValueError("paper/native baseline inherited Full's shortened control period")
    if label == "native_matched" and (
        baseline["comparison_contract"].get("native_replay_baseline") is not True
        or baseline.get("native_reference", {}).get("repeat_full_native_replay") is not True
    ):
        raise ValueError("native comparison needs its explicit replay/termination declaration")


def configuration(full, label):
    result = deepcopy(full)
    result["common_environment"] = effective(full["common_environment"], label)
    for name in ("workflow_work_qualification", "oracle_information", "source_CPU_binding"):
        result.pop(name, None)
    result.update(
        order=[label], purpose="matched_background_workload_system_baseline",
        status="CPU_prepared_baseline_not_registered",
        comparison_contract={
            "kind": "predictive_versus_same_traffic_" + label,
            "future_information_used": False,
            "complete_original_workflows": True,
            "same_fixed_tools_arrivals_and_resources_for_all_arms": True,
            "measured_cohort_not_delivered_to_scheduler": True,
            "stop_after_all_fixed_measured_workflows_complete": True,
            "tool_prefetch_disabled_by_user": True, "layer_handoff": False,
            "minimum_promising_measured_mean_JCT_reduction": .01,
            "common_window_output_throughput_noninferiority_fraction": .98,
            **({
                "paper_program_identity": "invocation_id",
                "paper_control_interval_ms": 5000,
                "paper_acting_decay_half_life_s": 1.,
                "paper_acting_decay_scope": "periodic_resume_only",
            } if label == "thunderagent_acting_decay" else {
                "native_replay_baseline": True,
                "shared_native_data_plane_and_execution_profile": True,
                "unmodified_upstream_native_source_claimed": False,
            }),
        },
        native_reference={
            "repeat_full_native_replay": label == "native_matched",
            "matched_native_capture_exists": False,
            "purpose": "matched_token_replay_not_new_source_collection",
        },
        gpu_execution={
            "registered": False, "automatic_resume_allowed": False,
            "conditional_on_completed_accepted_matching_ranking_campaign": True,
            "same_source_position_and_sampling_proofs_required": True,
            "actual_owned_exit_and_fresh_resources_required": True,
        },
        performance_accepted=False,
    )
    validate_configuration(full, result, label)
    return result


def prepare(args):
    if args.output_dir.exists():
        raise FileExistsError("preserve previous baseline preparations")
    plan = causal.load(args.execution_plan)
    verify_pins(plan["inputs_sha256"])
    full_path = Path(plan["configurations"]["full"])
    full = causal.load(full_path)
    cohort_path, arrival_path = Path(plan["cohort"]), Path(plan["arrivals"])
    cohort, arrivals = causal.load(cohort_path), causal.load(arrival_path)
    validate_cohort(cohort, arrivals)
    source = causal.load(Path(full["candidate_source_arm"]) / "composite_source.json")
    if (
        cohort["measured_workflow_ids"] != plan["fixed_measured_workflows"]
        or len(cohort["measured_workflow_ids"]) != 18
        or plan["workflows"] != len(source["selected_workflows"])
        or set(row["workflow_id"] for row in source["selected_workflows"])
        != set(row["workflow_id"] for row in arrivals["arrival_schedule"])
        or cohort.get("cohort_identity_available_to_scheduler") is not False
    ):
        raise ValueError("baseline preparation changed the entire source or fixed cohort")
    checkout = Path(plan["operator"])
    template = plan["stages"]["full"]["command"]
    engine = Path(template[template.index("--engine") + 1])
    reference_path = Path(template[template.index("--reference") + 1])
    reference = causal.load(reference_path)
    shared_execution = native_execution.configuration(full["common_environment"], checkout)
    paths = (args.execution_plan, full_path, cohort_path, arrival_path, reference_path, Path(__file__))
    pins = {str(path.resolve()): sha256_file(path) for path in paths}
    result, constructors = {}, {}
    for label in LABELS:
        spec = configuration(full, label)
        if native_execution.configuration(spec["common_environment"], checkout) != shared_execution:
            raise ValueError("baseline changed actual native graph or MoE configuration")
        configured = causal.experiment_reference(reference, spec)
        settings, _flags, _socket = causal.environment(
            configured, label, checkout, engine, args.output_dir / "unallocated" / label,
            19044 if label == LABELS[0] else 19045, args.output_dir / "unallocated_plugin",
            sampling_mode="position", max_running_requests=int(spec["common_environment"]["MAX_RUNNING_REQUESTS"]),
        )
        constructors[label] = causal.preflight_runtime(settings, spec["common_environment"]["MODEL_PATH"])
        result[label] = spec
    verify_pins(pins)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    configurations = {}
    for label, spec in result.items():
        path = args.output_dir / (label + ".json")
        write(path, spec)
        configurations[label] = {"path": str(path.resolve()), "sha256": sha256_file(path)}
    report = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "kind": "CPU_prepared_matched_background_baselines_not_registered",
        "source_execution_plan": str(args.execution_plan.resolve()),
        "workflows": plan["workflows"], "initial_workflows": plan["initial_workflows"],
        "mean_interval_seconds": plan["mean_interval_seconds"],
        "measured_workflows": cohort["measured_workflow_ids"],
        "common_window_offset_seconds": cohort["throughput_window_offset_seconds"],
        "configurations": configurations, "CPU_constructor_checks": constructors,
        "shared_native_execution_identity": shared_execution["identity"] if shared_execution else None,
        "common_NUMA_contract": host_numa.configuration(full["common_environment"]),
        "minimum_model_work_p80_p20": plan["minimum_model_work_p80_p20"],
        "normal_measured_tails_and_background_censoring_protocol_preserved": True,
        "future_baseline_requires_current_position_and_sampling_proofs": True,
        "future_baseline_requires_complete_matching_campaign_and_actual_owned_exit": True,
        "source_capture_recollection_or_independent_400GB_GPU_components_needed": False,
        "current_GPU_queue_changed": False, "GPU_registered_stages": [], "GPU_action": False,
        "performance_accepted": False, "inputs_sha256": pins,
    }
    write(args.output_dir / "preparation.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-plan", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or os.getpriority(os.PRIO_PROCESS, 0) != 19:
        raise ValueError("prepare with hidden CUDA and nice19")
    result = prepare(args)
    print(json.dumps({
        "kind": result["kind"], "configurations": result["configurations"],
        "CPU_constructor_checks": result["CPU_constructor_checks"], "GPU_action": False,
    }))


if __name__ == "__main__":
    main()
