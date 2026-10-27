"""Summarize sealed R1 observations without treating them as final performance."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file


def main():
    directory = Path(__file__).parent
    output = directory / "collecting_review.json"
    if output.exists():
        raise FileExistsError("preserve collecting review")
    paths = {
        key: directory / name for key, name in {
            "request": "mobile_game_epoch38_eligibility.json",
            "control_request": "control_mobile_game_epoch38_eligibility.json",
            "tool_waits": "tool_eligibility_waits_collecting.json",
            "pipeline": "collecting_pipeline.json",
        }.items()
    }
    reports = {key: json.loads(path.read_bytes()) for key, path in paths.items()}
    request, control = reports["request"], reports["control_request"]
    for field in (
        "request_id", "workflow_id", "invocation_id", "context_id",
        "context_epoch", "prompt_tokens", "output_tokens",
    ):
        if request[field] != control[field]:
            raise ValueError("request evidence is not paired")
    pipeline = reports["pipeline"]
    if pipeline["scope"] != "sealed_collecting_prefix_not_complete_performance":
        raise ValueError("unexpected pipeline scope")
    totals = reports["tool_waits"]["summary"]
    denominator = totals["native_queue_to_service_ms"]
    changes, previous = [], None
    for row in request["eligibility_samples"]:
        current = row["active"], row["paused_pending"], row["marked_for_pause"]
        if current != previous:
            changes.append(row)
            previous = current
    test_path = directory / "collecting_eligibility_cpu.xml"
    suites = ET.parse(test_path).getroot().findall("testsuite")
    if not suites or any(int(suite.get(key, "0")) for suite in suites for key in (
        "failures", "errors", "skipped",
    )):
        raise ValueError("CPU validation was not complete and passing")
    source_names = (
        "scripts/audit_request_agent_eligibility.py",
        "scripts/audit_agent_budget_composition.py",
        "scripts/audit_agent_admission_wait.py",
        "scripts/audit_collecting_agent_pipeline.py",
        "scripts/audit_join_reentry_service.py",
        "scripts/audit_tool_reentry_service.py",
        "scripts/audit_sealed_wait_prefetch.py",
        "tests/test_request_agent_eligibility_audit.py",
        "tests/test_collecting_agent_pipeline_audit.py",
    )
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "sealed_collecting_diagnostic_not_complete_performance",
        "exact_completed_receipts": pipeline["exact_completed_receipts"],
        "expected_requests": pipeline["expected_requests"],
        "same_request": {key: request[key] for key in (
            "request_id", "prompt_tokens", "output_tokens", "context_epoch",
        )},
        "native_queue_to_first_launch_ms": {
            "r1_full": request["native_queue_to_first_launch_ms"],
            "complete_v345_control": control["native_queue_to_first_launch_ms"],
        },
        "r1_preceding_tool_round_ms": request["preceding_tool_round"]["tool_round_duration_ms"],
        "r1_request_sample_hold_partition": request["sample_hold_partition"],
        "r1_observed_identity_changes": changes,
        "long_tool_successor_waits": {
            **totals,
            "definition": "tool-successor requests with at least 1000ms native queue-to-service waiting; not long-duration tools",
            "inactive_sample_hold_fraction": totals["inactive_ms"] / denominator,
            "active_sample_hold_fraction": totals["active_ms"] / denominator,
            "unknown_fraction": totals["unknown_ms"] / denominator,
        },
        "passing_cpu_cases": sum(int(suite.get("tests")) for suite in suites),
        "cpu_report": {"path": str(test_path), "sha256": sha256_file(test_path)},
        "input_reports": {
            key: {"path": str(path), "sha256": sha256_file(path)}
            for key, path in paths.items()
        },
        "source_sha256": {
            name: sha256_file(ROOT / name) for name in source_names
        },
        "operator_sha256": sha256_file(Path(__file__)),
        "limitations": [
            "Every input uses its own sealed prefix; the populations and end times are not atomic.",
            "Holding an identity snapshot for at most1500ms is a convention, not an exact transition log.",
            "The reference-only aggregate budget has no proven blocked-owner byte partition.",
            "A large inactive fraction does not quantify removable JCT or attribute the entire delay to one policy choice.",
            "One paired request does not establish full-run superiority, regression or frequency.",
            "No progressive layer-capacity handoff is implemented or qualified by this evidence.",
        ],
        "complete_performance_accepted": False, "goal_complete": False,
    }
    with output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        key: result[key] for key in (
            "exact_completed_receipts", "expected_requests", "native_queue_to_first_launch_ms",
            "passing_cpu_cases", "complete_performance_accepted",
        )
    }, indent=2))


if __name__ == "__main__":
    main()
