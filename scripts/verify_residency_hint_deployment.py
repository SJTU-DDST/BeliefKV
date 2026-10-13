#!/usr/bin/env python3
"""Verify frozen V54 source, unchanged settings and actual residency-hint use."""

from __future__ import annotations

import argparse
import ast
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from beliefkv.experiments.source_provenance import git_source_state
from scripts.summarize_semantic_h2d_ab import verify_frozen_execution_source


def load(path):
    return json.loads(path.read_bytes())


def identity(pid, ticks):
    values = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    if values[0] == "Z" or values[19] != ticks:
        raise ValueError(f"process identity changed: {pid}")
    return {"pid": pid, "state": values[0], "ppid": int(values[1]), "start_ticks": values[19]}


def state_tail(path):
    with path.open("rb") as stream:
        stream.seek(0, 2)
        end = stream.tell()
        stream.seek(max(0, end - 8 * 1024 * 1024))
        if stream.tell():
            stream.readline()
        start = stream.tell()
        raw = stream.read(end - start)
    raw = raw[:raw.rfind(b"\n") + 1]
    states = [
        json.loads(line) for line in raw.splitlines()
        if b'"admission_runtime_state"' in line
    ]
    if not states:
        raise ValueError("no runtime state in bounded tail")
    return states[-1], {
        "path": str(path), "start_offset": start, "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--previous-run", type=Path, required=True)
    parser.add_argument("--launch", type=Path, required=True)
    parser.add_argument("--scheduler-pid", type=int, required=True)
    parser.add_argument("--scheduler-ticks", required=True)
    parser.add_argument("--engine-pid", type=int, required=True)
    parser.add_argument("--engine-ticks", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("preserve prior validation")
    plan, previous, launch = (
        load(args.run_root / "ab_plan.json"),
        load(args.previous_run / "ab_plan.json"), load(args.launch),
    )
    differences = [
        key for key in plan.keys() | previous.keys() if plan.get(key) != previous.get(key)
    ]
    if differences != ["code_commit"]:
        raise ValueError(f"workload changed: {differences}")
    if launch["status"] != "running_new_predictive" or launch["target_revision"] != plan["code_commit"]:
        raise ValueError("unexpected deployment state")
    frozen = verify_frozen_execution_source(args.repo, plan["code_commit"])
    qualified = load(ROOT / "experiments/reports/v310_residency_hint_qualification_20261013.json")
    for name, expected in qualified["source_sha256"].items():
        if hashlib.sha256((args.repo / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"deployed source differs from qualification: {name}")
    engine = git_source_state(
        args.repo / "third_party/sglang-v0.5.20",
        canonical_patch=args.repo / "patches/sglang-v0.5.20-beliefkv-staging.patch",
    )["canonical_patch"]
    if engine["sha256"] != qualified["canonical_patch_sha256"] or not engine["matches_worktree"]:
        raise ValueError("engine patch or tree changed")
    server = args.run_root / "predictive_h2d/server"
    capacity = load(server / "native_capacity_census.json")["capacity"]
    if capacity != load(args.previous_run / "predictive_h2d/server/native_capacity_census.json")["capacity"]:
        raise ValueError("physical pool capacity changed")
    settings = None
    with (args.run_root / "predictive_h2d/server.log").open() as stream:
        for line in stream:
            if "] server_args=" in line:
                settings = ast.literal_eval(line.split("] server_args=", 1)[1])
                break
    expected = {
        "enable_prefill_delayer": False, "min_free_slots_delay": None,
        "enable_mixed_chunk": False, "prefill_decode_interval": 0,
        "max_running_requests": 48, "chunked_prefill_size": 4096,
    }
    if settings is None or any(settings.get(key) != value for key, value in expected.items()):
        raise ValueError("deferred prefill settings changed")
    state, source = state_tail(args.run_root / "predictive_h2d/opportunities/admission_opportunities.jsonl")
    if state["physical_disabled"] or state["execution_handoff"] or any(
        not state.get(key) for key in ("prepare_host", "resident_first", "tool_prefetch", "final_stage_prefetch")
    ):
        raise ValueError("predictive policy differs")
    if state["counts"].get("residency_ordering_hint_read", 0) <= 0:
        raise ValueError("no actual residency-hint use")
    telemetry = load(server / "native_telemetry_status.json")
    if telemetry["writer_error"] is not None or telemetry["dropped_records"] or telemetry["failed_records"]:
        raise ValueError("native evidence lost")
    scheduler = identity(args.scheduler_pid, args.scheduler_ticks)
    parent = identity(args.engine_pid, args.engine_ticks)
    if scheduler["ppid"] != parent["pid"]:
        raise ValueError("scheduler parent changed")
    with urlopen(f"http://127.0.0.1:{args.port}/health", timeout=5) as response:
        health = response.status
    now = datetime.now().astimezone()
    if health != 200 or not 0 <= now.timestamp() * 1000. - state["ts_ms"] < 30000:
        raise ValueError("stale runtime or unhealthy server")
    result = {
        "observed_at_local": now.isoformat(), "run_root": str(args.run_root),
        "code_commit": plan["code_commit"], "execution_verification": frozen,
        "qualification_status": qualified["status"], "source_matches": True,
        "plan_differences": differences, "engine": engine, "capacity": capacity,
        "settings": {key: settings.get(key) for key in expected},
        "scheduler": scheduler, "engine_parent": parent, "health": health,
        "runtime_counts": state["counts"], "runtime_input": source,
        "native_hint_feedback_usable": False,
        "feedback_limitation": (
            "V54 reads cached_tokens_device in the admission callback before native "
            "prepare_for_extend fills the tier breakdown. Initial zeros do not "
            "measure hint disagreement; the staged fix observes first GPU service."
        ),
        "scope": "Deployment and actual hint use, not busy-window throughput or JCT improvement.",
        "helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "helper_utf8_source": Path(__file__).read_text(),
    }
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({key: result[key] for key in (
        "code_commit", "source_matches", "settings", "health", "native_hint_feedback_usable",
    )}, indent=2))


if __name__ == "__main__":
    main()
