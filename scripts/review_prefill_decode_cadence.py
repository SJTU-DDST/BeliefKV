"""Compare cadence-only Full runs on the same complete fixed measured work."""

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts.review_predictive_thunder_cohort import TRAFFIC_FIELDS
from scripts.review_sustained_oracle import compare_cohorts
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import review_matches_execution, verify_execution


def matching_conditions(control, candidate, control_configuration, candidate_configuration):
    if control.get("order") != ["full"] or candidate.get("order") != ["full"]:
        raise ValueError("cadence comparison requires two Full arms")
    for name in TRAFFIC_FIELDS:
        if name == "source_server_parameters":
            continue
        if name not in control or control[name] != candidate.get(name):
            raise ValueError("cadence comparison changed traffic or resources: " + name)
    old, new = (
        dict(plan["source_server_parameters"]) for plan in (control, candidate)
    )
    if old.pop("prefill_decode_interval", 0) != 0 or new.pop("prefill_decode_interval", None) != 1 or old != new:
        raise ValueError("cadence comparison changed another native serving parameter")
    old, new = (
        dict(config["common_environment"])
        for config in (control_configuration, candidate_configuration)
    )
    if old.pop("PREFILL_DECODE_INTERVAL", "0") != "0" or new.pop("PREFILL_DECODE_INTERVAL", None) != "1" or old != new:
        raise ValueError("cadence comparison changed another Full policy setting")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("control", "candidate", "control-review-dir", "candidate-review-dir", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or args.output.exists():
        raise ValueError("hide CUDA and preserve prior cadence comparisons")
    plans = [causal.load(path) for path in (args.control, args.candidate)]
    configurations = [causal.load(Path(plan["experiment_configuration"])) for plan in plans]
    matching_conditions(*plans, *configurations)
    executions = [verify_execution(path, args.cohort) for path in (args.control, args.candidate)]
    if executions[0]["work"] != executions[1]["work"]:
        raise ValueError("cadence changed the complete measured native work")
    reports, capacities, inputs = [], [], [args.control, args.candidate, args.cohort]
    for plan, directory, execution in zip(
        plans, (args.control_review_dir, args.candidate_review_dir), executions, strict=True,
    ):
        causal.comparison.verify_sources(plan)
        review, cohort = (directory / name for name in ("review.json", "cohort.json"))
        report = causal.load(cohort)
        review_matches_execution(causal.load(review), execution)
        if report["cohort"] != causal.load(args.cohort):
            raise ValueError("cadence comparison changed cohort membership or the common window")
        for filename, prefix in report.get("input_jsonl_prefixes", {}).items():
            if Path(filename).stat().st_size != prefix["complete_prefix_bytes"] or (
                sha256_file(Path(filename)) != prefix["complete_prefix_sha256"]
            ):
                raise ValueError("completed cadence cohort evidence changed")
        server = Path(plan["run_root"]) / "full/server"
        capacity, info = server / "native_capacity_census.json", server / "server_info.json"
        capacities.append(causal.load(capacity)["capacity"])
        if causal.load(info).get("prefill_decode_interval") != len(reports):
            raise ValueError("actual server cadence differs from the declared 0-to-1 pair")
        reports.append(report)
        inputs.extend((review, cohort, capacity, info, Path(plan["experiment_configuration"])))
    if capacities[0] != capacities[1]:
        raise ValueError("cadence changed actual KV resources")
    result = compare_cohorts(*reports, minimum_jct_gain=.03, minimum_throughput_fraction=.98)
    for group in result["groups"].values():
        group["candidate_JCT_ms"] = group.pop("oracle_JCT_ms")
        group["candidate_completed_workflows"] = group.pop("oracle_completed_workflows")
        for row in group["workflows"]:
            row["candidate_JCT_ms"] = row.pop("oracle_JCT_ms")
    throughput = result["common_window_output_throughput"]
    throughput["candidate_tokens_per_second"] = throughput.pop("oracle_tokens_per_second")
    result.update(
        observed_at=datetime.now(timezone.utc).isoformat(),
        complete_measured_work_fresh_drain_and_actual_exit=True,
        only_native_prefill_decode_interval_changed=True,
        common_engine_tuning_not_agent_scheduling_innovation=True,
        same_actual_capacity=capacities[0], single_run_statistical_superiority_claimed=False,
        inputs_sha256={str(path.resolve()): sha256_file(path) for path in (*inputs, Path(__file__))},
        GPU_action=False, automatic_GPU_followup=False, performance_accepted=False,
    )
    write(args.output, result)
    print({
        "mean_JCT_reduction_percent": result["groups"]["measured"]["mean_JCT_reduction_percent"],
        "common_window_output_throughput": throughput,
        "checks": result["checks"],
    })


if __name__ == "__main__":
    main()
