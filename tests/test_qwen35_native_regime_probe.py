from __future__ import annotations

import os
import json
from pathlib import Path
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run_qwen35_native_regime_probe.sh"
TRAIN_SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run_qwen35_native_train_batches.sh"


def test_probe_defaults_to_current_staging_patch() -> None:
    script = SCRIPT.read_text()
    assert 'SGLANG_PATCH_FLAVOR="${SGLANG_PATCH_FLAVOR:-staging}"' in script
    assert 'SGLANG_PATCH_FLAVOR="$SGLANG_PATCH_FLAVOR" \\' in script


def test_probe_defaults_to_training_only_moderate_pressure_candidate() -> None:
    script = SCRIPT.read_text()
    for setting in (
        'ROOT_COUNT="${ROOT_COUNT:-108}"',
        'HICACHE_SIZE_GB="${HICACHE_SIZE_GB:-200}"',
        'HOST_SPLIT="${HOST_SPLIT:-80:20}"',
        'HICACHE_WRITE_POLICY="${HICACHE_WRITE_POLICY:-write_back}"',
        'FANOUT_PROFILE="${FANOUT_PROFILE:-native_in_graph_2to4}"',
    ):
        assert setting in script
    assert '--enable-beliefkv-admission --beliefkv-event-socket-path "$SOCKET"' in script
    assert '--mamba-full-memory-ratio 0.9' in script
    assert 'env -u BELIEFKV_FULL_MAMBA_HOST_SPLIT' in script
    assert '--stream-completion-shadow --child-stream-content-shadow' in script


def test_future_train_collection_defaults_to_user_selected_host_split() -> None:
    script = TRAIN_SCRIPT.read_text()
    assert 'FULL_MAMBA_HOST_SPLIT="${FULL_MAMBA_HOST_SPLIT:-80:20}"' in script
    assert 'env -u BELIEFKV_FULL_MAMBA_HOST_SPLIT' in script
    assert '.capacity.device_full_bytes / .capacity.device_total_bytes' in script


def test_auto_host_split_is_forwarded_explicitly_to_launcher() -> None:
    for path, variable in ((SCRIPT, "HOST_SPLIT"), (TRAIN_SCRIPT, "FULL_MAMBA_HOST_SPLIT")):
        assert f'host_split_env=(BELIEFKV_FULL_MAMBA_HOST_SPLIT="${variable}")' in path.read_text()
    launcher = SCRIPT.with_name("launch_qwen35_native_v0520.sh").read_text()
    assert 'BELIEFKV_FULL_MAMBA_HOST_SPLIT="${BELIEFKV_FULL_MAMBA_HOST_SPLIT:-80:20}"' in launcher
    assert 'if [[ "${BELIEFKV_FULL_MAMBA_HOST_SPLIT}" == auto ]]; then' in launcher
    assert 'unset BELIEFKV_FULL_MAMBA_HOST_SPLIT' in launcher


def test_resident_first_is_common_but_execution_handoff_is_predictive_only() -> None:
    script = SCRIPT.read_text()
    assert 'BELIEFKV_ENABLE_RESIDENT_FIRST=1' in script
    assert 'BELIEFKV_ENABLE_EXECUTION_HANDOFF=0' in script
    assert 'EXECUTION_HANDOFF="${EXECUTION_HANDOFF:-1}"' in script
    assert 'BELIEFKV_ENABLE_EXECUTION_HANDOFF="$EXECUTION_HANDOFF"' in script
    pair = SCRIPT.with_name("run_qwen35_semantic_h2d_ab.sh").read_text()
    assert 'HOST_SPLIT="${HOST_SPLIT:-80:20}"' in pair
    assert 'HOST_SPLIT="$HOST_SPLIT" HICACHE_SIZE_GB=200' in pair
    assert '--host-split "$HOST_SPLIT"' in pair


def test_confirmed_join_requires_verified_ack_patch_before_start(tmp_path: Path) -> None:
    run_root = tmp_path / "probe"
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env={
            **os.environ,
            "RUN_ROOT": str(run_root),
            "CONFIRMED_JOIN_CANARY": "1",
            "SGLANG_PATCH_FLAVOR": "staging",
        },
        text=True,
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 2
    assert "CONFIRMED_JOIN_CANARY" in result.stderr
    assert not run_root.exists()


def test_pressure_scan_rejects_unauthorized_single_wave_above_108(tmp_path: Path) -> None:
    run_root = tmp_path / "probe"
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env={**os.environ, "RUN_ROOT": str(run_root), "ROOT_COUNT": "109"},
        text=True,
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 2
    assert "Usage:" in result.stderr
    assert not run_root.exists()


