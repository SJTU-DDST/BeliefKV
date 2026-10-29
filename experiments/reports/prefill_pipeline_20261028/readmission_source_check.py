"""Bind the frozen ACK retry checkout to its existing complete CPU evidence."""

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
CHECKOUT = ROOT.parent / ".worktrees/beliefkv-ack-readmission-5cf3bb8b-20261028"
OUTPUT = HERE / "readmission_source_check.json"
sys.path.insert(0, str(CHECKOUT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file


def main():
    if OUTPUT.exists():
        raise FileExistsError("preserve earlier frozen-source evidence")
    evidence_path = HERE / "admission_retry_shared_r1_cpu.evidence.json"
    evidence = json.loads(evidence_path.read_bytes())
    manifest_path = Path(evidence["source_manifest"]["path"])
    report_path = Path(evidence["report"]["path"])
    inputs = {
        str(path): sha256_file(path)
        for path in (Path(__file__), evidence_path, manifest_path, report_path)
    }
    if (
        inputs[str(manifest_path)] != evidence["source_manifest"]["sha256"]
        or inputs[str(report_path)] != evidence["report"]["sha256"]
        or evidence["passing_unique_case_identities"] != 4102
        or evidence["retained_prior_unique_case_identities"] != 4100
        or evidence["new_unique_case_identities"] != 2
    ):
        raise ValueError("complete source-bound CPU evidence changed")
    suites = ET.parse(report_path).findall(".//testsuite")
    if (
        sum(int(row.attrib["tests"]) for row in suites) != 4108
        or any(int(row.attrib[name]) for row in suites for name in (
            "errors", "failures", "skipped",
        ))
    ):
        raise ValueError("complete CPU report has missing cases or failures")
    application = git_source_state(CHECKOUT)
    expected_commit = subprocess.check_output(
        ["git", "rev-parse", "5cf3bb8b"], cwd=ROOT, text=True,
    ).strip()
    if application["dirty"] or application["commit"] != expected_commit:
        raise ValueError("ACK retry checkout is not its exact clean frozen commit")
    manifest = json.loads(manifest_path.read_bytes())
    if len(manifest) != 1815:
        raise ValueError("CPU source manifest population changed")
    for relative, digest in manifest.items():
        if sha256_file(CHECKOUT / relative) != digest:
            raise ValueError("frozen checkout differs from CPU-tested source: " + relative)
    native_path = Path(evidence["environment"]["SGLANG_SOURCE_CHECKOUT"])
    native = git_source_state(native_path)
    tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=native_path, text=True,
    ).strip()
    if (
        native != evidence["native_engine"] or native["dirty"]
        or tree != "729aa0caa7a1ffac84463f46b3d1843d1a6a22e4"
        or sha256_file(native_path / "python/sglang/srt/managers/scheduler.py")
        != evidence["native_scheduler_sha256"]
    ):
        raise ValueError("native source differs from the complete CPU execution")
    if any(sha256_file(Path(path)) != digest for path, digest in inputs.items()):
        raise ValueError("source-check inputs changed while reading")
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Clean frozen ACK retry source equals all 1815 CPU-manifest files.",
        "checkout": str(CHECKOUT), "application": application,
        "native_engine": native, "native_tree": tree,
        "matched_cpu_manifest_files": len(manifest),
        "existing_passing_unique_cpu_cases": 4102, "cpu_tests_rerun": False,
        "R2_frozen_source_modified": False,
        "gpu_job_started": False, "gpu_correctness_verified": False,
        "performance_accepted": False, "inputs_sha256": inputs,
        "limitations": [
            "Exact source reuse does not provide new GPU or model qualification.",
            "The running R2 excludes this repair and cannot validate its performance.",
            "Fresh component/model/position evidence is required before a repaired Full run.",
        ],
    }
    with OUTPUT.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        key: result[key] for key in (
            "checkout", "matched_cpu_manifest_files", "cpu_tests_rerun",
            "gpu_job_started", "performance_accepted",
        )
    }, indent=2))


if __name__ == "__main__":
    main()
