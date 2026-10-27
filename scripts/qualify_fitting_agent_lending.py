#!/usr/bin/env python3
"""Run retained shared CPU coverage and bind the explicit fitting candidate."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.policy.thunder_agent_migration import MigratingProgram, ThunderAgentMigration
from scripts.run_causal_comparison import environment as serving_environment, preflight_runtime


REPORTS = ROOT / "experiments/reports"
PRIOR = REPORTS / "v371_pressure_plan_revalidation_cpu_evidence_20261027.json"
REPORT = REPORTS / "v376b_fitting_lending_shared_cpu_20261027.xml"
MANIFEST = REPORTS / "v377b_fitting_lending_cpu_evidence_20261027.sources.json"
OUTPUT = REPORTS / "v377b_fitting_lending_cpu_evidence_20261027.json"
CONFIG = ROOT / "configs/migration/predictive_fitting_lending_20261027.json"


def load(path):
    return json.loads(path.read_bytes())


def write(path, data):
    with path.open("x") as stream:
        json.dump(data, stream, indent=2, allow_nan=False)
        stream.write("\n")


def passed_cases(path):
    cases = list(ET.parse(path).iter("testcase"))
    if not cases or any(
        case.find(tag) is not None for case in cases
        for tag in ("failure", "error", "skipped")
    ):
        raise ValueError("shared CPU report must have no failures, errors or skips")
    return {(row.get("classname"), row.get("name")) for row in cases}


def main():
    if any(path.exists() for path in (REPORT, MANIFEST, OUTPUT)):
        raise FileExistsError("preserve previous qualification outputs")
    prior = load(PRIOR)
    configuration = load(CONFIG)
    paths = set(load(Path(prior["source_manifest"]["path"])))
    paths.update((
        str(CONFIG.relative_to(ROOT)),
        str(Path(__file__).resolve().relative_to(ROOT)),
        "scripts/audit_lending_handover_cost.py", "tests/test_fitting_agent_lending.py",
    ))
    sources = {name: sha256_file(ROOT / name) for name in sorted(paths)}
    write(MANIFEST, sources)
    command = [
        part for part in prior["command"] if not part.startswith("--junitxml=")
    ]
    command.extend((
        "tests/test_fitting_agent_lending.py", f"--junitxml={REPORT.relative_to(ROOT)}",
    ))
    environment = {
        **os.environ, **prior["environment"],
        "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21",
    }
    subprocess.run(command, cwd=ROOT, env=environment, check=True)
    previous, current = passed_cases(Path(prior["report"]["path"])), passed_cases(REPORT)
    if not previous <= current:
        raise ValueError("shared CPU coverage lost previous case identities")
    if any(sha256_file(ROOT / name) != expected for name, expected in sources.items()):
        raise ValueError("qualified CPU sources changed while executing")
    constructors = {}
    for label in ("full", "thunderagent", "thunderagent_acting_decay"):
        settings, _, _ = serving_environment(
            {"arms": {"full": configuration["common_environment"]}},
            label, ROOT, Path(environment["SGLANG_SOURCE_CHECKOUT"]),
            ROOT / "experiments/raw/cpu_fitting_lending_unallocated" / label,
            18657, ROOT / "experiments/raw/cpu_fitting_lending_unallocated/plugin",
            sampling_mode="position", max_running_requests=24,
        )
        constructors[label] = preflight_runtime(
            {**settings, "SGLANG_SOURCE_CHECKOUT": environment["SGLANG_SOURCE_CHECKOUT"]},
            configuration["common_environment"]["MODEL_PATH"],
        )
        expected = "fit" if label == "full" else "largest"
        if constructors[label]["verified_configuration"]["agent_lending_selection"] != expected:
            raise ValueError("fitting selection leaked across the declared policy boundary")
    policy = ThunderAgentMigration(10000, retain_blocked=True, lending_selection="fit")
    donors = [MigratingProgram(str(index), 100 + index * 101) for index in range(8)]
    samples = []
    for _ in range(1000):
        begin = time.thread_time_ns()
        selected = policy._fitting_lenders(donors, 2100)
        samples.append((time.thread_time_ns() - begin) / 1000)
        if sum(item.tokens + policy.buffer_tokens for item in selected) < 2100:
            raise ValueError("selector benchmark did not cover its logical deficit")
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "source_bound_shared_cpu_fitting_logical_lending",
        "command": command,
        "environment": {key: environment[key] for key in prior["environment"]},
        "report": {"path": str(REPORT), "sha256": sha256_file(REPORT)},
        "source_manifest": {"path": str(MANIFEST), "sha256": sha256_file(MANIFEST)},
        "previous_report": prior["report"],
        "preserved_development_report": str(REPORTS / "v376_fitting_lending_shared_cpu_20261027.xml"),
        "development_preflight_failure": "Qualification helper omitted the production SERVER_FIELDS environment conversion; runtime policy unchanged.",
        "passing_cases_including_subtests": len(current),
        "retained_previous_cases_including_subtests": len(previous),
        "new_cases": len(current - previous),
        "configuration": {"path": str(CONFIG), "sha256": sha256_file(CONFIG)},
        "constructors": constructors,
        "selector_thread_cpu_us": {
            "samples": len(samples), "median": statistics.median(samples),
            "p99": sorted(samples)[989], "maximum": max(samples),
            "scope": "Eight-donor selection only; excludes proof, logging, full controller and GPU interference.",
        },
        "native_cpu_copy_event_and_host_index_standins": True,
        "gpu_execution_registered": False,
        "gpu_correctness_verified": False,
        "performance_accepted": False,
    }
    write(OUTPUT, result)
    print(json.dumps({
        key: result[key] for key in (
            "passing_cases_including_subtests", "new_cases", "selector_thread_cpu_us",
        )
    }, indent=2))


if __name__ == "__main__":
    main()
