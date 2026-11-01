"""Compare native graph probes and partition overlapping CUDA kernel intervals."""

from collections import Counter, defaultdict
from datetime import datetime, timezone
import argparse
import gzip
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_native_system_costs import distribution
from scripts.train_workflow_work import write


def kernel_group(name):
    if "FlashAttn" in name or "flash::" in name:
        return "attention"
    if name == "fused_moe_kernel":
        return "moe_gemm"
    if "act_and_mul_kernel" in name:
        return "activation"
    if any(part in name for part in ("moe_sum", "moe_align", "expert_tokens", "router_triton")):
        return "moe_routing_reduce"
    if "nvjet_" in name or "cublas" in name:
        return "dense_gemm"
    if "rmsnorm" in name.lower():
        return "normalization"
    return "other"


def partition_kernels(events):
    edges, summed = [], Counter()
    for event in events:
        if event.get("cat") != "kernel" or event.get("ph") != "X":
            continue
        group = kernel_group(event["name"])
        start, duration = event["ts"], event["dur"]
        if duration < 0:
            raise ValueError("invalid CUDA duration")
        edges.extend(((start, group, 1), (start + duration, group, -1)))
        summed[group] += duration
    active, coverage = Counter(), Counter()
    previous = None
    for when, group, change in sorted(edges):
        if previous is not None and when > previous:
            members = tuple(sorted(name for name, count in active.items() if count))
            if members:
                coverage[members] += when - previous
        active[group] += change
        if active[group] < 0:
            raise ValueError("unbalanced kernel intervals")
        previous = when
    union = sum(coverage.values())
    if not union or any(active.values()):
        raise ValueError("empty or unbalanced CUDA capture")
    return {
        "kernel_duration_sum_ms": sum(summed.values()) / 1000,
        "kernel_interval_union_ms": union / 1000,
        "by_group": {
            group: {
                "summed_ms": value / 1000,
                "summed_fraction": value / sum(summed.values()),
                "exclusive_interval_ms": coverage[(group,)] / 1000,
                "exclusive_fraction_of_kernel_union": coverage[(group,)] / union,
            }
            for group, value in summed.items()
        },
        "simultaneously_active_kernel_groups_ms": {
            "+".join(members): value / 1000
            for members, value in coverage.items() if len(members) > 1
        },
    }


