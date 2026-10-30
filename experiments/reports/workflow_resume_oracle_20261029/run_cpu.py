"""Retain the shared CPU population and bind the explicit model/tool oracle."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from experiments.reports.layer_free_extent_20261029.run_bound_cpu import identities, write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--configuration", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    prior_path = ROOT / "experiments/reports/predicted_acting_pressure_20261029/cpu_r1/evidence.json"
    prior = json.loads(prior_path.read_bytes())
    retained, bad, _ = identities(Path(prior["report"]["path"]))
    if bad or any(sha256_file(Path(prior[key]["path"])) != prior[key]["sha256"]
                  for key in ("report", "source_manifest")):
        raise ValueError("prior complete shared CPU population changed")
    added_tests = ("tests/test_workflow_resume_oracle.py", "tests/test_workflow_work_oracle.py")
    names = set(json.loads(Path(prior["source_manifest"]["path"]).read_bytes()))
    names.update(added_tests)
    names.update((
        "beliefkv/runtime/workflow_work_oracle.py", "beliefkv/runtime/workflow_oracle_admission.py",
        "experiments/reports/workflow_resume_oracle_20261029/analyze.py",
        "experiments/reports/workflow_resume_oracle_20261029/prepare.py",
        "experiments/reports/workflow_resume_oracle_20261029/register.py",
        "experiments/reports/workflow_resume_oracle_20261029/qualify_progress.py",
        str(Path(__file__).relative_to(ROOT)),
    ))
    configuration = json.loads(args.configuration.read_bytes())
    names.add(str(args.configuration.resolve().relative_to(ROOT)))
    names.add(str(Path(configuration["common_environment"]["WORKFLOW_WORK_MODEL"]).relative_to(ROOT)))
    before = {name: sha256_file(ROOT / name) for name in sorted(names)}
    engine = Path(prior["environment"]["SGLANG_SOURCE_CHECKOUT"])
    native = git_source_state(engine)
    report, manifest = args.output_dir / "bound_cpu.xml", args.output_dir / "sources.json"
    command = [arg for arg in prior["command"] if not arg.startswith("--junitxml=")]
    command[0] = sys.executable
    command.extend(name for name in added_tests if name not in command)
    command.append("--junitxml=" + str(report.resolve()))
    env = {
        **os.environ, **prior["environment"], "CUDA_VISIBLE_DEVICES": "99999",
        "PYTHONHASHSEED": "21", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
    }
    result = subprocess.run(command, cwd=ROOT, env=env)
    actual, bad, records = identities(report)
    unchanged = before == {name: sha256_file(ROOT / name) for name in before} and native == git_source_state(engine)
    write(manifest, before)
    evidence = {
        "observed_at": datetime.now(timezone.utc).isoformat(), "command": command,
        "environment": prior["environment"], "returncode": result.returncode,
        "unique_test_identities": len(actual), "testcase_records": records,
        "retained_prior_union": len(retained), "all_retained_literal_identities_present": retained <= actual,
        "missing_identities": sorted(retained - actual), "failed_error_skipped_cases": bad,
        "sources_unchanged_during_execution": unchanged, "native_source": native,
        "source_manifest": {"path": str(manifest.resolve()), "sha256": sha256_file(manifest)},
        "report": {"path": str(report.resolve()), "sha256": sha256_file(report)},
        "prior_evidence_sha256": {str(prior_path): sha256_file(prior_path)},
        "GPU_experiment_started": False, "performance_accepted": False,
    }
    write(args.output_dir / "evidence.json", evidence)
    if result.returncode or bad or not retained <= actual or not unchanged:
        raise RuntimeError("preserve failed model/tool oracle CPU qualification")
    print({"passing_identities": len(actual), "retained": len(retained), "sources": len(before)})


if __name__ == "__main__":
    main()
