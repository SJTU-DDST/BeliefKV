import hashlib
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/run_qwen35_low_pressure_tool_join_train20.sh"


def test_low_pressure_training_tasks_are_frozen_and_balanced():
    source = (
        ROOT
        / "experiments/raw/qwen35_native_reactive_overlapped_128root_train_20260923_v3"
        / "qwen35-native-reactive-overlapped-128root-train-r0"
        / "runtime_workload_manifest.json"
    )
    script = SCRIPT.read_text(encoding="utf-8")
    expected_sha = re.search(r'EXPECTED_SHA256="([0-9a-f]{64})"', script)
    assert expected_sha is not None
    assert hashlib.sha256(source.read_bytes()).hexdigest() == expected_sha.group(1)

    ids = re.findall(
        r"(?:django|psf|pydata|pylint-dev|pytest-dev)__[a-z]+-[0-9]+",
        script,
    )
    assert len(ids) == len(set(ids)) == 20
    for start in range(0, 20, 5):
        assert [item.split("__", 1)[0] for item in ids[start:start + 5]] == [
            "django", "psf", "pydata", "pylint-dev", "pytest-dev",
        ]
    import json

    frozen = {
        row["instance_id"] for row in
        json.loads(source.read_text(encoding="utf-8"))["workloads"]
    }
    assert set(ids) <= frozen
    assert "CLIENT_CONCURRENCY=4" in script
    assert 'WORKFLOW_DEADLINE_SECONDS="${WORKFLOW_DEADLINE_SECONDS:-7200}"' in script
    assert "TOOL_WINDOW_AUDIT_MODE=training_replay" in script
    assert "MIN_AVAILABLE_KIB=$((60 * 1024 * 1024))" in script
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
