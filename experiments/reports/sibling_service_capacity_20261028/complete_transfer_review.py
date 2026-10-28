"""Reuse the complete native transfer balance for the current no-handoff run."""

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
REPORTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts.audit_sealed_wait_prefetch import SealedRecords


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError("preserve previous complete transfer review")
    registration = ROOT.parent / "ops/sibling_service_capacity_20261028_r1.registration.json"
    plan = causal.load(registration)
    if not causal.predecessor_ready({**plan, "after_registration": str(registration)}):
        raise ValueError("require complete Full and actual owned exit")
    arm = Path(plan["run_root"]) / "full"
    reader = SealedRecords()
    observed = sum(
        row.get("event") == "layer_kv_handoff_submitted"
        for row in reader(arm / "opportunities/admission_opportunities.jsonl")
    )
    if observed:
        raise ValueError("this no-handoff operator does not qualify expanded handoff ACK paths")
    helper = ROOT / "experiments/reports/host_demand_layer_handoff_20261028/joint_regression_audit.py"
    spec = importlib.util.spec_from_file_location("sibling_native_transfer_balance", helper)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.inspect("Current Full", registration, "full")
    review_path = REPORTS / "complete_review.json"
    review = causal.load(review_path)
    if result["code_commit"] != review["proofs"]["Full"]["code_commit"]:
        raise ValueError("transfer and complete work review describe different sources")
    if result["prefetch_journal_commands_by_source"].get("tool_wait", 0):
        raise ValueError("disabled tool prefetch actually issued")
    causal.reservation.write_json(output, {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Complete current no-handoff transfer/worker accounting, not exposed waiting.",
        "native_transfer_and_worker_review": result,
        "complete_review_sha256": sha256_file(review_path),
        "helper_sha256": sha256_file(helper),
        "operator_sha256": sha256_file(Path(__file__)),
        "performance_accepted": False,
        "limitations": [
            "Zero handoff is actual workload evidence; real component/model probes exercised it separately.",
            "Stream time and byte totals are not exposed transfer delay or removable workflow JCT.",
            "Last runtime counters are not shutdown-final, exclusive CPU or a complete opportunity recall.",
            "Budget plans, logical grants and physical releases are separate observations.",
        ],
    })
    print({key: result[key] for key in (
        "transfers", "workers", "handoff_events", "prefetch_journal_commands_by_source",
        "native_untagged_demand_plus_handoff_reconciled",
    )})


if __name__ == "__main__":
    main()
