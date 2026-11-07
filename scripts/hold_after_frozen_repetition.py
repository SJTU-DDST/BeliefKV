"""Activate the user's GPU hold after the current frozen repetition exits."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT.parent / "ops"
sys.path.insert(0, str(OPS))

import run_background83_frozen_replication_20261107 as repeat

REQUEST = repeat.PLAN.with_suffix(".pause_after.json")
HOLD = OPS / "gpu_hold_until_20261022_0600.json"
MARKER = Path("/tmp/beliefkv-experiments.paused")
reservation = repeat.causal.reservation


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def owners(request):
    result = [request["coordinator"]]
    for path in request["registration_states"]:
        path = Path(path)
        if not path.exists():
            continue
        state = repeat.load(path)
        result.extend(state.get(name) for name in ("watcher", "server", "driver"))
        result.extend(state.get("owned", ()))
    return [owner for owner in result if owner is not None]


def watch(path):
    request = repeat.load(path)
    repeat.campaign.verify_pins(request["inputs_sha256"])
    state_path = path.with_suffix(".state.json")
    state = {
        "status": "waiting_current_frozen_repetition",
        "process": reservation.process(os.getpid()),
        "GPU_action": False,
    }
    while True:
        if path.with_suffix(".cancel.json").exists():
            state["status"] = "cancelled_by_newer_instruction"
            break
        if repeat.campaign.sha256_file(HOLD) != request["hold_sha256"]:
            state["status"] = "superseded_by_changed_reservation"
            break
        state["live_owners"] = [owner for owner in owners(request) if reservation.alive(owner)]
        if not state["live_owners"]:
            current = repeat.load(HOLD)
            metadata = {
                "reason": "user_requested_no_GPU_after_current_frozen_repetition",
                "requested_at": request["requested_at"],
                "activated_at": timestamp(),
                "campaign": request["campaign"],
                "request": str(path),
                "automatic_resume_allowed": False,
                "resume_requires_explicit_user_release": True,
                "CPU_analysis_allowed": True,
            }
            try:
                with MARKER.open("x") as stream:
                    json.dump(metadata, stream, indent=2)
                    stream.write("\n")
            except FileExistsError:
                state["existing_pause_marker_preserved"] = True
            reservation.write_json(HOLD, {
                **current,
                "status": "user_requested_GPU_hold_after_frozen_repetition",
                "automatic_resume_allowed": False,
                "resume_requires_explicit_user_release": True,
                "pause_file": str(MARKER),
                "post_repetition_hold": metadata,
                "previous_hold_file": request["hold_backup"],
                "previous_hold_sha256": request["hold_sha256"],
            })
            state.update(
                status="GPU_hold_active_until_explicit_user_release",
                activated_at=metadata["activated_at"],
            )
            break
        state["checked_at"] = timestamp()
        reservation.write_json(state_path, state)
        time.sleep(5.)
    state["checked_at"] = timestamp()
    reservation.write_json(state_path, state)


def register():
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("register the hold using CPU-only nice19")
    if REQUEST.exists() or REQUEST.with_suffix(".log").exists():
        raise FileExistsError("preserve the previous hold request")
    plan, state = repeat.load(repeat.PLAN), repeat.load(repeat.PLAN.with_suffix(".state.json"))
    if not reservation.alive(state["process"]):
        raise ValueError("attach the hold to the actual current repetition coordinator")
    backup = REQUEST.with_suffix(".previous_hold.json")
    raw = HOLD.read_bytes()
    with backup.open("xb") as stream:
        stream.write(raw)
    repeat.write(REQUEST, {
        "requested_at": timestamp(),
        "source": "user_instruction",
        "instruction": "Finish this repetition, then stop further GPU experiments.",
        "campaign": str(repeat.PLAN),
        "coordinator": state["process"],
        "allowed_existing_stages": list(plan["stages"]),
        "registration_states": [
            str(Path(stage["registration"]).with_suffix(".state.json"))
            for stage in plan["stages"].values()
        ],
        "hold_backup": str(backup),
        "hold_sha256": repeat.campaign.sha256_file(backup),
        "resume_requires_explicit_user_release_after_current_round": True,
        "CPU_analysis_allowed": True,
        "inputs_sha256": {
            str(path): repeat.campaign.sha256_file(path)
            for path in (Path(__file__).resolve(), repeat.PLAN)
        },
    })
    with REQUEST.with_suffix(".log").open("x") as output:
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--watch", str(REQUEST)],
            cwd=ROOT, env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999"},
            stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    print(json.dumps({"request": str(REQUEST), "watcher": reservation.process(child.pid)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", type=Path)
    args = parser.parse_args()
    if args.watch:
        watch(args.watch)
    else:
        register()
