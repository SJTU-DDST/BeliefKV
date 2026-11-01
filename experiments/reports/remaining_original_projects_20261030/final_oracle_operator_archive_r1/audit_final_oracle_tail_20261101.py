"""Bind the existing live-tail inspector to the final frozen Oracle arm."""

from datetime import datetime, timezone
import os
from pathlib import Path
import sys

ROOT = Path("/home/longhao/experiment/BeliefKV")
OPS = ROOT.parent / "ops"
REPORTS = ROOT / "experiments/reports/remaining_original_projects_20261030"
HERE = REPORTS / "final82_oracle_tail_progress_r1"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.train_workflow_work import write
import audit_final82_live_tail_20261031 as inspector


def main():
    if (
        HERE.exists()
        or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve the Oracle tail evidence and use CPU-only nice19")
    parameters = {
        "OUTPUT": HERE / "tail.json",
        "REGISTRATION": OPS / "workflow_final82_initial12_mean70_capacity_oracle_20261031_r1.registration.json",
        "COHORT": REPORTS / "final_oracle_original82_traffic_r1/analysis_cohort.json",
        "OBSERVER": OPS / "workflow_final82_initial12_mean70_capacity_oracle_measured_completion_20261031_r1.registration.state.json",
        "COORDINATOR": OPS / "final_oracle_native_20261031_r1.state.json",
    }
    pins = {
        str(path): sha256_file(path) for path in (
            Path(__file__), Path(inspector.__file__),
            parameters["REGISTRATION"], parameters["COHORT"],
        )
    }
    for name, value in parameters.items():
        setattr(inspector, name, value)
    inspector.main()
    if any(sha256_file(Path(filename)) != digest for filename, digest in pins.items()):
        raise ValueError("the tail inspector or fixed inputs changed")
    write(HERE / "invocation.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "reused_inspector": str(inspector.__file__),
        "explicit_inspector_parameters": {
            name: str(value) for name, value in parameters.items()
        },
        "inputs_sha256": pins,
        "output_sha256": sha256_file(parameters["OUTPUT"]),
        "scope": [
            "Only input/output parameters of the existing read-only inspector are rebound.",
            "The frozen serving code, workload, tool times and measured cohort stay unchanged.",
            "Independent live prefixes do not form an atomic snapshot or forecast complete JCT.",
        ],
        "GPU_action": False, "performance_accepted": False,
    })


if __name__ == "__main__":
    main()