def compare(root, accounting, *, candidate_dir=None, candidate_label="exact"):
    report, sources = {}, {}
    labels = ("sparse", candidate_label)
    directories = {
        "sparse": root / "sparse",
        candidate_label: candidate_dir if candidate_dir is not None else root / "exact",
    }
    summaries = {}
    for mode in labels:
        path = directories[mode] / "summary.json"
        summaries[mode] = json.loads(path.read_bytes())
        sources[str(path.resolve())] = sha256_file(path)
    plan_path = root / "plan.json"
    plan = json.loads(plan_path.read_bytes())
    inputs_path = Path(plan["inputs"])
    inputs = json.loads(inputs_path.read_bytes())
    if sha256_file(inputs_path) != plan["inputs_sha256"]:
        raise ValueError("component inputs changed")
    candidate_plan_path = directories[candidate_label].parent / "plan.json"
    candidate_plan = json.loads(candidate_plan_path.read_bytes())
    if (
        candidate_plan["inputs_sha256"] != plan["inputs_sha256"]
        or candidate_plan["native"] != plan["native"]
    ):
        raise ValueError("candidate inputs or native engine differ from the reference")
    sources[str(candidate_plan_path.resolve())] = sha256_file(candidate_plan_path)
    numerical = []
    for case in inputs["cases"]:
        width, measurements = case["width"], {}
        for mode in labels:
            values = [
                summaries[mode]["decode_by_case"][f"{width}:measure{index}"]["worker_ms_per_output"]
                for index in range(3)
            ]
            measurements[mode] = {
                "worker_ms_per_output_median": statistics.median(values),
                "replicates": values,
            }
        for index in range(3):
            receipts = {}
            for mode in labels:
                path = directories[mode] / f"width{width}_measure{index}.json"
                receipts[mode] = json.loads(path.read_bytes())["records"]
                sources[str(path.resolve())] = sha256_file(path)
            if receipts["sparse"].keys() != receipts[candidate_label].keys():
                raise ValueError("component request identities changed")
            for identity, before in receipts["sparse"].items():
                after = receipts[candidate_label][identity]
                prefix = 0
                for a, b in zip(before["tokens"], after["tokens"], strict=True):
                    if a != b:
                        break
                    prefix += 1
                numerical.append({
                    "request": identity, "tokens": len(before["tokens"]),
                    "identical_output_prefix_tokens": prefix,
                    "all_token_ids_equal": before["tokens"] == after["tokens"],
                    "maximum_logprob_difference_on_common_prefix": max(
                        (abs(a - b) for a, b in zip(
                            before["logprobs"][:prefix], after["logprobs"][:prefix], strict=True,
                        )), default=None,
                    ),
                })
        before, after = (
            measurements[mode]["worker_ms_per_output_median"] for mode in labels
        )
        report[str(width)] = {
            "measurements": measurements,
            "candidate_worker_cost_reduction_percent": 100 * (1 - after / before),
            "context_lengths": case["context_lengths"],
        }
        if candidate_label == "exact":
            report[str(width)]["exact_worker_cost_reduction_percent"] = 100 * (1 - after / before)
    profiles = {}
    for mode in labels:
        profiles[mode] = []
        for profile in summaries[mode]["profiles"]:
            path = Path(profile["trace"])
            if "DECODE" not in path.name:
                continue
            with gzip.open(path, "rt") as stream:
                partition = partition_kernels(json.load(stream)["traceEvents"])
            profiles[mode].append({
                "trace": str(path), **partition,
            })
            sources[str(path.resolve())] = sha256_file(path)
    geometry = json.loads(accounting.read_bytes())
    worker = geometry["scopes"]["common_window"]["worker_ms"]
    cost = 0
    covered = 0
    for width, row in report.items():
        if width not in geometry["common_window_decode_by_width"]:
            continue
        observed = geometry["common_window_decode_by_width"][width]["worker_ms"]
        covered += observed
        cost += observed * row["candidate_worker_cost_reduction_percent"] / 100
    sources.update({
        str(path.resolve()): sha256_file(path)
        for path in (plan_path, inputs_path, accounting, Path(__file__))
    })
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(), "by_width": report,
        "candidate_label": candidate_label,
        "numerical": {
            "requests": len(numerical),
            "all_output_sequences_equal": all(row["all_token_ids_equal"] for row in numerical),
            "different_sequences": sum(not row["all_token_ids_equal"] for row in numerical),
            "rows": numerical,
        },
        "decode_cuda_profiles": profiles,
        **({
            "control_width8_cost_change_percent": report["8"]["candidate_worker_cost_reduction_percent"],
        } if candidate_label == "exact" else {
            "width8_cost_change_percent": report["8"]["candidate_worker_cost_reduction_percent"],
            "unchanged_config_width17_cost_change_percent": report["17"]["candidate_worker_cost_reduction_percent"],
        } if candidate_label == "tuned_moe" else {}),
        "sample_reweighting": {
            "covered_common_window_worker_fraction": covered / worker,
            "estimated_worker_time_change_seconds": cost / 1000,
            "estimated_total_worker_reduction_percent": 100 * cost / worker,
            "unmeasured_widths_treated_as_unchanged": True,
            "represents_JCT_or_a_causal_savings_bound": False,
        },
        "semantics": [
            "Each cost is the median of three unprofiled request bursts, divided by actual observed output tokens.",
            "CUDA profiling requests are excluded from ordinary cost comparison.",
            {
                "exact": "Exact graphs leave width8 on the same bucket as its reference.",
                "tuned_moe": "Sparse graphs with tuned MoE retain default launch parameters at width17/padded24.",
                "exact_moe": "Combined exact graphs and tuned MoE have no designated unchanged-width control.",
            }[candidate_label],
            "Static reweighting applies a few captured context examples to historical width totals. It is a screen, not an end-to-end forecast.",
            "Kernel groups overlap, including programmatic dependent launches; sums are not exclusive execution or removable JCT.",
            "Numerical equality here covers the bounded greedy probes, not every future request or full replay.",
            "No workload arrivals, Full policy or official comparison result is changed.",
        ],
        "inputs_sha256": sources, "GPU_action": False,
        "performance_accepted": False,
    }


