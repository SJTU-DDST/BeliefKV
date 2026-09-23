from __future__ import annotations

import hashlib
import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from beliefkv.experiments.p6_collection import load_collection_batch
from scripts.run_p6_collection_batch import (
    NATIVE_TELEMETRY_STREAMS,
    NATIVE_SCHEDULER_PATH,
    _actual_kv_pool_tokens,
    _materialize_runtime_workload_manifest,
    _native_model_manifest,
    _native_telemetry_fresh,
    _workflow_export_assessment,
    main as run_collection,
)
from scripts.freeze_qwen35_native_reactive_plan import (
    freeze_high_pressure_join_train_plan,
    freeze_native_reactive_train_plan,
)


def test_periodic_idle_native_status_does_not_block_collection(tmp_path: Path) -> None:
    for name in NATIVE_TELEMETRY_STREAMS:
        (tmp_path / name).touch()
    status = {
        "schema_version": 1,
        "source": "native_sglang_v0520",
        "record_counts": {},
        "pending_request_count": 0,
        "pending_batch_count": 0,
        "writer_error": None,
    }
    path = tmp_path / "native_telemetry_status.json"
    path.write_text(json.dumps(status))
    assert _native_telemetry_fresh(tmp_path)
    status["record_counts"] = {"audit": 1}
    path.write_text(json.dumps(status))
    assert not _native_telemetry_fresh(tmp_path)
    status["record_counts"] = {}
    path.write_text(json.dumps(status))
    (tmp_path / NATIVE_TELEMETRY_STREAMS[0]).write_text("{}\n")
    assert not _native_telemetry_fresh(tmp_path)


def test_workflow_errors_are_excluded_when_trace_telemetry_is_complete() -> None:
    trace = {
        "workflow_lifecycle_valid": True,
        "llm_pairing_valid": True,
        "tool_pairing_valid": True,
        "tool_status_coverage": 1.0,
        "workspace_digest_coverage": 1.0,
        "dynamic_subagent_count": 2,
        "all_subagents_returned": True,
        "all_joins_satisfied": True,
    }
    exclusions, complete = _workflow_export_assessment(
        {
            "workflows": [
                {
                    "instance_id": "repo__good",
                    "system_jct_eligible": True,
                    "trace": trace,
                    "runtime_control_delivery": {"degraded": False},
                },
                {
                    "instance_id": "repo__censored",
                    "system_jct_eligible": False,
                    "system_jct_exclusion_reasons": [
                        "outcome:error",
                        "missing_semantic_completion",
                    ],
                    "trace": trace,
                    "runtime_control_delivery": {"degraded": False},
                },
            ]
        }
    )

    assert complete == 2
    assert exclusions == [
        {
            "instance_id": "repo__censored",
            "reason": (
                "workflow_censored:outcome:error,"
                "missing_semantic_completion"
            ),
        }
    ]


def test_native_train_script_exports_after_collection_status_is_captured() -> None:
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts/run_qwen35_native_train_batches.sh"
    ).read_text(encoding="utf-8")

    capture = script.index('collection_status="$?"')
    stop = script.index("stop_server", capture)
    export = script.index("export_native_reactive_p6_dataset.py", stop)
    assert capture < stop < export


def test_actual_kv_pool_tokens_uses_server_report(monkeypatch: pytest.MonkeyPatch) -> None:
    requested_urls: list[str] = []

    def fake_urlopen(url: str, *, timeout: float) -> io.BytesIO:
        requested_urls.append(url)
        assert timeout == 3.0
        return io.BytesIO(b'{"max_total_num_tokens": 167816}')

    monkeypatch.setattr(
        "beliefkv.experiments.server_contract.urllib.request.urlopen",
        fake_urlopen,
    )

    assert _actual_kv_pool_tokens("http://127.0.0.1:18000/v1", timeout_s=3.0) == 167816
    assert requested_urls == ["http://127.0.0.1:18000/get_server_info"]


