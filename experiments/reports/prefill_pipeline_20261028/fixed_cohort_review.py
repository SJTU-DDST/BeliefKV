"""Compare the original 2973 tool successors without changing that cohort."""

from datetime import datetime, timezone
import importlib.util
import math
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal


def main():
    output = HERE / "fixed_cohort_review.json"
    if output.exists():
        raise FileExistsError("preserve original fixed-cohort evidence")
    complete_path = HERE / "complete_review.json"
    paths_path = HERE / "complete_jct_paths.json"
    service_path = HERE / "complete_service_interleaving.json"
    complete, paths, service = (
        causal.load(path) for path in (complete_path, paths_path, service_path)
    )
    if (
        complete["exact_work_and_original_stopping_verified"] is not True
        or paths["same_request_work_verified"] is not True
        or service["same_exact_request_work_verified"] is not True
    ):
        raise ValueError("fixed-cohort comparison requires complete exact work")
    helper = (
        ROOT / "experiments/reports/tool_offload_before_admission_20261027/"
        "control_regression_audit.py"
    )
    spec = importlib.util.spec_from_file_location("demand_fixed_historical_cohort", helper)
    prior = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prior)
    historical_path = helper.with_suffix(".json")
    historical = causal.load(historical_path)
    fixed_paths = paths["arms"]["Preadmission"]
    arms = {
        name: prior.critical_partition(
            service["arms"][name], fixed_paths, cohort="post_tool",
        )
        for name in ("Full", "Preadmission", "ThunderAgent")
    }
    reference = historical["arms"]["before"]["critical_tool_successors"]
    if arms["Preadmission"] != reference or any(
        (row["request_count"], row["output_tokens"]) != (2973, 467561)
        for row in arms.values()
    ):
        raise ValueError("the original historical tool-successor cohort changed")
    changed = []
    for workflow, current in paths["arms"]["Full"]["workflows"].items():
        previous = fixed_paths["workflows"][workflow]
        before_ids, after_ids = previous["critical_path_requests"], current["critical_path_requests"]
        if before_ids != after_ids:
            changed.append({
                "workflow_id": workflow,
                "old_path_requests": len(before_ids), "new_path_requests": len(after_ids),
                "only_old_path_requests": sorted(set(before_ids) - set(after_ids)),
                "only_new_path_requests": sorted(set(after_ids) - set(before_ids)),
            })
    comparisons = {}
    for name in ("Preadmission", "ThunderAgent"):
        before = arms[name]["workflow_equal_weight_s"]
        current = arms["Full"]["workflow_equal_weight_s"]
        delta = {field: value - before[field] for field, value in current.items()}
        if not math.isclose(
            delta["first_launch_to_complete_s"],
            math.fsum(delta[field] for field in (
                "prefill_member_interval_union_s", "decode_member_interval_union_s",
                "outside_member_worker_intervals_s",
            )),
            abs_tol=1e-6,
        ):
            raise ValueError("fixed-cohort service intervals do not reconstruct lifetime")
        comparisons[name] = delta
    inputs = {
        str(path): sha256_file(path) for path in (
            Path(__file__), helper, historical_path, complete_path, paths_path, service_path,
        )
    }
    for arm in service["arms"].values():
        for source, seal in arm["input_jsonl_prefixes"].items():
            if Path(source).stat().st_size != seal["complete_prefix_bytes"] or (
                sha256_file(Path(source)) != seal["complete_prefix_sha256"]
            ):
                raise ValueError("complete fixed-cohort worker inputs changed")
            inputs[source] = seal["complete_prefix_sha256"]
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "The exact original 2973 historical critical tool successors, not new critical paths.",
        "fixed_historical_cohort": arms,
        "fixed_historical_cohort_delta_s": comparisons,
        "actual_changed_workflow_paths": changed,
        "current_actual_critical_tool_successors": sum(
            service["arms"]["Full"]["requests"][request]["cohort"] == "post_tool"
            for workflow in paths["arms"]["Full"]["workflows"].values()
            for request in workflow["critical_path_requests"]
        ),
        "inputs_sha256": inputs,
        "performance_accepted": False,
        "incremental_control_cpu_measured": False,
        "limitations": [
            "Each original request and all ordinary tails remain; actual changed paths are disclosed.",
            "Historical-cohort sums are no longer the current workflow JCT critical-path decomposition.",
            "Worker intervals include runtime and dependency waits; they are not pure kernel time.",
            "This descriptive check does not identify isolated module effects or exposed control CPU.",
        ],
    }
    causal.reservation.write_json(output, result)
    print({
        "changed_actual_workflow_paths": len(changed),
        "fixed_historical_cohort_delta_s": comparisons,
        "current_actual_critical_tool_successors": result["current_actual_critical_tool_successors"],
    })


if __name__ == "__main__":
    main()
