"""Retain the earlier Agentix pressure regression and add native radix sessions."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
PRIOR = ROOT / "experiments/reports/agentix_pressure_all_baselines_cpu_20261025.xml"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from experiments.reports.kvflow_radix_session_20261028.run_bound_cpu import source_manifest
from experiments.reports.tokencake_radix_session_20261028.run_bound_cpu import case_ids


def main():
    output, xml, manifest = (HERE / ("bound_cpu" + suffix) for suffix in (
        ".json", ".xml", ".sources.json",
    ))
    if any(path.exists() for path in (output, xml, manifest)):
        raise FileExistsError("preserve prior Agentix session CPU executions")
    previous = case_ids(PRIOR)
    if len(previous) != 690:
        raise ValueError("prior Agentix pressure CPU population differs")
    native_path = Path(os.environ["SGLANG_SOURCE_CHECKOUT"])
    native_patch = ROOT.parent / "ops/sglang-agentix-runtime-20261025.patch"
    native = git_source_state(native_path, canonical_patch=native_patch)
    if (
        not native["canonical_patch"]["matches_worktree"]
        or native["canonical_patch"]["actual_tree"] != "c489dbb85265efce25622a52abfd698e9199688d"
    ):
        raise ValueError("Agentix session CPU requires its original native scheduler binding")
    tests = {f"tests/{module.split('.')[1]}.py" for module, _ in previous}
    tests |= {str(path.relative_to(ROOT)) for path in (ROOT / "tests").glob("test_agentix_*.py")}
    if any(not (ROOT / name).is_file() for name in tests):
        raise ValueError("a required Agentix pressure test module is missing")

    def snapshot():
        names = source_manifest().keys() | {str(Path(__file__).relative_to(ROOT))}
        return {name: sha256_file(ROOT / name) for name in sorted(names)}

    before = snapshot()
    command = [
        sys.executable, "-m", "pytest", "-q", *sorted(tests),
        "--tb=short", "--junitxml=" + str(xml),
    ]
    result = subprocess.run(
        command, cwd=ROOT, check=False,
        env=dict(os.environ, CUDA_VISIBLE_DEVICES="99999", PYTHONHASHSEED="21"),
    )
    if before != snapshot() or native != git_source_state(native_path, canonical_patch=native_patch):
        raise ValueError("Agentix/native source changed during CPU regression")
    suites = ET.parse(xml).findall(".//testsuite")
    totals = {
        name: sum(int(row.attrib[name]) for row in suites)
        for name in ("tests", "errors", "failures", "skipped")
    }
    current = case_ids(xml)
    with manifest.open("x") as stream:
        json.dump(before, stream, indent=2, allow_nan=False)
        stream.write("\n")
    record = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Real native Req/PrefillAdder/session/cache/allocator; CPU execution and copy stand-ins.",
        "command": command, "returncode": result.returncode, "totals": totals,
        "unique_test_identities": len(current),
        "all_prior_690_identities_retained": previous <= current,
        "retained_prior_identities": len(previous & current),
        "native_engine": native, "sources_unchanged_during_execution": True,
        "source_manifest": {"path": str(manifest), "sha256": sha256_file(manifest)},
        "report": {"path": str(xml), "sha256": sha256_file(xml)},
        "inputs_sha256": {str(path): sha256_file(path) for path in (PRIOR, native_patch)},
        "GPU_experiment_started": False, "formal_agentix_enabled": False, "performance_accepted": False,
    }
    with output.open("x") as stream:
        json.dump(record, stream, indent=2, allow_nan=False)
        stream.write("\n")
    if (
        result.returncode or any(totals[name] for name in ("errors", "failures", "skipped"))
        or len(current) != totals["tests"] or not record["all_prior_690_identities_retained"]
    ):
        raise ValueError("complete Agentix session CPU regression failed")
    print(json.dumps({"tests": len(current), "retained": len(previous), "sources": len(before)}, indent=2))


if __name__ == "__main__":
    main()
