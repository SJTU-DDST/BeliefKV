#!/usr/bin/env python3
"""Bind the normal confirmed-JOIN repair to an isolated CPU-checked candidate."""

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

RUNTIME = "beliefkv/runtime/sglang_v0520_runtime.py"
NEW_TEST = "tests/test_join_confirmed_reentry.py"


def qualify(candidate: Path, inherited_path: Path, check_path: Path, cpu_path: Path) -> dict:
    candidate = candidate.resolve()
    inherited = load(inherited_path)
    baseline = inherited["candidate_revision"]
    revision = git(candidate, "rev-parse", "HEAD")
    if (
        git(candidate, "status", "--porcelain")
        or git(candidate, "rev-parse", "HEAD^") != baseline
        or set(git(candidate, "diff", "--name-only", baseline, revision).splitlines())
        != {RUNTIME, NEW_TEST}
    ):
        raise ValueError("candidate must cleanly extend its parent with only this repair")
    production = check_ast(
        candidate, baseline, RUNTIME, class_name="NativeAdmissionRuntime",
        methods=(
            "__init__", "close", "_discard_join_ticket", "on_events",
            "_advance_join_ticket", "_live_join_ticket", "_select_confirmed_join_ticket",
            "_yield_confirmed_join_ticket", "dispatch_join_prefetch", "_record_prefetch_issued",
        ),
    )
    sources = dict(inherited["source_sha256"])
    for relative, expected in sources.items():
        if relative != RUNTIME and sha(candidate / relative) != expected:
            raise ValueError(f"unrelated inherited source changed: {relative}")
    source_parent = Path(inherited["candidate_checkout"])
    if (
        git(source_parent, "rev-parse", "HEAD") != baseline
        or git(source_parent, "status", "--porcelain")
    ):
        raise ValueError("inherited candidate changed")
    prior, _ = checks(Path(inherited["exact_candidate_checks"]["initial"]["path"]))
    recheck, _ = checks(Path(
        inherited["exact_candidate_checks"]["changed_module_recheck"]["path"]
    ))
    replaced = inherited["exact_candidate_checks"]["replaced_module"]
    prior = {key: status for key, status in prior.items() if key[0] != replaced}
    prior.update(recheck)
    current, totals = checks(check_path)
    if (
        not prior.keys() <= current.keys()
        or any(status != "passed" for status in current.values())
        or any(totals[key] for key in ("failures", "errors", "skipped"))
    ):
        raise ValueError("candidate checks did not cover the complete inherited suite")
    sources[NEW_TEST] = sha(candidate / NEW_TEST)
    modules = {
        ".".join(classname.split(".")[:2]).replace(".", "/") + ".py"
        for classname, _ in current
    }
    if modules != {path for path in sources if path.startswith("tests/")}:
        raise ValueError("candidate check modules differ from the source manifest")
    sources[RUNTIME] = sha(candidate / RUNTIME)
    cpu = load(cpu_path)
    for relative, expected in cpu["candidate_sources"].items():
        location = candidate / relative if relative.startswith("beliefkv/") else ROOT / relative
        if sha(location) != expected:
            raise ValueError(f"CPU source changed: {relative}")
    for relative, expected in cpu["baseline_sources"].items():
        if expected != inherited["source_sha256"].get(relative):
            raise ValueError(f"CPU baseline differs from inherited source: {relative}")
    if len(cpu["cases"]) != 4 or any(
        case.get("same_counters_and_no_actions") is not True for case in cpu["cases"]
    ):
        raise ValueError("missing enabled/disabled and timed/untimed CPU controls")
    engine_path = (candidate / "third_party/sglang-v0.5.20").resolve()
    engine = git_source_state(
        engine_path,
        canonical_patch=candidate / "patches/sglang-v0.5.20-beliefkv-staging.patch",
    )["canonical_patch"]
    if str(engine_path) != inherited["candidate_engine"] or any(
        engine[field] != inherited["engine"][field] for field in (
            "sha256", "expected_tree", "actual_tree", "matches_worktree",
            "covered_untracked_paths",
        )
    ):
        raise ValueError("inherited native engine changed")
    frozen = inherited["preserved_v60"]
    if (
        git(Path(frozen["checkout"]), "rev-parse", "HEAD") != frozen["revision"]
        or git(Path(frozen["checkout"]), "status", "--porcelain")
        or sha(Path(frozen["registration"]["path"])) != frozen["registration"]["sha256"]
    ):
        raise ValueError("registered V60 or reservation changed")
    if git(candidate, "status", "--porcelain") or git(candidate, "rev-parse", "HEAD") != revision:
        raise ValueError("candidate changed during qualification")
    return {
        "status": "qualified_for_cold_observation_not_deployed_or_gpu_accepted",
        "observed_at_local": datetime.now().astimezone().isoformat(),
        "candidate_checkout": str(candidate), "candidate_revision": revision,
        "inherited_revision": baseline, "inherited_qualification": bind(inherited_path),
        "source_sha256": sources, "production_delta": {RUNTIME: production},
        "exact_candidate_checks": {
            "passed_unique_tests": len(current), "passed_subtests": totals["tests"] - len(current),
            "inherited_test_identities_covered": len(prior),
            "remaining_failures": 0, "remaining_errors": 0, "remaining_skips": 0,
            "checks": {**bind(check_path), **totals},
            "setup": "CPU-only; existing cached Docker image, no image pull.",
        },
        "idle_dispatch_cpu": {
            **bind(cpu_path),
            "added_mean_thread_cpu_us": [
                case["added_mean_thread_cpu_us"] for case in cpu["cases"]
            ],
            "scope": "No live JOIN/final-stage candidate; added dispatch cost, not GPU benefit.",
        },
        "engine": engine, "candidate_engine": str(engine_path),
        "preserved_v60": frozen, "qualification_helper": bind(Path(__file__)),
        "limits": [
            "One runtime repair and its tests extend the previous qualified candidate; workload, harness, heads, budgets and native engine are unchanged.",
            "Confirmed JOIN can start or continue restoration without a child-time forecast; physical capacity and safe-input checks remain binding.",
            "Forecast/EOS-based earlier restoration remains available; this does not yet compare alternative trigger policies.",
            "Completion-to-parent-arrival recovery is separate from pre-completion predictive H2D; actual submit/reuse timestamps determine attribution.",
            "No deployment, reservation override, GPU-turnover, quality, fairness, recomputation or JCT/throughput acceptance.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--inherited", type=Path, required=True)
    parser.add_argument("--checks", type=Path, required=True)
    parser.add_argument("--cpu-control", type=Path, required=True)
    parser.add_argument("--output", type=Path, action="append", required=True)
    args = parser.parse_args()
    if any(path.exists() for path in args.output):
        raise FileExistsError("preserve existing qualification receipts")
    report = qualify(args.candidate, args.inherited, args.checks, args.cpu_control)
    for path in args.output:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write("\n")
    print(json.dumps({
        "status": report["status"], "candidate_revision": report["candidate_revision"],
        "source_count": len(report["source_sha256"]), "checks": report["exact_candidate_checks"],
    }, indent=2))


if __name__ == "__main__":
    main()
