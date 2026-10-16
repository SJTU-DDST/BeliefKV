#!/usr/bin/env python3
"""Bind the waiting-dispatch and reclamation combination for future cold runs."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys
import json


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state
from scripts.qualify_combined_cpu_candidate import (
    DOCKER_TEST, bind, check_ast, checks, git, load, sha,
)


RUNTIME = "beliefkv/runtime/sglang_v0520_runtime.py"
PATCH = "patches/sglang-v0.5.20-beliefkv-staging.patch"
CHANGED = {RUNTIME, PATCH, "tests/test_sglang_v0520_runtime.py"}
ENGINE_SOURCE = "python/sglang/srt/mem_cache/unified_radix_cache.py"
ENGINE_CHANGED = {
    ENGINE_SOURCE,
    "test/srt/test_beliefkv_execution_handoff.py",
    "test/srt/test_beliefkv_terminal_capacity.py",
}


def checked_receipt(binding):
    path = Path(binding["path"])
    if sha(path) != binding["sha256"]:
        raise ValueError(f"bound receipt changed: {path}")
    return checks(path)


def qualify(args):
    candidate = args.candidate.resolve()
    inherited, reclaim, frozen = (
        load(args.inherited), load(args.reclaim_evidence), load(args.preserved_v61),
    )
    baseline = inherited["candidate_revision"]
    revision = git(candidate, "rev-parse", "HEAD")
    if (
        git(candidate, "status", "--porcelain")
        or git(candidate, "rev-parse", "HEAD^") != baseline
        or set(git(candidate, "diff", "--name-only", baseline, revision).splitlines())
        != CHANGED
    ):
        raise ValueError("candidate must cleanly extend only dispatch, tests and patch")
    parent = Path(inherited["candidate_checkout"])
    if git(parent, "rev-parse", "HEAD") != baseline or git(parent, "status", "--porcelain"):
        raise ValueError("registered parent changed")
    sources = dict(inherited["source_sha256"])
    for name, expected in sources.items():
        if name not in CHANGED and sha(candidate / name) != expected:
            raise ValueError(f"unrelated inherited source changed: {name}")
    for name in CHANGED:
        sources[name] = sha(candidate / name)
    runtime_delta = check_ast(
        candidate, baseline, RUNTIME, class_name="NativeAdmissionRuntime",
        methods=("dispatch_after_forward",),
    )

    engine_path = (candidate / "third_party/sglang-v0.5.20").resolve()
    engine = git_source_state(engine_path, canonical_patch=candidate / PATCH)
    original_engine = git_source_state(
        Path(inherited["candidate_engine"]), canonical_patch=parent / PATCH,
    )
    before, after = original_engine["canonical_patch"], engine["canonical_patch"]
    if (
        before["sha256"] != inherited["engine"]["sha256"]
        or before["actual_tree"] != inherited["engine"]["actual_tree"]
        or before["actual_tree"] != reclaim["engine"]["base_tree"]
        or after["actual_tree"] != reclaim["engine"]["overlay_tree"]
        or original_engine["commit"] != engine["commit"]
        or set(git(
            engine_path, "diff", "--name-only", before["actual_tree"], after["actual_tree"],
        ).splitlines()) != ENGINE_CHANGED
    ):
        raise ValueError("engine differs from the original tree plus known-victim overlay")
    for name, expected in reclaim["engine"]["source_sha256"].items():
        if sha(engine_path / name) != expected:
            raise ValueError(f"reclaim source changed: {name}")
    engine_delta = check_ast(
        engine_path, before["actual_tree"], ENGINE_SOURCE,
        class_name="UnifiedRadixCache", methods=("reclaim_beliefkv_handoff_capacity",),
    )

    prior, _ = checked_receipt(inherited["exact_candidate_checks"]["initial"])
    prior_supplement, _ = checked_receipt(
        inherited["exact_candidate_checks"]["skipped_identity_supplement"],
    )
    prior.update(prior_supplement)
    current, totals = checks(args.checks)
    supplement, supplemental_totals = checks(args.supplement)
    failed = {key for key, status in current.items() if status != "passed"}
    if (
        failed != {DOCKER_TEST} or current[DOCKER_TEST] != "failure"
        or set(supplement) != failed or supplement[DOCKER_TEST] != "passed"
        or any(supplemental_totals[name] for name in ("failures", "errors", "skipped"))
    ):
        raise ValueError("only the first image's Docker environment failure may be replaced")
    current.update(supplement)
    if (
        not prior.keys() <= current.keys()
        or any(status != "passed" for status in prior.values())
        or any(status != "passed" for status in current.values())
    ):
        raise ValueError("combined candidate did not pass every inherited identity")
    old_engine, _ = checked_receipt({
        "path": reclaim["validation"]["final_receipt"],
        "sha256": reclaim["validation"]["final_receipt_sha256"],
    })
    old_names = {name for _, name in old_engine}
    new_names = {name for classname, name in current if classname == ""}
    if len(old_names) != len(old_engine) or new_names != old_names:
        raise ValueError("external engine identities must be unique and exactly covered")
    modules = {
        ".".join(classname.split(".")[:2]).replace(".", "/") + ".py"
        for classname, _ in current if classname
    }
    if modules != {name for name in sources if name.startswith("tests/")}:
        raise ValueError("runtime check modules differ from inherited sources")
    if (
        frozen["target_revision"] != baseline
        or frozen["not_before"] != "2026-10-15T19:51:00+08:00"
    ):
        raise ValueError("preserved V61 source or reservation differs")
    for name, expected in frozen["analysis_sources"].items():
        if sha(ROOT / name) != expected:
            raise ValueError(f"registered analyzer changed: {name}")
    if git(candidate, "status", "--porcelain") or git(candidate, "rev-parse", "HEAD") != revision:
        raise ValueError("candidate changed during validation")
    return {
        "status": "qualified_for_future_cold_observation_not_registered_or_deployed",
        "observed_at_local": datetime.now().astimezone().isoformat(),
        "candidate_checkout": str(candidate), "candidate_revision": revision,
        "inherited_revision": baseline, "inherited_qualification": bind(args.inherited),
        "source_sha256": sources, "runtime_delta": runtime_delta,
        "engine": {**engine, "production_delta": engine_delta},
        "reclaim_evidence": bind(args.reclaim_evidence),
        "exact_combined_checks": {
            "passed_unique_tests": len(current),
            "passed_subtests": totals["tests"] - len(current),
            "inherited_runtime_identities_covered": len(prior),
            "inherited_engine_identities_covered": len(old_engine),
            "initial": {**bind(args.checks), **totals},
            "environment_failure_replacement": {
                **bind(args.supplement), **supplemental_totals,
            },
            "remaining_failures": 0, "remaining_errors": 0, "remaining_skips": 0,
            "external_engine_classname": (
                "The combined root emits empty classname for external engine tests. "
                "Names are unique and match all 143 previously bound engine identities."
            ),
            "initial_failure": (
                "The cached Django image lacks pytest. Preserve the failed receipt; "
                "replace only that identity with a passing existing pytest-image check."
            ),
        },
        "preserved_v61": bind(args.preserved_v61),
        "qualification_helper": bind(Path(__file__)),
        "gpu_benefit_verified": False, "goal_complete": False,
        "limits": [
            "No registration, deployment or changes to the V61 waiter are performed.",
            "Conditional development CPU benchmarks are not measured GPU or JCT benefits.",
            "The new canonical patch binds the complete combined engine tree.",
            "Only dispatch and reclamation production methods extend the frozen source.",
            "Model, harness, workload, deadlines and observation settings are inherited.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "candidate", "inherited", "reclaim-evidence", "checks", "supplement",
        "preserved-v61",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--output", type=Path, action="append", required=True)
    args = parser.parse_args()
    if any(path.exists() for path in args.output):
        raise FileExistsError("preserve existing qualification records")
    report = qualify(args)
    for path in args.output:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write("\n")
    print(json.dumps({key: report[key] for key in (
        "status", "candidate_revision", "exact_combined_checks",
    )}, indent=2))


if __name__ == "__main__":
    main()
