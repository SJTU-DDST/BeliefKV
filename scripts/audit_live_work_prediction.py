"""Evaluate model ranks on actual ready-paused competition, using an observed replay prefix."""

import argparse
from collections import Counter, defaultdict
from itertools import combinations
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import (
    EventConfidence, RuntimeEvent, RuntimeEventKind as Kind, is_unobserved_join_event,
)
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_work import WorkflowWorkModel
from beliefkv.predictor.workflow_work_distribution import DistributionProgress, support_status
from scripts import run_causal_comparison as causal
from scripts.train_workflow_work import write


def journal(path, names):
    needles = [json.dumps(name).encode() for name in names]
    with path.open("rb") as stream:
        for line in stream:
            if not line.endswith(b"\n"):
                raise ValueError("require a closed complete journal")
            if any(needle in line for needle in needles):
                row = json.loads(line)
                if row.get("event") in names:
                    yield row


def ready_fitting(row):
    capacity = row["resume_capacity_tokens"]
    return [
        member for member in row["programs"]
        if member["application_ready"] and member["pending_before"]
        and not member["active_before"] and not member["marked_before"]
        and member.get("resume_window_eligible", True)
        and member["complete_budget_tokens"] <= capacity
    ]


def competitions(records):
    selections, controls = {}, []
    for row in records:
        if row["event"] == "workflow_size_resume_selection":
            if row["monotonic_s"] in selections:
                raise ValueError("duplicate selection boundary")
            selections[row["monotonic_s"]] = row
        else:
            controls.append(row)
    result = []
    for row in controls:
        fit = ready_fitting(row)
        workflows = {item["key"]["root_workflow_id"] for item in fit}
        if len(workflows) < 2 or sum(item["complete_budget_tokens"] for item in fit) <= row["resume_capacity_tokens"]:
            continue
        choice = selections[row["monotonic_s"]]
        candidates = {item["key"]["invocation_id"]: item for item in choice["candidates"]}
        if choice.get("rank_before_category") is not True or choice.get("rank_kind") not in (
            "reference_category_context_with_matched_oracle_bookkeeping",
            "oracle_remaining_model_tool_critical_path_seconds",
        ):
            raise ValueError("protected JOIN identification requires the recorded global rank contract")
        if any(
            len(item) < 3 or type(item[2]) not in (int, float)
            or not math.isfinite(item[2]) or item[2] != 0. and item[2] < 1.
            for item in choice["ordered"]
        ):
            raise ValueError("global rank must distinguish zero JOIN protection from ordinary work")
        ranks = {item[0]: item[2] for item in choice["ordered"]}
        unique = {}
        for member in fit:
            key = member["key"]
            selected = candidates.get(key["invocation_id"])
            reason = (
                "not_in_recorded_resume_selection" if selected is None else
                "request_identity_changed_within_boundary" if selected["key"] != key else None
            )
            prediction = selected.get("prediction") if selected else None
            if reason is None and (
                not isinstance(prediction, dict) or prediction.get("evidence") != "oracle"
            ):
                reason = "no_recorded_oracle_label"
            value = {
                "key": key, "selector_metadata_valid": reason is None,
                **({"unavailable_reason": reason} if reason else {}),
            }
            if reason is None:
                value.update({
                "key": key, "protected_JOIN": ranks[key["invocation_id"]] == 0.,
                "recorded_remaining_path_s": prediction["remaining_path_s"],
                "recorded_remaining_model_work": prediction["remaining_work"],
                })
            workflow = key["root_workflow_id"]
            if workflow not in unique or not unique[workflow]["selector_metadata_valid"]:
                unique[workflow] = value
        result.append({"monotonic_s": row["monotonic_s"], "candidates": list(unique.values())})
    return result, len(controls)


