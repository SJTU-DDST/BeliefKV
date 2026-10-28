"""Review the complete pressure-index Full without converting CPU costs to JCT."""

from collections import Counter
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OPS = ROOT.parent / "ops"
FULL = OPS / "full_only_pressure_index_20261028_r1.registration.json"
CONTROL = OPS / "v345_locobench_categorized_thunder_decay_20261027.registration.json"
FIGURES = ROOT / "experiments/figures/full_only_pressure_index_20261028_r1"
sys.path.insert(0, json.loads(FULL.read_bytes())["execution_checkout"])

from beliefkv.experiments.native_engine_variant import COMMON_TREE, SIBLING_CAPACITY_TREE
from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import run_causal_comparison as causal


def completed(plan, registration, label, tree):
    if not causal.predecessor_ready({**plan, "after_registration": str(registration)}):
        raise ValueError("require complete successful work and actual owned process exit")
    causal.comparison.verify_sources(plan)
    if plan["order"] != [label] or plan["engine_tree"] != tree or plan["quality_exclusions"]:
        raise ValueError("arm order, native engine or complete population differs")


def physical_review(rows):
    outcomes, checks = Counter(), Counter()
    released, reserved, snapshots, last = 0, 0, 0, None
    release_rows, journal_released, journal_reserved = 0, 0, 0
    for row in rows:
        event = row.get("event")
        if event == "agent_join_physical_plan_result":
            if not (
                0 <= row["checks"].get("donors_checked", 0) <= 8
                and 0 <= row["checks"].get("nodes_checked", 0) <= 64
                and row["released_device_tokens"] >= 0
                and row["reserved_handoff_tokens"] >= 0
            ):
                raise ValueError("physical attempt violated bounded native accounting")
            outcomes[row["outcome"]] += 1
            checks.update(row["checks"])
            released += row["released_device_tokens"]
            reserved += row["reserved_handoff_tokens"]
        elif event == "agent_join_planned_physical_release":
            release_rows += 1
            journal_released += sum(item["released_tokens"] for item in row["donors"])
            journal_reserved += sum(
                item["reserved_tokens"] for item in row["reserved_handoff_donors"]
            )
        elif event == "admission_runtime_state":
            snapshots += 1
            if (
                row["physical_disabled"] or row.get("profile_shared_path") is not False
                or row["tool_prefetch"] or row["tool_prefetch_priority_ms"] != 0.
                or row["execution_frontier_restore"]
                or row["shared_path_timing"]["enabled"]
            ):
                raise ValueError("runtime violated the no-profile/no-tool-prefetch contract")
            counts = row["counts"]
            observed = Counter({
                name.removeprefix("join_physical_plan_outcome_"): value
                for name, value in counts.items()
                if name.startswith("join_physical_plan_outcome_")
            })
            recorded_checks = Counter({
                name.removeprefix("join_physical_plan_"): value
                for name, value in counts.items()
                if name.startswith("join_physical_plan_")
                and name != "join_physical_plan_attempts"
                and not name.startswith("join_physical_plan_outcome_")
            })
            if (
                observed != outcomes or recorded_checks != checks
                or sum(observed.values()) != counts.get("join_physical_plan_attempts", 0)
                or released != counts.get("join_planned_physical_release_tokens", 0)
                or reserved != counts.get("join_planned_reserved_handoff_tokens", 0)
            ):
                raise ValueError("same-prefix physical events and runtime counters differ")
            last = row
    if last is None:
        raise ValueError("no healthy runtime snapshots")
    return {
        "runtime_snapshots": snapshots,
        "last_snapshot_final": last["final"],
        "physical_attempt_outcomes": dict(outcomes),
        "physical_attempt_checks": dict(checks),
        "cumulative_native_release_tokens_not_simultaneous_capacity": released,
        "cumulative_handoff_reserved_tokens_not_free_list_capacity": reserved,
        "physical_release_journal_events": release_rows,
        "journal_native_release_tokens": journal_released,
        "journal_reserved_handoff_tokens": journal_reserved,
        "journal_release_totals_reconciled": (
            released == journal_released and reserved == journal_reserved
        ),
        "last_recorded_counts_not_shutdown_final": last["counts"],
        "whole_run_control_cpu_measured": False,
        "exposed_gpu_idle_measured": False,
    }


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError("preserve original complete review")
    specs = (
        ("Full (pressure index)", FULL, "full", SIBLING_CAPACITY_TREE),
        ("ThunderAgent decay", CONTROL, "thunderagent_acting_decay", COMMON_TREE),
    )
    plans = {name: causal.load(path) for name, path, _, _ in specs}
    for name, path, label, tree in specs:
        completed(plans[name], path, label, tree)
    plot_state = causal.load(OPS / "full_only_pressure_index_20261028_r1.plots.state.json")
    if plot_state["status"] != "complete":
        raise ValueError("complete matched figures are required")
    figure_path = FIGURES / "evidence.json.gz"
    plotted = json.loads(gzip.decompress(figure_path.read_bytes()))
    if (
        plotted["native_engines"] != {name: tree for name, _, _, tree in specs}
        or plotted["profiling_enabled"] != {name: False for name, _, _, _ in specs}
    ):
        raise ValueError("figure native-engine or profiling identities differ")
    inputs = {str(figure_path): sha256_file(figure_path)}
    metrics, clocks, proofs, work, common = {}, {}, {}, None, None
    for name, registration, label, tree in specs:
        plan = plans[name]
        if plotted["registration_sha256"][str(registration)] != sha256_file(registration):
            raise ValueError("figure registration changed")
        contract = {field: plan.get(field) for field in (
            "source_plan", "token_source_sha256", "source_capacity", "source_server_parameters",
            "workflow_ids", "quality_exclusion_details", "sampling_mode",
            "stream_response_metrics", "request_timeout_s", "max_running_requests",
        )}
        overlay = plan["tool_timing_overlay"]
        if sha256_file(Path(overlay["path"])) != overlay["sha256"]:
            raise ValueError("tool timing artifact changed")
        contract["tool_timing_overlay"] = {
            key: value for key, value in overlay.items() if key != "path"
        }
        if common is not None and contract != common:
            raise ValueError("work, capacity, timing or stopping contract differs")
        common = contract
        arm = Path(plan["run_root"]) / label
        summary_path = arm / "replay/summary.json"
        summary = causal.load(summary_path)
        actual = causal.validate_result(plan, summary, arm, live_control=True)
        if work is not None and actual != work:
            raise ValueError("exact request tokens or original stopping differ")
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
            raise ValueError("fresh idle drain or actual owned exit is unverified")
        clock = causal.replay_event_metrics(summary, arm, plan["workflow_ids"])
        figure = plotted["arms"][name]
        if (
            abs(clock["mean_jct_ms"] - figure["mean_jct_s"] * 1000.) > .01
            or abs(clock["workflow_event_makespan_ms"] - figure["makespan_s"] * 1000.) > .01
        ):
            raise ValueError("independent workflow clocks and complete figures differ")
        metrics[name] = {field: figure[field] for field in (
            "mean_jct_s", "makespan_s", "end_to_end_output_tokens_per_s", "decode",
        )}
        metrics[name]["streaming_response_metrics"] = {
            field: summary["streaming_response_metrics"][field] for field in (
                "workflow_ttft_ms", "workflow_max_post_first_ready_output_gap_ms",
            )
        }
        clocks[name] = clock["workflow_jct_ms"]
        proofs[name] = {
            "registration": str(registration), "code_commit": plan["code_commit"],
            "engine_tree": tree, "exact_requests": len(actual),
        }
        for source, digest in figure["evidence_sha256"].items():
            if sha256_file(Path(source)) != digest:
                raise ValueError("completed figure source changed")
        inputs.update(figure["evidence_sha256"])
        for path in (registration, registration.with_suffix(".state.json"),
                     summary_path, drain_path, cleanup_path):
            inputs[str(path)] = sha256_file(path)
    reader = SealedRecords()
    arm = Path(plans["Full (pressure index)"]["run_root"]) / "full"
    physical = physical_review(reader(arm / "opportunities/admission_opportunities.jsonl"))
    if not physical["journal_release_totals_reconciled"]:
        raise ValueError("complete physical result and release journals differ")
    for path, seal in reader.prefixes.items():
        if Path(path).stat().st_size != seal["complete_prefix_bytes"] or (
            sha256_file(Path(path)) != seal["complete_prefix_sha256"]
        ):
            raise ValueError("physical-plan inputs are not complete and stable")
        inputs[path] = seal["complete_prefix_sha256"]
    for path in (HERE / "model_review.json", HERE / "live_start.json"):
        proof = causal.load(path)
        for source, digest in proof["inputs_sha256"].items():
            if sha256_file(Path(source)) != digest:
                raise ValueError("qualified model or startup source changed")
        inputs[str(path)] = sha256_file(path)
    full, control = metrics.values()
    paired = {
        identity: (value - clocks["ThunderAgent decay"][identity]) / 1000.
        for identity, value in sorted(clocks["Full (pressure index)"].items())
    }
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Complete combined-system comparison with an explicit native-engine difference.",
        "requests": len(work), "prompt_tokens": sum(row[0] for row in work.values()),
        "output_tokens": sum(row[1] for row in work.values()),
        "all_normal_tails_retained": True, "exact_work_and_original_stopping_verified": True,
        "two_fresh_idle_snapshots_and_actual_owned_exit_verified": True,
        "metrics": metrics, "proofs": proofs, "physical_plan_review": physical,
        "paired_workflow_difference_s": paired,
        "faster_workflows": sum(value < 0 for value in paired.values()),
        "slower_workflows": sum(value > 0 for value in paired.values()),
        "mean_jct_reduction_percent": 100. * (1. - full["mean_jct_s"] / control["mean_jct_s"]),
        "end_to_end_output_increase_percent": 100. * (
            full["end_to_end_output_tokens_per_s"] / control["end_to_end_output_tokens_per_s"] - 1.
        ),
        "figures": plotted["figures"], "inputs_sha256": inputs,
        "operator_sha256": sha256_file(Path(__file__)),
        "performance_accepted": False, "isolated_module_attribution": False,
        "limitations": [
            "Ancestor reclaim and sparse publication changed together; this is not an isolated ablation.",
            "The reused control has a different reviewed engine; both have control profiling disabled.",
            "Cumulative released tokens are not simultaneous capacity or consumer-allocation proof.",
            "Local CPU savings cannot be converted to whole-run CPU or exposed GPU/JCT delay.",
            "A single complete run does not establish repeated or all-baseline superiority.",
        ],
    }
    causal.reservation.write_json(output, result)
    print(json.dumps({key: result[key] for key in (
        "requests", "mean_jct_reduction_percent", "end_to_end_output_increase_percent",
        "faster_workflows", "slower_workflows", "physical_plan_review",
    )}, indent=2))


if __name__ == "__main__":
    main()
