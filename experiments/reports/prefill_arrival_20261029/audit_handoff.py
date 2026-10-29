"""Read complete frozen Full journals; do not infer missing historical branches."""

from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_sealed_wait_prefetch import SealedRecords

HERE = Path(__file__).resolve().parent
OPS = ROOT.parent / "ops"
REGISTRATIONS = {
    "Full R1": "demand_prefill_pipeline_20261028_r1.registration.json",
    "Full R2": "demand_prefill_pipeline_20261028_r2.registration.json",
    "Full ACK repair": "demand_prefill_ack_retry_20261028_r1.registration.json",
}


def main():
    output = HERE / "handoff_historical_audit.json"
    if output.exists():
        raise FileExistsError(output)
    arms = {}
    for label, filename in REGISTRATIONS.items():
        registration = OPS / filename
        plan = json.loads(registration.read_bytes())
        arm = Path(plan["run_root"]) / "full"
        summary = json.loads((arm / "replay/summary.json").read_bytes())
        if summary.get("completed_requests") != plan["source_plan"]["requests"]:
            raise ValueError("historical handoff audit requires complete work")
        reader, submitted, refused, last = SealedRecords(), [], [], None
        for row in reader(arm / "opportunities/admission_opportunities.jsonl"):
            if row.get("event") == "admission_runtime_state":
                last = row
            elif row.get("event") == "layer_kv_handoff_submitted":
                submitted.append(row)
            elif row.get("event") == "layer_kv_handoff_refused":
                refused.append(row)
        counts = last["counts"]
        constraints = Counter()
        for row in refused:
            if row["device_free_tokens"] < row["required_reserve_tokens"]:
                constraints["free_pages_below_input_growth_reserve"] += 1
            if not row["supported_cache"]:
                constraints["unsupported_cache"] += 1
        arms[label] = {
            "registration": str(registration), "registration_sha256": sha256_file(registration),
            "code_commit": plan["code_commit"], "engine_tree": plan["engine_tree"],
            "completed_requests": summary["completed_requests"],
            "submitted_handoffs": len(submitted), "native_refusal_journals": len(refused),
            "necessary_failed_constraints": dict(constraints),
            "native_refusals": refused,
            "submitted_restored_bytes": sum(row["reused_tokens"] * 98304 for row in submitted),
            "last_snapshot_final": last.get("final"),
            "observed_counts": {
                name: value for name, value in counts.items()
                if "handoff" in name or name.startswith("predicted_tool_offload")
                or name.startswith("demand_restore") or name.startswith("tool_offload_cost")
            },
            "sealed_inputs": reader.prefixes,
        }
    report = {
        "arms": arms, "performance_accepted": False,
        "limits": [
            "Necessary failed constraints are proven by each refusal snapshot; additional ancestor/path conditions may also fail.",
            "Pre-native returns were not exhaustively journalled historically; do not assign the unclassified remainder a causal reason.",
            "Snapshot counters are last observed values, not manufactured shutdown-final counts.",
            "Existing Host-backed demotion does not require a new D2H layer handoff.",
            "Transferred bytes or submitted handoffs do not establish exposed waiting saved or positive JCT contribution.",
        ],
        "operator_sha256": sha256_file(Path(__file__)),
    }
    with output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        label: {name: arm[name] for name in (
            "submitted_handoffs", "submitted_restored_bytes",
            "native_refusal_journals", "necessary_failed_constraints",
        )} for label, arm in arms.items()
    }, indent=2))


if __name__ == "__main__":
    main()
