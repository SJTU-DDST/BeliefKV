"""Run retained paper/causal cases plus the real TokenCake scheduler bindings."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
PRIOR = ROOT / "experiments/reports/tokencake_radix_session_20261028/formal_bound_cpu.json"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.native_engine_variant import TOKENCAKE_TREE, engine_patch
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from beliefkv.experiments.tokencake_engine import verify_native_hooks
from experiments.reports.kvflow_radix_session_20261028.run_bound_cpu import source_manifest
from experiments.reports.tokencake_radix_session_20261028.run_bound_cpu import case_ids


def main():
    output, xml, manifest = (HERE / ("bound_cpu" + suffix) for suffix in (
        ".json", ".xml", ".sources.json",
    ))
    if any(path.exists() for path in (output, xml, manifest)):
        raise FileExistsError("preserve previous native-hook CPU executions")
    prior = json.loads(PRIOR.read_bytes())
    paths = {name: Path(prior[name]["path"]) for name in ("report", "source_manifest")}
    if any(sha256_file(path) != prior[name]["sha256"] for name, path in paths.items()):
        raise ValueError("original 759-case formal CPU evidence changed")
    native_path = Path(os.environ["SGLANG_SOURCE_CHECKOUT"])
    patch = engine_patch(ROOT, TOKENCAKE_TREE)
    native = git_source_state(native_path)
    actual_tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=native_path, text=True,
    ).strip()
    if native["dirty"] or actual_tree != TOKENCAKE_TREE:
        raise ValueError("native-hook CPU requires the exact clean committed hook tree")
    bindings = verify_native_hooks(native_path)

    def snapshot():
        names = json.loads(paths["source_manifest"].read_bytes()).keys() | source_manifest().keys()
        names |= {str(Path(__file__).relative_to(ROOT))}
        return {name: sha256_file(ROOT / name) for name in sorted(names)}

    before = snapshot()
    tests = [name for name in prior["command"] if name.startswith("tests/")]
    tests.extend((
        "tests/test_tokencake_scheduler_hooks.py", "tests/test_prepare_ack_engine.py",
        "tests/test_native_engine_variant.py",
    ))
    command = [
        sys.executable, "-m", "pytest", "-q", *tests, "--tb=short", "--junitxml=" + str(xml),
    ]
    result = subprocess.run(
        command, cwd=ROOT, check=False,
        env=dict(os.environ, CUDA_VISIBLE_DEVICES="99999", PYTHONHASHSEED="21"),
    )
    if before != snapshot() or native != git_source_state(native_path):
        raise ValueError("native-hook sources changed during complete CPU regression")
    suites = ET.parse(xml).findall(".//testsuite")
    totals = {
        name: sum(int(row.attrib[name]) for row in suites)
        for name in ("tests", "errors", "failures", "skipped")
    }
    current, previous = case_ids(xml), case_ids(paths["report"])
    with manifest.open("x") as stream:
        json.dump(before, stream, indent=2, allow_nan=False)
        stream.write("\n")
    evidence = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Real native scheduler method, Req/PrefillAdder/cache/allocator with copy/event stand-ins.",
        "command": command, "returncode": result.returncode, "totals": totals,
        "unique_test_identities": len(current), "retained_prior_identities": len(current & previous),
        "all_prior_759_identities_retained": previous <= current and len(previous) == 759,
        "native_tree": TOKENCAKE_TREE, "native_engine": native, "native_scheduler_bindings": bindings,
        "sources_unchanged_during_execution": True,
        "source_manifest": {"path": str(manifest), "sha256": sha256_file(manifest)},
        "report": {"path": str(xml), "sha256": sha256_file(xml)},
        "inputs_sha256": {str(path): sha256_file(path) for path in (PRIOR, *paths.values(), patch)},
        "gpu_experiment_started": False, "performance_accepted": False,
    }
    with output.open("x") as stream:
        json.dump(evidence, stream, indent=2, allow_nan=False)
        stream.write("\n")
    if (
        result.returncode or any(totals[name] for name in ("errors", "failures", "skipped"))
        or len(current) != totals["tests"] or not evidence["all_prior_759_identities_retained"]
    ):
        raise ValueError("native-hook complete CPU regression did not pass")
    print(json.dumps({"tests": len(current), "sources": len(before)}, indent=2))


if __name__ == "__main__":
    main()
