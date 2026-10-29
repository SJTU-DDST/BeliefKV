"""Seal complete repaired-source performance, actual paths and the old fixed cohort."""

from datetime import datetime, timezone
import gzip
import importlib.util
import json
import math
from pathlib import Path


HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "readmission_complete"
spec = importlib.util.spec_from_file_location(
    "readmission_result_contract", HERE / "readmission_complete_review.py",
)
REVIEW = importlib.util.module_from_spec(spec)
spec.loader.exec_module(REVIEW)
BASE = REVIEW.BASE


def main():
    output = OUTPUT / "final_index.json"
    if output.exists():
        raise FileExistsError("preserve original complete repaired-source index")
    inputs = {}

    def load(path):
        inputs[str(path)] = BASE.sha256_file(path)
        return BASE.causal.load(path)

    complete = load(OUTPUT / "complete_review.json")
    mechanism = load(OUTPUT / "mechanism_review.json")
    paths = load(HERE / "readmission_paths.json")
    service = load(HERE / "readmission_service_interleaving.json")
    old_paths = load(HERE / "complete_jct_paths.json")
    old_cohort = load(HERE / "fixed_cohort_review.json")
    repeated = load(HERE / "repeat_r2_complete/final_index.json")
    if (
        not complete["exact_work_and_original_stopping_verified"]
        or not complete["two_fresh_idle_snapshots_and_actual_owned_exit_verified"]
        or not paths["same_request_work_verified"] or not service["same_exact_request_work_verified"]
        or not mechanism["byte_balance"]["exact_physical_bytes_reconciled"]
        or not mechanism["physical_plan_review"]["journal_release_totals_reconciled"]
        or not mechanism["final_eos"]["all_triggered_requests_observed_final"]
        or not mechanism["known_guards"]["all_cases_verified"]
        or complete["ack_native_capacity_retry_events"] != mechanism["ack_native_capacity_retry_events"]
    ):
        raise ValueError("complete repaired work, mechanism or retry evidence is unverified")
    for report in (complete, mechanism):
        for source, digest in report["inputs_sha256"].items():
            if BASE.sha256_file(Path(source)) != digest:
                raise ValueError("complete repaired result input changed: " + source)
            inputs[source] = digest
    plan = BASE.causal.load(REVIEW.REGISTRATION)
    REVIEW.same_work(plan)
    BASE.completed(plan, REVIEW.REGISTRATION, "full", BASE.DEMAND_PREFILL_PIPELINE_TREE)
    helper_path = BASE.ROOT / (
        "experiments/reports/tool_offload_before_admission_20261027/control_regression_audit.py"
    )
    helper_spec = importlib.util.spec_from_file_location("repair_fixed_cohort", helper_path)
    cohort = importlib.util.module_from_spec(helper_spec)
    helper_spec.loader.exec_module(cohort)
    fixed = {
        name: cohort.critical_partition(
            service["arms"][name], old_paths["arms"]["Preadmission"], cohort="post_tool",
        )
        for name in ("Full_ACK", "Preadmission")
    }
    if (
        fixed["Preadmission"] != old_cohort["fixed_historical_cohort"]["Preadmission"]
        or any((row["request_count"], row["output_tokens"]) != (2973, 467561)
               for row in fixed.values())
    ):
        raise ValueError("original fixed historical tool cohort changed")
    fixed_delta = {
        name: value - fixed["Preadmission"]["workflow_equal_weight_s"][name]
        for name, value in fixed["Full_ACK"]["workflow_equal_weight_s"].items()
    }
    if not math.isclose(fixed_delta["first_launch_to_complete_s"], math.fsum(
        fixed_delta[name] for name in (
            "prefill_member_interval_union_s", "decode_member_interval_union_s",
            "outside_member_worker_intervals_s",
        )
    ), abs_tol=1e-6):
        raise ValueError("fixed historical service components do not reconcile")
    current, before = paths["arms"]["Full_ACK"], paths["arms"]["Preadmission"]
    components = {
        name: current["mean_components_s"].get(name, 0.) - before["mean_components_s"].get(name, 0.)
        for name in current["mean_components_s"].keys() | before["mean_components_s"].keys()
    }
    delta = current["mean_jct_s"] - before["mean_jct_s"]
    if not math.isclose(math.fsum(components.values()), delta, abs_tol=1e-6):
        raise ValueError("actual path components do not reproduce complete JCT")
    chains = {
        name: row["critical_path_requests"] == before["workflows"][name]["critical_path_requests"]
        for name, row in current["workflows"].items()
    }
    archives = {}
    for source in (HERE / "readmission_paths.json", HERE / "readmission_service_interleaving.json"):
        archive = source.with_suffix(source.suffix + ".gz")
        if archive.exists():
            raise FileExistsError("preserve earlier complete repaired path archive")
        raw = source.read_bytes()
        with archive.open("xb") as stream:
            stream.write(gzip.compress(raw, mtime=0))
        if gzip.decompress(archive.read_bytes()) != raw:
            raise ValueError("repaired path archive differs from original bytes")
        archives[str(archive)] = {
            "original_sha256": BASE.sha256_file(source), "archive_sha256": BASE.sha256_file(archive),
        }
    inputs[str(helper_path)] = BASE.sha256_file(helper_path)
    inputs[str(Path(__file__))] = BASE.sha256_file(Path(__file__))
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Complete repaired-source result with actual and fixed historical paths kept separate.",
        "requests": complete["requests"], "prompt_tokens": complete["prompt_tokens"],
        "output_tokens": complete["output_tokens"], "all_normal_tails_retained": True,
        "metrics": complete["metrics"], "comparisons": complete["comparisons"],
        "original_R1_R2_comparisons_vs_same_V345": repeated["comparisons_vs_same_V345"],
        "same_actual_critical_chains_as_preadmission": chains,
        "mean_jct_delta_vs_preadmission_s": delta,
        "actual_critical_component_delta_vs_preadmission_s": components,
        "fixed_historical_tool_successors": fixed,
        "fixed_historical_tool_delta_vs_preadmission_s": fixed_delta,
        "native_byte_balance": mechanism["byte_balance"],
        "staged_native_demand": mechanism["staged_native_demand"],
        "staged_native_service": mechanism["staged_native_service"],
        "native_transfers": mechanism["native_transfers"],
        "handoff_events": mechanism["handoff_events"],
        "handoff_consumer_h2d_bytes": mechanism["handoff_consumer_h2d_bytes"],
        "physical_plan_review": mechanism["physical_plan_review"],
        "sibling_service": mechanism["sibling_service"],
        "distinct_multi_member_native_forwards": mechanism["distinct_multi_member_native_forwards"],
        "join_grants": mechanism["join_grants"],
        "final_eos": mechanism["final_eos"], "known_guards": mechanism["known_guards"],
        "ack_restore_ready_events": complete["ack_restore_ready_events"],
        "ack_native_capacity_retry_events": complete["ack_native_capacity_retry_events"],
        "archives": archives, "inputs_sha256": inputs,
        "main_admission_retry_fix_included": True,
        "ordinary_math_long_greedy_equivalence_verified": False,
        "incremental_control_cpu_measured": False, "performance_accepted": False,
        "limitations": [
            "Two identical-source repetitions and this separately qualified repaired source share one V345 control.",
            "An untriggered ACK retry cannot explain the measured performance change.",
            "All normal tails, workflow-gap regressions and slight output loss against preadmission remain.",
            "Source engines differ from V345; there is no isolated module or all-baseline acceptance.",
            "Copies and worker intervals do not establish hidden DMA, pure kernels or exposed control CPU.",
        ],
    }
    BASE.causal.reservation.write_json(output, result)
    print(json.dumps({
        name: result[name] for name in (
            "comparisons", "mean_jct_delta_vs_preadmission_s",
            "actual_critical_component_delta_vs_preadmission_s",
            "fixed_historical_tool_delta_vs_preadmission_s",
            "ack_native_capacity_retry_events",
        )
    }, indent=2))


if __name__ == "__main__":
    main()
