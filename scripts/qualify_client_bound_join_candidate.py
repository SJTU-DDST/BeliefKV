#!/usr/bin/env python3
"""Bind the client-bound JOIN handoff to an isolated, CPU-checked candidate."""

from __future__ import annotations

import argparse
import ast
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from beliefkv.experiments.source_provenance import git_source_state
from scripts.qualify_combined_cpu_candidate import bind, checks, git, load, sha

RUNTIME = "beliefkv/runtime/sglang_v0520_runtime.py"
TEST = "tests/test_join_confirmed_reentry.py"
METHODS = {
    "__init__", "on_events", "_advance_join_ticket", "_bind_confirmed_join_submit",
    "_live_join_ticket", "_pending_confirmed_join_ticket", "_pending_confirmed_join_submit",
    "_select_confirmed_join_ticket", "dispatch_join_prefetch",
    "snapshot_session_anchors", "inspect_context_h2d_opportunity",
    "_record_wait_prefetch_plan", "refreshed_prefetch_gpu_step", "_record_prefetch_issued",
    "_live_wait_prefetch", "_prefetch_lease_invalid_reason",
}
FIELDS = (
    ("client_request_id", "str | None", "None"),
    ("client_submit_ts_ms", "float | None", "None"),
    ("checkpoint_key", "PrefillCandidateKey | None", "None"),
    ("checkpoint_input_tokens", "int | None", "None"),
    ("restoration_complete", "bool", "False"),
)


def runtime_delta(candidate: Path, baseline: str) -> dict:
    before = ast.parse(git(candidate, "show", f"{baseline}:{RUNTIME}"))
    after = ast.parse((candidate / RUNTIME).read_text())
    old_classes = {node.name: node for node in before.body if isinstance(node, ast.ClassDef)}
    new_classes = {node.name: node for node in after.body if isinstance(node, ast.ClassDef)}
    old_methods = {
        node.name: ast.dump(node, include_attributes=False)
        for node in old_classes["NativeAdmissionRuntime"].body
        if isinstance(node, ast.FunctionDef)
    }
    new_methods = {
        node.name: ast.dump(node, include_attributes=False)
        for node in new_classes["NativeAdmissionRuntime"].body
        if isinstance(node, ast.FunctionDef)
    }
    changed = {
        name for name in old_methods.keys() | new_methods.keys()
        if old_methods.get(name) != new_methods.get(name)
    }
    if changed != METHODS:
        raise ValueError(f"runtime method scope differs: {sorted(changed ^ METHODS)}")
    ticket = new_classes["_JoinPrefetchTicket"]
    suffix = ticket.body[-len(FIELDS):]
    for node, (name, annotation, value) in zip(suffix, FIELDS, strict=True):
        if (
            not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name)
            or node.target.id != name or ast.unparse(node.annotation) != annotation
            or ast.unparse(node.value) != value
        ):
            raise ValueError("JOIN ticket checkpoint/consumer fields differ")
    ticket.body = ticket.body[:-len(FIELDS)]
    for classes in (old_classes, new_classes):
        runtime = classes["NativeAdmissionRuntime"]
        runtime.body = [
            node for node in runtime.body
            if not isinstance(node, ast.FunctionDef) or node.name not in METHODS
        ]
    if ast.dump(before, include_attributes=False) != ast.dump(after, include_attributes=False):
        raise ValueError("runtime changed outside the bound methods and ticket fields")
    return {"methods": sorted(changed), "appended_ticket_fields": [name for name, _, _ in FIELDS]}


