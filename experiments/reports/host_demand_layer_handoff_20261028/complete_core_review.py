"""Verify complete joint Full work and metrics with the explicit engine delta."""

from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
OPS = ROOT.parent / "ops"
REGISTRATION = OPS / "host_demand_layer_handoff_20261028_r1.registration.json"
FULL_PLAN = json.loads(REGISTRATION.read_bytes())
sys.path.insert(0, FULL_PLAN["execution_checkout"])

from beliefkv.experiments.native_engine_variant import COMMON_TREE, LAYER_TREE
from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts.summarize_semantic_h2d_ab import capacity_pressure_watch


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError(output)
    plot_state = OPS / "host_demand_layer_handoff_20261028_r1.plots.state.json"
    figures = ROOT / "experiments/figures/host_demand_layer_handoff_20261028_r1/evidence.json.gz"
    if causal.load(plot_state)["status"] != "complete":
        raise ValueError("complete figures are missing")
    plotted = json.loads(gzip.decompress(figures.read_bytes()))
    engines = {"Joint Full": LAYER_TREE, "ThunderAgent decay": COMMON_TREE}
    if (
        plotted["native_engines"] != engines
        or plotted["isolated_layer_handoff_ablation"] is not False
    ):
        raise ValueError("undeclared native engine comparison")
    registrations = {
        "Joint Full": (REGISTRATION, "full"),
        "ThunderAgent decay": (
            OPS / "v345_locobench_categorized_thunder_decay_20261027.registration.json",
            "thunderagent_acting_decay",
        ),
    }
    inputs = {str(path): sha256_file(path) for path in (figures, plot_state)}
    common, work = None, None
    metrics, proofs, elapsed, snapshots = {}, {}, {}, {}
    for name, (registration, label) in registrations.items():
        plan = causal.load(registration)
        causal.comparison.verify_sources(plan)
        if (
            plan["engine_tree"] != engines[name] or plan["quality_exclusions"]
            or not causal.predecessor_ready({**plan, "after_registration": str(registration)})
        ):
            raise ValueError("changed engine, excluded tail or still-owned process")
        contract = {field: plan.get(field) for field in (
            "source_plan", "token_source_sha256", "source_capacity", "source_server_parameters",
            "tool_timing_overlay", "sampling_mode", "stream_response_metrics",
            "request_timeout_s", "max_running_requests", "workflow_ids",
            "quality_exclusion_details",
        )}
        if common is not None and common != contract:
            raise ValueError("common work/timing/capacity/serving differs")
        common = contract
        arm = Path(plan["run_root"]) / label
        summary_path = arm / "replay/summary.json"
        summary = causal.load(summary_path)
        actual = causal.validate_result(plan, summary, arm, live_control=True)
        if work is not None and actual != work:
            raise ValueError("exact request tokens or native stopping differs")
        work = actual
        causal.observed_configuration(plan, label, arm, final=True)
        drain_path, cleanup_path = arm / "telemetry_drain.json", arm / "owned_process_cleanup.json"
        drain, cleanup = causal.load(drain_path), causal.load(cleanup_path)
        idle = drain["drained_status"]
        if (
            not drain["previous_zero_snapshot_ts_ms"] < idle["snapshot_ts_ms"]
            or any(idle.get(field) != 0 for field in (
                "pending_request_count", "pending_batch_count", "writer_queue_depth",
                "failed_records", "dropped_records",
            ))
            or idle.get("writer_error") is not None or cleanup["remaining_processes"]
            or any(causal.reservation.alive(row) for row in cleanup["identities"])
        ):
            raise ValueError("native drain or actual owned cleanup is incomplete")
        events = causal.replay_event_metrics(summary, arm, plan["workflow_ids"])
        plot = plotted["arms"][name]
        if (
            plot["code_commit"] != plan["code_commit"]
            or abs(plot["mean_jct_s"] * 1000. - events["mean_jct_ms"]) > .01
            or abs(plot["makespan_s"] * 1000. - events["workflow_event_makespan_ms"]) > .01
        ):
            raise ValueError("plotted code or workflow clock differs")
        for source, digest in plot["evidence_sha256"].items():
            if sha256_file(Path(source)) != digest:
                raise ValueError("plotted evidence changed")
            inputs[source] = digest
        metrics[name] = {field: plot[field] for field in (
            "mean_jct_s", "makespan_s", "end_to_end_output_tokens_per_s", "decode",
        )}
        metrics[name]["capacity_pressure"] = capacity_pressure_watch(
            causal.load(arm / "server/native_telemetry_status.json"),
        )
        metrics[name]["streaming_response_metrics"] = {
            field: summary["streaming_response_metrics"][field] for field in (
                "workflow_ttft_ms", "workflow_max_post_first_ready_output_gap_ms",
            )
        }
        elapsed[name] = events["workflow_jct_ms"]
        snapshots[name] = causal.qualified.last_runtime_state(
            arm / "opportunities/admission_opportunities.jsonl",
        )
        proofs[name] = {
            "registration": str(registration), "code_commit": plan["code_commit"],
            "engine_tree": plan["engine_tree"], "exact_requests": len(work),
        }
        for path in (
            registration, registration.with_suffix(".state.json"), summary_path,
            drain_path, cleanup_path, arm / "server/native_telemetry_status.json",
        ):
            inputs[str(path)] = sha256_file(path)
    previous_path = ROOT / "experiments/reports/tool_online_refresh_20261027/complete_core_review.json"
    previous = causal.load(previous_path)
    if previous["metrics"]["ThunderAgent decay"] != {
        key: metrics["ThunderAgent decay"][key] for key in previous["metrics"]["ThunderAgent decay"]
    }:
        raise ValueError("previous control changed")
    inputs[str(previous_path)] = sha256_file(previous_path)
    full, control = metrics.values()
    prior = previous["metrics"]["Full"]
    paired = [{
        "workflow_id": identity, "full_jct_s": value / 1000.,
        "control_jct_s": elapsed["ThunderAgent decay"][identity] / 1000.,
        "difference_s": (value - elapsed["ThunderAgent decay"][identity]) / 1000.,
    } for identity, value in sorted(elapsed["Joint Full"].items())]
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "complete_joint_system_comparison_with_declared_engine_delta",
        "requests": len(work), "prompt_tokens": sum(row[0] for row in work.values()),
        "output_tokens": sum(row[1] for row in work.values()),
        "all_normal_tails_retained": True, "exact_work_and_native_stopping_verified": True,
        "two_fresh_idle_snapshots_and_actual_owned_exit_verified": True,
        "proofs": proofs, "metrics": metrics,
        "mean_jct_reduction_percent": 100. * (1. - full["mean_jct_s"] / control["mean_jct_s"]),
        "end_to_end_output_increase_percent": 100. * (
            full["end_to_end_output_tokens_per_s"] / control["end_to_end_output_tokens_per_s"] - 1.
        ),
        "previous_online_refresh_full": {
            "metrics": prior,
            "mean_jct_increase_percent": 100. * (full["mean_jct_s"] / prior["mean_jct_s"] - 1.),
            "output_rate_change_percent": 100. * (
                full["end_to_end_output_tokens_per_s"] / prior["end_to_end_output_tokens_per_s"] - 1.
            ),
        },
        "paired_workflow_jct": paired,
        "faster_workflows": sum(row["difference_s"] < 0 for row in paired),
        "slower_workflows": sum(row["difference_s"] > 0 for row in paired),
        "last_recorded_runtime_snapshots_not_shutdown_final": snapshots,
        "inputs_sha256": inputs, "operator_sha256": sha256_file(Path(__file__)),
        "isolated_layer_handoff_attribution": False,
        "performance_accepted": False,
    }
    causal.reservation.write_json(output, result)
    print(json.dumps({key: result[key] for key in (
        "requests", "prompt_tokens", "output_tokens", "mean_jct_reduction_percent",
        "end_to_end_output_increase_percent", "faster_workflows", "slower_workflows",
    )}, indent=2))


if __name__ == "__main__":
    main()
