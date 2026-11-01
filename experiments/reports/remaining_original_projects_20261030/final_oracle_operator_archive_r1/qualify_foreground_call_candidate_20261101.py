"""Retain the full inherited CPU suite and bind the foreground-CALL repair."""

from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path("/home/longhao/experiment/BeliefKV")
REPORTS = ROOT / "experiments/reports/remaining_original_projects_20261030"
PRIOR = REPORTS / "prefill_decode_cadence_bound_cpu_r1/evidence.json"
HERE = REPORTS / "foreground_call_bound_cpu_r1"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts import run_causal_comparison as causal
from scripts.train_workflow_work import write


def cases(path):
    root = ET.parse(path).getroot()
    rows = {}
    for row in root.iter("testcase"):
        key = row.get("classname"), row.get("name")
        if key in rows:
            raise ValueError("duplicate literal test identity")
        rows[key] = any(row.find(name) is not None for name in ("failure", "error", "skipped"))
    return rows, sum(int(row.get("tests", 0)) for row in root.iter("testsuite"))


def main():
    if HERE.exists() or os.getpriority(os.PRIO_PROCESS, 0) != 19 or (
        os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
    ):
        raise ValueError("keep fresh evidence and use hidden-CUDA nice19")
    prior = causal.load(PRIOR)
    previous_sources = causal.load(Path(prior["source_manifest"]["path"]))
    changed = {
        name for name, digest in previous_sources.items()
        if sha256_file(ROOT / name) != digest
    }
    if changed != {"beliefkv/runtime/sglang_v0520_runtime.py"}:
        raise ValueError("unexpected inherited-source changes: " + str(sorted(changed)))
    for name in ("report", "source_manifest"):
        if sha256_file(Path(prior[name]["path"])) != prior[name]["sha256"]:
            raise ValueError("inherited CPU evidence changed")
    external = dict(prior["external_inputs_sha256"])
    for path in (PRIOR, Path(__file__), ROOT.parent / "ops/audit_foreground_call_capacity_20261101.py"):
        external[str(path)] = sha256_file(path)
    if any(sha256_file(Path(path)) != digest for path, digest in external.items()):
        raise ValueError("inherited or new external input changed")
    sources = {name: sha256_file(ROOT / name) for name in previous_sources}
    added = "tests/test_foreground_call_cold_reclaim.py"
    sources[added] = sha256_file(ROOT / added)
    environment = dict(prior["environment"])
    engine = Path(environment["SGLANG_SOURCE_CHECKOUT"])
    native = git_source_state(engine)
    if native != prior["native_source"]:
        raise ValueError("the qualified native CPU tree changed")
    inherited_cases, _ = cases(Path(prior["report"]["path"]))
    if any(inherited_cases.values()):
        raise ValueError("the inherited suite is not fully passing")
    command = [
        value for value in prior["command"] if not value.startswith("--junitxml=")
    ] + [added, "--junitxml=" + str(HERE / "bound_cpu.xml")]
    HERE.mkdir(parents=True, exist_ok=False)
    with (HERE / "pytest.log").open("x") as log:
        completed = subprocess.run(
            command, cwd=ROOT, env={**os.environ, **environment},
            stdout=log, stderr=subprocess.STDOUT,
        )
    observed, total = cases(HERE / "bound_cpu.xml")
    missing = sorted(set(inherited_cases) - set(observed))
    bad = sorted(key for key, failed in observed.items() if failed)
    unchanged = all(sha256_file(ROOT / name) == digest for name, digest in sources.items())
    if git_source_state(engine) != native or any(
        sha256_file(Path(path)) != digest for path, digest in external.items()
    ):
        raise ValueError("native or external input changed during qualification")
    write(HERE / "sources.json", sources)
    write(HERE / "evidence.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "command": command, "environment": environment, "returncode": completed.returncode,
        "unique_test_identities": len(observed), "junit_tests_including_subtests": total,
        "failed_error_skipped_cases": bad, "missing_identities": missing,
        "all_retained_literal_identities_present": not missing,
        "all_previous_source_paths_retained": set(previous_sources) <= set(sources),
        "sources_unchanged_during_execution": unchanged,
        "prior_evidence_sha256": sha256_file(PRIOR),
        "prior_source_paths_changed_by_candidate": sorted(changed),
        "native_source": native, "external_inputs_sha256": external,
        "report": {"path": str(HERE / "bound_cpu.xml"), "sha256": sha256_file(HERE / "bound_cpu.xml")},
        "source_manifest": {"path": str(HERE / "sources.json"), "sha256": sha256_file(HERE / "sources.json")},
        "source_kind": prior["source_kind"],
        "composition_origins_require_complete_registration_proofs_before_GPU": True,
        "GPU_experiment_started": False, "performance_accepted": False,
    })
    if completed.returncode or bad or missing or not unchanged:
        raise RuntimeError("preserve failed foreground-CALL CPU qualification")
    print({
        "passed_literal_cases": len(observed), "including_subtests": total,
        "bound_sources": len(sources), "retained_prior_cases": len(inherited_cases),
        "GPU_action": False,
    }, flush=True)


if __name__ == "__main__":
    main()
