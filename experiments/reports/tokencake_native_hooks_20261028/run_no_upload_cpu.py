"""Retain all native-hook cases while checking the empty-upload fast return."""

import argparse
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

from beliefkv.experiments.native_engine_variant import TOKENCAKE_TREE
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from beliefkv.experiments.tokencake_engine import verify_native_hooks
from experiments.reports.kvflow_radix_session_20261028.run_bound_cpu import source_manifest
from experiments.reports.tokencake_radix_session_20261028.run_bound_cpu import case_ids


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="no_ready_upload_cpu")
    args = parser.parse_args()
    if not args.name or Path(args.name).name != args.name:
        raise ValueError("CPU evidence name must be a single path component")
    output, xml, manifest = (HERE / (args.name + suffix) for suffix in (
        ".json", ".xml", ".sources.json",
    ))
    if any(path.exists() for path in (output, xml, manifest)):
        raise FileExistsError("preserve previous empty-upload CPU attempts")
    prior_path = HERE / "bound_cpu.json"
    prior = json.loads(prior_path.read_bytes())
    report = Path(prior["report"]["path"])
    if sha256_file(report) != prior["report"]["sha256"] or len(case_ids(report)) != 812:
        raise ValueError("original complete native-hook CPU evidence changed")
    native_path = Path(os.environ["SGLANG_SOURCE_CHECKOUT"])
    native = git_source_state(native_path)
    tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=native_path, text=True,
    ).strip()
    if native["dirty"] or tree != TOKENCAKE_TREE:
        raise ValueError("empty-upload CPU needs the clean exact native-hook engine")
    bindings = verify_native_hooks(native_path)

    def snapshot():
        names = source_manifest().keys() | {str(Path(__file__).relative_to(ROOT))}
        return {name: sha256_file(ROOT / name) for name in sorted(names)}

    before = snapshot()
    tests = [name for name in prior["command"] if name.startswith("tests/")]
    command = [
        sys.executable, "-m", "pytest", "-q", *tests,
        "--tb=short", "--junitxml=" + str(xml),
    ]
    execution = subprocess.run(
        command, cwd=ROOT, check=False,
        env=dict(os.environ, CUDA_VISIBLE_DEVICES="99999", PYTHONHASHSEED="21"),
    )
    if before != snapshot() or native != git_source_state(native_path):
        raise ValueError("empty-upload CPU sources changed during execution")
    totals = {
        name: sum(int(row.attrib[name]) for row in ET.parse(xml).findall(".//testsuite"))
        for name in ("tests", "errors", "failures", "skipped")
    }
    current, previous = case_ids(xml), case_ids(report)
    with manifest.open("x") as stream:
        json.dump(before, stream, indent=2, allow_nan=False)
        stream.write("\n")
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Real native scheduler/Req/cache/allocator; CPU copy/executor stand-ins.",
        "command": command, "returncode": execution.returncode, "totals": totals,
        "unique_test_identities": len(current), "all_prior_812_identities_retained": previous <= current,
        "retained_prior_identities": len(previous & current),
        "native_tree": tree, "native_engine": native, "native_scheduler_bindings": bindings,
        "sources_unchanged_during_execution": True,
        "source_manifest": {"path": str(manifest), "sha256": sha256_file(manifest)},
        "report": {"path": str(xml), "sha256": sha256_file(xml)},
        "inputs_sha256": {str(path): sha256_file(path) for path in (prior_path, report)},
        "current_running_tokencake_changed": False,
        "gpu_experiment_started": False, "performance_accepted": False,
    }
    with output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    if (
        execution.returncode or any(totals[name] for name in ("errors", "failures", "skipped"))
        or len(current) != totals["tests"] or not result["all_prior_812_identities_retained"]
    ):
        raise ValueError("complete empty-upload CPU regression failed")
    print(json.dumps({"tests": len(current), "retained": len(previous), "sources": len(before)}, indent=2))


if __name__ == "__main__":
    main()
