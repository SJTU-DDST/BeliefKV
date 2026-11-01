"""Expose unchanged ignored regression fixtures in the isolated source operator."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path("/home/longhao/experiment/.worktrees/beliefkv-original19-source-operator-75414c7e")
MAIN = Path("/home/longhao/experiment/BeliefKV")
REPORTS = MAIN / "experiments/reports/remaining_original_projects_20261030"
HERE = REPORTS / "remaining_node_original19_cpu_fixtures_r1"
FAILED = REPORTS / "remaining_node_original19_bound_cpu_r1/bound_cpu.xml"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.train_workflow_work import write


def main():
    if HERE.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve attempts and keep CPU-only nice19 settings")
    failed = [
        {"classname": row.get("classname"), "name": row.get("name"),
         "message": item.get("message"), "text": item.text}
        for row in ET.parse(FAILED).iter("testcase")
        for item in row if item.tag in ("failure", "error")
    ]
    if len(failed) != 4 or any("FileNotFoundError" not in row["text"] for row in failed):
        raise ValueError("review all unexpected qualification failures before fixture binding")
    links = [
        "third_party/sglang-v0.5.20",
        "experiments/reports/original_project_supplement_20261030/composite48_r1",
        "experiments/reports/remaining_original_projects_20261030/event_tool_replacement_window2s_cpu_r1",
        "experiments/reports/remaining_original_projects_20261030/sustained56_initial12_mean70_r1",
    ]
    actual_inputs = [
        MAIN / "third_party/sglang-v0.5.20/python/sglang/srt/mem_cache/radix_cache.py",
        MAIN / "third_party/sglang-v0.5.20/python/sglang/srt/mem_cache/unified_radix_cache.py",
        MAIN / links[1] / "composite_source.json",
        MAIN / links[2] / "full.json",
        MAIN / links[3] / "full.json",
    ]
    before = {str(path): sha256_file(path) for path in actual_inputs}
    for name in links:
        link, target = ROOT / name, MAIN / name
        if not target.is_dir() or link.exists() or link.is_symlink():
            raise ValueError("expected an absent ignored fixture and an intact original: " + name)
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target, target_is_directory=True)
    if any(sha256_file(path) != before[str(path)] for path in actual_inputs):
        raise ValueError("fixture bytes changed")
    write(HERE / "binding.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "original_failed_report": str(FAILED), "original_failed_report_sha256": sha256_file(FAILED),
        "failures": failed, "unchanged_ignored_fixture_symlinks": {
            str(ROOT / name): str(MAIN / name) for name in links
        },
        "inputs_sha256": {**before, str(Path(__file__)): sha256_file(Path(__file__))},
        "production_or_test_code_changed": False, "test_assertions_weakened": False,
        "new_full_CPU_qualification_required": True, "GPU_action": False,
    })
    print(json.dumps({"missing_ignored_fixture_paths_bound": 4, "files_pinned": len(before),
                      "project_source_and_tests_changed": False, "GPU_action": False}))


if __name__ == "__main__":
    main()
