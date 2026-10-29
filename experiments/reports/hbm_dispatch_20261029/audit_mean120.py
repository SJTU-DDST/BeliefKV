"""Apply the existing complete capacity audit to the unchanged mean120 pair."""

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
    registrations = {
        "Original FULL": ROOT.parent / "ops/arrival_mean120_full_20261029_r1.registration.json",
        "ThunderAgent decay": ROOT.parent / "ops/arrival_mean120_thunder_20261029_r1.registration.json",
    }
    reader, arms, work = SealedRecords(), {}, {}
    for label, path in registrations.items():
        work[label] = complete_work(path)
        if work[label] is None:
            raise ValueError("require complete work, fresh drain and actual owned exit")
        arms[label] = audit_arm(reader, label, path)
    if work["Original FULL"] != work["ThunderAgent decay"]:
        raise ValueError("complete work differs")
    for path, seal in reader.prefixes.items():
        if Path(path).stat().st_size != seal["complete_prefix_bytes"]:
            raise ValueError("complete source is still changing")
    destination = Path(__file__).with_name("mean120_original_complete_audit_r1.json")
    causal.reservation.write_json(destination, {
        "observed_at": datetime.now(timezone.utc).isoformat(), "arms": arms,
        "input_jsonl_prefixes": reader.prefixes,
        "operator_sha256": sha256_file(Path(__file__)),
        "audit_implementation_sha256": sha256_file(
            ROOT / "experiments/reports/layer_free_extent_20261029/capacity_and_tick_audit.py"
        ),
        "performance_accepted": False,
        "semantics": "Existing two-second-capped complete capacity audit; unobserved intervals remain unknown. Node releases, backup commands and verified sequences have different units. No exhaustive mandatory-D2H, GPU utilization, tick causality or removable JCT claim.",
    })
    print({
        label: {
            "middle_memory": arm["memory"]["common_300_1800_s"],
            "raw_fit_waits": arm["paused_admission"]["complete"],
            "demand_backups": arm["demand_aware_new_D2H_backups"]["commands"],
        } for label, arm in arms.items()
    })


if __name__ == "__main__":
    main()
