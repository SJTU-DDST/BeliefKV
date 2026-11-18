"""Fit a causal seconds readout and audit actual R5 choices on the same states."""

import argparse
from collections import Counter
from copy import deepcopy
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.workflow_combination import CombinationCosts
from beliefkv.policy.workflow_shortest_frontier import choose_shortest_work_first
from beliefkv.predictor.workflow_completion_path import (
    KIND as PATH_KIND, TARGET, TIMING_FEATURES, ObservedToolClock, feature_names, path_features,
)
from beliefkv.predictor.workflow_continuous_work import log_work_weight
from beliefkv.predictor.workflow_work import WorkflowWorkModel, work_prediction_features
from beliefkv.predictor.workflow_work_distribution import DistributionProgress, SNAPSHOT_EVENTS
from beliefkv.predictor.workflow_work_readout import representation, representation_fingerprint
from beliefkv.runtime.workflow_work_oracle import WorkflowWorkOracleArtifact, cache_remaining_paths
from scripts.audit_r5_shortest_contract import candidate
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.stop_after_measured_cohort import replay_arm
from scripts.run_causal_gpu_replay import source_plan
from scripts.train_workflow_work import write
from scripts.train_workflow_work_progress_trees import portable_tree
from scripts.verify_measured_execution import verify_execution


class Labels:
    def __init__(self, artifact):
        self.artifact = artifact
        self.done, self.active, self.cache = {}, {}, {}
        self.events, self.requests = {}, {}
        for workflow, graph in artifact.graphs.items():
            self.done[workflow], self.active[workflow] = {}, {}
            for node in graph:
                if node["event_id"] is not None:
                    self.events[node["event_id"]] = workflow, node["node_id"]
                else:
                    self.requests[node["request_id"]] = workflow, node["node_id"]

    def event(self, event):
        known = self.events.get(event.event_id)
        if known is not None:
            workflow, node = known
            if workflow != event.workflow_id:
                raise ValueError("event identity changed its source workflow")
            self.done[workflow][node] = event.ts_ms / 1000.
            self.cache.pop(workflow, None)

    def progress(self, request, output, now, *, completed=False):
        workflow, node = self.requests[request]
        if completed:
            self.done[workflow][node] = now
            self.active[workflow].pop(request, None)
        else:
            self.active[workflow][request] = output
        self.cache.pop(workflow, None)

    def at(self, workflow, now):
        if workflow not in self.cache:
            a = self.artifact
            self.cache[workflow] = cache_remaining_paths(
                a.graphs[workflow], a.requests, self.done[workflow], self.active[workflow],
                a.model_seconds_per_work_token,
                prefill_seconds_per_token=a.model_prefill_seconds_per_token,
                prefill_done_after_output=a.model_prefill_progress == "first_observed_output",
            )
        return self.cache[workflow].at(now)


