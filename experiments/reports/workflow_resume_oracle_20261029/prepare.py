"""Bind a continuous remaining-model-work oracle to the complete fixed workload."""

import argparse
from collections import Counter
from copy import deepcopy
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.causal_replay_tokens import CapturedTokenCorpus
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_work import WorkflowProgress
from beliefkv.runtime.workflow_work_oracle import (
    KIND, WorkflowWorkOracleArtifact, critical_path_graphs,
)
from beliefkv.experiments.tool_timing_overlay import load_overlay
from scripts import run_causal_comparison as causal
from scripts.run_causal_gpu_replay import source_plan
from scripts.train_workflow_work import write


def artifact(source, model, dataset, overlay, model_seconds_per_work_token):
    graph, expected, evidence = source_plan(source)
    labels = {}
    for row in dataset["rows"]:
        workflow, value = row["logical_workflow_id"], row["total_work"]
        if workflow in labels and labels[workflow] != value:
            raise ValueError("conflicting total workflow work labels")
        labels[workflow] = value
    if set(labels) != {node.workflow_id for node in graph.nodes}:
        raise ValueError("oracle labels have another execution population")
    if dataset["prefill_token_weight"] != .01 or model["metadata"]["prefill_token_weight"] != .01:
        raise ValueError("oracle and learned model work units differ")
    events = sorted(
        (node.event for node in graph.nodes if node.event is not None),
        key=lambda event: event.ts_ms,
    )
    states, previous, requests, totals, members = {}, {}, {}, Counter(), Counter()
    with CapturedTokenCorpus(source / "server/request_tokens.jsonl", expected) as corpus:
        for event in events:
            if event.kind is not Kind.LLM_RESULT:
                continue
            request = event.attributes["request_id"]
            if request in requests:
                raise ValueError("duplicate source request completion")
            value = corpus.get(request)
            progress = states.setdefault(event.workflow_id, WorkflowProgress())
            increment = progress.completed(
                request, event.invocation_id, event.context_id, value.prompt_tokens, value.output_tokens,
            )
            context = event.workflow_id, event.context_id
            requests[request] = {
                "workflow_id": event.workflow_id, "invocation_id": event.invocation_id,
                "context_id": event.context_id, "prompt_tokens": len(value.prompt_tokens),
                "output_tokens": len(value.output_tokens), "input_increment": increment,
                "predecessor_request_id": previous.get(context),
            }
            previous[context] = request
            totals[event.workflow_id] += increment + 100 * len(value.output_tokens)
            members[event.workflow_id] += 1
    if requests.keys() != graph.request_usage.keys() or any(
        not math.isclose(totals[workflow] / 100., labels[workflow], abs_tol=1e-8)
        for workflow in labels
    ):
        raise ValueError("oracle complete work does not reconstruct learned labels")
    result = {
        "kind": KIND, "future_information_used": True, "deployable": False,
        "prefill_token_weight": .01, "work_thresholds": model["work_thresholds"],
        "model_seconds_per_work_token": model_seconds_per_work_token,
        "workflow_graphs": critical_path_graphs(load_overlay(graph, overlay)[0]),
        "source_binding": {
            "source_arm": str(source.resolve()), "source_evidence": evidence,
            "token_path": str((source / "server/request_tokens.jsonl").resolve()),
            "token_sha256": sha256_file(source / "server/request_tokens.jsonl"),
            "tool_overlay_path": str(overlay.resolve()), "tool_overlay_sha256": sha256_file(overlay),
        },
        "workflows": {
            workflow: {"total_work_units": total, "requests": members[workflow]}
            for workflow, total in sorted(totals.items())
        },
        "requests": requests,
        "scope": (
            "Exact future necessary input increments, output lengths, tool durations and fixed "
            "replay DAG determine remaining critical path. Model token cost is calibrated before "
            "this experiment, not exact future GPU time. Actual events/output update progress. "
            "The oracle graph changes ranks only, not JOIN/physical/admission authority. "
            "No future wall-clock completion is injected. This is not an optimal JCT bound."
        ),
    }
    WorkflowWorkOracleArtifact(result).validate_source(source, graph, evidence)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    base = ROOT / "configs/migration/predictive_workflow_dispatch_20261029.json"
    source = ROOT / "experiments/raw/locobench_parallel_retry_source_20261026_v230/native_capture"
    dataset_path = ROOT / "experiments/reports/workflow_work_training_20261029/native_progress_fit_r1/evaluation.json"
    candidate = deepcopy(causal.load(base))
    common = candidate["common_environment"]
    model_path = Path(common["WORKFLOW_WORK_MODEL"])
    overlay = ROOT / candidate["tool_timing_overlay"]["path"]
    calibration = ROOT / "experiments/reports/decay_tick2s_no_handoff_20261030/queue_r1/complete_review/review.json"
    decode = causal.load(calibration)["metrics"]["FULL"]["decode"]
    model_seconds_per_work_token = decode["request_step_ms"] / decode["request_steps"] / 1000.
    oracle = artifact(
        source, causal.load(model_path), causal.load(dataset_path), overlay,
        model_seconds_per_work_token,
    )
    oracle["model_cost_calibration"] = {
        "path": str(calibration), "sha256": sha256_file(calibration),
        "definition": "complete_original_Full_decode_request_step_ms/request_steps/1000",
        "prefill_equivalent_weight": .01,
        "limitations": "fixed context/batch-averaged model proxy, not future exact GPU service seconds",
    }
    oracle_path = args.output_dir / "oracle.json"
    write(oracle_path, oracle)
    common.update({
        "WORKFLOW_WORK_SOURCE": "oracle", "BELIEFKV_WORKFLOW_WORK_SOURCE": "oracle",
        "WORKFLOW_WORK_MODEL": str(oracle_path.resolve()),
        "BELIEFKV_WORKFLOW_WORK_MODEL": str(oracle_path.resolve()),
        "WORKFLOW_WORK_MODEL_SHA256": sha256_file(oracle_path),
        "BELIEFKV_WORKFLOW_WORK_MODEL_SHA256": sha256_file(oracle_path),
    })
    candidate.update({
        "status": "explicit_oracle_requires_source_bound_CPU_and_GPU_qualification",
        "candidate_provenance": {
            "base": str(base), "base_sha256": sha256_file(base),
            "oracle_scope": "remaining_model_tool_critical_path_seconds",
            "future_information_used": True, "deployable": False,
            "only_changed_policy": "workflow_rank_provider_and_continuous_priority",
            "native_engine_changed": False,
        },
        "gpu_execution": {
            "registered": False, "requires_fresh_position_and_sampling": True,
            "requires_decision_eligible_model": False, "requires_explicit_oracle": True,
        },
        "performance_accepted": False,
    })
    candidate["workflow_work_qualification"] = {
        "kind": KIND, "artifact": str(oracle_path.resolve()),
        "artifact_sha256": sha256_file(oracle_path),
        "future_information_used": True, "deployable": False,
        "scope": "remaining_model_tool_critical_path_seconds",
        "learned_comparator_model": str(model_path),
        "learned_comparator_model_sha256": sha256_file(model_path),
        "learned_calibration_requirement_applies": False,
    }
    candidate["comparison_contract"].update({
        "workflow_work_prediction": "future_model_tokens_tool_durations_DAG_and_actual_progress",
        "workflow_resume_key": [
            "original_category", "oracle_remaining_model_tool_path_seconds", "context_tokens", "queue_sequence",
        ],
        "future_information_used": True, "deployable": False,
        "workflow_work_uncertainty_fallback": "not_applicable_bound_oracle_rejects_mismatch",
        "workflow_free_dispatch_unknown_model_fallback": "not_applicable_bound_oracle",
        "workflow_ready_join_sibling_preference": "within_category_and_workflow_path_fitting_subsets",
    })
    preflights = {}
    for mode in ("full", "scheduler_only", "native_matched"):
        configured = arm_environment(common, mode)
        for name in causal.SERVER_FIELDS:
            if name in configured:
                configured["BELIEFKV_" + name] = configured[name]
        preflights[mode] = causal.preflight_runtime(configured, common["MODEL_PATH"])
        if mode != "full" and configured["BELIEFKV_WORKFLOW_WORK_SOURCE"] != "learned":
            raise ValueError("oracle leaked into a control")
    write(args.output_dir / "full.json", candidate)
    write(args.output_dir / "preparation.json", {
        "inputs_sha256": {
            str(path): sha256_file(path) for path in (base, dataset_path, model_path, Path(__file__))
        },
        "oracle": {"path": str(oracle_path.resolve()), "sha256": sha256_file(oracle_path)},
        "configuration": {
            "path": str((args.output_dir / "full.json").resolve()),
            "sha256": sha256_file(args.output_dir / "full.json"),
        },
        "constructor_preflights": preflights,
        "requests": len(oracle["requests"]), "workflows": len(oracle["workflows"]),
        "total_logical_work": sum(x["total_work_units"] for x in oracle["workflows"].values()) / 100.,
        "future_information_used": True, "GPU_experiment_started": False,
    })
    print({"requests": len(oracle["requests"]), "workflows": len(oracle["workflows"])})


if __name__ == "__main__":
    main()
