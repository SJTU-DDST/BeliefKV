from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from beliefkv.predictor.structured_frontier import (
    FrontierBeliefModel,
    evaluate_frontier_model,
    runtime_environment_digest,
)
from beliefkv.runtime.sglang_v0520_prediction import validate_admission_artifact
from scripts.promote_qwen35_admission_predictor import (
    ROOT,
    SGLANG_COMMIT,
    _digest,
    _sha,
    promote,
)


def _write(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return path


def _row(split: str, project: str, index: int) -> dict:
    decision = f"{split}-{index}"
    return {
        "schema_version": 2,
        "decision_id": decision,
        "workflow_id": f"workflow-{decision}",
        "instance_id": f"instance-{decision}",
        "base_commit": "fixed",
        "episode_group_id": f"episode-{decision}",
        "split": split,
        "project": project,
        "trigger_kind": "reactivate",
        "training_eligible": True,
        "invocations": [{
            "invocation_id": "worker",
            "agent_definition_id": "worker",
            "state": "ready",
            "context_tokens": 4096,
            "current_sequence_tokens": 4096,
        }],
        "labels": [{
            "invocation_id": "worker",
            "next_boundary_kind": "final_answer",
            "next_output_tokens": 20 + (index % 3),
            "censored": False,
        }],
    }


def _dataset(root: Path, split: str, contract: dict, rows: list[dict]) -> dict:
    manifest = {
        "dataset_kind": "beliefkv_p6_training_evidence",
        "formal_local_training_eligible": True,
        "evaluation_role": "frozen_split_local_training_evidence",
        "split_contract": {
            "source": "explicit frozen split manifest",
            "development_only": False,
            "manifest_digest": "split-lock",
            "counts_on_request_calls": {split: len(rows)},
        },
        "source": {
            "workload_manifest_sha256": "frozen",
            "runtime_environment_contract": contract,
            "collection_contract": {
                "plan_id": f"qwen35-v0520-{split}",
                "split": split,
                "runtime_source_stable": True,
                "predictor_enabled": False,
                "predictive_actions_enabled": False,
            },
        },
    }
    _write(root / "dataset_manifest.json", manifest)
    (root / "frontier_decision_points.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    return manifest


@pytest.fixture(scope="module")
def evidence(tmp_path_factory):
    root = tmp_path_factory.mktemp("admission-gate")
    model_dir = root / "Qwen3.5-35B-A3B"
    hashes = {}
    for name in ("config.json", "model.safetensors.index.json", "tokenizer.json", "tokenizer_config.json"):
        data = {"file": name}
        if name == "config.json":
            data.update({
                "model_type": "qwen3_5_moe",
                "architectures": ["Qwen3_5MoeForConditionalGeneration"],
                "text_config": {
                    "model_type": "qwen3_5_moe_text",
                    "num_hidden_layers": 40,
                    "full_attention_interval": 4,
                },
            })
        hashes[name] = _sha(_write(model_dir / name, data))
    inventory = _write(root / "inventory.json", {
        "schema_version": 1,
        "model_path": str(model_dir),
        "files": {name: {"sha256": digest} for name, digest in hashes.items()},
    })
    profile = _write(root / "profile.json", {"runtime": "v0.5.20"})
    contract = {
        "runtime_profile": {"profile_id": "qwen35", "sha256": _sha(profile)},
        "model_revision_sha256": hashes,
        "server_identity": {
            "model_path": str(model_dir),
            "served_model_name": "Qwen3.5-35B-A3B",
            "sglang_version": "0.5.20",
            "weight_dtype": "bfloat16",
            "resolved_kv_dtype": "bfloat16",
        },
        "hardware": {"uuid": "test-gpu"},
        "sglang_commit": SGLANG_COMMIT,
        "sglang_patch_sha256": _sha(ROOT / "patches/sglang-v0.5.20-beliefkv-staging.patch"),
    }
    fit_projects = [f"train/{i}" for i in range(5)]
    calibration_projects = ["cal/a", "cal/b"]
    evaluation_projects = ["test/a", "test/b"]
    train = [_row("train", fit_projects[i % 5], i) for i in range(40)]
    cal = [_row("calibration", calibration_projects[i % 2], i) for i in range(16)]
    test = [_row("test_id", evaluation_projects[i % 2], i) for i in range(16)]
    dirs = {split: root / split for split in ("train", "calibration", "test_id")}
    manifests = {
        split: _dataset(dirs[split], split, contract, rows)
        for split, rows in (("train", train), ("calibration", cal), ("test_id", test))
    }
    coverage = _write(dirs["train"] / "coverage_report.json", {
        "coverage_gate_passed": True,
        "dataset_manifest_sha256": _sha(dirs["train"] / "dataset_manifest.json"),
    })
    audit = _write(root / "calibration_audit.json", {
        "split": "calibration",
        "coverage_gate_passed": True,
        "calibration_blockers": [],
        "source": {
            "dataset_dirs": [str(dirs["calibration"])],
            "dataset_manifest_sha256s": [_sha(dirs["calibration"] / "dataset_manifest.json")],
            "runtime_environment_digest": runtime_environment_digest(contract),
        },
    })
    model = FrontierBeliefModel(model_version="qwen35-admission-test")
    fit_summary = model.fit(train)
    cal_summary = model.calibrate(cal)
    metadata = {
        "fit_split": "train",
        "fit_projects": fit_projects,
        "fit_task_count": 40,
        "formal_diversity_gate": {
            "projects": fit_projects, "project_count": 5, "task_count": 40, "workflow_count": 40,
        },
        "calibration_split": "calibration",
        "calibration_projects": calibration_projects,
        "calibration_status": "calibrated",
        "online_eligible": False,
        "predictive_action_eligible": False,
        "development_only": False,
        "beliefkv_worktree_clean": True,
        "test_id_status": "sealed_not_evaluated",
        "dataset_dirs": [str(dirs["train"])],
        "dataset_manifest_file_sha256s": [_sha(dirs["train"] / "dataset_manifest.json")],
        "coverage_report_sha256": _sha(coverage),
        "calibration_dataset_dirs": [str(dirs["calibration"])],
        "calibration_dataset_manifest_digests": [_digest(manifests["calibration"])],
        "calibration_coverage_report": str(audit),
        "calibration_coverage_report_sha256": _sha(audit),
        "deployment_runtime_profile": json.loads(profile.read_text()),
        "deployment_runtime_profile_path": str(profile),
        "runtime_environment_contract_digests": [runtime_environment_digest(contract)],
        "runtime_environment_contracts": [contract],
        "semantic_source_runtime_environment_contracts": [contract],
    }
    assert fit_summary["observation_counts"]["next_output_demand"] == 40
    assert cal_summary["observation_counts"]["next_output_tokens"] == 16
    artifact = _write(root / "fitted.json", model.to_dict(metadata=metadata))
    reports = {}
    for split, rows, projects in (
        ("calibration", cal, calibration_projects),
        ("test_id", test, evaluation_projects),
    ):
        report = evaluate_frontier_model(model, rows)
        report["evaluation_projects"] = sorted(projects)
        reports[split] = _write(root / f"{split}_report.json", report)
    criteria = _write(root / "criteria.json", {
        "max_next_output_mae": 100,
        "min_interval_coverage": 0.8,
        "min_available_rate": 0.9,
    })
    return {
        "root": root, "artifact": artifact, "reports": reports, "dirs": dirs,
        "criteria": criteria, "inventory": inventory, "model_path": model_dir,
    }


def _promote(evidence, tmp_path, **overrides):
    arguments = {
        "artifact": evidence["artifact"],
        "calibration_report": evidence["reports"]["calibration"],
        "evaluation_report": evidence["reports"]["test_id"],
        "evaluation_dirs": [evidence["dirs"]["test_id"]],
        "criteria_path": evidence["criteria"],
        "inventory_path": evidence["inventory"],
        "model_path": evidence["model_path"],
        "output": tmp_path / "online.json",
    }
    arguments.update(overrides)
    return promote(**arguments)


def _modified_artifact(evidence, tmp_path, mutate) -> Path:
    raw = json.loads(evidence["artifact"].read_text())
    mutate(raw)
    return _write(tmp_path / "modified.json", raw)


def test_promotes_only_replayed_admission_evidence(evidence, tmp_path):
    digest = _promote(evidence, tmp_path)
    promoted = tmp_path / "online.json"
    raw = json.loads(promoted.read_text())
    assert _sha(promoted) == digest
    assert raw["metadata"]["online_eligible"] is True
    assert raw["metadata"]["predictive_action_eligible"] is False
    assert raw["metadata"]["admission_eligibility_evidence"]["evaluation_report_sha256"] == _sha(
        evidence["reports"]["test_id"]
    )
    validate_admission_artifact(
        str(promoted), expected_sha256=digest, model_path=str(evidence["model_path"])
    )
    assert json.loads(evidence["artifact"].read_text())["metadata"]["online_eligible"] is False


def test_new_project_timing_contract_cannot_be_promoted_without_validation(
    evidence, tmp_path,
):
    artifact = _modified_artifact(
        evidence, tmp_path,
        lambda raw: raw.update(tool_feature_contract="observed_command_child_project_v3"),
    )
    with pytest.raises(ValueError, match="sealed formal calibrated fit"):
        _promote(evidence, tmp_path, artifact=artifact)


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("development_only", True, "sealed formal"),
        ("calibration_status", "uncalibrated", "sealed formal"),
        ("online_eligible", True, "sealed formal"),
        ("fit_split", "development", "sealed formal"),
    ],
)
def test_rejects_nonformal_artifacts(evidence, tmp_path, field, value, reason):
    artifact = _modified_artifact(
        evidence, tmp_path, lambda raw: raw["metadata"].__setitem__(field, value)
    )
    with pytest.raises(ValueError, match=reason):
        _promote(evidence, tmp_path, artifact=artifact)
    assert not (tmp_path / "online.json").exists()


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"served_model_name": "Qwen3-Coder-30B-A3B-Instruct"}, "model/runtime identity"),
        ({"sglang_version": "0.5.2rc1"}, "model/runtime identity"),
        ({"weight_dtype": "float16"}, "model/runtime identity"),
    ],
)
def test_rejects_old_or_other_runtime_identity(evidence, tmp_path, change, reason):
    artifact = _modified_artifact(
        evidence, tmp_path,
        lambda raw: raw["metadata"]["semantic_source_runtime_environment_contracts"][0][
            "server_identity"
        ].update(change),
    )
    with pytest.raises(ValueError, match=reason):
        _promote(evidence, tmp_path, artifact=artifact)


