"""Prepare a matched parameter repair bundle without registering GPU work."""

import argparse
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.workflow_combination import CombinationCosts
from beliefkv.predictor.workflow_continuous_work import forecast_fingerprint
from beliefkv.runtime.ready_wait_priority import ReadyWaitSettings
from beliefkv.runtime.workflow_tool_pressure import ToolRetentionSettings
from beliefkv.runtime.workflow_work_oracle import WorkflowWorkOracleArtifact
from scripts.finish_model_work_oracle import verify_same_objective
from scripts.prepare_bounded_ready_wait import configuration
from scripts.train_workflow_work import write


def profile_identity(profile):
    return hashlib.sha256(json.dumps({
        "costs": profile["costs"], "capacity_summary_kind": profile["kind"],
    }, sort_keys=True).encode()).hexdigest()


def prepare_artifacts(model, truth):
    if any(
        value.get("shortest_work_first_admission") is not True
        or value.get("cost_aware_maximal_admission") is not True
        or value.get("maximal_admission_objective") != "benefit"
        for value in (model, truth)
    ):
        raise ValueError("parameter review requires the existing maximal benefit selector")
    profile = model["admission_cost_profile"]
    if (
        profile != truth["admission_cost_profile"]
        or profile["kind"] != "r5_frozen_batch_cost_proxy_capacity_v7"
        or profile["identity"] != profile_identity(profile)
    ):
        raise ValueError("preserve the exact existing cost profile identity")
    settings = ToolRetentionSettings()
    results = deepcopy(model), deepcopy(truth)
    for value in results:
        updated = value["admission_cost_profile"]
        updated["costs"]["join_progress"] = "linear"
        updated["identity"] = profile_identity(updated)
        CombinationCosts(**updated["costs"])
        value["tool_retention_policy"] = asdict(settings)
    if forecast_fingerprint(results[0]) != forecast_fingerprint(model):
        raise ValueError("scheduler parameters changed predictor weights")
    return results


def prepare(learned_path, oracle_path, output):
    if output.exists():
        raise FileExistsError("preserve earlier preparations")
    paths = {"learned": learned_path, "oracle": oracle_path}
    configs = {name: json.loads(path.read_bytes()) for name, path in paths.items()}
    inputs, old = list(paths.values()), {}
    for name, config in configs.items():
        env = config["common_environment"]
        path = Path(env["WORKFLOW_WORK_MODEL"])
        if (
            sha256_file(path) != env["WORKFLOW_WORK_MODEL_SHA256"]
            or env.get("AGENT_PRESSURE_PAUSE_POLICY") != "workflow_tool_aware"
        ):
            raise ValueError("require the pinned existing tool-aware work provider")
        old[name] = json.loads(path.read_bytes())
        inputs.append(path)
    verify_same_objective(configs["learned"], configs["oracle"], old["learned"], old["oracle"])
    model, truth = prepare_artifacts(old["learned"], old["oracle"])
    model_path, truth_path = output / "learned_work.json", output / "oracle_work.json"
    write(model_path, model)
    truth["combination_reference_source_model_sha256"] = sha256_file(model_path)
    WorkflowWorkOracleArtifact(truth)
    write(truth_path, truth)
    settings = ReadyWaitSettings.from_artifact(model)
    prepared = {}
    for name, path in (("learned", model_path), ("oracle", truth_path)):
        value = configuration(configs[name], path, sha256_file(path), settings)
        value["comparison_contract"].update(
            workflow_join_progress="linear_unfinished_share_v1",
            tool_retention_policy=asdict(ToolRetentionSettings()),
            continuous_work_utility_requires_matching_scale=True,
        )
        value["status"] = "CPU_prepared_scheduler_parameter_repairs_not_GPU_registered"
        write(output / (name + ".json"), value)
        prepared[name] = value
    verify_same_objective(prepared["learned"], prepared["oracle"], model, truth)
    inputs += [
        Path(__file__), ROOT / "beliefkv/policy/workflow_combination.py",
        ROOT / "beliefkv/runtime/workflow_tool_pressure.py",
        ROOT / "beliefkv/runtime/workflow_combination.py",
        ROOT / "scripts/finish_model_work_oracle.py",
    ]
    report = {
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in inputs},
        "configurations": {
            name: {"path": str((output / (name + ".json")).resolve()),
                   "sha256": sha256_file(output / (name + ".json"))}
            for name in prepared
        },
        "join_progress": "linear_unfinished_share_v1",
        "tool_retention_policy": asdict(ToolRetentionSettings()),
        "ready_wait_policy_unchanged": settings.record(),
        "forecast_parameters_cost_measurements_and_traffic_unchanged": True,
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
    args = parser.parse_args()
    report = prepare(args.learned_config, args.oracle_config, args.output_dir)
    print(json.dumps({"output": str(args.output_dir.resolve()), "GPU_action": report["GPU_action"]}))


if __name__ == "__main__":
    main()
