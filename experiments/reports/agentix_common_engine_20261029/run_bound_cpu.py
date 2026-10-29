"""Retain both Agentix and causal-control cases on the reviewed common engine."""

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

from beliefkv.experiments.agentix_engine import verify_native_hooks
from beliefkv.experiments.native_engine_variant import AGENTIX_TREE
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from experiments.reports.kvflow_radix_session_20261028.run_bound_cpu import source_manifest
from experiments.reports.tokencake_radix_session_20261028.run_bound_cpu import case_ids


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--native-patch", required=True, type=Path)
    parser.add_argument("--prior", required=True, action="append", type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output, report, manifest = (
        args.output_dir / ("common_engine_cpu" + suffix)
        for suffix in (".json", ".xml", ".sources.json")
    )
    if any(path.exists() for path in (output, report, manifest)):
        raise FileExistsError("preserve every common-engine qualification attempt")
    prior = {str(path.resolve()): case_ids(path) for path in args.prior}
    previous = set().union(*prior.values())
    if len(previous) < 782:
        raise ValueError("common-engine regression must retain the complete Agentix case population")
    tests = {f"tests/{module.split('.')[1]}.py" for module, _ in previous}
    tests.update(
        "tests/" + path.name for pattern in ("test_agentix*.py", "test_native_engine_variant.py")
        for path in (ROOT / "tests").glob(pattern)
    )
    native_path = Path(os.environ["SGLANG_SOURCE_CHECKOUT"])
    native = git_source_state(native_path, canonical_patch=args.native_patch)
    if native["canonical_patch"]["actual_tree"] != AGENTIX_TREE:
        raise ValueError("CPU port verification requires the reviewed common Agentix tree")
    hooks = verify_native_hooks(native_path)

    def snapshot():
        names = source_manifest().keys() | {str(Path(__file__).relative_to(ROOT))}
        return {name: sha256_file(ROOT / name) for name in sorted(names)}

    before = snapshot()
    command = [
        sys.executable, "-m", "pytest", "-q", *sorted(tests), "--tb=short",
        "--junitxml=" + str(report),
    ]
    result = subprocess.run(
        command, cwd=ROOT, check=False,
        env=dict(os.environ, CUDA_VISIBLE_DEVICES="99999", PYTHONHASHSEED="21"),
    )
    if before != snapshot() or native != git_source_state(native_path, canonical_patch=args.native_patch):
        raise ValueError("application/native source changed during common-engine verification")
    totals = {
        name: sum(int(row.attrib[name]) for row in ET.parse(report).findall(".//testsuite"))
        for name in ("tests", "errors", "failures", "skipped")
    }
    current = case_ids(report)
    evidence = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Exact common-engine projection and real native CPU lifecycles; copy/executor stand-ins.",
        "command": command, "returncode": result.returncode, "totals": totals,
        "unique_test_identities": len(current), "native_engine": native,
        "native_optional_hooks": hooks,
        "all_prior_752_identities_retained": previous <= current,
        "all_prior_identities_retained": previous <= current,
        "prior_populations": {
            path: {"cases": len(cases), "retained": len(cases & current)}
            for path, cases in prior.items()
        },
        "sources_unchanged_during_execution": True,
        "inputs_sha256": {
            str(path.resolve()): sha256_file(path) for path in (*args.prior, args.native_patch)
        },
        "GPU_experiment_started": False, "formal_agentix_enabled": False,
        "performance_accepted": False,
    }
    with manifest.open("x") as stream:
        json.dump(before, stream, indent=2, allow_nan=False)
        stream.write("\n")
    evidence.update(
        source_manifest={"path": str(manifest.resolve()), "sha256": sha256_file(manifest)},
        report={"path": str(report.resolve()), "sha256": sha256_file(report)},
    )
    with output.open("x") as stream:
        json.dump(evidence, stream, indent=2, allow_nan=False)
        stream.write("\n")
    if (
        result.returncode or any(totals[name] for name in ("errors", "failures", "skipped"))
        or len(current) != totals["tests"] or not previous <= current
    ):
        raise ValueError("complete Agentix/common-control CPU regression failed")
    print(json.dumps({
        "cases": len(current), "retained_union": len(previous), "source_files": len(before),
        "native_tree": AGENTIX_TREE,
    }, indent=2))


if __name__ == "__main__":
    main()
