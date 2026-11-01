"""Reweight disjoint, numerically matched native execution-component comparisons."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.train_workflow_work import write


def merge(paths, accounting):
    geometry = json.loads(accounting.read_bytes())
    accounting = accounting.resolve()
    expected_accounting = sha256_file(accounting)
    combined, numerical, source_rows, native_states, input_geometry = {}, [], [], set(), set()
    labels = set()
    for path in paths:
        report = json.loads(path.read_bytes())
        labels.add(report["candidate_label"])
        if report["inputs_sha256"].get(str(accounting)) != expected_accounting:
            raise ValueError("comparisons must use the same immutable workload accounting")
        if not report["numerical"]["all_output_sequences_equal"]:
            raise ValueError("preserve a numerical mismatch separately from matched cost evidence")
        overlap = combined.keys() & report["by_width"].keys()
        if overlap:
            raise ValueError("comparison widths overlap: " + str(sorted(overlap)))
        plans = [
            Path(filename) for filename in report["inputs_sha256"]
            if Path(filename).name == "plan.json"
        ]
        if not plans:
            raise ValueError("comparison is missing pinned native plans")
        for plan_path in plans:
            if sha256_file(plan_path) != report["inputs_sha256"][str(plan_path)]:
                raise ValueError("native component plan changed")
            plan = json.loads(plan_path.read_bytes())
            native_states.add(json.dumps(plan["native"], sort_keys=True))
            input_path = Path(plan["inputs"])
            if sha256_file(input_path) != plan["inputs_sha256"]:
                raise ValueError("native component inputs changed")
            inputs = json.loads(input_path.read_bytes())
            input_geometry.add(json.dumps({
                key: inputs[key] for key in (
                    "device_full_tokens", "engine_tree", "capture_buckets", "sparse_buckets",
                )
            }, sort_keys=True))
        combined.update(report["by_width"])
        numerical.extend(report["numerical"]["rows"])
        source_rows.append({
            "analysis": str(path.resolve()), "sha256": sha256_file(path),
            "widths": sorted(map(int, report["by_width"])),
            "requests": report["numerical"]["requests"],
        })
    if len(labels) != 1 or len(native_states) != 1 or len(input_geometry) != 1:
        raise ValueError("candidate, native source or execution geometry differs across comparisons")
    identities = [row["request"] for row in numerical]
    if len(identities) != len(set(identities)):
        raise ValueError("component request identities were counted more than once")
    total = geometry["scopes"]["common_window"]["worker_ms"]
    decode = geometry["phase_costs"]["window:decode"]["worker_ms"]
    covered, changed = 0., 0.
    for width, row in combined.items():
        observed = geometry["common_window_decode_by_width"][width]["worker_ms"]
        covered += observed
        changed += observed * row["candidate_worker_cost_reduction_percent"] / 100
    return {
        "candidate_label": labels.pop(),
        "by_width": {key: combined[key] for key in sorted(combined, key=int)},
        "numerical": {
            "requests": len(numerical), "output_tokens": sum(row["tokens"] for row in numerical),
            "all_output_sequences_equal": True, "rows": numerical,
        },
        "sample_reweighting": {
            "covered_common_window_worker_fraction": covered / total,
            "covered_common_window_decode_worker_fraction": covered / decode,
            "estimated_worker_time_change_seconds": changed / 1000,
            "estimated_total_worker_reduction_percent": 100 * changed / total,
            "unmeasured_widths_treated_as_unchanged": True,
            "represents_JCT_or_a_causal_savings_bound": False,
        },
        "source_comparisons": source_rows,
        "execution_geometry": json.loads(input_geometry.pop()),
        "accounting": str(accounting), "accounting_sha256": expected_accounting,
        "script_sha256": sha256_file(Path(__file__)),
        "semantics": [
            "Width groups are disjoint and keep their own captured contexts and server startup populations.",
            "Comparisons share native sources, physical Device capacity and graph bucket definitions.",
            "This combines separate component measurements; it is not a new serving run or independent replication.",
            "Unprofiled burst medians are reweighted against old worker intervals, without a JCT simulation.",
            "No confidence interval or whole-workflow performance gain is inferred.",
        ],
        "GPU_action": False, "performance_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparisons", type=Path, nargs="+", required=True)
    parser.add_argument("--accounting", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve earlier aggregate evidence")
    result = merge(args.comparisons, args.accounting)
    write(args.output, result)
    print(json.dumps({
        "widths": list(result["by_width"]), "numerical": {
            key: value for key, value in result["numerical"].items() if key != "rows"
        },
        "sample_reweighting": result["sample_reweighting"],
    }))


if __name__ == "__main__":
    main()
