"""After complete native collection, expand training without starting GPU work."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts import run_causal_source_capture as capture


def source_ready(plan, state, *, alive=capture.reservation.alive):
    status = state.get("status", "")
    if status in ("capture_failed", "capture_interrupted", "source_captured_selection_rejected"):
        raise ValueError("source did not complete its declared collection: " + status)
    if status != "source_captured_gpu_replay_pending":
        if state.get("watcher") and not alive(state["watcher"]):
            raise ValueError("native capture exited without a complete source")
        return False
    if (
        state.get("original_exit_code") != 0
        or state.get("source_selection", {}).get("accepted") is not True
        or state.get("source_selection", {}).get("workflow_count")
        != int(plan["environment"]["ROOT_COUNT"])
        or not state.get("source_requests", 0)
    ):
        raise ValueError("source completion lacks its exact successful population")
    return not any(alive(state.get(name)) for name in ("watcher", "driver"))


def audit_finished_source(plan, state):
    run = Path(plan["run_root"])
    tokens, graph = (
        capture.load(run / name) for name in ("token_evidence.json", "causal_evidence.json")
    )
    drain = capture.load(run / "native_capture/telemetry_drain.json")
    if (
        tokens.get("token_evidence_complete") is not True
        or tokens.get("native_requests") != state["source_requests"]
        or graph.get("causal_graph_compiled") is not True
        or not drain["previous_zero_snapshot_ts_ms"] < drain["drained_status"]["snapshot_ts_ms"]
        or any(
            capture.reservation.owned_runs().get(path)
            for path in (run, run / "native_capture")
        )
    ):
        raise ValueError("source needs complete tokens, causal events, fresh drain and actual owned exit")
    return {str(run / name): sha256_file(run / name) for name in (
        "token_evidence.json", "causal_evidence.json", "native_capture/telemetry_drain.json",
    )}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--historical-capture", type=Path, required=True)
    parser.add_argument("--calibration-dataset", type=Path, required=True)
    parser.add_argument("--evaluation-dataset", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or git_source_state(ROOT)["dirty"]:
        raise ValueError("CPU postprocessing requires CUDA hidden and clean frozen sources")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    state_path = args.output_dir / "state.json"
    inputs = (args.registration, args.calibration_dataset, args.evaluation_dataset,
              Path(__file__), ROOT / "scripts/train_workflow_work.py")
    fingerprints = {str(path.resolve()): sha256_file(path) for path in inputs}
    plan = capture.load(args.registration)
    state = {
        "status": "waiting_complete_native_training_source",
        "watcher": capture.reservation.process(os.getpid()), "inputs_sha256": fingerprints,
        "application": git_source_state(ROOT), "GPU_experiment_started": False,
        "performance_accepted": False,
    }

    def pulse():
        if any(sha256_file(Path(path)) != digest for path, digest in fingerprints.items()):
            raise ValueError("frozen CPU postprocessing input changed")
        state["checked_at"] = datetime.now(timezone.utc).isoformat()
        capture.reservation.write_json(state_path, state)

    def command(name, values):
        pulse()
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts/train_workflow_work.py"), *values],
            cwd=ROOT, text=True, capture_output=True,
        )
        capture.reservation.write_json(args.output_dir / (name + ".json"), {
            "arguments": values, "returncode": completed.returncode,
            "stdout": completed.stdout, "stderr": completed.stderr,
        })
        if completed.returncode:
            raise ValueError("CPU " + name + " failed; preserve its evidence")

    try:
        source_path = args.registration.with_suffix(".state.json")
        while True:
            pulse()
            observed = capture.load(source_path)
            if source_ready(plan, observed):
                break
            time.sleep(15.)
        state["source_completion_sha256"] = audit_finished_source(plan, observed)
        state["status"] = "exporting_completed_training_workflows"
        dataset = args.output_dir / "train32.json"
        command("export", [
            "export", "--capture", str(args.historical_capture),
            "--capture", str(Path(plan["run_root"]) / "native_capture"),
            "--prefill-token-weight", "0.01", "--output", str(dataset),
        ])
        rows = capture.load(dataset)
        if rows["rejected_non_natural_workflows"] or len({r["workflow"] for r in rows["rows"]}) != 32:
            raise ValueError("training expansion must retain exactly24 old and8 new natural workflows")
        state["status"] = "fitting_expanded_work_model"
        command("fit", [
            "fit", "--dataset", str(dataset),
            "--calibration-dataset", str(args.calibration_dataset),
            "--evaluation-dataset", str(args.evaluation_dataset),
            "--split-mode", "capture", "--output-dir", str(args.output_dir / "fit"),
        ])
        state["evaluation"] = capture.load(args.output_dir / "fit/evaluation.json")
        state["status"] = "training_complete_requires_review"
        state["further_GPU_action_registered"] = False
    except BaseException as error:
        state.update(status="stopped_no_GPU_action", error=repr(error))
    finally:
        state["finished_at"] = datetime.now(timezone.utc).isoformat()
        state["checked_at"] = state["finished_at"]
        capture.reservation.write_json(state_path, state)


if __name__ == "__main__":
    main()
