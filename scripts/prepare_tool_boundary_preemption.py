"""Prepare an Oracle-only tool-boundary follow-up on unchanged completed traffic."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.tool_boundary_preemption import BOUNDED_KIND, KIND
from beliefkv.runtime.workflow_work_oracle import WorkflowWorkOracleArtifact
from scripts.audit_workflow_combination_choices import verify_model_work_pair
from scripts.train_workflow_work import write


DEFAULT_POLICY = {
    "kind": KIND, "margin_ms": 400.,
    "cooldown_ms": 2000., "ready_wait_protection_ms": 30_000.,
}
BOUNDED_POLICY = {
    "kind": BOUNDED_KIND, "margin_ms": 400.,
    "cooldown_ms": 30_000., "ready_wait_protection_ms": 30_000.,
    "min_work_ratio": 1.2, "min_switch_interval_ms": 1000.,
    "migration_window_ms": 60_000., "migration_budget_pool_multiples": 4.,
    "native_probe_budget_ms": 2.,
    "returned_boundary_ttl_ms": 2000.,
}


def prepare_artifact(original, *, bounded=False):
    artifact = WorkflowWorkOracleArtifact(original)
    if (
        not artifact.continuous_combination
        or original.get("shortest_work_first_admission") is not True
        or original.get("cost_aware_maximal_admission") is not True
        or original.get("maximal_admission_objective") != "benefit"
        or "tool_boundary_preemption" in original
    ):
        raise ValueError("prepare from the unchanged continuous maximal-benefit Oracle")
    result = deepcopy(original)
    result["tool_boundary_preemption"] = dict(BOUNDED_POLICY if bounded else DEFAULT_POLICY)
    WorkflowWorkOracleArtifact(result)
    return result


def configuration(original, model_path, digest, *, bounded=False):
    env = original["common_environment"]
    if (
        env.get("WORKFLOW_WORK_SOURCE") != "oracle"
        or env.get("AGENT_RESIDENCY_PAUSE") != "1"
        or env.get("TOOL_OFFLOAD_BEFORE_ADMISSION") != "1"
        or env.get("TOOL_PREFETCH") != "0" or env.get("LAYER_HANDOFF") != "0"
    ):
        raise ValueError("tool-boundary follow-up requires the retained matched Oracle settings")
    result = deepcopy(original)
    for name in ("WORKFLOW_WORK_MODEL", "BELIEFKV_WORKFLOW_WORK_MODEL"):
        result["common_environment"][name] = str(model_path.resolve())
    for name in ("WORKFLOW_WORK_MODEL_SHA256", "BELIEFKV_WORKFLOW_WORK_MODEL_SHA256"):
        result["common_environment"][name] = digest
    result["comparison_contract"].update(
        tool_boundary_preemption=BOUNDED_KIND if bounded else KIND,
        tool_boundary_current_tool_residual_assumed_ms=0.,
        tool_boundary_future_tool_duration_used=False,
        tool_boundary_native_inflight_continuity_preserved=True,
        tool_boundary_requires_actual_ready_consumer_and_safe_native_release=True,
        tool_boundary_score_is_not_optimal_JCT_bound=True,
        tool_boundary_oracle_only_followup=True,
        learned_successor_must_be_separately_prepared_and_qualified=True,
    )
    result["purpose"] = "Cost-aware tool-boundary Oracle on the unchanged completed workload"
    result["status"] = "CPU_prepared_tool_boundary_Oracle_not_GPU_registered"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-configuration", type=Path, required=True)
    parser.add_argument("--completed-full-registration", type=Path, required=True)
    parser.add_argument("--matching-learned-configuration", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--bounded", action="store_true")
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve previous preparations")
    original = json.loads(args.source_configuration.read_bytes())
    model = Path(original["common_environment"]["WORKFLOW_WORK_MODEL"])
    if sha256_file(model) != original["common_environment"]["WORKFLOW_WORK_MODEL_SHA256"]:
        raise ValueError("original Oracle model fingerprint changed")
    full = json.loads(args.completed_full_registration.read_bytes())
    full_configuration = Path(full["experiment_configuration"])
    verified = verify_model_work_pair(
        json.loads(full_configuration.read_bytes()), original,
        json.loads(args.matching_learned_configuration.read_bytes()),
    )
    raw = prepare_artifact(json.loads(model.read_bytes()), bounded=args.bounded)
    if (
        len(full["workflow_ids"]) != len(set(full["workflow_ids"]))
        or set(full["workflow_ids"]) != raw["workflows"].keys()
        or Path(full["source_arm"]).resolve() != Path(raw["source_binding"]["source_arm"]).resolve()
    ):
        raise ValueError("completed Full belongs to another workload")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    prepared_model = args.output_dir / "oracle_work.json"
    write(prepared_model, raw)
    prepared = configuration(
        original, prepared_model, sha256_file(prepared_model), bounded=args.bounded,
    )
    write(args.output_dir / "oracle.json", prepared)
    inputs = (
        args.source_configuration, args.completed_full_registration,
        args.matching_learned_configuration, full_configuration, *verified, model, Path(__file__),
        ROOT / "beliefkv/policy/tool_boundary_preemption.py",
        ROOT / "beliefkv/runtime/tool_boundary_preemption.py",
        ROOT / "beliefkv/runtime/predicted_tool_offload.py",
        ROOT / "beliefkv/runtime/reference_tool_replacement.py",
        ROOT / "beliefkv/runtime/workflow_combination.py",
        ROOT / "beliefkv/runtime/workflow_size_admission.py",
        ROOT / "beliefkv/runtime/sglang_v0520_runtime.py",
    )
    write(args.output_dir / "preparation.json", {
        "source_configuration": str(args.source_configuration.resolve()),
        "reused_completed_Full_registration": str(args.completed_full_registration.resolve()),
        "oracle_configuration": str((args.output_dir / "oracle.json").resolve()),
        "oracle_configuration_sha256": sha256_file(args.output_dir / "oracle.json"),
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in inputs},
        "unchanged_work_tokens_tools_dependencies_arrivals_cohort_and_resources": True,
        "new_action_space_is_cost_aware_tool_boundary_yield_only": True,
        "source_complete_execution_reverification_still_required_before_GPU": True,
        "Full_rerun_registered": False, "learned_successor_registered": False,
        "GPU_action": False, "performance_accepted": False,
    })
    print(json.dumps({"output": str(args.output_dir.resolve()), "GPU_action": False}))


if __name__ == "__main__":
    main()
