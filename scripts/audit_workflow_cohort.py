"""Audit a complete fixed cohort without truncating tails or changing arrivals."""

import argparse
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
