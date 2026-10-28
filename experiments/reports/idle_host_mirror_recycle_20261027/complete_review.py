"""Review complete matched work and the new Host-mirror backup lifecycle."""

from datetime import datetime, timezone
import gzip
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal

OPS = Path("/home/longhao/experiment/ops")
DIRECTORY = Path(__file__).resolve().parent
FIGURES = ROOT / "experiments/figures/idle_host_mirror_recycle_20261027_r1"
PRIOR_REVIEW = ROOT / "experiments/reports/tool_offload_before_admission_20261027/complete_review.py"
SPEC = importlib.util.spec_from_file_location("pre_admission_review_helpers", PRIOR_REVIEW)
HELPERS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPERS)
REGISTRATIONS = {
    "full": (OPS / "idle_host_mirror_recycle_20261027_r1.registration.json", "full", "Full"),
    "thunderagent_acting_decay": (
        OPS / "v345_locobench_categorized_thunder_decay_20261027.registration.json",
        "thunderagent_acting_decay", "ThunderAgent decay",
    ),
}


def completed_arm(registration, label):
    plan = causal.load(registration)
    causal.comparison.verify_sources(plan)
    if not causal.predecessor_ready({**plan, "after_registration": str(registration)}):
        raise ValueError("complete review requires actual successful owned exit")
    if plan["quality_exclusions"]:
        raise ValueError("complete review may not remove normal workflow tails")
    arm = (Path(plan["run_root"]) / label).resolve()
    summary = causal.load(arm / "replay/summary.json")
    work = causal.validate_result(plan, summary, arm, live_control=True)
    causal.observed_configuration(plan, label, arm, final=True)
    drain = causal.load(arm / "telemetry_drain.json")
    cleanup = causal.load(arm / "owned_process_cleanup.json")
    idle = drain["drained_status"]
    if (
        not drain["previous_zero_snapshot_ts_ms"] < idle["snapshot_ts_ms"]
        or any(idle.get(name) != 0 for name in (
            "pending_request_count", "pending_batch_count", "writer_queue_depth",
            "failed_records", "dropped_records",
        ))
        or idle.get("writer_error") is not None or cleanup["remaining_processes"]
        or any(causal.reservation.alive(identity) for identity in cleanup["identities"])
    ):
        raise ValueError("two fresh idle snapshots or actual process cleanup are missing")
    return plan, arm, summary, work


