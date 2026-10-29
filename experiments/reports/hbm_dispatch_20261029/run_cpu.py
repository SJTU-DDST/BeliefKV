"""Retain every qualified mean120 case while checking the dispatch extension."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from experiments.reports.layer_free_extent_20261029.run_bound_cpu import identities, write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    prior_path = ROOT / "experiments/reports/arrival_mean120_20261029/cpu_r1/evidence.json"
    prior_digest = sha256_file(prior_path)
    prior = json.loads(prior_path.read_bytes())
    retained, bad, _ = identities(Path(prior["report"]["path"]))
    if bad or any(sha256_file(Path(prior[key]["path"])) != prior[key]["sha256"]
                  for key in ("report", "source_manifest")):
        raise ValueError("incomplete or changed retained CPU evidence")
    names = set(json.loads(Path(prior["source_manifest"]["path"]).read_bytes()))
    names.update((
        "tests/test_reference_free_dispatch.py",
        "beliefkv/runtime/reference_free_continuation.py",
        "beliefkv/runtime/reference_thunder_admission.py",
        "beliefkv/runtime/predicted_tool_offload.py",
        "beliefkv/runtime/layer_handoff.py",
        "beliefkv/runtime/sglang_v0520_runtime.py",
        "beliefkv/experiments/internal_comparison.py",
        "scripts/run_causal_comparison.py",
        "experiments/reports/layer_free_extent_20261029/capacity_and_tick_audit.py",
        "experiments/reports/layer_free_extent_20261029/capacity_and_tick_audit_r1.json",
        "experiments/reports/layer_free_extent_20261029/capacity_and_tick_audit_r1_source.py",
        "experiments/reports/layer_free_extent_20261029/capacity_and_tick_audit_r2.json",
    ))
    names.update(str(path.relative_to(ROOT)) for path in HERE.iterdir() if path.suffix in (".py", ".json"))
    before = {name: sha256_file(ROOT / name) for name in sorted(names)}
    engine = Path(prior["environment"]["SGLANG_SOURCE_CHECKOUT"])
    native = git_source_state(engine)
    report, sources = args.output_dir / "bound_cpu.xml", args.output_dir / "sources.json"
    command = [arg for arg in prior["command"] if not arg.startswith("--junitxml=")]
    command[0] = sys.executable
    command.extend(("tests/test_reference_free_dispatch.py", "--junitxml=" + str(report)))
    env = {**os.environ, **prior["environment"], "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21"}
    completed = subprocess.run(command, cwd=ROOT, env=env)
    actual, bad, records = identities(report)
    unchanged = (
        before == {name: sha256_file(ROOT / name) for name in before}
        and native == git_source_state(engine) and sha256_file(prior_path) == prior_digest
    )
    write(sources, before)
    evidence = {
        "observed_at": datetime.now(timezone.utc).isoformat(), "command": command,
        "environment": prior["environment"], "returncode": completed.returncode,
        "unique_test_identities": len(actual), "testcase_records": records,
        "retained_prior_union": len(retained),
        "all_retained_literal_identities_present": retained <= actual,
        "missing_identities": sorted(retained - actual), "failed_error_skipped_cases": bad,
        "sources_unchanged_during_execution": unchanged, "native_source": native,
        "source_manifest": {"path": str(sources), "sha256": sha256_file(sources)},
        "report": {"path": str(report), "sha256": sha256_file(report)},
        "prior_evidence_sha256": {str(prior_path): prior_digest},
        "GPU_experiment_started": False, "performance_accepted": False,
    }
    write(args.output_dir / "evidence.json", evidence)
    if completed.returncode or bad or not retained <= actual or not unchanged:
        raise RuntimeError("dispatch qualification failed; preserve this attempt")
    print(json.dumps({"passing_identities": len(actual), "retained": len(retained), "sources": len(before)}))


if __name__ == "__main__":
    main()
