"""Measure actual unbacked donor/copy scale before another layer-handoff run."""

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path("/home/longhao/experiment/BeliefKV")
OPS = ROOT.parent / "ops"
REPORTS = ROOT / "experiments/reports/remaining_original_projects_20261030"
HERE = REPORTS / "handoff_service_scale_complete_controls_r1"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts.audit_native_system_costs import distribution
from scripts.audit_restore_wait_cohorts import analyze
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_selected_capacity_shortfalls import summarize as summarize_shortfalls
from scripts.verify_measured_execution import verify_execution
from scripts.train_workflow_work import write


def inspect(registration, cohort):
    execution = verify_execution(registration, cohort)
    plan = causal.load(registration)
    arm = Path(plan["run_root"]) / "full"
    capacity_path = arm / "server/native_capacity_census.json"
    capacity = causal.load(capacity_path)["capacity"]
    if capacity["device_mamba_slots"] or capacity["host_mamba_slots"]:
        raise ValueError("this service-scale model is explicitly FULL-only")
    bytes_per_token = capacity["host_full_bytes"] / capacity["host_full_tokens"]
    sealed, directions, command_rows = SealedRecords(), defaultdict(list), defaultdict(list)
    for row in sealed(arm / "server/transfer_telemetry.jsonl"):
        if row.get("status") != "completed":
            raise ValueError("completed-control telemetry contains an incomplete transfer")
        directions[row["direction"]].append(row)
        command_rows[row["command_id"]].append(row)
    transfers = {
        direction: {
            "records": len(rows), "bytes": sum(row["actual_bytes"] for row in rows),
            "stream_ms": distribution(row["transfer_stream_elapsed_ms"] for row in rows),
            "submit_to_ACK_ms": distribution(row["submit_to_ack_ms"] for row in rows),
        } for direction, rows in directions.items()
    }
    events, backups, evictions = [], [], []
    for row in sealed(arm / "opportunities/admission_opportunities.jsonl"):
        if row.get("event") in (
            "selected_consumer_capacity_shortfall", "layer_kv_handoff_geometry_shortfall",
            "layer_kv_handoff_submitted", "layer_kv_handoff_refused",
        ):
            events.append(row)
        elif row.get("event") == "predicted_tool_offload_backup":
            backups.append(row)
        elif row.get("event") == "predicted_tool_kv_evicted":
            evictions.append(row)
    shortfalls = summarize_shortfalls(events)
    bound_copies, unknown_copies = [], []
    latest = {}
    for row in sorted(backups + evictions, key=lambda row: row["ts_ms"]):
        key = row["context_id"], row["context_epoch"], row.get("beneficiary_request_id")
        if row["event"] == "predicted_tool_offload_backup":
            latest[key] = row
        elif key in latest:
            backup = latest.pop(key)
            copies = [
                item for command in backup["command_ids"] for item in command_rows.get(command, ())
                if item["direction"] == "d2h"
            ]
            budgeted = backup.get("backup_tokens_budgeted")
            if type(budgeted) is not int or budgeted < 0:
                raise ValueError("predicted backup lacks its actual bounded token budget")
            restore = backup.get("beneficiary_restore_bytes_budgeted")
            result = {
                "context_id": key[0], "context_epoch": key[1], "beneficiary_request_id": key[2],
                "backup_tokens_budgeted": budgeted,
                "backup_bytes_budgeted": budgeted * bytes_per_token,
                "beneficiary_restore_bytes_budgeted": restore,
                "backup_to_eviction_observed_ms": row["ts_ms"] - backup["ts_ms"],
                "freed_bytes": row.get("freed_bytes"),
                "matched_D2H_records": len(copies),
                "matched_D2H_stream_ms": sum(item["transfer_stream_elapsed_ms"] for item in copies),
            }
            (bound_copies if copies else unknown_copies).append(result)
    samples = [
        row for row in sealed(arm / "server/runtime_audit.jsonl")
        if row.get("event") == "gpu_restore_dependency_wait"
    ]
    waits = analyze(samples, {}, {}, {})["summary"]
    certificate = causal.load(Path(execution["certificate"]))
    measured_start = min(certificate["measured_end_ms"][key] - certificate["measured_JCT_ms"][key]
                         for key in certificate["measured_workflow_ids"])
    span = certificate["cohort_completion_monotonic_ms"] - measured_start
    d2h = transfers["d2h"]["stream_ms"]["sum"]
    h2d = transfers["h2d"]["stream_ms"]["sum"]
    backup_tokens = [row["backup_tokens_budgeted"] for row in backups]
    tail_ratios = [
        row["backup_tokens_budgeted"] * bytes_per_token / row["beneficiary_restore_bytes_budgeted"]
        for row in backups if row.get("beneficiary_restore_bytes_budgeted", 0) > 0
    ]
    for name, prefix in sealed.prefixes.items():
        if Path(name).stat().st_size != prefix["complete_prefix_bytes"] or (
            sha256_file(Path(name)) != prefix["complete_prefix_sha256"]
        ):
            raise ValueError("completed-control evidence changed")
    return {
        "registration": str(registration), "complete_measured_requests": execution["measured_requests"],
        "actual_owned_exit_verified": True, "transfers": transfers,
        "unbacked_predicted_backup_episodes": len(backups),
        "settled_predicted_eviction_events": len(evictions),
        "backup_token_budgets": distribution(backup_tokens),
        "backup_budget_fraction_of_consumer_restore": distribution(tail_ratios),
        "backups_with_following_same_identity_eviction_and_bound_D2H": bound_copies,
        "following_eviction_but_D2H_identity_unobserved": unknown_copies,
        "without_matching_following_eviction": len(latest),
        "selected_consumer_capacity": shortfalls,
        "sampled_actual_GPU_restore_wait": waits,
        "transfer_service_scale": {
            "all_recorded_D2H_stream_ms": d2h, "all_recorded_H2D_stream_ms": h2d,
            "optimistic_additional_two_direction_overlap_ms": min(d2h, h2d),
            "fixed_measured_execution_span_ms": span,
            "optimistic_stream_scale_fraction_of_span": min(d2h, h2d) / span,
            "all_background_copies_included_so_not_measurement_attribution": True,
            "JCT_or_exposed_stall_upper_bound_claimed": False,
        },
        "input_jsonl_prefixes": sealed.prefixes,
        "inputs_sha256": {str(path): sha256_file(path) for path in (
            registration, cohort, Path(execution["certificate"]), capacity_path,
        )},
    }


