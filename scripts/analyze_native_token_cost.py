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


def compare(root, accounting):
    report, sources = {}, {}
    summaries = {}
    for mode in ("sparse", "exact"):
        path = root / mode / "summary.json"
        summaries[mode] = json.loads(path.read_bytes())
        sources[str(path.resolve())] = sha256_file(path)
    plan_path = root / "plan.json"
    plan = json.loads(plan_path.read_bytes())
    inputs_path = Path(plan["inputs"])
    inputs = json.loads(inputs_path.read_bytes())
    if sha256_file(inputs_path) != plan["inputs_sha256"]:
        raise ValueError("component inputs changed")
    numerical = []
    for case in inputs["cases"]:
        width, measurements = case["width"], {}
        for mode in ("sparse", "exact"):
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
            for mode in ("sparse", "exact"):
                path = root / mode / f"width{width}_measure{index}.json"
                receipts[mode] = json.loads(path.read_bytes())["records"]
                sources[str(path.resolve())] = sha256_file(path)
            if receipts["sparse"].keys() != receipts["exact"].keys():
                raise ValueError("component request identities changed")
            for identity, before in receipts["sparse"].items():
                after = receipts["exact"][identity]
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
            measurements[mode]["worker_ms_per_output_median"] for mode in ("sparse", "exact")
        )
        report[str(width)] = {
            "measurements": measurements,
            "exact_worker_cost_reduction_percent": 100 * (1 - after / before),
            "context_lengths": case["context_lengths"],
        }
    profiles = {}
    for mode in ("sparse", "exact"):
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
        cost += observed * row["exact_worker_cost_reduction_percent"] / 100
    sources.update({
        str(path.resolve()): sha256_file(path)
        for path in (plan_path, inputs_path, accounting, Path(__file__))
    })
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(), "by_width": report,
        "numerical": {
            "requests": len(numerical),
            "all_output_sequences_equal": all(row["all_token_ids_equal"] for row in numerical),
            "different_sequences": sum(not row["all_token_ids_equal"] for row in numerical),
            "rows": numerical,
        },
        "decode_cuda_profiles": profiles,
        "control_width8_cost_change_percent": report["8"]["exact_worker_cost_reduction_percent"],
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
            "A width8 control uses the same graph size in both modes; it observes cross-start drift without correcting it away.",
            "Static reweighting applies a few captured context examples to historical width totals. It is a screen, not an end-to-end forecast.",
            "Kernel groups overlap, including programmatic dependent launches; sums are not exclusive execution or removable JCT.",
            "Numerical equality here covers the bounded greedy probes, not every future request or full replay.",
            "No workload arrivals, Full policy or official comparison result is changed.",
        ],
        "inputs_sha256": sources, "GPU_action": False,
        "performance_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("root", "accounting", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve earlier cost evidence")
    result = compare(args.root, args.accounting)
    write(args.output, result)
    print(json.dumps({
        "by_width": result["by_width"],
        "all_output_sequences_equal": result["numerical"]["all_output_sequences_equal"],
        "sample_reweighting": result["sample_reweighting"],
    }))


if __name__ == "__main__":
    main()
