"""Verify the complete reference-core Full comparison without launching GPUs."""

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
        raise FileExistsError("preserve complete comparison evidence")
    figures = ROOT / "experiments/figures/decode_diagnostics_20261027_v402"
    figure_path = figures / "evidence.json.gz"
    plotted = json.loads(gzip.decompress(figure_path.read_bytes()))
    specs = {
        "reference_full": (
            "/home/longhao/experiment/ops/v400b_locobench_reference_thunder_offload_20261027.registration.json",
            "full", "Full",
        ),
        "thunderagent_acting_decay": (
            "/home/longhao/experiment/ops/v345_locobench_categorized_thunder_decay_20261027.registration.json",
            "thunderagent_acting_decay", "ThunderAgent decay",
        ),
    }
    inputs = {str(figure_path): sha256_file(figure_path)}
    proofs, metrics, lifetimes, expected = {}, {}, {}, None
    contract = None
    for name, (registration, label, figure_label) in specs.items():
        path = Path(registration)
        plan = causal.load(path)
        causal.comparison.verify_sources(plan)
        if not causal.predecessor_ready({**plan, "after_registration": str(path)}):
            raise ValueError("owned experiment processes are still live")
        arm = Path(plan["run_root"]) / label
        summary_path = arm / "replay/summary.json"
        summary = causal.load(summary_path)
        work = causal.validate_result(plan, summary, arm, live_control=True)
        if expected is not None and work != expected:
            raise ValueError("complete request work differs")
        expected = work
        causal.observed_configuration(plan, label, arm, final=True)
        drain_path = arm / "telemetry_drain.json"
        cleanup_path = arm / "owned_process_cleanup.json"
        drain, cleanup = causal.load(drain_path), causal.load(cleanup_path)
        idle = drain["drained_status"]
        if (
            not drain["previous_zero_snapshot_ts_ms"] < idle["snapshot_ts_ms"]
            or any(idle.get(key) != 0 for key in (
                "pending_request_count", "pending_batch_count", "writer_queue_depth",
                "failed_records", "dropped_records",
            ))
            or idle.get("writer_error") is not None
            or cleanup["remaining_processes"]
            or any(causal.reservation.alive(identity) for identity in cleanup["identities"])
        ):
            raise ValueError("fresh idle telemetry or actual cleanup is missing")
        figure = plotted["arms"][figure_label]
        if (
            figure["code_commit"] != plan["code_commit"]
            or contract is not None and contract != figure["match_contract"]
        ):
            raise ValueError("plotted complete comparison contract differs")
        for source, digest in figure["evidence_sha256"].items():
            if sha256_file(Path(source)) != digest:
                raise ValueError("plotted source evidence changed")
            inputs[source] = digest
        for source, prefix in figure["input_jsonl_prefixes"].items():
            data = Path(source)
            if (
                data.stat().st_size != prefix["complete_prefix_bytes"]
                or sha256_file(data) != prefix["complete_prefix_sha256"]
            ):
                raise ValueError("completed plotted JSONL changed")
        contract = figure["match_contract"]
        observed = causal.replay_event_metrics(summary, arm, plan["workflow_ids"])
        if (
            abs(figure["mean_jct_s"] * 1000. - observed["mean_jct_ms"]) > .01
            or abs(figure["makespan_s"] * 1000. - observed["workflow_event_makespan_ms"]) > .01
        ):
            raise ValueError("complete metric boundaries differ")
        final = causal.qualified.last_runtime_state(
            arm / "opportunities/admission_opportunities.jsonl",
        )
        proofs[name] = {
            "registration": registration, "code_commit": plan["code_commit"],
            "engine_tree": plan["engine_tree"], "exact_requests": len(work),
            "normal_tails_retained": not plan["quality_exclusions"],
            "final_agent_admission": final["agent_admission"],
            "final_paper_baseline": final["paper_baseline"],
            "physical_disabled": final["physical_disabled"],
        }
        metrics[name] = {
            key: figure[key] for key in (
                "mean_jct_s", "makespan_s", "end_to_end_output_tokens_per_s", "decode",
            )
        }
        metrics[name]["workflow_ttft_ms"] = summary["streaming_response_metrics"]["workflow_ttft_ms"]
        metrics[name]["workflow_max_post_first_ready_output_gap_ms"] = (
            summary["streaming_response_metrics"]["workflow_max_post_first_ready_output_gap_ms"]
        )
        lifetimes[name] = summary["workflow_elapsed_ms"]
        for source in (
            path, path.with_suffix(".state.json"), summary_path, drain_path, cleanup_path,
            arm / "server/native_telemetry_status.json",
        ):
            inputs[str(source)] = sha256_file(source)
    full, control = metrics["reference_full"], metrics["thunderagent_acting_decay"]
    paired = [{
        "workflow_id": identity,
        "reference_full_jct_s": lifetimes["reference_full"][identity] / 1000.,
        "thunderagent_jct_s": value / 1000.,
        "difference_s": (lifetimes["reference_full"][identity] - value) / 1000.,
    } for identity, value in sorted(lifetimes["thunderagent_acting_decay"].items())]
    restore_path = output.parent / "complete_restore_churn.json"
    paths_path = output.parent / "complete_jct_paths.json"
    restore, paths = causal.load(restore_path), causal.load(paths_path)
    for source in (restore_path, paths_path):
        inputs[str(source)] = sha256_file(source)
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "complete_matched_reference_core_full_vs_existing_acting_decay_control",
        "requests": len(expected),
        "prompt_tokens": sum(row[0] for row in expected.values()),
        "output_tokens": sum(row[1] for row in expected.values()),
        "all_normal_tails_retained": True,
        "same_exact_tokens_and_native_stopping_verified": True,
        "two_fresh_idle_snapshots_verified": True,
        "all_owned_processes_exited": True,
        "proofs": proofs, "metrics": metrics,
        "mean_jct_reduction_percent": 100. * (1. - full["mean_jct_s"] / control["mean_jct_s"]),
        "end_to_end_throughput_increase_percent": 100. * (
            full["end_to_end_output_tokens_per_s"] / control["end_to_end_output_tokens_per_s"] - 1.
        ),
        "paired_workflow_jct": paired,
        "faster_workflows": sum(row["difference_s"] < 0 for row in paired),
        "slower_workflows": sum(row["difference_s"] > 0 for row in paired),
        "restore": {
            name: value["transfers"] for name, value in restore["arms"].items()
        },
        "mean_observed_critical_path_components_s": {
            name: value["mean_components_s"] for name, value in paths["arms"].items()
        },
        "figures_directory": str(figures),
        "inputs_sha256": inputs,
        "operator_sha256": sha256_file(Path(__file__)),
        "performance_accepted": False,
        "repeated_run_acceptance": False,
        "all_baseline_goal_complete": False,
        "limitations": [
            "One complete run per method; a 0.037% output-throughput difference is not a clear gain.",
            "The follow-up consumer-selection repair did not run in frozen V400b.",
            "Critical-path components contain overlapping/interleaved work, not isolated GPU or removable transfer cost.",
            "Sampled layer waits cannot be expanded into whole-run migration overhead.",
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
