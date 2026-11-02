"""Train R5 on complete executions with causal features and source-bound work labels."""

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import EventConfidence, RuntimeEvent, RuntimeEventKind as Kind, is_unobserved_join_event
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_work import R5_SCHEMA, WorkflowWorkModel
from beliefkv.predictor.workflow_work_distribution import DistributionProgress, SNAPSHOT_EVENTS, support_status
from beliefkv.runtime.workflow_work_oracle import WorkflowWorkOracleArtifact
from scripts.train_workflow_work import (
    NATIVE_PROGRESS, _native_progress_records, capture_split, fit,
    native_decode_progress, probabilities, write,
)


def label_sources(binding):
    evidence = {}
    for name, digest in (("token_path", "token_sha256"), ("tool_overlay_path", "tool_overlay_sha256")):
        path = Path(binding[name])
        observed = sha256_file(path)
        if observed != binding[digest]:
            raise ValueError("a complete-work label source changed: " + name)
        evidence[str(path.resolve())] = observed
    return evidence


def bind_parent_dataset(values, parent_path, *, additional_sources=None):
    parent = json.loads(parent_path.read_bytes())

    def without_text(row):
        return {
            key: list(value) if key == "features" else value
            for key, value in row.items() if key not in ("text", "task_text")
        }

    old_sources, new_sources = parent["sources_sha256"], values["sources_sha256"]
    additional_sources = additional_sources or {}
    if (
        any(new_sources.get(path) != digest for path, digest in old_sources.items())
        or any(new_sources[path] != additional_sources.get(path)
               for path in new_sources.keys() - old_sources.keys())
        or len(parent["rows"]) != len(values["rows"])
        or any(without_text(left) != without_text(right)
               for left, right in zip(parent["rows"], values["rows"]))
        or any(left["text"] != right["task_text"]
               for left, right in zip(parent["rows"], values["rows"]))
    ):
        raise ValueError("delegation export changed source work, features, or snapshot boundaries")
    values["parent_dataset"] = {
        "path": str(parent_path.resolve()), "sha256": sha256_file(parent_path),
        "original_fields_except_text_equal": True,
        "additional_label_source_hashes": sorted(new_sources.keys() - old_sources.keys()),
    }
    return values


def snapshots(events, requests, *, progress=(), observed_delegations=False):
    """Future request totals are labels; observations expose them only after actual service."""
    if len({event.workflow_id for event in events}) != 1:
        raise ValueError("export one complete original workflow at a time")
    workflow = events[0].workflow_id
    expected = {key: row for key, row in requests.items() if row["workflow_id"] == workflow}
    submitted, completed = set(), set()
    total = sum(row["input_increment"] + 100 * row["output_tokens"] for row in expected.values()) / 100.
    state, result = DistributionProgress(), []
    observations = [(event.ts_ms, index, event) for index, event in enumerate(events)]
    observations.extend(
        (row["ts_ms"], len(events) + index, row) for index, row in enumerate(progress)
    )
    observations.sort(key=lambda row: row[:2])
    for _when, _order, event in observations:
        if isinstance(event, dict):
            if event["request_id"] not in submitted or event["request_id"] in completed:
                raise ValueError("decode observation is outside its observed request lifetime")
            state.generated(event["request_id"], event["output_tokens"])
            name = "native_decode_progress"
        else:
            if event.kind is Kind.WORKFLOW_END:
                continue
            if event.confidence is EventConfidence.INFERRED or is_unobserved_join_event(event):
                continue
            state.event(event)
            request = event.attributes.get("request_id")
            if event.kind in (Kind.LLM_SUBMIT, Kind.LLM_RESULT):
                if request not in expected or any(
                    expected[request][name] != getattr(event, name)
                    for name in ("workflow_id", "invocation_id", "context_id")
                ):
                    raise ValueError("source model boundary differs from the bound label identity")
                item = expected[request]
                if event.kind is Kind.LLM_SUBMIT:
                    if request in submitted:
                        raise ValueError("duplicate logical request submission")
                    submitted.add(request)
                    state.started_summary(
                        request, event.invocation_id, event.context_id,
                        item["prompt_tokens"], item["input_increment"],
                    )
                else:
                    if request not in submitted or request in completed:
                        raise ValueError("invalid logical completion")
                    completed.add(request)
                    state.completed_summary(
                        request, event.invocation_id, event.context_id,
                        item["prompt_tokens"], item["output_tokens"], item["input_increment"],
                    )
            if event.kind not in SNAPSHOT_EVENTS:
                continue
            name = event.kind.value
        attained = state.counts["input_tokens"] / 100. + state.counts["output_tokens"]
        if attained > total + 1e-6:
            raise ValueError("observed logical work exceeds the complete workflow label")
        result.append({
            "snapshot": len(result), "features": state.features(), "event": name,
            "attained_work": attained, "remaining_work": max(0., total - attained), "total_work": total,
            "text": state.prediction_text(observed_delegations=observed_delegations),
            **({"task_text": state.text} if observed_delegations else {}),
        })
    if submitted != expected.keys() or completed != expected.keys() or not state.text:
        raise ValueError("require complete original work and explicit initial task metadata")
    if not math.isclose(total, state.counts["input_tokens"] / 100. + state.counts["output_tokens"]):
        raise ValueError("observed work does not conserve the complete workflow label")
    if sum(event.kind is Kind.WORKFLOW_END for event in events) != 1:
        raise ValueError("require a complete source workflow")
    return result


