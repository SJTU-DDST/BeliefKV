from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from beliefkv.predictor.structured_frontier import FrontierBeliefModel
from scripts.train_qwen35_native_frontier import main


def _write_table(root: Path, name: str, rows: list[dict]) -> dict:
    path = root / f"{name}.jsonl"
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    return {
        "path": path.name,
        "row_count": len(rows),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _dataset(root: Path, *, run_id: str = "run-1", size: int = 1) -> Path:
    root.mkdir()
    decisions = [
        {
            "schema_version": 2,
            "decision_id": f"{run_id}-decision-{index}",
            "run_id": run_id,
            "split": "train",
            "project": f"project-{index % 5}",
            "instance_id": f"task-{index}",
            "base_commit": f"base-{index}",
            "workflow_id": f"{run_id}-workflow-{index}",
            "episode_group_id": f"{run_id}-episode-{index}",
            "trigger_kind": "llm_submit",
            "invocations": [{
                "invocation_id": "worker",
                "agent_definition_id": "worker",
                "state": "running_llm",
                "boundary_history": ["tool"],
                "current_sequence_tokens": 128,
            }],
            "labels": [{
                "invocation_id": "worker",
                "remaining_output_tokens": 10 + index % 2,
                "target_training_eligible": {"remaining_decode_demand": True},
            }],
        }
        for index in range(size)
    ]
    requests = [{"split": "train", "request_id": f"{run_id}-request"}]
    manifest = {
        "dataset_kind": "beliefkv_p6_training_evidence",
        "evaluation_role": "frozen_split_local_training_evidence",
        "formal_training_eligible": False,
        "formal_local_training_eligible": True,
        "integrity": {"passes": True},
        "split_contract": {
            "source": "explicit frozen split manifest",
            "development_only": False,
            "manifest_digest": "frozen-split-hash",
        },
        "tables": {
            "frontier_decision_points": _write_table(
                root, "frontier_decision_points", decisions
            ),
            "request_calls": _write_table(root, "request_calls", requests),
        },
        "source": {
            "run_id": run_id,
            "workload_manifest_sha256": ["workload-hash"],
            "collection_contract": {
                "plan_id": "qwen35-native-reactive-v0520-v1",
                "split": "train",
                "runtime_policy": "frozen_native_reactive_v0520",
                "raw_trace_eligible": True,
                "model_revision_stable": True,
                "runtime_source_stable": True,
                "predictor_enabled": False,
                "predictive_actions_enabled": False,
            },
            "runtime_environment_contract": {
                "runtime_kind": "native_reactive_v0520",
                "uniform": True,
                "model_revision_sha256": {
                    "config.json": "config-hash", "tokenizer.json": "tokenizer-hash"
                },
                "server_identity": {
                    "weight_dtype": "bfloat16",
                    "resolved_kv_dtype": "bfloat16",
                },
                "hardware": {"uuid": "GPU-test"},
                "sglang_commit": "commit",
                "sglang_patch_sha256": "patch",
            },
            "native_request_evidence": {
                "schema_version": 1,
                "telemetry_complete": True,
                "complete_request_count": 1,
                "missing_or_incomplete_request_count": 0,
                "status": {
                    "schema_version": 1,
                    "source": "native_sglang_v0520",
                    "writer_error": None,
                    "pending_request_count": 0,
                    "pending_batch_count": 0,
                    "failed_records": 0,
                    "dropped_records": 0,
                    "record_counts": {"events": 2, "audit": 1, "transfer": 0},
                },
            },
        },
    }
    (root / "dataset_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def _args(tmp_path: Path, *roots: Path) -> list[str]:
    return [
        *(part for root in roots for part in ("--dataset-dir", str(root))),
        "--model-version", "qwen35-native-test",
        "--output", str(tmp_path / "model.json"),
    ]


def _mutate(root: Path, field: str, value: object) -> None:
    path = root / "dataset_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    target = manifest
    keys = field.split(".")
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = value
    path.write_text(json.dumps(manifest), encoding="utf-8")


def test_formal_train_fit_is_offline_uncalibrated_and_seals_tests(tmp_path: Path) -> None:
    root = _dataset(tmp_path / "train", size=40)
    assert main(_args(tmp_path, root)) == 0
    raw = json.loads((tmp_path / "model.json").read_text(encoding="utf-8"))
    metadata = raw["metadata"]
    assert raw["training_summary"]["split_counts"] == {"train": 40}
    assert raw["training_summary"]["action_target_count"] == 0
    assert raw["training_summary"]["operational_timing"]["sample_count"] == 0
    assert raw["calibration_summary"] == {}
    assert raw["components"]["operational_release"]["training_count"] == 0
    assert metadata["formal_diversity_gate"]["project_count"] == 5
    assert metadata["formal_diversity_gate"]["task_count"] == 40
    assert metadata["formal_diversity_gate"]["workflow_count"] == 40
    assert metadata["development_only"] is False
    assert metadata["online_eligible"] is False
    assert metadata["predictive_action_eligible"] is False
    assert metadata["offline"] is True
    assert metadata["calibration_status"] == "uncalibrated"
    assert metadata["test_id_status"] == "sealed_not_evaluated"
    assert metadata["test_ood_status"] == "sealed_not_evaluated"
    assert metadata["pcie_service_head"] == "not_fitted_no_verified_evidence"
    assert FrontierBeliefModel.load(tmp_path / "model.json").training_summary[
        "decision_point_count"
    ] == 40


def test_native_train_preserves_target_local_censoring(tmp_path: Path) -> None:
    root = _dataset(tmp_path / "train", size=40)
    manifest_path = root / "dataset_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source"]["native_request_evidence"]["missing_or_incomplete_request_count"] = 1
    requests = root / "request_calls.jsonl"
    with requests.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"request_id": "censored", "split": "train"}) + "\n")
    manifest["tables"]["request_calls"]["row_count"] = 2
    manifest["tables"]["request_calls"]["sha256"] = hashlib.sha256(
        requests.read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert main(_args(tmp_path, root)) == 0


@pytest.mark.parametrize(
    "plan_id",
    (
        "qwen35-native-reactive-v0520-v3",
        "qwen35-native-reactive-v0520-v4-128root",
        "qwen35-native-reactive-v0520-v5-overlapped-128root",
    ),
)
def test_native_train_accepts_new_frozen_dynamic_plan_ids(
    tmp_path: Path, plan_id: str
) -> None:
    root = _dataset(tmp_path / plan_id, size=40)
    _mutate(root, "source.collection_contract.plan_id", plan_id)

    assert main(_args(tmp_path, root)) == 0


def test_small_fit_requires_explicit_development_override(tmp_path: Path) -> None:
    root = _dataset(tmp_path / "train")
    args = _args(tmp_path, root)
    with pytest.raises(ValueError, match="workflow memorization"):
        main(args)
    assert not (tmp_path / "model.json").exists()
    with pytest.raises(SystemExit, match="2"):
        main(args + ["--minimum-projects", "1"])
    with pytest.raises(SystemExit, match="2"):
        main(args + ["--minimum-projects", "6"])
    assert main(args + [
        "--development-only", "--minimum-projects", "1",
        "--minimum-tasks", "1", "--minimum-workflows", "1",
    ]) == 0
    metadata = json.loads((tmp_path / "model.json").read_text())["metadata"]
    assert metadata["development_only"] is True
    assert metadata["online_eligible"] is False


@pytest.mark.parametrize(("field", "value"), [
    ("evaluation_role", "development_diagnostic"),
    ("formal_local_training_eligible", False),
    ("source.collection_contract.plan_id", "p6-agent-semantics-v1"),
    ("source.collection_contract.split", "calibration"),
    ("source.collection_contract.runtime_policy", "frozen_p5_observed"),
    ("source.collection_contract.predictor_enabled", True),
    ("source.runtime_environment_contract.runtime_kind", "other"),
    ("split_contract.development_only", True),
    ("split_contract.manifest_digest", ""),
    ("source.native_request_evidence.telemetry_complete", False),
    ("source.native_request_evidence.missing_or_incomplete_request_count", -1),
    ("source.native_request_evidence.status.failed_records", 1),
    ("integrity.passes", False),
])
def test_preflight_rejects_unverified_inputs(
    tmp_path: Path, field: str, value: object
) -> None:
    root = _dataset(tmp_path / "train")
    _mutate(root, field, value)
    with pytest.raises(ValueError, match="verified native"):
        main(_args(tmp_path, root))
    assert not (tmp_path / "model.json").exists()


@pytest.mark.parametrize("table", ["frontier_decision_points", "request_calls"])
def test_preflight_rejects_mixed_split_even_when_table_rehashed(
    tmp_path: Path, table: str
) -> None:
    root = _dataset(tmp_path / "train")
    path = root / f"{table}.jsonl"
    row = json.loads(path.read_text().splitlines()[0])
    row["split"] = "test_id"
    path.write_text(json.dumps(row) + "\n")
    _mutate(root, f"tables.{table}.sha256", hashlib.sha256(path.read_bytes()).hexdigest())
    with pytest.raises(ValueError, match="non-train"):
        main(_args(tmp_path, root))
    assert not (tmp_path / "model.json").exists()


def test_preflight_rejects_tampered_table_and_duplicate_runs(tmp_path: Path) -> None:
    root = _dataset(tmp_path / "train")
    with (root / "request_calls.jsonl").open("a") as stream:
        stream.write("{}\n")
    with pytest.raises(ValueError, match="table is not verified"):
        main(_args(tmp_path, root))
    first = _dataset(tmp_path / "first")
    other = _dataset(tmp_path / "other")
    with pytest.raises(ValueError, match="duplicate source run"):
        main(_args(tmp_path, first, other))
    assert not (tmp_path / "model.json").exists()


def test_cli_rejects_nontrain_and_action_inputs(tmp_path: Path) -> None:
    root = _dataset(tmp_path / "train")
    for option in ("--split", "--action-target", "--calibration-dataset-dir"):
        with pytest.raises(SystemExit, match="2"):
            main(_args(tmp_path, root) + [option, "test_id"])
    assert not (tmp_path / "model.json").exists()


def test_preflight_rejects_invalid_thresholds_duplicate_dirs_and_empty_fit(
    tmp_path: Path,
) -> None:
    root = _dataset(tmp_path / "train")
    args = _args(tmp_path, root)
    with pytest.raises(SystemExit, match="2"):
        main(args + ["--development-only", "--minimum-tasks", "0"])
    with pytest.raises(ValueError, match="duplicate dataset directory"):
        main(_args(tmp_path, root, root))
    decisions = root / "frontier_decision_points.jsonl"
    row = json.loads(decisions.read_text().splitlines()[0])
    row["training_eligible"] = False
    decisions.write_text(json.dumps(row) + "\n")
    _mutate(
        root,
        "tables.frontier_decision_points.sha256",
        hashlib.sha256(decisions.read_bytes()).hexdigest(),
    )
    with pytest.raises(ValueError, match="eligible train decision rows"):
        main(args)
    assert not (tmp_path / "model.json").exists()
