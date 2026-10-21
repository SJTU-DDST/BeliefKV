#!/usr/bin/env python3
"""Pause a frozen internal matrix after an arm, preserving its postprocessing."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.run_internal_comparison import load, qualified, reservation


def at_boundary(state: dict, arm: str, *, alive=reservation.alive) -> bool:
    return (
        state.get("current_arm") == arm
        and state.get("status") == f"postprocessing_{arm}"
        and state.get("driver") is not None
        and not alive(state["driver"])
    )


def activate_pause(marker: Path, request: dict) -> None:
    marker.parent.mkdir(parents=True, exist_ok=True)
    try:
        with marker.open("x") as stream:
            json.dump({
                "reason": "user_requested_pause_after_internal_arm",
                "registration": request["registration"], "arm": request["arm"],
                "requested_at": request["requested_at"],
                "activated_at": reservation.now().isoformat(),
                "automatic_resume_allowed": False,
            }, stream, indent=2)
            stream.write("\n")
    except FileExistsError:
        # A newer hold must never be replaced by this administrative request.
        pass


def finish_pause(state: dict, request: dict) -> dict:
    target = request["arm"]
    if target not in state.get("completed_arms", ()):
        raise ValueError("target arm postprocessing has not completed")
    if state.get("current_arm") != target:
        raise ValueError("another arm was started after the requested boundary")
    return {
        **state, "status": "paused_after_arm",
        "reason": "user_requested_pause_after_internal_arm",
        "paused_after_arm": target, "paused_at": reservation.now().isoformat(),
        "pending_arms": [
            mode for mode in state["order"] if mode not in state["completed_arms"]
        ],
        "supervisor_status_before_pause": state["status"],
        "performance_accepted": False,
    }


def watch(path: Path) -> None:
    request = load(path)
    state_path = Path(request["state"])
    watcher = request["watcher"]
    request.update(status="waiting_arm_boundary", supervisor=reservation.process(os.getpid()))
    reservation.write_json(path, request)
    try:
        while True:
            state = load(state_path)
            if state.get("watcher") != watcher:
                # Compare stable process identity; scheduler snapshots can update state.
                current = state.get("watcher", {})
                if any(current.get(name) != watcher[name] for name in ("pid", "start_ticks")):
                    raise ValueError("comparison supervisor identity changed")
            if at_boundary(state, request["arm"]):
                activate_pause(Path(request["pause_marker"]), request)
                request.update(status="waiting_postprocessing", activated_at=reservation.now().isoformat())
                reservation.write_json(path, request)
                break
            if not reservation.alive(watcher):
                raise ValueError("comparison exited before requested postprocessing boundary")
            if (
                state.get("current_arm") in state["order"]
                and state["order"].index(state["current_arm"]) > state["order"].index(request["arm"])
            ):
                activate_pause(Path(request["pause_marker"]), request)
                raise ValueError("requested boundary was missed; pause activated")
            time.sleep(.25)
        while reservation.alive(watcher):
            time.sleep(.25)
        state = finish_pause(load(state_path), request)
        reservation.write_json(state_path, state)
        request.update(status="paused", completed_arms=state["completed_arms"],
                       pending_arms=state["pending_arms"], finished_at=reservation.now().isoformat())
    except Exception as error:
        request.update(status="needs_investigation", error=repr(error),
                       finished_at=reservation.now().isoformat())
    finally:
        reservation.write_json(path, request)


def register(registration: Path, arm: str) -> dict:
    plan = load(registration)
    state_path = registration.with_suffix(".state.json")
    state = load(state_path)
    if arm not in plan["order"] or state.get("current_arm") != arm:
        raise ValueError("request must target the current internal arm")
    if not reservation.alive(state["watcher"]):
        raise ValueError("comparison supervisor is not alive")
    path = registration.with_suffix(".pause_after.json")
    log = registration.with_suffix(".pause_after.log")
    if path.exists() or log.exists():
        raise FileExistsError("preserve the existing boundary pause request")
    request = {
        "schema_version": 1, "status": "registered", "arm": arm,
        "registration": str(registration.resolve()), "state": str(state_path.resolve()),
        "watcher": state["watcher"], "pause_marker": str(qualified.PAUSE),
        "requested_at": reservation.now().isoformat(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "automatic_resume_allowed": False,
    }
    reservation.write_json(path, request)
    with log.open("xb") as output:
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--watch", str(path)],
            cwd=ROOT, env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999"},
            stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return {"request": str(path), "supervisor": reservation.process(child.pid), "arm": arm}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path)
    parser.add_argument("--arm")
    parser.add_argument("--watch", type=Path)
    args = parser.parse_args()
    if args.watch:
        watch(args.watch)
    elif args.registration and args.arm:
        print(json.dumps(register(args.registration, args.arm), indent=2))
    else:
        parser.error("supply --registration and --arm, or --watch")


if __name__ == "__main__":
    main()
