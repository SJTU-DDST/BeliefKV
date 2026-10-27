#!/usr/bin/env python3
"""Bind shared CPU regression and actual constructors to the offload candidate."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.run_causal_comparison import environment, preflight_runtime


def cases(path):
    root = ET.parse(path)
    suites = list(root.iter("testsuite"))
    if not suites or any(
        int(suite.get(field, "0")) for suite in suites
        for field in ("errors", "failures", "skipped")
    ):
        raise ValueError("shared CPU regression has missing or unsuccessful cases")
    identities = {
        (case.get("classname"), case.get("name")) for case in root.iter("testcase")
    }
    if len(identities) != len(list(root.iter("testcase"))):
        raise ValueError("duplicate CPU testcase identities")
    return identities, sum(int(suite.get("tests", "0")) for suite in suites)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--cpu-report", type=Path, required=True)
    parser.add_argument("--previous-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    previous, _ = cases(args.previous_report)
    current, reported = cases(args.cpu_report)
    if not previous <= current:
        raise ValueError("shared CPU regression omitted prior cases")
    config = ROOT / "configs/migration/predictive_tool_offload_20261027.json"
    common = json.loads(config.read_bytes())["common_environment"]
    reference = {"arms": {"full": common}}
    constructors = {}
    for label in ("full", "thunderagent", "thunderagent_acting_decay"):
        settings, _, _ = environment(
            reference, label, ROOT, args.engine, args.output.parent / "cpu-no-server",
            18654, ROOT, max_running_requests=24, sampling_mode="position",
        )
        constructors[label] = preflight_runtime(settings, common["MODEL_PATH"])
        if constructors[label]["verified_configuration"]["enable_predicted_tool_offload"] is not (
            label == "full"
        ):
            raise ValueError("offload candidate or baseline isolation changed")
    paths = [
        "beliefkv/runtime/predicted_tool_offload.py",
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "beliefkv/experiments/internal_comparison.py",
        "configs/migration/predictive_tool_offload_20261027.json",
        "scripts/run_causal_comparison.py",
        "scripts/audit_workflow_startup.py",
        "scripts/profile_autonomous_graph_startup.py",
        "scripts/qualify_predicted_tool_offload_cpu.py",
        "tests/test_predicted_tool_offload.py", "tests/test_workflow_startup_audit.py",
    ]
    result = {
        "schema_version": 1, "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "source_bound_cpu_offload_and_constructor_qualification",
        "cpu_report": {"path": str(args.cpu_report.resolve()), "sha256": sha256_file(args.cpu_report)},
        "previous_report": {
            "path": str(args.previous_report.resolve()), "sha256": sha256_file(args.previous_report),
        },
        "passing_cases": len(current), "junit_tests_including_subtests": reported,
        "retained_previous_cases": len(previous),
        "new_cases": len(current - previous),
        "source_sha256": {path: sha256_file(ROOT / path) for path in paths},
        "constructors": constructors,
        "native_cpu_tree": str(args.engine.resolve()),
        "copy_event_and_host_index_standins": True,
        "gpu_validation_completed": False, "full_model_correctness_verified": False,
        "performance_accepted": False,
        "preserved_failed_development_reports": [
            "experiments/reports/v301_predicted_tool_offload_lifecycle_cpu_20261027.xml",
            "experiments/reports/v303_predicted_tool_offload_lifecycle_cpu_20261027.xml",
            "experiments/reports/v305_predicted_tool_offload_shared_cpu_20261027.xml",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({key: result[key] for key in (
        "scope", "passing_cases", "retained_previous_cases", "new_cases",
        "gpu_validation_completed", "performance_accepted",
    )}, indent=2))


if __name__ == "__main__":
    main()
