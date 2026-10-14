#!/usr/bin/env python3
"""Evaluate bounded transfer histories using only receipts before submission."""

from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
import gzip
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.runtime.native_h2d_seed import load_h2d_seed
from beliefkv.runtime.native_transfer_service import (
    NativeServiceSample, estimate_native_service, load_native_service_seed,
    percentile, pool_shape,
)


@dataclass(frozen=True)
class Observation:
    sample: NativeServiceSample
    completed_ms: float | None


class History:
    def __init__(self, *, stratified: bool, max_age_ms: float | None,
                 rich_limit: int, legacy_limit: int, rich_seed, legacy_seed):
        self.stratified = stratified
        self.max_age_ms = max_age_ms
        self.rich_limit = rich_limit
        self.legacy = deque((Observation(NativeServiceSample(*row), None)
                             for row in legacy_seed), maxlen=legacy_limit)
        self.mixed = deque(maxlen=rich_limit)
        self.groups = defaultdict(lambda: deque(maxlen=rich_limit // 8))
        for sample in rich_seed:
            self.add(Observation(sample, None), update_legacy=False)

    def add(self, observation: Observation, *, update_legacy: bool = True):
        sample = observation.sample
        if self.stratified:
            self.groups[(sample.direction, sample.shape)].append(observation)
        else:
            self.mixed.append(observation)
        if update_legacy and sample.direction == "h2d":
            self.legacy.append(Observation(
                NativeServiceSample(sample.size_bytes, sample.submit_to_ack_ms),
                observation.completed_ms,
            ))

    def eligible(self, observations, now_ms: float):
        return [
            row for row in observations
            if row.completed_ms is None or (
                row.completed_ms < now_ms
                and (self.max_age_ms is None or now_ms - row.completed_ms <= self.max_age_ms)
            )
        ]

    def predict(self, size: int, direction: str, shape: str, now_ms: float):
        if self.stratified:
            rows = list(self.groups.get((direction, shape), ()))
            if shape != "unknown":
                rows.extend(self.groups.get((direction, "unknown"), ()))
            else:
                rows = [row for key, group in self.groups.items()
                        if key[0] == direction for row in group]
        else:
            rows = self.mixed
        eligible = self.eligible(rows, now_ms)
        estimate = estimate_native_service(
            (row.sample for row in eligible), size, direction=direction, shape=shape,
        )
        support_path = "typed"
        if estimate is None and direction == "h2d":
            eligible = self.eligible(self.legacy, now_ms)
            estimate = estimate_native_service((row.sample for row in eligible), size)
            support_path = "legacy_unknown_shape"
        if estimate is None:
            return None
        requested_shape = shape if support_path == "typed" else "unknown"
        compatible = [
            row for row in eligible if row.sample.direction == direction
            and (row.sample.shape == requested_shape or requested_shape == "unknown"
                 or row.sample.shape == "unknown")
            and abs(math.log2(size / row.sample.size_bytes)) <= 2.
        ]
        exact = [row for row in compatible if row.sample.shape == requested_shape]
        if len(exact) >= 3:
            compatible = exact
        chosen = sorted(compatible, key=lambda row: abs(
            math.log2(size / row.sample.size_bytes),
        ))[:16]
        assert len(chosen) == estimate.sample_count
        ages = [now_ms - row.completed_ms for row in chosen if row.completed_ms is not None]
        return {
            "p50_ms": estimate.submit_to_ack_p50_ms,
            "p90_ms": estimate.submit_to_ack_p90_ms,
            "sample_count": estimate.sample_count,
            "support": estimate.support, "support_path": support_path,
            "selected_max_age_ms": max(ages) if ages else None,
            "selected_median_age_ms": percentile(ages, .5) if ages else None,
            "selected_untimed_seed_samples": sum(row.completed_ms is None for row in chosen),
        }

    def stored(self):
        return (sum(map(len, self.groups.values())) if self.stratified
                else len(self.mixed)) + len(self.legacy)


def summary(values):
    values = [value for value in values if value is not None]
    if not values:
        return {"count": 0}
    return {
        "count": len(values), "p50": percentile(values, .5),
        "p90": percentile(values, .9), "max": max(values),
    }


def metrics(rows, policy):
    supported = [row for row in rows if row["predictions"][policy] is not None]
    total_bytes = sum(row["size_bytes"] for row in rows)
    return {
        "queries": len(rows), "supported": len(supported),
        "support_fraction": len(supported) / len(rows) if rows else None,
        "supported_byte_fraction": (
            sum(row["size_bytes"] for row in supported) / total_bytes if total_bytes else None
        ),
        "p50_absolute_error_ms": summary(
            abs(row["predictions"][policy]["p50_ms"] - row["actual_ms"]) for row in supported
        ),
        "p50_signed_error_ms": summary(
            row["predictions"][policy]["p50_ms"] - row["actual_ms"] for row in supported
        ),
        "p90_empirical_coverage": (
            sum(row["actual_ms"] <= row["predictions"][policy]["p90_ms"]
                for row in supported) / len(supported) if supported else None
        ),
        "selected_max_age_ms": summary(
            row["predictions"][policy]["selected_max_age_ms"] for row in supported
        ),
        "support_paths": dict(Counter(
            row["predictions"][policy]["support_path"] for row in supported
        )),
    }


def read_prefix(path, metadata):
    checksum, consumed = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while consumed < metadata["complete_prefix_bytes"]:
            line = stream.readline(metadata["complete_prefix_bytes"] - consumed)
            if not line.endswith(b"\n"):
                raise ValueError("sealed transfer prefix lost a complete line")
            consumed += len(line)
            checksum.update(line)
            yield json.loads(line)
    if checksum.hexdigest() != metadata["complete_prefix_sha256"]:
        raise ValueError("sealed transfer prefix changed")


def source_at(commit, path):
    return subprocess.check_output(["git", "show", f"{commit}:{path}"], cwd=ROOT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sealed", required=True, type=Path)
    parser.add_argument("--support-report", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    details = args.output.with_suffix(".rows.json.gz")
    if args.output.exists() or details.exists():
        raise ValueError("preserve prior evaluation evidence")
    sealed_bytes = args.sealed.read_bytes()
    sealed = json.loads(gzip.decompress(sealed_bytes))
    support = json.loads(args.support_report.read_bytes())
    if support["sealed_report_sha256"] != hashlib.sha256(sealed_bytes).hexdigest():
        raise ValueError("support queries use another sealed source")
    arm = Path(sealed["arm"])
    plan_bytes = (arm.parent / "ab_plan.json").read_bytes()
    plan = json.loads(plan_bytes)
    service_path = "beliefkv/runtime/native_transfer_service.py"
    service_source = source_at(plan["code_commit"], service_path)
    if service_source != (ROOT / service_path).read_bytes():
        raise ValueError("evaluation must use the collection estimator unchanged")
    runtime_source = source_at(plan["code_commit"], "beliefkv/runtime/sglang_v0520_runtime.py")
    limits = {}
    for node in ast.walk(ast.parse(runtime_source)):
        if (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Attribute)
            and node.target.attr in ("_native_service_samples", "_h2d_samples")
            and isinstance(node.value, ast.Call)):
            limits[node.target.attr] = ast.literal_eval(next(
                keyword.value for keyword in node.value.keywords if keyword.arg == "maxlen"
            ))
    if limits != support["collection_history_bounds"]:
        raise ValueError("collection history bounds changed")
    rich_seed = load_native_service_seed(
        plan["transfer_service_seed"], plan["transfer_service_seed_sha256"],
    )
    legacy_seed = load_h2d_seed(plan["h2d_seed_artifact"], plan["h2d_seed_sha256"])
    policy_specs = {
        "mixed_fifo": {"stratified": False, "max_age_ms": None},
        "direction_shape_32": {"stratified": True, "max_age_ms": None},
        "direction_shape_32_age120s": {"stratified": True, "max_age_ms": 120_000.},
        "direction_shape_32_age300s": {"stratified": True, "max_age_ms": 300_000.},
    }
    histories = {
        name: History(
            **spec, rich_limit=limits["_native_service_samples"],
            legacy_limit=limits["_h2d_samples"], rich_seed=rich_seed, legacy_seed=legacy_seed,
        ) for name, spec in policy_specs.items()
    }
    supplement_name = "mixed_fifo_plus_recent_full"
    policy_specs[supplement_name] = {
        "base": "mixed_fifo", "supplement": "direction_shape_32_age300s",
        "scope": "H2D FULL-only when the base is unsupported; live timed samples only",
        "additional_maximum_samples": 32,
    }
    policy_names = tuple(policy_specs)
    transfer_path = arm / "server/transfer_telemetry.jsonl"
    metadata = sealed["input_jsonl_prefixes"][str(transfer_path)]
    transfers, skipped, seen = [], Counter(), set()
    for row in read_prefix(transfer_path, metadata):
        size, delay = row.get("actual_bytes"), row.get("submit_to_ack_ms")
        submit, complete = row.get("submit_ts_ms"), row.get("complete_ts_ms")
        if (row.get("status") != "completed" or row.get("direction") not in ("h2d", "d2h")
            or type(size) is not int or size <= 0 or type(delay) not in (int, float)
            or not math.isfinite(delay) or not 0 < delay <= 60_000
            or type(submit) not in (int, float) or type(complete) not in (int, float)
            or not math.isfinite(submit) or not math.isfinite(complete) or complete < submit):
            skipped["ineligible"] += 1
            continue
        identity = row.get("command_id")
        if not identity or identity in seen:
            raise ValueError("missing or duplicate controller receipt identity")
        seen.add(identity)
        units = row.get("num_tokens_by_pool") or {}
        sample = NativeServiceSample(
            size, delay, row["direction"],
            pool_shape(units.get("kv", units.get("full", 0)), units.get("mamba", 0)),
            row.get("enqueue_to_submit_ms"),
        )
        transfers.append({
            "command_id": identity, "submit_ms": submit, "complete_ms": complete,
            "sample": sample, "origin": row.get("telemetry_origin"),
            "tagged_controlled": bool(row.get("tagged_child_commits")),
        })
    if not transfers:
        raise ValueError("no eligible receipts")
    completed_order = sorted(transfers, key=lambda row: row["complete_ms"])
    queries = [
        (row["submit_ms"], "held_out_transfer", row) for row in transfers
    ] + [(row["ts_ms"], "service_rejection", row) for row in support["rows"]]
    queries.sort(key=lambda item: item[0])
    cutoff = (min(row["submit_ms"] for row in transfers)
              + max(row["submit_ms"] for row in transfers)) / 2.
    index, forecast_rows, rejection_rows = 0, [], []
    cpu_us = defaultdict(list)
    memory_max = {name: history.stored() for name, history in histories.items()}
    for timestamp, kind, row in queries:
        while index < len(completed_order) and completed_order[index]["complete_ms"] < timestamp:
            previous = completed_order[index]
            observation = Observation(previous["sample"], previous["complete_ms"])
            for name, history in histories.items():
                history.add(observation)
                memory_max[name] = max(memory_max[name], history.stored())
            index += 1
        if kind == "held_out_transfer":
            size, direction, shape = (row["sample"].size_bytes, row["sample"].direction,
                                      row["sample"].shape)
        else:
            size, direction, shape = sum(row["planned_pool_bytes"].values()), "h2d", row["shape"]
        predictions = {}
        for name, history in histories.items():
            started = time.thread_time_ns()
            predictions[name] = history.predict(size, direction, shape, timestamp)
            cpu_us[name].append((time.thread_time_ns() - started) / 1000.)
        predictions[supplement_name] = predictions["mixed_fifo"]
        fallback = predictions["direction_shape_32_age300s"]
        if (predictions[supplement_name] is None and direction == "h2d" and shape == "full"
            and fallback is not None and fallback["support_path"] == "typed"
            and fallback["selected_untimed_seed_samples"] == 0):
            predictions[supplement_name] = fallback
        result = {
            "timestamp_ms": timestamp, "direction": direction, "shape": shape,
            "size_bytes": size, "predictions": predictions,
        }
        if kind == "held_out_transfer":
            forecast_rows.append({
                **result, "command_id": row["command_id"],
                "actual_ms": row["sample"].submit_to_ack_ms, "origin": row["origin"],
                "tagged_controlled": row["tagged_controlled"],
                "split": "later_time_half" if timestamp >= cutoff else "earlier_time_half",
            })
        else:
            rejection_rows.append({
                **result, **{key: row.get(key) for key in (
                    "workflow_id", "context_id", "context_epoch", "join_id", "source",
                )},
            })
    groups = {"all": forecast_rows}
    groups.update({
        f"{direction}:{shape}": [
            row for row in forecast_rows if row["direction"] == direction and row["shape"] == shape
        ] for direction, shape in sorted({
            (row["direction"], row["shape"]) for row in forecast_rows
        })
    })
    groups.update({
        split: [row for row in forecast_rows if row["split"] == split]
        for split in ("earlier_time_half", "later_time_half")
    })
    for split in ("earlier_time_half", "later_time_half"):
        groups[f"{split}:h2d:full"] = [
            row for row in groups[split] if (row["direction"], row["shape"]) == ("h2d", "full")
        ]
    comparisons = {}
    for name in policy_names:
        if name == "mixed_fifo":
            continue
        comparisons[name] = {}
        for group, values in groups.items():
            matched = [row for row in values if row["predictions"][name] is not None
                       and row["predictions"]["mixed_fifo"] is not None]
            recovered = [row for row in values if row["predictions"][name] is not None
                         and row["predictions"]["mixed_fifo"] is None]
            lost = [row for row in values if row["predictions"][name] is None
                    and row["predictions"]["mixed_fifo"] is not None]
            comparisons[name][group] = {
                "matched_current": metrics(matched, "mixed_fifo"),
                "matched_candidate": metrics(matched, name),
                "newly_supported": metrics(recovered, name),
                "lost_current_support": len(lost),
            }
    details.parent.mkdir(parents=True, exist_ok=True)
    raw_details = json.dumps({
        "transfer_predictions": forecast_rows, "service_rejection_queries": rejection_rows,
    }, separators=(",", ":"), allow_nan=False).encode()
    details.write_bytes(gzip.compress(raw_details, mtime=0))
    report = {
        "status": "causal_receipt_timing_evaluation_not_callback_replay_or_policy_gain",
        "observed_at_local": datetime.now().astimezone().isoformat(),
        "collection_code_commit": plan["code_commit"],
        "sealed_report": str(args.sealed.resolve()),
        "sealed_report_sha256": hashlib.sha256(sealed_bytes).hexdigest(),
        "plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
        "support_report": str(args.support_report.resolve()),
        "support_report_sha256": hashlib.sha256(args.support_report.read_bytes()).hexdigest(),
        "transfer_source": {**metadata, "path": str(transfer_path)},
        "estimator_sha256": hashlib.sha256(service_source).hexdigest(),
        "runtime_sha256": hashlib.sha256(runtime_source).hexdigest(),
        "helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "collection_history_bounds": limits,
        "policy_specs": policy_specs, "maximum_stored_samples": memory_max,
        "supplement_stored_sample_bound": memory_max["mixed_fifo"] + 32,
        "uncontrolled_thread_cpu_per_prediction_us": {
            name: summary(values) for name, values in cpu_us.items()
        },
        "split_time_ms": cutoff, "eligible_completed_receipts": len(transfers),
        "skipped": dict(skipped),
        "metrics": {name: {group: metrics(values, name) for group, values in groups.items()}
                    for name in policy_names},
        "paired_comparisons": comparisons,
        "rejection_query_support": {
            name: sum(row["predictions"][name] is not None for row in rejection_rows)
            for name in policy_names
        },
        "rejection_queries": len(rejection_rows),
        "detailed_predictions": str(details.resolve()),
        "detailed_predictions_sha256": hashlib.sha256(details.read_bytes()).hexdigest(),
        "limitations": [
            "Each query uses only completed receipts strictly before submit/query; its own and in-flight labels are excluded.",
            "Receipt wall timestamps approximate evidence availability, not exact scheduler callback/cache order.",
            "Targets are controller submit-to-ACK, including polling; no GPU DMA or exposed service stall is inferred.",
            "Replacement policies retain at most 256 typed and 64 legacy samples. Strata reserve 32 per direction/shape.",
            "The supplement preserves all base estimates and adds at most 32 recent timed FULL-only H2D receipts.",
            "Age policies filter timed live samples; physical seed age is unknown and remains separately identified.",
            "Legacy fallback has unknown pool shape, as in the collection estimator; typed foreign shapes never contribute.",
            "Replacement policies and time halves were fixed before V433; the supplement is a subsequent exploratory proposal.",
            "Recovered transfer estimates are observational held-out labels, not outcomes of newly enabled prefetch actions.",
            "Rejection queries have no executed-transfer label and establish support only, not timing accuracy or benefit.",
            "CPU measurements include diagnostic sample/age reconstruction; they exclude cache reuse and scheduler integration.",
            "The independently sealed live sources are not an atomic snapshot; later arrivals/outcomes are excluded.",
        ],
    }
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        "eligible": len(transfers), "rejection_support": report["rejection_query_support"],
        "h2d_full": {name: values.get("h2d:full") for name, values in report["metrics"].items()},
        "output": str(args.output),
    }), flush=True)


if __name__ == "__main__":
    main()
