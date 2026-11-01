"""Screen resident compute sharing without treating frontier latency as JCT."""

from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
from scipy.optimize import nnls

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_native_system_costs import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write


def geometry(contexts, buckets):
    if not contexts or any(type(value) is not int or value < 0 for value in contexts):
        raise ValueError("require actual positive-width decode geometry")
    if not buckets or tuple(sorted(set(buckets))) != tuple(buckets):
        raise ValueError("require the observed increasing graph buckets")
    slot = bisect_left(buckets, len(contexts))
    if slot == len(buckets):
        raise ValueError("batch exceeds observed graph support")
    return (
        1., len(contexts) / 24., buckets[slot] / 24.,
        sum(contexts) / 100000., max(contexts) / 100000.,
    )


def two_class_savings(short_workflows, long_workflows, mixed_ms, short_ms, long_ms, steps=1):
    """Exact sum-of-completions difference for two homogeneous closed cohorts."""
    if min(short_workflows, long_workflows, steps) <= 0 or min(
        mixed_ms, short_ms, long_ms,
    ) <= 0:
        raise ValueError("two nonempty cohorts and positive costs are required")
    return steps * (
        (short_workflows + long_workflows) * (mixed_ms - short_ms)
        - long_workflows * long_ms
    )


def segment_ms(contexts, steps, coefficients, buckets):
    if type(steps) is not int or steps <= 0:
        raise ValueError("a decode segment needs positive integral progress")
    first = np.dot(geometry(contexts, buckets), coefficients)
    last = np.dot(geometry([value + steps - 1 for value in contexts], buckets), coefficients)
    return float((first + last) * steps / 2.)


def simulate_frontier(members, coefficients, buckets, *, extra_short_every=0, short_first=False):
    """Keep current resident members only; do not invent successors or arrivals."""
    if extra_short_every and short_first or extra_short_every < 0:
        raise ValueError("select one compute-sharing variant")
    if len({item["request_id"] for item in members}) != len(members) or not members:
        raise ValueError("frontier request identities must be nonempty and unique")
    if any(
        type(item["remaining"]) is not int or item["remaining"] <= 0
        or type(item["context"]) is not int or item["context"] < 0
        or type(item["short"]) is not bool
        for item in members
    ):
        raise ValueError("future output is required only for this offline screen")
    live = [dict(item) for item in members]
    initial = {item["request_id"]: item["remaining"] for item in live}
    generated, finishes, full_steps, wall = Counter(), {}, 0, 0.
    while live:
        short = [item for item in live if item["short"]]
        long = [item for item in live if not item["short"]]
        extra = bool(
            short and long and (short_first or extra_short_every and full_steps >= extra_short_every)
        )
        selected = short if extra else live
        steps = min(item["remaining"] for item in selected)
        if extra_short_every and short and long:
            steps = 1 if extra else min(steps, extra_short_every - full_steps)
        wall += segment_ms([item["context"] for item in selected], steps, coefficients, buckets)
        if extra_short_every and short and long:
            full_steps = 0 if extra else full_steps + steps
        for item in selected:
            item["context"] += steps
            item["remaining"] -= steps
            generated[item["request_id"]] += steps
            if not item["remaining"]:
                finishes[item["request_id"]] = wall
        live = [item for item in live if item["remaining"]]
    if dict(generated) != initial or finishes.keys() != initial.keys():
        raise ValueError("compute partition lost or duplicated output work")
    frontier = defaultdict(float)
    for item in members:
        frontier[item["workflow_id"]] = max(
            frontier[item["workflow_id"]], finishes[item["request_id"]],
        )
    return {
        "frontier_workflow_finish_ms": dict(frontier), "request_finish_ms": finishes,
        "frontier_workflow_mean_ms": statistics.mean(frontier.values()),
        "drain_ms": wall, "output_tokens": sum(initial.values()),
    }


