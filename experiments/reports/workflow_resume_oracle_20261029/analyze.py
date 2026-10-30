"""CPU-only local resume diagnostic using future work as evaluation labels."""

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.experiments.causal_replay_tokens import CapturedTokenCorpus
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_work import WorkflowProgress
from experiments.reports.arrival_mean120_20261029.queue_pair import complete_work
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_workflow_size_selection import EVENT, inspect_choice
from scripts.run_causal_gpu_replay import source_plan
from scripts.train_workflow_work import native_decode_progress
from scripts import run_causal_comparison as causal


def select(row, remaining, thresholds, *, continuous):
    """Hold the observed candidates, categories and resume budget fixed."""
    inspect_choice(row)
    metadata = {item["key"]["invocation_id"]: item["key"] for item in row["candidates"]}
    if len(metadata) != len(row["candidates"]):
        raise ValueError("duplicate candidate metadata")
    ordered = []
    for owner, category, _bucket, tokens, sequence, group in row["ordered"]:
        workflow = metadata[owner]["root_workflow_id"]
        value = remaining[workflow]
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("remaining logical work must be finite and nonnegative")
        rank = value if continuous else bisect_right(thresholds, value)
        ordered.append((owner, category, rank, tokens, sequence, group[2] if group else None))
    ordered.sort(key=lambda item: item[1:5])
    groups = defaultdict(list)
    for item in ordered:
        if item[5] is not None:
            groups[item[1], item[2], item[5]].append(item)
    seen, selected, required = set(), [], 0
    for anchor in ordered:
        peers = groups.get((anchor[1], anchor[2], anchor[5]), (anchor,))
        for item in peers:
            if item[0] in seen:
                continue
            seen.add(item[0])
            if required + item[3] + 100 <= row["capacity_tokens"]:
                selected.append(item[0])
                required += item[3] + 100
    return selected


