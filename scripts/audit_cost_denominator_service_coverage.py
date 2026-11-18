"""Check whether partial-identity samples precede cost-reversal service evidence."""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts.audit_native_system_costs import distribution
from scripts.audit_periodic_admission_decisions import useful_work
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write


NAMES = ("shorter_workflow_expensive_request", "longer_workflow_cheaper_request")


def inspect(report, batches):
    pairs = report["strict_cost_reversals"]["unique_request_pair_examples"]
    queries, counts = defaultdict(list), Counter()
    coverage = [[Counter(), Counter()] for _pair in pairs]
    for index, pair in enumerate(pairs):
        boundary = pair["monotonic_s"] * 1000.
        for side, name in enumerate(NAMES):
            member = pair[name]
            progress = member["following_exact_native_progress"]
            if progress is None:
                continue
            queries[member["key"]["request_id"]].append((
                index, side, member["key"], boundary,
                boundary + progress["new_service_wait_ms"],
            ))
    for batch in batches:
        if batch.get("event") != "gpu_service_sample":
            continue
        for sample in batch["request_samples"]:
            if sample.get("request_id") not in queries or not useful_work(
                sample, sample.get("phase", batch["phase"]),
            ):
                continue
            if all(sample.get(name) is not None for name in (
                "attempt_id", "session_id", "session_generation",
            )):
                continue
            for index, side, key, boundary, first_exact in queries[sample["request_id"]]:
                if any(
                    sample.get(name) is not None and sample[name] != key[target]
                    for name, target in (
                        ("workflow_id", "root_workflow_id"), ("invocation_id", "invocation_id"),
                        ("context_id", "context_id"), ("context_epoch", "context_epoch"),
                        ("attempt_id", "attempt_id"), ("session_id", "session_id"),
                        ("session_generation", "session_generation"),
                    )
                ):
                    continue
                if batch["launch_monotonic_ms"] >= boundary and (
                    batch["service_start_monotonic_ms"] < first_exact
                ):
                    coverage[index][side]["ambiguous_positive_samples_before_first_exact"] += 1
    examples, gaps = [], []
    for pair, sides in zip(pairs, coverage):
        clean = all(not side["ambiguous_positive_samples_before_first_exact"] for side in sides)
        both = pair["both_followed_by_exact_new_progress"]
        direct = (
            pair["selected_long_request_actual_direct_grant"]
            and pair["selected_long_request_retained_at_grant"] is True
        )
        counts["unique_exact_request_pairs"] += 1
        counts["both_requests_have_exact_following_service"] += both
        counts["pairs_with_ambiguous_earlier_positive_samples"] += not clean
        counts["direct_retained_long_served_first_without_earlier_ambiguous_samples"] += (
            both and direct and clean and pair["selected_long_served_first"] is True
        )
        if both and direct and clean and pair["selected_long_served_first"] is True:
            gaps.append(pair["actual_short_minus_long_service_gap_ms"])
        examples.append({
            "raw_journal_sequence": pair["raw_journal_sequence"],
            "request_ids": [pair[name]["key"]["request_id"] for name in NAMES],
            "partial_identity_coverage": [dict(side) for side in sides],
        })
    return {
        "counts": dict(counts),
        "unambiguous_observed_long_first_pair_gaps_ms": distribution(gaps),
        "unique_short_requests": len({pair[NAMES[0]]["key"]["request_id"] for pair in pairs}),
        "unique_long_requests": len({pair[NAMES[1]]["key"]["request_id"] for pair in pairs}),
        "unique_short_workflows": len({pair[NAMES[0]]["key"]["root_workflow_id"] for pair in pairs}),
        "both_alternative_sets_current_free_plus_safe_cold_fit": sum(
            pair["actual_set_evaluation"]["current_device_summary_fits"]
            and pair["one_for_one_swap_evaluation"]["current_device_summary_fits"]
            for pair in pairs
        ),
        "short_alternative_current_free_plus_safe_cold_fit": sum(
            pair["one_for_one_swap_evaluation"]["current_device_summary_fits"] for pair in pairs
        ),
        "short_alternative_has_more_prefill_proxy": sum(
            pair["one_for_one_swap_evaluation"]["prefill_proxy_ms"]
            > pair["actual_set_evaluation"]["prefill_proxy_ms"] for pair in pairs
        ),
        "short_alternative_has_more_H2D_proxy": sum(
            pair["one_for_one_swap_evaluation"]["H2D_proxy_ms"]
            > pair["actual_set_evaluation"]["H2D_proxy_ms"] for pair in pairs
        ),
        "short_alternative_has_more_decode_proxy": sum(
            pair["one_for_one_swap_evaluation"]["decode_after_ms"]
            > pair["actual_set_evaluation"]["decode_after_ms"] for pair in pairs
        ),
        "examples": examples,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve previous service-coverage evidence")
    report = causal.load(args.analysis)
    paths = [Path(name) for name in report["input_jsonl_prefixes"] if name.endswith("/server/runtime_audit.jsonl")]
    if len(paths) != 1:
        raise ValueError("require exactly one original sealed native journal")
    pins = {str(path.resolve()): sha256_file(path) for path in (args.analysis, Path(__file__))}
    reader = SealedRecords()
    result = inspect(report, reader(paths[0]))
    if reader.prefixes[str(paths[0])] != report["input_jsonl_prefixes"][str(paths[0])]:
        raise ValueError("native input differs from the original denominator audit")
    if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
        raise ValueError("service-coverage input changed")
    result.update(
        inputs_sha256=pins, input_jsonl_prefixes=reader.prefixes,
        scope={
            "same_sealed_native_samples_and_exact_request_pair_population": True,
            "partial_identity_samples_never_promoted_to_exact_attempt_evidence": True,
            "reported_gaps_require_both_exact_new_services_and_retained_direct_grant": True,
            "new_service_clock_is_observed_worker_interval_not_GPU_kernel_start": True,
            "observed_gap_is_not_causally_attributed_to_one_selection": True,
            "complete_counterfactual_JCT_proven": False,
        },
        GPU_action=False,
    )
    write(args.output, result)
    print(json.dumps({key: value for key, value in result.items() if key not in (
        "examples", "inputs_sha256", "input_jsonl_prefixes", "scope",
    )}), flush=True)


if __name__ == "__main__":
    main()
