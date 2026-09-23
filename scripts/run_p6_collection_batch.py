#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from beliefkv.experiments.agent_protocol import LoopGuardPolicy
from beliefkv.experiments.deepagents_swebench import (
    DeepAgentsExperimentConfig,
    run_experiment,
    write_json,
)
from beliefkv.experiments.harness_preflight import preflight_command_for_policy
from beliefkv.experiments.p6_collection import load_collection_batch
from beliefkv.experiments.server_contract import (
    capacity_contract,
    fetch_server_info,
    validate_server_identity,
    validate_native_reactive_v0520,
)
from beliefkv.experiments.model_migration import inspect_model_config
from beliefkv.runtime.context_lifecycle import ContextLifecyclePolicy
from beliefkv.runtime.langchain_tool_safety import ToolObservationBudgetPolicy


DEFAULT_PAUSE_FILE = Path("/tmp/beliefkv-experiments.paused")
DEFAULT_HARNESS_PROFILES = REPOSITORY_ROOT / "configs/p6/harness_profiles_v1.json"
DEFAULT_QWEN35_INVENTORY = (
    REPOSITORY_ROOT / "configs/migration/2026-09-22_qwen35_model_artifact.json"
)
NATIVE_MODEL_MANIFEST_FILES = (
    "config.json", "model.safetensors.index.json", "tokenizer.json",
    "tokenizer_config.json",
)
NATIVE_SCHEDULER_PATH = (
    REPOSITORY_ROOT
    / "third_party/sglang-v0.5.20/python/sglang/srt/managers/scheduler.py"
)
NATIVE_PATCH_PATH = REPOSITORY_ROOT / "patches/sglang-v0.5.20-beliefkv-staging.patch"
NATIVE_TELEMETRY_STREAMS = (
    "runtime_events.sglang.jsonl",
    "runtime_audit.jsonl",
    "transfer_telemetry.jsonl",
    "host_pool_telemetry.jsonl",
)
RAW_TRACE_MIN_COVERAGE = 0.95
WORKFLOW_EXCLUSIONS_FILENAME = "TRAINING_EXCLUSIONS.json"


def _native_telemetry_fresh(directory: Path) -> bool:
    if any(
        not (directory / name).is_file()
        or (directory / name).stat().st_size
        for name in NATIVE_TELEMETRY_STREAMS
    ):
        return False
    status_path = directory / "native_telemetry_status.json"
    if not status_path.is_file():
        return True
    status = json.loads(status_path.read_text(encoding="utf-8"))
    return bool(
        status.get("schema_version") == 1
        and status.get("source") == "native_sglang_v0520"
        and status.get("writer_error") is None
        and status.get("pending_request_count") == 0
        and status.get("pending_batch_count") == 0
        and not any((status.get("record_counts") or {}).values())
    )


def _workflow_trace_issues(workflow: dict[str, object]) -> list[str]:
    trace = workflow.get("trace")
    control = workflow.get("runtime_control_delivery")
    if not isinstance(trace, dict) or not isinstance(control, dict):
        return ["missing_trace_or_control_delivery"]
    issues: list[str] = []
    if bool(control.get("degraded")):
        issues.append("runtime_control_delivery_degraded")
    for field in (
        "workflow_lifecycle_valid",
        "llm_pairing_valid",
        "tool_pairing_valid",
    ):
        if not bool(trace.get(field)):
            issues.append(field)
    for field in ("tool_status_coverage", "workspace_digest_coverage"):
        try:
            coverage = float(trace.get(field, 0.0))
        except (TypeError, ValueError):
            coverage = -1.0
        if not 0.0 <= coverage <= 1.0:
            issues.append(f"invalid_{field}")
        elif coverage < 1.0:
            issues.append(f"incomplete_{field}")
    return issues


def _workflow_trace_complete(workflow: dict[str, object]) -> bool:
    return not _workflow_trace_issues(workflow)


def _workflow_export_assessment(
    summary: dict[str, object],
) -> tuple[list[dict[str, str]], int]:
    workflows = summary.get("workflows")
    if not isinstance(workflows, list):
        return [], 0
    exclusions: list[dict[str, str]] = []
    trace_complete = 0
    for workflow in workflows:
        if not isinstance(workflow, dict):
            continue
        issues = _workflow_trace_issues(workflow)
        trace_complete += int(not issues)
        if not issues:
            continue
        instance_id = str(workflow.get("instance_id") or "")
        if not instance_id:
            continue
        exclusions.append(
            {
                "instance_id": instance_id,
                "reason": "trace_telemetry_incomplete:" + ",".join(issues),
            }
        )
    return exclusions, trace_complete


def _raw_trace_coverage_passed(
    complete_workflows: int, workflow_count: int
) -> bool:
    return bool(
        workflow_count > 0
        and complete_workflows / workflow_count >= RAW_TRACE_MIN_COVERAGE
    )


