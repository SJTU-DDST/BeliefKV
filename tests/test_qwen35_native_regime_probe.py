from __future__ import annotations

import os
from pathlib import Path
import subprocess


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run_qwen35_native_regime_probe.sh"


def test_probe_defaults_to_current_staging_patch() -> None:
    script = SCRIPT.read_text()
    assert 'SGLANG_PATCH_FLAVOR="${SGLANG_PATCH_FLAVOR:-staging}"' in script
    assert 'SGLANG_PATCH_FLAVOR="$SGLANG_PATCH_FLAVOR" \\' in script


def test_probe_defaults_to_training_only_moderate_pressure_candidate() -> None:
    script = SCRIPT.read_text()
    for setting in (
        'ROOT_COUNT="${ROOT_COUNT:-36}"',
        'HICACHE_SIZE_GB="${HICACHE_SIZE_GB:-200}"',
        'HOST_SPLIT="${HOST_SPLIT:-35:65}"',
        'HICACHE_WRITE_POLICY="${HICACHE_WRITE_POLICY:-write_back}"',
        'FANOUT_PROFILE="${FANOUT_PROFILE:-native_in_graph_1to4}"',
    ):
        assert setting in script
    assert '--enable-beliefkv-admission --beliefkv-event-socket-path "$SOCKET"' in script
    assert '--mamba-full-memory-ratio 0.9' in script
    assert '--stream-completion-shadow --child-stream-content-shadow' in script


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


def test_pressure_scan_rejects_more_than_64_roots_before_start(tmp_path: Path) -> None:
    run_root = tmp_path / "probe"
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env={**os.environ, "RUN_ROOT": str(run_root), "ROOT_COUNT": "65"},
        text=True,
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 2
    assert "Usage:" in result.stderr
    assert not run_root.exists()
