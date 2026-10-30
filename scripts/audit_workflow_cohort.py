"""Audit a complete fixed cohort without truncating tails or changing arrivals."""

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workflow_cohort import summarize_cohort
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", type=Path, required=True)
    parser.add_argument("--arrivals", type=Path, required=True)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bin-seconds", type=float, default=30.)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve every cohort measurement")
    reader = SealedRecords()
    report = summarize_cohort(
        json.loads(args.cohort.read_bytes()), json.loads(args.arrivals.read_bytes()),
        reader(args.arm / "replay/runtime_events.replay.jsonl"),
        reader(args.arm / "server/runtime_audit.jsonl"), bin_seconds=args.bin_seconds,
    )
    requests, output_by_workflow = set(), Counter()
    for receipt in reader(args.arm / "replay/native_requests.jsonl"):
        if (
            receipt.get("exact_tokens_verified") is not True
            or receipt["request_id"] in requests
            or type(receipt.get("output_tokens")) is not int
            or receipt["output_tokens"] <= 0
            or receipt["native_receipt"]["prompt_tokens"] != receipt["prompt_tokens"]
            or len(receipt["native_receipt"]["output_token_ids"]) != receipt["output_tokens"]
        ):
            raise ValueError("cohort throughput requires unique exact native receipts")
        requests.add(receipt["request_id"])
        output_by_workflow[receipt["native_receipt"]["identity"][0]] += receipt["output_tokens"]
    if (
        not requests
        or dict(output_by_workflow) != report["all_service_output_tokens_by_workflow"]
    ):
        raise ValueError("native service output accounting differs from complete exact receipts")
    report["service_output_matches_complete_receipts"] = True
    report["exact_receipt_requests"] = len(requests)
    for path, prefix in reader.prefixes.items():
        if (
            Path(path).stat().st_size != prefix["complete_prefix_bytes"]
            or sha256_file(Path(path)) != prefix["complete_prefix_sha256"]
        ):
            raise ValueError("cohort audit requires complete unchanged serving artifacts")
    report.update(
        input_jsonl_prefixes=reader.prefixes,
        inputs_sha256={str(path.resolve()): sha256_file(path)
                       for path in (args.arrivals, args.cohort, Path(__file__))},
    )
    write(args.output, report)
    print({
        "measured_workflows": report["measured_complete_workflow_JCT_ms"]["count"],
        "mean_JCT_seconds": report["measured_complete_workflow_JCT_ms"]["mean"] / 1000.,
        "background_covers_measured": report["background_submission_coverage"]["covers_all_measured_completions"],
    })


if __name__ == "__main__":
    main()
