"""Reconcile staged native demand, handoff and Host hits on complete runs."""

import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_staged_native_restore import inspect as staged_inspect, service_evidence
from scripts import run_causal_comparison as causal


def reconcile(native, staged):
    transfers = native["transfers"]
    amounts = {
        "untagged_native_h2d_bytes": transfers["h2d_bytes"] - transfers["h2d_tagged_bytes"],
        "native_request_host_hit_bytes": native["native_request_host_hit_bytes"],
        "layer_handoff_consumer_bytes": native["handoff_consumer_h2d_bytes"],
        "staged_native_demand_bytes": staged["counts"].get("native_h2d_bytes", 0),
    }
    if any(type(value) is not int or value < 0 for value in amounts.values()):
        raise ValueError("native demand accounting requires exact nonnegative byte counts")
    legacy_gap = (
        amounts["untagged_native_h2d_bytes"]
        - amounts["native_request_host_hit_bytes"] - amounts["layer_handoff_consumer_bytes"]
    )
    if native["untagged_h2d_unexplained_after_request_hits_and_handoff_bytes"] != legacy_gap:
        raise ValueError("legacy native scope does not reconcile")
    handoff_keys = {
        (row["native_pair_submit_ts_ms"], tuple(row["published_consumer_anchor"]), row["consumer_bytes"])
        for row in native["handoffs"]
    }
    staged_keys = {
        (row["native_submit_ts_ms"], tuple(row["native_node_ids"]), row["native_h2d_bytes"])
        for row in staged["episodes"]
    }
    if handoff_keys & staged_keys:
        raise ValueError("native H2D cannot be credited as both staged demand and layer handoff")
    remainder = legacy_gap - amounts["staged_native_demand_bytes"]
    if remainder != 0:
        raise ValueError(f"complete native demand H2D has unexplained bytes: {remainder}")
    return {
        **amounts, "unexplained_native_h2d_bytes": remainder,
        "exact_physical_bytes_reconciled": True,
        "native_cache_hit_counters_changed": False,
    }


def audit(registration, label):
    helper = ROOT / "experiments/reports/join_blocked_layer_groups_20261028/handoff_ack_review.py"
    spec = importlib.util.spec_from_file_location("staged_demand_native_legacy_scope", helper)
    legacy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(legacy)
    native = legacy.inspect("Full native demand pipeline", registration, label)
    plan = causal.load(registration)
    arm = Path(plan["run_root"]) / label
    unit, remainder = divmod(
        plan["source_capacity"]["host_full_bytes"], plan["source_capacity"]["host_full_tokens"],
    )
    if remainder or unit <= 0:
        raise ValueError("staged demand requires exact observed native FULL byte geometry")
    reader = SealedRecords()
    journals = list(reader(arm / "opportunities/admission_opportunities.jsonl"))
    transfers = list(reader(arm / "server/transfer_telemetry.jsonl"))
    staged = staged_inspect(journals, transfers, bytes_per_token=unit)
    services = service_evidence(
        staged["episodes"], journals, reader(arm / "server/runtime_audit.jsonl"),
        receipts=reader(arm / "replay/native_requests.jsonl"),
    )
    balance = reconcile(native, staged)
    for path, seal in reader.prefixes.items():
        if (
            Path(path).stat().st_size != seal["complete_prefix_bytes"]
            or sha256_file(Path(path)) != seal["complete_prefix_sha256"]
        ):
            raise ValueError("complete demand/first-service input changed during audit")
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Complete exact native bytes and subsequent service; not hidden DMA or causal gains.",
        "registration": str(registration), "registration_sha256": sha256_file(registration),
        "native_legacy_accounting_scope": native, "staged_native_demand": staged,
        "staged_native_service": services, "byte_balance": balance,
        "input_jsonl_prefixes": reader.prefixes,
        "operators_sha256": {
            str(path): sha256_file(path) for path in (
                Path(__file__), helper, ROOT / "scripts/audit_staged_native_restore.py",
            )
        },
        "performance_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--label", default="full")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve earlier native demand evidence")
    result = audit(args.registration.resolve(), args.label)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(result["byte_balance"], indent=2))


if __name__ == "__main__":
    main()
