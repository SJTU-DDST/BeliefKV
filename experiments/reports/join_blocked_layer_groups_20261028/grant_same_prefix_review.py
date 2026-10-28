"""Recheck the original sealed grant prefix after the submitted-boundary audit fix."""

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_reference_join_grants import inspect
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.run_causal_comparison import reservation


def statuses(programs):
    return dict(Counter(
        row["status"] for row in programs
        if row["grant_event"] == "agent_join_blocked_capacity_admitted"
    ))


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError(output)
    reports = Path(__file__).parent
    original_path = reports / "grants_prefix_r1.json"
    original = json.loads(original_path.read_bytes())
    reader = SealedRecords()
    reader.prefixes = original["input_jsonl_prefixes"].copy()
    arm = Path(original["arm"])
    revised = inspect(
        reader(arm / "opportunities/admission_opportunities.jsonl"),
        reader(arm / "replay/runtime_events.replay.jsonl"),
        reader(arm / "server/runtime_events.sglang.jsonl"),
        reader(arm / "replay/native_requests.jsonl"),
        reader(arm / "server/runtime_audit.jsonl"),
    )
    if reader.prefixes != original["input_jsonl_prefixes"]:
        raise ValueError("audit changed the previously sealed observation prefix")
    changed = []
    for before, after in zip(original["programs"], revised["programs"], strict=True):
        if before["grant_number"] != after["grant_number"] or before["key"] != after["key"]:
            raise ValueError("audit changed grant identity or episode ordering")
        if before["status"] != after["status"]:
            if not (
                before["status"] == "native_service_identity_unproven"
                and after["status"] == "service_after_grant_verified"
                and after["grant_targets_submitted_join_successor"]
            ):
                raise ValueError("an unrelated association changed")
            changed.append({
                "grant_number": after["grant_number"],
                "request_id": after["service_request_id"],
                "context_epoch": after["key"]["context_epoch"],
                "status_before": before["status"], "status_after": after["status"],
                "grant_to_next_native_service_ms": after["grant_to_next_native_service_ms"],
            })
    report = reports / "grant_submitted_targeted_cpu.xml"
    xml = ET.parse(report).getroot()
    if any(list(xml.iter(tag)) for tag in ("failure", "error", "skipped")):
        raise ValueError("audit CPU regression is not clean")
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Same sealed live prefix, audit-only correction; running runtime unchanged.",
        "original_report": str(original_path),
        "original_report_sha256": sha256_file(original_path),
        "blocked_join_statuses_before": statuses(original["programs"]),
        "blocked_join_statuses_after": statuses(revised["programs"]),
        "changed_associations": changed,
        "passing_audit_cpu_cases": len(list(xml.iter("testcase"))),
        "audit_cpu_report_sha256": sha256_file(report),
        "input_jsonl_prefixes": reader.prefixes,
        "sources_sha256": {
            name: sha256_file(ROOT / name) for name in (
                "scripts/audit_reference_join_grants.py",
                "tests/test_reference_join_grant_audit.py",
            )
        },
        "operator_sha256": sha256_file(Path(__file__)),
        "complete_run_verified": False,
        "performance_accepted": False,
    }
    reservation.write_json(output, result)
    print(json.dumps({
        key: result[key] for key in (
            "blocked_join_statuses_before", "blocked_join_statuses_after",
            "passing_audit_cpu_cases",
        )
    }, indent=2))


if __name__ == "__main__":
    main()