@pytest.mark.parametrize("value", [None, -1, float("nan"), float("inf")])
def test_rejects_invalid_report_metrics(evidence, tmp_path, value):
    report = json.loads(evidence["reports"]["test_id"].read_text())
    report["scalar"]["next_output_tokens"]["episode_weighted_mae"] = value
    path = _write(tmp_path / "bad_report.json", report)
    with pytest.raises(ValueError, match="metrics do not match replay"):
        _promote(evidence, tmp_path, evaluation_report=path)


def test_rejects_missing_evaluation_rows(evidence, tmp_path):
    missing = tmp_path / "empty"
    missing.mkdir()
    with pytest.raises(FileNotFoundError):
        _promote(evidence, tmp_path, evaluation_dirs=[missing])


def test_rejects_missing_training_evidence(evidence, tmp_path):
    artifact = _modified_artifact(
        evidence, tmp_path,
        lambda raw: raw["training_summary"]["observation_counts"].__setitem__(
            "next_output_demand", 0
        ),
    )
    with pytest.raises(ValueError, match="train demand samples"):
        _promote(evidence, tmp_path, artifact=artifact)


def test_rejects_changed_model_revision(evidence, tmp_path):
    artifact = _modified_artifact(
        evidence, tmp_path,
        lambda raw: raw["metadata"]["semantic_source_runtime_environment_contracts"][0][
            "model_revision_sha256"
        ].__setitem__("config.json", "0" * 64),
    )
    with pytest.raises(ValueError, match="model revision mismatch"):
        _promote(evidence, tmp_path, artifact=artifact)


