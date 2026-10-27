"""Verify complete sibling Full, unchanged work, owned exit and matched figures."""

from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError("preserve complete sibling comparison evidence")
    figures = ROOT / "experiments/figures/reference_join_siblings_20261027_r1_cpu_retry"
    figure_path = figures / "evidence.json.gz"
    plotted = json.loads(gzip.decompress(figure_path.read_bytes()))
    registrations = {
        "full": (
            Path("/home/longhao/experiment/ops/reference_join_siblings_20261027_r1.registration.json"),
            "full", "Full",
        ),
        "thunderagent_acting_decay": (
            Path("/home/longhao/experiment/ops/v345_locobench_categorized_thunder_decay_20261027.registration.json"),
            "thunderagent_acting_decay", "ThunderAgent decay",
        ),
    }
    work, contract = None, None
    metrics, elapsed, proofs, snapshots = {}, {}, {}, {}
    inputs = {str(figure_path): sha256_file(figure_path)}
    for name, (registration, label, figure_label) in registrations.items():
        plan = causal.load(registration)
        causal.comparison.verify_sources(plan)
        if not causal.predecessor_ready({**plan, "after_registration": str(registration)}):
            raise ValueError("complete comparison requires actual owned exit")
        if plan["quality_exclusions"]:
            raise ValueError("normal workflow tails were excluded")
        arm = Path(plan["run_root"]) / label
        summary_path = arm / "replay/summary.json"
        summary = causal.load(summary_path)
        current_work = causal.validate_result(plan, summary, arm, live_control=True)
        if work is not None and current_work != work:
            raise ValueError("comparison changed exact requests, tokens or stopping")
        work = current_work
        causal.observed_configuration(plan, label, arm, final=True)
        drain_path, cleanup_path = arm / "telemetry_drain.json", arm / "owned_process_cleanup.json"
        drain, cleanup = causal.load(drain_path), causal.load(cleanup_path)
        idle = drain["drained_status"]
        if (
            not drain["previous_zero_snapshot_ts_ms"] < idle["snapshot_ts_ms"]
            or any(idle.get(key) != 0 for key in (
                "pending_request_count", "pending_batch_count", "writer_queue_depth",
                "failed_records", "dropped_records",
            ))
            or idle.get("writer_error") is not None or cleanup["remaining_processes"]
            or any(causal.reservation.alive(item) for item in cleanup["identities"])
        ):
            raise ValueError("complete telemetry drain or actual cleanup is missing")
        figure = plotted["arms"][figure_label]
        if (
            figure["code_commit"] != plan["code_commit"]
            or contract is not None and figure["match_contract"] != contract
        ):
            raise ValueError("figure contract changed common work or serving parameters")
        contract = figure["match_contract"]
        for source, digest in figure["evidence_sha256"].items():
            if sha256_file(Path(source)) != digest:
                raise ValueError("plotted input changed")
            inputs[source] = digest
        for source, prefix in figure["input_jsonl_prefixes"].items():
            path = Path(source)
            if (
                path.stat().st_size != prefix["complete_prefix_bytes"]
                or sha256_file(path) != prefix["complete_prefix_sha256"]
            ):
                raise ValueError("completed plotted JSONL changed")
        observed = causal.replay_event_metrics(summary, arm, plan["workflow_ids"])
        if (
            abs(figure["mean_jct_s"] * 1000. - observed["mean_jct_ms"]) > .01
            or abs(figure["makespan_s"] * 1000. - observed["workflow_event_makespan_ms"]) > .01
        ):
            raise ValueError("complete JCT or makespan clocks differ")
        metrics[name] = {
            key: figure[key] for key in (
                "mean_jct_s", "makespan_s", "end_to_end_output_tokens_per_s", "decode",
            )
        }
        for key in ("workflow_ttft_ms", "workflow_max_post_first_ready_output_gap_ms"):
            metrics[name][key] = summary["streaming_response_metrics"][key]
        elapsed[name] = summary["workflow_elapsed_ms"]
        snapshots[name] = causal.qualified.last_runtime_state(
            arm / "opportunities/admission_opportunities.jsonl",
        )
        proofs[name] = {
            "registration": str(registration), "code_commit": plan["code_commit"],
            "engine_tree": plan["engine_tree"], "exact_completed_requests": len(work),
        }
        for path in (
            registration, registration.with_suffix(".state.json"), summary_path,
            drain_path, cleanup_path, arm / "server/native_telemetry_status.json",
        ):
            inputs[str(path)] = sha256_file(path)
    full, control = metrics["full"], metrics["thunderagent_acting_decay"]
    paired = [{
        "workflow_id": identity, "full_jct_s": elapsed["full"][identity] / 1000.,
        "thunderagent_jct_s": when / 1000.,
        "difference_s": (elapsed["full"][identity] - when) / 1000.,
    } for identity, when in sorted(elapsed["thunderagent_acting_decay"].items())]
    supplemental = {}
    for name in ("complete_restore_churn", "complete_jct_paths", "complete_sampled_restore_waits"):
        source = output.parent / f"{name}.json"
        supplemental[name] = causal.load(source)
        inputs[str(source)] = sha256_file(source)
    failure = Path("/home/longhao/experiment/ops/reference_join_siblings_20261027_r1.plots.state.json")
    inputs[str(failure)] = sha256_file(failure)
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "complete_matched_sibling_reference_full_vs_existing_v345_decay_control",
        "requests": len(work), "prompt_tokens": sum(row[0] for row in work.values()),
        "output_tokens": sum(row[1] for row in work.values()),
        "same_exact_tokens_and_native_stopping_verified": True,
        "all_normal_tails_retained": True, "two_fresh_idle_snapshots_verified": True,
        "all_owned_processes_exited": True, "proofs": proofs, "metrics": metrics,
        "mean_jct_reduction_percent": 100. * (1. - full["mean_jct_s"] / control["mean_jct_s"]),
        "end_to_end_throughput_increase_percent": 100. * (
            full["end_to_end_output_tokens_per_s"] / control["end_to_end_output_tokens_per_s"] - 1.
        ),
        "paired_workflow_jct": paired,
        "faster_workflows": sum(row["difference_s"] < 0 for row in paired),
        "slower_workflows": sum(row["difference_s"] > 0 for row in paired),
        "transfers": {
            name: row["transfers"]
            for name, row in supplemental["complete_restore_churn"]["arms"].items()
        },
        "final_runtime_snapshots": snapshots, "figures_directory": str(figures),
        "original_plot_waiter_failure": causal.load(failure),
        "cpu_plot_retry_used_existing_plotting_environment": True,
        "inputs_sha256": inputs, "operator_sha256": sha256_file(Path(__file__)),
        "performance_accepted": False, "repeated_run_acceptance": False,
        "all_baseline_goal_complete": False,
        "limitations": [
            "This is one complete changed candidate, not an independent repeated trial.",
            "Worker intervals include runtime and waiting; fixed-work output throughput is tied to makespan.",
            "Sampled GPU waits are not expanded or allocated to individual requests.",
            "The tool-closer EOS guard was CPU-repaired after frozen 525c7cc6; it did not run here.",
            "The plot waiter failed only for a missing matplotlib interpreter; preserve that failure.",
            "No progressive layer-capacity handoff ran in this experiment.",
        ],
    }
    causal.reservation.write_json(output, result)
    print(json.dumps({
        key: result[key] for key in (
            "requests", "prompt_tokens", "output_tokens", "mean_jct_reduction_percent",
            "end_to_end_throughput_increase_percent", "faster_workflows", "slower_workflows",
        )
    }, indent=2))


if __name__ == "__main__":
    main()
