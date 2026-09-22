"""Regression gates for the isolated, intentionally incomplete v0.5.20 port."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "patches/sglang-v0.5.20-beliefkv-staging.patch"
CHECKOUT = ROOT / "third_party/sglang-v0.5.20"
MODEL = ROOT / "configs/migration/2026-09-22_qwen35_model_artifact.json"
ENV = ROOT / "configs/migration/2026-09-22_next_environment.json"


def test_staging_patch_remains_fail_closed() -> None:
    patch = PATCH.read_text()
    assert "BeliefKV v0.5.20 activation unsupported" in patch
    assert "beliefkv_metadata" in patch
    assert "test/srt/test_beliefkv_metadata.py" in patch
    assert "test/srt/test_beliefkv_scheduler_hook.py" in patch


def test_patch_applies_to_pinned_upstream_index() -> None:
    if not (CHECKOUT / ".git").exists():
        pytest.skip("the optional SGLang source checkout is absent")
    assert subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=CHECKOUT, text=True
    ).strip() == "94602c9c2b7cbdb8efd5c52802dac6a1c180089e"
    subprocess.run(
        ["git", "apply", "--cached", "--check", str(PATCH)],
        cwd=CHECKOUT,
        check=True,
    )


def test_new_model_and_environment_are_frozen() -> None:
    model = json.loads(MODEL.read_text())
    assert len(model["files"]) == 17
    assert len([name for name in model["files"] if name.endswith(".safetensors")]) == 14
    assert all(
        entry["size_bytes"] > 0 and len(entry["sha256"]) == 64
        for entry in model["files"].values()
    )
    env = json.loads(ENV.read_text())
    assert env["sglang"]["installed_version"] == "0.5.20"
    assert env["sglang"]["source_is_active"] is False
    assert env["sglang"]["source_commit"] == "94602c9c2b7cbdb8efd5c52802dac6a1c180089e"
    assert env["model_manifest_sha256"] == hashlib.sha256(MODEL.read_bytes()).hexdigest()
    assert env["staging_patch_sha256"] == hashlib.sha256(PATCH.read_bytes()).hexdigest()
    pip_packages = {p["name"].lower(): p["version"] for p in env["pip_packages"]}
    assert pip_packages["langchain"] == "1.3.14"
    assert pip_packages["langchain-openai"] == "1.1.9"
    assert pip_packages["openai"] == "2.6.1"
