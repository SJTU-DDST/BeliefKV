"""Compare exact measured old-prefix work and descriptive complete-run IO."""

from collections import defaultdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path("/home/longhao/experiment/.worktrees/beliefkv-final-followup-a013dbd7")
MAIN = Path("/home/longhao/experiment/BeliefKV")
OPS = MAIN.parent / "ops"
REPORTS = MAIN / "experiments/reports/remaining_original_projects_20261030"
COHORT = REPORTS / "sustained56_initial12_mean70_r1/analysis_cohort.json"
HERE = REPORTS / "final82_oracle_and_selected_native_r1/native_prefix_io"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts.audit_previous_prefix_prefill import inspect as inspect_prefix
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution


def main():
    if HERE.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve IO evidence and use hidden-CUDA nice19")
    definitions = {
        "predictive": OPS / "workflow_original56_initial12_mean70_toolaware_control_20261031_r1.registration.json",
        "native": OPS / "workflow_final_selected_original56_nonranking_native_20261031_r1.registration.json",
    }
    measured = set(causal.load(COHORT)["measured_workflow_ids"])
    outputs, work = {}, []
    for label, registration in definitions.items():
        execution = verify_execution(registration, COHORT)
        work.append(execution["work"])
        plan = causal.load(registration)
        arm = Path(plan["run_root"]) / plan["order"][0]
        reader = SealedRecords()
        prefix = inspect_prefix(
            (row for row in reader(arm / "server/eviction_attribution.jsonl")
             if row.get("workflow_id") in measured),
            reader(arm / "server/runtime_audit.jsonl"),
        )
        totals = defaultdict(lambda: {
            "records": 0, "actual_bytes": 0, "stream_elapsed_ms": 0., "submit_to_ACK_ms": 0.,
        })
        identities = set()
        for row in reader(arm / "server/transfer_telemetry.jsonl"):
            key = row["command_id"], row["direction"]
            if key in identities or row["status"] != "completed":
                raise ValueError("duplicate or incomplete native transfer")
            identities.add(key)
            values = row["actual_bytes"], row["transfer_stream_elapsed_ms"], row["submit_to_ack_ms"]
            if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0 for value in values):
                raise ValueError("invalid native transfer service/byte evidence")
            item = totals[row["direction"]]
            item["records"] += 1
            item["actual_bytes"] += row["actual_bytes"]
            item["stream_elapsed_ms"] += row["transfer_stream_elapsed_ms"]
            item["submit_to_ACK_ms"] += row["submit_to_ack_ms"]
        for filename, bound in reader.prefixes.items():
            if Path(filename).stat().st_size != bound["complete_prefix_bytes"] or (
                sha256_file(Path(filename)) != bound["complete_prefix_sha256"]
            ):
                raise ValueError("completed prefix/IO evidence changed")
        output = {
            "registration": str(registration), "complete_measured_execution_verified": True,
            "measured_requests": execution["measured_requests"],
            "measured_output_tokens": execution["measured_output_tokens"],
            "fixed_measured_previous_prefix_prefill": prefix,
            "complete_execution_descriptive_transfer_totals": dict(totals),
            "input_jsonl_prefixes": reader.prefixes,
            "inputs_sha256": {str(path): sha256_file(path) for path in (
                registration, COHORT, Path(execution["certificate"]), Path(__file__),
                ROOT / "scripts/audit_previous_prefix_prefill.py",
            )},
            "scope": [
                "Old-prefix analysis uses the same fully completed measured workflows.",
                "Actual ranges are unioned per exact request/attempt/session; unknown identity remains unknown.",
                "Whole-run copy totals include different censored background progress and are descriptive.",
                "Native transfer ACK service sums are neither exposed GPU stalls nor avoidable JCT.",
                "Affected whole worker batches are not allocated to tokens or treated as pure GPU time.",
                "No source workload, timing, serving code or GPU allocation changes.",
            ],
            "GPU_action": False, "performance_accepted": False,
        }
        write(HERE / (label + ".json"), output)
        outputs[label] = sha256_file(HERE / (label + ".json"))
        print(json.dumps({
            "label": label, "measured_prefix_counts": prefix["counts"],
            "descriptive_whole_execution_IO": dict(totals),
        }), flush=True)
    if work[0] != work[1]:
        raise ValueError("complete measured native work differs")
    write(HERE / "audit.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "complete_measured_work_equal": True, "outputs_sha256": outputs,
        "operator_sha256": sha256_file(Path(__file__)),
        "GPU_action": False, "performance_accepted": False,
    })


if __name__ == "__main__":
    main()
