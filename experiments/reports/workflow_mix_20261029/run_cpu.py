"""Bind mixed-workload preparation to the retained complete shared CPU suite."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from experiments.reports.layer_free_extent_20261029.run_bound_cpu import identities, write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--previous-evidence", type=Path,
        default=ROOT / "experiments/reports/workflow_mix_20261029/bound_cpu_r4/evidence.json",
    )
    parser.add_argument("--bind-input", type=Path, action="append", default=[])
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    previous_path = args.previous_evidence
    previous = json.loads(previous_path.read_bytes())
    retained, bad, _ = identities(Path(previous["report"]["path"]))
    if bad or any(
        sha256_file(Path(previous[key]["path"])) != previous[key]["sha256"]
        for key in ("report", "source_manifest")
    ):
        raise ValueError("prior complete shared CPU population changed")
    names = set(json.loads(Path(previous["source_manifest"]["path"]).read_bytes()))
    added = (
        "tests/test_workload_mix.py", "tests/test_workflow_rank_input.py",
        "tests/test_permuted_replay_arrivals.py",
        "tests/test_workflow_oracle_path_cache.py",
        "tests/test_workflow_competition_readiness.py", "tests/test_workflow_competition_audit.py",
        "tests/test_workload_mix_completion.py",
        "tests/test_parallel_workstream_pilot.py", "tests/test_nested_delegation.py",
        "tests/test_checkpoint_workstream_pilot.py", "tests/test_workload_requirements.py",
        "tests/test_workflow_combination.py", "tests/test_workflow_combination_runtime.py",
        "tests/test_sustained_workload.py",
        "tests/test_checkpoint_subagents.py", "tests/test_structured_checkpoint_pilot.py",
        "tests/test_deepagents_swebench.py", "tests/test_deepagents_adapter.py",
        "tests/test_subagent_state.py",
        "tests/test_checkpoint_native_parser.py", "tests/test_parallel_checkpoint_pilot.py",
        "tests/test_checkpoint_repository_tools.py", "tests/test_focused_review_pilot.py",
        "tests/test_original_task_mix.py",
        "tests/test_original_project_supplement.py", "tests/test_reused_workflow_mix.py",
        "tests/test_composite_workload.py", "tests/test_workflow_cohort.py",
        "tests/test_composite_comparison.py",
    )
    names.update(added)
    names.update((
        "beliefkv/experiments/workload_mix.py", "beliefkv/experiments/replay_arrivals.py",
        "scripts/prepare_workflow_mix.py", "scripts/prepare_workflow_mix_candidates.py",
        "beliefkv/predictor/workflow_rank_input.py",
        "configs/workloads/locobench_mixed_scope18_20261029.json",
        "configs/migration/workflow_mix_scope18_native_20261029.json",
        "experiments/reports/workflow_mix_20261029/scope18_declaration.json",
        "experiments/reports/workflow_mix_20261029/finish_capture.py",
        "experiments/reports/workflow_mix_20261029/register_capture.py",
        "experiments/reports/workflow_mix_20261029/live_start.py",
        "experiments/reports/workflow_mix_20261029/qualify_oracle_cache.py",
        "scripts/audit_workflow_competition.py",
        "experiments/reports/workflow_mix_20261029/review_burst.py",
        "experiments/reports/workflow_mix_20261029/prepare_burst_oracle.py",
        "experiments/reports/workflow_mix_20261029/qualify_burst_oracle.py",
        "experiments/reports/workflow_mix_20261029/register_burst_oracle.py",
        "experiments/reports/workflow_mix_20261029/live_burst.py",
        "scripts/prepare_parallel_workstream_pilot.py",
        "scripts/audit_parallel_workstreams.py",
        "configs/workloads/locobench_parallel_pilot6_20261030.json",
        "configs/migration/workflow_parallel_pilot6_native_20261030.json",
        "experiments/reports/workflow_mix_20261029/parallel_pilot6_declaration.json",
        "experiments/reports/workflow_mix_20261029/finish_parallel_pilot.py",
        "experiments/reports/workflow_mix_20261029/register_parallel_pilot.py",
        "experiments/reports/workflow_mix_20261029/prepare_main_arrivals.py",
        "beliefkv/experiments/workload_requirements.py",
        "beliefkv/policy/workflow_combination.py",
        "beliefkv/runtime/workflow_combination.py",
        "scripts/audit_workload_requirements.py",
        "scripts/prepare_checkpoint_workstream_pilot.py",
        "scripts/prepare_sustained_workload.py",
        "configs/workloads/locobench_checkpoint_pilot6_20261030.json",
        "configs/migration/workflow_checkpoint_pilot6_native_20261030.json",
        "experiments/reports/checkpoint_pilot6_20261030/declaration.json",
        "beliefkv/runtime/checkpoint_subagents.py",
        "scripts/prepare_structured_checkpoint_pilot.py",
        "scripts/audit_checkpoint_prompt_delivery.py",
        "configs/workloads/locobench_structured_checkpoint_pilot6_20261030.json",
        "configs/migration/workflow_structured_checkpoint_pilot6_native_20261030.json",
        "experiments/reports/structured_checkpoint_pilot6_20261030/declaration.json",
        "scripts/prepare_parallel_checkpoint_pilot.py",
        "configs/workloads/locobench_parallel_checkpoint_pilot6_20261030.json",
        "configs/migration/workflow_parallel_checkpoint_pilot6_native_20261030.json",
        "experiments/reports/parallel_checkpoint_pilot6_20261030/declaration.json",
        "beliefkv/runtime/checkpoint_repository_tools.py",
        "scripts/prepare_focused_review_pilot.py",
        "configs/workloads/locobench_focused_review_pilot6_20261030.json",
        "configs/migration/workflow_focused_review_pilot6_native_20261030.json",
        "experiments/reports/focused_review_pilot6_20261030/declaration.json",
        "scripts/prepare_original_task_mix.py",
        "configs/workloads/locobench_original_task_mix6_20261030.json",
        "configs/migration/workflow_original_task_mix6_native_20261030.json",
        "experiments/reports/original_task_mix6_20261030/declaration.json",
        "experiments/reports/original_task_mix6_20261030/qualified_manifest.json",
        "experiments/reports/original_task_mix6_20261030/native_qualified.json",
        "experiments/reports/original_task_mix6_20261030/tool_binding.json",
        "configs/workloads/locobench_original_task_mix6_coder_20261030.json",
        "configs/migration/workflow_original_task_mix6_coder_native_20261030.json",
        "experiments/reports/original_task_mix6_20261030/preparation_r2/declaration.json",
        "experiments/reports/original_task_mix6_20261030/preparation_r2/qualified_manifest.json",
        "experiments/reports/original_task_mix6_20261030/preparation_r2/native_qualified.json",
        "experiments/reports/original_task_mix6_20261030/preparation_r2/tool_binding.json",
        "scripts/prepare_reused_workflow_mix.py",
        "scripts/prepare_original_project_supplement.py",
        "configs/workloads/locobench_original_supplement26_coder_20261030.json",
        "configs/migration/workflow_original_supplement26_native_20261030.json",
        "experiments/reports/original_project_supplement_20261030/preparation_r1/declaration.json",
        "experiments/reports/original_project_supplement_20261030/preparation_r1/qualified_manifest.json",
        "experiments/reports/original_project_supplement_20261030/preparation_r1/native_qualified.json",
        "experiments/reports/original_project_supplement_20261030/preparation_r1/tool_binding.json",
        "beliefkv/experiments/composite_workload.py", "beliefkv/experiments/workflow_cohort.py",
        "scripts/prepare_composite_workload.py", "scripts/register_composite_source.py",
        "scripts/audit_workflow_cohort.py",
        "scripts/prepare_composite_comparison.py",
        str(Path(__file__).relative_to(ROOT)),
    ))
    names.update(str(path.resolve().relative_to(ROOT)) for path in args.bind_input)
    before = {name: sha256_file(ROOT / name) for name in sorted(names)}
    engine = Path(previous["environment"]["SGLANG_SOURCE_CHECKOUT"])
    native = git_source_state(engine)
    report, sources = args.output_dir / "bound_cpu.xml", args.output_dir / "sources.json"
    command = [value for value in previous["command"] if not value.startswith("--junitxml=")]
    command[0] = sys.executable
    command.extend(name for name in added if name not in command)
    command.append("--junitxml=" + str(report.resolve()))
    environment = {
        **os.environ, **previous["environment"], "CUDA_VISIBLE_DEVICES": "99999",
        "PYTHONHASHSEED": "21", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
    }
    result = subprocess.run(command, cwd=ROOT, env=environment)
    actual, bad, records = identities(report)
    unchanged = before == {name: sha256_file(ROOT / name) for name in before} and native == git_source_state(engine)
    write(sources, before)
    write(args.output_dir / "evidence.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(), "command": command,
        "environment": previous["environment"], "returncode": result.returncode,
        "unique_test_identities": len(actual), "testcase_records": records,
        "retained_prior_union": len(retained), "all_retained_literal_identities_present": retained <= actual,
        "missing_identities": sorted(retained - actual), "failed_error_skipped_cases": bad,
        "sources_unchanged_during_execution": unchanged, "native_source": native,
        "source_manifest": {"path": str(sources.resolve()), "sha256": sha256_file(sources)},
        "report": {"path": str(report.resolve()), "sha256": sha256_file(report)},
        "prior_evidence_sha256": {str(previous_path): sha256_file(previous_path)},
        "GPU_experiment_started": False, "performance_accepted": False,
    })
    if result.returncode or bad or not retained <= actual or not unchanged:
        raise RuntimeError("preserve failed mixed-workload CPU evidence")
    print({"passing_identities": len(actual), "retained": len(retained), "sources": len(before)})


if __name__ == "__main__":
    main()
