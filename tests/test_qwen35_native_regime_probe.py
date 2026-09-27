from __future__ import annotations

import os
from pathlib import Path
import subprocess


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run_qwen35_native_regime_probe.sh"


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