def qualify(args) -> dict:
    candidate = args.candidate.resolve()
    inherited = load(args.inherited)
    baseline = inherited["candidate_revision"]
    revision = git(candidate, "rev-parse", "HEAD")
    if (
        git(candidate, "status", "--porcelain")
        or git(candidate, "rev-parse", "HEAD^") != baseline
        or set(git(candidate, "diff", "--name-only", baseline, revision).splitlines()) != {RUNTIME, TEST}
    ):
        raise ValueError("candidate must cleanly extend the inherited runtime and JOIN tests")
    delta = runtime_delta(candidate, baseline)
    source_parent = Path(inherited["candidate_checkout"])
    if git(source_parent, "rev-parse", "HEAD") != baseline or git(source_parent, "status", "--porcelain"):
        raise ValueError("inherited candidate changed")
    sources = dict(inherited["source_sha256"])
    for name, expected in sources.items():
        if name not in (RUNTIME, TEST) and sha(candidate / name) != expected:
            raise ValueError(f"unrelated source changed: {name}")
    prior, _ = checks(Path(inherited["exact_candidate_checks"]["checks"]["path"]))
    current, initial_totals = checks(args.checks)
    supplement, supplement_totals = checks(args.supplement)
    if (
        initial_totals["failures"] or initial_totals["errors"]
        or any(supplement_totals[key] for key in ("failures", "errors", "skipped"))
        or not supplement.keys() <= current.keys()
        or any(current[key] != "skipped" for key in supplement)
    ):
        raise ValueError("supplement must replace only initially skipped checks")
    current.update(supplement)
    if not prior.keys() <= current.keys() or any(value != "passed" for value in current.values()):
        raise ValueError("candidate did not pass every inherited identity")
    modules = {
        ".".join(classname.split(".")[:2]).replace(".", "/") + ".py"
        for classname, _ in current
    }
    if modules != {name for name in sources if name.startswith("tests/")}:
        raise ValueError("check modules differ from the inherited source manifest")
    for name in (RUNTIME, TEST):
        sources[name] = sha(candidate / name)
    cpu = load(args.cpu_control)
    for name, expected in cpu["baseline_sources"].items():
        if inherited["source_sha256"].get(name) != expected:
            raise ValueError(f"CPU baseline differs: {name}")
    for name, expected in cpu["candidate_sources"].items():
        location = candidate / name if name.startswith("beliefkv/") else ROOT / name
        if sha(location) != expected:
            raise ValueError(f"CPU candidate differs: {name}")
    if len(cpu["cases"]) != 4 or any(case["same_counters_and_no_actions"] is not True for case in cpu["cases"]):
        raise ValueError("incomplete idle CPU controls")
    engine_path = (candidate / "third_party/sglang-v0.5.20").resolve()
    engine = git_source_state(
        engine_path, canonical_patch=candidate / "patches/sglang-v0.5.20-beliefkv-staging.patch",
    )["canonical_patch"]
    if str(engine_path) != inherited["candidate_engine"] or any(
        engine[key] != inherited["engine"][key] for key in (
            "sha256", "expected_tree", "actual_tree", "matches_worktree", "covered_untracked_paths",
        )
    ):
        raise ValueError("native engine changed")
    frozen = load(args.preserved_v61)
    if frozen["target_revision"] != baseline or frozen["not_before"] != "2026-10-15T19:51:00+08:00":
        raise ValueError("registered V61 candidate or reservation differs")
    for name, expected in frozen["analysis_sources"].items():
        if sha(ROOT / name) != expected:
            raise ValueError(f"registered V61 analysis changed: {name}")
    if git(candidate, "status", "--porcelain") or git(candidate, "rev-parse", "HEAD") != revision:
        raise ValueError("candidate changed during qualification")
    return {
        "status": "qualified_for_cold_observation_not_deployed_or_gpu_accepted",
        "observed_at_local": datetime.now().astimezone().isoformat(),
        "candidate_checkout": str(candidate), "candidate_revision": revision,
        "inherited_revision": baseline, "inherited_qualification": bind(args.inherited),
        "source_sha256": sources, "production_delta": {RUNTIME: delta},
        "exact_candidate_checks": {
            "passed_unique_tests": len(current),
            "passed_subtests": initial_totals["tests"] - len(current),
            "inherited_test_identities_covered": len(prior),
            "remaining_failures": 0, "remaining_errors": 0, "remaining_skips": 0,
            "initial": {**bind(args.checks), **initial_totals},
            "skipped_identity_supplement": {**bind(args.supplement), **supplement_totals},
            "setup": "CPU-only; explicit existing cached sandbox image, no image pull.",
        },
        "idle_dispatch_cpu": {
            **bind(args.cpu_control),
            "added_mean_thread_cpu_us": [case["added_mean_thread_cpu_us"] for case in cpu["cases"]],
            "scope": "No live handoff; not active-event cost, DMA, GPU throughput or JCT.",
        },
        "engine": engine, "candidate_engine": str(engine_path),
        "preserved_v61": bind(args.preserved_v61), "qualification_helper": bind(Path(__file__)),
        "limits": [
            "Only runtime and JOIN tests extend the frozen V61 source; harness, heads, workload, budgets and native engine remain unchanged.",
            "Old request registration still retires. A separately bound consumer retains only the prior native session and safe input checkpoint.",
            "Compaction, missing continuity/input/generation evidence, expiry, cancellation and native visibility invalidate new recovery.",
            "Complete restorations retain consumer identity for residency checks while other confirmed JOINs progress.",
            "Client-bound state proves neither HTTP arrival order nor useful reuse; independent submit, arrival and first-service evidence determines attribution.",
            "No deployment, reservation override, quality/fairness/recomputation acceptance or mean-JCT/throughput gain is established.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("candidate", "inherited", "checks", "supplement", "cpu-control", "preserved-v61"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--output", type=Path, action="append", required=True)
    args = parser.parse_args()
    if any(path.exists() for path in args.output):
        raise FileExistsError("preserve existing qualification receipts")
    report = qualify(args)
    for path in args.output:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write("\n")
    print(json.dumps({key: report[key] for key in (
        "status", "candidate_revision", "exact_candidate_checks",
    )}, indent=2))


if __name__ == "__main__":
    main()
