"""Retain both Full and causal-control CPU identities on the new native branch."""

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

from beliefkv.experiments.source_provenance import git_source_state, sha256_file


def write(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def identities(path):
    cases = ET.parse(path).findall(".//testcase")
    bad = [row.attrib for row in cases if any(row.find(tag) is not None for tag in (
        "failure", "error", "skipped",
    ))]
    return {(row.attrib["classname"], row.attrib["name"]) for row in cases}, bad, len(cases)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--engine", required=True, type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    report, manifest, evidence = (
        args.output_dir / name for name in ("bound_cpu.xml", "sources.json", "evidence.json")
    )
    paths = (
        ROOT / "experiments/reports/prefill_arrival_20261029/complete_cpu.evidence.json",
        ROOT / "experiments/reports/warm_start_thunder_20261029/cpu_r1/bound_cpu.json",
    )
    prior, inputs, source_names, tests, retained = [], {}, set(), set(), set()
    for path in paths:
        item = json.loads(path.read_bytes())
        inputs[str(path)] = sha256_file(path)
        sources = Path(item["source_manifest"]["path"])
        previous_report = Path(item["report"]["path"])
        if (
            sha256_file(sources) != item["source_manifest"]["sha256"]
            or sha256_file(previous_report) != item["report"]["sha256"]
        ):
            raise ValueError("retained CPU evidence changed")
        old, bad, _ = identities(previous_report)
        if bad:
            raise ValueError("retained CPU evidence is incomplete")
        retained |= old
        source_names |= set(json.loads(sources.read_bytes()))
        tests |= {name for name in item["command"] if name.startswith("tests/") and name.endswith(".py")}
        prior.append(item)
    source_names |= {
        str(Path(__file__).relative_to(ROOT)),
        "tests/test_layer_handoff_free_extent.py",
    }
    tests.add("tests/test_layer_handoff_free_extent.py")
    source_names |= tests
    native_tests = [
        str(args.engine / "test/srt" / name) for name in (
            "test_beliefkv_layer_handoff.py", "test_beliefkv_layer_groups.py",
        )
    ]
    before = {name: sha256_file(ROOT / name) for name in sorted(source_names)}
    native = git_source_state(args.engine)
    env = {
        **prior[0]["environment"], "CUDA_VISIBLE_DEVICES": "99999",
        "PYTHONHASHSEED": "21", "OMP_NUM_THREADS": "1",
        "SGLANG_SOURCE_CHECKOUT": str(args.engine), "PYTHONPATH": str(args.engine / "python"),
    }
    command = [
        sys.executable, "-m", "pytest", "-q", *sorted(tests), *native_tests,
        "--tb=short", "--junitxml=" + str(report),
    ]
    completed = subprocess.run(command, cwd=ROOT, env={**os.environ, **env})
    actual, bad, records = identities(report)
    unchanged = (
        before == {name: sha256_file(ROOT / name) for name in before}
        and native == git_source_state(args.engine)
        and all(sha256_file(Path(path)) == digest for path, digest in inputs.items())
    )
    write(manifest, before)
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "command": command, "environment": env, "returncode": completed.returncode,
        "unique_test_identities": len(actual), "testcase_records": records,
        "retained_prior_union": len(retained),
        "all_retained_literal_identities_present": retained <= actual,
        "missing_identities": sorted(retained - actual), "failed_error_skipped_cases": bad,
        "sources_unchanged_during_execution": unchanged, "native_source": native,
        "source_manifest": {"path": str(manifest), "sha256": sha256_file(manifest)},
        "report": {"path": str(report), "sha256": sha256_file(report)},
        "prior_evidence_sha256": inputs,
        "scope": "CPU native allocator/session/lock ownership with copy/event stand-ins.",
        "GPU_qualification_pending": True, "performance_accepted": False,
        "running_baseline_sources_changed": False,
    }
    write(evidence, result)
    if completed.returncode or bad or not retained <= actual or not unchanged:
        raise RuntimeError("CPU qualification failed; preserve its exact report")
    print(json.dumps({
        "passing_identities": len(actual), "retained_prior_union": len(retained),
        "source_files": len(before), "GPU_qualification_pending": True,
    }))


if __name__ == "__main__":
    main()