def test_native_policy_baseline_disables_beliefkv_policy() -> None:
    script = SCRIPT.read_text()
    assert 'control_flags=(--native-policy-baseline)' in script
    assert 'telemetry_env=(BELIEFKV_NATIVE_TELEMETRY_DIR="$RUN_ROOT/server")' in script
    assert 'BELIEFKV_ENABLE_PREPARE_HOST=0 BELIEFKV_ENABLE_TOOL_PREFETCH=0' in script
    assert 'unset_env=(-u BELIEFKV_NATIVE_TELEMETRY_DIR)' in script


def test_pair_driver_freezes_both_arms_and_exports_timelines_automatically():
    script = SCRIPT.with_name("run_qwen35_semantic_h2d_ab.sh").read_text()
    assert '--verify-frozen-plan' in script
    assert 'NATIVE_POLICY_BASELINE="$baseline"' in script
    assert 'ARRIVAL_BATCH_SIZE="$ARRIVAL_BATCH_SIZE"' in script
    assert '--output-html "$RUN_ROOT/timelines/$arm.html"' in script
    assert 'scripts/compare_native_policy_runs.py' in script


def test_native_policy_baseline_rejects_predictive_mix_before_launch(tmp_path: Path) -> None:
    run_root = tmp_path / "baseline"
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env={
            **os.environ, "RUN_ROOT": str(run_root),
            "NATIVE_POLICY_BASELINE": "1", "AB_MODE": "predictive_h2d",
        },
        text=True, capture_output=True, timeout=5,
    )
    assert result.returncode == 2
    assert not run_root.exists()


@pytest.mark.parametrize("mode,native", (("reactive", False), ("predictive_h2d", False), ("off", True)))
def test_effective_policy_environment_without_launching_gpu(tmp_path, mode, native):
    from sys import executable

    binary = tmp_path / "bin"
    binary.mkdir()
    capture = binary / "bash"
    capture.write_text(
        "#!/bin/sh\nexec " + executable + " -c '"
        'import json,os;print(json.dumps({k:v for k,v in os.environ.items() '
        'if k.startswith("BELIEFKV_")}))'
        "'\n"
    )
    capture.chmod(0o755)
    artifact = tmp_path / "model.json"
    artifact.write_text("{}")
    run_root = tmp_path / "probe"
    result = subprocess.run(
        ["/bin/bash", str(SCRIPT)],
        env={
            **os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"],
            "RUN_ROOT": str(run_root), "AB_MODE": mode,
            "NATIVE_POLICY_BASELINE": str(int(native)),
            "PREPARE_HOST": "1", "ENABLE_TOOL_TIMING": "1", "PORT": "1",
            "CONFIRMED_JOIN_CANARY": "0",
            "SEMANTIC_REPORT_ARTIFACT": str(artifact),
            "TOOL_TIMING_ARTIFACT": str(artifact),
            "TRANSFER_SERVICE_SEED": str(artifact),
            "BELIEFKV_SEMANTIC_REPORT_ARTIFACT": "/inherited/unused-semantic.json",
            "BELIEFKV_EMIT_SEMANTIC_TEXT": "1",
        },
        text=True, capture_output=True, timeout=10,
    )
    assert result.returncode == 1
    rows = (run_root / "server.log").read_text().splitlines()
    environment = json.loads(next(line for line in rows if line.startswith("{")))
    predictive = mode == "predictive_h2d"
    assert environment["BELIEFKV_EMIT_SEMANTIC_TEXT"] == str(int(predictive))
    assert environment["BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH"] == str(int(predictive))
    assert environment["BELIEFKV_ENABLE_TOOL_PREFETCH"] == str(int(predictive))
    assert environment["BELIEFKV_ENABLE_PREPARE_HOST"] == str(int(not native))
    assert environment["BELIEFKV_ENABLE_RESIDENT_FIRST"] == str(int(not native))
    if predictive:
        assert environment["BELIEFKV_SEMANTIC_REPORT_ARTIFACT"] == str(artifact)
        assert environment["BELIEFKV_H2D_OPPORTUNITY_SAMPLING"] == "1"
    else:
        assert "BELIEFKV_SEMANTIC_REPORT_ARTIFACT" not in environment
    if not native:
        assert environment["BELIEFKV_TOOL_TIMING_ARTIFACT"] == str(artifact)
    if mode == "reactive":
        assert environment["BELIEFKV_H2D_OPPORTUNITY_SAMPLING"] == "0"
        assert environment["BELIEFKV_ENABLE_EXECUTION_HANDOFF"] == "0"
