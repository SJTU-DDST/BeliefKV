from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from beliefkv.experiments.native_pcie_service import _labels, fit_native_pcie_service
from scripts.fit_native_pcie_service import main


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _row(command: str, direction: str, group: str, *, direct: bool = False,
         elapsed: float = 3.0) -> dict:
    base = {
        "row_type": "pcie_operation", "command_id": command, "direction": direction,
        "status": "completed", "actual_bytes": 8192 + len(command) * 1024,
        "training_eligible_service_curve": True, "workflow_id": group,
        "submit_ts_ms": 100.0, "complete_ts_ms": 100.0 + elapsed,
        "native_unacked_bytes_at_submit": 0,
    }
    if direct:
        return {
            **base, "duration_label_kind": "direct_dma",
            "start_timestamp_semantics": "dma_start", "start_ts_ms": 101.0,
            "direct_dma_duration_ms": elapsed - 1.0,
        }
    return {
        **base, "telemetry_origin": "native_hicache_ack_v0520",
        "duration_label_kind": "native_transfer_stream",
        "start_timestamp_semantics": "device_event_no_wall_anchor",
        "start_ts_ms": None, "submit_to_complete_ms": elapsed,
        "transfer_stream_elapsed_ms": elapsed - .5,
    }


def _corpus(tmp_path: Path, *, direct: bool = False) -> tuple[Path, Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    split_manifest = tmp_path / "split.json"
    split_manifest.write_text(json.dumps({"schema_version": 1, "frozen": True, "projects": []}))
    environment = {
        "runtime_kind": "native_reactive_v0520", "uniform": True,
        "hardware": {"uuid": "GPU-test"},
        "server_identity": {"served_model_name": "Qwen3.5-test"},
    }
    roots = []
    for split in ("train", "calibration"):
        root = tmp_path / split
        root.mkdir()
        rows = [
            {**_row(f"{split}-{i}-{direction}", direction, f"{split}-wf-{i % 2}",
                    direct=direct, elapsed=3.0 + i % 2), "run_id": split}
            for direction in ("h2d", "d2h") for i in range(6)
        ]
        raw = "".join(json.dumps(row) + "\n" for row in rows).encode()
        (root / "pcie_operations.jsonl").write_bytes(raw)
        manifest = {
            "dataset_kind": "beliefkv_p6_training_evidence",
            "evaluation_role": "frozen_split_local_training_evidence",
            "formal_local_training_eligible": True,
            "integrity": {"passes": True},
            "source": {
                "run_id": split,
                "collection_contract": {
                    "split": split, "runtime_policy": "frozen_native_reactive_v0520",
                    "raw_trace_eligible": True, "model_revision_stable": True,
                    "runtime_source_stable": True, "predictor_enabled": False,
                    "predictive_actions_enabled": False,
                },
                "runtime_environment_contract": environment,
                "native_request_evidence": {"telemetry_complete": True},
            },
            "split_contract": {
                "source": "explicit frozen split manifest", "development_only": False,
                "unit": "dataset plus repository", "manifest_digest": _digest(
                    json.loads(split_manifest.read_text())),
                "counts_on_request_calls": {split: 6},
            },
            "tables": {"pcie_operations": {
                "path": "pcie_operations.jsonl", "row_count": len(rows),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }},
        }
        (root / "dataset_manifest.json").write_text(json.dumps(manifest))
        roots.append(root)
    return roots[0], roots[1], split_manifest


def test_native_ack_and_stream_never_produce_dma_service_or_queue() -> None:
    labels, rejected = _labels(_row("one", "h2d", "wf"))
    assert labels == {"ack": 3.0}
    assert rejected["service"] == "no_identifiable_dma_service_boundary"
    assert rejected["queue"] == "no_wall_clock_start_anchor"
    broken = _row("two", "d2h", "wf")
    broken["submit_to_complete_ms"] = None
    assert _labels(broken)[0] == {}
    broken["start_ts_ms"] = 101
    assert _labels(broken)[0] == {}


def test_dma_start_requires_aligned_submit_start_complete() -> None:
    row = _row("one", "d2h", "wf", direct=True)
    assert _labels(row)[0] == {"service": 2.0, "queue": 1.0}
    row["start_ts_ms"] = None
    assert _labels(row)[0] == {}
    row["start_ts_ms"] = 99.0
    assert _labels(row)[0] == {}
    row["start_ts_ms"] = 101.0
    row["start_timestamp_semantics"] = "hicache_api_submit_begin"
    assert _labels(row)[0] == {}


def test_split_isolation_and_grouped_evaluation(tmp_path: Path) -> None:
    train, cal, split = _corpus(tmp_path, direct=True)
    result = fit_native_pcie_service(train, cal, split_manifest=split)
    assert result["models"]["d2h"]["service"]["status"] == "fitted"
    assert result["models"]["h2d"]["queue"]["status"] == "fitted"
    assert result["calibration_evaluation"]["d2h"]["service"]["group_count"] == 2
    assert result["train"]["timing_coverage"]["h2d:start_and_complete_present"] == 6
    assert result["independence_contract"]["independent_transfer_sample_count"] is None
    # Changing only calibration labels cannot change coefficients or feature selection.
    path = cal / "pcie_operations.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    for row in rows:
        row["complete_ts_ms"] += 20
    raw = "".join(json.dumps(row) + "\n" for row in rows).encode()
    path.write_bytes(raw)
    manifest_path = cal / "dataset_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["tables"]["pcie_operations"]["sha256"] = hashlib.sha256(raw).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    changed = fit_native_pcie_service(train, cal, split_manifest=split)
    assert changed["models"] == result["models"]
    assert changed["calibration_evaluation"] != result["calibration_evaluation"]


def test_unanchored_native_corpus_explicitly_unavailable(tmp_path: Path) -> None:
    train, cal, split = _corpus(tmp_path)
    # Remove attribution to reproduce the native GPU telemetry.
    for root in (train, cal):
        path = root / "pcie_operations.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        for row in rows:
            row.pop("workflow_id")
        raw = "".join(json.dumps(row) + "\n" for row in rows).encode()
        path.write_bytes(raw)
        manifest_path = root / "dataset_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["tables"]["pcie_operations"]["sha256"] = hashlib.sha256(raw).hexdigest()
        manifest_path.write_text(json.dumps(manifest))
    result = fit_native_pcie_service(train, cal, split_manifest=split)
    assert result["models"]["h2d"]["ack"]["status"] == "fitted"
    assert result["models"]["h2d"]["service"]["status"] == "unavailable"
    assert result["models"]["h2d"]["service"]["reason"] == (
        "unidentifiable_dma_service_without_anchored_start")
    assert result["models"]["d2h"]["queue"]["status"] == "unavailable"
    assert result["calibration_evaluation"]["h2d"]["ack"]["grouping"] == "run_only_or_mixed"
    assert result["train"]["timing_coverage"]["h2d:start_and_complete_present"] == 0
    assert result["acceptance"]["joint_queue_and_service_eligible"] is False
    assert main(["--train-dir", str(train), "--calibration-dir", str(cal),
                 "--split-manifest", str(split),
                 "--output", str(tmp_path / "artifact.json")]) == 2
    assert json.loads((tmp_path / "artifact.json").read_text())["models"]["h2d"]["service"]["status"] == "unavailable"


def test_rejects_integrity_and_split_problems(tmp_path: Path) -> None:
    train, cal, split = _corpus(tmp_path)
    source = train / "pcie_operations.jsonl"
    source.write_bytes(source.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="blank PCIe row"):
        fit_native_pcie_service(train, cal, split_manifest=split)
    source.write_bytes(source.read_bytes().rstrip(b"\n") + b"\n")
    source.write_bytes(source.read_bytes().replace(b'"status": "completed"',
                                                   b'"status": "completeD"'))
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        fit_native_pcie_service(train, cal, split_manifest=split)
    train, cal, split = _corpus(tmp_path / "again")
    source = train / "pcie_operations.jsonl"
    raw = source.read_bytes()
    raw += raw.splitlines(keepends=True)[0]
    source.write_bytes(raw)
    train_manifest = train / "dataset_manifest.json"
    amended = json.loads(train_manifest.read_text())
    amended["tables"]["pcie_operations"]["row_count"] += 1
    amended["tables"]["pcie_operations"]["sha256"] = hashlib.sha256(raw).hexdigest()
    train_manifest.write_text(json.dumps(amended))
    with pytest.raises(ValueError, match="duplicate PCIe command"):
        fit_native_pcie_service(train, cal, split_manifest=split)
    manifest_path = cal / "dataset_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["split_contract"]["manifest_digest"] = "wrong"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="split digest"):
        fit_native_pcie_service(train, cal, split_manifest=split)
