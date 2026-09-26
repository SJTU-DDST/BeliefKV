#!/usr/bin/env python3
"""Project-disjoint, read-only screen of child terminal intent and RETURN ETA.

Uses the first hidden sample of *every* observed child model round for intent.
Unfinished children are censored, never mislabeled as negative final rounds.
Hidden vectors and generated text are never exported by this script.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
from pathlib import Path

import numpy as np

if __package__:
    from scripts.audit_child_hidden_trace import index_workflow
    from scripts.pilot_hidden_eta_project_split import features, fit_ridge, predict
else:
    from audit_child_hidden_trace import index_workflow
    from pilot_hidden_eta_project_split import features, fit_ridge, predict


def load_records(workflows: Path, traces: Path) -> tuple[list[dict], dict]:
    requests = {}
    complete_children = set()
    terminal_requests = {}
    for event_file in workflows.glob("*/runtime_events.deepagents.jsonl"):
        with event_file.open() as stream:
            events = [json.loads(line) for line in stream]
        terminals, join_last_children = index_workflow(events)
        result_by_invocation = {}
        for event in events:
            if event["kind"] == "llm_result":
                rid = (event.get("attributes") or {}).get("request_id")
                if rid:
                    result_by_invocation.setdefault(event.get("invocation_id"), []).append(
                        event
                    )
        for event in events:
            if event["kind"] != "return":
                continue
            invocation = event.get("invocation_id")
            if invocation and invocation.startswith("deepagents-invocation:"):
                complete_children.add(invocation)
        for rid, (invocation, return_ms) in terminals.items():
            terminal_requests[rid] = (invocation, return_ms)
        for invocation, rounds in result_by_invocation.items():
            if invocation not in complete_children:
                continue
            for event in rounds:
                rid = (event.get("attributes") or {}).get("request_id")
                if rid:
                    requests[rid] = {
                        "invocation": invocation,
                        "project": event_file.parent.name.split("__", 1)[0],
                        "terminal": rid in terminals,
                        "join_last": (
                            rid in terminals
                            and invocation in join_last_children
                        ),
                        "return_ms": terminals[rid][1] if rid in terminals else None,
                    }
    records = []
    for path in traces.glob("*.npz"):
        with np.load(path, allow_pickle=False) as trace:
            rid = str(trace["rid"])
            if rid not in requests or not len(trace["hidden"]):
                continue
            metadata = requests[rid]
            if str(trace["finish_reason"]) not in {"stop", "length"}:
                continue
            first_arrival_ms = float(trace["arrival_ns"][0]) / 1e6
            samples = [
                (int(i) // 32, float(timestamp) / 1e6 - first_arrival_ms,
                 metadata["return_ms"] - float(timestamp) / 1e6
                 if metadata["terminal"] else None,
                 vector.astype(np.float32))
                for i, timestamp, vector in zip(
                    trace["token_counts"],
                    trace["arrival_ns"],
                    trace["hidden"],
                )
            ]
            if metadata["terminal"] and any(row[2] <= 0 for row in samples):
                continue
            records.append({
                **metadata,
                "rid": rid,
                "first_arrival_ms": first_arrival_ms,
                "samples": samples,
            })
    return records, {
        "observed_complete_children": len(complete_children),
        "eligible_terminal_requests": len(terminal_requests),
        "terminal_rounds_with_hidden": sum(r["terminal"] for r in records),
        "other_rounds_with_hidden": sum(not r["terminal"] for r in records),
    }


def load_batch_records(
    roots: list[Path], traces: Path,
) -> tuple[list[dict], dict]:
    if not traces.is_dir():
        raise FileNotFoundError(f"hidden-state trace directory is absent: {traces}")
    records = []
    counts = {}
    for root in roots:
        if not root.is_dir() or not any(
            root.glob("*/runtime_events.deepagents.jsonl")
        ):
            raise FileNotFoundError(f"workflow events are absent from {root}")
        batch, metrics = load_records(root, traces)
        records.extend(batch)
        for key, count in metrics.items():
            counts[key] = counts.get(key, 0) + count
    if len({row["rid"] for row in records}) != len(records):
        raise ValueError("workflow batches contain duplicate request IDs")
    return records, counts


def first_features(records: list[dict], hidden: bool) -> np.ndarray:
    return features(
        [record["samples"][0] for record in records],
        hidden,
    )


def at_stage(records: list[dict], tokens: int) -> list[dict]:
    selected = []
    for record in records:
        eligible = [
            row for row in record["samples"] if row[0] * 32 >= tokens
        ]
        if eligible:
            selected.append({**record, "samples": eligible})
    return selected


def content_cues(workflows: Path) -> dict[str, dict[str, float]]:
    cues = {}
    for path in workflows.glob("*/runtime_events.deepagents.jsonl"):
        with path.open() as stream:
            events = (json.loads(line) for line in stream)
            for event in events:
                attrs = event.get("attributes") or {}
                rid = attrs.get("request_id")
                if not rid:
                    continue
                for flag, label in (
                    ("beliefkv_child_first_content_shadow", "content"),
                    ("beliefkv_child_first_tool_chunk_shadow", "tool"),
                ):
                    if attrs.get(flag):
                        cues.setdefault(rid, {})[label] = min(
                            cues.get(rid, {}).get(label, float("inf")),
                            event["ts_ms"],
                        )
    return cues


def content_gated_report(
    records: list[dict], chosen: np.ndarray, cues: dict,
    all_terminal_count: int,
) -> dict:
    true_positive = false_positive = 0
    leads = []
    join_leads = []
    for record, accepted in zip(records, chosen):
        if not accepted:
            continue
        events = cues.get(record["rid"], {})
        content = events.get("content")
        if content is None:
            continue
        stage_arrival_ms = (
            record["first_arrival_ms"] + record["samples"][0][1]
        )
        candidate = max(stage_arrival_ms, content)
        if events.get("tool", float("inf")) <= candidate:
            continue
        if record["terminal"]:
            true_positive += 1
            leads.append(record["return_ms"] - candidate)
            if record.get("join_last"):
                join_leads.append(record["return_ms"] - candidate)
        else:
            false_positive += 1
    join_total = sum(row.get("join_last", False) for row in records)
    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "precision": round(
            true_positive / (true_positive + false_positive), 4
        ) if true_positive + false_positive else None,
        "recall_of_all_terminal_rounds": round(
            true_positive / all_terminal_count, 4
        ) if all_terminal_count else None,
        "median_return_lead_ms": round(
            statistics.median(leads), 2
        ) if leads else None,
        "at_least_500ms_early": sum(lead >= 500 for lead in leads),
        "actionable_precision": round(
            sum(lead >= 500 for lead in leads)
            / (true_positive + false_positive), 4
        ) if true_positive + false_positive else None,
        "eligible_join_last_terminal_rounds": join_total,
        "join_last_true_positive": len(join_leads),
        "join_last_at_least_500ms_early": sum(
            lead >= 500 for lead in join_leads
        ),
        "join_last_median_return_lead_ms": (
            round(statistics.median(join_leads), 2) if join_leads else None
        ),
    }


def classification_report(labels: np.ndarray, scores: np.ndarray) -> dict:
    actual = labels.astype(bool)
    predicted = scores >= 0.5
    positives = scores[actual]
    negatives = scores[~actual]
    auc = (
        float(np.mean(positives[:, None] > negatives[None, :]))
        if len(positives) and len(negatives) else None
    )
    tp = int(np.count_nonzero(actual & predicted))
    fp = int(np.count_nonzero(~actual & predicted))
    return {
        "natural_terminal_rounds": int(np.count_nonzero(actual)),
        "nonterminal_rounds": int(np.count_nonzero(~actual)),
        "auc": round(auc, 4) if auc is not None else None,
        "true_positive": tp,
        "false_positive": fp,
        "precision": round(tp / (tp + fp), 4) if tp + fp else None,
        "recall": round(tp / np.count_nonzero(actual), 4) if actual.any() else None,
    }


def eta_report(model: tuple, records: list[dict], chosen: np.ndarray,
               hidden: bool) -> dict:
    errors = []
    lead = []
    for record, accepted in zip(records, chosen):
        if not accepted or not record["terminal"]:
            continue
        samples = record["samples"]
        true = np.asarray([row[2] for row in samples])
        estimate = predict(model, features(samples, hidden))
        mask = (true >= 500) & (true <= 5000)
        if mask.any():
            errors.append(float(np.median(np.abs(true[mask] - estimate[mask]))))
        candidates = np.flatnonzero(estimate <= 1500)
        if len(candidates):
            lead.append(float(true[candidates[0]]))
    return {
        "terminal_with_eta": len(errors),
        "median_per_terminal_error_p50_ms": (
            round(statistics.median(errors), 2) if errors else None
        ),
        "first_eta_triggered": len(lead),
        "first_eta_trigger_500_to_3000ms": sum(
            500 <= value <= 3000 for value in lead
        ),
        "first_eta_trigger_early_over_3000ms": sum(value > 3000 for value in lead),
        "first_eta_trigger_late_under_500ms": sum(value < 500 for value in lead),
    }


def first_eta_trigger_report(
    model: tuple, records: list[dict], chosen: np.ndarray,
    cues: dict[str, dict[str, float]], hidden: bool,
    threshold_ms: float = 1500,
) -> dict:
    leads = []
    join_leads = []
    false_triggers = 0
    for record, accepted in zip(records, chosen):
        if not accepted:
            continue
        cue = cues.get(record["rid"], {})
        content_ts = cue.get("content")
        if content_ts is None:
            continue
        predictions = predict(model, features(record["samples"], hidden))
        for sample, prediction in zip(record["samples"], predictions):
            if prediction > threshold_ms:
                continue
            trigger_ts = max(
                record["first_arrival_ms"] + sample[1], content_ts,
            )
            if cue.get("tool", float("inf")) <= trigger_ts:
                continue
            if record["terminal"]:
                lead = record["return_ms"] - trigger_ts
                leads.append(lead)
                if record.get("join_last"):
                    join_leads.append(lead)
            else:
                false_triggers += 1
            break
    total = len(leads) + false_triggers
    actionable = sum(500 <= lead <= 3000 for lead in leads)
    return {
        "threshold_ms": threshold_ms,
        "first_triggered_rounds": total,
        "nonterminal_false_triggers": false_triggers,
        "terminal_true_triggers": len(leads),
        "join_last_true_triggers": len(join_leads),
        "first_trigger_500_to_3000ms": actionable,
        "first_trigger_over_3000ms": sum(lead > 3000 for lead in leads),
        "first_trigger_under_500ms": sum(lead < 500 for lead in leads),
        "join_last_trigger_500_to_3000ms": sum(
            500 <= lead <= 3000 for lead in join_leads
        ),
        "first_trigger_actionable_precision": (
            round(actionable / total, 4) if total else None
        ),
        "first_trigger_lead_p50_ms": (
            round(statistics.median(leads), 2) if leads else None
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--traces", required=True, type=Path)
    parser.add_argument("--heldout-traces", type=Path)
    parser.add_argument(
        "--train-workflows", required=True, type=Path, action="append",
    )
    parser.add_argument(
        "--heldout-workflows", required=True, type=Path, action="append",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    train, train_counts = load_batch_records(
        args.train_workflows, args.traces,
    )
    heldout, heldout_counts = load_batch_records(
        args.heldout_workflows, args.heldout_traces or args.traces,
    )
    train_projects = {row["project"] for row in train}
    heldout_projects = {row["project"] for row in heldout}
    if not train_projects.isdisjoint(heldout_projects):
        parser.error("heldout project overlaps training projects")
    result = {
        "diagnostic_only": True,
        "train_projects": sorted(train_projects),
        "heldout_projects": sorted(heldout_projects),
        "train": train_counts,
        "heldout": heldout_counts,
        "stages": {},
    }
    cues = {}
    for root in args.heldout_workflows:
        cues.update(content_cues(root))
    terminal_count = sum(row["terminal"] for row in heldout)
    for stage in (32, 256, 512):
        selected_train = at_stage(train, stage)
        selected_heldout = at_stage(heldout, stage)
        positive = [row for row in selected_train if row["terminal"]]
        negative = [row for row in selected_train if not row["terminal"]]
        if len(positive) < 5 or len(negative) < 5 or not selected_heldout:
            result["stages"][stage] = {"insufficient_data": True}
            continue
        random.Random(17).shuffle(negative)
        sample = positive + negative[: 5 * len(positive)]
        random.Random(23).shuffle(sample)
        label = np.asarray([row["terminal"] for row in sample], dtype=np.float64)
        heldout_label = np.asarray([row["terminal"] for row in selected_heldout])
        stage_result = {
            "train_terminal": len(positive),
            "train_nonterminal": len(negative),
            "heldout_rounds": len(selected_heldout),
            "heldout_stage_only_terminal_precision": round(
                float(np.mean(heldout_label)), 4
            ),
        }
        for hidden in (False, True):
            classifier = fit_ridge(first_features(sample, hidden), label)
            mean, scale, weights, intercept = classifier
            scores = np.clip(
                (first_features(selected_heldout, hidden) - mean) / scale @ weights
                + intercept, 0, 1,
            )
            chosen = scores >= 0.5
            training = [
                row for item in positive for row in item["samples"]
                if row[2] is not None and row[2] > 0
            ]
            eta_model = fit_ridge(
                features(training, hidden),
                np.log1p(np.asarray([row[2] for row in training])),
            )
            stage_result["hidden_plus_progress" if hidden else "progress_only"] = {
                "terminal_classifier": classification_report(
                    heldout_label, scores
                ),
                "return_eta": eta_report(
                    eta_model, selected_heldout, chosen, hidden
                ),
            }
            if hidden and stage in (256, 512):
                gate_name = (
                    "frozen_content_gate" if stage == 512
                    else "exploratory_content_gate"
                )
                stage_result[gate_name] = content_gated_report(
                    selected_heldout, chosen, cues, terminal_count,
                )
                stage_result[gate_name]["first_eta_trigger"] = (
                    first_eta_trigger_report(
                        eta_model, selected_heldout, chosen, cues, hidden,
                    )
                )
        result["stages"][stage] = stage_result
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
