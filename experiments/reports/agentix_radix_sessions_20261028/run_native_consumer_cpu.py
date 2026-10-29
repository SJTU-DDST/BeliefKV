"""Source-bind actual native prefill consumers and finite-session pressure."""

from datetime import datetime, timezone
import argparse
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=HERE)
    parser.add_argument("--prior", type=Path, default=HERE / "bound_cpu.xml")
    parser.add_argument("--native-patch", type=Path, default=ROOT.parent / "ops/sglang-agentix-runtime-20261025.patch")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output, xml, manifest = (args.output_dir / ("native_consumer_cpu" + suffix) for suffix in (
        ".json", ".xml", ".sources.json",
    ))
    if any(path.exists() for path in (output, xml, manifest)):
        raise FileExistsError("preserve earlier actual-native-consumer CPU evidence")
    prior_path = args.prior
    previous = case_ids(prior_path)
    if len(previous) != 752:
        raise ValueError("prior native-session CPU population changed")
    native_path = Path(os.environ["SGLANG_SOURCE_CHECKOUT"])
    patch = args.native_patch
    native = git_source_state(native_path, canonical_patch=patch)
    if (
        not native["canonical_patch"]["matches_worktree"]
        or native["canonical_patch"]["actual_tree"] != "c489dbb85265efce25622a52abfd698e9199688d"
    ):
        raise ValueError("actual native consumer requires the original Agentix engine")
    tests = {f"tests/{module.split('.')[1]}.py" for module, _ in previous}
    tests |= {str(path.relative_to(ROOT)) for path in (ROOT / "tests").glob("test_agentix_*.py")}

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
    if before != snapshot() or native != git_source_state(native_path, canonical_patch=patch):
        raise ValueError("application/native changed during CPU execution")
    totals = {
        name: sum(int(row.attrib[name]) for row in ET.parse(xml).findall(".//testsuite"))
        for name in ("tests", "errors", "failures", "skipped")
    }
    current = case_ids(xml)
    with manifest.open("x") as stream:
        json.dump(before, stream, indent=2, allow_nan=False)
        stream.write("\n")
    record = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Real native PrefillAdder/alloc_for_extend/session/tree/allocator; CPU copy/executor stand-ins.",
        "command": command, "returncode": result.returncode, "totals": totals,
        "unique_test_identities": len(current), "all_prior_752_identities_retained": previous <= current,
        "retained_prior_identities": len(previous & current),
        "native_engine": native, "sources_unchanged_during_execution": True,
        "source_manifest": {"path": str(manifest), "sha256": sha256_file(manifest)},
        "report": {"path": str(xml), "sha256": sha256_file(xml)},
        "inputs_sha256": {str(path): sha256_file(path) for path in (prior_path, patch)},
        "GPU_experiment_started": False, "formal_agentix_enabled": False,
        "performance_accepted": False,
    }
    with output.open("x") as stream:
        json.dump(record, stream, indent=2, allow_nan=False)
        stream.write("\n")
    if (
        result.returncode or any(totals[name] for name in ("errors", "failures", "skipped"))
        or len(current) != totals["tests"] or not record["all_prior_752_identities_retained"]
    ):
        raise ValueError("complete actual-native-consumer CPU regression failed")
    print(json.dumps({"tests": len(current), "retained": len(previous), "sources": len(before)}, indent=2))


if __name__ == "__main__":
    main()
