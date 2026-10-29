"""Bind the formal TokenCake configuration, native sessions and all prior cases."""

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
from experiments.reports.kvflow_radix_session_20261028.run_bound_cpu import source_manifest
from experiments.reports.tokencake_radix_session_20261028.run_bound_cpu import case_ids


def main():
    output, xml, manifest_path = (
        HERE / ("formal_bound_cpu" + suffix) for suffix in (".json", ".xml", ".sources.json")
    )
    if any(path.exists() for path in (output, xml, manifest_path)):
        raise FileExistsError("preserve earlier formal CPU execution")
    prior_path = HERE / "bound_cpu.json"
    prior = json.loads(prior_path.read_bytes())
    prior_manifest = Path(prior["source_manifest"]["path"])
    prior_report = Path(prior["report"]["path"])
    for name, path in (("source_manifest", prior_manifest), ("report", prior_report)):
        if sha256_file(path) != prior[name]["sha256"]:
            raise ValueError("original 742-case CPU evidence changed")
    native_path = Path(os.environ["SGLANG_SOURCE_CHECKOUT"])
    native = git_source_state(native_path)
    tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=native_path, text=True,
    ).strip()
    if native["dirty"] or tree != prior["native_tree"]:
        raise ValueError("formal TokenCake CPU requires the same clean common native tree")

    def snapshot():
        names = json.loads(prior_manifest.read_bytes()).keys() | source_manifest().keys()
        names |= {
            str(path.relative_to(ROOT)) for path in HERE.glob("*.py")
        }
        return {name: sha256_file(ROOT / name) for name in sorted(names)}

    before = snapshot()
    tests = [name for name in prior["command"] if name.startswith("tests/")]
    tests.extend(("tests/test_tokencake_component_queue.py", "tests/test_tokencake_locobench_config.py"))
    command = [
        sys.executable, "-m", "pytest", "-q", *tests,
        "--tb=short", "--junitxml=" + str(xml),
    ]
    result = subprocess.run(
        command, cwd=ROOT, check=False,
        env=dict(os.environ, CUDA_VISIBLE_DEVICES="99999", PYTHONHASHSEED="21"),
    )
    if before != snapshot() or native != git_source_state(native_path):
        raise ValueError("formal CPU source changed during execution")
    suites = ET.parse(xml).findall(".//testsuite")
    totals = {
        name: sum(int(row.attrib[name]) for row in suites)
        for name in ("tests", "errors", "failures", "skipped")
    }
    current, retained = case_ids(xml), case_ids(prior_report)
    with manifest_path.open("x") as stream:
        json.dump(before, stream, indent=2, allow_nan=False)
        stream.write("\n")
    evidence = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Full formal TokenCake configuration and native CPU cache/session/allocator; copy stand-ins.",
        "command": command, "returncode": result.returncode, "totals": totals,
        "unique_test_identities": len(current),
        "all_prior_742_identities_retained": retained <= current and len(retained) == 742,
        "retained_prior_identities": len(current & retained),
        "native_tree": tree, "native_engine": native, "sources_unchanged_during_execution": True,
        "source_manifest": {"path": str(manifest_path), "sha256": sha256_file(manifest_path)},
        "report": {"path": str(xml), "sha256": sha256_file(xml)},
        "prior_inputs_sha256": {
            str(path): sha256_file(path) for path in (prior_path, prior_manifest, prior_report)
        },
        "gpu_experiment_started": False, "performance_accepted": False,
    }
    with output.open("x") as stream:
        json.dump(evidence, stream, indent=2, allow_nan=False)
        stream.write("\n")
    if (
        result.returncode or any(totals[name] for name in ("errors", "failures", "skipped"))
        or len(current) != totals["tests"] or not evidence["all_prior_742_identities_retained"]
    ):
        raise ValueError("formal TokenCake CPU checks did not all pass")
    print(json.dumps({"tests": len(current), "source_files": len(before)}, indent=2))


if __name__ == "__main__":
    main()
