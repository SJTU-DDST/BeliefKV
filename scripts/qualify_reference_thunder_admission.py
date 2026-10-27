#!/usr/bin/env python3
"""Qualify reference-core Full and its explicit free-capacity JOIN extension."""

import argparse
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prior", type=Path, default=PRIOR)
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument("--sources", type=Path, default=MANIFEST)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--configuration", type=Path, default=CONFIG)
    args = parser.parse_args()
    if any(path.exists() for path in (args.report, args.sources, args.output)):
        raise FileExistsError("preserve previous qualification outputs")
    prior = json.loads(args.prior.read_bytes())
    configuration = json.loads(args.configuration.read_bytes())
    join_free = configuration["common_environment"].get("AGENT_JOIN_FREE_ADMISSION", "0") == "1"
    siblings = configuration["common_environment"].get("AGENT_COADMIT_JOIN_CHILDREN", "0") == "1"
    paths = set(json.loads(Path(prior["source_manifest"]["path"]).read_bytes()))
    paths.update((
        str(args.configuration.resolve().relative_to(ROOT)), str(Path(__file__).resolve().relative_to(ROOT)),
        "beliefkv/runtime/reference_thunder_admission.py",
        "tests/test_reference_thunder_admission.py",
        "beliefkv/runtime/paper_baseline.py", "beliefkv/policy/paper_baselines.py",
    ))
    extra_tests = (
        "tests/test_reference_join_free_admission.py",
        "tests/test_request_agent_eligibility_audit.py",
        "tests/test_collecting_agent_pipeline_audit.py",
        "tests/test_audit_tool_reentry_service.py",
    ) if join_free or siblings else ()
    if siblings:
        extra_tests += ("tests/test_reference_sibling_coadmission.py",)
    paths.update(extra_tests)
    if join_free or siblings:
        paths.update((
            "scripts/audit_request_agent_eligibility.py",
            "scripts/audit_collecting_agent_pipeline.py",
        ))
    sources = {name: sha256_file(ROOT / name) for name in sorted(paths)}
    write(args.sources, sources)
    command = [part for part in prior["command"] if not part.startswith("--junitxml=")]
    if "tests/test_reference_thunder_admission.py" not in command:
        command.append("tests/test_reference_thunder_admission.py")
    command.extend(name for name in extra_tests if name not in command)
    command.append(f"--junitxml={args.report.resolve().relative_to(ROOT)}")
    environment = {
        **os.environ, **prior["environment"],
        "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21",
    }
    subprocess.run(command, cwd=ROOT, env=environment, check=True)
    previous, current = passed_cases(Path(prior["report"]["path"])), passed_cases(args.report)
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
        if settings["BELIEFKV_AGENT_JOIN_FREE_ADMISSION"] != str(int(join_free and label == "full")):
            raise ValueError("free-capacity JOIN admission leaked into a paper control")
        if settings["BELIEFKV_AGENT_COADMIT_JOIN_CHILDREN"] != str(int(siblings and label == "full")):
            raise ValueError("sibling admission leaked into a paper control")
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
        "report": {"path": str(args.report.resolve()), "sha256": sha256_file(args.report)},
        "source_manifest": {"path": str(args.sources.resolve()), "sha256": sha256_file(args.sources)},
        "previous_report": prior["report"],
        "passing_cases_including_subtests": len(current),
        "retained_previous_cases_including_subtests": len(previous),
        "new_cases": len(current - previous),
        "reference_methods_inherited_without_override": list(inherited),
        "configuration": {
            "path": str(args.configuration.resolve()), "sha256": sha256_file(args.configuration),
        },
        "join_free_admission": join_free,
        "coadmit_join_children": siblings,
        "constructors": constructors,
        "preserved_development_reports": [
            str(REPORTS / name) for name in (
                "v393_reference_thunder_admission_targeted_cpu_20261027.xml",
                "v393b_reference_thunder_admission_targeted_cpu_20261027.xml",
                "v398_reference_thunder_offload_targeted_cpu_20261027.xml",
                "v398b_reference_thunder_offload_targeted_cpu_20261027.xml",
            )
        ],
        "development_failures": (
            "New test fixtures initially omitted the required event socket and one "
            "JOIN member's invocation_create; the native offload fixture initially "
            "retained legacy execution recovery and was aligned with the declared "
            "reference profile. Preserve the separate actual V396b integration failure."
        ),
        "gpu_development_failure": {
            "path": str(REPORTS / "v397_reference_thunder_offload_integration_failure_20261027.json"),
            "sha256": sha256_file(
                REPORTS / "v397_reference_thunder_offload_integration_failure_20261027.json",
            ),
        } if (REPORTS / "v397_reference_thunder_offload_integration_failure_20261027.json").exists() else None,
        "native_cpu_copy_event_and_host_index_standins": True,
        "gpu_execution_registered": False,
        "gpu_correctness_verified": False,
        "performance_accepted": False,
        "research_contribution_claimed": False,
    }
    write(args.output, result)
    print(json.dumps({
        key: result[key] for key in (
            "passing_cases_including_subtests", "new_cases",
            "reference_methods_inherited_without_override",
        )
    }, indent=2))


if __name__ == "__main__":
    main()
