#!/usr/bin/env python3
"""Qualify Full composition with the exact reference ThunderAgent decay core."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.paper_baseline import ThunderAgentDecayAdapter
from beliefkv.runtime.reference_thunder_admission import ReferenceThunderAdmission
from scripts.qualify_fitting_agent_lending import passed_cases, write
from scripts.run_causal_comparison import environment as serving_environment, preflight_runtime


REPORTS = ROOT / "experiments/reports"
PRIOR = REPORTS / "v377b_fitting_lending_cpu_evidence_20261027.json"
CONFIG = ROOT / "configs/migration/predictive_reference_thunder_20261027.json"
REPORT = REPORTS / "v394_reference_thunder_shared_cpu_20261027.xml"
MANIFEST = REPORTS / "v395_reference_thunder_cpu_evidence_20261027.sources.json"
OUTPUT = REPORTS / "v395_reference_thunder_cpu_evidence_20261027.json"


def main():
    if any(path.exists() for path in (REPORT, MANIFEST, OUTPUT)):
        raise FileExistsError("preserve previous qualification outputs")
    prior = json.loads(PRIOR.read_bytes())
    configuration = json.loads(CONFIG.read_bytes())
    paths = set(json.loads(Path(prior["source_manifest"]["path"]).read_bytes()))
    paths.update((
        str(CONFIG.relative_to(ROOT)), str(Path(__file__).resolve().relative_to(ROOT)),
        "beliefkv/runtime/reference_thunder_admission.py",
        "tests/test_reference_thunder_admission.py",
        "beliefkv/runtime/paper_baseline.py", "beliefkv/policy/paper_baselines.py",
    ))
    sources = {name: sha256_file(ROOT / name) for name in sorted(paths)}
    write(MANIFEST, sources)
    command = [part for part in prior["command"] if not part.startswith("--junitxml=")]
    command.extend((
        "tests/test_reference_thunder_admission.py", f"--junitxml={REPORT.relative_to(ROOT)}",
    ))
    environment = {
        **os.environ, **prior["environment"],
        "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21",
    }
    subprocess.run(command, cwd=ROOT, env=environment, check=True)
    previous, current = passed_cases(Path(prior["report"]["path"])), passed_cases(REPORT)
    if not previous <= current:
        raise ValueError("shared CPU coverage lost previous case identities")
    inherited = ("attach", "tick", "completed", "aborted", "_refresh_streaming_progress")
    if any(
        getattr(ReferenceThunderAdmission, name) is not getattr(ThunderAgentDecayAdapter, name)
        for name in inherited
    ):
        raise ValueError("Full changed a reference scheduling or native lifecycle method")
    constructors = {}
    for label in ("full", "thunderagent", "thunderagent_acting_decay"):
        settings, _, _ = serving_environment(
            {"arms": {"full": configuration["common_environment"]}},
            label, ROOT, Path(environment["SGLANG_SOURCE_CHECKOUT"]),
            ROOT / "experiments/raw/cpu_reference_thunder_unallocated" / label,
            18658, ROOT / "experiments/raw/cpu_reference_thunder_unallocated/plugin",
            sampling_mode="position", max_running_requests=24,
        )
        expected = "thunderagent_reference" if label == "full" else "off"
        if settings["BELIEFKV_AGENT_ADMISSION"] != expected:
            raise ValueError("Full composition leaked into a paper control")
        constructors[label] = preflight_runtime(
            {**settings, "SGLANG_SOURCE_CHECKOUT": environment["SGLANG_SOURCE_CHECKOUT"]},
            configuration["common_environment"]["MODEL_PATH"],
        )
    if any(sha256_file(ROOT / name) != expected for name, expected in sources.items()):
        raise ValueError("qualified CPU sources changed while executing")
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "source_bound_cpu_full_with_reference_thunderagent_decay",
        "command": command,
        "environment": {key: environment[key] for key in prior["environment"]},
        "report": {"path": str(REPORT), "sha256": sha256_file(REPORT)},
        "source_manifest": {"path": str(MANIFEST), "sha256": sha256_file(MANIFEST)},
        "previous_report": prior["report"],
        "passing_cases_including_subtests": len(current),
        "retained_previous_cases_including_subtests": len(previous),
        "new_cases": len(current - previous),
        "reference_methods_inherited_without_override": list(inherited),
        "configuration": {"path": str(CONFIG), "sha256": sha256_file(CONFIG)},
        "constructors": constructors,
        "preserved_development_reports": [
            str(REPORTS / name) for name in (
                "v393_reference_thunder_admission_targeted_cpu_20261027.xml",
                "v393b_reference_thunder_admission_targeted_cpu_20261027.xml",
            )
        ],
        "development_failures": (
            "New test fixtures initially omitted the required event socket and one "
            "JOIN member's invocation_create; production scheduler was unchanged."
        ),
        "native_cpu_copy_event_and_host_index_standins": True,
        "gpu_execution_registered": False,
        "gpu_correctness_verified": False,
        "performance_accepted": False,
        "research_contribution_claimed": False,
    }
    write(OUTPUT, result)
    print(json.dumps({
        key: result[key] for key in (
            "passing_cases_including_subtests", "new_cases",
            "reference_methods_inherited_without_override",
        )
    }, indent=2))


if __name__ == "__main__":
    main()
