"""Retain the full preceding qualification and add the workflow recovery checks."""

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

ADDED = (
    "tests/test_workflow_size_resume.py",
    "tests/test_workflow_work.py",
    "tests/test_workflow_size_admission.py",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--prior-evidence", type=Path,
        default=ROOT / "experiments/reports/decay_tick2s_no_handoff_20261030/cpu_r1/evidence.json",
    )
    parser.add_argument("--additional-source", action="append", default=[])
    parser.add_argument("--additional-test", action="append", default=[])
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    previous = args.prior_evidence.resolve()
    prior_digest = sha256_file(previous)
    prior = json.loads(previous.read_bytes())
    retained, bad, _records = identities(Path(prior["report"]["path"]))
    if bad:
        raise ValueError("previous CPU qualification is incomplete")
    for key in ("report", "source_manifest"):
        if sha256_file(Path(prior[key]["path"])) != prior[key]["sha256"]:
            raise ValueError("previous CPU qualification changed")
    names = set(json.loads(Path(prior["source_manifest"]["path"]).read_bytes()))
    names.update(ADDED)
    names.update(args.additional_source)
    names.update(args.additional_test)
    names.update((
        "beliefkv/policy/workflow_size_resume.py",
        "beliefkv/predictor/workflow_work.py",
        "beliefkv/runtime/workflow_work_worker.py",
        "beliefkv/runtime/workflow_size_admission.py",
        "scripts/train_workflow_work.py",
        "scripts/benchmark_workflow_size_cpu.py",
        "scripts/prepare_workflow_size_candidate.py",
        "configs/migration/predictive_workflow_size_resume_20261029.json",
        str(Path(__file__).resolve().relative_to(ROOT)),
    ))
    before = {name: sha256_file(ROOT / name) for name in sorted(names)}
    engine = Path(prior["environment"]["SGLANG_SOURCE_CHECKOUT"])
    native = git_source_state(engine)
    report = args.output_dir.resolve() / "bound_cpu.xml"
    sources = args.output_dir.resolve() / "sources.json"
    command = [arg for arg in prior["command"] if not arg.startswith("--junitxml=")]
    command[0] = sys.executable
    command.extend(name for name in (*ADDED, *args.additional_test) if name not in command)
    command.append("--junitxml=" + str(report))
    env = {
        **os.environ, **prior["environment"], "CUDA_VISIBLE_DEVICES": "99999",
        "PYTHONHASHSEED": "21", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
    }
    completed = subprocess.run(command, cwd=ROOT, env=env)
    actual, bad, records = identities(report)
    unchanged = (
        before == {name: sha256_file(ROOT / name) for name in before}
        and native == git_source_state(engine) and sha256_file(previous) == prior_digest
    )
    write(sources, before)
    evidence = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "command": command, "environment": prior["environment"],
        "returncode": completed.returncode, "unique_test_identities": len(actual),
        "testcase_records": records, "retained_prior_union": len(retained),
        "all_retained_literal_identities_present": retained <= actual,
        "missing_identities": sorted(retained - actual), "failed_error_skipped_cases": bad,
        "sources_unchanged_during_execution": unchanged, "native_source": native,
        "report": {"path": str(report), "sha256": sha256_file(report)},
        "source_manifest": {"path": str(sources), "sha256": sha256_file(sources)},
        "prior_evidence_sha256": {str(previous): prior_digest},
        "GPU_experiment_started": False, "performance_accepted": False,
    }
    write(args.output_dir / "evidence.json", evidence)
    if completed.returncode or bad or not retained <= actual or not unchanged:
        raise RuntimeError("workflow recovery qualification failed; retain this attempt")
    print({"passing": len(actual), "retained": len(retained), "bound_sources": len(before)})


if __name__ == "__main__":
    main()
