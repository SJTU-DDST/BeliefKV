#!/usr/bin/env python3
"""Independently reconcile current CPU sources, native tree and preserved checks."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.native_engine_variant import SIBLING_CAPACITY_TREE, engine_patch
from beliefkv.experiments.source_provenance import git_source_state


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bind(path):
    return {"path": str(path), "sha256": sha(path)}


def load(path):
    return json.loads(path.read_bytes())


def cases(path):
    root = ET.parse(path).getroot()
    result = {}
    for item in root.iter("testcase"):
        key = item.get("classname"), item.get("name")
        status = next((
            name for name in ("failure", "error", "skipped") if item.find(name) is not None
        ), "passed")
        if key in result:
            raise ValueError("duplicate testcase identity")
        result[key] = status
    return result


def main():
    output = HERE / "final_cpu_review.json"
    if output.exists():
        raise FileExistsError("preserve previous review")
    evidence_path = HERE / "qualified_shared_cpu_r2.evidence.json"
    evidence = load(evidence_path)
    manifest_path = Path(evidence["source_manifest"]["path"])
    if sha(manifest_path) != evidence["source_manifest"]["sha256"]:
        raise ValueError("application source manifest changed")
    sources = load(manifest_path)
    if any(sha(ROOT / name) != digest for name, digest in sources.items()):
        raise ValueError("qualified application source changed")
    current = cases(Path(evidence["report"]["path"]))
    current_xml = ET.parse(Path(evidence["report"]["path"])).getroot()
    junit_cases = sum(int(suite.get("tests", 0)) for suite in current_xml.iter("testsuite"))
    previous = cases(ROOT / "experiments/reports/join_blocked_layer_groups_20261028/fixture_qualified_shared_cpu.xml")
    if not previous.keys() <= current.keys() or set(current.values()) != {"passed"}:
        raise ValueError("shared CPU coverage missing or not passing")
    native_report = HERE / "native_targeted_r1.xml"
    native_checks = cases(native_report)
    if set(native_checks.values()) != {"passed"}:
        raise ValueError("native CPU checks are not all passing")
    engine = Path(evidence["environment"]["SGLANG_SOURCE_CHECKOUT"])
    current_native = git_source_state(engine, canonical_patch=engine_patch(ROOT, SIBLING_CAPACITY_TREE))
    if current_native != evidence["native_engine"]:
        raise ValueError("declared native source changed")
    if current_native["canonical_patch"]["actual_tree"] != SIBLING_CAPACITY_TREE:
        raise ValueError("wrong native tree")
    old = load(ROOT / "configs/migration/predictive_join_blocked_layer_groups_20261028.json")
    new = load(ROOT / "configs/migration/predictive_sibling_service_capacity_20261028.json")
    differences = sorted(
        key for key in old["common_environment"].keys() | new["common_environment"].keys()
        if old["common_environment"].get(key) != new["common_environment"].get(key)
    )
    if differences != ["SGLANG_PATCH_FLAVOR"]:
        raise ValueError(f"workload/capacity/timing environment changed: {differences}")
    for key in ("source_profile", "source_registration", "tool_timing_overlay", "order", "native_reference"):
        if old[key] != new[key]:
            raise ValueError("original comparison contract changed: " + key)
    required = {
        "layer_handoff_enabled": True, "enable_tool_prefetch": False,
        "enable_predicted_tool_offload": True, "enable_agent_working_set": True,
        "coadmit_join_children": True, "agent_join_blocked_handoff": True,
    }
    for name, expected in required.items():
        if evidence["constructors"]["full"]["verified_configuration"].get(name) is not expected:
            raise ValueError("effective Full constructor mismatch: " + name)
    for arm in ("thunderagent", "thunderagent_acting_decay"):
        actual = evidence["constructors"][arm]["verified_configuration"]
        if any(actual.get(name) is not False for name in required):
            raise ValueError("Full implementation leaked into paper control: " + arm)
    cost_path = HERE / "control_cost_cpu_r4.json"
    cost = load(cost_path)
    if any(sha(ROOT / name) != digest for name, digest in cost["candidate_sources"].items()):
        raise ValueError("control-cost sources changed")
    if cost["gpu_execution"] is not False:
        raise ValueError("CPU cost fixture consumed GPU")
    failures = {}
    for name in (
        "initial_targeted.xml", "implementation_targeted_r1.xml",
        "physical_targeted_r1.xml", "qualified_shared_cpu_r1.xml",
        "multi_donor_fixture_retry.xml",
    ):
        path = HERE / name
        checks = cases(path)
        failures[name] = {
            **bind(path),
            "nonpassing_cases": sum(value != "passed" for value in checks.values()),
        }
    result = {
        "scope": "current_source_bound_cpu_implementation_not_gpu_or_performance_acceptance",
        "application_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        ).strip(),
        "qualification": bind(evidence_path), "source_manifest": bind(manifest_path),
        "bound_application_files": len(sources),
        "shared_unique_testcases": len(current),
        "prior_unique_testcases_retained": len(previous),
        "new_unique_testcases": len(current.keys() - previous.keys()),
        "shared_passing_subtests": junit_cases - len(current),
        "legacy_evidence_passing_cases_field": evidence["passing_cases_including_subtests"],
        "native_checks": {**bind(native_report), "unique_testcases": len(native_checks)},
        "native_engine": current_native,
        "control_cost": {**bind(cost_path), "cases": cost["cases"]},
        "comparison_environment_changes": differences,
        "three_effective_constructors_verified": True,
        "preserved_development_failures": failures,
        "gpu_execution_registered": False,
        "gpu_numerical_correctness_verified": False,
        "performance_accepted": False,
        "remaining_limits": [
            "Logical eligibility is not atomic reservation of physical HBM and Host capacity.",
            "Multiple small donors use native ACK-gated backup/reclaim, not a combined layer transaction.",
            "All 8/4 layer groups are still submitted synchronously; only proven independent ACKs bypass drain.",
            "One layer transaction remains in flight; unsubmitted queues and unknown dependencies still refuse.",
            "Small-fixture thread CPU timings exclude writer thread, DMA, GPU and whole-workload effects.",
        ],
    }
    with output.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({
        name: result[name] for name in (
            "scope", "shared_unique_testcases", "prior_unique_testcases_retained",
            "new_unique_testcases", "shared_passing_subtests",
            "three_effective_constructors_verified",
        )
    }, indent=2))


if __name__ == "__main__":
    main()
