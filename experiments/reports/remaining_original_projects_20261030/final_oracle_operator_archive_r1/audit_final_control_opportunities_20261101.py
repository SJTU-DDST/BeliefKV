"""Seal complete final82 backup routes and measured critical-path exposure."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path("/home/longhao/experiment/BeliefKV")
OPS = ROOT.parent / "ops"
REPORTS = ROOT / "experiments/reports/remaining_original_projects_20261030"
OUTPUT = REPORTS / "final82_complete_control_opportunities_r1"
REGISTRATION = OPS / "workflow_final82_initial12_mean70_predictive_control_20261031_r1.registration.json"
COHORT = REPORTS / "final_oracle_original82_traffic_r1/analysis_cohort.json"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.train_workflow_work import write
from audit_measured_prefill_opportunity_20261031 import inspect as inspect_prefill
from audit_prepare_routes_handoff_20261031 import inspect as inspect_backups


def main():
    if (
        OUTPUT.exists()
        or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve prior evidence and use nice19 hidden-CUDA analysis")
    inputs = (
        REGISTRATION, COHORT, Path(__file__),
        OPS / "audit_measured_prefill_opportunity_20261031.py",
        OPS / "audit_prepare_routes_handoff_20261031.py",
        OPS / "audit_final82_live_tail_20261031.py",
    )
    pins = {str(path): sha256_file(path) for path in inputs}
    backups = inspect_backups(REGISTRATION, COHORT, complete=True)
    write(OUTPUT / "backup_routes.json", backups)
    print(json.dumps({
        "complete": True, "backup_routes": backups["route_summary"],
        "last_runtime_counts": backups["last_runtime_counts"],
        "last_agent_counts": backups["last_agent_counts"],
    }), flush=True)
    prefill = inspect_prefill(REGISTRATION, COHORT)
    write(OUTPUT / "critical_path.json", prefill)
    if any(sha256_file(Path(filename)) != digest for filename, digest in pins.items()):
        raise ValueError("analysis inputs changed")
    write(OUTPUT / "audit.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "complete_measured_work_and_actual_exit_verified": True,
        "inputs_sha256": pins,
        "outputs_sha256": {
            str(OUTPUT / name): sha256_file(OUTPUT / name)
            for name in ("backup_routes.json", "critical_path.json")
        },
        "scope": [
            "Final complete control replaces no independently sealed earlier live prefix.",
            "Backup paths can coalesce native copies; their stream sums must not be added.",
            "Critical-path exposure is not removable JCT or proof of physical readiness.",
            "No serving code, workload, running experiment or GPU allocation is changed.",
        ],
        "GPU_action": False, "performance_accepted": False,
    })
    print(json.dumps(prefill["workflow_equal_weight_mean_s"]), flush=True)


if __name__ == "__main__":
    main()
