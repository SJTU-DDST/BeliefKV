"""Read-only forecast transitions and complete-run transfer/metric comparison."""

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[3]
OPS = ROOT.parent / "ops"
NAMES = (
    "tool_offload_before_admission_20261027_r1",
    "tool_prediction_curve_repair_20261027_r1",
    "tool_online_refresh_20261027_r1",
)
FROZEN = Path(json.loads((OPS / (NAMES[-1] + ".registration.json")).read_bytes())["execution_checkout"])
sys.path.insert(0, str(FROZEN))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.sglang_v0520_prediction import NativeToolWaitHint
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import run_causal_comparison as causal


def forecast_probability(row):
    issued = row["observed_monotonic_ms"]
    hint = NativeToolWaitHint(
        None, row["p10_ms"], row["p50_ms"], row["p90_ms"],
        issued, row.get("expires_monotonic_ms", issued + 1000.), "analysis",
        release_cdf=tuple(map(tuple, row["release_cdf"])),
        residual_duration_mass=tuple(map(tuple, row.get("residual_duration_mass", ()))),
        residual_support_detail=row.get("residual_support_detail"),
    )
    return hint.release_probability_within(1000., now_ms=issued)


def inspect(name):
    path = OPS / (name + ".registration.json")
    plan = causal.load(path)
    if not causal.predecessor_ready({**plan, "after_registration": str(path)}):
        raise ValueError("read only complete, actually exited runs")
    arm = Path(plan["run_root"]) / "full"
    reader = SealedRecords()
    groups, offloads = defaultdict(list), []
    last = None
    for row in reader(arm / "opportunities/admission_opportunities.jsonl"):
        if row.get("event") == "admission_runtime_state":
            last = row
        elif row.get("event") == "tool_wait_forecast":
            key = tuple(row[field] for field in (
                "workflow_id", "invocation_id", "context_id",
                "context_epoch", "invocation_revision_ts_ms",
            ))
            groups[key].append(row)
        elif row.get("event") == "predicted_tool_kv_evicted":
            offloads.append(row)
    if last is None or last["physical_disabled"]:
        raise ValueError("missing healthy last recorded runtime snapshot")
    transitions, examples = 0, []
    seen_pass, seen_fail, reissued, forecasts = 0, 0, 0, 0
    for key, rows in groups.items():
        rows.sort(key=lambda row: row["observed_monotonic_ms"])
        reissued += len(rows) > 1
        forecasts += len(rows)
        if name != NAMES[-1]:
            continue
        probabilities = [forecast_probability(row) for row in rows]
        flags = [p is not None and p <= .1 + 1e-12 for p in probabilities]
        seen_pass += sum(flags)
        seen_fail += len(flags) - sum(flags)
        if flags and not flags[0] and any(flags[1:]):
            transitions += 1
            index = flags.index(True)
            if len(examples) < 5:
                examples.append({
                    "wait_identity": key,
                    "first_completion_probability_within_1s": probabilities[0],
                    "later_completion_probability_within_1s": probabilities[index],
                    "delay_to_first_pass_ms": (
                        rows[index]["observed_monotonic_ms"] - rows[0]["observed_monotonic_ms"]
                    ),
                    "first_scope": rows[0].get("residual_support_detail"),
                    "later_scope": rows[index].get("residual_support_detail"),
                })
    transfers, stream_ms, operations, tagged = Counter(), Counter(), Counter(), Counter()
    for row in reader(arm / "server/transfer_telemetry.jsonl"):
        direction = row["direction"]
        if row["status"] != "completed" or direction not in ("h2d", "d2h"):
            raise ValueError("incomplete or unknown transfer")
        transfers[direction] += row["actual_bytes"]
        operations[direction] += 1
        stream_ms[direction] += row["transfer_stream_elapsed_ms"]
        tagged[direction] += sum(
            item["num_bytes"] for item in row.get("tagged_child_commits", ())
            if item.get("command_id") is not None
        )
    summary_path = arm / "replay/summary.json"
    summary = causal.load(summary_path)
    if summary["completed_requests"] != 3674 or not summary["exact_tokens_verified"]:
        raise ValueError("incomplete work")
    elapsed = list(summary["workflow_elapsed_ms"].values())
    if len(elapsed) != 18:
        raise ValueError("workflow population changed")
    makespan = (summary["finished_monotonic_ms"] - summary["started_monotonic_ms"]) / 1000.
    count_keys = (
        "online_tool_supported", "online_tool_cold_or_tail_unsupported",
        "tool_wait_accepted", "tool_wait_expired", "tool_hint_physical_inspection_deferred",
        "prediction_maintenance_sweeps", "tool_prefetch_scans",
        "tool_offload_cost_rejected", "predicted_tool_offload_evicted_nodes",
        "predicted_tool_offload_freed_bytes", "predicted_tool_offload_backup_issued",
    )
    return {
        "registration": str(path), "registration_sha256": sha256_file(path),
        "code_commit": plan["code_commit"],
        "mean_jct_s": statistics.mean(elapsed) / 1000.,
        "makespan_s": makespan, "end_to_end_output_tokens_per_s": 549671 / makespan,
        "counts": {key: last["counts"].get(key, 0) for key in count_keys},
        "counts_scope": "last recorded snapshot in complete file; not a shutdown-final record",
        "forecast_gate": {
            "forecast_records": forecasts, "observed_wait_groups": len(groups),
            "same_wait_groups_with_multiple_forecasts": reissued,
            "gate_evaluated_only_for_current_frozen_curve_semantics": name == NAMES[-1],
            "passing_at_issuance": seen_pass, "failing_or_unsupported_at_issuance": seen_fail,
            "first_failed_then_later_passed_wait_groups": transitions, "examples": examples,
            "horizon_ms": 1000., "minimum_survival": .9,
            "not_actual_cost_capacity_tail_support_or_physical_eligibility": True,
        },
        "predicted_offload_journal_events": len(offloads),
        "predicted_offload_journal_bytes": sum(row["freed_bytes"] for row in offloads),
        "transfer_bytes": dict(transfers), "native_demand_h2d_bytes": transfers["h2d"] - tagged["h2d"],
        "transfer_stream_ms_sum_not_exposed_wait": dict(stream_ms),
        "transfer_operations": dict(operations),
        "full_scheduler_profile_enabled": last["shared_path_timing"]["enabled"],
        "forecast_journal_writer_samples": last["opportunity_writer_timing"]["categories"].get("tool_wait_forecast"),
        "input_jsonl_prefixes": reader.prefixes,
        "summary_sha256": sha256_file(summary_path),
    }


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError(output)
    runs = {name: inspect(name) for name in NAMES}
    current = runs[NAMES[-1]]
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "complete observed counters and forecast gates, not causal module attribution",
        "runs": runs,
        "current_vs_first": {
            "mean_jct_difference_s": current["mean_jct_s"] - runs[NAMES[0]]["mean_jct_s"],
            "mean_jct_increase_percent": 100. * (current["mean_jct_s"] / runs[NAMES[0]]["mean_jct_s"] - 1.),
        },
        "current_vs_direct_predecessor": {
            "mean_jct_difference_s": current["mean_jct_s"] - runs[NAMES[1]]["mean_jct_s"],
            "mean_jct_increase_percent": 100. * (current["mean_jct_s"] / runs[NAMES[1]]["mean_jct_s"] - 1.),
        },
        "measured_frozen_checkout": str(FROZEN), "operator_sha256": sha256_file(Path(__file__)),
        "performance_accepted": False,
    }
    causal.reservation.write_json(output, result)
    print(json.dumps({
        name: {key: run[key] for key in (
            "mean_jct_s", "end_to_end_output_tokens_per_s", "counts", "forecast_gate", "transfer_bytes",
        )} for name, run in runs.items()
    }, indent=2))


if __name__ == "__main__":
    main()
