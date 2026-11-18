"""Prepare matched bounded-aging artifacts without changing a live registration."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_continuous_work import forecast_fingerprint
from beliefkv.predictor.workflow_work import WorkflowWorkModel
from beliefkv.runtime.ready_wait_priority import ReadyWaitSettings
from beliefkv.runtime.workflow_work_oracle import WorkflowWorkOracleArtifact
from scripts.finish_model_work_oracle import verify_same_objective
from scripts.train_workflow_work import write


def prepare_artifacts(model, truth, settings):
    if not settings.scale_ms or any(
        value.get("shortest_work_first_admission") is not True
        or value.get("cost_aware_maximal_admission") is not True
        for value in (model, truth)
    ):
        raise ValueError("bounded aging requires the existing maximal short-work selector")
    result = deepcopy(model), deepcopy(truth)
    for artifact in result:
        artifact.update(
            ready_wait_priority_ms=settings.scale_ms,
            ready_wait_priority_grace_ms=settings.grace_ms,
            ready_wait_priority_max_boost=settings.max_boost,
        )
    if forecast_fingerprint(result[0]) != forecast_fingerprint(model):
        raise ValueError("aging tuning changed the learned predictor")
    WorkflowWorkModel(result[0])
    return result


def configuration(original, artifact_path, digest, settings):
    result = deepcopy(original)
    for prefix in ("", "BELIEFKV_"):
        result["common_environment"][prefix + "WORKFLOW_WORK_MODEL"] = str(artifact_path.resolve())
        result["common_environment"][prefix + "WORKFLOW_WORK_MODEL_SHA256"] = digest
    result["comparison_contract"].update(
        ready_wait_priority_ms=settings.scale_ms,
        ready_wait_priority_grace_ms=settings.grace_ms,
        ready_wait_priority_max_boost=settings.max_boost,
        ready_wait_priority_kind=settings.record()["kind"],
        ready_wait_is_not_active_preemption=True,
        ready_wait_tuning_does_not_change_capacity_hold_timers=True,
    )
    result["status"] = "CPU_prepared_bounded_ready_wait_not_GPU_registered"
    return result


def prepare(learned_path, oracle_path, output, settings):
    if output.exists():
        raise FileExistsError("preserve previous preparations and results")
    configs = {
        name: json.loads(path.read_bytes()) for name, path in (
            ("learned", learned_path), ("oracle", oracle_path),
        )
    }
    inputs = [learned_path, oracle_path]
    artifacts = {}
    for name, config in configs.items():
        env = config["common_environment"]
        path = Path(env["WORKFLOW_WORK_MODEL"])
        if sha256_file(path) != env["WORKFLOW_WORK_MODEL_SHA256"]:
            raise ValueError("the source work artifact changed")
        artifacts[name] = json.loads(path.read_bytes())
        inputs.append(path)
    verify_same_objective(configs["learned"], configs["oracle"], artifacts["learned"], artifacts["oracle"])
    model, truth = prepare_artifacts(artifacts["learned"], artifacts["oracle"], settings)
    output.mkdir(parents=True)
    model_path, truth_path = output / "learned_work.json", output / "oracle_work.json"
    write(model_path, model)
    truth["combination_reference_source_model_sha256"] = sha256_file(model_path)
    WorkflowWorkOracleArtifact(truth)
    write(truth_path, truth)
    prepared = {
        name: configuration(configs[name], path, sha256_file(path), settings)
        for name, path in (("learned", model_path), ("oracle", truth_path))
    }
    verify_same_objective(prepared["learned"], prepared["oracle"], model, truth)
    for name, value in prepared.items():
        write(output / (name + ".json"), value)
    inputs += [
        Path(__file__), ROOT / "beliefkv/runtime/ready_wait_priority.py",
        ROOT / "beliefkv/runtime/workflow_combination.py",
        ROOT / "beliefkv/runtime/workflow_capacity_accumulation.py",
        ROOT / "scripts/finish_model_work_oracle.py",
    ]
    report = {
        "settings": settings.record(),
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in inputs},
        "configurations": {
            name: {
                "path": str((output / (name + ".json")).resolve()),
                "sha256": sha256_file(output / (name + ".json")),
            } for name in prepared
        },
        "same_work_predictions_costs_workload_tools_arrivals_cohort_and_resources": True,
        "capacity_hold_minimum_wait_and_cooldown_ms": 30000,
        "GPU_action": False, "live_or_pending_registrations_changed": False,
        "performance_accepted": False,
    }
    write(output / "preparation.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--learned-config", type=Path, required=True)
    parser.add_argument("--oracle-config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--scale-ms", type=int, default=120000)
    parser.add_argument("--grace-ms", type=int, default=60000)
    parser.add_argument("--max-boost", type=float, default=1.25)
    args = parser.parse_args()
    report = prepare(
        args.learned_config, args.oracle_config, args.output_dir,
        ReadyWaitSettings(args.scale_ms, args.grace_ms, args.max_boost),
    )
    print(json.dumps({"settings": report["settings"], "output": str(args.output_dir.resolve()), "GPU_action": False}))


if __name__ == "__main__":
    main()
