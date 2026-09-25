"""Split-isolated PCIe timing fit with strict ACK/DMA timing boundaries."""

from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import heapq
import json
import math
from pathlib import Path
from typing import Any, Mapping


HEADS = ("ack", "service", "queue")
DIRECTIONS = ("h2d", "d2h")
FEATURES = ("actual_bytes", "extent_count", "native_inflight_operation_count_at_submit",
            "native_concurrent_bytes", "native_unacked_bytes_at_submit")
MAX_FIT_PER_HEAD = 4096


def _digest(raw: Any) -> str:
    return sha256(json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _number(value: Any, *, positive: bool = False) -> float | None:
    if type(value) not in (int, float):
        return None
    result = float(value)
    if not math.isfinite(result) or result < 0 or (positive and result == 0):
        return None
    return result


def _manifest(root: Path, split: str) -> tuple[dict[str, Any], str]:
    path = root / "dataset_manifest.json"
    raw = path.read_bytes()
    manifest = json.loads(raw)
    source = manifest.get("source") or {}
    contract = source.get("collection_contract") or {}
    environment = source.get("runtime_environment_contract") or {}
    grouping = manifest.get("split_contract") or {}
    table = (manifest.get("tables") or {}).get("pcie_operations") or {}
    if (
        manifest.get("dataset_kind") != "beliefkv_p6_training_evidence"
        or manifest.get("evaluation_role") != "frozen_split_local_training_evidence"
        or manifest.get("formal_local_training_eligible") is not True
        or (manifest.get("integrity") or {}).get("passes") is not True
        or contract.get("split") != split
        or contract.get("runtime_policy") != "frozen_native_reactive_v0520"
        or any(contract.get(key) is not True for key in
               ("raw_trace_eligible", "model_revision_stable", "runtime_source_stable"))
        or contract.get("predictor_enabled") is not False
        or contract.get("predictive_actions_enabled") is not False
        or environment.get("runtime_kind") != "native_reactive_v0520"
        or environment.get("uniform") is not True
        or not (environment.get("hardware") or {}).get("uuid")
        or not (environment.get("server_identity") or {}).get("served_model_name", "").startswith("Qwen3.5")
        or (source.get("native_request_evidence") or {}).get("telemetry_complete") is not True
        or grouping.get("source") != "explicit frozen split manifest"
        or grouping.get("development_only") is not False
        or grouping.get("unit") != "dataset plus repository"
        or not grouping.get("manifest_digest")
        or set((grouping.get("counts_on_request_calls") or {})) != {split}
        or table.get("path") != "pcie_operations.jsonl"
        or type(table.get("row_count")) is not int
        or table["row_count"] <= 0
        or not isinstance(table.get("sha256"), str)
        or not source.get("run_id")
    ):
        raise ValueError(f"unverified native {split} PCIe evidence: {root}")
    return manifest, sha256(raw).hexdigest()


def _labels(row: Mapping[str, Any]) -> tuple[dict[str, float], dict[str, str]]:
    labels: dict[str, float] = {}
    rejected: dict[str, str] = {}
    if row.get("telemetry_origin") == "native_hicache_ack_v0520":
        submit = _number(row.get("submit_ts_ms"))
        end = _number(row.get("complete_ts_ms"))
        ack = _number(row.get("submit_to_complete_ms"), positive=True)
        if (row.get("duration_label_kind") != "native_transfer_stream"
            or submit is None or end is None or end <= submit or ack is None):
            rejected["ack"] = "missing_or_ambiguous_ack_interval"
        else:
            labels["ack"] = ack
    else:
        rejected["ack"] = "not_native_ack"

    if (row.get("start_timestamp_semantics") == "dma_start"
        and row.get("duration_label_kind") == "direct_dma"):
        submit = _number(row.get("submit_ts_ms"))
        start = _number(row.get("start_ts_ms"))
        end = _number(row.get("complete_ts_ms"))
        if submit is None or start is None or end is None or end <= start or start < submit:
            rejected["service"] = "invalid_dma_interval"
        else:
            labels["service"] = end - start
    else:
        rejected["service"] = "no_identifiable_dma_service_boundary"

    # A device event without a wall anchor is not comparable with submit_ts_ms.
    if (row.get("start_timestamp_semantics") != "dma_start"
        or row.get("duration_label_kind") != "direct_dma"):
        rejected["queue"] = "no_wall_clock_start_anchor"
    else:
        submit = _number(row.get("submit_ts_ms"))
        start = _number(row.get("start_ts_ms"))
        end = _number(row.get("complete_ts_ms"))
        if submit is None or start is None or end is None or start < submit or end <= start:
            rejected["queue"] = "invalid_submit_to_start_interval"
        else:
            labels["queue"] = start - submit
    return labels, rejected


def _features(row: Mapping[str, Any], selected: tuple[str, ...]) -> list[float]:
    return [1.0] + [math.log1p(float(row[name])) for name in selected]


def _predict(model: Mapping[str, Any], row: Mapping[str, Any]) -> float | None:
    if model.get("status") != "fitted":
        return None
    selected = tuple(model["features"])
    if any(_number(row.get(name), positive=(name == "actual_bytes")) is None
           for name in selected):
        return None
    linear = sum(a * b for a, b in zip(model["coefficients"], _features(row, selected)))
    return max(0.0, math.expm1(min(40.0, max(-40.0, linear))))


def _quantile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    sorted_values = sorted(values)
    position = (len(sorted_values) - 1) * fraction
    left = int(position)
    return sorted_values[left] + (sorted_values[min(left + 1, len(sorted_values) - 1)]
                                  - sorted_values[left]) * (position - left)


def _fit(rows: list[tuple[dict[str, Any], float]], head: str) -> dict[str, Any]:
    if len(rows) < 2:
        return {
            "status": "unavailable",
            "reason": (
                "unidentifiable_dma_service_without_anchored_start"
                if head == "service" and not rows else
                "unidentifiable_queue_without_anchored_start"
                if head == "queue" and not rows else
                "insufficient_labeled_transfers"
            ),
        }
    # Optional features are enabled only when every sampled training row has
    # them. Calibration never determines the feature set or coefficients.
    selected = tuple(name for name in FEATURES if all(
        _number(row.get(name), positive=(name == "actual_bytes")) is not None
        for row, _ in rows))
    if "actual_bytes" not in selected:
        return {"status": "unavailable", "reason": "missing_bytes"}
    import numpy as np

    x = np.asarray([_features(row, selected) for row, _ in rows], dtype=float)
    y = np.log1p(np.asarray([label for _, label in rows]))
    coefficients = np.linalg.lstsq(x, y, rcond=None)[0]
    model: dict[str, Any] = {
        "status": "fitted",
        "features": list(selected),
        "coefficients": coefficients.tolist(),
        "fit_command_observations": len(rows),
        "algorithm": "log1p_elapsed_ols_v1",
    }
    residual = [abs(_predict(model, row) - label) for row, label in rows]
    model["apparent_absolute_residual_p90_ms"] = _quantile(residual, .90)
    model["apparent_absolute_residual_p95_ms"] = _quantile(residual, .95)
    return model


def _stream(root: Path, manifest: Mapping[str, Any], split: str) -> dict[str, Any]:
    path = root / "pcie_operations.jsonl"
    expected = manifest["tables"]["pcie_operations"]
    digest = sha256()
    seen: set[tuple[str, str]] = set()
    heaps: dict[tuple[str, str], list[tuple[int, int, dict[str, Any], float]]] = defaultdict(list)
    labels: dict[tuple[str, str], list[tuple[dict[str, Any], float]]] = defaultdict(list)
    rejection: Counter[str] = Counter()
    counts: Counter[str] = Counter()
    group_ids: dict[tuple[str, str], set[str]] = defaultdict(set)
    grouping: Counter[str] = Counter()
    timing: Counter[str] = Counter()
    for direction in DIRECTIONS:
        for field in ("all_commands", "submit_present", "start_present",
                      "complete_present", "start_and_complete_present",
                      "dma_start_semantics", "native_ack_commands",
                      "unanchored_stream_observations"):
            timing[f"{direction}:{field}"] = 0
    run_id = manifest["source"]["run_id"]
    with path.open("rb") as handle:
        for index, line in enumerate(handle):
            digest.update(line)
            if not line.strip():
                raise ValueError(f"blank PCIe row at {path}:{index + 1}")
            row = json.loads(line)
            if (row.get("row_type") != "pcie_operation"
                or row.get("run_id") != run_id
                or row.get("direction") not in DIRECTIONS
                or not isinstance(row.get("command_id"), str)
                or not row["command_id"]):
                raise ValueError(f"invalid PCIe identity at {path}:{index + 1}")
            key = (run_id, row["command_id"])
            if key in seen:
                raise ValueError(f"duplicate PCIe command: {key}")
            seen.add(key)
            counts["raw_rows"] += 1
            direction = row["direction"]
            timing[f"{direction}:all_commands"] += 1
            if _number(row.get("submit_ts_ms")) is not None:
                timing[f"{direction}:submit_present"] += 1
            if _number(row.get("start_ts_ms")) is not None:
                timing[f"{direction}:start_present"] += 1
            if _number(row.get("complete_ts_ms")) is not None:
                timing[f"{direction}:complete_present"] += 1
            if (_number(row.get("start_ts_ms")) is not None
                and _number(row.get("complete_ts_ms")) is not None):
                timing[f"{direction}:start_and_complete_present"] += 1
            if row.get("start_timestamp_semantics") == "dma_start":
                timing[f"{direction}:dma_start_semantics"] += 1
            if row.get("telemetry_origin") == "native_hicache_ack_v0520":
                timing[f"{direction}:native_ack_commands"] += 1
            if (_number(row.get("transfer_stream_elapsed_ms"), positive=True) is not None
                and row.get("start_timestamp_semantics") == "device_event_no_wall_anchor"):
                timing[f"{direction}:unanchored_stream_observations"] += 1
            if row.get("status") != "completed" or row.get("training_eligible_service_curve") is not True:
                rejection["ineligible_or_incomplete"] += 1
                continue
            if _number(row.get("actual_bytes"), positive=True) is None:
                rejection["missing_or_invalid_bytes"] += 1
                continue
            if row.get("split") not in (None, split):
                raise ValueError(f"foreign split in {path}:{index + 1}")
            row_labels, invalid = _labels(row)
            for head, reason in invalid.items():
                rejection[f"{head}:{reason}"] += 1
            workflow = row.get("workflow_id")
            episode = row.get("episode_id")
            if isinstance(workflow, str) and workflow:
                group = f"workflow:{workflow}"
                grouping["workflow_labeled_commands"] += 1
            elif isinstance(episode, str) and episode:
                group = f"episode:{episode}"
                grouping["episode_labeled_commands"] += 1
            else:
                group = f"run:{run_id}"
                grouping["run_only_commands"] += 1
            for head, label in row_labels.items():
                cell = (direction, head)
                counts[f"{direction}:{head}:labeled_commands"] += 1
                group_ids[cell].add(group)
                item = {"actual_bytes": row["actual_bytes"],
                        **{name: row.get(name) for name in FEATURES if name != "actual_bytes"},
                        "group": group}
                if split == "calibration":
                    labels[cell].append((item, label))
                else:
                    rank = int.from_bytes(sha256(f"{run_id}:{row['command_id']}:{head}".encode()).digest()[:8], "big")
                    entry = (-rank, -index, item, label)
                    heap = heaps[cell]
                    if len(heap) < MAX_FIT_PER_HEAD:
                        heapq.heappush(heap, entry)
                    elif entry[:2] > heap[0][:2]:
                        heapq.heapreplace(heap, entry)
    if counts["raw_rows"] != expected["row_count"] or digest.hexdigest() != expected["sha256"]:
        raise ValueError(f"PCIe table count or SHA256 mismatch: {path}")
    for cell, heap in heaps.items():
        labels[cell] = [(row, label) for _, _, row, label in heap]
    return {
        "samples": labels,
        "counts": dict(counts),
        "rejections": dict(sorted(rejection.items())),
        "group_counts": {":".join(cell): len(groups) for cell, groups in group_ids.items()},
        "identity_coverage": dict(grouping),
        "timing_coverage": dict(sorted(timing.items())),
        "source": {"dataset_dir": str(root), "manifest_sha256": sha256((root / "dataset_manifest.json").read_bytes()).hexdigest(),
                   "table_sha256": digest.hexdigest(), "run_id": run_id, "split": split},
    }


def _evaluate(rows: list[tuple[dict[str, Any], float]], model: Mapping[str, Any]) -> dict[str, Any]:
    if model.get("status") != "fitted":
        return {"status": "unavailable", "reason": model.get("reason"), "labeled_commands": len(rows)}
    errors: list[float] = []
    relative: list[float] = []
    group_errors: dict[str, list[float]] = defaultdict(list)
    covered90 = covered95 = 0
    missing = 0
    for row, actual in rows:
        predicted = _predict(model, row)
        if predicted is None:
            missing += 1
            continue
        error = abs(predicted - actual)
        errors.append(error)
        relative.append(error / max(actual, 1e-9))
        group_errors[row["group"]].append(error / max(actual, 1e-9))
        covered90 += error <= model["apparent_absolute_residual_p90_ms"]
        covered95 += error <= model["apparent_absolute_residual_p95_ms"]
    return {
        "status": "evaluated" if errors else "unavailable",
        "labeled_commands": len(rows),
        "predicted_commands": len(errors),
        "unavailable_feature_commands": missing,
        "absolute_error_p50_ms": _quantile(errors, .50),
        "absolute_error_p95_ms": _quantile(errors, .95),
        "relative_error_p95": _quantile(relative, .95),
        "apparent_train_interval_p90_coverage": covered90 / len(errors) if errors else None,
        "apparent_train_interval_p95_coverage": covered95 / len(errors) if errors else None,
        "group_count": len(group_errors),
        "group_mean_relative_error_p95": _quantile(
            [sum(values) / len(values) for values in group_errors.values()], .95),
        "grouping": ("workflow_or_episode" if group_errors and
                     all(not group.startswith("run:") for group in group_errors)
                     else "run_only_or_mixed"),
    }


def fit_native_pcie_service(train_dir: Path, calibration_dir: Path,
                            *, split_manifest: Path) -> dict[str, Any]:
    """Fit on train only; calibration is opened only after models are frozen."""
    train_dir, calibration_dir = Path(train_dir).resolve(), Path(calibration_dir).resolve()
    if train_dir == calibration_dir:
        raise ValueError("train and calibration directories must differ")
    train_manifest, _ = _manifest(train_dir, "train")
    cal_manifest, _ = _manifest(calibration_dir, "calibration")
    frozen = json.loads(Path(split_manifest).read_text(encoding="utf-8"))
    from beliefkv.experiments.p6_split import validate_split_manifest

    validate_split_manifest(frozen)
    digest = _digest(frozen)
    for manifest in (train_manifest, cal_manifest):
        if manifest["split_contract"]["manifest_digest"] != digest:
            raise ValueError("dataset split digest differs from frozen split")
    train_source, cal_source = train_manifest["source"], cal_manifest["source"]
    if train_source["run_id"] == cal_source["run_id"]:
        raise ValueError("train and calibration share a run")
    train_env = train_source["runtime_environment_contract"]
    if _digest(train_env) != _digest(cal_source["runtime_environment_contract"]):
        raise ValueError("train and calibration hardware/runtime contracts differ")
    hardware_key = _digest(train_env)
    train = _stream(train_dir, train_manifest, "train")
    models = {direction: {
        head: _fit(train["samples"].get((direction, head), []), head)
        for head in HEADS} for direction in DIRECTIONS}
    cal = _stream(calibration_dir, cal_manifest, "calibration")
    evaluations = {direction: {
        head: _evaluate(cal["samples"].get((direction, head), []), models[direction][head])
        for head in HEADS} for direction in DIRECTIONS}
    # No transfer-to-workflow join exists in current native telemetry; one run
    # cannot provide independent episode-level generalization evidence.
    eligible = all(
        models[direction][head]["status"] == "fitted"
        and evaluations[direction][head]["status"] == "evaluated"
        and evaluations[direction][head]["grouping"] == "workflow_or_episode"
        and train["group_counts"].get(f"{direction}:{head}", 0) >= 2
        and evaluations[direction][head]["group_count"] >= 2
        and evaluations[direction][head]["unavailable_feature_commands"] == 0
        and evaluations[direction][head]["relative_error_p95"] <= .25
        and evaluations[direction][head]["apparent_train_interval_p90_coverage"] >= .85
        and evaluations[direction][head]["apparent_train_interval_p95_coverage"] >= .90
        for direction in DIRECTIONS for head in HEADS
    )
    return {
        "schema_version": 1,
        "scope": "qwen3.5_native_reactive_pcie_offline",
        "hardware_key": hardware_key,
        "hardware_contract": train_env,
        "split_manifest_digest": digest,
        "sources": [train["source"], cal["source"]],
        "timing_contract": {
            "ack": "native submit_to_complete_ms is submit-to-ack, not DMA service",
            "service": "only direct_dma with wall-clock dma_start and complete_ts_ms",
            "queue": "only direct_dma with aligned wall-clock submit_ts_ms and dma_start",
            "transfer_stream_elapsed_ms": "device-event stream interval only; not identified DMA service",
            "non_stream_overhead_is_not_queue": True,
            "native_ack_is_not_proven_independent_dma": True,
        },
        "feature_contract": {
            "actual_bytes": "positive required",
            "optional": list(FEATURES[1:]),
            "missing_optional_features": "excluded from head fit; prediction needs only selected features",
            "native_unacked_bytes_at_submit": "observed unacked load, not independent DMA concurrency",
        },
        "train": {key: train[key] for key in ("counts", "rejections", "group_counts", "identity_coverage", "timing_coverage")},
        "calibration": {key: cal[key] for key in ("counts", "rejections", "group_counts", "identity_coverage", "timing_coverage")},
        "models": models,
        "calibration_evaluation": evaluations,
        "independence_contract": {
            "command_ids": "distinct observed transfer attempts, not IID physical DMA repetitions",
            "independent_transfer_sample_count": None,
            "evaluation_units": "workflow or explicit episode when present; otherwise entire run",
            "unattributed_native_rows_are_not_workflow_samples": True,
        },
        "acceptance": {
            "joint_queue_and_service_eligible": eligible,
            "online_eligible": False,
            "calibration_used_for_fit_or_model_selection": False,
            "group_minimum_per_head_and_split": 2,
            "maximum_relative_error_p95": .25,
            "minimum_p90_interval_coverage": .85,
            "minimum_p95_interval_coverage": .90,
            "reason": None if eligible else "unidentifiable DMA service/queue, insufficient independent groups/features, or error/coverage gate failure",
        },
    }
