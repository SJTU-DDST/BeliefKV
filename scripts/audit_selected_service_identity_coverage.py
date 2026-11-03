"""Check earlier incomplete native evidence before verified workflow service."""

import argparse
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts.audit_periodic_admission_decisions import useful_work
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write


FIELDS = (
    "request_id", "workflow_id", "invocation_id", "context_id", "context_epoch",
    "attempt_id", "session_id", "session_generation",
)


def inspect(selected, labelled, batches):
    labels = {row["raw_journal_sequence"]: row for row in labelled["decisions"]}
    queries, evidence = defaultdict(list), defaultdict(Counter)
    eligible = [row for row in selected["decisions"] if row["all_workflows_bounded_served"]]
    for decision in eligible:
        sequence = decision["raw_journal_sequence"]
        start = labels[sequence]["monotonic_s"] * 1000.
        for workflow in decision["workflows"]:
            end = start + workflow["first_bounded_wait_ms"]
            for member in workflow["members"]:
                key = tuple(member["key"])
                if len(key) != len(FIELDS):
                    raise ValueError("coverage needs the original full request identity")
                queries[key[0]].append((sequence, key, start, end))
    for batch in batches:
        if batch.get("event") != "gpu_service_sample":
            continue
        for sample in batch["request_samples"]:
            if sample.get("request_id") not in queries:
                continue
            progress = useful_work(sample, sample.get("phase", batch["phase"]))
            if progress is False:
                continue
            partial = any(sample.get(name) is None for name in FIELDS[5:])
            for sequence, key, start, end in queries[sample["request_id"]]:
                if any(
                    sample.get(name) is not None and sample[name] != value
                    for name, value in zip(FIELDS, key)
                ):
                    continue
                if batch["launch_monotonic_ms"] >= start and batch["service_start_monotonic_ms"] < end:
                    reason = (
                        "unknown_progress_before_verified_workflow_service" if progress is None else
                        "partial_identity_positive_progress_before_verified_workflow_service" if partial else
                        "exact_positive_progress_before_verified_workflow_service"
                    )
                    evidence[sequence][reason] += 1
    counts = Counter()
    examples = []
    for decision in eligible:
        sequence = decision["raw_journal_sequence"]
        clean = not evidence[sequence]
        counts["original_complete_service_windows"] += 1
        counts["windows_with_earlier_uncertain_or_uncredited_service"] += not clean
        counts["windows_without_earlier_uncertain_or_uncredited_service"] += clean
        if clean:
            counts["clean_shortest_in_first_wave"] += decision["shortest_delay_after_first_ms"] == 0.
            counts["clean_shortest_served_later"] += decision["shortest_delay_after_first_ms"] > 0.
        else:
            examples.append({"raw_journal_sequence": sequence, "earlier_evidence": dict(evidence[sequence])})
    return {
        "counts": dict(counts), "ambiguous_windows": examples,
        "scope": {
            "same_sealed_native_journal_as_original_selected_service_audit": True,
            "each_selected_member_checked_before_its_workflow_first_verified_service": True,
            "known_foreign_attempt_context_or_session_rejected": True,
            "partial_identity_or_unknown_progress_never_promoted_to_exact_service": True,
            "earlier_exact_progress_is_not_credited_to_an_unverified_grant": True,
            "already_inflight_before_selection_excluded": True,
            "overlapping_decision_windows_are_not_independent_JCT_samples": True,
            "GPU_or_causal_JCT_gain_proven": False,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("analysis", "labels", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if (
        args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("use hidden CUDA, nice19 and preserve prior coverage evidence")
    selected, labelled = causal.load(args.analysis), causal.load(args.labels)
    for name, digest in selected["inputs_sha256"].items():
        if sha256_file(Path(name)) != digest:
            raise ValueError("a sealed selected-service audit input changed")
    if selected["inputs_sha256"].get(str(args.labels.resolve())) != sha256_file(args.labels):
        raise ValueError("work labels differ from the original service-order population")
    paths = [Path(name) for name in selected["input_jsonl_prefixes"] if name.endswith("/server/runtime_audit.jsonl")]
    if len(paths) != 1:
        raise ValueError("coverage requires the same single native journal")
    reader = SealedRecords()
    result = inspect(selected, labelled, reader(paths[0]))
    if reader.prefixes[str(paths[0])] != selected["input_jsonl_prefixes"][str(paths[0])]:
        raise ValueError("coverage read another native prefix")
    result.update(
        inputs_sha256={str(path.resolve()): sha256_file(path) for path in (
            args.analysis, args.labels, Path(__file__), ROOT / "scripts/audit_periodic_admission_decisions.py",
        )},
        input_jsonl_prefixes=reader.prefixes, GPU_action=False, performance_accepted=False,
    )
    write(args.output, result)
    print(json.dumps(result["counts"]), flush=True)


if __name__ == "__main__":
    main()