def export(artifact_path, *, observed_delegations=False):
    artifact_bytes = artifact_path.read_bytes()
    raw = json.loads(artifact_bytes)
    artifact = WorkflowWorkOracleArtifact(raw)
    binding = raw["source_binding"]
    composition = Path(binding["source_arm"]) / "composite_source.json"
    declaration = json.loads(composition.read_bytes())
    if sha256_file(composition) != binding["source_evidence"]["composite_source_sha256"]:
        raise ValueError("the complete original source composition changed")
    selected = declaration["selected_workflows"]
    if {row["workflow_id"] for row in selected} != artifact.workflows.keys():
        raise ValueError("source population and complete-work labels differ")
    by_capture = defaultdict(list)
    evidence = {str(artifact_path.resolve()): hashlib.sha256(artifact_bytes).hexdigest(),
                str(composition.resolve()): sha256_file(composition)}
    evidence.update(label_sources(binding))
    for item in selected:
        capture = Path(item["source_capture"])
        paths = list(capture.glob(f"client_*/workflows/{item['task_id']}/runtime_events.deepagents.jsonl"))
        if len(paths) != 1 or sha256_file(paths[0]) != item["event_sha256"]:
            raise ValueError("the original complete workflow event stream changed")
        events = [RuntimeEvent.from_dict(json.loads(line)) for line in paths[0].open()]
        if {event.workflow_id for event in events} != {item["workflow_id"]}:
            raise ValueError("original source ownership changed")
        evidence[str(paths[0].resolve())] = item["event_sha256"]
        by_capture[capture].append((item, events))
    rows = []
    for capture, workflows in by_capture.items():
        workflow_ids = {item["workflow_id"] for item, _events in workflows}
        lengths = {
            request: item["output_tokens"] for request, item in artifact.requests.items()
            if item["workflow_id"] in workflow_ids
        }
        service = capture / "server/runtime_audit.jsonl"
        streamed = native_decode_progress(
            (event for _item, events in workflows for event in events),
            _native_progress_records(service), lengths,
        )
        evidence[str(service.resolve())] = sha256_file(service)
        observations = defaultdict(list)
        for row in streamed:
            observations[row["workflow_id"]].append(row)
        for item, events in workflows:
            workflow = item["workflow_id"]
            values = snapshots(
                events, artifact.requests, progress=observations[workflow],
                observed_delegations=observed_delegations,
            )
            for row in values:
                rows.append({
                    **row, "workflow": str(capture.resolve()) + ":" + workflow,
                    "logical_workflow_id": workflow, "source_capture": str(capture.resolve()),
                    "source_workflow_sha256": item["event_sha256"], "project": item["project"],
                    "task_template": hashlib.sha256(values[0]["text"].encode()).hexdigest(),
                })
    if any(sha256_file(Path(path)) != digest for path, digest in evidence.items()):
        raise ValueError("a source changed during conditional-work export")
    return {
        "schema": R5_SCHEMA, "rows": rows, "sources_sha256": evidence, "prefill_token_weight": .01,
        "progress_observation_contract": NATIVE_PROGRESS,
        "future_source_graph_used_for_labels_only": True,
        "future_tool_waits_excluded_from_model_work_target": True,
        "full_prompt_and_tool_outputs_excluded_from_features": True,
        "complete_original_workflows": len(artifact.workflows),
        "observed_delegation_inputs": observed_delegations,
    }