def test_rejects_coder_config_even_with_spoofed_identity(evidence, tmp_path):
    coder_path = tmp_path / "Qwen3-Coder-30B-A3B-Instruct"
    shutil.copytree(evidence["model_path"], coder_path)
    _write(coder_path / "config.json", {
        "model_type": "qwen3_moe",
        "architectures": ["Qwen3MoeForCausalLM"],
    })
    inventory = json.loads(evidence["inventory"].read_text())
    inventory["model_path"] = str(coder_path)
    inventory["files"]["config.json"]["sha256"] = _sha(coder_path / "config.json")
    inventory_path = _write(tmp_path / "coder_inventory.json", inventory)

    def spoof(raw):
        contract = raw["metadata"]["semantic_source_runtime_environment_contracts"][0]
        contract["server_identity"]["model_path"] = str(coder_path)
        contract["model_revision_sha256"]["config.json"] = _sha(coder_path / "config.json")

    artifact = _modified_artifact(evidence, tmp_path, spoof)
    with pytest.raises(ValueError, match="model config is not Qwen3.5"):
        _promote(
            evidence, tmp_path, artifact=artifact,
            inventory_path=inventory_path, model_path=coder_path,
        )


def test_rejects_overlapping_test_projects(evidence, tmp_path):
    artifact = _modified_artifact(
        evidence, tmp_path,
        lambda raw: raw["metadata"].__setitem__(
            "calibration_projects", ["test/a", "test/b"]
        ),
    )
    with pytest.raises(ValueError, match="calibration projects mismatch"):
        _promote(evidence, tmp_path, artifact=artifact)


def test_rejects_threshold_failure(evidence, tmp_path):
    criteria = json.loads(evidence["criteria"].read_text())
    criteria["max_next_output_mae"] = 0.001
    path = _write(tmp_path / "too_strict.json", criteria)
    with pytest.raises(ValueError, match="demand MAE"):
        _promote(evidence, tmp_path, criteria_path=path)


def test_rejects_profile_or_coverage_tampering(evidence, tmp_path):
    artifact = _modified_artifact(
        evidence, tmp_path,
        lambda raw: raw["metadata"]["deployment_runtime_profile"].__setitem__(
            "runtime", "old"
        ),
    )
    with pytest.raises(ValueError, match="deployment profile"):
        _promote(evidence, tmp_path, artifact=artifact)
    artifact = _modified_artifact(
        evidence, tmp_path,
        lambda raw: raw["metadata"].__setitem__(
            "calibration_coverage_report_sha256", "0" * 64
        ),
    )
    with pytest.raises(ValueError, match="audit missing or changed"):
        _promote(evidence, tmp_path, artifact=artifact)


def test_rejects_calibration_summary_mismatch(evidence, tmp_path):
    artifact = _modified_artifact(
        evidence, tmp_path,
        lambda raw: raw["calibration_summary"].__setitem__("decision_point_count", 17),
    )
    with pytest.raises(ValueError, match="calibration summary does not match replay"):
        _promote(evidence, tmp_path, artifact=artifact)