def observation_timeline(events, native, requests):
    """Use actual client publication and coarse native output, not future agent events."""
    submits, results = {}, {}
    timeline = []
    for index, raw in enumerate(events):
        event = RuntimeEvent.from_dict(raw)
        if event.confidence is EventConfidence.INFERRED or is_unobserved_join_event(event):
            continue
        timeline.append((event.ts_ms, 0, index, event))
        if event.kind in (Kind.LLM_SUBMIT, Kind.LLM_RESULT):
            request = event.attributes["request_id"]
            item = requests[request]
            if any(item[name] != getattr(event, name) for name in (
                "workflow_id", "invocation_id", "context_id",
            )):
                raise ValueError("replay event ownership differs from bound logical work")
            target = submits if event.kind is Kind.LLM_SUBMIT else results
            if request in target:
                raise ValueError("duplicate observed replay model boundary")
            target[request] = event
    published, previous, domains = Counter(), -math.inf, set()
    for index, batch in enumerate(native):
        now = batch["complete_monotonic_ms"]
        domains.add(batch["monotonic_clock_domain"])
        if not math.isfinite(now) or now < previous:
            raise ValueError("native service completion clock moved backwards")
        previous = now
        for member in batch["request_samples"]:
            request = member.get("request_id")
            if request not in submits:
                continue
            if any(member.get(name) != requests[request][name] for name in (
                "workflow_id", "invocation_id", "context_id",
            )):
                raise ValueError("native sample ownership differs from its observed request")
            if member.get("phase", batch["phase"]) != "decode":
                continue
            if member["token_delta_semantics"] != "observed_output_ids_delta":
                raise ValueError("require observed output IDs, not prefill input tokens")
            before, delta = member["output_tokens_before"], member["token_delta"]
            if "output_tokens_after" in member:
                count = member["output_tokens_after"]
                reported = member.get("output_tokens_reported_before")
                if (
                    type(count) is not int or type(reported) is not int
                    or reported < 0 or count < 0 or delta != max(0, count - reported)
                ):
                    raise ValueError("invalid cumulative native output accounting")
            else:
                count = before + delta
            if count > requests[request]["output_tokens"] or now < submits[request].ts_ms:
                raise ValueError("invalid observed native output")
            if request in results and now > results[request].ts_ms:
                if delta == 0:
                    continue
                raise ValueError("positive model output follows observed completion")
            if count == requests[request]["output_tokens"] or count - published[request] < 128:
                continue
            published[request] = count
            timeline.append((now, 1, index, (request, count)))
    if len(domains) != 1:
        raise ValueError("require one native monotonic clock domain")
    return sorted(timeline, key=lambda item: item[:3])


def snapshot(progress, event, requests):
    state = progress.setdefault(event.workflow_id, DistributionProgress())
    state.event(event)
    request = event.attributes.get("request_id")
    if event.kind in (Kind.LLM_SUBMIT, Kind.LLM_RESULT):
        item = requests[request]
        args = request, event.invocation_id, event.context_id, item["prompt_tokens"]
        if event.kind is Kind.LLM_SUBMIT:
            state.started_summary(*args, item["input_increment"])
        else:
            state.completed_summary(*args, item["output_tokens"], item["input_increment"])


def rank_summary(decisions, model_name, *, measured, held_out_only=False):
    by_boundary, by_pair = [], defaultdict(list)
    counts = Counter()
    for decision in decisions:
        values = []
        for left, right in combinations(decision["candidates"], 2):
            a, b = left["key"]["root_workflow_id"], right["key"]["root_workflow_id"]
            if held_out_only and not {a, b} <= measured:
                continue
            counts["cross_workflow_pairs"] += 1
            if not left.get("selector_metadata_valid", True) or not right.get("selector_metadata_valid", True):
                counts["missing_exact_selector_metadata_pairs"] += 1
                continue
            if left["protected_JOIN"] or right["protected_JOIN"]:
                counts["excluded_protected_JOIN_pairs"] += 1
                continue
            pa, pb = left.get("predictions", {}).get(model_name), right.get("predictions", {}).get(model_name)
            if pa is None or pb is None:
                counts["missing_observed_input_pairs"] += 1
                continue
            predicted = pa["effective_weight"] - pb["effective_weight"]
            entry = {}
            for label, field in (
                ("model", "observed_remaining_model_work"), ("model_tool_path", "recorded_remaining_path_s"),
            ):
                truth = left[field] - right[field]
                if abs(truth) < 1e-9:
                    continue
                correct = .5 if abs(predicted) < 1e-10 else float(truth * predicted < 0)
                entry[label] = correct
                by_pair[(min(a, b), max(a, b), label)].append(correct)
            if entry:
                values.append(entry)
                counts["evaluated_pair_observations"] += 1
                counts["effective_prediction_ties"] += abs(predicted) < 1e-10
                counts["model_and_path_order_disagree"] += (
                    (left["observed_remaining_model_work"] - right["observed_remaining_model_work"])
                    * (left["recorded_remaining_path_s"] - right["recorded_remaining_path_s"]) < 0
                )
        if values:
            by_boundary.append(values)
    metrics = {}
    for label in ("model", "model_tool_path"):
        boundary_scores = [
            sum(row[label] for row in values if label in row) / sum(label in row for row in values)
            for values in by_boundary if any(label in row for row in values)
        ]
        pair_scores = [
            sum(values) / len(values) for (_a, _b, target), values in by_pair.items() if target == label
        ]
        metrics[label] = {
            "boundary_equal_weight_accuracy": sum(boundary_scores) / len(boundary_scores) if boundary_scores else None,
            "unique_workflow_pair_equal_weight_accuracy": sum(pair_scores) / len(pair_scores) if pair_scores else None,
            "boundaries": len(boundary_scores), "unique_workflow_pairs": len(pair_scores),
        }
    return {"counts": dict(counts), "metrics": metrics}


