"""Run the paper-policy regression with exact before/after source binding."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file


TESTS = (
    "test_kvflow_native_sessions.py", "test_kvflow_runtime.py",
    "test_kvflow_native_cache.py", "test_kvflow.py", "test_paper_prefix_transfers.py",
    "test_progressive_native_restore.py", "test_paper_baseline_runtime.py",
    "test_paper_baseline_comparison.py", "test_paper_prefix_gpu_qualification.py",
    "test_tokencake_runtime.py", "test_tokencake_native_capacity.py",
    "test_tokencake_public_runtime.py", "test_tokencake_paper_runtime.py",
    "test_tokencake_gpu_qualification.py", "test_tokencake_capacity.py",
    "test_tokencake_lifecycle.py", "test_paper_baselines.py", "test_tokencake.py",
)


def source_manifest():
    paths = subprocess.check_output([
        "git", "ls-files", "--cached", "--others", "--exclude-standard",
        "--", "beliefkv", "scripts", "tests", "configs",
    ], cwd=ROOT, text=True).splitlines()
    paths.append(str(Path(__file__).relative_to(ROOT)))
    return {
        name: sha256_file(ROOT / name)
        for name in sorted(set(paths)) if (ROOT / name).is_file()
    }


def main():
    output = HERE / "bound_paper_cpu.json"
    xml = HERE / "bound_paper_cpu.xml"
    manifest_path = HERE / "bound_paper_cpu.sources.json"
    if any(path.exists() for path in (output, xml, manifest_path)):
        raise FileExistsError("preserve earlier source-bound CPU evidence")
    native_path = Path(os.environ["SGLANG_SOURCE_CHECKOUT"])
    native = git_source_state(native_path)
    native_tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=native_path, text=True,
    ).strip()
    if native["dirty"] or native_tree != "11b84f7c31986782be3fbe699bae2574ef092389":
        raise ValueError("CPU compatibility must use the exact common paper-control engine")
    before = source_manifest()
    env = dict(os.environ, CUDA_VISIBLE_DEVICES="99999", PYTHONHASHSEED="21")
    command = [
        sys.executable, "-m", "pytest", "-q", *(f"tests/{name}" for name in TESTS),
        "--tb=short", "--junitxml=" + str(xml),
    ]
    result = subprocess.run(command, cwd=ROOT, env=env, check=False)
    after = source_manifest()
    if before != after or native != git_source_state(native_path):
        raise ValueError("source changed during the CPU execution")
    suites = ET.parse(xml).findall(".//testsuite")
    totals = {
        field: sum(int(suite.attrib[field]) for suite in suites)
        for field in ("tests", "errors", "failures", "skipped")
    }
    identities = {
        (case.attrib["classname"], case.attrib["name"])
        for case in ET.parse(xml).findall(".//testcase")
    }
    with manifest_path.open("x") as stream:
        json.dump(before, stream, indent=2, allow_nan=False)
        stream.write("\n")
    evidence = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Paper-control CPU regression with real native cache/allocator and copy/event stand-ins.",
        "command": command, "returncode": result.returncode, "totals": totals,
        "unique_test_identities": len(identities), "native_engine": native,
        "native_tree": native_tree, "sources_unchanged_during_execution": True,
        "source_manifest": {"path": str(manifest_path), "sha256": sha256_file(manifest_path)},
        "report": {"path": str(xml), "sha256": sha256_file(xml)},
        "new_native_session_test_identities": sum(
            name.startswith("tests.test_kvflow_native_sessions") for name, _ in identities
        ),
        "new_session_gpu_scene_executed": False, "gpu_experiment_started": False,
        "whole_llm_scheduler_or_baseline_qualified": False, "performance_accepted": False,
    }
    with output.open("x") as stream:
        json.dump(evidence, stream, indent=2, allow_nan=False)
        stream.write("\n")
    if result.returncode or any(totals[field] for field in ("errors", "failures", "skipped")):
        raise ValueError("source-bound paper-policy CPU regression did not pass")
    if totals["tests"] != len(identities):
        raise ValueError("CPU case identities are missing or duplicated")
    print(json.dumps({
        "unique_test_identities": len(identities), "source_files": len(before),
        "new_native_session_test_identities": evidence["new_native_session_test_identities"],
        "gpu_experiment_started": False,
    }, indent=2))


if __name__ == "__main__":
    main()
