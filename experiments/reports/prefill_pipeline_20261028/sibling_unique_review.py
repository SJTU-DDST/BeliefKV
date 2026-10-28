"""Separate multi-member service journals from distinct native forward batches."""

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal


def main():
    output = HERE / "sibling_unique_review.json"
    if output.exists():
        raise FileExistsError("preserve original sibling-forward counting evidence")
    source = HERE / "mechanism_review.json"
    sibling = causal.load(source)["sibling_service"]
    rows = [
        row for row in sibling["records"]
        if row["status"] == "native_batch_join_and_completed_sessions_verified"
        and row["members"] >= 2
    ]
    if len(rows) != sibling["summary"]["verified_multi_member_batches"]:
        raise ValueError("legacy multi-member journal count differs")
    forwards = defaultdict(list)
    for row in rows:
        if type(row["forward_iter"]) is not int or row["phase"] != "prefill":
            raise ValueError("multi-member service lacks its actual native prefill identity")
        forwards[(row["forward_iter"], row["worker_sample_id"])].append({
            "join_id": row["join_id"], "request_ids": row["request_ids"],
            "journal_number": row["journal_number"],
        })
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Unique native forward counting after complete sibling/session verification.",
        "verified_multi_member_journal_records": len(rows),
        "verified_distinct_multi_member_native_forwards": len(forwards),
        "forwards": [
            {"forward_iter": key[0], "worker_sample_id": key[1], "join_records": value}
            for key, value in sorted(forwards.items())
        ],
        "inputs_sha256": {
            str(path): sha256_file(path) for path in (Path(__file__), source)
        },
        "legacy_auditor_or_report_changed": False,
        "performance_accepted": False,
    }
    causal.reservation.write_json(output, result)
    print({
        key: result[key] for key in (
            "verified_multi_member_journal_records",
            "verified_distinct_multi_member_native_forwards",
        )
    })


if __name__ == "__main__":
    main()
