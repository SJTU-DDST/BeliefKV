#!/usr/bin/env python3
"""Audit Mamba COW barriers and direct Host restoration on bounded trace prefixes."""

from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
from statistics import mean, median


BASE = Path("/home/longhao/experiment")
MAIN = BASE / "BeliefKV"
DEV = BASE / ".worktrees/beliefkv-v25-followup-20261011"
ENGINE = BASE / ".worktrees/sglang-v1076-load-anchor-20261016"
RAW = MAIN / "experiments/raw"
MODEL_CONFIG = Path("/srv/ai/models/Qwen/Qwen3.5-35B-A3B/config.json")
NAME = "v1109_mamba_cow_restore_frontier_20261016.json"
CASES = {
    "v73_predictive_live_prefix": RAW / (
        "qwen35_native_predictive_online_poisson_156root_20261016_v73/predictive_h2d"
    ),
    "v72_predictive_complete": RAW / (
        "qwen35_native_predictive_replenished_108plus48_20261016_v72_workflow_work/"
        "predictive_h2d"
    ),
    "v58_native_historical": RAW / (
        "qwen35_native_predictive_replenished_108plus48_20261014_v58/native"
    ),
}


def bound(path):
    payload = path.read_bytes()
    return {
        "path": str(path), "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def distribution(values):
    ordered = sorted(values)
    if any(type(value) not in (int, float) or not math.isfinite(value)
           or value < 0 for value in ordered):
        raise ValueError("invalid nonnegative measurement")
    return {
        "count": len(ordered),
        "sum": sum(ordered),
        "mean": mean(ordered) if ordered else None,
        "p50": median(ordered) if ordered else None,
        "p90": ordered[math.ceil(.9 * len(ordered)) - 1] if ordered else None,
        "max": ordered[-1] if ordered else None,
    }


def records(receipt, names):
    markers = tuple(
        marker
        for name in names
        for marker in (
            f'"event":"{name}"'.encode(), f'"event": "{name}"'.encode(),
            f'"kind":"{name}"'.encode(), f'"kind": "{name}"'.encode(),
        )
    )
    seen, digest = 0, hashlib.sha256()
    with Path(receipt["path"]).open("rb") as stream:
        while seen < receipt["snapshot_size_bytes"]:
            line = stream.readline(receipt["snapshot_size_bytes"] - seen)
            if not line.endswith(b"\n"):
                receipt["discarded_incomplete_tail_bytes"] = len(line)
                break
            seen += len(line)
            digest.update(line)
            if any(marker in line for marker in markers):
                row = json.loads(line)
                if row.get("event", row.get("kind")) in names:
                    yield row
    receipt.update(
        read_prefix_bytes=seen, read_prefix_sha256=digest.hexdigest(),
        scan_finished_at=datetime.now().astimezone().isoformat(),
    )


def shape(service):
    requests = service.get("request_samples", [])
    if not requests or len(requests) != service["batch_size"] or any(
        row.get("prefill_geometry_source") not in (
            "native_batch", "native_request_range"
        )
        or type(row.get("token_delta")) is not int
        or row["token_delta"] < 0
        for row in requests
    ):
        return None
    return sum(row["token_delta"] for row in requests)


def sample_summary(rows):
    return {
        "batch_count": len(rows),
        "first_dependency_wait_ms": distribution([
            row["first_wait_ms"] for row in rows
        ]),
        "all_dependency_wait_ms": distribution([
            row["total_wait_ms"] for row in rows
        ]),
        "wait_over_1ms": sum(row["total_wait_ms"] > 1 for row in rows),
        "wait_over_5ms": sum(row["total_wait_ms"] > 5 for row in rows),
        "short_single_request_batches": sum(
            row["batch_size"] == 1 and row["extend_tokens"] is not None
            and row["extend_tokens"] < 512 for row in rows
        ),
    }


def summarize_host_requests(rows, full_unit):
    return {
        "request_count": len(rows),
        "cached_host_full_tokens_at_first_service": sum(
            row["cached_tokens_host"] for row in rows
        ),
        "logical_host_full_bytes_at_first_service": sum(
            row["cached_tokens_host"] * full_unit for row in rows
        ),
        "host_full_tokens_per_request": distribution([
            row["cached_tokens_host"] for row in rows
        ]),
        "mamba_host_hit_slots": sum(row["mamba_host_hit_slots"] for row in rows),
    }


def analyze(label, arm, receipts, last_mamba, full_unit, mamba_unit):
    services, probes = {}, []
    for row in records(receipts["audit"], (
        "gpu_service_sample", "gpu_restore_dependency_wait"
    )):
        if row["event"] == "gpu_restore_dependency_wait":
            probes.append(row)
        elif row.get("phase") == "prefill":
            identity = row["forward_iter"]
            if identity in services:
                raise ValueError("duplicate prefill forward identity")
            services[identity] = {
                "forward_iter": identity,
                "request_ids": [item["request_id"] for item in row["request_samples"]],
                "batch_size": row["batch_size"],
                "extend_tokens": shape(row),
            }
    groups, paired, rejected = defaultdict(list), [], Counter()
    for probe in probes:
        service = services.get(probe["forward_iter"])
        if service is None or service["request_ids"] != probe["request_ids"]:
            rejected["no_exact_completed_service_identity"] += 1
            continue
        layers = probe["layers"]
        if not layers:
            rejected["no_observed_dependencies"] += 1
            continue
        waits = [item["gpu_wait_ms"] for item in layers]
        if not math.isclose(sum(waits), probe["gpu_layer_dependency_wait_ms"],
                            abs_tol=1e-5):
            raise ValueError("inconsistent GPU wait receipt")
        first = layers[0]["layer"]
        route = (
            "pre_forward_last_mamba_barrier" if first == last_mamba
            else "layerwise_entry_no_observed_pre_forward_barrier" if first == 0
            else "other_first_dependency"
        )
        sample = {
            **service, "route": route, "first_observed_layer": first,
            "observed_dependency_count": len(layers),
            "first_wait_ms": waits[0], "total_wait_ms": sum(waits),
            "probe_ts_ms": probe["ts_ms"],
        }
        paired.append(sample)
        groups[route].append(sample)

    host_groups = defaultdict(list)
    seen_requests = set()
    for row in records(receipts["events"], ("llm_submit",)):
        attrs = row["attributes"]
        rid = attrs["request_id"]
        if rid in seen_requests:
            raise ValueError("duplicate first-service request cache record")
        seen_requests.add(rid)
        full, mamba = attrs["cached_tokens_host"], attrs["mamba_host_hit_slots"]
        if type(full) is not int or type(mamba) is not int or min(full, mamba) < 0:
            raise ValueError("unknown native host-hit units")
        route = (
            "full_and_mamba_host_hit" if full and mamba
            else "full_host_hit_without_mamba_host_hit" if full
            else "mamba_host_hit_without_full_host_hit" if mamba
            else "no_host_hit_at_first_service"
        )
        host_groups[route].append({
            "request_id": rid, "cached_tokens_host": full,
            "mamba_host_hit_slots": mamba,
        })

    transfer_groups = defaultdict(list)
    for row in records(receipts["transfer"], ("transfer_telemetry",)):
        if row["direction"].lower() != "h2d" or row["status"] != "completed":
            continue
        units = row["num_tokens_by_pool"]
        full, mamba = units.get("kv", units.get("full", 0)), units.get("mamba", 0)
        if full * full_unit + mamba * mamba_unit != row["actual_bytes"]:
            raise ValueError("transfer bytes disagree with pool units")
        route = (
            "full_and_mamba" if full and mamba else "full_only" if full
            else "mamba_only" if mamba else "empty"
        )
        transfer_groups[route].append(row)

    barrier = groups["pre_forward_last_mamba_barrier"]
    return {
        "label": label, "arm_path": str(arm),
        "receipts": receipts,
        "prefill_batches_with_completed_service": len(services),
        "load_consuming_probe_batches": len(probes),
        "paired_probe_batches": len(paired),
        "rejected_probes": dict(rejected),
        "pre_forward_barrier_fraction_of_paired_samples": (
            len(barrier) / len(paired) if paired else None
        ),
        "routes": {name: sample_summary(rows) for name, rows in groups.items()},
        "short_prefill_probes": sample_summary([
            row for row in paired if row["batch_size"] == 1
            and row["extend_tokens"] is not None and row["extend_tokens"] < 512
        ]),
        "first_service_request_cache": {
            name: summarize_host_requests(rows, full_unit)
            for name, rows in host_groups.items()
        },
        "native_h2d_ack_batches_including_tagged_prefetch": {
            name: {
                "batch_count": len(rows),
                "actual_bytes": sum(row["actual_bytes"] for row in rows),
                "full_bytes": sum(
                    row["num_tokens_by_pool"].get("kv", 0) * full_unit for row in rows
                ),
                "mamba_slots": sum(
                    row["num_tokens_by_pool"].get("mamba", 0) for row in rows
                ),
                "mamba_bytes": sum(
                    row["num_tokens_by_pool"].get("mamba", 0) * mamba_unit for row in rows
                ),
                "transfer_stream_elapsed_ms": distribution([
                    row["transfer_stream_elapsed_ms"] for row in rows
                    if row.get("transfer_stream_elapsed_ms") is not None
                ]),
            } for name, rows in transfer_groups.items()
        },
        "samples": paired,
    }


def main():
    outputs = [root / "experiments/reports" / NAME for root in (MAIN, DEV)]
    if any(path.exists() for path in outputs):
        raise FileExistsError("preserve previous analysis")
    config = json.loads(MODEL_CONFIG.read_bytes())["text_config"]
    full_layers = [
        index for index, kind in enumerate(config["layer_types"])
        if kind == "full_attention"
    ]
    mamba_layers = [
        index for index, kind in enumerate(config["layer_types"])
        if kind == "linear_attention"
    ]
    last_mamba = max(mamba_layers)
    if len(full_layers) != 10 or last_mamba != 38:
        raise ValueError("unexpected model layout")
    receipts = {
        label: {
            name: {
                "path": str(path), "snapshot_size_bytes": path.stat().st_size,
                "snapshot_at": datetime.now().astimezone().isoformat(),
            }
            for name, filename in (
                ("audit", "runtime_audit.jsonl"),
                ("events", "runtime_events.sglang.jsonl"),
                ("transfer", "transfer_telemetry.jsonl"),
            )
            for path in [arm / "server" / filename]
        } for label, arm in CASES.items()
    }
    status = {}
    for label, arm in CASES.items():
        payload = json.loads((arm / "server/native_telemetry_status.json").read_bytes())
        units = payload["host_pool_evidence"]
        status[label] = {
            "full_unit": units["full"]["bytes_per_unit"],
            "mamba_unit": units["mamba"]["bytes_per_unit"],
        }
    result = {
        "status": "bounded_cpu_route_and_dependency_audit_not_causal_speedup",
        "analysis_started_at": datetime.now().astimezone().isoformat(),
        "model_layout": {
            "source": bound(MODEL_CONFIG),
            "full_attention_layers": full_layers,
            "mamba_layers": mamba_layers,
            "pre_forward_cow_wait_layer": last_mamba,
            "full_layer_fraction_completed_by_cow_barrier": (
                sum(layer <= last_mamba for layer in full_layers) / len(full_layers)
            ),
            "mamba_layer_fraction_completed_by_cow_barrier": 1.0,
            "semantics": (
                "For the current serial layer-ordered H2D batch when COW waits "
                "on layer38: all Mamba transfers and 9/10 equal-sized FULL layers "
                "in that batch precede the barrier. This is not a proportion "
                "of the entire agent context or evidence of exposed GPU stall."
            ),
        },
        "sources": [bound(path) for path in (
            ENGINE / "python/sglang/srt/mem_cache/memory_pool.py",
            ENGINE / "python/sglang/srt/model_executor/model_runner.py",
            ENGINE / "python/sglang/srt/mem_cache/l2_transfer.py",
            ENGINE / "python/sglang/srt/mem_cache/unified_cache/components/mamba.py",
            ENGINE / "python/sglang/srt/managers/cache_controller.py",
            DEV / "beliefkv/runtime/restore_wait_probe.py",
            Path(__file__),
        )],
        "arms": {
            label: analyze(label, arm, receipts[label], last_mamba, **status[label])
            for label, arm in CASES.items()
        },
        "limitations": [
            "Live V73 inputs are separate bounded complete-line prefixes, not an atomic snapshot.",
            "V73 has no completed same-arrival-table native comparison yet.",
            "Historical native and predictive arms differ in versions and realized demand.",
            "Dependency probes sample every16 load-consuming prefills; no extrapolation to all requests.",
            "A first wait at layer38 identifies the current pre-forward COW fence; "
            "a first wait at layer0 records no such Python fence in that sample.",
            "Direct Host-to-request Mamba loading can avoid checkpoint-to-request "
            "D2D COW; Host-hit counters alone do not identify every per-request copy.",
            "Resident Mamba COW with no current H2D consumer is outside these "
            "load-consuming samples and does not imply new state restoration.",
            "Transfer ACK batches include tagged predictive loads; first-service "
            "cached_host counters are logical request evidence, not physical byte receipts.",
            "Waiting until layer38 constrains data readiness, but elapsed waiting "
            "can be tiny when earlier CPU/GPU work already overlaps the copy.",
            "Only missing data in the current restore batch is covered by the "
            "90percent FULL /100percent Mamba frontier, not already resident context.",
            "Wait-event CUDA timing includes event overhead; small values are "
            "not identifiable DMA stalls. No per-module JCT saving is inferred.",
        ],
        "goal_complete": False,
    }
    result["analysis_finished_at"] = datetime.now().astimezone().isoformat()
    payload = json.dumps(result, indent=2, allow_nan=False) + "\n"
    for path in outputs:
        with path.open("x") as stream:
            stream.write(payload)
    print(json.dumps({
        "output": str(outputs[0]),
        "model_layout": result["model_layout"],
        "arms": {
            label: {name: arm[name] for name in (
                "prefill_batches_with_completed_service", "paired_probe_batches",
                "pre_forward_barrier_fraction_of_paired_samples", "routes",
                "first_service_request_cache",
            )} for label, arm in result["arms"].items()
        },
    }, indent=2))


if __name__ == "__main__":
    main()
