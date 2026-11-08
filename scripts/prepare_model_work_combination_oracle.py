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
from beliefkv.predictor.workflow_continuous_work import forecast_fingerprint
from beliefkv.runtime.workflow_work_oracle import (
    COMBINATION_WORK_KIND, CONTINUOUS_COMBINATION_WORK_KIND, CONTINUOUS_PRESSURE_WORK_KIND,
    TOOL_BAND_PRESSURE_ORDER, WorkflowWorkOracleArtifact,
)
from scripts.train_workflow_work import write


def prepare(config, model, labels, artifact_path, *, continuous_model=None, tool_band_pressure=False):
    common = config["common_environment"]
    if type(tool_band_pressure) is not bool or tool_band_pressure and (
        continuous_model is None
        or any(common.get(prefix + "AGENT_PRESSURE_PAUSE_POLICY") != "workflow_tool_aware"
               for prefix in ("", "BELIEFKV_"))
    ):
        raise ValueError("pressure work order requires continuous truth and the unchanged tool-aware policy")
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
    if continuous_model is not None:
        reference = continuous_model.raw.get("continuous_work_readout")
        if (
            reference is None
            or forecast_fingerprint(continuous_model.raw) != forecast_fingerprint(model.raw)
            or continuous_model.raw.get("metadata", {}).get("continuous_work_source_model_sha256")
            != common["WORKFLOW_WORK_MODEL_SHA256"]
        ):
            raise ValueError("continuous oracle reference must come from the same retained R5 predictor")
        artifact.update(
            kind=CONTINUOUS_COMBINATION_WORK_KIND, combination_work_readout="continuous",
            combination_work_reference=deepcopy(reference),
            combination_reference_source_model_sha256=common["WORKFLOW_WORK_MODEL_SHA256"],
        )
    if tool_band_pressure:
        artifact.update(
            kind=CONTINUOUS_PRESSURE_WORK_KIND,
            admission_order="r5_combination_and_tool_band_pressure",
            pressure_work_order=TOOL_BAND_PRESSURE_ORDER,
        )
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
    if continuous_model is not None:
        candidate.update(
            purpose="Same R5 selector, exact continuous remaining model work and fixed training scale",
            status="CPU_prepared_continuous_model_work_oracle_not_registered",
        )
        candidate["workflow_work_qualification"].update(
            three_bin_thresholds_and_cost_profile_copied_from_learned_model=False,
            cost_profile_and_work_thresholds_copied_from_learned_model=True,
            continuous_reference_uses_retained_training_labels_only=True,
            continuous_learned_model_serving_eligibility_granted=False,
        )
    if tool_band_pressure:
        candidate.update(
            purpose="Exact continuous model-work combination and mandatory same-tool-band pressure ordering",
            status="CPU_prepared_continuous_pressure_oracle_not_registered",
        )
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
    if continuous_model is not None:
        contract.update(
            workflow_work_prediction="exact_continuous_remaining_model_work",
            workflow_resume_key=[
                "protected_ready_JOIN", "training_scaled_continuous_work_combination", "reference_tie",
            ],
            oracle_candidate_kind="same_R5_selector_exact_continuous_model_work",
            same_learned_combination_selector_and_utility_mapping=False,
            same_learned_combination_selector=True,
            continuous_utility_reference_work=reference["reference_work"],
            continuous_utility_definition="1/(1+remaining_model_work/reference_work)",
            prediction_and_utility_mapping_are_joint_change=True,
        )
    if tool_band_pressure:
        contract.update(
            oracle_candidate_kind="continuous_model_work_combination_and_tool_band_pressure",
            oracle_future_work_used_outside_combination=True,
            oracle_future_work_outside_combination_scope="mandatory_pressure_within_tool_retention_bands",
            oracle_pressure_work_order=TOOL_BAND_PRESSURE_ORDER,
            pressure_change_is_pure_prediction_effect=False,
            ordinary_combination_selector_unchanged=True,
            pressure_marks_grant_capacity=False,
            existing_tool_weights_and_protected_native_state_preserved=True,
        )
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
    parser.add_argument("--continuous-reference-model", type=Path)
    parser.add_argument("--tool-band-pressure-order", action="store_true")
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
    continuous_model = (
        WorkflowWorkModel.load(
            args.continuous_reference_model, sha256_file(args.continuous_reference_model),
        ) if args.continuous_reference_model is not None else None
    )
    artifact_path = args.output_dir / "oracle.json"
    artifact, candidate = prepare(
        config, model, json.loads(args.oracle_labels.read_bytes()), artifact_path,
        continuous_model=continuous_model,
        tool_band_pressure=args.tool_band_pressure_order,
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
            *((args.continuous_reference_model,) if args.continuous_reference_model is not None else ()),
        )},
        "artifact_sha256": sha256_file(artifact_path),
        "configuration_sha256": sha256_file(args.output_dir / "full.json"),
        "complete_workflows": len(artifact["workflows"]),
        "complete_requests": len(artifact["requests"]),
        "work_thresholds": artifact["work_thresholds"],
        "work_readout": artifact["combination_work_readout"],
        "continuous_reference_work": (
            continuous_model.raw["continuous_work_readout"]["reference_work"]
            if continuous_model is not None else None
        ),
        "cost_identity": artifact["admission_cost_profile"]["identity"],
        "learned_schema_and_pressure_tool_order_semantics_preserved": not args.tool_band_pressure_order,
        "tool_band_pressure_order_enabled": args.tool_band_pressure_order,
        "tool_replacement_order_and_tool_retention_semantics_preserved": True,
        "bookkeeping_execution_cost_identical_claimed": False,
    })
    print(json.dumps({"configuration": str(args.output_dir / "full.json"), "GPU_action": False}))


if __name__ == "__main__":
    main()
