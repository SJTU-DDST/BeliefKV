import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/run_qwen35_high_pressure_tool_join_train40.sh"
SOURCE = (
    ROOT
    / "experiments/raw/qwen35_native_reactive_overlapped_128root_train_20260923_v3"
    / "qwen35-native-reactive-overlapped-128root-train-r0"
    / "runtime_workload_manifest.json"
)


def test_high_pressure_training_replays_balanced_frozen_tasks():
    script = SCRIPT.read_text(encoding="utf-8")
    frozen = json.loads(SOURCE.read_text(encoding="utf-8"))
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() in script
    selected = subprocess.check_output([
        "jq", "-r",
        """
        [.workloads[].instance_id
          | select(test("^(django|psf|pydata|pylint-dev|pytest-dev)__"))]
        | sort | group_by(split("__")[0]) | map(.[0:8])
        | transpose | flatten | .[]
        """,
        str(SOURCE),
    ], text=True).splitlines()
    assert len(selected) == len(set(selected)) == 40
    assert set(selected) <= {
        row["instance_id"] for row in frozen["workloads"]
    }
    assert [
        [item.split("__", 1)[0] for item in selected[offset:offset + 5]]
        for offset in range(0, 40, 5)
    ] == [
        ["django", "psf", "pydata", "pylint-dev", "pytest-dev"]
    ] * 8
    assert "CLIENT_CONCURRENCY=40 ARMS=intent" in script
    assert "TOOL_WINDOW_AUDIT_MODE=training_replay" in script
    assert 'WORKFLOW_DEADLINE_SECONDS="${WORKFLOW_DEADLINE_SECONDS:-7200}"' in script
    assert "MIN_AVAILABLE_KIB=$((60 * 1024 * 1024))" in script
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
