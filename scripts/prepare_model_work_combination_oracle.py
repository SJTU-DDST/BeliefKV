"""Bind exact model-work labels to the unchanged learned R5 combination policy."""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_work import R5_SCHEMA, WorkflowWorkModel
from beliefkv.runtime.workflow_work_oracle import (
    COMBINATION_WORK_KIND, WorkflowWorkOracleArtifact,
)
from scripts.train_workflow_work import write


def prepare(config, model, labels, artifact_path):
    common = config["common_environment"]
    if (
        model.schema != R5_SCHEMA
        or common.get("WORKFLOW_WORK_SOURCE") != "learned"
        or common.get("WORKFLOW_COMBINATION_ADMISSION") != "1"
        or model.raw["admission_cost_profile"]["kind"]
        != "r5_frozen_batch_cost_proxy_capacity_v7"
    ):
        raise ValueError("require the existing learned R5 benefit-only combination policy")
    WorkflowWorkOracleArtifact(labels)
    artifact = deepcopy(labels)
    artifact.update({
        "kind": COMBINATION_WORK_KIND, "admission_order": "r5_combination_only",
        "combination_work_readout": "three_bins",
        "remaining_work_ranking_enabled": True,
        "work_thresholds": list(model.thresholds),
        "admission_cost_profile": deepcopy(model.raw["admission_cost_profile"]),
        "free_dispatch_coalescing_window_ms": model.raw["free_dispatch_coalescing_window_ms"],
    })
    for name in ("model_prefill_seconds_per_token", "model_prefill_progress"):
        artifact.pop(name, None)
    WorkflowWorkOracleArtifact(artifact)
    candidate = deepcopy(config)
    candidate.update(
        purpose="Same R5 selector and three-bin utility, substituting exact remaining model work",
        status="CPU_prepared_model_work_oracle_not_registered",
        workflow_work_qualification={
            "kind": "declared_future_model_work_diagnostic_not_deployable",
            "decision_eligible": True,
            "deployable": False,
            "three_bin_thresholds_and_cost_profile_copied_from_learned_model": True,
        },
    )
    # The new serving source gets its own CPU qualification, rather than
    # claiming the original frozen learned source still qualifies this mode.
    candidate.pop("source_CPU_binding", None)
    env = candidate["common_environment"]
    env.update(WORKFLOW_WORK_SOURCE="oracle", BELIEFKV_WORKFLOW_WORK_SOURCE="oracle")
    for name in ("WORKFLOW_WORK_MODEL", "BELIEFKV_WORKFLOW_WORK_MODEL"):
        env[name] = str(artifact_path.resolve())
    artifact_digest = hashlib.sha256(
        (json.dumps(artifact, ensure_ascii=False, allow_nan=False) + "\n").encode()
    ).hexdigest()
    for name in ("WORKFLOW_WORK_MODEL_SHA256", "BELIEFKV_WORKFLOW_WORK_MODEL_SHA256"):
        env[name] = artifact_digest
    candidate["workflow_work_qualification"]["model_sha256"] = artifact_digest
    contract = candidate["comparison_contract"]
    contract.update({
        "future_information_used": True, "deployable": False,
        "workflow_work_prediction": "exact_remaining_model_work_three_bins",
        "workflow_resume_key": ["protected_ready_JOIN", "exact_work_bin_combination", "reference_tie"],
        "workflow_work_uncertainty_fallback": "exact_bound_truth_or_reference_on_invalid_metadata",
        "oracle_candidate_kind": "same_R5_selector_exact_three_model_work_bins",
        "oracle_future_tool_durations_used": False,
        "oracle_future_work_used_outside_combination": False,
        "prediction_and_combination_are_joint_change": False,
        "same_learned_combination_selector_and_utility_mapping": True,
        "ranking_gain_isolated": False,
        "oracle_bookkeeping_synchronous_unlike_learned_worker": True,
    })
    changed = {
        name for name in common if env.get(name) != common[name]
    } | {name for name in env if name not in common}
    expected = {
        "WORKFLOW_WORK_SOURCE", "BELIEFKV_WORKFLOW_WORK_SOURCE",
        "WORKFLOW_WORK_MODEL", "BELIEFKV_WORKFLOW_WORK_MODEL",
        "WORKFLOW_WORK_MODEL_SHA256", "BELIEFKV_WORKFLOW_WORK_MODEL_SHA256",
    }
    if changed != expected:
        raise ValueError("model-work substitution changed unrelated runtime controls")
    return artifact, candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("learned-config", "oracle-labels", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve preparations; use CPU-only nice19")
    config = json.loads(args.learned_config.read_bytes())
    common = config["common_environment"]
    path = Path(common["WORKFLOW_WORK_MODEL"])
    if path != Path(common["BELIEFKV_WORKFLOW_WORK_MODEL"]) or (
        common["WORKFLOW_WORK_MODEL_SHA256"] != common["BELIEFKV_WORKFLOW_WORK_MODEL_SHA256"]
    ):
        raise ValueError("learned model aliases differ")
    model = WorkflowWorkModel.load(path, common["WORKFLOW_WORK_MODEL_SHA256"])
    artifact_path = args.output_dir / "oracle.json"
    artifact, candidate = prepare(
        config, model, json.loads(args.oracle_labels.read_bytes()), artifact_path,
    )
    args.output_dir.mkdir(parents=True, exist_ok=False)
    write(artifact_path, artifact)
    assert sha256_file(artifact_path) == candidate["common_environment"]["WORKFLOW_WORK_MODEL_SHA256"]
    write(args.output_dir / "full.json", candidate)
    write(args.output_dir / "preparation.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "GPU_action": False, "GPU_registered": False,
        "input_sha256": {str(p.resolve()): sha256_file(p) for p in (
            args.learned_config, args.oracle_labels, path, Path(__file__),
        )},
        "artifact_sha256": sha256_file(artifact_path),
        "configuration_sha256": sha256_file(args.output_dir / "full.json"),
        "complete_workflows": len(artifact["workflows"]),
        "complete_requests": len(artifact["requests"]),
        "work_thresholds": artifact["work_thresholds"],
        "cost_identity": artifact["admission_cost_profile"]["identity"],
        "learned_schema_and_pressure_tool_order_semantics_preserved": True,
        "bookkeeping_execution_cost_identical_claimed": False,
    })
    print(json.dumps({"configuration": str(args.output_dir / "full.json"), "GPU_action": False}))


if __name__ == "__main__":
    main()
