#!/usr/bin/env python3
"""Run the retained shared CPU cases and bind the adaptive tool candidate."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.qualify_predicted_tool_offload_cpu import cases
from scripts.run_causal_comparison import (
    environment, preflight_runtime, validate_timing_prior_contract,
)
from beliefkv.experiments.tool_timing_overlay import load_overlay
from scripts.run_causal_gpu_replay import source_plan


def source_manifest():
    paths = [
        path for directory in ("beliefkv", "scripts", "tests", "configs")
        for path in (ROOT / directory).rglob("*")
        if path.is_file() and path.suffix in (".py", ".sh", ".json")
    ]
    return {str(path.relative_to(ROOT)): sha256_file(path) for path in sorted(paths)}


def qualify(args):
    sources_path = args.output.with_suffix(".sources.json")
    if any(path.exists() for path in (args.report, args.output, sources_path)):
        raise FileExistsError("preserve prior CPU evidence")
    previous, _ = cases(args.previous_report)
    paths = {
        classname.split(".")[0] + "/" + classname.split(".")[1] + ".py"
        for classname, _ in previous
    } | {
        "tests/test_tool_policy_adaptation.py",
        "tests/test_online_workload_timing.py",
        "tests/test_idle_full_prepare_pipeline.py",
        "tests/test_sglang_v0520_runtime.py",
        "tests/test_demand_agent_lending.py",
        "tests/test_projected_pressure_release.py",
    }
    if not all((ROOT / path).is_file() for path in paths):
        raise ValueError("previous CPU modules cannot be located")
    sources = source_manifest()
    env = {
        **os.environ, "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21",
        "SGLANG_SOURCE_CHECKOUT": str(args.engine.resolve()),
        "BELIEFKV_TEST_SANDBOX_DOCKER": "1",
        "BELIEFKV_TEST_SANDBOX_IMAGE": "sha256:a0d821c46d8f12f52660a084241f9ad5e75a1c058e3c30e77808757ef35ae8ab",
    }
    command = [
        sys.executable, "-m", "pytest", "-q", *sorted(paths),
        "--tb=short", "--junitxml=" + str(args.report),
    ]
    subprocess.run(command, cwd=ROOT, env=env, check=True)
    current, reported = cases(args.report)
    if not previous <= current or source_manifest() != sources:
        raise ValueError("CPU qualification omitted earlier cases or sources changed during testing")
    config = json.loads(args.config.read_bytes())
    common = config["common_environment"]
    plan, _, _ = source_plan(Path(
        "experiments/raw/locobench_parallel_retry_source_20261026_v230/native_capture",
    ))
    _, timing = load_overlay(plan, ROOT / config["tool_timing_overlay"]["path"])
    validate_timing_prior_contract(common, timing)
    constructors = {}
    for arm in ("full", "thunderagent", "thunderagent_acting_decay"):
        settings, _, _ = environment(
            {"arms": {"full": common}}, arm, ROOT, args.engine,
            args.output.parent / "cpu-no-server", 18654, ROOT,
            max_running_requests=24, sampling_mode="position",
        )
        constructors[arm] = preflight_runtime(settings, common["MODEL_PATH"])
        verified = constructors[arm]["verified_configuration"]
        if (
            verified["enable_predicted_tool_offload"] is not (arm == "full")
            or verified["tool_offload_policy"] != ("demand_cost" if arm == "full" else "off")
            or verified["tool_timing_prior_sha256"] != (
                common["BELIEFKV_TOOL_TIMING_PRIOR_SHA256"] if arm == "full" else None
            )
            or verified["agent_blocked_budget_proof"] != (
                common["AGENT_BLOCKED_BUDGET_PROOF"] if arm == "full" else "predicted_pressure"
            )
            or verified["agent_pressure_pause_policy"] != (
                common.get("AGENT_PRESSURE_PAUSE_POLICY", "reference") if arm == "full" else "reference"
            )
        ):
            raise ValueError("adaptive tool policy leaked into a paper arm or failed to load")
    if source_manifest() != sources:
        raise ValueError("candidate sources changed during constructor qualification")
    with sources_path.open("x") as stream:
        json.dump(sources, stream, indent=2)
        stream.write("\n")
    result = {
        "schema_version": 1, "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "source_bound_shared_cpu_adaptive_tool_policy",
        "command": command,
        "environment": {name: env[name] for name in (
            "CUDA_VISIBLE_DEVICES", "PYTHONHASHSEED", "SGLANG_SOURCE_CHECKOUT",
            "BELIEFKV_TEST_SANDBOX_DOCKER", "BELIEFKV_TEST_SANDBOX_IMAGE",
        )},
        "passing_cases": len(current), "junit_tests_including_subtests": reported,
        "retained_previous_cases": len(previous), "new_cases": len(current - previous),
        "previous_report": {
            "path": str(args.previous_report.resolve()),
            "sha256": sha256_file(args.previous_report),
        },
        "report": {"path": str(args.report.resolve()), "sha256": sha256_file(args.report)},
        "source_manifest": {"path": str(sources_path.resolve()), "sha256": sha256_file(sources_path)},
        "configuration": {"path": str(args.config.resolve()), "sha256": sha256_file(args.config)},
        "timing_overlay": timing, "constructors": constructors,
        "native_cpu_copy_event_and_host_index_standins": True,
        "gpu_execution_registered": False, "gpu_correctness_verified": False,
        "performance_accepted": False,
        "preserved_development_reports": [
            "experiments/reports/v310_adaptive_tool_policy_initial_cpu_20261027.xml",
            "experiments/reports/v312_adaptive_tool_policy_targeted_cpu_20261027.xml",
            "experiments/reports/v313_adaptive_tool_policy_targeted_cpu_20261027.xml",
            "experiments/reports/v316_adaptive_tool_policy_shared_cpu_20261027.xml",
            "experiments/reports/v319_adaptive_tool_policy_shared_cpu_20261027.xml",
        ],
        "preexisting_core_fixture_repair": {
            "frozen_reproduction": "d6a4ce4c7bc11d6fc64b20edf45bb655907141b1",
            "test": "tests/test_sglang_v0520_runtime.py::test_native_shadow_burst_reserves_each_node_and_credits_only_its_ack",
            "reproduced_failures": 2,
            "repair": "Complete the JOIN before starting the independent tool wait; retain runtime blocking guards.",
        },
    }
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    return {name: result[name] for name in (
        "scope", "passing_cases", "retained_previous_cases", "new_cases",
        "gpu_correctness_verified", "performance_accepted",
    )}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--previous-report", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/migration/predictive_adaptive_tool_offload_20261027.json")
    print(json.dumps(qualify(parser.parse_args()), indent=2))


if __name__ == "__main__":
    main()
