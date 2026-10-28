"""Run the declared CPU audits only after this exact Full has completed."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

DIRECTORY = Path(__file__).resolve().parent
ROOT = DIRECTORY.parents[2]
sys.path.insert(0, str(ROOT))

SPEC = importlib.util.spec_from_file_location(
    "complete_mirror_review", DIRECTORY / "complete_review.py",
)
REVIEW = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REVIEW)


def main():
    plans, arms = {}, {}
    for name, (registration, label, _figure_label) in REVIEW.REGISTRATIONS.items():
        plan, arm, _summary, _work = REVIEW.completed_arm(registration, label)
        plans[name], arms[name] = plan, arm
    if REVIEW.causal.load(
        REVIEW.OPS / "idle_host_mirror_recycle_20261027_r1.plots.state.json",
    )["status"] != "complete":
        raise ValueError("wait for the existing complete matched plotting process")
    full, control = arms["full"], arms["thunderagent_acting_decay"]
    paired = ["--arm", f"Full={full}", "--arm", f"ThunderAgent decay={control}"]
    registration = REVIEW.REGISTRATIONS["full"][0]
    reentries = DIRECTORY / "complete_tool_reentries.json"
    prefetch = DIRECTORY / "complete_prefetch.json.gz"
    case_report = (
        ROOT / "experiments/reports/reference_join_siblings_20261027/tool_closer_trigger_audit.json"
    )
    commands = [
        ("complete_restore_churn", "scripts/audit_agent_restore_churn.py", paired),
        ("complete_jct_paths", "scripts/audit_causal_jct_paths.py", paired),
        ("complete_sampled_restore_waits", "scripts/audit_restore_wait_cohorts.py", paired),
        ("complete_tool_reentries", "scripts/audit_tool_reentry_service.py", paired),
        ("complete_tool_admission_wait", "scripts/audit_agent_admission_wait.py", [
            "--arm", str(full), "--reentries", str(reentries), "--label", "Full",
            "--minimum-wait-ms", "1000",
        ]),
        ("complete_capacity_waits", "scripts/audit_reference_capacity_waits.py", [
            "--arm", str(full), "--reentries", str(reentries), "--label", "Full",
            "--minimum-wait-ms", "1000",
        ]),
        ("complete_prefetch", "scripts/audit_sealed_wait_prefetch.py", [
            "--arm", str(full), "--collection-commit", plans["full"]["code_commit"],
        ]),
        ("complete_grant_service", "scripts/audit_reference_join_grants.py", [
            "--arm", str(full), "--prefetch-report", str(prefetch),
        ]),
        ("complete_guard_cases", "scripts/audit_tool_eos_guard_cases.py", [
            "--registration", str(registration), "--case-report", str(case_report),
        ]),
        ("complete_eos_trigger_lifecycle", "scripts/audit_eos_trigger_lifecycle.py", [
            "--registration", str(registration),
            "--expected-receipts", str(control / "replay/native_requests.jsonl"),
        ]),
        ("complete_wait_offload", "scripts/audit_wait_offload.py", ["--arm", str(full)]),
        ("complete_mirror_lifecycle", str(DIRECTORY / "mirror_lifecycle.py"), []),
    ]
    outputs = {
        name: DIRECTORY / (name + (".json.gz" if name == "complete_prefetch" else ".json"))
        for name, _source, _arguments in commands
    }
    if any(path.exists() for path in outputs.values()):
        raise FileExistsError("preserve existing complete/partial audits before a new invocation")
    for name, source, arguments in commands:
        command = [sys.executable, str(ROOT / source), *arguments, "--output", str(outputs[name])]
        print(json.dumps({"audit": name, "command": command}), flush=True)
        subprocess.run(
            command, cwd=ROOT, check=True,
            env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21"},
        )
    REVIEW.main()


if __name__ == "__main__":
    main()
