#!/usr/bin/env python3
"""Bind an isolated wait-capacity repair candidate to exact CPU checks."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from beliefkv.experiments.source_provenance import git_source_state
from scripts.qualify_combined_cpu_candidate import bind, check_ast, checks, git, load, sha


CHANGED = {
    "beliefkv/runtime/sglang_v0520_runtime.py",
    "beliefkv/runtime/sglang_v0520_physical.py",
    "tests/test_sglang_v0520_runtime.py",
    "tests/test_sglang_v0520_physical.py",
    "tests/test_wait_prefetch_partial_extent.py",
    "scripts/benchmark_prepare_rejection_registration.py",
}


def qualify(
    candidate: Path, inherited_path: Path, check_path: Path,
    recheck_path: Path, cpu_path: Path,
) -> dict:
    candidate = candidate.resolve()
    inherited = load(inherited_path)
    baseline = inherited["candidate_revision"]
    revision = git(candidate, "rev-parse", "HEAD")
    if git(candidate, "status", "--porcelain"):
        raise ValueError("candidate must be clean")
    if (
        git(candidate, "rev-parse", "HEAD^^^") != baseline
        or set(git(candidate, "diff", "--name-only", baseline, revision).splitlines()) != CHANGED
        or git(candidate, "diff", "--name-only", "HEAD^", "HEAD")
        != "tests/test_wait_prefetch_partial_extent.py"
    ):
        raise ValueError("candidate must contain only two repairs and the test-identity commit")
    production = {
        "beliefkv/runtime/sglang_v0520_runtime.py": check_ast(
            candidate, baseline, "beliefkv/runtime/sglang_v0520_runtime.py",
            class_name="NativeAdmissionRuntime",
            methods=("_wait_prefetch_opportunity", "capture_shadow_candidate"),
        ),
        "beliefkv/runtime/sglang_v0520_physical.py": check_ast(
            candidate, baseline, "beliefkv/runtime/sglang_v0520_physical.py",
            functions=("missing_prepare_full_prefix_tokens",),
        ),
    }
    sources = dict(inherited["source_sha256"])
    for relative, expected in sources.items():
        if relative not in CHANGED and sha(candidate / relative) != expected:
            raise ValueError(f"inherited source changed: {relative}")
    initial, initial_totals = checks(check_path)
    rechecked, recheck_totals = checks(recheck_path)
    changed_module = "tests.test_wait_prefetch_partial_extent"
    initial_module = {key for key in initial if key[0] == changed_module}
    if (
        {key[0] for key in rechecked} != {changed_module}
        or len(rechecked) != len(initial_module)
        or any(status != "passed" for status in initial.values())
        or any(status != "passed" for status in rechecked.values())
        or any(
            totals[field] for totals in (initial_totals, recheck_totals)
            for field in ("failures", "errors", "skipped")
        )
    ):
        raise ValueError("full initial checks or changed-module recheck did not pass")
    current = {key: status for key, status in initial.items() if key not in initial_module}
    current.update(rechecked)
    total_checks = initial_totals["tests"] - len(initial_module) + recheck_totals["tests"]
    prior, _ = checks(Path(inherited["exact_candidate_checks"]["initial"]["path"]))
    modules = {
        ".".join(classname.split(".")[:2]).replace(".", "/") + ".py"
        for classname, _ in current
    }
    expected_modules = {path for path in sources if path.startswith("tests/")}
    if (
        not prior.keys() <= current.keys() or modules != expected_modules
        or any(status != "passed" for status in current.values())
    ):
        raise ValueError("candidate test coverage incomplete or checks did not pass")
    cpu = load(cpu_path)
    for relative, expected in cpu["source_sha256"].items():
        if sha(candidate / relative) != expected:
            raise ValueError(f"CPU evidence source changed: {relative}")
    for case in cpu["cases"]:
        if case.get("cold_candidates_and_actions_equal") is not True:
            raise ValueError("missing CPU control equivalence")
        if any(arm["registered_nodes"] for arm in case["measurements"].values()):
            raise ValueError("unbacked leaf control grew the candidate registry")
    frozen = inherited["preserved_v60"]
    if (
        git(Path(frozen["checkout"]), "rev-parse", "HEAD") != frozen["revision"]
        or git(Path(frozen["checkout"]), "status", "--porcelain")
        or sha(Path(frozen["registration"]["path"])) != frozen["registration"]["sha256"]
    ):
        raise ValueError("registered V60 source or reservation changed")
    base_checkout = Path(inherited["candidate_checkout"])
    if (
        git(base_checkout, "rev-parse", "HEAD") != baseline
        or git(base_checkout, "status", "--porcelain")
    ):
        raise ValueError("inherited candidate changed")
    engine_path = (candidate / "third_party/sglang-v0.5.20").resolve()
    if str(engine_path) != inherited["candidate_engine"]:
        raise ValueError("candidate engine reference changed")
    engine = git_source_state(
        engine_path,
        canonical_patch=candidate / "patches/sglang-v0.5.20-beliefkv-staging.patch",
    )["canonical_patch"]
    for field in (
        "sha256", "expected_tree", "actual_tree", "matches_worktree", "covered_untracked_paths",
    ):
        if engine[field] != inherited["engine"][field]:
            raise ValueError(f"inherited engine changed: {field}")
    sources.update({relative: None for relative in CHANGED})
    sources = {relative: sha(candidate / relative) for relative in sorted(sources)}
    if git(candidate, "rev-parse", "HEAD") != revision or git(candidate, "status", "--porcelain"):
        raise ValueError("candidate changed during qualification")
    return {
        "status": "qualified_for_cold_observation_not_deployed_or_gpu_accepted",
        "observed_at_local": datetime.now().astimezone().isoformat(),
        "candidate_checkout": str(candidate), "candidate_revision": revision,
        "inherited_revision": baseline, "inherited_qualification": bind(inherited_path),
        "source_sha256": sources, "production_delta": production,
        "exact_candidate_checks": {
            "passed_unique_tests": len(current),
            "passed_subtests": total_checks - len(current),
            "inherited_test_identities_covered": len(prior),
            "remaining_failures": 0, "remaining_errors": 0, "remaining_skips": 0,
            "initial_revision": git(candidate, "rev-parse", "HEAD^"),
            "initial": {**bind(check_path), **initial_totals},
            "changed_module_recheck": {**bind(recheck_path), **recheck_totals},
            "replaced_module": changed_module,
            "setup": "CPU-only; used the existing cached Docker image selected for V678.",
        },
        "cpu_control": {
            **bind(cpu_path),
            "added_thread_cpu_ms_per_iteration": [
                case["thread_cpu_mean_delta_ms_per_iteration"] for case in cpu["cases"]
            ],
            "scope": "Exact-source unchanged-action controls; added cost, not a speedup.",
        },
        "engine": engine, "candidate_engine": str(engine_path),
        "preserved_v60": frozen, "qualification_helper": bind(Path(__file__)),
        "limits": [
            "Two wait-capacity repairs and one test-identity-only commit extend 9c79eee; harness, models, budgets and engine remain unchanged.",
            "V59 recorded no native zero-byte budget; that repair is not an explanation of V59 performance.",
            "Host budget rejection can retain a safe existing candidate; historical candidate availability remains unproven.",
            "Earlier component CPU evidence remains historical; do not add percentages or call it exact performance of this candidate.",
            "No deployment, reservation override, GPU, task-quality, fairness, recomputation or JCT/throughput acceptance is claimed.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--inherited", type=Path, required=True)
    parser.add_argument("--checks", type=Path, required=True)
    parser.add_argument("--recheck", type=Path, required=True)
    parser.add_argument("--cpu-control", type=Path, required=True)
    parser.add_argument("--output", type=Path, action="append", required=True)
    args = parser.parse_args()
    if any(path.exists() for path in args.output):
        raise FileExistsError("preserve existing receipts")
    report = qualify(
        args.candidate, args.inherited, args.checks, args.recheck, args.cpu_control,
    )
    for path in args.output:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write("\n")
    print(json.dumps({
        "status": report["status"], "candidate_revision": report["candidate_revision"],
        "source_count": len(report["source_sha256"]),
        "checks": report["exact_candidate_checks"],
    }, indent=2))


if __name__ == "__main__":
    main()