def _native_model_manifest(
    model_path: Path, inventory_path: Path
) -> dict[str, str]:
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if (
        not isinstance(inventory, dict)
        or Path(str(inventory.get("model_path"))).resolve() != model_path.resolve()
        or not isinstance(inventory.get("files"), dict)
    ):
        raise ValueError("Qwen3.5 inventory does not match the requested model")
    hashes = {}
    for filename in NATIVE_MODEL_MANIFEST_FILES:
        source = model_path / filename
        if not source.is_file():
            raise ValueError(f"Qwen3.5 model manifest is missing {filename}")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        expected = inventory["files"].get(filename)
        if not isinstance(expected, dict) or expected.get("sha256") != digest:
            raise ValueError(f"Qwen3.5 model inventory changed: {filename}")
        hashes[filename] = digest
    return hashes


def _native_runtime_contract(
    *,
    gpu: int,
    identity: dict[str, object],
    capacity: dict[str, object],
    model_manifest: dict[str, str],
    source_fingerprint: dict[str, object],
) -> dict[str, object]:
    checkout = REPOSITORY_ROOT / "third_party/sglang-v0.5.20"
    commit = subprocess.check_output(
        ("git", "-C", str(checkout), "rev-parse", "HEAD"), text=True
    ).strip()
    if commit != "94602c9c2b7cbdb8efd5c52802dac6a1c180089e":
        raise RuntimeError("native reactive scheduler checkout revision changed")
    uuid = subprocess.check_output(
        (
            "nvidia-smi", "-i", str(gpu), "--query-gpu=uuid",
            "--format=csv,noheader",
        ),
        text=True,
    ).strip()
    if not uuid.startswith("GPU-") or "\n" in uuid:
        raise RuntimeError("native reactive GPU UUID is unavailable")
    return {
        "schema_version": 1,
        "contract_state": "validated",
        "runtime_kind": "native_reactive_v0520",
        "runtime_profile": None,
        "model_revision_sha256": model_manifest,
        "server_identity": identity,
        "server_capacity": capacity,
        "hardware": {"uuid": uuid, "gpu_index": gpu},
        "sglang_commit": commit,
        "sglang_patch_sha256": hashlib.sha256(
            NATIVE_PATCH_PATH.read_bytes()
        ).hexdigest(),
        "beliefkv_source_sha256": source_fingerprint["digest"],
    }


def _actual_kv_pool_tokens(base_url: str, *, timeout_s: float = 10.0) -> int:
    payload = fetch_server_info(base_url, timeout_s=timeout_s)
    if not isinstance(payload, dict):
        raise RuntimeError("SGLang /get_server_info did not return a JSON object")
    try:
        actual = int(payload["max_total_num_tokens"])
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError(
            "SGLang /get_server_info omitted a valid max_total_num_tokens"
        ) from error
    if actual <= 0:
        raise RuntimeError("SGLang reported a non-positive KV pool capacity")
    return actual


def _materialize_runtime_workload_manifest(
    *,
    source_path: Path,
    destination: Path,
    profile_path: Path,
    image_lock_path: Path | None,
    selected_instance_ids: list[str] | None,
) -> tuple[Path, list[dict[str, object]], int]:
    source = json.loads(source_path.read_text(encoding="utf-8"))
    profiles = json.loads(profile_path.read_text(encoding="utf-8"))
    if profiles.get("schema_version") != 1:
        raise ValueError("unsupported P6 harness profile schema")
    profile_by_instance = profiles.get("instances", {})
    image_lock_by_tag: dict[str, str] = {}
    if image_lock_path is not None:
        image_lock = json.loads(image_lock_path.read_text(encoding="utf-8"))
        if image_lock.get("lock_state") != "frozen_local_images":
            raise ValueError("image lock is not frozen_local_images")
        rows = image_lock.get("images")
        if not isinstance(rows, list) or not rows:
            raise ValueError("image lock contains no images")
        for raw in rows:
            if raw.get("status") != "pulled_verified":
                raise ValueError("image lock contains an unverified image")
            tag = str(raw.get("image") or "")
            digest = str(raw.get("repo_digest") or "")
            if not tag or "@sha256:" not in digest:
                raise ValueError("image lock contains an invalid immutable identity")
            image_lock_by_tag[tag] = digest
    selected = set(selected_instance_ids or ())
    workloads = [
        dict(item)
        for item in source.get("workloads", [])
        if not selected or str(item.get("instance_id")) in selected
    ]
    found = {str(item.get("instance_id")) for item in workloads}
    if selected:
        missing = sorted(selected - found)
        if missing:
            raise ValueError(f"instances are absent from source manifest: {missing}")
    applied: list[dict[str, object]] = []
    for item in workloads:
        instance_id = str(item.get("instance_id"))
        repo = str(item.get("repo"))
        item.pop("preflight_command", None)
        profile = profile_by_instance.get(instance_id)
        if not isinstance(profile, dict):
            if image_lock_path is not None:
                source_image = str(item.get("docker_image"))
                try:
                    item["docker_image"] = image_lock_by_tag[source_image]
                except KeyError as error:
                    raise ValueError(
                        f"image lock omitted source image for {instance_id}: "
                        f"{source_image}"
                    ) from error
            continue
        if str(profile.get("repo")) != repo:
            raise ValueError(f"harness profile repo mismatch for {instance_id}")
        source_image = str(item.get("docker_image"))
        if source_image != str(profile.get("source_image")):
            raise ValueError(f"harness profile source image mismatch for {instance_id}")
        runtime_image = str(profile["runtime_image"])
        item["docker_image"] = runtime_image
        policy = profile.get("preflight_policy")
        preflight = preflight_command_for_policy(
            str(policy) if policy is not None else None
        )
        if preflight is not None:
            item["preflight_command"] = preflight
        applied.append(
            {
                "instance_id": instance_id,
                "repo": repo,
                "source_image": source_image,
                "runtime_image": runtime_image,
                "preflight_policy": profile.get("preflight_policy"),
            }
        )
    runtime = {
        **source,
        "workloads": workloads,
        "source_workload_manifest": str(source_path),
        "harness_profile_id": profiles.get("profile_id"),
        "harness_profile_sha256": hashlib.sha256(profile_path.read_bytes()).hexdigest(),
        "image_lock": str(image_lock_path) if image_lock_path is not None else None,
        "image_lock_sha256": (
            hashlib.sha256(image_lock_path.read_bytes()).hexdigest()
            if image_lock_path is not None
            else None
        ),
    }
    write_json(destination, runtime)
    return destination, applied, len(workloads)


