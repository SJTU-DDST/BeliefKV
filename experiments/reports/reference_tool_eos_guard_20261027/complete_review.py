"""Review complete EOS-guard Full against the existing matched decay control."""

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
        raise FileExistsError("preserve complete EOS-guard comparison evidence")
    directory = output.parent
    figures = ROOT / "experiments/figures/reference_tool_eos_guard_20261027_r1"
    figure_path = figures / "evidence.json.gz"
    plot_state = Path(
        "/home/longhao/experiment/ops/reference_tool_eos_guard_20261027_r1.plots.state.json"
    )
    if causal.load(plot_state).get("status") != "complete":
        raise ValueError("complete matched figures are not available")
    plotted = json.loads(gzip.decompress(figure_path.read_bytes()))
    registrations = {
        "full": (
            Path("/home/longhao/experiment/ops/reference_tool_eos_guard_20261027_r1.registration.json"),
            "full", "Full",
        ),
        "thunderagent_acting_decay": (
            Path("/home/longhao/experiment/ops/v345_locobench_categorized_thunder_decay_20261027.registration.json"),
            "thunderagent_acting_decay", "ThunderAgent decay",
        ),
    }
    work, contract = None, None
    metrics, elapsed, proofs, snapshots = {}, {}, {}, {}
    inputs = {
        str(path): sha256_file(path) for path in (figure_path, plot_state)
    }
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
    guard_path = directory / "complete_guard_cases.json"
    guard = causal.load(guard_path)
    full_registration = registrations["full"][0]
    if (
        guard.get("registration_sha256") != sha256_file(full_registration)
        or guard.get("exact_completed_receipts") != len(work)
        or guard.get("summary", {}).get("known_cases") != 4
        or guard.get("summary", {}).get("all_cases_verified") is not True
    ):
        raise ValueError("complete known-counterexample evidence is missing")
    for source, prefix in guard["input_jsonl_prefixes"].items():
        if (
            Path(source).stat().st_size != prefix["complete_prefix_bytes"]
            or sha256_file(Path(source)) != prefix["complete_prefix_sha256"]
        ):
            raise ValueError("guard cases were not audited on complete immutable input")
    inputs[str(guard_path)] = sha256_file(guard_path)
    lifecycle_path = directory / "complete_eos_trigger_lifecycle.json"
    lifecycle = causal.load(lifecycle_path)
    if (
        lifecycle.get("registration_sha256") != sha256_file(full_registration)
        or lifecycle.get("code_commit") != proofs["full"]["code_commit"]
        or lifecycle.get("exact_completed_receipts") != len(work)
    ):
        raise ValueError("complete EOS lifecycle evidence belongs to different work")
    for source, prefix in lifecycle["input_jsonl_prefixes"].items():
        if (
            Path(source).stat().st_size != prefix["complete_prefix_bytes"]
            or sha256_file(Path(source)) != prefix["complete_prefix_sha256"]
        ):
            raise ValueError("EOS lifecycle evidence is not a complete immutable input")
    inputs[str(lifecycle_path)] = sha256_file(lifecycle_path)
    full, control = metrics["full"], metrics["thunderagent_acting_decay"]
    paired = [{
        "workflow_id": identity, "full_jct_s": elapsed["full"][identity] / 1000.,
        "thunderagent_jct_s": when / 1000.,
        "difference_s": (elapsed["full"][identity] - when) / 1000.,
    } for identity, when in sorted(elapsed["thunderagent_acting_decay"].items())]
    supplemental = {}
    for name in (
        "complete_restore_churn", "complete_jct_paths", "complete_sampled_restore_waits",
        "complete_grant_service",
    ):
        source = directory / f"{name}.json"
        supplemental[name] = causal.load(source)
        inputs[str(source)] = sha256_file(source)
    prefetch_path = directory / "complete_prefetch.json.gz"
    prefetch = json.loads(gzip.decompress(prefetch_path.read_bytes()))
    full_plan = causal.load(full_registration)
    if (
        Path(prefetch["arm"]).resolve() != (Path(full_plan["run_root"]) / "full").resolve()
        or prefetch["collection_code_commit"] != full_plan["code_commit"]
    ):
        raise ValueError("prefetch audit belongs to another frozen arm")
    for source, prefix in prefetch["input_jsonl_prefixes"].items():
        if (
            Path(source).stat().st_size != prefix["complete_prefix_bytes"]
            or sha256_file(Path(source)) != prefix["complete_prefix_sha256"]
        ):
            raise ValueError("prefetch audit does not seal the complete immutable input")
    inputs[str(prefetch_path)] = sha256_file(prefetch_path)
    prior_path = ROOT / "experiments/reports/reference_join_siblings_20261027/complete_review.json"
    prior = causal.load(prior_path)
    inputs[str(prior_path)] = sha256_file(prior_path)
    if (
        prior["requests"] != len(work)
        or prior["proofs"]["thunderagent_acting_decay"] != proofs["thunderagent_acting_decay"]
    ):
        raise ValueError("previous sibling comparison used different work or control")
    previous_full = prior["metrics"]["full"]
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "complete_matched_eos_guard_full_vs_existing_v345_decay_control",
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
        "previous_sibling_full": {
            "metrics": previous_full,
            "mean_jct_reduction_percent": 100. * (
                1. - full["mean_jct_s"] / previous_full["mean_jct_s"]
            ),
            "end_to_end_throughput_increase_percent": 100. * (
                full["end_to_end_output_tokens_per_s"]
                / previous_full["end_to_end_output_tokens_per_s"] - 1.
            ),
        },
        "transfers": {
            name: row["transfers"]
            for name, row in supplemental["complete_restore_churn"]["arms"].items()
        },
        "known_counterexample_summary": guard["summary"],
        "all_observed_eos_trigger_summary": lifecycle["summary"],
        "prefetch_summary": {
            "copies_by_source": {
                name: {
                    key: row[key] for key in (
                        "issued_commands", "acknowledged_commands", "actual_bytes",
                        "full_verified_reused_bytes_known", "full_identity_not_verified_bytes_known",
                    )
                }
                for name, row in prefetch["summary"]["by_source"].items()
            },
            "wait_events_by_source": {
                name: {
                    key: row[key] for key in (
                        "observed_events", "events_with_observed_full_plan",
                        "events_with_early_reused_full", "early_started_and_reused_full_bytes",
                        "completion_boundary_unknown_events",
                    )
                }
                for name, row in prefetch["wait_event_attribution"]["summary"]["by_source"].items()
            },
        },
        "grant_service_summary": supplemental["complete_grant_service"]["summary"],
        "final_runtime_snapshots": snapshots, "figures_directory": str(figures),
        "inputs_sha256": inputs, "operator_sha256": sha256_file(Path(__file__)),
        "performance_accepted": False, "repeated_run_acceptance": False,
        "all_baseline_goal_complete": False,
        "limitations": [
            "A known-case guard repair is not proof that every tool format is recognized.",
            "This changed candidate and earlier Full configurations are not independent repeated trials.",
            "Worker intervals include runtime and waiting; fixed-work output throughput is tied to makespan.",
            "Sampled GPU waits are not expanded or allocated to individual requests.",
            "Verified copied/reused bytes do not establish hidden GPU time or removable JCT.",
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
