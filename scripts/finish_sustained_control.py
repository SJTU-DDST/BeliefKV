"""Wait for a complete control, then collect CPU-only workload and JCT evidence."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from experiments.reports.workflow_mix_20261029.review_burst import complete_work
from scripts import run_causal_source_capture as capture


def registered_control_complete(registration):
    if not registration.is_file() or not registration.with_suffix(".state.json").is_file():
        return False
    return complete_work(registration) is not None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--position-registration", type=Path, required=True)
    parser.add_argument("--analysis-checkout", type=Path, required=True)
    parser.add_argument("--arrivals", type=Path, required=True)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    state_path = args.output_dir.with_suffix(".state.json")
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or args.output_dir.exists() or state_path.exists():
        raise ValueError("hide CUDA and preserve every earlier reviewer and result")
    state = {
        "status": "waiting_complete_sustained_control",
        "process": capture.reservation.process(os.getpid()), "steps": [],
        "GPU_followup_registered": False,
        "inputs_sha256": {
            str(path.resolve()): sha256_file(path)
            for path in (Path(__file__), args.arrivals, args.cohort, args.position_registration)
        },
    }

    def publish():
        state["checked_at"] = datetime.now(timezone.utc).isoformat()
        capture.reservation.write_json(state_path, state)

    try:
        deadline = time.monotonic() + 4 * 3600
        while True:
            for path in (args.position_registration, args.registration):
                current_path = path.with_suffix(".state.json")
                if current_path.exists():
                    current = json.loads(current_path.read_bytes())
                    if current.get("status") == "stopped_for_investigation":
                        raise RuntimeError(f"preserve stopped experiment: {path}: {current.get('error')}")
            if registered_control_complete(args.registration):
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("complete sustained control review exceeded four hours")
            publish()
            time.sleep(10.)
        registration = json.loads(args.registration.read_bytes())
        arm = Path(registration["run_root"]) / "full"
        state["inputs_sha256"][str(args.registration.resolve())] = sha256_file(args.registration)
        commands = (
            ("complete_control", [
                "experiments/reports/workflow_mix_20261029/review_burst.py",
                "--registration", str(args.registration), "--output-dir", str(args.output_dir),
            ]),
            ("fixed_cohort", [
                "scripts/audit_workflow_cohort.py", "--arm", str(arm),
                "--arrivals", str(args.arrivals), "--cohort", str(args.cohort),
                "--output", str(args.output_dir / "cohort.json"), "--bin-seconds", "30",
            ]),
            ("supply", [
                "scripts/audit_sustained_supply.py", "--registration", str(args.registration),
                "--cohort", str(args.cohort), "--output", str(args.output_dir / "supply.json"),
            ]),
            ("periodic_decisions", [
                "scripts/audit_periodic_admission_decisions.py",
                "--registration", str(args.registration),
                "--output", str(args.output_dir / "periodic_decisions.json"),
            ]),
        )
        for name, parts in commands:
            script = args.analysis_checkout / parts[0]
            command = [sys.executable, str(script), *parts[1:]]
            state.update(status="reviewing_complete_sustained_control", current_step=name)
            state["inputs_sha256"][str(script.resolve())] = sha256_file(script)
            publish()
            child = subprocess.Popen(command, cwd=args.analysis_checkout)
            while child.poll() is None:
                publish()
                try:
                    child.wait(timeout=30.)
                except subprocess.TimeoutExpired:
                    pass
            state["steps"].append({"name": name, "returncode": child.returncode})
            if child.returncode:
                raise RuntimeError(f"preserve failed CPU review step: {name}")
        state.update(status="complete_requires_workload_and_oracle_decision", formal_workload_qualified=False)
    except BaseException as error:
        state.update(status="stopped_preserve_sustained_review", error=repr(error))
    state["finished_at"] = datetime.now(timezone.utc).isoformat()
    publish()


if __name__ == "__main__":
    main()
