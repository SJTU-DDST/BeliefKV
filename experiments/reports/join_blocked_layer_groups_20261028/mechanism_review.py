"""Associate complete JOIN grants and actual grouped handoffs with native service."""

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
REPORTS = Path(__file__).parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(REPORTS))

from handoff_ack_review import inspect
from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_reference_join_grants import audit as audit_grants
from scripts.audit_native_system_costs import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import run_causal_comparison as causal


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError(output)
    complete_path = REPORTS / "complete_review.json"
    complete = causal.load(complete_path)
    if not all(complete.get(field) is True for field in (
        "all_normal_tails_retained", "exact_work_and_native_stopping_verified",
        "two_fresh_idle_snapshots_and_actual_owned_exit_verified",
    )):
        raise ValueError("mechanism review requires complete work and cleanup")
    registration = ROOT.parent / "ops/join_blocked_layer_groups_20261028_r1.registration.json"
    plan = causal.load(registration)
    arm = Path(plan["run_root"]) / "full"
    transfers = inspect("Grouped Full", registration, "full")
    reader, groups, event_counts = SealedRecords(), Counter(), Counter()
    for row in reader(arm / "opportunities/admission_opportunities.jsonl"):
        event = row.get("event")
        event_counts[event] += 1
        if event == "layer_kv_handoff_submitted":
            pair = row["donor_group_layers"], row["consumer_group_layers"]
            if pair != (8, 4):
                raise ValueError("actual handoff does not use the declared direction groups")
            groups[str(pair)] += 1
        elif event == "prefetch_native_issued" and row.get("source") == "tool_wait":
            raise ValueError("cancelled tool prefetch issued an actual command")
    grants = audit_grants(arm)
    for name, prefix in {**reader.prefixes, **grants["input_jsonl_prefixes"]}.items():
        if Path(name).stat().st_size != prefix["complete_prefix_bytes"] or (
            sha256_file(Path(name)) != prefix["complete_prefix_sha256"]
        ):
            raise ValueError("complete mechanism inputs changed")
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Actual logical grants, paired ACKs and same-session service associations.",
        "complete_review_sha256": sha256_file(complete_path),
        "native_transfer_and_worker_review": transfers,
        "join_grants": grants,
        "actual_handoff_direction_groups": dict(groups),
        "post_submit_to_journal_wall_ms_not_exclusive_cpu": distribution(
            row["journal_ts_ms"] - row["native_pair_submit_ts_ms"]
            for row in transfers["handoffs"]
        ),
        "native_run_batch_before_final_donor_ack_count": sum(
            row["native_run_batch_before_d2h_ack"] for row in transfers["handoffs"]
        ),
        "new_budget_handoff_events": event_counts["agent_join_blocked_capacity_admitted"],
        "tool_prefetch_command_count": transfers["prefetch_journal_commands_by_source"].get("tool_wait", 0),
        "operator_sha256": sha256_file(Path(__file__)),
        "native_review_helper_sha256": sha256_file(
            REPORTS / "handoff_ack_review.py"
        ),
        "input_jsonl_prefixes": reader.prefixes,
        "isolated_performance_attribution": False,
        "performance_accepted": False,
        "limitations": [
            "Logical budget release does not prove physical HBM release.",
            "Native run_batch service start does not expose a CUDA kernel read or hidden DMA time.",
            "Stream durations include dependencies and are not exclusive DMA or JCT delay.",
            "D2H ACKs publish backup paths; H2D publishes its anchor and aggregate bytes, not node generations.",
            "Grouping, admission, idle prepare and tool-prefetch cancellation changed together.",
        ],
    }
    causal.reservation.write_json(output, result)
    print(json.dumps({
        "join_grants": grants["summary"], "layer_handoffs": transfers["handoff_events"],
        "actual_handoff_direction_groups": dict(groups),
        "handoff_h2d_bytes": transfers["handoff_consumer_h2d_bytes"],
        "new_budget_handoff_events": result["new_budget_handoff_events"],
        "workers": transfers["workers"], "transfers": transfers["transfers"],
        "tool_prefetch_command_count": result["tool_prefetch_command_count"],
        "post_submit_to_journal_wall_ms_not_exclusive_cpu": result[
            "post_submit_to_journal_wall_ms_not_exclusive_cpu"
        ],
    }, indent=2))


if __name__ == "__main__":
    main()