def fit_cost(samples, buckets):
    # Median geometry cells keep repeated tokens from posing as independent tasks.
    cells = defaultdict(list)
    for row in samples:
        if row["split"] == 0:
            continue
        key = row["width"], round(row["sum_context"] / 32768.), round(row["max_context"] / 32768.)
        cells[key].append(row)
    if len(cells) < 20:
        raise ValueError("insufficient distinct geometry cells for a timing screen")
    x, y = [], []
    for members in cells.values():
        x.append(np.median(np.array([row["features"] for row in members]), axis=0))
        y.append(statistics.median(row["worker_ms"] for row in members))
    coefficients, residual = nnls(np.array(x), np.array(y))
    if not np.all(np.isfinite(coefficients)) or min(coefficients) < 0 or sum(coefficients) <= 0:
        raise ValueError("invalid monotone worker cost")
    held = [row for row in samples if row["split"] == 0]
    errors = [
        abs(float(np.dot(row["features"], coefficients)) - row["worker_ms"]) / row["worker_ms"]
        for row in held
    ]
    return coefficients, {
        "features": ["intercept", "width/24", "graph_bucket/24", "context_sum/1e5", "max_context/1e5"],
        "coefficients_ms": coefficients.tolist(), "median_geometry_cells": len(cells),
        "training_worker_samples": len(samples) - len(held), "held_out_worker_samples": len(held),
        "held_out_absolute_relative_error": distribution(errors),
        "training_cell_residual_l2": float(residual),
        "same_request_set_kept_in_one_split": True,
        "held_out_request_sets_can_share_workflows": True,
        "worker_model_is_not_a_kernel_or_causal_performance_model": True,
    }


def membership_split(members):
    raw = "\n".join(sorted(item["request_id"] for item in members)).encode("ascii")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "little") % 5


