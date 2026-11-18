"""Separate first calls from real continuations at sealed admission decisions."""

import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.train_workflow_work import write


def request_epochs(expected):
    first = {}
    for request in expected.values():
        identity = request["llm_submit"][0]
        owner, epoch = tuple(identity[:3]), identity[3]
        if type(epoch) is not int or epoch < 0:
            raise ValueError("invalid source context epoch")
        first[owner] = min(epoch, first.get(owner, epoch))
    return first


def classify(row, expected, first):
    counts, continuation_workflows = Counter(), set()
    for program in row["programs"]:
        if not (
            program["application_ready"] and program["pending_before"]
            and not program["active_before"]
        ):
            continue
        counts["ready_paused"] += 1
        counts["marked_ready_paused"] += bool(program["marked_before"])
        key = program.get("key")
        source = expected.get(key["request_id"]) if key else None
        identity = (
            [key["root_workflow_id"], key["invocation_id"], key["context_id"], key["context_epoch"]]
            if key else None
        )
        if source is None or source["llm_submit"][0] != identity:
            counts["unknown_source_request"] += 1
        elif identity[3] == first[tuple(identity[:3])]:
            counts["first_call_ready_paused"] += 1
        else:
            counts["continuation_ready_paused"] += 1
            continuation_workflows.add(identity[0])
    counts["continuation_ready_paused_workflows"] = len(continuation_workflows)
    return dict(counts)


def summarize(rows, begin, end):
    selected = [row for row in rows if begin <= row["offset_s"] < end]
    if not selected:
        raise ValueError("require observed admission decisions")
    count = len(selected)
    return {
        "begin_s": begin, "end_s": end, "decisions": count,
        "mean_candidates_per_decision": {
            name: sum(row.get(name, 0) for row in selected) / count for name in (
                "ready_paused", "first_call_ready_paused", "continuation_ready_paused",
                "continuation_ready_paused_workflows", "unknown_source_request",
            )
        },
        "with_continuation_ready_paused": sum(
            row.get("continuation_ready_paused", 0) > 0 for row in selected
        ),
        "with_at_least_two_continuation_workflows": sum(
            row.get("continuation_ready_paused_workflows", 0) >= 2 for row in selected
        ),
        "with_ready_paused_but_no_continuation": sum(
            row.get("ready_paused", 0) > 0 and row.get("continuation_ready_paused", 0) == 0
            for row in selected
        ),
        "unknown_source_requests": sum(row.get("unknown_source_request", 0) for row in selected),
        "sampled_decision_statistics_not_time_weighted_or_physical_fit": True,
    }


def analyze(evidence, expected_path, demand_path, complete_path, output):
    if output.exists():
        raise FileExistsError("preserve previous supply evidence")
    paths = (evidence, expected_path, demand_path, complete_path, Path(__file__))
    pins = {str(path.resolve()): sha256_file(path) for path in paths}
    declaration = json.loads(evidence.read_bytes())
    expected = json.loads(expected_path.read_bytes())
    first = request_epochs(expected)
    demand = json.loads(demand_path.read_bytes())
    complete = json.loads(complete_path.read_bytes())
    origin = demand["observations"][0]["monotonic_ms"] / 1000.
    source, = (
        (Path(name), prefix) for name, prefix in declaration["input_jsonl_prefixes"].items()
        if name.endswith("/admission_opportunities.jsonl")
    )
    path, binding = source
    limit, consumed, digest, rows = binding["complete_prefix_bytes"], 0, hashlib.sha256(), []
    marker = b'"event":"agent_periodic_admission_decision"'
    marker_spaced = b'"event": "agent_periodic_admission_decision"'
    with path.open("rb") as stream:
        while consumed < limit:
            raw = stream.readline(limit - consumed)
            if not raw.endswith(b"\n"):
                raise ValueError("incomplete declared journal prefix")
            digest.update(raw)
            consumed += len(raw)
            if marker not in raw and marker_spaced not in raw:
                continue
            row = json.loads(raw)
            rows.append({"offset_s": row["monotonic_s"] - origin, **classify(row, expected, first)})
    if digest.hexdigest() != binding["complete_prefix_sha256"]:
        raise ValueError("journal does not match the complete predecessor prefix")
    begin, end = complete["cohort"]["throughput_window_offset_seconds"]
    report = {
        "inputs_sha256": pins, "source_prefix": {"path": str(path), **binding},
        "windows": {
            "common_throughput_window": summarize(rows, begin, end),
            "complete_measured_lifetimes": summarize(
                rows, begin, complete["measured_cohort_completion_offset_s"],
            ),
        },
        "scope": {
            "classification_uses_exact_source_request_workflow_invocation_context_epoch": True,
            "continuation_has_a_prior_source_model_call_in_same_context": True,
            "continuation_not_a_physical_residency_or_recoverability_certificate": True,
            "first_calls_include_roots_and_children_and_are_not_all_subject_to_child_gate": True,
            "no_counterfactual_recomputation_queueing_or_JCT_simulation": True,
        },
        "GPU_action": False, "performance_accepted": False,
    }
    if pins != {str(path.resolve()): sha256_file(path) for path in paths}:
        raise ValueError("source documents changed")
    output.mkdir(parents=True)
    with gzip.open(output / "decisions.jsonl.gz", "wt", encoding="ascii") as stream:
        for row in rows:
            stream.write(json.dumps(row, sort_keys=True) + "\n")
    report["decisions_sha256"] = sha256_file(output / "decisions.jsonl.gz")
    write(output / "supply.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("evidence", "expected", "demand", "complete", "output_dir"):
        parser.add_argument("--" + name.replace("_", "-"), required=True, type=Path)
    args = parser.parse_args()
    report = analyze(args.evidence, args.expected, args.demand, args.complete, args.output_dir)
    print(json.dumps({"windows": report["windows"], "GPU_action": False}))


if __name__ == "__main__":
    main()
