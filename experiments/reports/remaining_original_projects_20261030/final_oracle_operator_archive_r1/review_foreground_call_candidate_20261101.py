"""Compare complete fixed work and verify actual CALL cold-release events."""

from datetime import datetime, timezone
import argparse
import os
from pathlib import Path
import sys

OPS = Path("/home/longhao/experiment/ops")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("checkout", "control", "candidate", "control-review", "candidate-review", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.checkout))
    from beliefkv.experiments.source_provenance import sha256_file
    from scripts import run_causal_comparison as causal
    from scripts.audit_sealed_wait_prefetch import SealedRecords
    from scripts.review_predictive_thunder_cohort import TRAFFIC_FIELDS
    from scripts.review_sustained_oracle import compare_cohorts
    from scripts.train_workflow_work import write
    from scripts.verify_measured_execution import review_matches_execution, verify_execution
    from audit_foreground_call_capacity_20261101 import inspect

    if args.output.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve comparisons and use hidden-CUDA nice19")
    plans = [causal.load(path) for path in (args.control, args.candidate)]
    if any(plan["order"] != ["full"] for plan in plans) or any(
        plans[0][name] != plans[1].get(name) for name in TRAFFIC_FIELDS
    ):
        raise ValueError("the CALL-only pair changed traffic or serving resources")
    configs = [causal.load(Path(plan["experiment_configuration"])) for plan in plans]
    if configs[0]["common_environment"] != configs[1]["common_environment"] or (
        configs[1]["candidate_provenance"]["kind"] != "observed_foreground_CALL_cold_owner_coverage_repair"
    ):
        raise ValueError("the CALL-only pair changed scheduling parameters")
    executions = [verify_execution(path, args.cohort) for path in (args.control, args.candidate)]
    if executions[0]["work"] != executions[1]["work"]:
        raise ValueError("complete measured work differs")
    cohorts, capacities, inputs = [], [], [args.control, args.candidate, args.cohort, Path(__file__)]
    reader = SealedRecords()
    prefix_loss = []
    measured = set(causal.load(args.cohort)["measured_workflow_ids"])
    for plan, directory, execution in zip(
        plans, (args.control_review, args.candidate_review), executions, strict=True,
    ):
        causal.comparison.verify_sources(plan)
        review_path, cohort_path = directory / "review.json", directory / "cohort.json"
        review, cohort = causal.load(review_path), causal.load(cohort_path)
        review_matches_execution(review, execution)
        if cohort["cohort"] != causal.load(args.cohort):
            raise ValueError("fixed cohort changed")
        for filename, prefix in cohort["input_jsonl_prefixes"].items():
            path = Path(filename)
            if path.stat().st_size != prefix["complete_prefix_bytes"] or (
                sha256_file(path) != prefix["complete_prefix_sha256"]
            ):
                raise ValueError("completed cohort prefix changed")
        arm = Path(plan["run_root"]) / "full"
        capacity_path = arm / "server/native_capacity_census.json"
        capacities.append(causal.load(capacity_path)["capacity"])
        loss = sum(
            row["previously_served_prefix_recompute_proxy_tokens"]
            for row in reader(arm / "server/eviction_attribution.jsonl")
            if row.get("event") == "context_prefix_reuse_probe"
            and row["workflow_id"] in measured
        )
        prefix_loss.append(loss)
        cohorts.append(cohort)
        inputs.extend((review_path, cohort_path, capacity_path, Path(plan["experiment_configuration"])))
    if capacities[0] != capacities[1]:
        raise ValueError("actual KV capacities differ")
    calls = inspect(args.candidate, args.cohort)
    write(args.output.parent / "candidate_CALL_audit.json", calls)
    arm = Path(plans[1]["run_root"]) / "full"
    receipts = {
        row["request_id"]: row["native_receipt"]
        for row in reader(arm / "replay/native_requests.jsonl")
    }
    by_owner = {}
    for case in calls["cases"]:
        rid = case["prior_caller_result"]["attributes"]["request_id"]
        receipt = receipts[rid]
        by_owner.setdefault((receipt["session_id"], receipt["session_generation"]), []).append(case)
    releases, offset = [], None
    for row in reader(arm / "opportunities/admission_opportunities.jsonl"):
        if row.get("event") == "admission_runtime_state":
            offset = row["ts_ms"] - row["monotonic_ms"]
        elif row.get("event") == "parent_pressure_demoted" and row.get("source") == "child_wait":
            releases.append(row)
        elif row.get("event") in (
            "critical_parent_headroom", "wait_prefetch_capacity_reclaimed", "execution_handoff_capacity_reclaimed",
        ):
            for victim in row["victims"]:
                if victim["scope"] != "causal_waiting_full":
                    continue
                cases = [
                    case for owner in victim.get("full_session_owners", ())
                    for case in by_owner.get(tuple(owner), ())
                ]
                if not cases:
                    continue
                matched = []
                for case in cases:
                    if offset is not None and case["call_event"]["ts_ms"] + 50 < row["ts_ms"] - offset < case["return_event"]["ts_ms"] - 50:
                        matched.append(case["child_id"])
                if matched:
                    releases.append({
                        "event": row["event"], "ts_ms": row["ts_ms"], "victim": victim,
                        "CALL_child_ids": sorted(set(matched)),
                        "beneficiary_context_id": row.get("context_id"),
                        "beneficiary_workflow_id": row.get("workflow_id"),
                    })
    result = compare_cohorts(
        *cohorts, minimum_jct_gain=.03, minimum_throughput_fraction=.98,
    )
    for group in result["groups"].values():
        group["candidate_JCT_ms"] = group.pop("oracle_JCT_ms")
        group["candidate_completed_workflows"] = group.pop("oracle_completed_workflows")
        for row in group["workflows"]:
            row["candidate_JCT_ms"] = row.pop("oracle_JCT_ms")
    result["common_window_output_throughput"]["candidate_tokens_per_second"] = (
        result["common_window_output_throughput"].pop("oracle_tokens_per_second")
    )
    metric_gate = all(result["checks"][name] for name in (
        "fixed_measured_mean_JCT_gain", "common_window_output_throughput_noninferior",
    ))
    for filename, prefix in reader.prefixes.items():
        path = Path(filename)
        if path.stat().st_size != prefix["complete_prefix_bytes"] or (
            sha256_file(path) != prefix["complete_prefix_sha256"]
        ):
            raise ValueError("completed CALL comparison observations changed")
    result.update(
        observed_at=datetime.now(timezone.utc).isoformat(),
        reference="nonranking_Full", candidate="foreground_CALL_cold_reclaim",
        complete_measured_work_fresh_drain_and_actual_exit=True,
        actual_capacity_equal=capacities[0], measured_prefix_loss_proxy_tokens=prefix_loss,
        measured_prefix_loss_nonincrease=prefix_loss[1] <= prefix_loss[0],
        actual_CALL_release_records=releases, metric_gate_passed=metric_gate,
        engineering_promotion_signal=bool(metric_gate and releases and prefix_loss[1] <= prefix_loss[0]),
        formal_workload_accepted=False, single_pair_statistical_significance_claimed=False,
        actual_RELEASE_to_consumer_causal_JCT_gain_claimed=False,
        inputs_sha256={str(path.resolve()): sha256_file(path) for path in inputs},
        independently_sealed_prefixes=reader.prefixes,
        GPU_action=False, automatic_GPU_followup=False, performance_accepted=False,
    )
    write(args.output, result)
    print({
        "mean_JCT_gain_percent": result["groups"]["measured"]["mean_JCT_reduction_percent"],
        "output_gain_percent": result["common_window_output_throughput"]["gain_percent"],
        "CALL_release_records": len(releases),
        "engineering_promotion_signal": result["engineering_promotion_signal"],
    }, flush=True)


if __name__ == "__main__":
    main()
