"""Complete fixed-work review of the empirical tool-CDF conversion repair."""

from collections import Counter
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts.audit_sealed_wait_prefetch import SealedRecords

DIRECTORY = Path(__file__).resolve().parent
OPS = Path("/home/longhao/experiment/ops")
HELPER_PATH = ROOT / "experiments/reports/idle_host_mirror_recycle_20261027/complete_review.py"
SPEC = importlib.util.spec_from_file_location("completed_arm_helpers", HELPER_PATH)
HELPERS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPERS)


def main():
    output = DIRECTORY / "complete_review.json"
    if output.exists():
        raise FileExistsError("preserve original complete repair result")
    registrations = {
        "fixed_full": ("tool_prediction_curve_repair_20261027_r1.registration.json", "full"),
        "previous_full": ("tool_offload_before_admission_20261027_r1.registration.json", "full"),
        "thunderagent_decay": (
            "v345_locobench_categorized_thunder_decay_20261027.registration.json",
            "thunderagent_acting_decay",
        ),
    }
    expected, contract, metrics, inputs = None, None, {}, {}
    for label, (name, arm_label) in registrations.items():
        path = OPS / name
        plan, arm, summary, work = HELPERS.completed_arm(path, arm_label)
        current = {field: plan[field] for field in (
            "source_plan", "token_source_sha256", "source_capacity", "source_server_parameters",
            "workflow_ids", "quality_exclusions", "engine_tree", "tool_timing_overlay",
            "sampling_mode", "stream_response_metrics", "request_timeout_s", "max_running_requests",
        )}
        if expected is not None and (work != expected or current != contract):
            raise ValueError("complete fixed work, timing, engine or serving differs")
        expected, contract = work, current
        observed = causal.replay_event_metrics(summary, arm, plan["workflow_ids"])
        reader, directions = SealedRecords(), {}
        for row in reader(arm / "server/transfer_telemetry.jsonl"):
            if row.get("status") != "completed":
                raise ValueError("complete transfer telemetry has an unfinished operation")
            values = directions.setdefault(row["direction"], Counter())
            values.update(operations=1, actual_bytes=row["actual_bytes"])
        state = causal.qualified.last_runtime_state(arm / "opportunities/admission_opportunities.jsonl")
        releases = Counter()
        for row in reader(arm / "opportunities/admission_opportunities.jsonl"):
            if row.get("event") == "predicted_tool_kv_evicted":
                releases.update(events=1, actual_bytes=row["freed_bytes"])
            elif row.get("event") == "parent_pressure_demoted":
                releases[f"pressure_{row['source']}_bytes"] += row["freed_units"].get("0", 0) * 98304
        HELPERS.HELPERS.verify_seals({"input_jsonl_prefixes": reader.prefixes}, inputs, (arm,))
        throughput = 549671. / (observed["workflow_event_makespan_ms"] / 1000.)
        metrics[label] = {
            "registration": str(path), "code_commit": plan["code_commit"],
            "exact_requests": len(work), "workflow_count": len(plan["workflow_ids"]),
            "mean_jct_s": observed["mean_jct_ms"] / 1000.,
            "makespan_s": observed["workflow_event_makespan_ms"] / 1000.,
            "end_to_end_output_tokens_per_s": throughput,
            "transfers": directions, "tool_release_paths": releases,
            "runtime_counts": state["counts"], "workflow_elapsed_ms": summary["workflow_elapsed_ms"],
        }
        for source in (
            path, path.with_suffix(".state.json"), arm / "replay/summary.json",
            arm / "telemetry_drain.json", arm / "owned_process_cleanup.json",
        ):
            inputs[str(source)] = sha256_file(source)
    fixed = metrics["fixed_full"]
    improvements = {
        label: {
            "mean_jct_reduction_pct": (1. - fixed["mean_jct_s"] / values["mean_jct_s"]) * 100.,
            "output_throughput_increase_pct": (
                fixed["end_to_end_output_tokens_per_s"] / values["end_to_end_output_tokens_per_s"] - 1.
            ) * 100.,
        } for label, values in metrics.items() if label != "fixed_full"
    }
    report = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics, "improvements": improvements, "inputs_sha256": inputs,
        "operator_sha256": sha256_file(Path(__file__)),
        "helper_sha256": sha256_file(HELPER_PATH),
        "all_complete_work_and_normal_tails_retained": True,
        "performance_accepted": False,
        "limitations": [
            "One execution per changed configuration is not a repeated causal trial.",
            "Release counters omit some untagged native migration; they are not saved JCT.",
            "This run does not enable progressive layer handoff.",
        ],
    }
    causal.reservation.write_json(output, report)
    print(json.dumps({"improvements": improvements, "fixed_full": {
        key: fixed[key] for key in (
            "mean_jct_s", "makespan_s", "end_to_end_output_tokens_per_s", "tool_release_paths",
        )
    }}, indent=2))


if __name__ == "__main__":
    main()
