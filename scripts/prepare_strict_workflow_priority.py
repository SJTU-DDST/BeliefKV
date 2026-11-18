"""Restore matched remaining-work priority without wait or utility overrides."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.workflow_shortest_frontier import strict_remaining_work_enabled
from beliefkv.predictor.workflow_continuous_work import forecast_fingerprint
from beliefkv.predictor.workflow_work import WorkflowWorkModel
from beliefkv.runtime.workflow_work_oracle import WorkflowWorkOracleArtifact
from scripts.finish_model_work_oracle import verify_same_objective
from scripts.prepare_shortest_work_admission import configuration
from scripts.train_workflow_work import write


def prepare_artifacts(model, truth):
    if (
        model["admission_cost_profile"] != truth["admission_cost_profile"]
        or model["admission_cost_profile"]["kind"] != "r5_frozen_batch_cost_proxy_capacity_v7"
        or any(raw.get("strict_remaining_work_admission", False) is not False for raw in (model, truth))
        or any("tool_boundary_preemption" in raw or "pressure_work_order" in raw for raw in (model, truth))
    ):
        raise ValueError("prepare from matched ordinary continuous-work providers without extra preemption")
    result = deepcopy(model), deepcopy(truth)
    for raw in result:
        raw.update(
            strict_remaining_work_admission=True, remaining_work_ranking_enabled=True,
            shortest_work_first_admission=True, cost_aware_maximal_admission=False,
            ready_wait_priority_ms=0, ready_capacity_accumulation_ms=0,
        )
        for name in (
            "ready_wait_priority_grace_ms", "ready_wait_priority_max_boost",
            "maximal_admission_objective",
        ):
            raw.pop(name, None)
        strict_remaining_work_enabled(raw)
    if forecast_fingerprint(result[0]) != forecast_fingerprint(model):
        raise ValueError("strict priority changed prediction parameters")
    WorkflowWorkModel(result[0])
    return result


def prepare(learned_path, oracle_path, output):
    if output.exists():
        raise FileExistsError("preserve all previous preparations")
    configs = {
        name: json.loads(path.read_bytes())
        for name, path in (("learned", learned_path), ("oracle", oracle_path))
    }
    raw, inputs = {}, [learned_path, oracle_path]
    for name, config in configs.items():
        env = config["common_environment"]
        path = Path(env["WORKFLOW_WORK_MODEL"])
        if sha256_file(path) != env["WORKFLOW_WORK_MODEL_SHA256"]:
            raise ValueError("source work provider changed")
        raw[name] = json.loads(path.read_bytes())
        inputs.append(path)
    verify_same_objective(configs["learned"], configs["oracle"], raw["learned"], raw["oracle"])
    model, truth = prepare_artifacts(raw["learned"], raw["oracle"])
    model_path, truth_path = output / "learned_work.json", output / "oracle_work.json"
    write(model_path, model)
    truth["combination_reference_source_model_sha256"] = sha256_file(model_path)
    WorkflowWorkOracleArtifact(truth)
    write(truth_path, truth)
    prepared = {}
    for name, path in (("learned", model_path), ("oracle", truth_path)):
        value = configuration(configs[name], path, sha256_file(path))
        contract = value["comparison_contract"]
        for key in tuple(contract):
            if key.startswith(("ready_wait_", "ready_capacity_")) or key == "maximal_admission_objective":
                contract.pop(key)
        contract.update(
            strict_remaining_work_admission=True, cost_aware_maximal_admission=False,
            ready_wait_priority_ms=0, ready_capacity_accumulation_ms=0,
            selection_strategy="live_JOIN_rights_then_remaining_work_then_ready_sibling_adjacency",
            priority_is_not_divided_by_request_cost_or_JOIN_fanout=True,
            public_timeout_force_resume_enabled=False,
            candidate_reference_is_fallback_not_reserved_prefix_slots=True,
            ready_capacity_accumulation_enabled=False,
        )
        value["status"] = "CPU_prepared_strict_remaining_work_not_GPU_registered"
        write(output / (name + ".json"), value)
        prepared[name] = value
    verify_same_objective(prepared["learned"], prepared["oracle"], model, truth)
    inputs += [
        Path(__file__), ROOT / "beliefkv/policy/workflow_shortest_frontier.py",
        ROOT / "beliefkv/runtime/workflow_combination.py",
        ROOT / "beliefkv/runtime/workflow_size_admission.py",
        ROOT / "beliefkv/runtime/paper_baseline.py",
        ROOT / "scripts/finish_model_work_oracle.py",
    ]
    report = {
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in inputs},
        "configurations": {
            name: {"path": str((output / (name + ".json")).resolve()),
                   "sha256": sha256_file(output / (name + ".json"))}
            for name in prepared
        },
        "same_forecast_model_work_cost_measurements_and_traffic": True,
        "ready_wait_priority_ms": 0, "ready_capacity_accumulation_ms": 0,
        "public_timeout_force_resume_enabled": False,
        "GPU_action": False, "Full_rerun_registered": False,
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
    prepare(args.learned_config, args.oracle_config, args.output_dir)
    print(json.dumps({"output": str(args.output_dir.resolve()), "GPU_action": False}))


if __name__ == "__main__":
    main()
