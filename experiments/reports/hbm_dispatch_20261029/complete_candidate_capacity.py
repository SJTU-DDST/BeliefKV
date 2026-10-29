"""Apply the unchanged complete capacity auditor to the finished dispatch arm."""

from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from experiments.reports.arrival_mean120_20261029.queue_pair import complete_work
from experiments.reports.layer_free_extent_20261029.capacity_and_tick_audit import audit_arm
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import run_causal_comparison as causal


def main():
    registration = ROOT.parent / "ops/hbm_dispatch_mean120_20261029_r2.registration.json"
    original = ROOT.parent / "ops/arrival_mean120_full_20261029_r1.registration.json"
    destination = Path(__file__).with_name("complete_candidate_capacity_r1.json")
    if destination.exists():
        raise FileExistsError("preserve the original complete capacity review")
    work, reference = complete_work(registration), complete_work(original)
    if work is None or reference is None or work != reference:
        raise ValueError("require identical complete work, fresh drain and actual owned exits")
    reader = SealedRecords()
    result = audit_arm(reader, "Dispatch FULL", registration)
    for path, seal in reader.prefixes.items():
        if Path(path).stat().st_size != seal["complete_prefix_bytes"]:
            raise ValueError("completed input is still changing")
    causal.reservation.write_json(destination, {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "arm": result,
        "input_jsonl_prefixes": reader.prefixes,
        "operator_sha256": sha256_file(Path(__file__)),
        "audit_implementation_sha256": sha256_file(
            ROOT / "experiments/reports/layer_free_extent_20261029/capacity_and_tick_audit.py"
        ),
        "performance_accepted": False,
        "semantics": (
            "Unchanged two-second-capped capacity audit of complete matched work. "
            "Physical residency is not useful active residency or GPU utilization. "
            "Releases, commands and verified sequences use different counting units; "
            "none is an exhaustive mandatory A-before-B census."
        ),
    })
    print({
        "middle_memory": result["memory"]["common_300_1800_s"],
        "raw_fit_waits": result["paused_admission"]["complete"],
        "new_D2H_commands": result["demand_aware_new_D2H_backups"]["commands"],
    })


if __name__ == "__main__":
    main()
