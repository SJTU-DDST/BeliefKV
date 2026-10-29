"""Seal the complete repetition, retaining actual paths and the old fixed cohort."""

from datetime import datetime, timezone
import gzip
import importlib.util
import json
import math
from pathlib import Path


HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "repeat_r2_complete"
spec = importlib.util.spec_from_file_location(
    "repeat_result_reader", HERE / "repeat_complete_review.py",
)
REVIEW = importlib.util.module_from_spec(spec)
spec.loader.exec_module(REVIEW)
BASE = REVIEW.BASE


def main():
    output = OUTPUT / "final_index.json"
    if output.exists():
        raise FileExistsError("preserve earlier complete repeat index")
    inputs = {}

    def load(path):
        inputs[str(path)] = BASE.sha256_file(path)
        return BASE.causal.load(path)

    complete = load(OUTPUT / "complete_review.json")
    mechanism = load(OUTPUT / "mechanism_review.json")
    paths = load(HERE / "repeat_r2_paths.json")
    service = load(HERE / "repeat_r2_service_interleaving.json")
    old_paths = load(HERE / "complete_jct_paths.json")
    old_cohort = load(HERE / "fixed_cohort_review.json")
    previous = load(HERE / "complete_review.json")
    if (
        type(complete.get("both_repeats_improve_both_metrics")) is not bool
        or not complete["two_fresh_idle_snapshots_and_actual_owned_exit_verified"]
        or not paths["same_request_work_verified"] or not service["same_exact_request_work_verified"]
        or not mechanism["byte_balance"]["exact_physical_bytes_reconciled"]
        or not mechanism["final_eos"]["all_triggered_requests_observed_final"]
        or not mechanism["known_guards"]["all_cases_verified"]
    ):
        raise ValueError("complete repeated work, path or mechanism evidence is unverified")
    for report in (complete, mechanism):
        for source, digest in report["inputs_sha256"].items():
            if BASE.sha256_file(Path(source)) != digest:
                raise ValueError("complete repetition inputs changed: " + source)
            inputs[source] = digest
    first = BASE.causal.load(REVIEW.REPEAT.PRIOR)
    second = BASE.causal.load(REVIEW.REPEAT.TARGET)
    REVIEW.repeat_identity(first, second)
    BASE.completed(second, REVIEW.REPEAT.TARGET, "full", BASE.DEMAND_PREFILL_PIPELINE_TREE)
    helper = BASE.ROOT / (
        "experiments/reports/tool_offload_before_admission_20261027/control_regression_audit.py"
    )
    cohort = REVIEW.helper(helper, "repeat_original_fixed_cohort")
    fixed = {
        name: cohort.critical_partition(
            service["arms"][name], old_paths["arms"]["Preadmission"], cohort="post_tool",
        )
        for name in ("Full_R2", "Preadmission")
    }
    if (
        fixed["Preadmission"] != old_cohort["fixed_historical_cohort"]["Preadmission"]
        or any((row["request_count"], row["output_tokens"]) != (2973, 467561)
               for row in fixed.values())
    ):
        raise ValueError("the original fixed historical cohort changed")
    fixed_delta = {
        name: value - fixed["Preadmission"]["workflow_equal_weight_s"][name]
        for name, value in fixed["Full_R2"]["workflow_equal_weight_s"].items()
    }
    if not math.isclose(fixed_delta["first_launch_to_complete_s"], math.fsum(
        fixed_delta[name] for name in (
            "prefill_member_interval_union_s", "decode_member_interval_union_s",
            "outside_member_worker_intervals_s",
        )
    ), abs_tol=1e-6):
        raise ValueError("fixed historical service components do not reconcile")
    current, before = paths["arms"]["Full_R2"], paths["arms"]["Preadmission"]
    components = {
        name: current["mean_components_s"].get(name, 0.) - before["mean_components_s"].get(name, 0.)
        for name in current["mean_components_s"].keys() | before["mean_components_s"].keys()
    }
    delta = current["mean_jct_s"] - before["mean_jct_s"]
    if not math.isclose(math.fsum(components.values()), delta, abs_tol=1e-6):
        raise ValueError("actual complete path components do not reconstruct JCT")
    chains = {
        name: row["critical_path_requests"] == before["workflows"][name]["critical_path_requests"]
        for name, row in current["workflows"].items()
    }
    row, control = complete["metrics"]["Full R2"], previous["metrics"]["Preadmission Full"]
    preadmission = {
        "mean_jct_reduction_percent": 100. * (1. - row["mean_jct_s"] / control["mean_jct_s"]),
        "end_to_end_output_increase_percent": 100. * (
            row["end_to_end_output_tokens_per_s"] / control["end_to_end_output_tokens_per_s"] - 1.
        ),
    }
    archives = {}
    for source in (HERE / "repeat_r2_paths.json", HERE / "repeat_r2_service_interleaving.json"):
        archive = source.with_suffix(source.suffix + ".gz")
        if archive.exists():
            raise FileExistsError("preserve earlier path archive")
        raw = source.read_bytes()
        with archive.open("xb") as stream:
            stream.write(gzip.compress(raw, mtime=0))
        if gzip.decompress(archive.read_bytes()) != raw:
            raise ValueError("repeat path archive differs from original bytes")
        archives[str(archive)] = {
            "original_sha256": BASE.sha256_file(source), "archive_sha256": BASE.sha256_file(archive),
        }
    inputs[str(helper)] = BASE.sha256_file(helper)
    inputs[str(Path(__file__))] = BASE.sha256_file(Path(__file__))
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Complete R2 repetition and mechanisms; actual and fixed historical paths are separate.",
        "requests": complete["requests"], "prompt_tokens": complete["prompt_tokens"],
        "output_tokens": complete["output_tokens"], "all_normal_tails_retained": True,
        "metrics": complete["metrics"],
        "comparisons_vs_same_V345": complete["comparisons_vs_same_V345"],
        "both_repeats_improve_both_metrics": complete["both_repeats_improve_both_metrics"],
        "R2_comparison_vs_preadmission": preadmission,
        "same_actual_critical_chains_as_preadmission": chains,
        "mean_jct_delta_vs_preadmission_s": delta,
        "actual_critical_component_delta_vs_preadmission_s": components,
        "fixed_historical_tool_successors": fixed,
        "fixed_historical_tool_delta_vs_preadmission_s": fixed_delta,
        "native_byte_balance": mechanism["byte_balance"],
        "staged_native_demand": mechanism["staged_native_demand"],
        "staged_native_service": mechanism["staged_native_service"],
        "handoff_events": mechanism["handoff_events"],
        "distinct_multi_member_native_forwards": mechanism["distinct_multi_member_native_forwards"],
        "final_eos": mechanism["final_eos"], "known_guards": mechanism["known_guards"],
        "archives": archives, "inputs_sha256": inputs,
        "main_admission_retry_fix_included": False,
        "ordinary_math_long_greedy_equivalence_verified": False,
        "performance_accepted": False,
        "limitations": [
            "Both Full repetitions improve JCT/output against the same single complete V345 run.",
            "The reused V345 native engine differs; R1/R2 source, configuration and engine are identical.",
            "Gains remain modest; positive repetition alone does not prove all-baseline superiority.",
            "R2 is slightly below preadmission output throughput; this negative result is retained.",
            "Native ACK/service and worker intervals do not prove hidden DMA or exclusive control CPU.",
            "No new repair, module ablation or changed workload is validated by this repetition.",
        ],
    }
    BASE.causal.reservation.write_json(output, result)
    print(json.dumps({
        name: result[name] for name in (
            "R2_comparison_vs_preadmission", "mean_jct_delta_vs_preadmission_s",
            "actual_critical_component_delta_vs_preadmission_s",
            "fixed_historical_tool_delta_vs_preadmission_s",
        )
    }, indent=2))


if __name__ == "__main__":
    main()