def inspect(registration, cohort_path, assessment_path, critical_path, *, maximum_frontiers):
    plan = json.loads(registration.read_bytes())
    cohort = json.loads(cohort_path.read_bytes())
    assessment = json.loads(assessment_path.read_bytes())
    paths = json.loads(critical_path.read_bytes())
    arm = Path(plan["run_root"]) / "full"
    sizes = {row["workflow_id"]: row["model_work_units"] for row in assessment["workflows"]}
    if set(sizes) != set(plan["workflow_ids"]):
        raise ValueError("work-size inventory differs from the selected population")
    measured = set(cohort["measured_workflow_ids"])
    if measured != set(paths["critical_path"]["workflows"]):
        raise ValueError("critical paths differ from the frozen measurement cohort")
    threshold = statistics.median(sizes.values())
    reader = SealedRecords()
    requests = {row["request_id"]: row for row in reader(arm / "replay/native_requests.jsonl")}
    info = json.loads((arm / "server/server_info.json").read_bytes())
    if info.get("enable_mixed_chunk") or info.get("speculative_algorithm") is not None:
        raise ValueError("screen supports the selected ordinary non-mixed decode only")
    buckets = info["cuda_graph_config"]["decode"]["bs"]
    samples, frontiers, seen_sets = [], [], set()
    by_width, pair, missing_receipts, output_ends = defaultdict(list), Counter(), set(), {}
    previous_forward, observed_samples = -1, 0
    for row in reader(arm / "server/runtime_audit.jsonl"):
        if row.get("event") != "gpu_service_sample":
            continue
        observed_samples += 1
        if row["forward_iter"] <= previous_forward:
            raise ValueError("service forwards are not in execution order")
        previous_forward = row["forward_iter"]
        for item in row["request_samples"]:
            rid = item["request_id"]
            before, after = item["output_tokens_reported_before"], item["output_tokens_after"]
            if (
                before != output_ends.get(rid, 0)
                or after - before != item["output_token_delta"]
            ):
                raise ValueError("native output progress is not conserved")
            output_ends[rid] = after
        if row["phase"] != "decode":
            continue
        members = row["request_samples"]
        elapsed = row["service_elapsed_ms"]
        if (
            type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed <= 0
            or not row["cuda_graph_replayed"] or len(members) != row["decode_batch_size"]
        ):
            pair["unsupported_decode_rows"] += 1
            continue
        contexts = [item["sequence_tokens_before"] for item in members]
        features = geometry(contexts, buckets)
        split = membership_split(members)
        samples.append({
            "features": features, "width": len(members), "worker_ms": elapsed, "split": split,
            "sum_context": sum(contexts), "max_context": max(contexts),
        })
        by_width[len(members)].append(elapsed)
        groups = {sizes[item["workflow_id"]] <= threshold for item in members}
        pair["graph_decode_rows"] += 1
        pair["graph_decode_worker_us"] += elapsed * 1000.
        if len(groups) < 2:
            continue
        pair["short_long_mixed_decode_rows"] += 1
        pair["short_long_mixed_decode_worker_us"] += elapsed * 1000.
        pair["mixed_rows_with_measured_member"] += any(item["workflow_id"] in measured for item in members)
        key = tuple(sorted(item["request_id"] for item in members))
        if key in seen_sets:
            continue
        seen_sets.add(key)
        if not all(item["request_id"] in requests for item in members):
            pair["mixed_frontiers_with_missing_receipts"] += 1
            missing_receipts.update(item["request_id"] for item in members if item["request_id"] not in requests)
            continue
        if any(
            requests[item["request_id"]]["output_tokens"] <= item["output_tokens_reported_before"]
            for item in members
        ):
            pair["mixed_frontiers_with_already_completed_members"] += 1
            continue
        items = []
        for member in members:
            receipt = requests[member["request_id"]]
            if (
                tuple(receipt["native_receipt"]["identity"])
                != tuple(member[name] for name in (
                    "workflow_id", "invocation_id", "context_id", "context_epoch",
                ))
            ):
                raise ValueError("batch member and request receipt have different owners")
            items.append({
                "request_id": member["request_id"], "workflow_id": member["workflow_id"],
                "remaining": receipt["output_tokens"] - member["output_tokens_reported_before"],
                "context": member["sequence_tokens_before"],
                "short": sizes[member["workflow_id"]] <= threshold,
            })
        frontiers.append({"forward_iter": row["forward_iter"], "members": items})
    status = json.loads((arm / "server/native_telemetry_status.json").read_bytes())
    if (
        status.get("writer_error") is not None or status["dropped_records"] or status["failed_records"]
        or observed_samples != status["background_writer_timing"]["categories"]["audit:gpu_service_sample"]["records_seen"]
    ):
        raise ValueError("incomplete native worker record")
    if any(output_ends.get(rid) != row["output_tokens"] for rid, row in requests.items()):
        raise ValueError("complete native outputs differ from recorded worker progress")
    measured_requests = [row for row in requests.values() if row["native_receipt"]["identity"][0] in measured]
    if len(measured_requests) != 2819 or sum(row["output_tokens"] for row in measured_requests) != 362072:
        raise ValueError("selected measured work changed")
    coefficients, cost = fit_cost(samples, buckets)
    count = min(maximum_frontiers, len(frontiers))
    indices = sorted({round(index * (len(frontiers) - 1) / max(1, count - 1)) for index in range(count)})
    simulations = []
    for index in indices:
        row = frontiers[index]
        members = row["members"]
        baseline = simulate_frontier(members, coefficients, buckets)
        variants = {
            "short_first": simulate_frontier(members, coefficients, buckets, short_first=True),
            "one_extra_short_per_8_full": simulate_frontier(
                members, coefficients, buckets, extra_short_every=8,
            ),
            "one_extra_short_per_32_full": simulate_frontier(
                members, coefficients, buckets, extra_short_every=32,
            ),
        }
        simulations.append({
            "forward_iter": row["forward_iter"], "requests": len(members),
            "workflows": len({item["workflow_id"] for item in members}),
            "baseline": baseline, "variants": variants,
        })
    variant_summary = {}
    for name in ("short_first", "one_extra_short_per_8_full", "one_extra_short_per_32_full"):
        gains, losses = [], []
        for row in simulations:
            before, after = row["baseline"], row["variants"][name]
            gains.append(100 * (1 - after["frontier_workflow_mean_ms"] / before["frontier_workflow_mean_ms"]))
            losses.append(100 * (after["drain_ms"] / before["drain_ms"] - 1))
        variant_summary[name] = {
            "closed_frontier_mean_finish_gain_percent": distribution(gains),
            "closed_frontier_drain_time_increase_percent": distribution(losses),
            "frontiers_with_positive_finish_gain_and_at_most_2pct_drain_increase": sum(
                gain > 0 and loss <= 2 for gain, loss in zip(gains, losses)
            ),
            "sampled_closed_frontiers": len(simulations),
        }
    strata = {}
    for label, accept in (
        ("short_half", lambda value: value <= threshold),
        ("long_half", lambda value: value > threshold),
    ):
        rows = [
            (workflow, row) for workflow, row in paths["workflows"].items()
            if accept(sizes[workflow])
        ]
        strata[label] = {
            "measured_workflows": len(rows),
            "means_s": {
                name.removesuffix("_ms"): statistics.mean(row[name] for _, row in rows) / 1000.
                for name in next(iter(paths["workflows"].values())) if name.endswith("_ms")
            },
        }
    for filename, prefix in reader.prefixes.items():
        if Path(filename).stat().st_size != prefix["complete_prefix_bytes"]:
            raise ValueError("completed analysis input changed length")
        if sha256_file(Path(filename)) != prefix["complete_prefix_sha256"]:
            raise ValueError("completed analysis input changed contents")
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(), "registration": str(registration),
        "population_model_work": assessment["summary"]["model_work"],
        "short_class_definition": "whole-workflow intrinsic model work at or below the fixed population median",
        "median_model_work_units": threshold, "measured_strata": strata,
        "observed_decode": dict(pair), "graph_worker_ms_by_actual_width": {
            str(width): distribution(values) for width, values in sorted(by_width.items())
        },
        "timing_screen": cost, "frontier_variant_summary": variant_summary,
        "distinct_mixed_request_sets": len(seen_sets), "supported_receipt_frontiers": len(frontiers),
        "distinct_requests_missing_complete_receipts": len(missing_receipts),
        "frontier_simulations": simulations,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            registration, cohort_path, assessment_path, critical_path,
            arm / "server/server_info.json", arm / "server/native_telemetry_status.json",
            Path(__file__), ROOT / "scripts/audit_sealed_wait_prefetch.py",
        )},
        "input_jsonl_prefixes": reader.prefixes,
        "scope": [
            "All resident members were observed together in an actual decode forward.",
            "Original fixed measurement cohort, complete requests, outputs and arrival table are unchanged.",
            "Future output lengths and whole-workflow sizes are used only in this offline screen.",
            "No online policy or future-size predictor is enabled or modified.",
            "Closed frontier max is not an observed JOIN or complete-workflow JCT.",
            "Per-frontier changes are correlated, cannot be summed and do not simulate future arrivals, tools or KV growth pressure.",
            "The monotone worker model can extrapolate to unseen subset geometry; held-out errors are screening evidence only.",
            "Keeping current KV avoids an added copy here but does not grant capacity for future requests or output growth.",
            "No full-workflow net-benefit estimate or performance acceptance follows from this screen.",
        ],
        "GPU_action": False, "automatic_GPU_followup": False, "performance_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("registration", "cohort", "assessment", "critical-path", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--maximum-frontiers", type=int, default=128)
    args = parser.parse_args()
    if (
        args.output.exists() or not 1 <= args.maximum_frontiers <= 512
        or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        parser.error("preserve outputs and run bounded nice19 CPU-only analysis")
    result = inspect(
        args.registration, args.cohort, args.assessment, args.critical_path,
        maximum_frontiers=args.maximum_frontiers,
    )
    write(args.output, result)
    print(json.dumps({
        "timing_screen": result["timing_screen"], "strata": result["measured_strata"],
        "variants": result["frontier_variant_summary"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