def main():
    if HERE.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve audits and use CPU-only nice19")
    definitions = {
        "best_original56": (
            OPS / "workflow_original56_initial12_mean70_toolaware_control_20261031_r1.registration.json",
            REPORTS / "sustained56_initial12_mean70_r1/analysis_cohort.json",
        ),
        "latest_original69": (
            OPS / "workflow_original69_initial12_mean70_predictive_control_20261031_r1.registration.json",
            REPORTS / "original69_initial12_mean70_prepare_r1/analysis_cohort.json",
        ),
    }
    result = {}
    for label, (registration, cohort) in definitions.items():
        value = inspect(registration, cohort)
        result[label] = value
        write(HERE / (label + ".json"), value)
        print(json.dumps({
            "arm": label, "unbacked_backup_episodes": value["unbacked_predicted_backup_episodes"],
            "backup_tokens": value["backup_token_budgets"], "service_scale": value["transfer_service_scale"],
            "matched_bound_copy_episodes": len(value["backups_with_following_same_identity_eviction_and_bound_D2H"]),
        }), flush=True)
    write(HERE / "audit.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(), "arms": result,
        "semantics": [
            "Actual unbacked backups and settled duplicate eviction are distinct.",
            "A capacity deficit does not prove fresh donor D2H is mandatory.",
            "Service sums and sampled GPU waits are not removable workflow JCT or expanded waits.",
            "The full-pool handoff changes neither legal ready-agent preemption nor future capacity authority.",
            "No GPU handoff run is authorized by this descriptive audit alone.",
        ],
        "operator_sha256": sha256_file(Path(__file__)), "GPU_action": False,
    })


if __name__ == "__main__":
    main()