def collect(registration, cohort_path, oracle_path, output):
    plan = json.loads(registration.read_bytes())
    proof = verify_execution(registration, cohort_path)
    config = json.loads(Path(plan["experiment_configuration"]).read_bytes())
    base_path = Path(config["common_environment"]["WORKFLOW_WORK_MODEL"])
    oracle = WorkflowWorkOracleArtifact.load(oracle_path, sha256_file(oracle_path))
    graph, _source, evidence = source_plan(Path(plan["source_arm"]))
    oracle.validate_source(Path(plan["source_arm"]), graph, evidence)
    reader, arm, timeline, decisions = SealedRecords(), replay_arm(plan), [], []
    for ordinal, raw in enumerate(reader(arm / "replay/runtime_events.replay.jsonl")):
        event = RuntimeEvent.from_dict(raw)
        timeline.append((event.ts_ms / 1000., 0, ordinal, event))
    receipts = {}
    for raw in reader(arm / "replay/native_requests.jsonl"):
        request = raw["request_id"]
        if request not in oracle.requests or request in receipts or raw["exact_tokens_verified"] is not True:
            raise ValueError("completed request differs from the fixed oracle work")
        item = oracle.requests[request]
        if (raw["prompt_tokens"], raw["output_tokens"]) != (item["prompt_tokens"], item["output_tokens"]):
            raise ValueError("completion-path labels changed request work")
        receipts[request] = raw
        timeline.append((raw["native_complete_monotonic_ms"] / 1000., 1, len(timeline), request))
    generated = Counter()
    for raw in reader(arm / "server/runtime_audit.jsonl"):
        if raw.get("event") != "gpu_service_sample" or raw["phase"] != "decode":
            continue
        for member in raw["request_samples"]:
            request, count = member["request_id"], member["output_tokens_after"]
            if request not in receipts or count >= receipts[request]["output_tokens"]:
                continue
            if count - generated[request] >= 128:
                generated[request] = count
                timeline.append((raw["complete_monotonic_ms"] / 1000., 2, len(timeline), (request, count)))
    for raw in reader(arm / "opportunities/admission_opportunities.jsonl"):
        if raw.get("event") == "workflow_combination_selection" and raw["candidates"]:
            timeline.append((raw["monotonic_s"], 3, len(timeline), raw))
    labels, states, clocks, task_text, snapshots = Labels(oracle), {}, {}, {}, []
    completed, submitted = set(), set()
    for now, action, _ordinal, value in sorted(timeline, key=lambda item: item[:3]):
        snapshot_workflow = None
        if action == 0:
            event, workflow = value, value.workflow_id
            if workflow not in states:
                states[workflow], clocks[workflow] = DistributionProgress(), ObservedToolClock()
            state = states[workflow]
            state.event(event)
            clocks[workflow].event(event, now)
            labels.event(event)
            request = event.attributes.get("request_id")
            if event.kind is Kind.LLM_SUBMIT:
                item = oracle.requests[request]
                state.started_summary(
                    request, event.invocation_id, event.context_id, item["prompt_tokens"], item["input_increment"],
                )
                submitted.add(request)
            if event.kind in SNAPSHOT_EVENTS and event.kind is not Kind.WORKFLOW_END:
                snapshot_workflow = workflow
            if state.text:
                task_text.setdefault(workflow, state.text)
        elif action in (1, 2):
            request, count = (value, receipts[value]["output_tokens"]) if action == 1 else value
            item = oracle.requests[request]
            workflow = item["workflow_id"]
            if request in completed:
                continue
            if request not in submitted:
                raise ValueError("native output precedes observed request submission")
            state = states[workflow]
            if action == 1:
                state.completed_summary(
                    request, item["invocation_id"], item["context_id"],
                    item["prompt_tokens"], item["output_tokens"], item["input_increment"],
                )
                completed.add(request)
            else:
                state.generated(request, count)
            labels.progress(request, count, now, completed=action == 1)
            snapshot_workflow = workflow
        else:
            row = {
                key: value[key] for key in (
                    "source", "monotonic_s", "decision", "candidates", "resources", "predictions", "keys",
                )
            }
            row["states"] = {
                workflow: {
                    "features": states[workflow].features(), "timing": clocks[workflow].features(now),
                    "path": labels.at(workflow, now),
                } for workflow in {item["workflow"] for item in value["candidates"]}
            }
            decisions.append(row)
        if snapshot_workflow is not None and states[snapshot_workflow].text:
            state = states[snapshot_workflow]
            snapshots.append({
                "workflow": snapshot_workflow, "features": state.features(),
                "timing": clocks[snapshot_workflow].features(now),
                "path": labels.at(snapshot_workflow, now),
            })
    if set(receipts) != completed or set(plan["workflow_ids"]) != set(task_text):
        raise ValueError("require all complete workflows and original bounded task metadata")
    dataset = {
        "rows": snapshots, "decisions": decisions, "task_text": task_text,
        "base_model": str(base_path), "base_sha256": sha256_file(base_path),
        "oracle_artifact": str(oracle_path.resolve()),
        "registration": str(registration), "cohort": json.loads(cohort_path.read_bytes()),
        "model_seconds_per_work_token": oracle.model_seconds_per_work_token,
        "complete_measured_requests": proof["measured_requests"],
        "complete_measured_outputs": proof["measured_output_tokens"],
        "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {
            str(path.resolve()): sha256_file(path) for path in (
                registration, cohort_path, oracle_path, base_path, Path(__file__),
            )
        },
        "target_is_fixed_service_and_tool_DAG_not_observed_JCT": True,
        "future_graph_only_in_offline_labels": True,
        "full_prompts_and_tool_output_excluded_from_features": True,
        "event_publication_is_not_proof_of_server_application_at_the_same_instant": True,
        "GPU_action": False,
    }
    write(output, dataset)
    return dataset


