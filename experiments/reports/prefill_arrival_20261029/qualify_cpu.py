"""Run the retained Full CPU suite and bind changed arrival/prefill sources."""

import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file

HERE = Path(__file__).resolve().parent
PREVIOUS = ROOT / "experiments/reports/prefill_pipeline_20261028/admission_retry_shared_r1_cpu.evidence.json"
ENGINE = ROOT.parent / ".worktrees/sglang-causal-729aa0caa7a1-5cf3bb8bf40d"
ADDED_TESTS = (
    "tests/test_causal_replay_arrivals.py",
    "tests/test_native_mixed_service_accounting.py",
    "tests/test_causal_decode_diagnostics.py", "tests/test_arrival_schedule.py",
)
ADDED_SOURCES = (
    "beliefkv/experiments/replay_arrivals.py", "beliefkv/experiments/arrival_schedule.py",
    "scripts/prepare_causal_arrivals.py", "scripts/audit_decode_batch_efficiency.py",
    "scripts/plot_causal_decode_diagnostics.py",
    "experiments/reports/prefill_arrival_20261029/qualify_cpu.py",
    "experiments/reports/prefill_arrival_20261029/prepare_candidates.py",
    "experiments/reports/prefill_arrival_20261029/audit_handoff.py",
)


def cases(path):
    tree = ET.parse(path)
    rows = tree.findall(".//testcase")
    failed = [row.attrib for row in rows if any(row.find(tag) is not None for tag in (
        "failure", "error", "skipped",
    ))]
    return {(row.attrib["classname"], row.attrib["name"]) for row in rows}, failed


def write(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def main():
    report, manifest, evidence = (
        HERE / "complete_cpu.xml", HERE / "complete_cpu.sources.json",
        HERE / "complete_cpu.evidence.json",
    )
    if any(path.exists() for path in (report, manifest, evidence)):
        raise FileExistsError("preserve previous CPU attempts")
    prior = json.loads(PREVIOUS.read_bytes())
    previous_sources = Path(prior["source_manifest"]["path"])
    if sha256_file(previous_sources) != prior["source_manifest"]["sha256"]:
        raise ValueError("retained CPU manifest changed")
    paths = set(json.loads(previous_sources.read_bytes())) | set(ADDED_TESTS) | set(ADDED_SOURCES)
    paths |= {str(path.relative_to(ROOT)) for path in HERE.glob("*.json") if (
        path.name.startswith("chunk") or path.name.startswith("poisson")
    )}
    before = {name: sha256_file(ROOT / name) for name in sorted(paths)}
    engine_before = git_source_state(ENGINE)
    command = [value for value in prior["command"] if not value.startswith("--junitxml=")]
    command.extend(name for name in ADDED_TESTS if name not in command)
    command.append("--junitxml=" + str(report))
    env = {
        **prior["environment"], "CUDA_VISIBLE_DEVICES": "99999",
        "SGLANG_SOURCE_CHECKOUT": str(ENGINE), "PYTHONPATH": str(ENGINE / "python"),
    }
    executed = subprocess.run(command, cwd=ROOT, env={**os.environ, **env})
    if executed.returncode:
        raise RuntimeError("CPU checks failed; preserve the actual report")
    actual, failed = cases(report)
    previous, previous_failed = cases(Path(prior["report"]["path"]))
    if failed or previous_failed or not previous <= actual:
        raise ValueError("CPU failures, skips or lost retained case identities")
    if before != {name: sha256_file(ROOT / name) for name in before} or engine_before != git_source_state(ENGINE):
        raise ValueError("application or native sources changed during qualification")
    write(manifest, before)
    write(evidence, {
        "command": command, "environment": env,
        "passing_cases": len(actual), "retained_prior_cases": len(previous),
        "all_prior_literal_case_ids_retained": True,
        "source_manifest": {"path": str(manifest), "sha256": sha256_file(manifest),
                            "files": len(before)},
        "report": {"path": str(report), "sha256": sha256_file(report)},
        "native_source": engine_before, "native_sources_unchanged": True,
        "previous_evidence": {"path": str(PREVIOUS), "sha256": sha256_file(PREVIOUS)},
        "gpu_validation_pending": True, "performance_accepted": False,
        "scope": "CPU native admission and copy/event stand-ins; no real DMA or model claim.",
    })
    print(json.dumps({"passing_cases": len(actual), "retained_prior_cases": len(previous),
                      "source_files": len(before)}))


if __name__ == "__main__":
    main()
