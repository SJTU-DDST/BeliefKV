"""Inventory every explicit backup route instead of one offload event marker."""

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path("/home/longhao/experiment/BeliefKV")
OPS = ROOT.parent / "ops"
REPORTS = ROOT / "experiments/reports/remaining_original_projects_20261030"
HERE = REPORTS / "handoff_prepare_route_inventory_r4"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts.audit_native_system_costs import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution
from audit_final82_live_tail_20261031 import prefix_digest


def inspect(registration, cohort, *, complete):
    plan = causal.load(registration)
    if complete:
        verify_execution(registration, cohort)
    else:
        state = causal.load(registration.with_suffix(".state.json"))
        if state["status"] != "running_full" or not all(
            causal.reservation.alive(state[name]) for name in ("watcher", "driver", "server")
        ):
            raise ValueError("a live prefix requires the actual declared serving owners")
    arm, reader = Path(plan["run_root"]) / "full", SealedRecords()
    issued, predicted, last = {}, set(), None
    for row in reader(arm / "opportunities/admission_opportunities.jsonl"):
        if row.get("event") == "prepare_native_issued":
            if row["command_id"] in issued:
                raise ValueError("explicit backup command was logged twice")
            issued[row["command_id"]] = row
        elif row.get("event") == "predicted_tool_offload_backup":
            predicted.update(row["command_ids"])
        elif row.get("event") == "admission_runtime_state":
            last = row
    if not predicted <= issued.keys() or last is None:
        raise ValueError("detailed backup markers or final native state are missing")
    transfers, tags, all_d2h = {}, {}, []
    for row in reader(arm / "server/transfer_telemetry.jsonl"):
        if row["direction"] != "d2h":
            continue
        if row["status"] != "completed" or row["command_id"] in transfers:
            raise ValueError("require unique completed native D2H receipts")
        transfers[row["command_id"]] = row
        all_d2h.append(row)
        for child in row.get("tagged_child_commits", ()):
            if child["command_id"] in tags:
                raise ValueError("shadow child command has multiple native final receipts")
            tags[child["command_id"]] = row["command_id"], child
    groups = defaultdict(list)
    for command, row in issued.items():
        groups[row.get("source", "unknown")].append(command)
    groups["dedicated_predicted_marker_subset"] = sorted(predicted)
    groups["tool_wait_without_dedicated_marker"] = [
        command for command in groups.get("tool_wait", ()) if command not in predicted
    ]
    summaries = {}
    for source, commands in groups.items():
        matched = [command for command in commands if command in tags]
        native = {tags[command][0] for command in matched}
        summaries[source] = {
            "issued_commands": len(commands), "native_tagged_commands": len(matched),
            "missing_native_tag_commands": sorted(set(commands) - tags.keys()),
            "actual_tagged_bytes": sum(tags[command][1]["num_bytes"] for command in matched),
            "actual_tagged_tokens": distribution(
                tags[command][1]["num_tokens_by_pool"].get("kv", 0) for command in matched),
            "containing_native_D2H_stream_ms_deduplicated": sum(
                transfers[identity]["transfer_stream_elapsed_ms"] for identity in native),
            "containing_native_D2H_submit_to_ACK_ms_deduplicated": sum(
                transfers[identity]["submit_to_ack_ms"] for identity in native),
        }
    if complete and any(
        row["missing_native_tag_commands"] for row in summaries.values()
    ):
        raise ValueError("complete backup inventory lacks final native receipts")
    agents = last.get("agent_admission") or {}
    counts = agents.get("counts") or {}
    result = {
        "registration": str(registration), "complete_measured_work_and_owned_exit_verified": complete,
        "route_summary": summaries,
        "all_logged_explicit_backup_commands": len(issued),
        "all_native_tagged_shadow_commands": sum(command.startswith("beliefkv-shadow-") for command in tags),
        "native_shadow_tags_without_prepare_issue_record": sorted(
            command for command in tags
            if command.startswith("beliefkv-shadow-") and command not in issued),
        "all_native_D2H_stream_ms": sum(row["transfer_stream_elapsed_ms"] for row in all_d2h),
        "last_runtime_counts": {
            name: last.get("counts", {}).get(name, 0)
            for name in ("shadow_native_issued", "predicted_tool_offload_backup_issued",
                         "tool_prepare_issued", "join_prepare_issued", "idle_cold_full_backup_issued")
        },
        "last_agent_counts": {
            name: counts.get(name, 0) for name in (
                "tool_event_replacement_backup_attempts", "tool_event_replacement_grants",
                "tool_event_replacement_no_complete_pair")
        },
        "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {str(path): sha256_file(path) for path in (
            registration, cohort, Path(__file__),
            OPS / "audit_final82_live_tail_20261031.py",
        )},
        "scope": [
            "prepare_native_issued includes ordinary prediction, event replacement, tool preparation and JOIN preparation.",
            "The dedicated predicted_tool_offload_backup marker is a subset; event replacement does not emit it.",
            "tool_wait is a conservative superset that can include proactive tool preparation.",
            "join_prepare includes proactive preparation and demand-driven JOIN physical recovery.",
            "A native copy can coalesce routes; route service sums must not be added.",
            "No submit-to-ACK sum, stream sum or capacity deficit is asserted to be removable JCT.",
            "Live missing tags are pending or outside the independently sealed receipt prefix, not zero cost.",
        ],
        "GPU_action": False, "performance_accepted": False,
    }
    for filename, prefix in reader.prefixes.items():
        if prefix_digest(Path(filename), prefix["complete_prefix_bytes"]) != prefix["complete_prefix_sha256"]:
            raise ValueError("sealed backup route evidence changed")
    return result


def main():
    if HERE.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve evidence and use nice19 CPU-only analysis")
    definitions = {
        "best_original56": (
            OPS / "workflow_original56_initial12_mean70_toolaware_control_20261031_r1.registration.json",
            REPORTS / "sustained56_initial12_mean70_r1/analysis_cohort.json", True,
        ),
        "latest_original69": (
            OPS / "workflow_original69_initial12_mean70_predictive_control_20261031_r1.registration.json",
            REPORTS / "original69_initial12_mean70_prepare_r1/analysis_cohort.json", True,
        ),
        "live_original82": (
            OPS / "workflow_final82_initial12_mean70_predictive_control_20261031_r1.registration.json",
            REPORTS / "final_oracle_original82_traffic_r1/analysis_cohort.json", False,
        ),
    }
    results = {}
    for label, (registration, cohort, complete) in definitions.items():
        result = inspect(registration, cohort, complete=complete)
        write(HERE / (label + ".json"), result)
        results[label] = result
        print(json.dumps({
            "label": label, "routes": result["route_summary"],
            "runtime_counts": result["last_runtime_counts"], "agent_counts": result["last_agent_counts"],
        }), flush=True)
    write(HERE / "audit.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(), "arms": results,
        "prior_audits_preserved_with_dedicated_marker_scope_only": True,
        "GPU_action": False,
    })


if __name__ == "__main__":
    main()
