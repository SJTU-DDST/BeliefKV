"""Seal complete work and headline metrics without claiming causal attribution."""

from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError(output)
    ops = Path("/home/longhao/experiment/ops")
    registrations = {
        "Full": (ops / "tool_online_refresh_20261027_r1.registration.json", "full"),
        "ThunderAgent decay": (
            ops / "v345_locobench_categorized_thunder_decay_20261027.registration.json",
            "thunderagent_acting_decay",
        ),
    }
    figures = ROOT / "experiments/figures/tool_online_refresh_20261027_r1/evidence.json.gz"
    plotted = json.loads(gzip.decompress(figures.read_bytes()))
    metrics, work, contract, inputs = {}, None, None, {str(figures): sha256_file(figures)}
    for name, (registration, label) in registrations.items():
        plan = causal.load(registration)
        causal.comparison.verify_sources(plan)
        if not causal.predecessor_ready({**plan, "after_registration": str(registration)}):
            raise ValueError("complete work requires actual owned exit")
        arm = Path(plan["run_root"]) / label
        summary = causal.load(arm / "replay/summary.json")
        actual_work = causal.validate_result(plan, summary, arm, live_control=True)
        if work is not None and work != actual_work:
            raise ValueError("work differs")
        work = actual_work
        fields = (
            "source_plan", "token_source_sha256", "source_capacity",
            "source_server_parameters", "engine_tree", "tool_timing_overlay",
            "sampling_mode", "stream_response_metrics", "request_timeout_s", "workflow_ids",
        )
        current = {key: plan[key] for key in fields}
        if contract is not None and contract != current:
            raise ValueError("common comparison contract differs")
        contract = current
        if plan["quality_exclusions"]:
            raise ValueError("normal tails were excluded")
        causal.observed_configuration(plan, label, arm, final=True)
        drain = causal.load(arm / "telemetry_drain.json")
        cleanup = causal.load(arm / "owned_process_cleanup.json")
        final = drain["drained_status"]
        if (
            not drain["previous_zero_snapshot_ts_ms"] < final["snapshot_ts_ms"]
            or any(final[key] != 0 for key in (
                "pending_request_count", "pending_batch_count", "writer_queue_depth",
                "failed_records", "dropped_records",
            ))
            or final.get("writer_error") is not None or cleanup["remaining_processes"]
            or any(causal.reservation.alive(row) for row in cleanup["identities"])
        ):
            raise ValueError("native drain or actual cleanup is incomplete")
        plot = plotted["arms"][name]
        mean = statistics.mean(summary["workflow_elapsed_ms"].values()) / 1000.
        if plot["mean_jct_s"] != mean:
            raise ValueError("plotted JCT differs")
        metrics[name] = {key: plot[key] for key in (
            "mean_jct_s", "makespan_s", "end_to_end_output_tokens_per_s", "decode",
        )}
        for path in (
            registration, registration.with_suffix(".state.json"),
            arm / "replay/summary.json", arm / "telemetry_drain.json",
            arm / "owned_process_cleanup.json",
        ):
            inputs[str(path)] = sha256_file(path)
    full, control = metrics.values()
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "complete_core_work_metrics_not_per_module_causal_attribution",
        "requests": len(work), "all_normal_tails_retained": True,
        "prompt_tokens": sum(item[0] for item in work.values()),
        "output_tokens": sum(item[1] for item in work.values()),
        "exact_work_and_native_stopping_verified": True,
        "fresh_idle_drain_and_actual_owned_exit_verified": True,
        "metrics": metrics,
        "mean_jct_reduction_percent": 100. * (1. - full["mean_jct_s"] / control["mean_jct_s"]),
        "end_to_end_output_increase_percent": 100. * (
            full["end_to_end_output_tokens_per_s"] / control["end_to_end_output_tokens_per_s"] - 1.
        ),
        "inputs_sha256": inputs, "operator_sha256": sha256_file(Path(__file__)),
        "performance_accepted": False,
    }
    causal.reservation.write_json(output, result)
    print(json.dumps({key: result[key] for key in (
        "requests", "prompt_tokens", "output_tokens", "mean_jct_reduction_percent",
        "end_to_end_output_increase_percent",
    )}, indent=2))


if __name__ == "__main__":
    main()
