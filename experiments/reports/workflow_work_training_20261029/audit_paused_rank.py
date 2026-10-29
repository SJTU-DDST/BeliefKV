"""Evaluate causal call-boundary predictions on observed paused candidate sets."""

import argparse
from collections import Counter, defaultdict
from itertools import combinations
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.experiments.causal_replay_tokens import CapturedTokenCorpus
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_work import WorkflowProgress, WorkflowWorkModel, execution_control_text
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.run_causal_gpu_replay import source_plan
from scripts.train_workflow_work import write


def pair_metrics(samples):
    counts = Counter()
    distinct = set()
    for sample in samples:
        # One vote per workflow/category; fanout does not multiply its pairs.
        groups = defaultdict(dict)
        for row in sample:
            group = groups[row["category"]]
            previous = group.get(row["workflow"])
            if previous is None or row["context_tokens"] < previous["context_tokens"]:
                group[row["workflow"]] = row
        for group in groups.values():
            for left, right in combinations(group.values(), 2):
                if left["true_bucket"] == right["true_bucket"]:
                    continue
                counts["different_true_class_pairs"] += 1
                distinct.add(tuple(sorted((
                    left.get("request_id", left["workflow"]),
                    right.get("request_id", right["workflow"]),
                ))))
                prediction = left["bucket"] - right["bucket"]
                truth = left["true_bucket"] - right["true_bucket"]
                original = left["context_tokens"] - right["context_tokens"]
                if original:
                    counts["context_comparable_pairs"] += 1
                    counts["context_order_correct"] += original * truth > 0
                    counts["combined_order_correct"] += (prediction or original) * truth > 0
                if not prediction:
                    counts["predicted_ties"] += 1
                    continue
                counts["model_disambiguated_pairs"] += 1
                counts["correct_model_order" if prediction * truth > 0 else "wrong_model_order"] += 1
                if original * prediction < 0:
                    counts["reversed_context_size_order"] += 1
                    counts[
                        "beneficial_class_reversal" if prediction * truth > 0 else "harmful_class_reversal"
                    ] += 1
    denominator = counts["model_disambiguated_pairs"]
    return {
        **counts, "disambiguated_pair_accuracy": (
            counts["correct_model_order"] / denominator if denominator else None
        ),
        "distinct_request_pairs_with_true_class_difference": len(distinct),
        "scope": "sampled_paused_pairs_within_original_category; not feasible admission or JCT counterfactual",
    }


def continuous_pair_metrics(samples):
    counts = Counter()
    distinct = set()
    for sample in samples:
        groups = defaultdict(dict)
        for row in sample:
            current = groups[row["category"]].get(row["workflow"])
            if current is None or row["context_tokens"] < current["context_tokens"]:
                groups[row["category"]][row["workflow"]] = row
        for group in groups.values():
            for left, right in combinations(group.values(), 2):
                truth = left["remaining_logical_work"] - right["remaining_logical_work"]
                original = left["context_tokens"] - right["context_tokens"]
                if not truth or not original:
                    continue
                prediction = left["bucket"] - right["bucket"]
                counts["comparable_pairs"] += 1
                counts["context_order_correct"] += original * truth > 0
                counts["combined_order_correct"] += (prediction or original) * truth > 0
                distinct.add(tuple(sorted((
                    left.get("request_id", left["workflow"]),
                    right.get("request_id", right["workflow"]),
                ))))
    return {
        **counts, "distinct_request_pairs": len(distinct),
        "scope": "continuous logical remaining work; sampled pairs, not capacity feasibility or JCT",
    }


