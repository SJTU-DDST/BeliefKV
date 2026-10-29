"""Retain the complete causal-control CPU population and qualify warm arrivals."""

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

from beliefkv.experiments.native_engine_variant import COMMON_TREE
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from experiments.reports.kvflow_radix_session_20261028.run_bound_cpu import source_manifest
from experiments.reports.kvflow_radix_session_20261028.run_causal_entry_cpu import identities


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--prior", required=True, type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report, manifest_path, output = (
        args.output_dir / ("bound_cpu" + suffix) for suffix in (".xml", ".sources.json", ".json")
    )
    if any(path.exists() for path in (report, manifest_path, output)):
        raise FileExistsError("preserve every warm-start qualification attempt")
    previous = identities(args.prior)
    if len(previous) != 726:
        raise ValueError("previous common causal-control case population changed")
    engine = Path(os.environ["SGLANG_SOURCE_CHECKOUT"])
    native = git_source_state(
        engine, canonical_patch=ROOT / "patches/sglang-v0.5.20-beliefkv-staging.patch",
    )
    if native["canonical_patch"]["actual_tree"] != COMMON_TREE:
        raise ValueError("warm-start ThunderAgent requires the unchanged common engine")
    tests = {f"tests/{module.split('.')[1]}.py" for module, _ in previous}
    tests.update((
        "tests/test_arrival_schedule.py", "tests/test_causal_replay_arrivals.py",
        "tests/test_causal_replay_delivery.py", "tests/test_causal_sampling_benchmark.py",
        "tests/test_tool_timing_overlay.py",
    ))

    def snapshot():
        paths = source_manifest().keys() | {
            str(path.relative_to(ROOT)) for path in HERE.iterdir() if path.suffix in (".py", ".json")
        }
        return {name: sha256_file(ROOT / name) for name in sorted(paths)}

    before = snapshot()
    command = [
        sys.executable, "-m", "pytest", "-q", *sorted(tests),
        "--tb=short", "--junitxml=" + str(report),
    ]
    result = subprocess.run(
        command, cwd=ROOT, check=False,
        env=dict(os.environ, CUDA_VISIBLE_DEVICES="99999", PYTHONHASHSEED="21"),
    )
    if before != snapshot() or native != git_source_state(
        engine, canonical_patch=ROOT / "patches/sglang-v0.5.20-beliefkv-staging.patch",
    ):
        raise ValueError("application/native inputs changed during warm-start verification")
    totals = {
        name: sum(int(row.attrib[name]) for row in ET.parse(report).findall(".//testsuite"))
        for name in ("tests", "errors", "failures", "skipped")
    }
    actual = identities(report)
    with manifest_path.open("x") as stream:
        json.dump(before, stream, indent=2, allow_nan=False)
        stream.write("\n")
    evidence = {
        "observed_at": datetime.now(timezone.utc).isoformat(), "command": command,
        "returncode": result.returncode, "totals": totals, "unique_test_identities": len(actual),
        "all_prior_726_identities_retained": previous <= actual,
        "native_engine": native, "sources_unchanged_during_execution": True,
        "source_manifest": {"path": str(manifest_path.resolve()), "sha256": sha256_file(manifest_path)},
        "report": {"path": str(report.resolve()), "sha256": sha256_file(report)},
        "inputs_sha256": {str(args.prior.resolve()): sha256_file(args.prior)},
        "GPU_experiment_started": False, "performance_accepted": False,
    }
    with output.open("x") as stream:
        json.dump(evidence, stream, indent=2, allow_nan=False)
        stream.write("\n")
    if (
        result.returncode or any(totals[name] for name in ("errors", "failures", "skipped"))
        or not previous <= actual or len(actual) != totals["tests"]
    ):
        raise ValueError("complete warm-start causal/control CPU regression failed")
    print(json.dumps({"tests": len(actual), "retained": len(previous), "sources": len(before)}))


if __name__ == "__main__":
    main()
