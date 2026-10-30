"""Bind a composed original trace pool without claiming a new native capture."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.composite_workload import (
    KIND, load_composed_source, verify_composed_registration,
)
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from experiments.reports.workflow_work_training_20261029.finish_training import (
    audit_finished_source, source_ready,
)
from scripts import run_causal_source_capture as capture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-arm", type=Path, required=True)
    parser.add_argument("--origin-registration", type=Path, action="append", required=True)
    parser.add_argument("--cpu-report", type=Path, required=True)
    parser.add_argument("--cpu-sources", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or git_source_state(ROOT)["dirty"]:
        raise ValueError("register from clean committed sources and preserve existing bindings")
    count = capture.comparison.qualify_report(args.cpu_report, args.cpu_sources)
    sources = capture.load(args.cpu_sources)
    required = {
        "beliefkv/experiments/composite_workload.py", "scripts/prepare_composite_workload.py",
        "scripts/register_composite_source.py", "tests/test_composite_workload.py",
    }
    if not required <= sources.keys():
        raise ValueError("composed source operators lack current CPU binding")
    arm = args.source_arm.resolve()
    graph, _, _ = load_composed_source(arm)
    declaration = capture.load(arm / "composite_source.json")
    expected_origins = {Path(row["source_capture"]).resolve() for row in declaration["sources"]}
    origins, inputs, completions = {}, set(), {}
    for path in args.origin_registration:
        plan = capture.load(path)
        state_path = path.with_suffix(".state.json")
        state = capture.load(state_path)
        if not source_ready(plan, state):
            raise ValueError("all original captures must finish with actual owned exit")
        capture.comparison.verify_sources(plan)
        origins[(Path(plan["run_root"]) / "native_capture").resolve()] = plan
        completion = audit_finished_source(plan, state)
        completions[str(path.resolve())] = completion
        inputs.update((path, state_path, *(Path(name) for name in completion)))
    if origins.keys() != expected_origins:
        raise ValueError("bind exactly the original sources used in the composed population")
    first = next(iter(origins.values()))
    if len({row["engine_tree"] for row in origins.values()}) != 1:
        raise ValueError("original sources used different native engines")
    environment = deepcopy(first["environment"])
    environment.update(
        ROOT_COUNT=str(graph.summary()["workflows"]),
        WORKLOAD_MANIFEST=str(arm / "workload_manifest.json"),
    )
    environment.pop("CLIENT_ROOT_CONCURRENCY", None)
    inputs.update(arm / name for name in declaration["artifacts_sha256"])
    inputs.update((arm / "composite_source.json", args.cpu_report, args.cpu_sources))
    inputs.update(Path(name) for name in declaration["original_inputs_sha256"])
    result = {
        "schema_version": 1, "source_kind": KIND, "source_profile": "nested_oneshot",
        "purpose": "independently_compiled_whole_original_trace_composition",
        "code_commit": git_source_state(ROOT)["commit"],
        "execution_checkout": str(ROOT), "source_sha256": sources, "passing_cpu_cases": count,
        "external_inputs": [
            {"path": str(path.resolve()), "sha256": sha256_file(path)}
            for path in sorted(inputs)
        ],
        "source_arm": str(arm), "composition_sha256": sha256_file(arm / "composite_source.json"),
        "original_completion_proofs": completions,
        "engine_checkout": first["engine_checkout"], "engine_tree": first["engine_tree"],
        "engine_evidence": first["engine_evidence"], "hold_file": first["hold_file"],
        "environment": environment,
        "environment_scope": "common_replay_resources_not_one_natural_capture_protocol",
        "matched_natural_native_baseline_available": False,
        "capture_capacity": capture.load(arm / "server/native_capacity_census.json")["capacity"],
        "GPU_experiment_started": False, "performance_accepted": False,
    }
    verify_composed_registration(result)
    capture.comparison.verify_sources(result)
    capture.reservation.write_json(args.output, result)
    print(json.dumps({
        "source_kind": KIND, "projects": graph.summary()["workflows"],
        "registration": str(args.output), "GPU_action": False,
    }))


if __name__ == "__main__":
    main()