def analyze(registration, dataset_path):
    plan = causal.load(registration)
    if complete_work(registration) is None or plan["order"] != ["full"]:
        raise ValueError("require complete Full work, fresh drain, and actual owned exit")
    for name in ("beliefkv/policy/workflow_size_resume.py", "beliefkv/policy/paper_baselines.py"):
        if sha256_file(ROOT / name) != plan["source_sha256"][name]:
            raise ValueError("recorded policy differs from the exact local replay")
    environment = plan["reference_environment"]
    model_path = Path(environment["BELIEFKV_WORKFLOW_WORK_MODEL"])
    if sha256_file(model_path) != environment["BELIEFKV_WORKFLOW_WORK_MODEL_SHA256"]:
        raise ValueError("recorded workflow model changed")
    model, dataset = causal.load(model_path), causal.load(dataset_path)
    thresholds = model["work_thresholds"]
    if (
        len(thresholds) != 2 or not 0 < thresholds[0] < thresholds[1]
        or dataset["prefill_token_weight"] != model["metadata"]["prefill_token_weight"]
    ):
        raise ValueError("remaining-work labels and recorded model use different units")
    labels = {}
    for item in dataset["rows"]:
        workflow, total = item["logical_workflow_id"], item["total_work"]
        if workflow in labels and labels[workflow] != total:
            raise ValueError("one execution has conflicting final work labels")
        labels[workflow] = total
    if set(labels) != set(plan["workflow_ids"]):
        raise ValueError("evaluation execution population differs from the comparison")
    arm, reader = Path(plan["run_root"]) / "full", SealedRecords()
    events = [
        RuntimeEvent.from_dict(row) for row in reader(arm / "replay/runtime_events.replay.jsonl")
    ]
    source = Path(plan["source_arm"])
    graph, expected, source_evidence = source_plan(source)
    generated = native_decode_progress(
        events, reader(arm / "server/runtime_audit.jsonl"),
        {request: usage[1] for request, usage in graph.request_usage.items()},
    )
    observations = [(event.ts_ms, i, event) for i, event in enumerate(events)]
    observations.extend(
        (item["ts_ms"], len(events) + i, item) for i, item in enumerate(generated)
    )
    observations.sort(key=lambda item: item[:2])
    progress = {workflow: WorkflowProgress() for workflow in labels}
    counts, changes, position = Counter(), [], 0
    with CapturedTokenCorpus(source / "server/request_tokens.jsonl", expected) as corpus:
        def apply_through(when):
            nonlocal position
            while position < len(observations) and observations[position][0] <= when:
                event = observations[position][2]
                position += 1
                if isinstance(event, dict):
                    progress[event["workflow_id"]].generated(
                        event["request_id"], event["output_tokens"],
                    )
                    continue
                state = progress[event.workflow_id]
                state.event(event)
                if event.kind in (Kind.LLM_SUBMIT, Kind.LLM_RESULT):
                    request = event.attributes["request_id"]
                    value = corpus.get(request)
                    if event.kind is Kind.LLM_SUBMIT:
                        state.started(
                            request, event.invocation_id, event.context_id, value.prompt_tokens,
                        )
                    else:
                        state.completed(
                            request, event.invocation_id, event.context_id,
                            value.prompt_tokens, value.output_tokens,
                        )

        for row in reader(arm / "opportunities/admission_opportunities.jsonl"):
            if row.get("event") != EVENT:
                continue
            apply_through(row["monotonic_s"] * 1000.)
            remaining = {
                workflow: max(0., total - (
                    progress[workflow].counts["input_tokens"] * dataset["prefill_token_weight"]
                    + progress[workflow].counts["output_tokens"]
                )) for workflow, total in labels.items()
            }
            reference = inspect_choice(row)["reference_selected"]
            learned = row["selected_in_priority_order"]
            classes = select(row, remaining, thresholds, continuous=False)
            continuous = select(row, remaining, thresholds, continuous=True)
            counts["controls"] += 1
            counts["nonempty_controls"] += bool(row["ordered"])
            counts["multiple_candidate_controls"] += len(row["ordered"]) > 1
            changed = {}
            for label, values in (
                ("learned", learned), ("oracle_classes", classes), ("oracle_continuous", continuous),
            ):
                changed[label] = set(values) != set(reference)
                counts[label + "_selected_set_changes_from_reference"] += changed[label]
                counts[label + "_selected_agents"] += len(values)
            counts["oracle_continuous_changes_from_learned"] += set(continuous) != set(learned)
            if any(changed.values()):
                changes.append({
                    "monotonic_s": row["monotonic_s"], "capacity_tokens": row["capacity_tokens"],
                    "reference": reference, "learned": learned,
                    "oracle_classes": classes, "oracle_continuous": continuous,
                })
        apply_through(math.inf)
        for workflow, total in labels.items():
            state = progress[workflow]
            actual = (
                state.counts["input_tokens"] * dataset["prefill_token_weight"]
                + state.counts["output_tokens"]
            )
            if state.active_calls or not math.isclose(actual, total, abs_tol=1e-8):
                raise ValueError("reconstructed whole-execution work does not match its final label")
        token_evidence = corpus.evidence
    for path, evidence in reader.prefixes.items():
        if Path(path).stat().st_size != evidence["complete_prefix_bytes"]:
            raise ValueError("complete input journal changed")
    return {
        "counts": dict(counts), "changed_local_controls": changes,
        "registration": str(registration), "registration_sha256": sha256_file(registration),
        "dataset_sha256": sha256_file(dataset_path), "model_sha256": sha256_file(model_path),
        "thresholds": thresholds, "source_evidence": source_evidence,
        "token_source": token_evidence, "input_jsonl_prefixes": reader.prefixes,
        "operator_sha256": sha256_file(Path(__file__)),
        "scope": (
            "Perfect remaining logical model work is an offline evaluation label only. "
            "Each local choice holds the observed paused candidates, original categories, "
            "sibling membership and decayed resume budget fixed. Post-resume pressure, "
            "active-agent selection, physical capacity, asynchronous prediction caches, "
            "later candidate sets and new arrivals are not counterfactually simulated. "
            "This is neither a workflow JCT upper bound nor a performance forecast."
        ),
        "all_final_work_labels_reconstructed": True,
        "future_labels_delivered_to_runtime": False,
        "GPU_action": False, "performance_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("offline oracle diagnostic must hide CUDA")
    if args.output.exists():
        raise FileExistsError("preserve prior diagnostic attempts")
    report = analyze(args.registration, args.dataset)
    causal.reservation.write_json(args.output, report)
    print(report["counts"])


if __name__ == "__main__":
    main()
