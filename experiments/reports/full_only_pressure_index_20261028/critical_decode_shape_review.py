"""Describe actual decode shapes serving the same historical critical requests."""

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
HISTORY = ROOT / "experiments/reports/tool_offload_before_admission_20261027"
sys.path.insert(0, str(HISTORY))

from control_regression_audit import SOURCES, load, require, sealed_rows


def audit(service, paths):
    arm = Path(service["arm"])
    selected = {
        rid for workflow in paths["workflows"].values()
        for rid in workflow["critical_path_requests"]
        if service["requests"][rid]["cohort"] == "post_tool"
    }
    receipts = arm / "replay/native_requests.jsonl"
    lifetimes = {
        row["request_id"]: (row["submitted_monotonic_ms"], row["native_complete_monotonic_ms"])
        for row in sealed_rows(receipts, service["input_jsonl_prefixes"][str(receipts)],
                               (b'"native_receipt"',))
        if row["request_id"] in selected
    }
    require(lifetimes.keys() == selected, "critical request lifetime coverage changed")
    groups = defaultdict(lambda: {"observations": 0, "member_ms": 0.})
    weighted_batch, weighted_context, previous = 0., 0., {}
    samples = 0
    raw = arm / "server/runtime_audit.jsonl"
    clock = service["work"]["clock"]["monotonic_clock_domain"]
    for row in sealed_rows(raw, service["input_jsonl_prefixes"][str(raw)],
                           (b'"gpu_service_sample"',)):
        samples += 1
        require(row["sample_id"] == f"native-service-{samples:09d}", "missing worker sample")
        require(row["monotonic_clock_domain"] == clock, "worker clock changed")
        if row["phase"] != "decode":
            continue
        members = row["request_samples"]
        batch = row["batch_size"]
        require(len(members) == batch and batch > 0, "invalid worker batch")
        context = math.fsum(member["sequence_tokens_before"] for member in members) / batch
        key = (batch, int(context) // 2048, row["cuda_graph_replayed"])
        for member in members:
            rid = member["request_id"]
            if rid not in selected:
                continue
            begin = max(lifetimes[rid][0], row["service_start_monotonic_ms"])
            end = min(lifetimes[rid][1], row["complete_monotonic_ms"])
            if end <= begin:
                continue
            require(begin >= previous.get(rid, begin), "same-request decode intervals overlap")
            previous[rid] = end
            elapsed = end - begin
            groups[key]["observations"] += 1
            groups[key]["member_ms"] += elapsed
            weighted_batch += batch * elapsed
            weighted_context += context * elapsed
    total = math.fsum(row["member_ms"] for row in groups.values())
    expected = math.fsum(
        service["requests"][rid]["decode_member_interval_union_ms"] for rid in selected
    )
    require(math.isclose(total, expected, abs_tol=.001),
            "critical decode observations do not reproduce the existing interval union")
    return {
        "arm": str(arm), "critical_tool_requests": len(selected),
        "critical_decode_member_s_per_workflow": total / len(paths["workflows"]) / 1000.,
        "critical_member_time_weighted_actual_batch": weighted_batch / total,
        "critical_member_time_weighted_batch_mean_context": weighted_context / total,
        "positive_lifetime_member_observations": sum(row["observations"] for row in groups.values()),
        "no_same_request_decode_interval_overlap_verified": True,
    }, groups


def main():
    output = Path(__file__).with_suffix(".json")
    require(not output.exists(), "preserve original critical-shape evidence")
    reference = load(HISTORY / "control_regression_audit.json")
    require(reference["same_exact_work_and_same_critical_request_chains_verified"],
            "historical exact work or critical chain differs")
    comparison_path = HISTORY / "recent_comparison.json"
    comparison = load(comparison_path, reference["sources_sha256"][str(comparison_path)])
    arms, grouped = {}, {}
    for name, service_file, path_file, key in (
        ("before", "recent_preadmission_service_interleaving.json", "complete_jct_paths.json", "Full"),
        ("grouped", "recent_grouped_and_curve_service_interleaving.json",
         "recent_grouped_and_curve_jct_paths.json", "GroupedFull"),
    ):
        evidence = []
        for filename in (service_file, path_file):
            path = HISTORY / filename
            evidence.append(load(path, comparison["inputs_sha256"][str(path)]))
        arms[name], grouped[name] = audit(evidence[0]["arms"][key], evidence[1]["arms"][key])
    left, right = grouped["before"], grouped["grouped"]
    common = left.keys() & right.keys()
    composition = math.fsum(
        (right[key]["observations"] - left[key]["observations"])
        * (left[key]["member_ms"] / left[key]["observations"]
           + right[key]["member_ms"] / right[key]["observations"]) / 2.
        for key in common
    )
    within = math.fsum(
        (right[key]["member_ms"] / right[key]["observations"]
         - left[key]["member_ms"] / left[key]["observations"])
        * (left[key]["observations"] + right[key]["observations"]) / 2.
        for key in common
    )
    unmatched = math.fsum(right[key]["member_ms"] for key in right.keys() - left.keys()) - math.fsum(
        left[key]["member_ms"] for key in left.keys() - right.keys()
    )
    delta = arms["grouped"]["critical_decode_member_s_per_workflow"] - arms["before"][
        "critical_decode_member_s_per_workflow"
    ]
    require(math.isclose((composition + within + unmatched) / 18000., delta, abs_tol=1e-6),
            "descriptive group decomposition does not reconstruct complete critical decode")
    require(math.isclose(delta, reference["critical_delta_workflow_equal_weight_s"][
        "critical_tool_successors"
    ]["decode_member_interval_union_s"], abs_tol=1e-6), "historical decode delta changed")
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Complete same-critical-tool-request decode membership geometry.",
        "arms": arms,
        "critical_decode_delta_s_per_workflow": delta,
        "descriptive_symmetric_bin_decomposition_s_per_workflow": {
            "membership_count_and_shape_composition": composition / 18000.,
            "within_common_bin_member_interval": within / 18000.,
            "unmatched_bins": unmatched / 18000.,
        },
        "bins": {
            name: [
                {"batch": key[0], "mean_context_2048_bin": key[1], "cuda_graph": key[2], **value}
                for key, value in sorted(groups.items())
            ] for name, groups in grouped.items()
        },
        "sources_sha256": {
            **SOURCES,
            **{str(path): hashlib.sha256(path.read_bytes()).hexdigest()
               for path in (Path(__file__), HISTORY / "control_regression_audit.py")},
        },
        "gpu_experiment_started": False, "production_code_changed": False,
        "performance_accepted": False, "isolated_control_cost_attribution": False,
        "limitations": [
            "All critical tool successors and normal tails are retained; none are matched away.",
            "Each positive member interval counts in its workflow path, including other batch members.",
            "Actual batch means and 2048-token context bins are coarse; differing batches are not identical.",
            "The symmetric bin identity is arithmetic, not a causal estimate or an isolated scheduling effect.",
            "Within-bin changes include clipping, context variation, dependencies, runtime and waits.",
            "These worker intervals do not identify pure kernels, exposed control CPU or removable JCT.",
        ],
    }
    with output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({key: result[key] for key in (
        "arms", "critical_decode_delta_s_per_workflow",
        "descriptive_symmetric_bin_decomposition_s_per_workflow",
    )}, indent=2))


if __name__ == "__main__":
    main()
