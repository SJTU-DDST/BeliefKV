"""Inspect complete prefill geometry and already recorded CPU/stream samples."""

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.analyze_prefill_turnover import analyze_samples, summarize
from scripts.analyze_prefill_overlap_neighbors import analyze as analyze_neighbors
from scripts.analyze_prefill_overlap_neighbors import key as service_key
from scripts.audit_restore_transition_phases import restore_phase_groups
from scripts.audit_sealed_wait_prefetch import SealedRecords


def inspect(arm):
    reader = SealedRecords()
    native = list(reader(arm / "server/runtime_events.sglang.jsonl"))
    submits = {
        row["attributes"]["request_id"]: row
        for row in native if row.get("kind") == "llm_submit"
    }
    probes, prefills, waits, services = [], [], [], {}
    for row in reader(arm / "server/runtime_audit.jsonl"):
        event = row.get("event")
        if event in ("prefill_turnover_sample", "prefill_turnover_incomplete"):
            probes.append(row)
        elif event == "gpu_restore_dependency_wait":
            waits.append(row)
        elif event == "gpu_service_sample":
            identity = service_key(row)
            if identity is None or identity in services:
                raise ValueError("invalid or duplicate native worker identity")
            services[identity] = [row]
            if row["phase"] == "prefill":
                if any(item["request_id"] not in submits for item in row["request_samples"]):
                    raise ValueError("prefill request has no exact native submission")
                prefills.append(row)
    if not prefills:
        raise ValueError("missing complete prefill observations")
    paired = analyze_samples(
        probes, prefills, waits, begin=0.,
        end=max(row["complete_ts_ms"] for row in prefills) + 1.,
    )
    neighbors = analyze_neighbors(paired["samples"], services)
    restores = restore_phase_groups(paired["samples"], native)
    geometry = Counter()
    request_geometry = defaultdict(Counter)
    for row in prefills:
        geometry["batches"] += 1
        geometry["member_observations"] += len(row["request_samples"])
        geometry["worker_ms"] += row["service_elapsed_ms"]
        for item in row["request_samples"]:
            extend, prefix = item["token_delta"], item["sequence_tokens_before"]
            if type(extend) is not int or extend < 0 or type(prefix) is not int or prefix < 0:
                raise ValueError("invalid actual prefill geometry")
            geometry["extend_tokens"] += extend
            geometry["prefix_tokens"] += prefix
            request_geometry[item["request_id"]]["chunks"] += 1
            request_geometry[item["request_id"]]["extend_tokens"] += extend
    for row in paired["samples"]:
        host = [
            submits[rid]["attributes"].get("cached_tokens_host")
            for rid in row["request_ids"]
        ]
        status = (
            "unknown" if any(type(value) is not int or value < 0 for value in host)
            else "positive_host" if any(host) else "zero_host"
        )
        row["native_request_host_class"] = status
    for path, seal in reader.prefixes.items():
        if Path(path).stat().st_size != seal["complete_prefix_bytes"] or (
            sha256_file(Path(path)) != seal["complete_prefix_sha256"]
        ):
            raise ValueError("complete prefill input changed during inspection")
    return {
        "arm": str(arm), "complete_prefill_geometry": dict(geometry),
        "request_geometry": {rid: dict(values) for rid, values in request_geometry.items()},
        "sampled_phases": paired, "sampled_neighbors": neighbors,
        "sampled_host_counter_associations": restores,
        "input_jsonl_prefixes": reader.prefixes,
    }


def compare(left, right):
    grouped = []
    for rows in (left, right):
        bins = defaultdict(list)
        for row in rows:
            group = (
                row["batch_size"], row["extend_tokens"] // 16,
                row["prefix_tokens"] // max(1, row["batch_size"]) // 2048,
                row["loop"], row["graph"], row["native_request_host_class"],
            )
            bins[group].append(row)
        grouped.append(bins)
    result = []
    for minimum in (1, 3):
        common = {
            key: min(len(grouped[0][key]), len(grouped[1][key]))
            for key in grouped[0].keys() & grouped[1].keys()
            if min(len(grouped[0][key]), len(grouped[1][key])) >= minimum
        }
        weight = sum(common.values())
        metrics = {}
        for metric in ("gpu_run_batch_span_ms", "launch_to_processed_completion_ms"):
            means, supported = [], {
                key: count for key, count in common.items()
                if all(metric in row["metrics"] for side in grouped for row in side[key])
            }
            for side in grouped:
                denominator = sum(supported.values())
                means.append(math.fsum(
                    math.fsum(row["metrics"][metric] for row in side[key])
                    / len(side[key]) * count for key, count in supported.items()
                ) / denominator if denominator else None)
            metrics[metric] = {"balanced_mean_ms": means, "common_weight": sum(supported.values())}
        for phase in ("selection", "run_batch", "post_launch_physical", "result_processing"):
            supported = {
                key: count for key, count in common.items()
                if all(phase in row["phases"] for side in grouped for row in side[key])
            }
            denominator = sum(supported.values())
            metrics[phase + "_thread_cpu_ms"] = {
                "balanced_mean_ms": [
                    math.fsum(
                        math.fsum(row["phases"][phase]["thread_cpu_ms"] for row in side[key])
                        / len(side[key]) * count for key, count in supported.items()
                    ) / denominator if denominator else None for side in grouped
                ],
                "common_weight": denominator,
            }
        result.append({
            "minimum_samples_per_arm_per_bin": minimum,
            "common_bins": len(common), "common_sample_weight": weight, "metrics": metrics,
        })
    return result


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError("preserve initial prefill evidence")
    completed_path = ROOT / "experiments/reports/full_only_pressure_index_20261028/complete_jct_paths.json"
    completed = json.loads(completed_path.read_bytes())
    if completed["same_request_work_verified"] is not True:
        raise ValueError("complete matched-work comparison is required")
    arms = {name: inspect(Path(arm["arm"])) for name, arm in completed["arms"].items()}
    baseline = arms["Preadmission"]["sampled_phases"]["samples"]
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Complete prefill work and actual sparse CPU/compute-stream observations.",
        "arms": arms,
        "balanced_prefill_samples_vs_preadmission": {
            name: compare(baseline, arm["sampled_phases"]["samples"])
            for name, arm in arms.items() if name != "Preadmission"
        },
        "inputs_sha256": {str(completed_path): sha256_file(completed_path)},
        "operator_sources_sha256": {
            str(path): sha256_file(path) for path in (
                Path(__file__), ROOT / "scripts/analyze_prefill_turnover.py",
                ROOT / "scripts/analyze_prefill_overlap_neighbors.py",
                ROOT / "scripts/audit_restore_transition_phases.py",
                ROOT / "scripts/audit_sealed_wait_prefetch.py",
            )
        },
        "performance_accepted": False,
        "limitations": [
            "Sparse samples are not extrapolated to whole-run CPU or critical-path losses.",
            "Thread CPU, stream spans and worker intervals overlap and are not additive.",
            "Host counters describe the request's initial accounting, not every chunk's actual DMA.",
            "No observed restore-wait sample is not evidence of zero GPU dependency waiting.",
            "Balanced bins contain different actual requests and do not establish causal effects.",
            "All complete prefills and normal workflow tails are retained outside matched diagnostics.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        "geometry": {name: arm["complete_prefill_geometry"] for name, arm in arms.items()},
        "balanced_samples": result["balanced_prefill_samples_vs_preadmission"],
    }, indent=2))


if __name__ == "__main__":
    main()
