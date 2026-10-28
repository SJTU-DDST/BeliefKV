"""Review the complete diagnostic run without extrapolating CPU samples."""

from collections import Counter
from datetime import datetime, timezone
import gzip
import json
import math
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OPS = ROOT.parent / "ops"
FULL = OPS / "control_plan_diagnostics_20261028_r1.registration.json"
CONTROL = OPS / "v345_locobench_categorized_thunder_decay_20261027.registration.json"
FIGURES = ROOT / "experiments/figures/control_plan_diagnostics_20261028_r1"
sys.path.insert(0, json.loads(FULL.read_bytes())["execution_checkout"])

from beliefkv.experiments.native_engine_variant import COMMON_TREE, SIBLING_CAPACITY_TREE
from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import run_causal_comparison as causal


def profile_review(arm):
    reader, outcomes, checks = SealedRecords(), Counter(), Counter()
    states, last, released, reserved = 0, None, 0, 0
    for row in reader(arm / "opportunities/admission_opportunities.jsonl"):
        if row.get("event") == "agent_join_physical_plan_result":
            if not (
                0 <= row["checks"].get("donors_checked", 0) <= 8
                and 0 <= row["checks"].get("nodes_checked", 0) <= 64
                and row["released_device_tokens"] >= 0 and row["reserved_handoff_tokens"] >= 0
            ):
                raise ValueError("physical attempt violated bounded checks or capacity accounting")
            outcomes[row["outcome"]] += 1
            checks.update(row["checks"])
            released += row["released_device_tokens"]
            reserved += row["reserved_handoff_tokens"]
        elif row.get("event") == "admission_runtime_state":
            states += 1
            if row["physical_disabled"] or row.get("profile_shared_path") is not True:
                raise ValueError("diagnostic runtime was disabled or unprofiled")
            timing = row["shared_path_timing"]
            if (
                timing["schema_version"] != 2 or timing["sample_every"] != 64
                or timing["cpu_clock"] != "thread_time_ns" or not timing["enabled"]
            ):
                raise ValueError("unexpected control sampling contract")
            counts = row["counts"]
            recorded = Counter({
                name.removeprefix("join_physical_plan_outcome_"): value
                for name, value in counts.items()
                if name.startswith("join_physical_plan_outcome_")
            })
            if (
                recorded != outcomes
                or sum(recorded.values()) != counts.get("join_physical_plan_attempts", 0)
                or released != counts.get("join_planned_physical_release_tokens", 0)
                or reserved != counts.get("join_planned_reserved_handoff_tokens", 0)
            ):
                raise ValueError("same-prefix physical outcomes and runtime counters disagree")
            last = row
    if last is None:
        raise ValueError("no diagnostic runtime snapshots")
    timing = last["shared_path_timing"]
    for phase in timing["phases"].values():
        if (
            sum(phase["histogram"]) != phase["count"]
            or not 0 <= phase["thread_cpu_self_ms"] <= phase["thread_cpu_total_ms"]
            or not 0 <= phase["self_ms"] <= phase["total_ms"]
            or not math.isclose(
                phase["thread_cpu_mean_ms"] * phase["count"],
                phase["thread_cpu_total_ms"], abs_tol=1e-6,
            )
        ):
            raise ValueError("invalid measured timing samples")
    for path, seal in reader.prefixes.items():
        if (
            Path(path).stat().st_size != seal["complete_prefix_bytes"]
            or sha256_file(Path(path)) != seal["complete_prefix_sha256"]
        ):
            raise ValueError("diagnostic input is not complete and stable")
    return {
        "runtime_snapshots": states, "last_snapshot_final": last["final"],
        "last_recorded_profile_samples_only": timing,
        "physical_attempt_outcomes": dict(outcomes), "physical_attempt_checks": dict(checks),
        "cumulative_native_release_tokens_not_simultaneous_capacity": released,
        "cumulative_handoff_reserved_tokens_not_free_list_capacity": reserved,
        "input_jsonl_prefixes": reader.prefixes,
        "whole_run_cpu_extrapolated": False, "exposed_gpu_idle_measured": False,
    }


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError("preserve complete review")
    plot_state = causal.load(OPS / "control_plan_diagnostics_20261028_r1.plots_r2.state.json")
    if plot_state["status"] != "complete":
        raise ValueError("complete figures and actual Full exit are required")
    figure_path = FIGURES / "evidence.json.gz"
    plotted = json.loads(gzip.decompress(figure_path.read_bytes()))
    if (
        plotted["native_engines"] != {
            "Full (profiled)": SIBLING_CAPACITY_TREE, "ThunderAgent decay": COMMON_TREE,
        }
        or plotted["profiling_enabled"] != {
            "Full (profiled)": True, "ThunderAgent decay": False,
        }
    ):
        raise ValueError("figure engine/profile identities differ")
    inputs = {str(figure_path): sha256_file(figure_path)}
    metrics, clocks, proofs, work, common = {}, {}, {}, None, None
    for name, registration, label, tree in (
        ("Full (profiled)", FULL, "full", SIBLING_CAPACITY_TREE),
        ("ThunderAgent decay", CONTROL, "thunderagent_acting_decay", COMMON_TREE),
    ):
        plan = causal.load(registration)
        causal.comparison.verify_sources(plan)
        if (
            plan["order"] != [label] or plan["engine_tree"] != tree
            or plan["quality_exclusions"]
            or not causal.predecessor_ready({**plan, "after_registration": str(registration)})
            or plotted["registration_sha256"][str(registration)] != sha256_file(registration)
        ):
            raise ValueError("arm incomplete, changed, excluded or still owns processes")
        contract = {field: plan.get(field) for field in (
            "source_plan", "token_source_sha256", "source_capacity", "source_server_parameters",
            "workflow_ids", "quality_exclusion_details", "sampling_mode",
            "stream_response_metrics", "request_timeout_s", "max_running_requests",
        )}
        overlay = plan["tool_timing_overlay"]
        if sha256_file(Path(overlay["path"])) != overlay["sha256"]:
            raise ValueError("tool timing changed")
        contract["tool_timing_overlay"] = {
            key: value for key, value in overlay.items() if key != "path"
        }
        if common is not None and contract != common:
            raise ValueError("complete work, capacity, timing or stopping contract differs")
        common = contract
        arm = Path(plan["run_root"]) / label
        summary_path = arm / "replay/summary.json"
        summary = causal.load(summary_path)
        actual = causal.validate_result(plan, summary, arm, live_control=True)
        if work is not None and work != actual:
            raise ValueError("exact request work and original stopping differ")
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
            raise ValueError("fresh idle drain or actual owned exit is missing")
        clock = causal.replay_event_metrics(summary, arm, plan["workflow_ids"])
        figure = plotted["arms"][name]
        if (
            abs(clock["mean_jct_ms"] - figure["mean_jct_s"] * 1000.) > .01
            or abs(clock["workflow_event_makespan_ms"] - figure["makespan_s"] * 1000.) > .01
        ):
            raise ValueError("independent workflow clocks and figures differ")
        metrics[name] = {field: figure[field] for field in (
            "mean_jct_s", "makespan_s", "end_to_end_output_tokens_per_s", "decode",
        )}
        clocks[name] = clock["workflow_jct_ms"]
        proofs[name] = {
            "code_commit": plan["code_commit"], "engine_tree": tree,
            "registration": str(registration), "exact_requests": len(actual),
        }
        for source, digest in figure["evidence_sha256"].items():
            if sha256_file(Path(source)) != digest:
                raise ValueError("complete figure input changed")
        inputs.update(figure["evidence_sha256"])
        for path in (registration, registration.with_suffix(".state.json"),
                     summary_path, drain_path, cleanup_path):
            inputs[str(path)] = sha256_file(path)
    full, control = metrics.values()
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Complete diagnostic Full with explicit engine/profiling differences.",
        "requests": len(work), "prompt_tokens": sum(row[0] for row in work.values()),
        "output_tokens": sum(row[1] for row in work.values()),
        "all_normal_tails_retained": True, "exact_work_and_original_stopping_verified": True,
        "two_fresh_idle_snapshots_and_actual_owned_exit_verified": True,
        "metrics": metrics, "proofs": proofs,
        "mean_jct_reduction_percent": 100. * (1. - full["mean_jct_s"] / control["mean_jct_s"]),
        "end_to_end_output_increase_percent": 100. * (
            full["end_to_end_output_tokens_per_s"] / control["end_to_end_output_tokens_per_s"] - 1.
        ),
        "paired_workflow_difference_s": {
            identity: (value - clocks["ThunderAgent decay"][identity]) / 1000.
            for identity, value in sorted(clocks["Full (profiled)"].items())
        },
        "diagnostics": profile_review(Path(causal.load(FULL)["run_root"]) / "full"),
        "figures": plotted["figures"], "inputs_sha256": inputs,
        "operator_sha256": sha256_file(Path(__file__)),
        "performance_accepted": False, "isolated_control_cost_attribution": False,
        "limitations": [
            "The control has a different reviewed engine and no shared-path profiling.",
            "Historical control CPU remains unknown; current samples are not its incremental cost.",
            "Sample totals are not expanded to whole-run CPU or exposed GPU delay.",
            "First service to completion includes interleaving and transfer dependencies.",
            "A single diagnostic run is not a repeatability or all-baseline performance proof.",
        ],
    }
    causal.reservation.write_json(output, result)
    print(json.dumps({
        "requests": result["requests"], "metrics": metrics,
        "physical_attempt_outcomes": result["diagnostics"]["physical_attempt_outcomes"],
    }, indent=2))


if __name__ == "__main__":
    main()
