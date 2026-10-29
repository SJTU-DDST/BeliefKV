"""Bind one merged paper-policy and causal-supervisor CPU execution."""

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
from experiments.reports.kvflow_radix_session_20261028.run_bound_cpu import TESTS, source_manifest


def identities(path):
    return {
        (case.attrib["classname"], case.attrib["name"])
        for case in ET.parse(path).findall(".//testcase")
    }


def main():
    output = HERE / "causal_entry_bound_cpu.json"
    xml = HERE / "causal_entry_bound_cpu.xml"
    sources = HERE / "causal_entry_bound_cpu.sources.json"
    if any(path.exists() for path in (output, xml, sources)):
        raise FileExistsError("preserve existing merged CPU evidence")
    native_path = Path(os.environ["SGLANG_SOURCE_CHECKOUT"])
    native = git_source_state(native_path)
    tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=native_path, text=True,
    ).strip()
    if native["dirty"] or tree != "11b84f7c31986782be3fbe699bae2574ef092389":
        raise ValueError("merged CPU entry requires the unchanged clean common native engine")
    before = source_manifest()
    before[str(Path(__file__).relative_to(ROOT))] = sha256_file(Path(__file__))
    tests = list(dict.fromkeys((*TESTS,
        "test_causal_paper_qualification.py", "test_causal_comparison.py",
        "test_agentx_baseline_operator.py", "test_position_replay_calibration.py",
    )))
    command = [
        sys.executable, "-m", "pytest", "-q", *(f"tests/{name}" for name in tests),
        "--tb=short", "--junitxml=" + str(xml),
    ]
    result = subprocess.run(
        command, cwd=ROOT,
        env=dict(os.environ, CUDA_VISIBLE_DEVICES="99999", PYTHONHASHSEED="21"),
        check=False,
    )
    after = source_manifest()
    after[str(Path(__file__).relative_to(ROOT))] = sha256_file(Path(__file__))
    if before != after or native != git_source_state(native_path):
        raise ValueError("source changed during merged CPU execution")
    suites = ET.parse(xml).findall(".//testsuite")
    totals = {
        field: sum(int(suite.attrib[field]) for suite in suites)
        for field in ("tests", "errors", "failures", "skipped")
    }
    cases, retained = identities(xml), identities(HERE / "bound_paper_cpu.xml")
    with sources.open("x") as stream:
        json.dump(before, stream, indent=2, allow_nan=False)
        stream.write("\n")
    evidence = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Real causal supervisor fail-stop and paper-policy CPU regression; native copies/events are stand-ins.",
        "command": command, "returncode": result.returncode, "totals": totals,
        "unique_test_identities": len(cases), "retained_prior_identities": len(retained & cases),
        "new_test_identities": len(cases - retained),
        "all_prior_379_identities_retained": retained <= cases and len(retained) == 379,
        "native_engine": native, "native_tree": tree,
        "sources_unchanged_during_execution": True,
        "source_manifest": {"path": str(sources), "sha256": sha256_file(sources)},
        "report": {"path": str(xml), "sha256": sha256_file(xml)},
        "gpu_experiment_started": False, "performance_accepted": False,
    }
    with output.open("x") as stream:
        json.dump(evidence, stream, indent=2, allow_nan=False)
        stream.write("\n")
    if (
        result.returncode or any(totals[field] for field in ("errors", "failures", "skipped"))
        or totals["tests"] != len(cases) or not evidence["all_prior_379_identities_retained"]
    ):
        raise ValueError("merged source-bound CPU regression did not pass completely")
    print(json.dumps({
        "tests": len(cases), "retained": len(retained), "source_files": len(before),
        "gpu_experiment_started": False,
    }, indent=2))


if __name__ == "__main__":
    main()
