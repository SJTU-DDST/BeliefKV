"""Retain the complete six-project pilot and inspect its shape without GPU follow-up."""

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workload_mix import inventory_capture, population_summary
from experiments.reports.workflow_work_training_20261029.finish_training import (
    audit_finished_source, source_ready,
)
from scripts import run_causal_source_capture as capture
from scripts.audit_parallel_workstreams import audit
from scripts.train_workflow_work import write


def validate_population(rows, declaration):
    expected = {(row["instance_id"], row["project"]) for row in declaration["assignments"]}
    if len(rows) != 6 or {(row["task_id"], row["project"]) for row in rows} != expected:
        raise ValueError("pilot trace differs from all six predeclared projects")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--declaration", type=Path, required=True)
    parser.add_argument("--model-cost-evidence", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("pilot reviewer must remain CPU-only")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    inputs = (args.registration, args.declaration, args.model_cost_evidence, Path(__file__),
              ROOT / "scripts/audit_parallel_workstreams.py")
    binding = {str(path.resolve()): sha256_file(path) for path in inputs}
    state = {
        "status": "waiting_complete_native_pilot",
        "process": capture.reservation.process(os.getpid()),
        "inputs_sha256": binding, "GPU_followup_registered": False,
    }
    plan = capture.load(args.registration)
    try:
        started = time.monotonic()
        while True:
            if binding != {name: sha256_file(Path(name)) for name in binding}:
                raise ValueError("pilot reviewer inputs changed")
            state["checked_at"] = datetime.now(timezone.utc).isoformat()
            capture.reservation.write_json(args.output_dir / "state.json", state)
            source = capture.load(args.registration.with_suffix(".state.json"))
            if source_ready(plan, source, alive=lambda value: bool(value) and capture.reservation.alive(value)):
                break
            if time.monotonic() - started > 6 * 3600:
                raise TimeoutError("pilot reviewer exceeded its six-hour wait")
            time.sleep(10.)
        write(args.output_dir / "complete_source.json", audit_finished_source(plan, source))
        arm = Path(plan["run_root"]) / "native_capture"
        cost = capture.load(args.model_cost_evidence)["model_cost_calibration"]["model_seconds_per_work_token"]
        inventory = inventory_capture(arm, seconds_per_model_unit=cost)
        write(args.output_dir / "inventory.json", inventory)
        workstreams = audit(arm)
        write(args.output_dir / "workstreams.json", workstreams)
        declaration = capture.load(args.declaration)
        rows = inventory["workflows"]
        validate_population(rows, declaration)
        controls = [
            capture.load(path)["agent_control"] for path in arm.glob("client_*/workflows/*/result.json")
        ]
        write(args.output_dir / "review.json", {
            "inputs_sha256": binding, "complete_source_and_actual_exit": True,
            "population": population_summary(rows), "workstream_summary": workstreams["summary"],
            "all_workflows_naturally_eligible": all(row["eligible"] for row in rows),
            "root_terminal_delegation_deferrals": sum(
                row.get("root_delegation_return_deferrals", 0) for row in controls
            ),
            "diagnostic_targets": declaration["shape_review"],
            "automatic_expansion_or_GPU_followup": False, "performance_accepted": False,
        })
        state.update(status="complete_pilot_requires_shape_review", GPU_followup_registered=False)
    except BaseException as error:
        state.update(status="stopped_preserve_pilot_evidence", error=repr(error))
    state["finished_at"] = datetime.now(timezone.utc).isoformat()
    capture.reservation.write_json(args.output_dir / "state.json", state)
    print(state)


if __name__ == "__main__":
    main()
