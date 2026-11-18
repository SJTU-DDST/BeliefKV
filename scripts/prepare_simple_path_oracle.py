"""Restore the original global path Oracle with optional tool-boundary handover."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.tool_boundary_preemption import BOUNDED_KIND
from beliefkv.runtime.workflow_work_oracle import KIND, WorkflowWorkOracleArtifact
from scripts.prepare_tool_boundary_preemption import BOUNDED_POLICY
from scripts.train_workflow_work import write


def prepare_artifact(control, legacy, *, check_interval_ms, tool_boundary_preemption=False):
    for raw in (control, legacy):
        WorkflowWorkOracleArtifact(raw)
        if (
            raw["kind"] != KIND or raw.get("admission_order") != "global_ready_remaining_path"
            or raw.get("free_dispatch_coalescing_window_ms") != 2000
        ):
            raise ValueError("restore from the original global two-second path Oracle")
    if control["model_seconds_per_work_token"] != legacy["model_seconds_per_work_token"]:
        raise ValueError("do not change the original model-time calibration")
    if type(check_interval_ms) is not int or not 0 < check_interval_ms <= 2000:
        raise ValueError("reuse the existing bounded tool-check interval")
    if type(tool_boundary_preemption) is not bool:
        raise ValueError("tool-boundary preemption must be explicitly enabled or disabled")
    result = deepcopy(control)
    for name in (
        "admission_cost_profile", "combination_work_readout", "combination_work_reference",
        "combination_reference_source_model_sha256", "cost_aware_maximal_admission",
        "maximal_admission_objective", "ready_wait_priority_ms",
        "ready_wait_priority_grace_ms", "ready_wait_priority_max_boost",
        "ready_capacity_accumulation_ms", "strict_remaining_work_admission",
    ):
        result.pop(name, None)
    result.update(remaining_work_ranking_enabled=True, shortest_work_first_admission=True)
    result.pop("tool_boundary_preemption", None)
    if tool_boundary_preemption:
        result["tool_boundary_preemption"] = {
            **BOUNDED_POLICY,
            "margin_ms": float(check_interval_ms),
            "cooldown_ms": float(result["free_dispatch_coalescing_window_ms"]),
            "ready_wait_protection_ms": 0.,
            "min_work_ratio": 1.,
            "min_switch_interval_ms": float(check_interval_ms),
            "returned_boundary_ttl_ms": float(result["free_dispatch_coalescing_window_ms"]),
        }
    WorkflowWorkOracleArtifact(result)
    return result


def prepare(full_registration, legacy_registration, output, *, tool_boundary_preemption=False):
    if output.exists():
        raise FileExistsError("preserve previous Oracle candidates")
    inputs = [full_registration, legacy_registration]
    registrations = [
        json.loads(path.read_bytes()) for path in (full_registration, legacy_registration)
    ]
    configurations, artifacts = [], []
    for registration in registrations:
        config_path = Path(registration["experiment_configuration"])
        config = json.loads(config_path.read_bytes())
        env = config["common_environment"]
        path = Path(env["WORKFLOW_WORK_MODEL"])
        if (
            sha256_file(path) != env["WORKFLOW_WORK_MODEL_SHA256"]
            or env["WORKFLOW_WORK_MODEL_SHA256"]
            != registration["reference_environment"]["WORKFLOW_WORK_MODEL_SHA256"]
            or env["WORKFLOW_WORK_SOURCE"] != "oracle"
            or env["WORKFLOW_COMBINATION_ADMISSION"] != "0"
        ):
            raise ValueError("require the original non-combination Full/Oracle configurations")
        configurations.append(config)
        artifacts.append(WorkflowWorkOracleArtifact.load(path, sha256_file(path)).raw)
        inputs.extend((config_path, path))
    full, legacy = artifacts
    if (
        full["remaining_work_ranking_enabled"] is not False
        or legacy["remaining_work_ranking_enabled"] is not True
        or set(full["workflows"]) != set(registrations[0]["workflow_ids"])
        or full["source_binding"]["source_arm"] != registrations[0]["source_arm"]
    ):
        raise ValueError("the complete Full and original ranked Oracle identities differ")
    config = deepcopy(configurations[0])
    env = config["common_environment"]
    interval = int(env["BELIEFKV_TOOL_OFFLOAD_INTERVAL_MS"])
    raw = prepare_artifact(
        full, legacy, check_interval_ms=interval,
        tool_boundary_preemption=tool_boundary_preemption,
    )
    model = output / "oracle_work.json"
    write(model, raw)
    for name in ("WORKFLOW_WORK_MODEL", "BELIEFKV_WORKFLOW_WORK_MODEL"):
        env[name] = str(model.resolve())
    for name in ("WORKFLOW_WORK_MODEL_SHA256", "BELIEFKV_WORKFLOW_WORK_MODEL_SHA256"):
        env[name] = sha256_file(model)
    config["workflow_work_qualification"] = {
        "kind": KIND, "artifact": str(model.resolve()), "artifact_sha256": sha256_file(model),
        "remaining_work_ranking_enabled": True,
        "future_information_used": True, "deployable": False,
        "inherited_cost_calibration_not_new_population_measurement": True,
    }
    config["comparison_contract"].update(
        workflow_work_ranking=True,
        matched_pair_changes_only_remaining_work_ranking=False,
        selection_strategy="protected_live_JOIN_then_remaining_model_tool_path_then_greedy_fit",
        ready_siblings_adjacent_without_fanout_score=True,
        public_timeout_force_resume_enabled=False,
        ready_wait_priority_ms=0, ready_capacity_accumulation_ms=0,
        combination_utility_and_JOIN_coverage_scores_enabled=False,
        tool_boundary_preemption=BOUNDED_KIND if tool_boundary_preemption else "disabled",
        tool_boundary_uses_same_remaining_path_priority=tool_boundary_preemption,
        tool_boundary_ready_wait_protection_ms=0,
        same_original_tasks_tokens_tools_arrivals_measurement_and_resources=True,
    )
    config["purpose"] = (
        "Original global shortest-path Oracle with safe tool-boundary handover"
        if tool_boundary_preemption else
        "Original global shortest-path Oracle without additional tool-boundary preemption"
    )
    config["status"] = "CPU_prepared_simple_path_Oracle_not_GPU_registered"
    config["gpu_execution"] = {
        "registered": False, "automatic_resume_allowed": False,
        "deferred_while_materialization_campaign_runs": True,
    }
    write(output / "oracle.json", config)
    inputs.extend((
        Path(__file__), ROOT / "beliefkv/runtime/workflow_oracle_admission.py",
        ROOT / "beliefkv/runtime/tool_boundary_preemption.py",
        ROOT / "beliefkv/runtime/tool_boundary_schedule.py",
        ROOT / "beliefkv/policy/tool_boundary_preemption.py",
    ))
    result = {
        "legacy_policy_registration": str(legacy_registration.resolve()),
        "matching_completed_Full_registration": str(full_registration.resolve()),
        "configuration": {"path": str((output / "oracle.json").resolve()),
                          "sha256": sha256_file(output / "oracle.json")},
        "ordinary_order": "remaining_model_tool_critical_path_then_greedy_fit",
        "window_ms": 2000, "combination_selector_instantiated": False,
        "tool_boundary_preemption_enabled": tool_boundary_preemption,
        "ready_wait_priority_ms": 0, "ready_wait_protection_ms": 0,
        "active_continuity_and_native_capacity_authority_preserved": True,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in inputs},
        "GPU_action": False, "performance_accepted": False,
    }
    write(output / "preparation.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full-registration", type=Path, required=True)
    parser.add_argument("--legacy-oracle-registration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--tool-boundary-preemption", action="store_true",
        help="explicitly reproduce the additional tool-boundary preemption candidate",
    )
    args = parser.parse_args()
    result = prepare(
        args.full_registration, args.legacy_oracle_registration, args.output,
        tool_boundary_preemption=args.tool_boundary_preemption,
    )
    print(json.dumps({"configuration": result["configuration"], "GPU_action": False}))


if __name__ == "__main__":
    main()