def main():
    output = DIRECTORY / "complete_review.json"
    if output.exists():
        raise FileExistsError("preserve original complete mirror review")
    plot_state = OPS / "idle_host_mirror_recycle_20261027_r1.plots.state.json"
    if causal.load(plot_state)["status"] != "complete":
        raise ValueError("the complete matched figures are not available")
    figure_path = FIGURES / "evidence.json.gz"
    plotted = json.loads(gzip.decompress(figure_path.read_bytes()))
    inputs = {str(path): sha256_file(path) for path in (plot_state, figure_path)}
    plans, arms, summaries, proofs, metrics, snapshots = {}, {}, {}, {}, {}, {}
    expected, contract = None, None
    for name, (registration, label, figure_label) in REGISTRATIONS.items():
        plan, arm, summary, work = completed_arm(registration, label)
        if expected is not None and expected != work:
            raise ValueError("the comparison changes exact requests, tokens or stopping")
        expected = work
        plans[name], arms[name], summaries[name] = plan, arm, summary
        figure = plotted["arms"][figure_label]
        if (
            figure["code_commit"] != plan["code_commit"]
            or contract is not None and figure["match_contract"] != contract
        ):
            raise ValueError("plotted work/timing/capacity/serving contract differs")
        contract = figure["match_contract"]
        for source, digest in figure["evidence_sha256"].items():
            if sha256_file(Path(source)) != digest:
                raise ValueError("a complete plotted input changed")
            inputs[source] = digest
        HELPERS.verify_seals(figure, inputs, (arm,))
        observed = causal.replay_event_metrics(summary, arm, plan["workflow_ids"])
        if (
            abs(figure["mean_jct_s"] * 1000. - observed["mean_jct_ms"]) > .01
            or abs(figure["makespan_s"] * 1000. - observed["workflow_event_makespan_ms"]) > .01
        ):
            raise ValueError("complete workflow metric clocks disagree")
        pressure = HELPERS.capacity_pressure_watch(causal.load(
            arm / "server/native_telemetry_status.json",
        ))
        reader = HELPERS.SealedRecords()
        prefix = HELPERS.recompute_proxy(reader(arm / "server/eviction_attribution.jsonl"))
        common = pressure["same_session_common_input"]
        if (
            prefix["common_previously_served_input_tokens"] != common["common_tokens"]
            or prefix["missing_previously_served_prefix_proxy_tokens"]
            != common["missing_from_cache_tokens"]
        ):
            raise ValueError("complete native same-session prefix probes disagree")
        HELPERS.verify_seals({"input_jsonl_prefixes": reader.prefixes}, inputs, (arm,))
        metrics[name] = {
            key: figure[key] for key in (
                "mean_jct_s", "makespan_s", "end_to_end_output_tokens_per_s", "decode",
            )
        }
        for key in ("workflow_ttft_ms", "workflow_max_post_first_ready_output_gap_ms"):
            metrics[name][key] = summary["streaming_response_metrics"][key]
        metrics[name].update(capacity_pressure=pressure, same_session_prefix_probe=prefix)
        snapshots[name] = causal.qualified.last_runtime_state(
            arm / "opportunities/admission_opportunities.jsonl",
        )
        proofs[name] = {
            "registration": str(registration), "code_commit": plan["code_commit"],
            "engine_tree": plan["engine_tree"], "exact_completed_requests": len(work),
        }
        for path in (
            registration, registration.with_suffix(".state.json"), arm / "replay/summary.json",
            arm / "telemetry_drain.json", arm / "owned_process_cleanup.json",
            arm / "server/native_telemetry_status.json",
        ):
            inputs[str(path)] = sha256_file(path)
    full_plan, full_arm = plans["full"], arms["full"]
    if (
        not snapshots["full"]["idle_host_mirror_recycle"]
        or snapshots["thunderagent_acting_decay"].get("idle_host_mirror_recycle", False)
        or any(full_plan["reference_environment"].get(name) != "1" for name in (
            "IDLE_HOST_MIRROR_RECYCLE", "BELIEFKV_IDLE_HOST_MIRROR_RECYCLE",
        ))
    ):
        raise ValueError("the candidate or paper control has incorrect mirror enablement")
    supplemental = {}
    for name, source in HELPERS.AUDITORS.items():
        path = DIRECTORY / (name + ".json")
        report = causal.load(path)
        if report.get("auditor_sha256", sha256_file(ROOT / source)) != sha256_file(ROOT / source):
            raise ValueError("a complete audit operator changed")
        for helper, digest in report.get("auditor_source_sha256", {}).items():
            if sha256_file(ROOT / helper) != digest:
                raise ValueError("a complete audit helper changed")
        allowed = tuple(arms.values())
        if name == "complete_jct_paths":
            source_arm = Path(full_plan["source_arm"]).resolve()
            if (
                Path(report["source_evidence"]["source_arm"]).resolve() != source_arm
                or report["source_graph"] != full_plan["source_plan"]
            ):
                raise ValueError("JCT paths use a different registered source graph")
            allowed += (source_arm,)
        HELPERS.verify_seals(report, inputs, allowed)
        if "arms" in report:
            if set(report["arms"]) != {"Full", "ThunderAgent decay"}:
                raise ValueError("a complete audit has a different arm population")
            for figure_label, method in (("Full", "full"), ("ThunderAgent decay", "thunderagent_acting_decay")):
                observed_arm = report["arms"][figure_label].get("arm")
                if observed_arm is not None and Path(observed_arm).resolve() != arms[method]:
                    raise ValueError("a complete audit belongs to a different arm")
                HELPERS.verify_seals(report["arms"][figure_label], inputs, (arms[method],))
        elif "arm" in report and Path(report["arm"]).resolve() != full_arm:
            raise ValueError("a complete audit belongs to a different Full")
        for path_key, digest_key in (("report", "report_sha256"), ("reentries", "reentries_sha256")):
            if path_key in report:
                dependency = DIRECTORY / "complete_tool_reentries.json"
                if (
                    Path(report[path_key]).resolve() != dependency
                    or report[digest_key] != sha256_file(dependency)
                ):
                    raise ValueError("tool wait analysis uses different reentry evidence")
        supplemental[name] = report
        inputs[str(path)] = sha256_file(path)
    for name in ("complete_guard_cases", "complete_eos_trigger_lifecycle"):
        report = supplemental[name]
        if (
            report["registration_sha256"] != sha256_file(REGISTRATIONS["full"][0])
            or report["code_commit"] != full_plan["code_commit"]
            or report["exact_completed_receipts"] != len(expected)
        ):
            raise ValueError("complete EOS evidence belongs to different work")
    guard = supplemental["complete_guard_cases"]["summary"]
    if guard["known_cases"] != 4 or not guard["all_cases_verified"]:
        raise ValueError("not all known tool-EOS counterexamples verify")
    prefetch_path = DIRECTORY / "complete_prefetch.json.gz"
    prefetch = json.loads(gzip.decompress(prefetch_path.read_bytes()))
    if (
        Path(prefetch["arm"]).resolve() != full_arm
        or prefetch["collection_code_commit"] != full_plan["code_commit"]
    ):
        raise ValueError("complete prefetch evidence belongs to a different Full")
    HELPERS.verify_seals(prefetch, inputs, (full_arm,))
    for source, digest in prefetch["analysis_source_sha256"].items():
        if sha256_file(ROOT / source) != digest:
            raise ValueError("prefetch analysis source changed")
    inputs[str(prefetch_path)] = sha256_file(prefetch_path)
    grants = supplemental["complete_grant_service"]
    if (
        grants["report_inputs_sha256"].get(str(prefetch_path)) != sha256_file(prefetch_path)
        or len(grants["programs"]) != grants["summary"]["journal_programs"]
        or any(row["status"] != "service_after_grant_verified" for row in grants["programs"])
    ):
        raise ValueError("complete grants are not bound to exact native service and prefetch evidence")
    mirror_path = DIRECTORY / "complete_mirror_lifecycle.json"
    mirror = causal.load(mirror_path)
    if (
        mirror["registration_sha256"] != sha256_file(REGISTRATIONS["full"][0])
        or mirror["code_commit"] != full_plan["code_commit"]
        or Path(mirror["arm"]).resolve() != full_arm
        or mirror["operator_sha256"] != sha256_file(DIRECTORY / "mirror_lifecycle.py")
    ):
        raise ValueError("mirror lifecycle evidence belongs to a different Full or operator")
    HELPERS.verify_seals(mirror, inputs, (full_arm,))
    counts = snapshots["full"]["counts"]
    if (
        mirror["summary"]["released_host_nodes"] != counts.get("idle_host_mirror_recycle_host_nodes", 0)
        or mirror["summary"]["released_host_bytes"] != counts.get("idle_host_mirror_recycle_host_bytes", 0)
    ):
        raise ValueError("complete mirror-release nodes or bytes disagree with the runtime")
    inputs[str(mirror_path)] = sha256_file(mirror_path)
    unit, remainder = divmod(
        full_plan["source_capacity"]["host_full_bytes"], full_plan["source_capacity"]["host_full_tokens"],
    )
    if remainder or unit != 98304:
        raise ValueError("unexpected complete native FULL geometry")
    early = HELPERS.early_service(full_arm, unit)
    HELPERS.verify_seals(early, inputs, (full_arm,))
    previous_path = PRIOR_REVIEW.with_suffix(".json")
    previous = causal.load(previous_path)
    if (
        previous["requests"] != len(expected)
        or previous["proofs"]["thunderagent_acting_decay"] != proofs["thunderagent_acting_decay"]
    ):
        raise ValueError("the preceding Full used different work or control")
    inputs[str(previous_path)] = sha256_file(previous_path)
    full, control = metrics["full"], metrics["thunderagent_acting_decay"]
    paired = [
        {"workflow_id": wid, "full_jct_s": summaries["full"]["workflow_elapsed_ms"][wid] / 1000.,
         "thunderagent_jct_s": when / 1000.,
         "difference_s": (summaries["full"]["workflow_elapsed_ms"][wid] - when) / 1000.}
        for wid, when in sorted(summaries["thunderagent_acting_decay"]["workflow_elapsed_ms"].items())
    ]
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "complete_matched_host_mirror_full_vs_existing_v345_decay_control",
        "requests": len(expected), "prompt_tokens": sum(row[0] for row in expected.values()),
        "output_tokens": sum(row[1] for row in expected.values()),
        "same_exact_tokens_and_native_stopping_verified": True, "all_normal_tails_retained": True,
        "two_fresh_idle_snapshots_verified": True, "all_owned_processes_exited": True,
        "proofs": proofs, "metrics": metrics, "final_runtime_snapshots": snapshots,
        "mean_jct_reduction_percent": 100. * (1. - full["mean_jct_s"] / control["mean_jct_s"]),
        "end_to_end_throughput_increase_percent": 100. * (
            full["end_to_end_output_tokens_per_s"] / control["end_to_end_output_tokens_per_s"] - 1.
        ),
        "paired_workflow_jct": paired,
        "faster_workflows": sum(row["difference_s"] < 0 for row in paired),
        "slower_workflows": sum(row["difference_s"] > 0 for row in paired),
        "previous_pre_admission_full": previous["metrics"]["full"],
        "transfers": {name: row["transfers"] for name, row in supplemental["complete_restore_churn"]["arms"].items()},
        "prefetch_summary": prefetch["summary"],
        "known_counterexample_summary": guard,
        "all_observed_eos_trigger_summary": supplemental["complete_eos_trigger_lifecycle"]["summary"],
        "grant_service_summary": grants["summary"], "early_offload_service": early,
        "mirror_lifecycle_summary": mirror["summary"],
        "inputs_sha256": inputs, "figures_directory": str(FIGURES),
        "operator_sha256": sha256_file(Path(__file__)),
        "shared_review_helper_sha256": sha256_file(PRIOR_REVIEW),
        "auditor_sources_sha256": {source: sha256_file(ROOT / source) for source in HELPERS.AUDITORS.values()},
        "performance_accepted": False, "repeated_run_acceptance": False,
        "all_baseline_goal_complete": False,
        "limitations": [
            "A backup ACK does not establish HBM release, first use or causal module speedup.",
            "Changed Full configurations are not independent repeated trials.",
            "Fixed-work end-to-end output throughput depends on makespan.",
            "Sampled waits are not expanded or subtracted from complete JCT.",
            "No progressive layer-capacity handoff ran in this experiment.",
        ],
    }
    causal.reservation.write_json(output, result)
    print(json.dumps({name: result[name] for name in (
        "requests", "mean_jct_reduction_percent", "end_to_end_throughput_increase_percent",
        "faster_workflows", "slower_workflows", "mirror_lifecycle_summary",
    )}, indent=2))


if __name__ == "__main__":
    main()