def audit(decisions, timeline, requests, totals, models):
    progress, projections, position = {}, {}, 0
    for decision in decisions:
        while position < len(timeline) and timeline[position][0] <= decision["monotonic_s"] * 1000.:
            _when, _priority, _index, item = timeline[position]
            if isinstance(item, RuntimeEvent):
                snapshot(progress, item, requests)
            else:
                request, count = item
                progress[requests[request]["workflow_id"]].generated(request, count)
            position += 1
        for candidate in decision["candidates"]:
            if not candidate.get("selector_metadata_valid", True):
                continue
            workflow = candidate["key"]["root_workflow_id"]
            state = progress.get(workflow)
            if state is None or not state.text:
                candidate["unavailable_reason"] = "no_observed_task_metadata"
                continue
            features = state.features()
            attained = state.counts["input_tokens"] * .01 + state.counts["output_tokens"]
            candidate["observed_remaining_model_work"] = totals[workflow] - attained
            if candidate["observed_remaining_model_work"] < -1e-6:
                raise ValueError("observed model service exceeds complete logical work")
            candidate["features"] = features
            candidate["predictions"] = {}
            for name, model in models.items():
                text = state.prediction_text(observed_delegations=model.observed_delegations)
                key = name, workflow, text
                if key not in projections:
                    projections[key] = model.task_encoding(text)
                projection, coverage = projections[key]
                if projection is None:
                    continue
                probabilities = model.predict(projection, features)
                support = support_status(model, features, coverage)
                candidate["predictions"][name] = {
                    "probabilities": probabilities, "support": support,
                    "effective_weight": (
                        probabilities[0] + .5 * probabilities[1] + .25 * probabilities[2]
                        if support == "calibrated_distribution" else .5
                    ),
                }
    return decisions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--model", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.registration, args.cohort = args.registration.resolve(), args.cohort.resolve()
    args.model = [path.resolve() for path in args.model]
    if args.output.exists():
        raise FileExistsError("preserve prior actual-candidate prediction audits")
    plan = causal.load(args.registration)
    if plan["order"] != ["full"] or plan.get("oracle_information") is None:
        raise ValueError("require the matched Full's recorded oracle bookkeeping")
    arm = Path(plan["run_root"]) / "full"
    paths = {
        "journal": arm / "opportunities/admission_opportunities.jsonl",
        "events": arm / "replay/runtime_events.replay.jsonl",
        "native": arm / "server/runtime_audit.jsonl",
        "labels": Path(plan["reference_environment"]["BELIEFKV_WORKFLOW_WORK_MODEL"]),
    }
    pins = {str(path): sha256_file(path) for path in (
        *paths.values(), *args.model, args.registration, args.cohort,
    )}
    labels = causal.load(paths["labels"])
    decisions, controls = competitions(journal(paths["journal"], {
        "agent_periodic_admission_decision", "workflow_size_resume_selection",
    }))
    with paths["events"].open() as stream:
        events = [json.loads(line) for line in stream]
    timeline = observation_timeline(
        events, journal(paths["native"], {"gpu_service_sample"}), labels["requests"],
    )
    models = {str(path): WorkflowWorkModel.load(path, pins[str(path)]) for path in args.model}
    totals = {key: row["total_work_units"] / 100. for key, row in labels["workflows"].items()}
    result = audit(decisions, timeline, labels["requests"], totals, models)
    measured = set(causal.load(args.cohort)["measured_workflow_ids"])
    summary = {
        name: {
            "all_workflows": rank_summary(result, name, measured=measured),
            "both_held_out_measured": rank_summary(result, name, measured=measured, held_out_only=True),
        } for name in models
    }
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("a closed prediction-audit input changed")
    write(args.output, {
        "analysis_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        ).strip(),
        "controls": controls, "competition_boundaries": len(result), "models": summary,
        "decisions": result, "inputs_sha256": pins, "GPU_action": False,
        "scope": {
            "actual_application_ready_window_eligible_logically_competing_candidates": True,
            "client_published_events_and_observed_native_progress_only_for_features": True,
            "future_original_work_and_logged_paths_are_labels_only": True,
            "input_work_charged_at_first_observed_output_like_R5": True,
            "observed_complete_work_charged_at_client_result": True,
            "client_publication_is_not_proof_of_native_ingest_by_that_time": True,
            "ideal_synchronous_predictor_not_online_worker_cache_or_lag_coverage": True,
            "repeated_boundary_pairs_and_unique_pairs_reported_separately": True,
            "protected_JOIN_pairs_excluded_from_predictor_rank_quality": True,
            "protected_JOIN_identification": "zero_global_rank_not_reference_category_v2",
            "logical_fit_not_native_physical_feasibility": True,
            "missing_or_changed_selector_identity_is_explicitly_unknown": True,
            "counterfactual_service_or_JCT_proven": False,
        },
    })
    print(json.dumps({"competition_boundaries": len(result), "models": summary}), flush=True)


if __name__ == "__main__":
    main()