def _write_fixture(tmp_path: Path, *, split: str = "train") -> Path:
    source = tmp_path / "source"
    (source / ".git").mkdir(parents=True)
    manifest = tmp_path / "workloads.json"
    manifest.write_text(
        json.dumps(
            {
                "dataset": "fixture",
                "dataset_revision": "v1",
                "workloads": [
                    {
                        "instance_id": "repo__task-1",
                        "repo": "org/repo",
                        "base_commit": "abc",
                        "problem_statement": "fix it",
                        "source_repo": str(source),
                        "docker_image": "fixture:latest",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "frozen": True,
                "plan_id": "fixture-plan",
                "predictor_enabled": False,
                "predictive_actions_enabled": False,
                "runtime_policy": "frozen_p5_observed",
                "batches": [
                    {
                        "batch_id": "batch-1",
                        "split": split,
                        "policy": "frozen_p5_observed",
                        "predictive_actions": False,
                        "projects": ["org/repo"],
                        "docker_images": ["fixture:latest"],
                        "workload_manifest": str(manifest),
                        "workload_manifest_sha256": digest,
                        "workflow_count": 1,
                        "concurrency": 1,
                        "preflight_command": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return plan


def test_load_collection_train_batch(tmp_path: Path) -> None:
    batch = load_collection_batch(_write_fixture(tmp_path), "batch-1")
    assert batch.split == "train"
    assert batch.workflow_count == 1
    assert batch.preflight_command is None
    assert batch.subagent_fanout_profile == "natural"
    assert batch.runtime_policy == "frozen_p5_observed"


def test_native_reactive_collection_requires_separate_frozen_policy(tmp_path: Path) -> None:
    plan = _write_fixture(tmp_path)
    raw = json.loads(plan.read_text(encoding="utf-8"))
    raw["runtime_policy"] = "frozen_native_reactive_v0520"
    raw["batches"][0]["policy"] = "frozen_native_reactive_v0520"
    plan.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="frozen source plan"):
        load_collection_batch(plan, "batch-1")
    raw["source_plan"] = str(plan)
    raw["source_plan_sha256"] = "invalid"
    plan.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="changed after freeze"):
        load_collection_batch(plan, "batch-1")
    original = _write_fixture(tmp_path / "donor")
    native = tmp_path / "native.json"
    freeze_native_reactive_train_plan(original, native)
    assert load_collection_batch(native, "batch-1").runtime_policy == (
        "frozen_native_reactive_v0520"
    )
    frozen = json.loads(native.read_text(encoding="utf-8"))
    frozen["batches"][0]["policy"] = "frozen_p5_observed"
    native.write_text(json.dumps(frozen), encoding="utf-8")
    with pytest.raises(ValueError, match="batch policy"):
        load_collection_batch(native, "batch-1")


def test_native_plan_freezes_only_train_without_relabeling_other_splits(
    tmp_path: Path,
) -> None:
    old_plan = _write_fixture(tmp_path, split="train")
    old = json.loads(old_plan.read_text(encoding="utf-8"))
    old["batches"].append({
        **old["batches"][0], "batch_id": "sealed-test", "split": "test_id",
    })
    old_plan.write_text(json.dumps(old), encoding="utf-8")
    native_path = tmp_path / "native.json"
    frozen = freeze_native_reactive_train_plan(old_plan, native_path)
    assert frozen["batch_count"] == 1
    assert frozen["batches"][0]["policy"] == "frozen_native_reactive_v0520"
    assert frozen["batches"][0]["batch_id"] == "batch-1"
    assert frozen["source_plan_sha256"] == hashlib.sha256(
        old_plan.read_bytes()
    ).hexdigest()
    assert load_collection_batch(native_path, "batch-1").runtime_policy == (
        "frozen_native_reactive_v0520"
    )
    assert json.loads(old_plan.read_text(encoding="utf-8")) == old
    with pytest.raises(FileExistsError):
        freeze_native_reactive_train_plan(old_plan, native_path)


def test_join_enriched_native_plan_preserves_source_and_other_batches(
    tmp_path: Path,
) -> None:
    old_plan = _write_fixture(tmp_path, split="train")
    original = json.loads(old_plan.read_text(encoding="utf-8"))
    output = tmp_path / "join.json"
    frozen = freeze_native_reactive_train_plan(
        old_plan, output, join_batch_id="batch-1"
    )
    assert frozen["plan_id"] == "qwen35-native-reactive-v0520-v2"
    assert load_collection_batch(output, "batch-1").subagent_fanout_profile == (
        "native_subagent_2to3"
    )
    assert json.loads(old_plan.read_text(encoding="utf-8")) == original
    with pytest.raises(ValueError, match="JOIN batch"):
        freeze_native_reactive_train_plan(
            old_plan, tmp_path / "invalid.json", join_batch_id="unknown"
        )


def test_join64_plan_contains_distinct_train_tasks_and_anchor(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    plan = freeze_high_pressure_join_train_plan(
        root / "configs/p6/collection_v4/collection_plan.json",
        [root / "configs/p6/h200_bf16_formal_train_v1/collection_plan.json"],
        root / "configs/p6/swebench_verified_split_v1.json",
        tmp_path / "plan.json",
        tmp_path / "manifest.json",
    )
    batch = load_collection_batch(tmp_path / "plan.json", "qwen35-native-join64-train-r0")
    assert (batch.workflow_count, batch.concurrency, batch.saturated_root_backlog) == (
        64, 64, True
    )
    assert batch.subagent_fanout_profile == "native_subagent_2to3"
    assert plan["unique_task_count"] == 64
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    anchor = json.loads(
        (root / "configs/p6/collection_v4/workload_manifests/p6-017-train-mixed-r0.json").read_text()
    )
    assert [item["instance_id"] for item in manifest["workloads"][:8]] == [
        item["instance_id"] for item in anchor["workloads"]
    ]
    assert len({item["instance_id"] for item in manifest["workloads"]}) == 64


def test_native_reactive_collection_preflight_and_raw_trace_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _write_fixture(tmp_path)
    native_plan = tmp_path / "native.json"
    freeze_native_reactive_train_plan(plan, native_plan)
    model = tmp_path / "model"
    model.mkdir()
    config = {
        "model_type": "qwen3_5_moe",
        "text_config": {
            "model_type": "qwen3_5_moe_text",
            "num_hidden_layers": 4, "num_key_value_heads": 2,
            "head_dim": 8, "full_attention_interval": 2,
        }
    }
    (model / "config.json").write_text(json.dumps(config), encoding="utf-8")
    for name in (
        "model.safetensors.index.json", "tokenizer.json", "tokenizer_config.json",
    ):
        (model / name).write_text("{}", encoding="utf-8")
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps({
        "model_path": str(model),
        "files": {
            name: {"sha256": hashlib.sha256((model / name).read_bytes()).hexdigest()}
            for name in (
                "config.json", "model.safetensors.index.json",
                "tokenizer.json", "tokenizer_config.json",
            )
        },
    }), encoding="utf-8")
    assert _native_model_manifest(model, inventory)["config.json"] == (
        json.loads(inventory.read_text(encoding="utf-8"))["files"]["config.json"]["sha256"]
    )
    profiles = tmp_path / "profiles.json"
    profiles.write_text(json.dumps({
        "schema_version": 1, "profile_id": "native", "instances": {},
    }), encoding="utf-8")
    output = tmp_path / "results" / "workloads"
    telemetry = output.parent / "server"
    telemetry.mkdir(parents=True)
    (telemetry / "native_telemetry_ready.json").write_text(json.dumps({
        "schema_version": 1,
        "source": "native_sglang_v0520",
        "scheduler_pid": os.getpid(),
        "scheduler_path": str(NATIVE_SCHEDULER_PATH),
        "scheduler_sha256": hashlib.sha256(
            NATIVE_SCHEDULER_PATH.read_bytes()
        ).hexdigest(),
    }), encoding="utf-8")
    for name in (
        "runtime_events.sglang.jsonl", "runtime_audit.jsonl",
        "transfer_telemetry.jsonl",
    ):
        (telemetry / name).touch()
    info = {
        "served_model_name": "Qwen3.5-35B-A3B",
        "model_path": str(model),
        "dtype": "bfloat16",
        "kv_cache_dtype": "bfloat16",
        "version": "0.5.20",
        "enable_hierarchical_cache": True,
        "hicache_size": 4,
        "hicache_write_policy": "write_back",
        "enable_beliefkv": False,
        "enable_beliefkv_admission": False,
        "beliefkv_admission_prefetch": False,
        "tensor_parallel_size": 1,
        "max_total_num_tokens": 100_000,
        "context_length": 131_072,
    }
    captured = []

    def run(config):
        captured.append(config)
        return {
            "system_jct_eligible_workflows": 0,
            "semantic_gate_completed_workflows": 1,
            "workflows": [
                {
                    "instance_id": "repo__task-1",
                    "system_jct_eligible": False,
                    "system_jct_exclusion_reasons": [
                        "outcome:error",
                        "missing_semantic_completion",
                    ],
                    "trace": {
                        "workflow_lifecycle_valid": True,
                        "llm_pairing_valid": True,
                        "tool_pairing_valid": True,
                        "tool_status_coverage": 1.0,
                        "workspace_digest_coverage": 1.0,
                        "dynamic_subagent_count": 0,
                    },
                    "runtime_control_delivery": {"degraded": False},
                }
            ],
        }

    args = [
        "run_p6_collection_batch.py",
        "--collection-plan", str(native_plan),
        "--batch-id", "batch-1",
        "--model", "Qwen3.5-35B-A3B",
        "--expected-model-path", str(model),
        "--native-model-inventory", str(inventory),
        "--native-telemetry-dir", str(telemetry),
        "--harness-profiles", str(profiles),
        "--output", str(output),
    ]
    monkeypatch.setattr(sys, "argv", args)
    monkeypatch.setattr(
        "scripts.run_p6_collection_batch.fetch_server_info", lambda _: info,
    )
    monkeypatch.setattr(
        "scripts.run_p6_collection_batch._runtime_source_fingerprint",
        lambda: {"digest": "stable"},
    )
    monkeypatch.setattr(
        "scripts.run_p6_collection_batch.run_experiment", run,
    )
    monkeypatch.setattr(
        "scripts.run_p6_collection_batch._native_runtime_contract",
        lambda **_: {"contract_state": "validated", "runtime_kind": "native_reactive_v0520"},
    )
    assert run_collection() == 0
    assert captured[0].control_socket is None
    assert captured[0].server_audit_path == telemetry / "runtime_audit.jsonl"
    assert captured[0].server_event_path == telemetry / "runtime_events.sglang.jsonl"
    assert json.loads(
        (telemetry / "native_runtime_contract.json").read_text()
    )["contract_state"] == "validated"
    assert captured[0].loop_guard.enforce_semantic_guard is False
    assert captured[0].loop_guard.enforce_soft_graph_budget is False
    assert captured[0].loop_guard.enforce_graph_step_budget is True
    assert captured[0].loop_guard.activation_wall_clock_s is None
    assert captured[0].context_lifecycle.window_tokens == 65_536
    assert captured[0].context_lifecycle.model_context_tokens == 131_072
    contract = json.loads((output / "p6_collection_contract.json").read_text())
    assert contract["runtime_policy"] == "frozen_native_reactive_v0520"
    assert contract["raw_trace_eligible"] is True
    assert contract["trace_complete_workflows"] == 1
    assert contract["excluded_workflow_count"] == 1
    assert contract["training_eligible"] is False
    assert contract["server_capacity"]["kv_bytes_per_token"] is None
    assert contract["server_capacity"]["kv_pool_bytes"] is None
    assert contract["model_revision_stable"] is True
    assert contract["formal_dataset_export_ready"] is False
    assert contract["graph_step_safety"]["semantic_patterns"] == "telemetry_only"
    assert contract["graph_step_safety"]["soft_budget_mode"] == "telemetry_only"
    assert contract["graph_step_safety"]["hard_limit_mode"] == "safety_finalization"
    exclusions = json.loads(
        (output.parent / "TRAINING_EXCLUSIONS.json").read_text()
    )
    assert exclusions["workflows"] == [
        {
            "instance_id": "repo__task-1",
            "reason": (
                "workflow_censored:outcome:error,"
                "missing_semantic_completion"
            ),
        }
    ]

    with patch.dict(info, {"enable_beliefkv_admission": True}):
        with pytest.raises(RuntimeError, match="native reactive"):
            run_collection()
    monkeypatch.setattr(sys, "argv", [*args, "--control-socket", "/tmp/invalid.sock"])
    with pytest.raises(ValueError, match="no BeliefKV control"):
        run_collection()
    config["text_config"]["head_dim"] = 16
    (model / "config.json").write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", args)
    with pytest.raises(ValueError, match="inventory changed"):
        run_collection()


def test_collection_batch_freezes_parallel_fanout(tmp_path: Path) -> None:
    plan = _write_fixture(tmp_path)
    raw = json.loads(plan.read_text(encoding="utf-8"))
    raw["batches"][0]["subagent_fanout_profile"] = "parallel_analysis_2to3"
    plan.write_text(json.dumps(raw), encoding="utf-8")

    batch = load_collection_batch(plan, "batch-1")

    assert batch.subagent_fanout_profile == "parallel_analysis_2to3"


def test_collection_batch_accepts_native_dynamic_fanout(tmp_path: Path) -> None:
    plan = _write_fixture(tmp_path)
    raw = json.loads(plan.read_text(encoding="utf-8"))
    raw["batches"][0]["subagent_fanout_profile"] = "native_dynamic_1to4"
    plan.write_text(json.dumps(raw), encoding="utf-8")

    batch = load_collection_batch(plan, "batch-1")

    assert batch.subagent_fanout_profile == "native_dynamic_1to4"


def test_collection_batch_freezes_native_subagent_fanout(tmp_path: Path) -> None:
    plan = _write_fixture(tmp_path)
    raw = json.loads(plan.read_text(encoding="utf-8"))
    raw["batches"][0]["subagent_fanout_profile"] = "native_subagent_2to3"
    raw["batches"][0]["semantic_gate_stop_after_first_join"] = True
    plan.write_text(json.dumps(raw), encoding="utf-8")

    batch = load_collection_batch(plan, "batch-1")

    assert batch.subagent_fanout_profile == "native_subagent_2to3"
    assert batch.semantic_gate_stop_after_first_join is True


def test_collection_batch_freezes_root_arrival_schedule(tmp_path: Path) -> None:
    plan = _write_fixture(tmp_path)
    raw = json.loads(plan.read_text(encoding="utf-8"))
    raw["batches"][0].update(
        {
            "workflow_arrival_interval_ms": 0,
            "workflow_arrival_batch_size": 1,
            "workflow_arrival_batch_interval_ms": 30_000,
            "saturated_root_backlog": False,
        }
    )
    plan.write_text(json.dumps(raw), encoding="utf-8")

    batch = load_collection_batch(plan, "batch-1")

    assert batch.workflow_arrival_interval_ms == 0.0
    assert batch.workflow_arrival_batch_size == 1
    assert batch.workflow_arrival_batch_interval_ms == 30_000.0
    assert batch.saturated_root_backlog is False


def test_collection_batch_rejects_conflicting_root_schedule(tmp_path: Path) -> None:
    plan = _write_fixture(tmp_path)
    raw = json.loads(plan.read_text(encoding="utf-8"))
    raw["batches"][0].update(
        {
            "workflow_arrival_batch_size": 1,
            "workflow_arrival_batch_interval_ms": 30_000,
            "saturated_root_backlog": True,
        }
    )
    plan.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="cannot be combined"):
        load_collection_batch(plan, "batch-1")


def test_collection_batch_keeps_calibration_and_test_sealed(tmp_path: Path) -> None:
    with pytest.raises(PermissionError, match="calibration"):
        load_collection_batch(
            _write_fixture(tmp_path / "cal", split="calibration"), "batch-1"
        )
    with pytest.raises(PermissionError, match="sealed test"):
        load_collection_batch(
            _write_fixture(tmp_path / "test", split="test_id"), "batch-1"
        )


def test_collection_batch_rejects_manifest_mutation(tmp_path: Path) -> None:
    plan = _write_fixture(tmp_path)
    raw = json.loads(plan.read_text(encoding="utf-8"))
    Path(raw["batches"][0]["workload_manifest"]).write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="digest mismatch"):
        load_collection_batch(plan, "batch-1")


def test_runtime_manifest_applies_instance_scoped_harness_profile(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.json"
    source.write_text(
        json.dumps(
            {
                "workloads": [
                    {
                        "instance_id": "psf__requests-5414",
                        "repo": "psf/requests",
                        "docker_image": "source:latest",
                    },
                    {
                        "instance_id": "psf__requests-1142",
                        "repo": "psf/requests",
                        "docker_image": "source-legacy:latest",
                        "preflight_command": "legacy repository-wide check",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    profiles = tmp_path / "profiles.json"
    profiles.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profile_id": "fixture",
                "instances": {
                    "psf__requests-5414": {
                        "repo": "psf/requests",
                        "source_image": "source:latest",
                        "runtime_image": "runtime:harness",
                        "preflight_policy": "psf_requests_pytest_httpbin_v1",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    path, applied, count = _materialize_runtime_workload_manifest(
        source_path=source,
        destination=tmp_path / "runtime.json",
        profile_path=profiles,
        image_lock_path=None,
        selected_instance_ids=None,
    )

    runtime = json.loads(path.read_text(encoding="utf-8"))
    assert count == 2
    assert runtime["workloads"][0]["docker_image"] == "runtime:harness"
    assert "pytest_httpbin" in runtime["workloads"][0]["preflight_command"]
    assert "preflight_command" not in runtime["workloads"][1]
    assert applied[0]["preflight_policy"] == "psf_requests_pytest_httpbin_v1"


def test_runtime_manifest_rejects_unknown_harness_preflight_policy(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.json"
    source.write_text(
        json.dumps(
            {
                "workloads": [
                    {
                        "instance_id": "repo__task-1",
                        "repo": "org/repo",
                        "docker_image": "source:latest",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    profiles = tmp_path / "profiles.json"
    profiles.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profile_id": "fixture",
                "instances": {
                    "repo__task-1": {
                        "repo": "org/repo",
                        "source_image": "source:latest",
                        "runtime_image": "runtime:harness",
                        "preflight_policy": "unknown-v1",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown harness preflight policy"):
        _materialize_runtime_workload_manifest(
            source_path=source,
            destination=tmp_path / "runtime.json",
            profile_path=profiles,
            image_lock_path=None,
            selected_instance_ids=None,
        )


def test_runtime_manifest_replaces_mutable_tag_with_locked_digest(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.json"
    source.write_text(
        json.dumps(
            {
                "workloads": [
                    {
                        "instance_id": "repo__task-1",
                        "repo": "org/repo",
                        "docker_image": "source:latest",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    profiles = tmp_path / "profiles.json"
    profiles.write_text(
        json.dumps({"schema_version": 1, "profile_id": "fixture", "instances": {}}),
        encoding="utf-8",
    )
    image_lock = tmp_path / "images.json"
    image_lock.write_text(
        json.dumps(
            {
                "lock_state": "frozen_local_images",
                "images": [
                    {
                        "image": "source:latest",
                        "repo_digest": "source@sha256:" + "a" * 64,
                        "status": "pulled_verified",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    path, applied, count = _materialize_runtime_workload_manifest(
        source_path=source,
        destination=tmp_path / "runtime.json",
        profile_path=profiles,
        image_lock_path=image_lock,
        selected_instance_ids=None,
    )

    runtime = json.loads(path.read_text(encoding="utf-8"))
    assert count == 1
    assert applied == []
    assert runtime["workloads"][0]["docker_image"] == (
        "source@sha256:" + "a" * 64
    )
    assert runtime["image_lock"] == str(image_lock)
    assert len(runtime["image_lock_sha256"]) == 64