def audit(arm, source, dataset_path, model_path):
    import numpy as np

    dataset = json.loads(dataset_path.read_bytes())
    labels = {}
    for row in dataset["rows"]:
        labels[row["logical_workflow_id"]] = {
            "total": row["total_work"], "text": row["text"],
        }
    model = WorkflowWorkModel.load(model_path, sha256_file(model_path))
    if model.deployment_controls is not None:
        prefix = execution_control_text(model.deployment_controls)
        if any(not row["text"].startswith(prefix) for row in labels.values()):
            raise ValueError("paused audit dataset and model execution controls differ")
    if dataset["prefill_token_weight"] != model.metadata["prefill_token_weight"]:
        raise ValueError("work model and offline labels use different units")
    plan, expected, source_evidence = source_plan(source)
    read = SealedRecords()
    events = sorted(
        (RuntimeEvent.from_dict(row) for row in read(arm / "replay/runtime_events.replay.jsonl")),
        key=lambda event: event.ts_ms,
    )
    submissions = [
        event for event in events if event.kind is Kind.LLM_SUBMIT
    ]
    if (
        {event.attributes["request_id"] for event in submissions} != set(plan.request_usage)
        or {event.workflow_id for event in events if event.kind is Kind.WORKFLOW_END} != set(labels)
    ):
        raise ValueError("paused audit requires the complete matched workload")
    cleanup = json.loads((arm / "owned_process_cleanup.json").read_bytes())
    drain = json.loads((arm / "telemetry_drain.json").read_bytes())
    status = json.loads((arm / "server/native_telemetry_status.json").read_bytes())
    if (
        cleanup["remaining_processes"]
        or not drain["previous_zero_snapshot_ts_ms"] < drain["drained_status"]["snapshot_ts_ms"]
        or any(status.get(key) != 0 for key in (
            "dropped_records", "failed_records", "writer_queue_depth", "pending_batch_count",
        ))
    ):
        raise ValueError("paused audit requires complete drained telemetry")
    progress = {workflow: WorkflowProgress() for workflow in labels}
    projections = {workflow: model.task_projection(value["text"]) for workflow, value in labels.items()}
    pending, steps, cache = {}, Counter(), {}
    samples, records, counts = [], [], Counter()
    position = 0
    with CapturedTokenCorpus(source / "server/request_tokens.jsonl", expected) as corpus:
        for observed in read(arm / "opportunities/admission_opportunities.jsonl"):
            if observed["event"] != "admission_runtime_state":
                continue
            now = observed["monotonic_ms"]
            while position < len(events) and events[position].ts_ms <= now:
                event = events[position]
                position += 1
                state = progress[event.workflow_id]
                state.event(event)
                request = event.attributes.get("request_id")
                if event.kind is Kind.LLM_SUBMIT:
                    value = corpus.get(request)
                    # The recorded output is not consumed at submission.
                    state.started(request, event.invocation_id, event.context_id, value.prompt_tokens)
                    steps[event.invocation_id] += 1
                    pending[event.invocation_id] = event.workflow_id, request, len(value.prompt_tokens)
                elif event.kind is Kind.LLM_RESULT:
                    value = corpus.get(request)
                    state.completed(
                        request, event.invocation_id, event.context_id,
                        value.prompt_tokens, value.output_tokens,
                    )
                    current = pending.get(event.invocation_id)
                    if current is not None and current[1] == request:
                        pending.pop(event.invocation_id)
            sample = []
            admission = observed.get("agent_admission", {})
            for invocation in admission.get("paused_pending_invocation_ids", ()):
                value = pending.get(invocation)
                if value is None:
                    counts["candidate_without_observed_pending_call"] += 1
                    continue
                workflow, request, size = value
                state = progress[workflow]
                features = state.features()
                if workflow not in cache or cache[workflow][0] != features:
                    scores = (
                        model.predict(projections[workflow], features)
                        if projections[workflow] is not None else (1 / 3,) * 3
                    )
                    support = model.metadata.get("calibration_class_projects", ())
                    supported = (
                        model.metadata.get("decision_eligible") is True
                        and len(support) == 3 and min(support) > 0 and max(scores) >= .6
                    )
                    cache[workflow] = features, int(np.argmax(scores)) if supported else 1
                attained = (
                    state.counts["input_tokens"] * dataset["prefill_token_weight"]
                    + state.counts["output_tokens"]
                )
                # Future totals enter this offline label only, never features or projection.
                remaining = max(0., labels[workflow]["total"] - attained)
                sample.append({
                    "workflow": workflow, "invocation": invocation, "request_id": request,
                    "monotonic_ms": now, "category": 1 if steps[invocation] == 1 else 0,
                    "context_tokens": size, "bucket": cache[workflow][1],
                    "true_bucket": int(np.searchsorted(model.thresholds, remaining, side="right")),
                    "remaining_logical_work": remaining,
                })
            counts["sampled_states"] += 1
            if len({row["workflow"] for row in sample}) >= 2:
                counts["cross_workflow_competition_states"] += 1
            samples.append(sample)
            records.extend(sample)
        source_evidence["token_source"] = corpus.evidence
    return {
        "counts": dict(counts), "pair_metrics": pair_metrics(samples),
        "continuous_pair_metrics": continuous_pair_metrics(samples), "candidates": records,
        "input_jsonl_prefixes": read.prefixes, "source_evidence": source_evidence,
        "model_sha256": sha256_file(model_path), "dataset_sha256": sha256_file(dataset_path),
        "scope": {
            "observed_post_control_paused_sets": True,
            "features": "causal_replay_call_boundaries; observed_completed_requests_only",
            "partial_decode_and_background_200ms_cache_emulated": False,
            "native_capacity_or_original_selection_reconstructed": False,
            "future_labels_used_online": False, "performance_counterfactual": False,
        },
        "GPU_experiment_started": False, "performance_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("arm", "source", "dataset", "model", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        report = audit(args.arm, args.source, args.dataset, args.model)
    except Exception as error:
        write(args.output.with_suffix(".error.json"), {
            "error": repr(error), "arguments": {key: str(value) for key, value in vars(args).items()},
            "GPU_experiment_started": False,
        })
        raise
    write(args.output, report)
    print(json.dumps({"counts": report["counts"], "pairs": report["pair_metrics"]}, indent=2))


if __name__ == "__main__":
    main()