def split_rows(rows, measured_ids, *, calibration_workflows=12, seed=21):
    if len(set(measured_ids)) != len(measured_ids):
        raise ValueError("the fixed measurement population contains duplicate workflows")
    by_workflow = defaultdict(list)
    for row in rows:
        by_workflow[row["logical_workflow_id"]].append(row)
    if not set(measured_ids) <= by_workflow.keys():
        raise ValueError("the complete fixed measurement population is absent")
    background = sorted(
        by_workflow.keys() - set(measured_ids),
        key=lambda identity: hashlib.sha256(f"{seed}:{identity}".encode()).digest(),
    )
    if not 0 < calibration_workflows < len(background):
        raise ValueError("reserve whole executions for training and calibration")
    calibration = set(background[:calibration_workflows])
    training = [row for identity in background if identity not in calibration for row in by_workflow[identity]]
    calibrated = [row for identity in background if identity in calibration for row in by_workflow[identity]]
    test = [row for identity in measured_ids for row in by_workflow[identity]]
    contract = capture_split(training, calibrated, test, require_capture_disjoint=False)
    return training, calibrated, test, contract


def distribution_metrics(model, rows):
    import numpy as np

    if not rows:
        raise ValueError("distribution metrics require complete evaluation executions")
    scores = probabilities(model, rows)
    targets = np.searchsorted(model.thresholds, [row["remaining_work"] for row in rows], side="right")
    counts = Counter(row["workflow"] for row in rows)
    weights = np.asarray([1. / counts[row["workflow"]] / len(counts) for row in rows])
    truth = np.eye(3)[targets]
    nll = float(np.sum(-weights * np.log(np.maximum(scores[np.arange(len(rows)), targets], 1e-12))))
    brier = float(np.sum(weights * ((scores - truth) ** 2).sum(axis=1)))
    confidence, predicted = scores.max(axis=1), scores.argmax(axis=1)
    confusion = np.zeros((3, 3))
    for target, prediction, weight in zip(targets, predicted, weights):
        confusion[target, prediction] += weight
    f1 = [
        2 * confusion[index, index] / max(1e-12, confusion[index].sum() + confusion[:, index].sum())
        for index in range(3)
    ]
    ece = 0.
    for index in range(10):
        mask = (confidence >= index / 10.) & (confidence < (index + 1) / 10. if index < 9 else confidence <= 1.)
        mass = weights[mask].sum()
        if mass:
            ece += abs(float(np.sum(weights[mask] * ((predicted[mask] == targets[mask]) - confidence[mask]))))
    coverage = {}
    from scripts.train_workflow_work import model_text

    for row in rows:
        key = row["workflow"], model_text(model, row)
        if key not in coverage:
            coverage[key] = model.task_encoding(key[1])[1]
    reasons = [
        support_status(model, row["features"], coverage[(row["workflow"], model_text(model, row))])
        for row in rows
    ]
    supported = np.asarray([reason == "calibrated_distribution" for reason in reasons])
    long = targets == 2
    initial = np.asarray([row["snapshot"] == 0 for row in rows])
    q = scores @ np.asarray((1., .5, .25))
    q = np.where(supported, q, .5)
    return {
        "workflows": len(counts), "projects": len({row["project"] for row in rows}),
        "snapshots": len(rows), "weighted_confusion": confusion.tolist(),
        "workflow_equal_weight_accuracy": float(weights[predicted == targets].sum()),
        "macro_f1": float(np.mean(f1)),
        "initial_accuracy": float(np.mean((predicted == targets)[initial])) if initial.any() else None,
        "initial_supported_distribution_coverage": float(np.mean(supported[initial])) if initial.any() else None,
        "long_to_short_rate": float(
            weights[supported & long & (predicted == 0)].sum()
            / max(1e-12, weights[supported & long].sum())
        ),
        "long_to_short_denominator": "supported_true_long_probability_mass",
        "true_long_supported_coverage": float(
            weights[supported & long].sum() / max(1e-12, weights[long].sum())
        ),
        "support_reason_equal_workflow_mass": {
            reason: float(weights[np.asarray([item == reason for item in reasons])].sum())
            for reason in sorted(set(reasons))
        },
        "workflow_equal_weight_NLL": nll,
        "workflow_equal_weight_Brier": brier, "workflow_equal_weight_ECE_10": ece,
        "supported_distribution_coverage": float(weights[supported].sum()),
        "mean_effective_probability_weight": float(np.sum(weights * q)),
        "maximum_softmax_threshold_used_for_R5_support": False,
        "runtime_uses_probability_weight_not_argmax": True,
        "real_admission_competition_accuracy": "requires_new_actual_control_snapshots",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    exporting = sub.add_parser("export")
    exporting.add_argument("--labels", type=Path, required=True)
    exporting.add_argument("--output", type=Path, required=True)
    exporting.add_argument("--observed-delegations", action="store_true")
    exporting.add_argument("--parent-dataset", type=Path)
    training = sub.add_parser("fit")
    training.add_argument("--dataset", type=Path, required=True)
    training.add_argument("--cohort", type=Path, required=True)
    training.add_argument("--output-dir", type=Path, required=True)
    training.add_argument("--cost-artifact", type=Path, required=True)
    training.add_argument("--epochs", type=int, default=200)
    args = parser.parse_args()
    if args.mode == "export":
        if args.output.exists():
            raise FileExistsError("preserve conditional-work exports")
        values = export(args.labels, observed_delegations=args.observed_delegations)
        if args.parent_dataset is not None:
            binding = json.loads(args.labels.read_bytes())["source_binding"]
            try:
                bind_parent_dataset(values, args.parent_dataset, additional_sources={
                    binding["token_path"]: binding["token_sha256"],
                    binding["tool_overlay_path"]: binding["tool_overlay_sha256"],
                })
            except ValueError:
                write(args.output.with_suffix(".unverified.json"), values)
                raise
        write(args.output, values)
        print(json.dumps({"dataset": str(args.output), "GPU_action": False}), flush=True)
        return
    if args.output_dir.exists():
        raise FileExistsError("preserve training attempts and held-out outcomes")
    dataset, cohort = json.loads(args.dataset.read_bytes()), json.loads(args.cohort.read_bytes())
    if dataset["schema"] != R5_SCHEMA or dataset["prefill_token_weight"] != .01:
        raise ValueError("R5 work units and causal features must match")
    for path, digest in dataset["sources_sha256"].items():
        if sha256_file(Path(path)) != digest:
            raise ValueError("a conditional-work source changed")
    training, calibration, test, split = split_rows(dataset["rows"], cohort["measured_workflow_ids"])
    raw = fit(
        training, calibration, epochs=args.epochs, split_mode="workflow",
        hidden_width=128, vocabulary_size=8192, dropout=.1, weight_decay=.05,
        feature_schema=R5_SCHEMA,
    )
    raw["metadata"].update(
        prefill_token_weight=.01, progress_observation_contract=NATIVE_PROGRESS,
        split_contract=split, decision_eligible=True,
        online_features_use_observed_prefix_only=True,
        unseen_project_generalization_claimed=False,
    )
    calibration_model = WorkflowWorkModel(raw)
    quality = distribution_metrics(calibration_model, calibration)
    reasons = []
    if quality["macro_f1"] < .5:
        reasons.append("calibration_macro_F1_below_0_50")
    if quality["long_to_short_rate"] > .1:
        reasons.append("calibration_long_to_short_above_0_10")
    if min(raw["metadata"]["calibration_class_workflows"]) < 2:
        reasons.append("insufficient_complete_execution_class_support")
    if quality["workflow_equal_weight_ECE_10"] > .15:
        reasons.append("calibration_ECE_above_0_15")
    if quality["supported_distribution_coverage"] < .5:
        reasons.append("calibration_supported_distribution_coverage_below_0_50")
    raw["metadata"].update(
        decision_eligible=not reasons, decision_rejection_reasons=reasons,
        calibration_distribution_metrics=quality,
    )
    cost = json.loads(args.cost_artifact.read_bytes())["admission_cost_profile"]
    raw["admission_cost_profile"] = deepcopy(cost)
    raw["free_dispatch_coalescing_window_ms"] = 2000
    raw["metadata"]["cost_profile_inherited_not_new_workload_calibration"] = True
    model = WorkflowWorkModel(raw)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    write(args.output_dir / "model.json", raw)
    write(args.output_dir / "evaluation.json", {
        "model_sha256": sha256_file(args.output_dir / "model.json"), "split_contract": split,
        "train": distribution_metrics(model, training),
        "calibration": distribution_metrics(model, calibration),
        "test": distribution_metrics(model, test),
        "decision_eligible": not reasons, "decision_rejection_reasons": reasons,
        "evaluation_labels_used_for_model_or_threshold_selection": False,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            args.dataset, args.cohort, args.cost_artifact, Path(__file__),
        )}, "GPU_action": False, "JCT_gain_proven": False,
    })
    print(json.dumps({
        "decision_eligible": not reasons, "reasons": reasons,
        "train_workflows": len({row["workflow"] for row in training}),
        "calibration_workflows": len({row["workflow"] for row in calibration}),
        "test_workflows": len({row["workflow"] for row in test}), "GPU_action": False,
    }), flush=True)


if __name__ == "__main__":
    main()
