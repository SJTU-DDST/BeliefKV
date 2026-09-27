import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = (
    "audit_join_parent_first_service.py",
    "evaluate_qwen35_terminal_join_sealed.py",
    "evaluate_qwen35_tool_sealed.py",
)


@pytest.mark.parametrize("name", SCRIPTS)
def test_sealed_script_runs_by_path_outside_repository(name, tmp_path):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / name), "--help"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
