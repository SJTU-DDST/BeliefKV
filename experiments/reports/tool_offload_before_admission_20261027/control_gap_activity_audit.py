#!/usr/bin/env python3
"""Distinguish other-worker coverage from gaps outside all recorded workers."""

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys

from control_regression_audit import HERE, ROOT, SOURCES, load, require, sealed_rows

sys.path.insert(0, str(ROOT))
from scripts.audit_decode_transfer_envelopes import EnvelopeIndex


OUTPUT = HERE / "control_gap_activity_audit.json"


def audit(service, paths):
    arm = Path(service["arm"])
    spans, first, sample_count = [], {}, 0
    clock = service["work"]["clock"]["monotonic_clock_domain"]
    for row in sealed_rows(
        arm / "server/runtime_audit.jsonl",
        service["input_jsonl_prefixes"][str(arm / "server/runtime_audit.jsonl")],
        (b'"gpu_service_sample"',),
    ):
        require(row["monotonic_clock_domain"] == clock, "changed worker clock")
        spans.append((row["service_start_monotonic_ms"], row["complete_monotonic_ms"]))
        sample_count += 1
        for member in row["request_samples"]:
            request_id = member["request_id"]
            require(request_id in service["requests"], "unknown worker request")
            first[request_id] = min(
                first.get(request_id, math.inf), row["launch_monotonic_ms"],
            )
    index, rows = EnvelopeIndex(spans), {}
    receipt_path = arm / "replay/native_requests.jsonl"
    for receipt in sealed_rows(
        receipt_path, service["input_jsonl_prefixes"][str(receipt_path)],
        (b'"native_receipt"',),
    ):
        request_id = receipt["request_id"]
        require(request_id not in rows, "duplicate receipt")
        request = service["requests"][request_id]
        require(receipt["native_receipt"]["clock_domain"] == clock, "changed receipt clock")
        require(receipt["exact_tokens_verified"] is True, "unverified receipt")
        end = receipt["native_complete_monotonic_ms"]
        begin = first[request_id]
        lifetime = request["first_launch_to_complete_ms"]
        require(math.isclose(end - begin, lifetime, abs_tol=.001), "changed lifetime")
        covered = index.overlap_ms(begin, end)
        own = request["member_worker_interval_union_ms"]
        other, outside = covered - own, lifetime - covered
        require(other >= -.01 and outside >= -.01, "invalid worker coverage")
        require(math.isclose(
            other + outside, request["outside_member_worker_intervals_ms"], abs_tol=.001,
        ), "outside-member coverage does not reconcile")
        rows[request_id] = {
            "cohort": request["cohort"],
            "outside_member_during_other_worker_ms": other,
            "outside_global_worker_intervals_ms": outside,
        }
    require(rows.keys() == service["requests"].keys(), "incomplete receipt coverage")
    ids = [
        request_id for workflow in paths["workflows"].values()
        for request_id in workflow["critical_path_requests"]
    ]
    populations = {
        "all_critical_requests": ids,
        "critical_tool_successors": [
            request_id for request_id in ids if rows[request_id]["cohort"] == "post_tool"
        ],
    }
    return {
        "arm": str(arm), "worker_samples": sample_count,
        "global_worker_union_ms": index.prefix_ms[-1],
        "critical_partitions": {
            name: {
                "requests": len(selected),
                "workflow_equal_weight_s": {
                    metric.removesuffix("_ms") + "_s": math.fsum(
                        rows[request_id][metric] for request_id in selected
                    ) / 18000. for metric in (
                        "outside_member_during_other_worker_ms",
                        "outside_global_worker_intervals_ms",
                    )
                },
            } for name, selected in populations.items()
        },
    }


def main():
    require(not OUTPUT.exists(), "preserve previous audit")
    comparison = load(HERE / "recent_comparison.json")
    control = load(HERE / "control_regression_audit.json")
    require(control["same_exact_work_and_same_critical_request_chains_verified"], "work changed")
    result = {}
    for label, service_name, path_name, arm_label in (
        ("before", "recent_preadmission_service_interleaving.json",
         "complete_jct_paths.json", "Full"),
        ("latest", "recent_grouped_and_curve_service_interleaving.json",
         "recent_grouped_and_curve_jct_paths.json", "GroupedFull"),
    ):
        inputs = []
        for name in (service_name, path_name):
            path = HERE / name
            inputs.append(load(path, comparison["inputs_sha256"][str(path)]))
        result[label] = audit(inputs[0]["arms"][arm_label], inputs[1]["arms"][arm_label])
        require(
            result[label]["arm"] == control["arms"][label]["arm"],
            "historical arm changed",
        )
    deltas = {
        population: {
            metric: result["latest"]["critical_partitions"][population][
                "workflow_equal_weight_s"
            ][metric] - value
            for metric, value in before["workflow_equal_weight_s"].items()
        } for population, before in result["before"]["critical_partitions"].items()
    }
    for population, values in deltas.items():
        require(math.isclose(
            math.fsum(values.values()),
            control["critical_delta_workflow_equal_weight_s"][population][
                "outside_member_worker_intervals_s"
            ], abs_tol=1e-6,
        ), "gap delta does not reproduce control audit")
    sources = (
        Path(__file__), HERE / "control_regression_audit.py",
        ROOT / "scripts/audit_decode_transfer_envelopes.py",
    )
    report = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "arms": result, "critical_delta_workflow_equal_weight_s": deltas,
        "sources_sha256": {
            **SOURCES,
            **{str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources},
        },
        "gpu_experiment_started": False, "production_code_changed": False,
        "isolated_control_cost_attribution": False,
        "limitations": [
            "Global worker union uses native service intervals, not CUDA kernel occupancy.",
            "Other-worker coverage can include runtime or waiting inside those intervals.",
            "Outside-global-worker gaps can include CPU work, dispatch, overlap accounting or waits.",
            "These descriptive interval intersections do not identify removable CPU or JCT time.",
            "All recorded workers are retained; union prevents counting overlapping workers twice.",
        ],
    }
    with OUTPUT.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"output": str(OUTPUT), "critical_deltas_s": deltas}, indent=2))


if __name__ == "__main__":
    main()
