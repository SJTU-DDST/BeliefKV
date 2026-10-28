"""Audit complete grouped Full work and compare it with the reused decay control."""

from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
OPS = ROOT.parent / "ops"
REPORTS = Path(__file__).parent
FULL = OPS / "join_blocked_layer_groups_20261028_r1.registration.json"
CONTROL = OPS / "v345_locobench_categorized_thunder_decay_20261027.registration.json"
OUTPUT = ROOT / "experiments/figures/join_blocked_layer_groups_20261028_r1"

sys.path.insert(0, json.loads(FULL.read_bytes())["execution_checkout"])
from beliefkv.experiments.native_engine_variant import COMMON_TREE, GROUPED_LAYER_TREE
from beliefkv.experiments.source_provenance import sha256_file
from scripts import plot_causal_decode_diagnostics as plotting
from scripts import run_causal_comparison as causal
from scripts.summarize_semantic_h2d_ab import capacity_pressure_watch


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists() or OUTPUT.exists():
        raise FileExistsError("preserve all complete comparison evidence")
    registrations = {
        "Grouped Full": (FULL, "full", GROUPED_LAYER_TREE),
        "ThunderAgent decay": (CONTROL, "thunderagent_acting_decay", COMMON_TREE),
    }
    inputs, arms, metrics, elapsed, proofs = {}, {}, {}, {}, {}
    common, work = None, None
    plotting.MATCH_FIELDS = tuple(field for field in plotting.MATCH_FIELDS if field != "engine_tree")
    for name, (registration, label, tree) in registrations.items():
        plan = causal.load(registration)
        causal.comparison.verify_sources(plan)
        if (
            plan["order"] != [label] or plan["engine_tree"] != tree or plan["quality_exclusions"]
            or not causal.predecessor_ready({**plan, "after_registration": str(registration)})
        ):
            raise ValueError("work is incomplete, excluded, changed or still owns processes")
        contract = {field: plan.get(field) for field in (
            "source_plan", "token_source_sha256", "source_capacity", "source_server_parameters",
            "sampling_mode", "stream_response_metrics",
            "request_timeout_s", "max_running_requests", "workflow_ids",
            "quality_exclusion_details",
        )}
        overlay = plan["tool_timing_overlay"]
        if sha256_file(Path(overlay["path"])) != overlay["sha256"]:
            raise ValueError("tool timing artifact changed")
        contract["tool_timing_overlay"] = {
            key: value for key, value in overlay.items() if key != "path"
        }
        if common is not None and common != contract:
            raise ValueError("work, timing, capacity or serving differs")
        common = contract
        arm = Path(plan["run_root"]) / label
        summary_path = arm / "replay/summary.json"
        summary = causal.load(summary_path)
        actual = causal.validate_result(plan, summary, arm, live_control=True)
        if work is not None and actual != work:
            raise ValueError("exact request work or original native stopping differs")
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
            raise ValueError("two fresh idle snapshots and actual cleanup did not verify")
        events = causal.replay_event_metrics(summary, arm, plan["workflow_ids"])
        plotted = plotting.extract(name, registration, arm, bin_s=60)
        if (
            abs(plotted["mean_jct_s"] * 1000. - events["mean_jct_ms"]) > .01
            or abs(plotted["makespan_s"] * 1000. - events["workflow_event_makespan_ms"]) > .01
        ):
            raise ValueError("plotted workflow clocks differ")
        arms[name] = plotted
        metrics[name] = {field: plotted[field] for field in (
            "mean_jct_s", "makespan_s", "end_to_end_output_tokens_per_s", "decode",
        )}
        metrics[name]["capacity_pressure"] = capacity_pressure_watch(
            causal.load(arm / "server/native_telemetry_status.json"),
        )
        metrics[name]["streaming_response_metrics"] = {
            field: summary["streaming_response_metrics"][field]
            for field in ("workflow_ttft_ms", "workflow_max_post_first_ready_output_gap_ms")
        }
        elapsed[name] = events["workflow_jct_ms"]
        proofs[name] = {
            "registration": str(registration), "code_commit": plan["code_commit"],
            "engine_tree": tree, "exact_requests": len(actual),
            "tool_timing_artifact_location": overlay["path"],
            "tool_timing_sha256": overlay["sha256"],
        }
        for path in (
            registration, registration.with_suffix(".state.json"), summary_path,
            drain_path, cleanup_path, arm / "server/native_telemetry_status.json",
        ):
            inputs[str(path)] = sha256_file(path)
        inputs.update(plotted["evidence_sha256"])
    plotting.require_matching(arms)
    full, control = metrics.values()
    paired = [{
        "workflow_id": identity, "full_jct_s": value / 1000.,
        "control_jct_s": elapsed["ThunderAgent decay"][identity] / 1000.,
        "difference_s": (value - elapsed["ThunderAgent decay"][identity]) / 1000.,
    } for identity, value in sorted(elapsed["Grouped Full"].items())]
    previous = {}
    for name, relative, key in (
        ("Previous joint Full", "host_demand_layer_handoff_20261028/complete_core_review.json", "Joint Full"),
        ("Online-refresh Full", "tool_online_refresh_20261027/complete_core_review.json", "Full"),
    ):
        path = ROOT / "experiments/reports" / relative
        prior = causal.load(path)
        for source, digest in prior["inputs_sha256"].items():
            if sha256_file(Path(source)) != digest:
                raise ValueError("previous complete review source changed")
        if (
            prior["requests"] != len(work)
            or prior["prompt_tokens"] != sum(row[0] for row in work.values())
            or prior["output_tokens"] != sum(row[1] for row in work.values())
        ):
            raise ValueError("previous complete work differs")
        prior_metrics = prior["metrics"][key]
        previous[name] = {
            "metrics": prior_metrics,
            "mean_jct_change_percent": 100. * (full["mean_jct_s"] / prior_metrics["mean_jct_s"] - 1.),
            "output_rate_change_percent": 100. * (
                full["end_to_end_output_tokens_per_s"]
                / prior_metrics["end_to_end_output_tokens_per_s"] - 1.
            ),
        }
        inputs[str(path)] = sha256_file(path)
    OUTPUT.mkdir(parents=True)
    figures = plotting.render(
        arms, OUTPUT,
        title="Same V311 work: grouped Full (8/4 layer engine) vs ThunderAgent decay",
        bin_s=60,
    )
    for name, key in (("time_bins.csv", "bins"), ("geometry.csv", "geometry")):
        plotting.write_csv(OUTPUT / name, [
            {"arm": label, **row} for label, arm in arms.items() for row in arm[key]
        ])
    with (OUTPUT / "evidence.json.gz").open("xb") as destination:
        with gzip.GzipFile(fileobj=destination, mode="wb", filename="", mtime=0) as compressed:
            compressed.write(json.dumps({
                "arms": arms, "figures": figures, "native_engines": {
                    name: row[2] for name, row in registrations.items()
                },
                "isolated_layer_handoff_ablation": False,
                "all_normal_tails_retained": True,
                "actual_partial_time_bins_retained": True,
                "worker_intervals_are_not_pure_cuda_kernels": True,
                "performance_accepted": False,
            }, indent=2, allow_nan=False).encode())
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Complete matched-system comparison with an explicit reviewed engine delta.",
        "requests": len(work), "prompt_tokens": sum(row[0] for row in work.values()),
        "output_tokens": sum(row[1] for row in work.values()),
        "all_normal_tails_retained": True,
        "exact_work_and_native_stopping_verified": True,
        "two_fresh_idle_snapshots_and_actual_owned_exit_verified": True,
        "proofs": proofs, "metrics": metrics, "paired_workflow_jct": paired,
        "faster_workflows": sum(row["difference_s"] < 0 for row in paired),
        "slower_workflows": sum(row["difference_s"] > 0 for row in paired),
        "mean_jct_reduction_percent": 100. * (1. - full["mean_jct_s"] / control["mean_jct_s"]),
        "end_to_end_output_increase_percent": 100. * (
            full["end_to_end_output_tokens_per_s"] / control["end_to_end_output_tokens_per_s"] - 1.
        ),
        "previous_complete_full": previous, "figures": figures, "figure_directory": str(OUTPUT),
        "inputs_sha256": inputs, "operator_sha256": sha256_file(Path(__file__)),
        "isolated_layer_handoff_attribution": False, "performance_accepted": False,
        "limitations": [
            "JOIN admission, idle prepare alignment, grouping and tool prefetch changed together.",
            "The retained V345 control uses the explicitly reviewed common native engine.",
            "One matched run does not establish repeatability or all-baseline superiority.",
        ],
    }
    causal.reservation.write_json(output, result)
    print(json.dumps({field: result[field] for field in (
        "requests", "prompt_tokens", "output_tokens", "mean_jct_reduction_percent",
        "end_to_end_output_increase_percent", "faster_workflows", "slower_workflows",
    )}, indent=2))


if __name__ == "__main__":
    main()
