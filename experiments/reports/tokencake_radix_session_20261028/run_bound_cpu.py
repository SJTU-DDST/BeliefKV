"""Run all prior paper/causal checks plus the native TokenCake session cases."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
PRIOR = ROOT / "experiments/reports/kvflow_radix_session_20261028"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from experiments.reports.kvflow_radix_session_20261028.run_bound_cpu import TESTS, source_manifest


def case_ids(path):
    return {
        (case.attrib["classname"], case.attrib["name"])
        for case in ET.parse(path).findall(".//testcase")
    }


def main():
    output, xml, sources = (
        HERE / ("bound_cpu" + suffix) for suffix in (".json", ".xml", ".sources.json")
    )
    if any(path.exists() for path in (output, xml, sources)):
        raise FileExistsError("preserve prior session-qualification CPU executions")
    prior_path = PRIOR / "causal_entry_bound_cpu.json"
    prior = json.loads(prior_path.read_bytes())
    prior_sources, prior_report = (
        Path(prior[name]["path"]) for name in ("source_manifest", "report")
    )
    if (
        sha256_file(prior_sources) != prior["source_manifest"]["sha256"]
        or sha256_file(prior_report) != prior["report"]["sha256"]
        or prior["unique_test_identities"] != 726
    ):
        raise ValueError("original 726-case CPU evidence changed")
    required_sources = json.loads(prior_sources.read_bytes())
    native_path = Path(os.environ["SGLANG_SOURCE_CHECKOUT"])
    native = git_source_state(native_path)
    tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=native_path, text=True,
    ).strip()
    if native["dirty"] or tree != prior["native_tree"]:
        raise ValueError("TokenCake CPU checks require the same clean common native tree")

    def snapshot():
        names = required_sources.keys() | source_manifest().keys() | {
            str(Path(__file__).relative_to(ROOT)),
        }
        return {name: sha256_file(ROOT / name) for name in sorted(names)}

    before = snapshot()
    tests = list(dict.fromkeys((*TESTS,
        "test_causal_paper_qualification.py", "test_causal_comparison.py",
        "test_agentx_baseline_operator.py", "test_position_replay_calibration.py",
        "test_tokencake_native_sessions.py",
    )))
    command = [
        sys.executable, "-m", "pytest", "-q", *(f"tests/{name}" for name in tests),
        "--tb=short", "--junitxml=" + str(xml),
    ]
    result = subprocess.run(
        command, cwd=ROOT, check=False,
        env=dict(os.environ, CUDA_VISIBLE_DEVICES="99999", PYTHONHASHSEED="21"),
    )
    if before != snapshot() or native != git_source_state(native_path):
        raise ValueError("CPU source changed during the complete execution")
    suites = ET.parse(xml).findall(".//testsuite")
    totals = {
        field: sum(int(suite.attrib[field]) for suite in suites)
        for field in ("tests", "errors", "failures", "skipped")
    }
    current, retained = case_ids(xml), case_ids(prior_report)
    with sources.open("x") as stream:
        json.dump(before, stream, indent=2, allow_nan=False)
        stream.write("\n")
    evidence = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Real native CPU Req/PrefillAdder/session/radix/allocator; copy and event stand-ins.",
        "command": command, "returncode": result.returncode, "totals": totals,
        "unique_test_identities": len(current), "retained_prior_identities": len(current & retained),
        "new_test_identities": len(current - retained),
        "all_prior_726_identities_retained": retained <= current and len(retained) == 726,
        "native_engine": native, "native_tree": tree, "sources_unchanged_during_execution": True,
        "source_manifest": {"path": str(sources), "sha256": sha256_file(sources)},
        "report": {"path": str(xml), "sha256": sha256_file(xml)},
        "prior_inputs_sha256": {
            str(path): sha256_file(path) for path in (prior_path, prior_sources, prior_report)
        },
        "new_native_session_gpu_scenes_executed": False,
        "gpu_experiment_started": False, "performance_accepted": False,
    }
    with output.open("x") as stream:
        json.dump(evidence, stream, indent=2, allow_nan=False)
        stream.write("\n")
    if (
        result.returncode or any(totals[name] for name in ("errors", "failures", "skipped"))
        or len(current) != totals["tests"] or not evidence["all_prior_726_identities_retained"]
    ):
        raise ValueError("source-bound TokenCake CPU regression did not pass completely")
    print(json.dumps({
        "tests": len(current), "retained": len(retained), "source_files": len(before),
        "new_native_session_gpu_scenes_executed": False,
    }, indent=2))


if __name__ == "__main__":
    main()
