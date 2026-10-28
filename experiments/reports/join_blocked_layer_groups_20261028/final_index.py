"""Seal the complete matched result and its separate correctness/audit evidence."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_native_system_costs import distribution
from scripts.run_causal_comparison import reservation


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError(output)
    reports = Path(__file__).parent
    load = lambda name: json.loads((reports / name).read_bytes())
    core, mechanism = load("complete_review.json"), load("mechanism_review.json")
    linked = load("complete_join_grants_with_copy.json")
    warming = load("complete_wait_prefetch_summary.json")
    native = mechanism["native_transfer_and_worker_review"]
    old_path = ROOT / "experiments/reports/host_demand_layer_handoff_20261028/joint_regression_audit.json"
    old = json.loads(old_path.read_bytes())["arms"]["JointFull"]
    if not all(core.get(field) is True for field in (
        "all_normal_tails_retained", "exact_work_and_native_stopping_verified",
        "two_fresh_idle_snapshots_and_actual_owned_exit_verified",
    )):
        raise ValueError("complete work or cleanup failed")
    if (
        (core["requests"], core["prompt_tokens"], core["output_tokens"])
        != (3674, 145565966, 549671)
        or linked["summary"]["program_statuses"] != {"service_after_grant_verified": 267}
        or mechanism["actual_handoff_direction_groups"] != {"(8, 4)": 23}
        or mechanism["tool_prefetch_command_count"] != 0
        or not native["native_untagged_demand_plus_handoff_reconciled"]
    ):
        raise ValueError("actual work, groups, service or transfer reconciliation differs")
    for source, digest in core["inputs_sha256"].items():
        if sha256_file(Path(source)) != digest:
            raise ValueError("complete comparison input changed")
    if mechanism["operator_sha256"] != sha256_file(reports / "mechanism_review.py") or (
        mechanism["native_review_helper_sha256"] != sha256_file(reports / "handoff_ack_review.py")
    ):
        raise ValueError("native audit source changed")
    cpu = {}
    for name in ("grant_submitted_targeted_cpu.xml", "anchor_and_expanded_ack_cpu.xml"):
        xml = ET.parse(reports / name).getroot()
        if any(list(xml.iter(tag)) for tag in ("failure", "error", "skipped")):
            raise ValueError("independent audit checks failed")
        cpu[name] = len(list(xml.iter("testcase")))
    grants = [
        row for row in linked["programs"]
        if row["grant_event"] == "agent_join_blocked_capacity_admitted"
    ]
    joined = warming["summary"]["wait_events"]["by_source"]["join_ticket"]
    previous_spans = [
        row["journal_ts_ms"] - row["native_pair_submit_ts_ms"] for row in old["handoffs"]
    ]
    figure_dir = Path(core["figure_directory"])
    figures = [path for path in figure_dir.iterdir() if path.is_file()]
    if len([path for path in figures if path.suffix == ".png"]) != 3:
        raise ValueError("complete inspected figures are missing")
    inputs = [
        reports / name for name in (
            "complete_review.json", "mechanism_review.json", "complete_restore_waits.json",
            "complete_wait_prefetch.json.gz", "complete_wait_prefetch_summary.json",
            "complete_join_grants_with_copy.json", "model_review.json", "gpu_review.json",
            "fixture_qualified_shared_cpu.evidence.json",
            "grant_submitted_targeted_cpu.xml", "anchor_and_expanded_ack_cpu.xml",
            "handoff_ack_review.py", "test_handoff_ack_review.py",
            "grant_submitted_initial_red_cpu.xml", "expanded_d2h_ack_initial_red_cpu.xml",
        )
    ]
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Complete matched system result; no isolated module or repeatability claim.",
        "mean_jct_reduction_vs_decay_percent": core["mean_jct_reduction_percent"],
        "end_to_end_output_increase_vs_decay_percent": core["end_to_end_output_increase_percent"],
        "new_blocked_budget_grants": len(grants),
        "new_blocked_budget_grants_with_verified_service": sum(
            row["status"] == "service_after_grant_verified" for row in grants
        ),
        "new_blocked_grants_with_verified_later_copy": sum(
            row["verified_reused_copy_bytes_after_grant"] > 0 for row in grants
        ),
        "verified_copy_bytes_after_blocked_grant": sum(
            row["verified_reused_copy_bytes_after_grant"] for row in grants
        ),
        "grant_to_next_native_service_ms": distribution(
            row["grant_to_next_native_service_ms"] for row in grants
        ),
        "join_waits_with_early_verified_reuse": joined["events_with_early_reused_full"],
        "join_early_verified_reuse_bytes": joined["early_started_and_reused_full_bytes"],
        "layer_handoff_h2d_bytes": native["handoff_consumer_h2d_bytes"],
        "layer_handoff_fraction_of_all_h2d_percent": 100. * (
            native["handoff_consumer_h2d_bytes"] / native["transfers"]["h2d_bytes"]
        ),
        "old_post_submit_to_journal_wall_ms_not_exclusive_cpu": distribution(previous_spans),
        "new_post_submit_to_journal_wall_ms_not_exclusive_cpu": mechanism[
            "post_submit_to_journal_wall_ms_not_exclusive_cpu"
        ],
        "extra_h2d_vs_previous_joint_bytes": (
            native["transfers"]["h2d_bytes"] - old["transfers"]["h2d_bytes"]
        ),
        "extra_d2h_vs_previous_joint_bytes": (
            native["transfers"]["d2h_bytes"] - old["transfers"]["d2h_bytes"]
        ),
        "extra_prefill_worker_interval_vs_previous_joint_s": (
            native["workers"]["prefill_worker_ms"] - old["workers"]["prefill_worker_ms"]
        ) / 1000.,
        "independent_audit_cpu_cases": cpu,
        "three_complete_figures_visually_inspected": True,
        "inputs_sha256": {str(path): sha256_file(path) for path in (*inputs, old_path, *figures)},
        "operator_sha256": sha256_file(Path(__file__)),
        "keep_grouped_overlap_experimental_per_latest_user_direction": True,
        "promote_as_clear_improvement": False,
        "all_baseline_goal_complete": False,
        "next_gpu_run_queued": False,
        "performance_accepted": False,
    }
    reservation.write_json(output, result)
    print(json.dumps({key: result[key] for key in (
        "mean_jct_reduction_vs_decay_percent", "end_to_end_output_increase_vs_decay_percent",
        "new_blocked_budget_grants_with_verified_service",
        "new_blocked_grants_with_verified_later_copy",
        "layer_handoff_fraction_of_all_h2d_percent", "promote_as_clear_improvement",
    )}, indent=2))


if __name__ == "__main__":
    main()