def fit(dataset, dataset_path, output):
    import lightgbm as lgb
    import numpy as np

    base = WorkflowWorkModel.load(Path(dataset["base_model"]), dataset["base_sha256"])
    measured = set(dataset["cohort"]["measured_workflow_ids"])
    metadata = base.metadata
    calibration_projects = set(metadata["calibration_projects"])
    # Original project names are present in the source registration's composition.
    oracle_path = Path(dataset["oracle_artifact"])
    artifact = json.loads(oracle_path.read_bytes())
    composition = json.loads((Path(artifact["source_binding"]["source_arm"]) / "composite_source.json").read_bytes())
    projects = {row["workflow_id"]: row["project"] for row in composition["selected_workflows"]}
    partition = {
        workflow: "evaluation" if workflow in measured else
        "calibration" if projects[workflow] in calibration_projects else "training"
        for workflow in dataset["task_text"]
    }
    projections = {workflow: base.task_encoding(text)[0] for workflow, text in dataset["task_text"].items()}
    if any(value is None for value in projections.values()):
        raise ValueError("path readout requires supported task projections")

    def values(workflow, row):
        projection = projections[workflow]
        observed = np.asarray(row["features"], dtype=float)
        transformed = work_prediction_features(observed, task_only_before_work=base.task_only_before_work)
        hidden = representation(base, projection.progress_projection, observed)
        return path_features(transformed, projection.task_structure, hidden, row["timing"])

    rows = dataset["rows"]
    x = np.asarray([values(row["workflow"], row) for row in rows])
    y = np.log1p([row["path"][0] for row in rows])
    split = np.asarray([partition[row["workflow"]] for row in rows])
    training, calibration = split == "training", split == "calibration"
    if not training.any() or not calibration.any() or not (split == "evaluation").any():
        raise ValueError("preserve whole-workflow training, calibration, and fixed18 evaluation")
    counts = Counter(row["workflow"] for row in rows if partition[row["workflow"]] == "training")
    weights = np.asarray([1. / counts[row["workflow"]] for row in rows if partition[row["workflow"]] == "training"])
    weights *= len(weights) / weights.sum()
    settings = {
        "objective": "regression", "num_threads": 1, "deterministic": True, "force_col_wise": True,
        "seed": 21, "verbosity": -1, "num_leaves": 8, "max_depth": 4, "learning_rate": .05,
        "min_data_in_leaf": 80, "lambda_l2": 10.,
    }
    trained = lgb.train(settings, lgb.Dataset(x[training], label=y[training], weight=weights), num_boost_round=64)
    scores = trained.predict(x, raw_score=True, num_threads=1)
    calibration_counts = Counter(row["workflow"] for row in rows if partition[row["workflow"]] == "calibration")
    calibration_weights = np.asarray([
        1. / calibration_counts[row["workflow"]] for row in rows if partition[row["workflow"]] == "calibration"
    ])
    offset = float(np.average(y[calibration] - scores[calibration], weights=calibration_weights))
    timing = np.asarray([row["timing"] for row in rows])
    reference = float(np.median([row["path"][0] for row in rows if partition[row["workflow"]] == "training"]))
    raw = {
        "kind": PATH_KIND, "target": TARGET, "representation_sha256": representation_fingerprint(base),
        "features": list(feature_names(base)), "trees": [
            portable_tree(tree["tree_structure"]) for tree in trained.dump_model()["tree_info"]
        ], "maximum_depth": 4, "calibration_offset": offset, "reference_path_s": reference,
        "model_seconds_per_work_token": dataset["model_seconds_per_work_token"],
        "calibration_status": "whole_workflow_heldout", "future_graph_used_online": False,
        "training_source": {"path": str(dataset_path.resolve()), "sha256": sha256_file(dataset_path)},
        "feature_log_min": timing[training].min(axis=0).tolist(),
        "feature_log_max": timing[training].max(axis=0).tolist(),
    }
    model_raw = deepcopy(base.raw)
    model_raw["completion_path_readout"] = raw
    model = WorkflowWorkModel(model_raw)
    checkpoints = np.unique(np.linspace(0, len(rows) - 1, min(256, len(rows)), dtype=int))
    error = max(abs(model.completion_path.tree.score(x[index]) - scores[index]) for index in checkpoints)
    if error > 1e-9:
        raise ValueError("portable completion-path head differs from fitted LightGBM")
    write(output / "model.json", model_raw)
    costs = CombinationCosts(**base.raw["admission_cost_profile"]["costs"])
    diagnostics = Counter()
    choices = []
    projection_cache = {workflow: model.task_encoding(text)[0] for workflow, text in dataset["task_text"].items()}
    for row in dataset["decisions"]:
        candidates = [candidate(value) for value in row["candidates"]]
        selected = set(row["decision"]["selected"])
        described = {value.identity for value in candidates}
        if row["resources"] is None or not selected <= described:
            diagnostics["incomplete_planning_metadata"] += 1
            continue
        from beliefkv.policy.workflow_combination import CombinationResources

        resources = CombinationResources(**row["resources"])
        required = row["decision"].get("required_reference_members", ())
        forecasts = {}
        for workflow, state in row["states"].items():
            _, predicted = model.predict(
                projection_cache[workflow], state["features"], with_work=True, path_timing=state["timing"],
            )
            forecasts[workflow] = predicted
        weights = {
            "true_model_path": {wf: 1. / (1. + state["path"][1] / reference) for wf, state in row["states"].items()},
            "true_model_tool_path": {wf: 1. / (1. + state["path"][0] / reference) for wf, state in row["states"].items()},
            "predicted_model_tool_path": {
                wf: 1. / (1. + forecast["remaining_path_s"] / reference)
                if "remaining_path_s" in forecast else log_work_weight(
                    forecast["log1p_remaining_work"], reference / dataset["model_seconds_per_work_token"],
                ) for wf, forecast in forecasts.items()
            },
        }
        selections = {}
        for name, mapping in weights.items():
            choice = choose_shortest_work_first(
                [replace(value, weight=mapping[value.workflow]) for value in candidates],
                row["decision"].get("reference", ()), resources, costs, required=required,
                strict_remaining_work=True, clock=lambda: 0,
            )
            selections[name] = list(choice["selected"])
            diagnostics[name + "_set_changes"] += set(choice["selected"]) != selected
        diagnostics["model_path_vs_model_tool_path_set_changes"] += (
            set(selections["true_model_path"]) != set(selections["true_model_tool_path"])
        )
        diagnostics["predicted_path_vs_true_path_set_differences"] += (
            set(selections["predicted_model_tool_path"]) != set(selections["true_model_tool_path"])
        )
        choices.append({
            "monotonic_s": row["monotonic_s"], "actual": list(row["decision"]["selected"]),
            "choices": selections, "workflows": {
                wf: {"split": partition[wf], "truth_path_s": state["path"][0], **forecasts[wf]}
                for wf, state in row["states"].items()
            },
        })
    errors = {}
    for name in ("training", "calibration", "evaluation"):
        mask = split == name
        errors[name] = {
            "workflows": sum(value == name for value in partition.values()), "snapshots": int(mask.sum()),
            "mean_absolute_log_error": float(np.mean(np.abs(scores[mask] + offset - y[mask]))),
        }
    report = {
        "split": partition, "errors": errors, "choice_counts": dict(diagnostics),
        "choices": choices, "portable_score_max_error": error, "training_parameters": settings,
        "fixed_boost_rounds": 64, "test_labels_used_for_training_or_selection": False,
        "model_sha256": sha256_file(output / "model.json"),
        "inputs_sha256": {str(dataset_path.resolve()): sha256_file(dataset_path)},
        "scope": {
            "same_recorded_candidate_windows_and_planning_resources": True,
            "all_state_reforecast_does_not_reproduce_async_cache_lag": True,
            "future_trace_only_used_for_offline_targets_and_diagnostics": True,
            "does_not_simulate_future_queues_transfers_or_JCT": True,
            "no_GPU_performance_gain_proven": True,
        }, "GPU_action": False,
    }
    write(output / "evaluation.json", report)
    print(json.dumps({"errors": errors, "choice_counts": dict(diagnostics), "model": str(output / "model.json")}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path)
    for name in ("registration", "cohort", "oracle"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("require a new CPU-only preparation directory")
    args.output_dir.mkdir(parents=True)
    started = time.monotonic()
    if args.dataset is None:
        if any(value is None for value in (args.registration, args.cohort, args.oracle)):
            parser.error("collect the exact completed registration, cohort, and oracle")
        dataset_path = args.output_dir / "dataset.json"
        dataset = collect(args.registration, args.cohort, args.oracle, dataset_path)
    else:
        dataset_path = args.dataset
        dataset = json.loads(dataset_path.read_bytes())
    fit(dataset, dataset_path, args.output_dir)
    print(json.dumps({"CPU_elapsed_s": time.monotonic() - started, "GPU_action": False}))


if __name__ == "__main__":
    main()