def _runtime_source_fingerprint() -> dict[str, object]:
    roots = (
        REPOSITORY_ROOT / "beliefkv/control",
        REPOSITORY_ROOT / "beliefkv/core",
        REPOSITORY_ROOT / "beliefkv/metrics",
        REPOSITORY_ROOT / "beliefkv/policy",
        REPOSITORY_ROOT / "beliefkv/runtime",
    )
    files = [
        path
        for root in roots
        for path in root.rglob("*.py")
        if path.is_file()
    ]
    files.extend(
        path
        for path in (
            REPOSITORY_ROOT / "beliefkv/experiments/agent_protocol.py",
            REPOSITORY_ROOT / "beliefkv/experiments/deepagents_swebench.py",
            REPOSITORY_ROOT / "beliefkv/experiments/model_migration.py",
            REPOSITORY_ROOT / "beliefkv/experiments/p6_collection.py",
            REPOSITORY_ROOT / "beliefkv/experiments/server_contract.py",
            REPOSITORY_ROOT / "beliefkv/experiments/harness_preflight.py",
            REPOSITORY_ROOT / "beliefkv/experiments/langgraph_peer_workflow.py",
            REPOSITORY_ROOT / "scripts/launch_deepagents_swebench_server.sh",
            REPOSITORY_ROOT / "scripts/prepare_deepagents_server_config.py",
            REPOSITORY_ROOT / "scripts/run_deepagents_swebench.py",
            REPOSITORY_ROOT / "scripts/run_p6_collection_batch.py",
            REPOSITORY_ROOT / "patches/sglang-0.5.2rc1-beliefkv.patch",
            REPOSITORY_ROOT / "patches/sglang-v0.5.20-beliefkv-staging.patch",
            REPOSITORY_ROOT / "scripts/launch_qwen35_native_v0520.sh",
        )
        if path.is_file()
    )
    digest = hashlib.sha256()
    for path in sorted(set(files)):
        relative = path.relative_to(REPOSITORY_ROOT).as_posix()
        content = path.read_bytes()
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(content).digest())
    return {
        "algorithm": "sha256(path\\0sha256(content))",
        "digest": digest.hexdigest(),
        "file_count": len(set(files)),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one frozen P6 agent-semantics collection batch."
    )
    parser.add_argument("--collection-plan", type=Path, required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument(
        "--instance-id",
        action="append",
        default=[],
        help=(
            "run only this instance from the frozen batch; repeat for a targeted "
            "harness recovery collection"
        ),
    )
    parser.add_argument("--allow-calibration", action="store_true")
    parser.add_argument("--allow-test", action="store_true")
    parser.add_argument(
        "--predictor-shadow-enabled",
        action="store_true",
        help=(
            "Record that the P6 frontier predictor ran in shadow mode on the "
            "serving side. The dataset exporter will mark this run formal "
            "ineligible, which is the correct provenance for a shadow run."
        ),
    )
    parser.add_argument(
        "--predictive-risk-shadow-enabled",
        action="store_true",
        help=(
            "Record that the serving-side P6 scenario-risk observer evaluated "
            "read-only would-actions; no predictive command is dispatched."
        ),
    )
    parser.add_argument(
        "--predictive-joint-enabled",
        action="store_true",
        help=(
            "Deprecated provenance flag for historical runs. The current "
            "serving path never grants this flag predictive action authority."
        ),
    )
    parser.add_argument(
        "--predictive-joint-overlay-enabled",
        action="store_true",
        help=(
            "Record that the serving-side predictive JointPlan may reorder "
            "execution/admission and dispatch PREPARE_HOST/PREFETCH_GPU."
        ),
    )
    parser.add_argument(
        "--predictive-prefetch-canary-enabled",
        action="store_true",
        help=(
            "Record that the bounded serving-side PREFETCH_GPU canary is enabled."
        ),
    )
    parser.add_argument(
        "--frontier-retraction-shadow-enabled",
        action="store_true",
        help="Record observed/frontier selective-retraction comparisons.",
    )
    parser.add_argument(
        "--frontier-retraction-canary-limit",
        type=int,
        default=0,
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:18000/v1")
    parser.add_argument("--model", required=True)
    parser.add_argument("--expected-model-path", type=Path, required=True)
    parser.add_argument(
        "--native-model-inventory", type=Path,
        default=DEFAULT_QWEN35_INVENTORY,
    )
    parser.add_argument(
        "--native-telemetry-dir", type=Path,
        help="Dedicated server/ directory from a patched native-reactive scheduler.",
    )
    parser.add_argument("--expected-weight-dtype", default="bfloat16")
    parser.add_argument("--expected-kv-dtype", default="bfloat16")
    parser.add_argument(
        "--kv-bytes-per-token", type=int,
        help="Legacy full-KV scalar; forbidden for hybrid native reactive collection.",
    )
    parser.add_argument(
        "--hbm-safety-margin-bytes", type=int, default=1_073_741_824
    )
    parser.add_argument("--control-socket", type=Path)
    parser.add_argument("--server-audit", type=Path)
    parser.add_argument("--server-events", type=Path)
    parser.add_argument("--server-log", type=Path)
    parser.add_argument("--gpu", type=int, default=0)
    parser.add_argument(
        "--pool-tokens",
        type=int,
        default=None,
        help=(
            "minimum actual SGLang max_total_num_tokens required by this run; "
            "the server-reported value is used for pressure accounting"
        ),
    )
    parser.add_argument("--max-completion-tokens", type=int, default=4096)
    parser.add_argument("--context-window-tokens", type=int)
    parser.add_argument("--context-keep-tokens", type=int, default=8_192)
    parser.add_argument("--summary-output-tokens", type=int, default=2_048)
    parser.add_argument("--tool-observation-turn-chars", type=int, default=65_536)
    parser.add_argument("--tool-observation-result-chars", type=int, default=16_384)
    parser.add_argument("--recursion-limit", type=int, default=2048)
    parser.add_argument(
        "--subagent-fanout-profile",
        choices=(
            "natural",
            "parallel_analysis_2to3",
            "native_subagent_2to3",
            "native_dynamic_1to4",
        ),
        help=(
            "optional assertion of the profile frozen in the collection batch; "
            "it cannot override the manifest"
        ),
    )
    parser.add_argument(
        "--workflow-arrival-interval-ms",
        type=float,
        help="optional assertion of the value frozen in the collection batch",
    )
    parser.add_argument(
        "--workflow-arrival-batch-size",
        type=int,
        help="optional assertion of the value frozen in the collection batch",
    )
    parser.add_argument(
        "--workflow-arrival-batch-interval-ms",
        type=float,
        help="optional assertion of the value frozen in the collection batch",
    )
    parser.add_argument(
        "--saturated-root-backlog",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "optional assertion of the root-submission mode frozen in the "
            "collection batch"
        ),
    )
    parser.add_argument("--request-timeout", type=float, default=7200.0)
    parser.add_argument(
        "--stop-after-first-native-join",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Diagnostic semantic gate only; controlled-stop rows are excluded "
            "from training and JCT."
        ),
    )
    parser.add_argument("--sandbox-command-timeout", type=int, default=600)
    parser.add_argument("--runtime-event-ack-timeout", type=float, default=10.0)
    parser.add_argument("--runtime-event-ack-retries", type=int, default=3)
    parser.add_argument(
        "--harness-profiles",
        type=Path,
        default=DEFAULT_HARNESS_PROFILES,
    )
    parser.add_argument(
        "--image-lock",
        type=Path,
        help="Frozen image requirements used to replace mutable tags by RepoDigest.",
    )
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pause_file = Path(
        os.environ.get("BELIEFKV_EXPERIMENT_PAUSE_FILE", str(DEFAULT_PAUSE_FILE))
    ).expanduser()
    if pause_file.exists():
        print(f"BeliefKV experiments are paused: {pause_file}", file=sys.stderr)
        return 75
    if (
        args.predictive_prefetch_canary_enabled
        and not args.predictive_joint_overlay_enabled
    ):
        raise ValueError(
            "predictive prefetch canary provenance requires predictive overlay"
        )
    if args.frontier_retraction_canary_limit < 0:
        raise ValueError("frontier retraction canary limit must be non-negative")
    batch = load_collection_batch(
        args.collection_plan,
        args.batch_id,
        allow_calibration=args.allow_calibration,
        allow_test=args.allow_test,
    )
    native_reactive = batch.runtime_policy == "frozen_native_reactive_v0520"
    if native_reactive:
        if (
            args.model != "Qwen3.5-35B-A3B"
            or args.native_telemetry_dir is None
            or args.control_socket is not None
            or args.server_audit is not None
            or args.server_events is not None
            or args.kv_bytes_per_token is not None
            or any((
                args.predictor_shadow_enabled,
                args.predictive_risk_shadow_enabled,
                args.predictive_joint_enabled,
                args.predictive_joint_overlay_enabled,
                args.predictive_prefetch_canary_enabled,
                args.frontier_retraction_shadow_enabled,
                args.frontier_retraction_canary_limit,
            ))
        ):
            raise ValueError(
                "Qwen3.5 native reactive collection requires the target model, "
                "dedicated native telemetry, no BeliefKV control or predictive "
                "actions, and no legacy KV scalar"
            )
        if args.expected_kv_dtype != "bfloat16":
            raise ValueError("native Qwen3.5 reactive collection requires BF16 KV")
    elif args.native_telemetry_dir is not None:
        raise ValueError("native telemetry is only valid for frozen native reactive")
    elif (
        args.control_socket is None or args.server_audit is None
        or args.server_events is None or args.server_log is None
    ):
        raise ValueError("legacy P6 collection requires control and server traces")
    if (
        args.subagent_fanout_profile is not None
        and args.subagent_fanout_profile != batch.subagent_fanout_profile
    ):
        raise ValueError(
            "CLI fanout profile differs from the frozen collection batch: "
            f"{args.subagent_fanout_profile} != {batch.subagent_fanout_profile}"
        )
    frozen_schedule = {
        "workflow_arrival_interval_ms": batch.workflow_arrival_interval_ms,
        "workflow_arrival_batch_size": batch.workflow_arrival_batch_size,
        "workflow_arrival_batch_interval_ms": (
            batch.workflow_arrival_batch_interval_ms
        ),
        "saturated_root_backlog": batch.saturated_root_backlog,
    }
    for field, frozen_value in frozen_schedule.items():
        cli_value = getattr(args, field)
        if cli_value is not None and cli_value != frozen_value:
            option = "--" + field.replace("_", "-")
            raise ValueError(
                f"{option} differs from the frozen collection batch: "
                f"{cli_value} != {frozen_value}"
            )
    workflow_arrival_interval_ms = batch.workflow_arrival_interval_ms
    workflow_arrival_batch_size = batch.workflow_arrival_batch_size
    workflow_arrival_batch_interval_ms = (
        batch.workflow_arrival_batch_interval_ms
    )
    saturated_root_backlog = batch.saturated_root_backlog
    fanout_profile = batch.subagent_fanout_profile
    frozen_semantic_gate = batch.semantic_gate_stop_after_first_join
    if (
        args.stop_after_first_native_join is not None
        and args.stop_after_first_native_join != frozen_semantic_gate
    ):
        raise ValueError(
            "--stop-after-first-native-join differs from the frozen collection batch"
        )
    minimum_pool_tokens = (
        args.pool_tokens if args.pool_tokens is not None
        else 1 if native_reactive else 163_840
    )
    if minimum_pool_tokens <= 0:
        raise ValueError("--pool-tokens must be positive")
    server_info = fetch_server_info(args.base_url)
    identity_check = (
        validate_native_reactive_v0520 if native_reactive
        else validate_server_identity
    )
    server_identity = identity_check(
        server_info,
        expected_model=args.model,
        expected_model_path=args.expected_model_path,
        expected_weight_dtype=args.expected_weight_dtype,
        expected_kv_dtype=args.expected_kv_dtype,
    )
    context_window_tokens = (
        args.context_window_tokens if args.context_window_tokens is not None
        else 65_536 if native_reactive else 131_072
    )
    server_context_length = int(server_info.get("context_length") or 0)
    if native_reactive and (
        server_context_length <= 0
        or context_window_tokens + args.max_completion_tokens + 1_024
        > server_context_length
    ):
        raise ValueError(
            "native reactive context window plus completion/reserve exceeds "
            "the server's live context length"
        )
    model_config = (
        json.loads((args.expected_model_path / "config.json").read_text(
            encoding="utf-8"
        )) if native_reactive else None
    )
    geometry = inspect_model_config(model_config) if native_reactive else None
    if native_reactive and (
        model_config.get("model_type") != "qwen3_5_moe"
        or (model_config.get("text_config") or {}).get("model_type")
        != "qwen3_5_moe_text"
    ):
        raise ValueError("native reactive model config is not Qwen3.5-35B-A3B")
    if native_reactive and not geometry["hybrid_linear_or_mamba"]:
        raise ValueError("native Qwen3.5 collection requires hybrid model geometry")
    native_model_manifest = (
        _native_model_manifest(
            args.expected_model_path, args.native_model_inventory
        ) if native_reactive else None
    )
    server_capacity = capacity_contract(
        server_info,
        kv_bytes_per_token=(
            None if native_reactive else args.kv_bytes_per_token or 98_304
        ),
        hbm_safety_margin_bytes=args.hbm_safety_margin_bytes,
    )
    actual_pool_tokens = int(server_capacity["max_total_num_tokens"])
    if actual_pool_tokens < minimum_pool_tokens:
        raise RuntimeError(
            "SGLang actual KV pool is below the collection requirement: "
            f"actual={actual_pool_tokens}, required={minimum_pool_tokens}"
        )
    telemetry_dir = args.native_telemetry_dir.resolve() if native_reactive else None
    if telemetry_dir is not None:
        ready_path = telemetry_dir / "native_telemetry_ready.json"
        ready = json.loads(ready_path.read_text(encoding="utf-8"))
        scheduler_pid = ready.get("scheduler_pid")
        if (
            ready.get("schema_version") != 1
            or ready.get("source") != "native_sglang_v0520"
            or type(scheduler_pid) is not int
            or scheduler_pid <= 0
            or ready.get("scheduler_path") != str(NATIVE_SCHEDULER_PATH)
            or ready.get("scheduler_sha256") != hashlib.sha256(
                NATIVE_SCHEDULER_PATH.read_bytes()
            ).hexdigest()
            or not all(
                (telemetry_dir / filename).is_file()
                for filename in NATIVE_TELEMETRY_STREAMS
            )
        ):
            raise RuntimeError("native scheduler telemetry is not ready")
        try:
            os.kill(scheduler_pid, 0)
        except OSError as error:
            raise RuntimeError("native scheduler telemetry process is gone") from error
        output = args.output or telemetry_dir.parent / "workloads"
        if output.resolve().parent != telemetry_dir.parent:
            raise ValueError("native telemetry and workloads must share a run directory")
        if output.exists() or not _native_telemetry_fresh(telemetry_dir):
            raise ValueError("native reactive batch requires fresh telemetry and output")
    else:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output = args.output or (
            REPOSITORY_ROOT
            / "experiments/raw/p6_agent_semantics_v1"
            / batch.batch_id
            / timestamp
            / "workloads"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    selected_instance_ids: list[str] | None = None
    if args.instance_id:
        selected_instance_ids = list(dict.fromkeys(args.instance_id))
    image_lock_path = (
        args.image_lock.expanduser().resolve()
        if args.image_lock is not None
        else None
    )
    workload_manifest, harness_profiles, workflow_count = (
        _materialize_runtime_workload_manifest(
            source_path=batch.workload_manifest,
            destination=output.parent / "runtime_workload_manifest.json",
            profile_path=args.harness_profiles.expanduser().resolve(),
            image_lock_path=image_lock_path,
            selected_instance_ids=selected_instance_ids,
        )
    )
    configured_concurrency = min(batch.concurrency, workflow_count)
    concurrency = (
        workflow_count if saturated_root_backlog else configured_concurrency
    )
    source_fingerprint = _runtime_source_fingerprint()
    native_runtime_contract = (
        _native_runtime_contract(
            gpu=args.gpu,
            identity=server_identity,
            capacity=server_capacity,
            model_manifest=native_model_manifest,
            source_fingerprint=source_fingerprint,
        )
        if native_reactive else None
    )
    if native_runtime_contract is not None:
        write_json(
            telemetry_dir / "native_runtime_contract.json",
            native_runtime_contract,
        )
    collection_contract = {
        "schema_version": 1,
        "plan_id": batch.plan_id,
        "batch_id": batch.batch_id,
        "split": batch.split,
        "workload_manifest": str(workload_manifest),
        "source_workload_manifest": str(batch.workload_manifest),
        "harness_profile_manifest": str(args.harness_profiles.resolve()),
        "harness_profile_manifest_sha256": hashlib.sha256(
            args.harness_profiles.read_bytes()
        ).hexdigest(),
        "applied_harness_profiles": harness_profiles,
        "image_lock": str(image_lock_path) if image_lock_path is not None else None,
        "image_lock_sha256": (
            hashlib.sha256(image_lock_path.read_bytes()).hexdigest()
            if image_lock_path is not None
            else None
        ),
        "selected_instance_ids": selected_instance_ids,
        "workflow_count": workflow_count,
        "configured_concurrency": configured_concurrency,
        "concurrency": concurrency,
        "workflow_arrival_interval_ms": workflow_arrival_interval_ms,
        "workflow_arrival_batch_size": workflow_arrival_batch_size,
        "workflow_arrival_batch_interval_ms": (
            workflow_arrival_batch_interval_ms
        ),
        "saturated_root_backlog": saturated_root_backlog,
        "root_submission_mode": (
            "all_roots_eager"
            if saturated_root_backlog
            else "arrival_schedule"
        ),
        "client_inflight_root_window": concurrency,
        "initial_unsubmitted_root_backlog": (
            0
            if saturated_root_backlog
            else max(0, workflow_count - concurrency)
        ),
        "required_minimum_pool_tokens": minimum_pool_tokens,
        "actual_pool_tokens": actual_pool_tokens,
        "server_identity": server_identity,
        "server_capacity": server_capacity,
        "hybrid_model_geometry": geometry,
        "model_revision_sha256": native_model_manifest,
        "native_model_inventory_sha256": (
            hashlib.sha256(args.native_model_inventory.read_bytes()).hexdigest()
            if native_reactive else None
        ),
        "native_reactive": native_reactive,
        "formal_dataset_export_ready": not native_reactive,
        "native_telemetry_dir": str(telemetry_dir) if telemetry_dir else None,
        "native_runtime_contract": native_runtime_contract,
        "predictor_enabled": (
            args.predictor_shadow_enabled
            or args.predictive_risk_shadow_enabled
            or args.predictive_joint_enabled
            or args.predictive_joint_overlay_enabled
        ),
        "predictive_actions_enabled": args.predictive_joint_overlay_enabled,
        "predictive_risk_shadow_enabled": args.predictive_risk_shadow_enabled,
        "predictive_joint_overlay_enabled": (
            args.predictive_joint_overlay_enabled
        ),
        "predictive_prefetch_canary_enabled": (
            args.predictive_prefetch_canary_enabled
        ),
        "frontier_retraction_shadow_enabled": (
            args.frontier_retraction_shadow_enabled
        ),
        "frontier_retraction_canary_limit": (
            args.frontier_retraction_canary_limit
        ),
        "predictive_transfer_model": (
            None if native_reactive else "extent_count_aware"
        ),
        "joint_predictive_enabled": args.predictive_joint_enabled,
        "legacy_predictive_flag_requested": args.predictive_joint_enabled,
        "runtime_policy": (
            "p6_predictive_joint"
            if args.predictive_joint_overlay_enabled
            else batch.runtime_policy
        ),
        "subagent_fanout_profile": fanout_profile,
        "initial_delegation_mode": (
            "model_selected_runtime_plan_1to4"
            if fanout_profile == "native_dynamic_1to4"
            else "profile_defined"
        ),
        "stop_after_first_native_join": frozen_semantic_gate,
        "request_timeout_s": args.request_timeout,
        "completion_semantics": "model_terminal_no_harness_llm_repair",
        "completion_gate_enabled": False,
        "completion_repair_attempts": 0,
        "runtime_event_ack_timeout_s": args.runtime_event_ack_timeout,
        "runtime_event_ack_retries": args.runtime_event_ack_retries,
        "context_lifecycle": {
            "window_tokens": context_window_tokens,
            "keep_tokens": args.context_keep_tokens,
            "intermediate_output_tokens": args.max_completion_tokens,
            "summary_output_tokens": args.summary_output_tokens,
        },
        "graph_step_safety": {
            "semantic_patterns": "telemetry_only",
            "soft_budget_mode": "telemetry_only",
            "soft_budget": LoopGuardPolicy().graph_step_soft_budget,
            "hard_limit": LoopGuardPolicy().graph_step_hard_limit,
            "reserve": LoopGuardPolicy().graph_step_reserve,
            "hard_limit_mode": "safety_finalization",
        },
        "tool_observation_budget": {
            "total_chars_per_turn": args.tool_observation_turn_chars,
            "max_chars_per_result": args.tool_observation_result_chars,
        },
        "runtime_source_fingerprint_start": source_fingerprint,
        "training_eligible": None,
    }
    write_json(
        output.parent / f"{output.name}.p6_collection_contract.json",
        collection_contract,
    )
    config = DeepAgentsExperimentConfig(
        mode="autonomous",
        base_url=args.base_url,
        model=args.model,
        output_dir=output,
        workload_manifest=workload_manifest,
        docker_image="unused:per-workload-image-required",
        control_socket=args.control_socket,
        server_audit_path=(
            telemetry_dir / "runtime_audit.jsonl"
            if telemetry_dir else args.server_audit
        ),
        server_event_path=(
            telemetry_dir / "runtime_events.sglang.jsonl"
            if telemetry_dir else args.server_events
        ),
        server_log_path=args.server_log,
        max_workflows=workflow_count,
        concurrency=concurrency,
        workflow_arrival_interval_ms=workflow_arrival_interval_ms,
        workflow_arrival_batch_size=workflow_arrival_batch_size,
        workflow_arrival_batch_interval_ms=(
            workflow_arrival_batch_interval_ms
        ),
        saturated_root_backlog=saturated_root_backlog,
        gpu_index=args.gpu,
        pool_tokens=actual_pool_tokens,
        max_completion_tokens=args.max_completion_tokens,
        subagent_fanout_profile=fanout_profile,
        stop_after_first_native_join=frozen_semantic_gate,
        recursion_limit=args.recursion_limit,
        request_timeout_s=args.request_timeout,
        sandbox_command_timeout_s=args.sandbox_command_timeout,
        sandbox_preflight_command=batch.preflight_command,
        completion_gate_enabled=False,
        completion_repair_attempts=0,
        runtime_event_ack_timeout_s=args.runtime_event_ack_timeout,
        runtime_event_ack_retries=args.runtime_event_ack_retries,
        context_lifecycle=ContextLifecyclePolicy(
            window_tokens=context_window_tokens,
            keep_tokens=args.context_keep_tokens,
            intermediate_output_tokens=args.max_completion_tokens,
            summary_output_tokens=args.summary_output_tokens,
            model_context_tokens=(
                server_context_length if native_reactive else 262_144
            ),
        ),
        loop_guard=(
            replace(
                LoopGuardPolicy(),
                enforce_semantic_guard=False,
                enforce_soft_graph_budget=False,
                activation_wall_clock_s=None,
            )
            if native_reactive else LoopGuardPolicy()
        ),
        tool_observation_budget=ToolObservationBudgetPolicy(
            total_chars_per_turn=args.tool_observation_turn_chars,
            max_chars_per_result=args.tool_observation_result_chars,
        ),
    )
    summary = run_experiment(config)
    workflow_exclusions, trace_complete_workflows = (
        _workflow_export_assessment(summary)
    )
    write_json(
        output.parent / WORKFLOW_EXCLUSIONS_FILENAME,
        {
            "schema_version": 1,
            "workflows": workflow_exclusions,
        },
    )
    final_fingerprint = _runtime_source_fingerprint()
    source_stable = final_fingerprint == source_fingerprint
    model_stable = (
        not native_reactive
        or _native_model_manifest(
            args.expected_model_path, args.native_model_inventory
        ) == native_model_manifest
    )
    system_eligible = (
        summary["system_jct_eligible_workflows"] == workflow_count
    )
    semantic_gate_passed = (
        summary["semantic_gate_completed_workflows"] == workflow_count
    )
    raw_trace_coverage = (
        trace_complete_workflows / workflow_count if workflow_count else 0.0
    )
    raw_trace_coverage_passed = _raw_trace_coverage_passed(
        trace_complete_workflows, workflow_count
    )
    final_contract = {
        **collection_contract,
        "runtime_source_fingerprint_end": final_fingerprint,
        "runtime_source_stable": source_stable,
        "training_eligible": (
            system_eligible and source_stable and model_stable
            and not frozen_semantic_gate
            and not native_reactive
        ),
        "raw_trace_eligible": (
            raw_trace_coverage_passed and source_stable and model_stable
            and not frozen_semantic_gate
        ) if native_reactive else None,
        "trace_complete_workflows": trace_complete_workflows,
        "raw_trace_coverage": raw_trace_coverage,
        "raw_trace_min_coverage": RAW_TRACE_MIN_COVERAGE,
        "raw_trace_coverage_passed": raw_trace_coverage_passed,
        "excluded_workflow_count": len(workflow_exclusions),
        "workflow_exclusions_path": str(
            output.parent / WORKFLOW_EXCLUSIONS_FILENAME
        ),
        "model_revision_stable": model_stable if native_reactive else None,
        "semantic_gate_passed": semantic_gate_passed,
        "ineligibility_reasons": [
            reason
            for condition, reason in (

                (
                    frozen_semantic_gate,
                    "diagnostic_semantic_gate_not_training_evidence",
                ),
                (
                    not frozen_semantic_gate
                    and not native_reactive
                    and not system_eligible,
                    "system_jct_gate_failed",
                ),
                (
                    not frozen_semantic_gate
                    and native_reactive
                    and not raw_trace_coverage_passed,
                    "raw_trace_coverage_below_threshold",
                ),
                (not source_stable, "runtime_source_changed_during_collection"),
                (not model_stable, "model_manifest_changed_during_collection"),
                (
                    native_reactive,
                    "native_reactive_formal_dataset_export_not_yet_validated",
                ),
            )
            if condition
        ],
    }
    summary["p6_collection"] = final_contract
    write_json(output / "p6_collection_contract.json", final_contract)
    write_json(output / "p6_collection_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False))
    passed = (
        semantic_gate_passed and source_stable
        if frozen_semantic_gate
        else (
            final_contract["raw_trace_eligible"] if native_reactive
            else final_contract["training_eligible"]
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
