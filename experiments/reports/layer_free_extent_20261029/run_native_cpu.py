"""Run all retained native handoff/cold-prepare tests on the committed engine."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    report = args.output_dir / "native_cpu.xml"
    native = git_source_state(args.engine)
    tests = [
        "srt/test_beliefkv_cold_full_prepare.py", "srt/test_beliefkv_layer_handoff.py",
        "srt/test_beliefkv_layer_groups.py",
    ]
    sources = {name: sha256_file(args.engine / "test" / name) for name in tests}
    command = [
        sys.executable, "-m", "pytest", "-q", *tests, "--tb=short",
        "--junitxml=" + str(report),
    ]
    env = {
        **os.environ, "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21",
        "OMP_NUM_THREADS": "1", "PYTHONPATH": str(args.engine / "python"),
    }
    completed = subprocess.run(command, cwd=args.engine / "test", env=env)
    cases = ET.parse(report).findall(".//testcase")
    bad = [row.attrib for row in cases if any(row.find(tag) is not None for tag in (
        "failure", "error", "skipped",
    ))]
    unchanged = (
        native == git_source_state(args.engine)
        and sources == {name: sha256_file(args.engine / "test" / name) for name in tests}
    )
    result = {
        "command": command, "returncode": completed.returncode, "tests": len(cases),
        "failed_error_skipped_cases": bad, "native_source": native,
        "sources_unchanged_during_execution": unchanged,
        "test_sources_sha256": sources,
        "operator_sha256": sha256_file(Path(__file__)),
        "report": {"path": str(report), "sha256": sha256_file(report)},
        "GPU_validation_pending": True, "performance_accepted": False,
    }
    with (args.output_dir / "evidence.json").open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    if completed.returncode or bad or not unchanged:
        raise RuntimeError("native CPU failed; preserve its original evidence")
    print(json.dumps({"native_cases": len(cases), "GPU_validation_pending": True}))


if __name__ == "__main__":
    main()
