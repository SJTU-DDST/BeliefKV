"""Summarize the complete regression without assigning an isolated module effect."""

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_native_system_costs import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.run_causal_comparison import reservation

REPORTS = Path(__file__).parent


def tool_copies(arm, reader):
    issues = {
        row["command_id"]: row
        for row in reader(arm / "opportunities/admission_opportunities.jsonl")
        if row["event"] == "prefetch_native_issued" and row.get("source") == "tool_wait"
    }
    copied = {}
    for row in reader(arm / "server/transfer_telemetry.jsonl"):
        for child in row.get("tagged_child_commits", ()):
            command = child.get("command_id")
            if command not in issues:
                continue
            if (
                row["direction"] != "h2d" or row["status"] != "completed"
                or command in copied
                or child["num_bytes"] != child["num_tokens_by_pool"]["kv"] * 98304
            ):
                raise ValueError("tool command lacks a unique completed FULL H2D")
            copied[command] = child["num_bytes"]
    uses = {}
    for row in reader(arm / "server/physical_action_use.jsonl"):
        command = row.get("command_id")
        if row["event"] == "beliefkv_prefetch_first_service" and command in issues:
            if command in uses or row.get("full_reuse_proof_version") != 2:
                raise ValueError("ambiguous or unsupported tool first-use evidence")
            uses[command] = row["full_node_reused"]
    return {
        "issued_node_commands": len(issues),
        "completed_h2d_node_commands": len(copied),
        "completed_h2d_bytes": sum(copied.values()),
        "first_service_reuse_verified_commands": sum(
            uses.get(command) is True for command in copied
        ),
        "first_service_reuse_verified_bytes": sum(
            amount for command, amount in copied.items() if uses.get(command) is True
        ),
        "first_service_reuse_negative_commands": sum(
            uses.get(command) is False for command in copied
        ),
        "first_service_reuse_unknown_commands": sum(
            uses.get(command) is None for command in copied
        ),
        "semantics": "Actual tagged H2D and version-2 first-use records; no isolated JCT effect.",
    }


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError(output)
    names = (
        "complete_core_review", "joint_regression_audit", "complete_jct_paths",
        "complete_service_interleaving", "complete_grant_service",
    )
    reports = {name: json.loads((REPORTS / f"{name}.json").read_bytes()) for name in names}
    core = reports["complete_core_review"]
    transfer = reports["joint_regression_audit"]["arms"]
    paths = reports["complete_jct_paths"]["arms"]
    service = reports["complete_service_interleaving"]["arms"]
    grants = reports["complete_grant_service"]
    means = {
        name: row["critical_path"]["workflow_equal_weight_mean_s"]
        for name, row in service.items()
    }
    for name, row in paths.items():
        if abs(row["mean_jct_s"] - means[name]["jct_s"]) > 1e-6:
            raise ValueError("critical-path clocks differ")
    handoffs = transfer["JointFull"]["handoffs"]
    reader = SealedRecords()
    tools = {
        name: tool_copies(Path(service[name]["arm"]), reader)
        for name in ("JointFull", "PreviousFull")
    }
    for path, seal in reader.prefixes.items():
        if (
            Path(path).stat().st_size != seal["complete_prefix_bytes"]
            or sha256_file(Path(path)) != seal["complete_prefix_sha256"]
        ):
            raise ValueError("complete evidence changed")
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "complete_requests": core["requests"],
        "complete_output_tokens": core["output_tokens"],
        "all_normal_tails_retained": core["all_normal_tails_retained"],
        "previous_to_joint_mean_jct_delta_s": (
            core["metrics"]["Joint Full"]["mean_jct_s"]
            - core["previous_online_refresh_full"]["metrics"]["mean_jct_s"]
        ),
        "critical_path_means_s": means,
        "previous_to_joint_critical_path_delta_s": {
            key: value - means["PreviousFull"][key]
            for key, value in means["JointFull"].items()
        },
        "previous_to_joint_transfer_byte_delta": {
            direction: (
                transfer["JointFull"]["transfers"][direction + "_bytes"]
                - transfer["PreviousFull"]["transfers"][direction + "_bytes"]
            ) for direction in ("d2h", "h2d")
        },
        "handoff": {
            "operations": len(handoffs),
            "consumer_bytes": sum(row["consumer_bytes"] for row in handoffs),
            "consumer_h2d_fraction": (
                sum(row["consumer_bytes"] for row in handoffs)
                / transfer["JointFull"]["transfers"]["h2d_bytes"]
            ),
            "consumer_tokens": distribution(row["consumer_tokens"] for row in handoffs),
            "host_submit_to_journal_ms": distribution(
                row["journal_ts_ms"] - row["native_pair_submit_ts_ms"] for row in handoffs
            ),
            "host_submit_to_journal_sum_ms": sum(
                row["journal_ts_ms"] - row["native_pair_submit_ts_ms"] for row in handoffs
            ),
            "h2d_stream_span_sum_ms_including_dependencies": sum(
                row["h2d_stream_ms_including_dependencies"] for row in handoffs
            ),
        },
        "idle_prepare_alignment": {
            "full_bytes_per_token": 98304,
            "maximum_batch_bytes": 512 * 1024**2,
            "aligned_batch_bytes": 512 * 1024**2 // 98304 * 98304,
            "false_shortfall_bytes": 512 * 1024**2 % 98304,
            "joint_checked_dispatches_all_rejected": transfer["JointFull"][
                "last_recorded_counts_not_shutdown_final"
            ]["idle_prepare_host_demand_reserve_skips"],
            "joint_issued_batches": transfer["JointFull"][
                "last_recorded_counts_not_shutdown_final"
            ].get("idle_full_prepare_issued_batches", 0),
            "previous_native_idle_stats": transfer["PreviousFull"][
                "last_recorded_idle_prepare"
            ]["native"],
        },
        "sibling_grants": {
            "summary": grants["summary"],
            "newly_granted_programs_per_sibling_event": dict(Counter(
                row["newly_granted_programs"] for row in grants["groups"]
                if row["event"] == "agent_join_siblings_coadmitted"
            )),
            "multi_member_service_entry_spread_ms": [
                row["newly_granted_service_start_spread_ms"] for row in grants["groups"]
                if row["event"] == "agent_join_siblings_coadmitted"
                and row["newly_granted_programs"] > 1
            ],
        },
        "tool_prefetch": tools,
        "input_reports_sha256": {
            str(REPORTS / f"{name}.json"): sha256_file(REPORTS / f"{name}.json")
            for name in names
        },
        "input_jsonl_prefixes": reader.prefixes,
        "operator_sha256": sha256_file(Path(__file__)),
        "limitations": [
            "Threshold, Host policy and engine changed together; isolated attribution is unavailable.",
            "Worker intervals include waits and runtime; they are not pure GPU kernel durations.",
            "Host submission and stream spans cannot be subtracted directly from workflow JCT.",
            "Sibling grants do not count every concurrent JOIN or guarantee simultaneous completion.",
            "The alignment repair has CPU regression evidence, not a new GPU performance result.",
        ],
        "performance_accepted": False,
    }
    reservation.write_json(output, result)
    print(json.dumps({
        key: result[key] for key in (
            "previous_to_joint_mean_jct_delta_s", "handoff", "sibling_grants", "tool_prefetch",
        )
    }, indent=2))


if __name__ == "__main__":
    main()
