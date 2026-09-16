#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any

from beliefkv.experiments.runtime_profile import (
    load_runtime_profile,
    validate_beliefkv_service_bindings,
    validate_server_against_runtime_profile,
)
from beliefkv.experiments.server_contract import fetch_server_info
from beliefkv.experiments.source_provenance import git_source_state, sha256_file


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _dirty_worktree_allowed() -> bool:
    return os.environ.get("BELIEFKV_ALLOW_DIRTY_WORKTREE") == "1"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate a frozen BeliefKV runtime profile before and after startup."
    )
    parser.add_argument("--runtime-profile", type=Path, required=True)
    parser.add_argument("--sglang-root", type=Path, required=True)
    parser.add_argument("--beliefkv-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--phase", choices=("preflight", "server"), default="server"
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:18000")
    parser.add_argument("--wait-seconds", type=float, default=0.0)
    return parser.parse_args()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _environment_manifest(profile: dict[str, Any]) -> dict[str, Any]:
    path = Path(str(profile["_environment_path"]))
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("environment manifest must contain a JSON object")
    return payload


def _model_contract(
    profile: dict[str, Any], environment: dict[str, Any]
) -> dict[str, Any]:
    model_path = Path(str(profile["_model_path"]))
    if not (model_path / "config.json").is_file():
        raise RuntimeError(f"model is missing: {model_path}")
    expected_hashes = environment.get("model", {}).get("revision_file_sha256", {})
    checks: list[dict[str, Any]] = []
    for relative, expected in sorted(expected_hashes.items()):
        source = model_path / relative
        actual = sha256_file(source) if source.is_file() else None
        checks.append(
            {
                "path": relative,
                "expected_sha256": expected,
                "actual_sha256": actual,
                "passed": actual == expected,
            }
        )
    if not checks or not all(bool(row["passed"]) for row in checks):
        raise RuntimeError("model revision contract failed")
    return {"model_path": str(model_path), "revision_checks": checks, "passed": True}


def _artifact_contract(profile: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    artifacts = profile["artifacts"]
    specifications = (
        (
            "gpu_service",
            "path",
            "sha256",
            "hardware_key",
            artifacts["gpu_service"]["hardware_key"],
        ),
        (
            "gpu_service_evaluation",
            "evaluation",
            "evaluation_sha256",
            "hardware_key",
            artifacts["gpu_service"]["hardware_key"],
        ),
        (
            "transfer_service",
            "path",
            "sha256",
            "hardware_key",
            artifacts["transfer_service"]["hardware_key"],
        ),
    )
    for name, path_key, sha_key, identity_key, expected_identity in specifications:
        owner = artifacts[
            "gpu_service" if name.startswith("gpu_service") else "transfer_service"
        ]
        source = Path(str(owner[path_key]))
        if not source.is_absolute():
            source = Path(str(profile["_repository_root"])) / source
        source = source.resolve()
        actual_sha = sha256_file(source) if source.is_file() else None
        payload = (
            json.loads(source.read_text(encoding="utf-8"))
            if source.is_file()
            else {}
        )
        actual_identity = payload.get(identity_key)
        row = {
            "name": name,
            "path": str(source),
            "expected_sha256": owner[sha_key],
            "actual_sha256": actual_sha,
            "expected_hardware_key": expected_identity,
            "actual_hardware_key": actual_identity,
        }
        row["passed"] = (
            actual_sha == owner[sha_key] and actual_identity == expected_identity
        )
        rows.append(row)
    if not all(bool(row["passed"]) for row in rows):
        raise RuntimeError("hardware service artifact contract failed")
    return {"artifacts": rows, "passed": True}


def _gpu_contract(environment: dict[str, Any]) -> dict[str, Any]:
    expected = environment.get("gpu")
    if not isinstance(expected, dict):
        raise RuntimeError("environment manifest omitted the GPU contract")
    visible = os.environ.get("CUDA_VISIBLE_DEVICES", "0").split(",")[0].strip()
    fields = "index,name,uuid,driver_version,memory.total"
    output = subprocess.check_output(
        (
            "nvidia-smi",
            f"--id={visible}",
            f"--query-gpu={fields}",
            "--format=csv,noheader,nounits",
        ),
        text=True,
        stderr=subprocess.STDOUT,
    ).strip()
    values = [item.strip() for item in output.split(",")]
    if len(values) != 5:
        raise RuntimeError(f"unexpected nvidia-smi result: {output!r}")
    actual = {
        "index": int(values[0]),
        "name": values[1],
        "uuid": values[2],
        "driver_version": values[3],
        "memory_total_mib": int(values[4]),
    }
    checks = {
        "name": actual["name"] == expected.get("name"),
        "uuid": actual["uuid"] == expected.get("uuid"),
        "driver_version": actual["driver_version"]
        == expected.get("driver_version"),
        "memory_total_mib": actual["memory_total_mib"]
        == int(expected.get("memory_total_mib") or 0),
    }
    if not all(checks.values()):
        raise RuntimeError(
            "GPU hardware contract failed: "
            + ", ".join(name for name, passed in checks.items() if not passed)
        )
    return {"expected": expected, "actual": actual, "checks": checks, "passed": True}


def _source_contract(
    profile: dict[str, Any], sglang_root: Path, output: Path
) -> dict[str, Any]:
    output_resolved = output.expanduser().resolve()
    excluded: list[str] = []
    try:
        excluded.append(str(output_resolved.relative_to(REPOSITORY_ROOT)))
    except ValueError:
        pass
    beliefkv = git_source_state(REPOSITORY_ROOT, exclude_paths=excluded)
    dirty_allowed = _dirty_worktree_allowed()
    if beliefkv["dirty"] and not dirty_allowed:
        raise RuntimeError("BeliefKV worktree is dirty; commit the code before launch")

    expected_commit = str(profile["runtime"]["sglang_commit"])
    actual_commit = subprocess.check_output(
        ("git", "rev-parse", "HEAD"),
        cwd=sglang_root,
        text=True,
    ).strip()
    if actual_commit != expected_commit:
        raise RuntimeError(
            f"SGLang commit {actual_commit} does not match {expected_commit}"
        )
    sglang = git_source_state(
        sglang_root,
        canonical_patch=Path(str(profile["_canonical_sglang_patch"])),
    )
    canonical = sglang["canonical_patch"]
    if canonical["expected_tree"] != profile["source_contract"]["expected_sglang_tree"]:
        raise RuntimeError("SGLang canonical tree does not match the runtime profile")
    return {
        "beliefkv": beliefkv,
        "beliefkv_dirty_worktree_allowed": dirty_allowed,
        "sglang": sglang,
        "passed": True,
    }


def _preflight(
    profile: dict[str, Any], profile_sha: str, args: argparse.Namespace
) -> dict[str, Any]:
    environment = _environment_manifest(profile)
    config = json.loads(
        args.beliefkv_config.expanduser().resolve().read_text(encoding="utf-8")
    )
    if not isinstance(config, dict):
        raise RuntimeError("BeliefKV config must contain a JSON object")
    return {
        "schema_version": 1,
        "contract_state": "preflight_passed",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime_profile": {
            "path": profile["_profile_path"],
            "profile_id": profile["profile_id"],
            "sha256": profile_sha,
        },
        "source": _source_contract(
            profile, args.sglang_root.expanduser().resolve(), args.output
        ),
        "model": _model_contract(profile, environment),
        "hardware": _gpu_contract(environment),
        "service_artifacts": _artifact_contract(profile),
        "service_bindings": validate_beliefkv_service_bindings(config, profile),
    }


def _wait_for_server(base_url: str, wait_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + max(0.0, wait_seconds)
    last_error: Exception | None = None
    while True:
        try:
            return fetch_server_info(base_url, timeout_s=10.0)
        except Exception as error:
            last_error = error
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"SGLang did not satisfy the ready endpoint: {last_error}"
                ) from last_error
            time.sleep(2.0)


def main() -> int:
    args = _parse_args()
    profile, profile_sha = load_runtime_profile(
        args.runtime_profile,
        repository_root=REPOSITORY_ROOT,
    )
    payload = _preflight(profile, profile_sha, args)
    if args.phase == "server":
        info = _wait_for_server(args.base_url, args.wait_seconds)
        payload["server"] = validate_server_against_runtime_profile(info, profile)
        payload["contract_state"] = "validated"
        payload["validated_at_utc"] = datetime.now(timezone.utc).isoformat()
    _atomic_json(args.output, payload)
    print(json.dumps(payload["runtime_profile"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
