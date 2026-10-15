#!/usr/bin/env python3
"""Bind an isolated scheduling candidate to inherited and exact CPU evidence."""

from __future__ import annotations

import argparse
import ast
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


CHANGED = {
    "beliefkv/runtime/event_channel.py",
    "beliefkv/runtime/sglang_v0520_admission.py",
    "beliefkv/runtime/sglang_v0520_runtime.py",
    "scripts/benchmark_compact_event_wire.py",
    "scripts/benchmark_prefill_key_equality.py",
    "scripts/benchmark_prefill_key_fallback.py",
    "scripts/benchmark_prefill_live_checks.py",
    "tests/test_benchmark_prefill_live_checks.py",
    "tests/test_event_channel.py",
    "tests/test_sglang_v0520_admission.py",
    "tests/test_sglang_v0520_runtime.py",
}
DOCKER_TEST = (
    "tests.test_deepagents_swebench",
    "test_test_progress_shadow_in_real_sandbox",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bind(path: Path) -> dict:
    return {"path": str(path.resolve()), "sha256": sha(path)}


def load(path: Path) -> dict:
    return json.loads(path.read_bytes())


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def checks(path: Path) -> tuple[dict, dict]:
    root = ET.parse(path).getroot()
    rows = {}
    for case in root.iter("testcase"):
        key = case.get("classname"), case.get("name")
        if key in rows:
            raise ValueError(f"duplicate test identity: {key}")
        rows[key] = next((
            status for status in ("failure", "error", "skipped")
            if case.find(status) is not None
        ), "passed")
    totals = {
        key: sum(int(suite.get(key, 0)) for suite in root.iter("testsuite"))
        for key in ("tests", "failures", "errors", "skipped")
    }
    return rows, totals


def check_ast(root: Path, baseline: str, path: str, *, class_name=None, methods=(), functions=()):
    before = ast.parse(git(root, "show", f"{baseline}:{path}"))
    after = ast.parse((root / path).read_text())
    if class_name is not None:
        classes = [
            next(node for node in module.body
                 if isinstance(node, ast.ClassDef) and node.name == class_name)
            for module in (before, after)
        ]
        method_maps = [
            {node.name: ast.dump(node, include_attributes=False)
             for node in cls.body if isinstance(node, ast.FunctionDef)}
            for cls in classes
        ]
        changed = {
            name for name in method_maps[0].keys() | method_maps[1].keys()
            if method_maps[0].get(name) != method_maps[1].get(name)
        }
        if changed != set(methods):
            raise ValueError(f"unexpected class changes in {path}: {changed}")
        for cls in classes:
            cls.body = [
                node for node in cls.body
                if not isinstance(node, ast.FunctionDef) or node.name not in changed
            ]
    for module in (before, after):
        module.body = [
            node for node in module.body
            if not isinstance(node, ast.FunctionDef) or node.name not in functions
        ]
    if path.endswith("/sglang_v0520_admission.py"):
        additions = [
            node for node in after.body
            if isinstance(node, ast.ImportFrom) and node.module == "types"
        ]
        if len(additions) != 1 or [
            name.name for name in additions[0].names
        ] != ["NotImplementedType"]:
            raise ValueError("unexpected key-comparison import")
        after.body.remove(additions[0])
    if ast.dump(before, include_attributes=False) != ast.dump(after, include_attributes=False):
        raise ValueError(f"unrelated production AST changes in {path}")
    return {"class": class_name, "methods": sorted(methods), "functions": sorted(functions)}


def check_cpu_evidence(path: Path, candidate: Path, sources: dict) -> dict:
    report = load(path)
    for relative, digest in report["candidate_sources"].items():
        if sha(candidate / relative) != digest:
            raise ValueError(f"CPU source mismatch: {path.name}: {relative}")
        sources[relative] = digest
    proofs = (
        "plan_selection_graph_and_counters_equal",
        "plans_live_selection_and_complete_graph_equal",
        "graph_counters_exact_acks_and_timing_counts_equal",
        "all_results_equal",
    )
    cases = report["cases"]
    if not cases:
        raise ValueError("CPU evidence has no cases")
    if "equal_parent_timing_scopes" in report and report["equal_parent_timing_scopes"] is not True:
        raise ValueError("asymmetric parent timing")
    prefixes = {}
    for case in cases:
        if not any(case.get(proof) is True for proof in proofs):
            raise ValueError(f"CPU equivalence missing: {path.name}")
        if case.get("physical_actions", 0):
            raise ValueError("CPU fixture issued physical actions")
        timing = case.get("timing_phase_counts_by_arm")
        if timing is not None and timing["baseline"] != timing["candidate"]:
            raise ValueError("unequal CPU timing phase counts")
        source = case.get("source")
        if source is not None and "read_prefix_sha256" in source:
            prefixes[source["path"]] = source
    for source_path, source in prefixes.items():
        with Path(source_path).open("rb") as stream:
            data = stream.read(source["read_prefix_bytes"])
        if (
            len(data) != source["read_prefix_bytes"]
            or hashlib.sha256(data).hexdigest() != source["read_prefix_sha256"]
        ):
            raise ValueError(f"historical prefix changed: {source_path}")
    return {
        **bind(path), "status": report["status"], "cases": len(cases),
        "verified_historical_prefixes": len(prefixes),
        "candidate_source_count": len(report["candidate_sources"]),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--inherited", type=Path, required=True)
    parser.add_argument("--frozen-v60", type=Path, required=True)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--checks", type=Path, required=True)
    parser.add_argument("--recheck", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, action="append", required=True)
    args = parser.parse_args()
    if any(path.exists() for path in args.output):
        raise ValueError("preserve existing qualification receipts")
    candidate = args.candidate.resolve()
    inherited = load(args.inherited)
    baseline = inherited["candidate_revision"]
    revision = git(candidate, "rev-parse", "HEAD")
    if git(candidate, "status", "--porcelain"):
        raise ValueError("candidate checkout must be clean")
    if git(candidate, "rev-parse", "HEAD^") != baseline:
        raise ValueError("candidate does not directly extend the inherited qualification")
    if set(git(candidate, "diff", "--name-only", baseline, revision).splitlines()) != CHANGED:
        raise ValueError("unexpected candidate paths")
    production = {
        "beliefkv/runtime/event_channel.py": check_ast(
            candidate, baseline, "beliefkv/runtime/event_channel.py",
            functions=("_encode_event_message",),
        ),
        "beliefkv/runtime/sglang_v0520_admission.py": check_ast(
            candidate, baseline, "beliefkv/runtime/sglang_v0520_admission.py",
            class_name="PrefillCandidateKey", methods=("__eq__",),
        ),
        "beliefkv/runtime/sglang_v0520_runtime.py": check_ast(
            candidate, baseline, "beliefkv/runtime/sglang_v0520_runtime.py",
            class_name="NativeAdmissionRuntime",
            methods=("_prefill_causal_ranks", "plan_native_prefill"),
        ),
    }
    frozen = args.frozen_v60.resolve()
    if (
        str(frozen) != inherited["preserved_v60"]["checkout"]
        or git(frozen, "rev-parse", "HEAD") != inherited["preserved_v60"]["revision"]
        or git(frozen, "status", "--porcelain")
    ):
        raise ValueError("frozen V60 changed")
    registration = inherited["preserved_v60"]["registration"]
    if sha(Path(registration["path"])) != registration["sha256"]:
        raise ValueError("existing reservation registration changed")
    sources = dict(inherited["source_sha256"])
    for relative, digest in sources.items():
        if relative not in CHANGED and sha(candidate / relative) != digest:
            raise ValueError(f"inherited source changed: {relative}")
    evidence = [
        check_cpu_evidence(path, candidate, sources) for path in args.evidence
    ]
    inherited_rows, _ = checks(Path(inherited["exact_candidate_checks"]["initial"]["path"]))
    initial, initial_totals = checks(args.checks)
    rechecked, recheck_totals = checks(args.recheck)
    unresolved = {key for key, status in initial.items() if status != "passed"}
    if (
        not inherited_rows.keys() <= initial.keys()
        or unresolved != {DOCKER_TEST}
        or initial[DOCKER_TEST] != "skipped"
        or set(rechecked) != unresolved
        or any(status != "passed" for status in rechecked.values())
        or any(recheck_totals[field] for field in ("failures", "errors", "skipped"))
    ):
        raise ValueError("exact candidate coverage or resolved Docker check does not match")
    test_modules = {
        ".".join(classname.split(".")[:2]).replace(".", "/") + ".py"
        for classname, _ in initial
    }
    expected_modules = {
        path for path in inherited["source_sha256"] if path.startswith("tests/")
    } | {"tests/test_sglang_v0520_admission.py", "tests/test_benchmark_prefill_live_checks.py"}
    if test_modules != expected_modules:
        raise ValueError("cold check module scope changed")
    sources.update({relative: None for relative in CHANGED | test_modules})
    sources = {relative: sha(candidate / relative) for relative in sorted(sources)}
    engine_path = args.engine.resolve()
    if (candidate / "third_party/sglang-v0.5.20").resolve() != engine_path:
        raise ValueError("candidate engine fixture points at a different checkout")
    sys.path.insert(0, str(candidate))
    from beliefkv.experiments.source_provenance import git_source_state

    engine = git_source_state(
        engine_path,
        canonical_patch=candidate / "patches/sglang-v0.5.20-beliefkv-staging.patch",
    )["canonical_patch"]
    for field in ("sha256", "expected_tree", "actual_tree", "matches_worktree", "covered_untracked_paths"):
        if engine[field] != inherited["engine"][field]:
            raise ValueError(f"inherited engine changed: {field}")
    for relative, digest in inherited["cpu_fixture_engine_sources"].items():
        if sha(engine_path / relative) != digest:
            raise ValueError(f"CPU engine fixture changed: {relative}")
    report = {
        "status": "qualified_for_cold_observation_not_deployed_or_gpu_accepted",
        "observed_at_local": datetime.now().astimezone().isoformat(),
        "candidate_checkout": str(candidate), "candidate_revision": revision,
        "inherited_revision": baseline, "inherited_qualification": bind(args.inherited),
        "source_sha256": sources, "production_delta": production,
        "cpu_evidence": evidence,
        "exact_candidate_checks": {
            "passed_unique_tests": len(initial),
            "passed_subtests": initial_totals["tests"] - len(initial),
            "remaining_failures": 0, "remaining_errors": 0, "remaining_skips": 0,
            "inherited_test_identities_covered": len(inherited_rows),
            "initial": {**bind(args.checks), **initial_totals},
            "targeted_recheck": {**bind(args.recheck), **recheck_totals},
            "resolved_test": list(DOCKER_TEST),
            "setup": "Selected an existing cached SWE-bench image for the CPU-only Docker check; no image pull or tracked source change.",
        },
        "engine": engine, "candidate_engine": str(engine_path),
        "preserved_v60": inherited["preserved_v60"],
        "qualification_helper": bind(Path(__file__)),
        "limits": [
            "This extends d925278 with immutable-key equality, same-call prefill checks and compact event wire.",
            "The original harness, models, engine, workload, physical budgets and frozen V60 remain unchanged.",
            "V665/V666 have asymmetric timing and are excluded; V667 binds equal parent scopes.",
            "Keep rebuilt-key, foreign-type and dense-message CPU regressions; do not add component percentages.",
            "This is cold observation qualification, not deployment, reservation override or GPU/JCT acceptance.",
            "Live opportunity frequency, useful FULL consumption, quality, fairness, recomputation and paired JCT/throughput remain unverified.",
        ],
    }
    if git(candidate, "rev-parse", "HEAD") != revision or git(candidate, "status", "--porcelain"):
        raise ValueError("candidate changed during qualification")
    for path in args.output:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write("\n")
    print(json.dumps({
        "status": report["status"], "candidate_revision": revision,
        "source_count": len(sources), "passed_unique_tests": len(initial),
        "passed_subtests": report["exact_candidate_checks"]["passed_subtests"],
        "outputs": [str(path) for path in args.output],
    }, indent=2), flush=True)


if __name__ == "__main__":
    main()