def analyze_prefill(root):
    plan_path, summary_path = root / "plan.json", root / "sparse/summary.json"
    plan, summary = (json.loads(path.read_bytes()) for path in (plan_path, summary_path))
    if plan.get("cold_prefill_only") is not True or plan["modes"] != ["sparse"]:
        raise ValueError("expected a separately declared native cold prefill probe")
    directory = root / "sparse"
    case_path, receipts_path = directory / "prefill_case.json", directory / "receipts.json"
    case, receipts = (json.loads(path.read_bytes()) for path in (case_path, receipts_path))
    expected = case["executed_input_tokens_per_request"]
    drain_path, cleanup_path = directory / "telemetry_drain.json", directory / "owned_process_cleanup.json"
    drain, cleanup = (json.loads(path.read_bytes()) for path in (drain_path, cleanup_path))
    status = drain["drained_status"]
    if (
        cleanup.get("remaining_processes") != [] or cleanup.get("reason") != "component_complete"
        or status["snapshot_ts_ms"] <= drain["previous_zero_snapshot_ts_ms"]
        or status.get("writer_error") is not None
        or any(status.get(key) != 0 for key in (
            "pending_batch_count", "pending_request_count", "writer_queue_depth", "failed_records", "dropped_records",
        ))
    ):
        raise ValueError("cold prefill collection lacks completed drain or cleanup")
    samples = [
        summary["prefill_by_case"][f"1:prefill_measure{index}"]
        for index in range(3)
    ]
    if (
        summary["all_output_ordinals_match_native_responses"] is not True
        or len(receipts) != 4 or len(summary["prefill_by_case"]) != 4
        or any(
            row["executed_input_tokens"] != expected or row["output_tokens"] != 1
            for row in summary["prefill_by_case"].values()
        )
        or any(len(row["tokens"]) != 1 or row["cached_tokens"] != 0 for row in receipts.values())
    ):
        raise ValueError("cold prefill actual work or completion evidence differs from its declaration")
    profiles, sources = [], {}
    for item in summary["profiles"]:
        path = Path(item["trace"])
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt") as stream:
            profile = partition_kernels(json.load(stream)["traceEvents"])
        profiles.append({"trace": str(path), **profile})
        sources[str(path.resolve())] = sha256_file(path)
    if not profiles:
        raise ValueError("cold prefill probe has no CUDA profile")
    for path in (plan_path, summary_path, case_path, receipts_path, drain_path, cleanup_path, Path(__file__)):
        sources[str(path.resolve())] = sha256_file(path)
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "captured_case": case, "unprofiled_samples": samples,
        "median_unprofiled_worker_ms_per_input_token": statistics.median(
            row["worker_ms_per_input_token"] for row in samples
        ),
        "all_four_single_token_outputs_equal": len({tuple(row["tokens"]) for row in receipts.values()}) == 1,
        "prefill_cuda_profiles": profiles, "inputs_sha256": sources,
        "semantics": [
            "Three unprofiled cold requests and a separate profiled request use the same exact captured prefix.",
            "Input execution counts include every native prefill chunk; each request returns one output token.",
            "Worker intervals include CPU, GPU and dependencies and are not pure CUDA kernel time.",
            "This one-context cold-prefill screen does not establish the cost of every continuation or workload.",
            "Kernel duration sums overlap; exclusive intervals and union are reported separately.",
            "No Full policy, tool timing, official workload or baseline result changes.",
        ],
        "GPU_action": False, "performance_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("root", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--accounting", type=Path)
    parser.add_argument("--candidate-dir", type=Path)
    parser.add_argument("--candidate-label", choices=("exact", "tuned_moe", "exact_moe"), default="exact")
    parser.add_argument("--prefill", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve earlier cost evidence")
    if args.prefill:
        result = analyze_prefill(args.root)
        write(args.output, result)
        print(json.dumps({
            "median_unprofiled_worker_ms_per_input_token": result["median_unprofiled_worker_ms_per_input_token"],
            "prefill_cuda_profiles": result["prefill_cuda_profiles"],
        }))
        return
    if args.accounting is None:
        parser.error("decode comparison requires --accounting")
    result = compare(
        args.root, args.accounting,
        candidate_dir=args.candidate_dir, candidate_label=args.candidate_label,
    )
    write(args.output, result)
    print(json.dumps({
        "by_width": result["by_width"],
        "all_output_sequences_equal": result["numerical"]["all_output_sequences_equal"],
        "sample_reweighting": result["sample_reweighting"],
    }))


if __name__ == "__main__":
    main()
