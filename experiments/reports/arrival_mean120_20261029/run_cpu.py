"""Bind the unchanged full regression and the new arrival experiment operator."""

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
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    prior_path = ROOT / "experiments/reports/layer_free_extent_20261029/complete_r5/evidence.json"
    prior = json.loads(prior_path.read_bytes())
    prior_digest = sha256_file(prior_path)
    manifest_path = Path(prior["source_manifest"]["path"])
    report_path = Path(prior["report"]["path"])
    if (
        sha256_file(manifest_path) != prior["source_manifest"]["sha256"]
        or sha256_file(report_path) != prior["report"]["sha256"]
    ):
        raise ValueError("retained qualification changed")
    retained, bad, _ = identities(report_path)
    if bad:
        raise ValueError("retained test population is incomplete")
    paths = set(json.loads(manifest_path.read_bytes()))
    paths.update(str(path.relative_to(ROOT)) for path in HERE.iterdir() if path.suffix in (".py", ".json"))
    paths.add("tests/test_mean120_arrival_experiment.py")
    before = {name: sha256_file(ROOT / name) for name in sorted(paths)}
    engine = Path(prior["environment"]["SGLANG_SOURCE_CHECKOUT"])
    native = git_source_state(engine)
    command = list(prior["command"])
    command[0] = sys.executable
    command = [arg for arg in command if not arg.startswith("--junitxml=")]
    report = args.output_dir / "bound_cpu.xml"
    command.extend(("tests/test_mean120_arrival_experiment.py", "--junitxml=" + str(report)))
    env = {**os.environ, **prior["environment"], "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21"}
    completed = subprocess.run(command, cwd=ROOT, env=env)
    actual, bad, records = identities(report)
    unchanged = (
        before == {name: sha256_file(ROOT / name) for name in before}
        and native == git_source_state(engine)
        and sha256_file(prior_path) == prior_digest
    )
    sources = args.output_dir / "sources.json"
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
    if completed.returncode or bad or not unchanged or not retained <= actual:
        raise RuntimeError("arrival qualification failed; preserve this attempt")
    print(json.dumps({"passing_identities": len(actual), "retained": len(retained), "sources": len(before)}))


if __name__ == "__main__":
    main()
