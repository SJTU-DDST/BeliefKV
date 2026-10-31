"""Stop background work after a fixed measured cohort, preserving complete JCT."""

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workflow_cohort import validate_cohort
from scripts import run_causal_comparison as causal
from scripts import run_causal_source_capture as capture
from scripts.audit_sealed_wait_prefetch import SealedRecords


def boundaries(records, starts, ends, allowed):
    for row in records:
        kind = row.get("kind")
        if kind not in ("workflow_start", "workflow_end"):
            continue
        workflow, when = row["workflow_id"], row["ts_ms"]
        target = starts if kind == "workflow_start" else ends
        if (
            workflow not in allowed or workflow in target
            or type(when) not in (int, float) or not math.isfinite(when)
            or kind == "workflow_end" and (
                workflow not in starts or when < starts[workflow]
            )
        ):
            raise ValueError("invalid or duplicate observed workflow boundary")
        target[workflow] = when


def tail_records(path, offset):
    if not path.exists():
        return [], offset
    rows = []
    with path.open("rb") as stream:
        stream.seek(offset)
        while True:
            line = stream.readline()
            if not line or not line.endswith(b"\n"):
                break
            rows.append(json.loads(line))
            offset = stream.tell()
    return rows, offset


def send(identity, sig):
    if not capture.reservation.alive(identity):
        raise RuntimeError("registered process exited or its PID identity changed")
    os.kill(identity["pid"], sig)


def wait_exit(identity, *, timeout):
    deadline = time.monotonic() + timeout
    while capture.reservation.alive(identity):
        if time.monotonic() >= deadline:
            raise TimeoutError("registered process did not finish its shutdown")
        time.sleep(.1)


def idle_snapshots(arm, server, *, timeout=45.):
    deadline, previous = time.monotonic() + timeout, None
    path = arm / "server/native_telemetry_status.json"
    baseline = causal.load(path)["snapshot_ts_ms"]
    while True:
        if not capture.reservation.alive(server):
            raise RuntimeError("server exited before fresh telemetry drain")
        status = causal.load(path)
        if any(status.get(name) for name in ("writer_error", "failed_records", "dropped_records")):
            raise ValueError("native telemetry failed during planned cohort shutdown")
        when = status.get("snapshot_ts_ms")
        if (
            type(when) in (int, float) and math.isfinite(when) and when > baseline
            and all(status.get(name) == 0 for name in (
                "writer_queue_depth", "pending_batch_count", "pending_request_count",
            ))
        ):
            if previous is not None and when > previous:
                return {
                    "snapshot_ts_ms_at_drain_entry": baseline,
                    "previous_zero_snapshot_ts_ms": previous, "drained_status": status,
                    "scope": "Two fresh idle snapshots after background cancellation; all measured work completed.",
                }
            previous = when
        else:
            previous = None
        if time.monotonic() >= deadline:
            raise TimeoutError("cohort shutdown did not reach fresh idle telemetry")
        time.sleep(.25)


def receipt_proof(comparison, cohort, arm):
    source, measured = Path(comparison["source_arm"]), set(cohort["measured_workflow_ids"])
    expected = causal.load(source / "expected.json")
    required = {
        request for request, row in expected.items() if row["llm_submit"][0][0] in measured
    }
    reader = SealedRecords()
    rows = list(reader(arm / "replay/native_requests.jsonl"))
    observed = {}
    for row in rows:
        request = row["request_id"]
        if (
            request in observed or request not in expected
            or row.get("exact_tokens_verified") is not True
            or row["native_receipt"].get("sampling_path") != comparison["sampling_mode"]
            or row["native_receipt"]["identity"] != expected[request]["llm_submit"][0]
            or row["prompt_tokens"] != expected[request]["llm_submit"][1]
            or row["output_tokens"] != expected[request]["llm_result"][1]
        ):
            raise ValueError("completed request receipt differs from the source")
        observed[request] = row
    if {request for request in observed if request in required} != required:
        raise ValueError("measured workflow end does not prove complete measured native requests")
    metadata = {}
    with (source / "server/request_tokens.jsonl").open() as stream:
        for line in stream:
            row = json.loads(line)
            if row["request_id"] in observed:
                metadata.setdefault(row["request_id"], {})[row["event"]] = row
    for request, row in observed.items():
        prompt, output = (
            metadata[request]["request_prompt"], metadata[request]["request_output"],
        )
        if (
            row["prompt_sha256"] != prompt["token_sha256"]
            or row["output_sha256"] != output["token_sha256"]
            or row["native_finish_reason"] != output["native_finish_reason"]
            or len(row["native_receipt"]["output_token_ids"]) != output["token_count"]
        ):
            raise ValueError("completed native token hashes or stopping changed")
    receipt_path = arm / "replay/native_requests.jsonl"
    prefix = reader.prefixes[str(receipt_path)]
    if (
        receipt_path.stat().st_size != prefix["complete_prefix_bytes"]
        or sha256_file(receipt_path) != prefix["complete_prefix_sha256"]
    ):
        raise ValueError("replay output was not completely flushed before review")
    return {
        "complete_measured_native_work_verified": True,
        "measured_workflow_ids": sorted(measured),
        "measured_requests": len(required), "all_completed_receipts": len(observed),
        "measured_output_tokens": sum(observed[request]["output_tokens"] for request in required),
        "complete_population_work_conserved": len(observed) == len(expected),
        "source_token_sha256": sha256_file(source / "server/request_tokens.jsonl"),
        "input_jsonl_prefixes": reader.prefixes,
    }


def stop_owned(comparison, cohort, output, *, state):
    """Pause only supervision while the existing client cancels and flushes."""
    arm = Path(comparison["run_root"]) / "full"
    supervisor, driver, server = (state[name] for name in ("watcher", "driver", "server"))
    if len({row["pid"] for row in (supervisor, driver, server)}) != 3:
        raise ValueError("cohort shutdown requires distinct registered process owners")
    paused = False
    try:
        send(supervisor, signal.SIGSTOP)
        paused = True
        deadline = time.monotonic() + 3.
        while capture.reservation.process(supervisor["pid"]).get("state") not in ("T", "t"):
            if time.monotonic() >= deadline:
                raise TimeoutError("supervision did not pause before client cancellation")
            time.sleep(.01)
        send(driver, signal.SIGINT)
        wait_exit(driver, timeout=60.)
        drained = idle_snapshots(arm, server)
        capture.reservation.write_json(output / "telemetry_drain.json", drained)
        receipts = receipt_proof(comparison, cohort, arm)
        if receipts["source_token_sha256"] != comparison["token_source_sha256"]:
            raise ValueError("cohort receipt proof belongs to a changed token corpus")
        capture.reservation.write_json(output / "receipts.json", receipts)
        # The frozen supervisor keeps its original interruption record. The
        # separate certificate classifies this requested cohort termination.
        send(supervisor, signal.SIGTERM)
    finally:
        if paused and capture.reservation.alive(supervisor):
            os.kill(supervisor["pid"], signal.SIGCONT)
    wait_exit(supervisor, timeout=100.)
    cleanup = causal.load(arm / "owned_process_cleanup.json")
    if cleanup["remaining_processes"] or any(
        capture.reservation.alive(row) for row in cleanup["identities"]
    ):
        raise ValueError("planned cohort shutdown left owned processes running")
    return receipts


def review(plan, comparison, completion):
    output = Path(plan["analysis_output"])
    output.mkdir(parents=True, exist_ok=False)
    arm = Path(comparison["run_root"]) / "full"
    common = ["--registration", plan["comparison"]]
    commands = (
        ("cohort", [
            "scripts/audit_workflow_cohort.py", "--arm", str(arm),
            "--arrivals", comparison["workflow_arrivals_path"], "--cohort", plan["cohort"],
            "--source-arm", comparison["source_arm"], "--output", str(output / "cohort.json"),
        ]),
        ("supply", [
            "scripts/audit_sustained_supply.py", *common, "--cohort", plan["cohort"],
            "--output", str(output / "supply.json"),
        ]),
        ("periodic", [
            "scripts/audit_periodic_admission_decisions.py", *common,
            "--output", str(output / "periodic_decisions.json"),
        ]),
        ("demand", [
            "scripts/audit_live_kv_demand.py", *common, "--sample-interval-s", "60",
            "--output", str(output / "demand.json"),
        ]),
    )
    for name, parts in commands:
        subprocess.run([sys.executable, str(ROOT / parts[0]), *parts[1:]], cwd=ROOT, check=True)
    cohort = causal.load(output / "cohort.json")
    decode = [
        row for row in SealedRecords()(arm / "server/runtime_audit.jsonl")
        if row.get("event") == "gpu_service_sample" and row.get("phase") == "decode"
    ]
    steps = sum(row["batch_size"] for row in decode)
    weighted_ms = sum(row["batch_size"] * row["service_elapsed_ms"] for row in decode)
    if not steps or not math.isfinite(weighted_ms) or weighted_ms <= 0:
        raise ValueError("completed measurement lacks native decode cost support")
    control = {
        "complete_work_and_actual_exit_verified": False,
        "measured_cohort_and_owned_exit_verified": True,
        "complete_measured_native_work_verified": completion["complete_measured_native_work_verified"],
        "registration": plan["comparison"], "registration_sha256": sha256_file(Path(plan["comparison"])),
        "termination_protocol": "all_fixed_measured_workflows_completed",
        "cohort_sha256": sha256_file(Path(plan["cohort"])),
        "evaluation_cohort": cohort["cohort"],
        "measured_native_requests": completion["measured_requests"],
        "background_right_censoring_explicit": True,
        "all_workflows_completed": cohort["all_workflows_completed"],
        "termination_certificate": str(Path(plan["output_dir"]) / "completion.json"),
        "termination_certificate_sha256": sha256_file(Path(plan["output_dir"]) / "completion.json"),
        "model_cost_calibration": {
            "model_seconds_per_work_token": weighted_ms / steps / 1000.,
            "definition": "cohort_observation_decode_request_step_ms/request_steps/1000",
            "prefill_equivalent_weight": .01,
            "limitations": "batch/context-averaged worker-time proxy; not exact future service cost",
        },
        "performance_accepted": False,
    }
    capture.reservation.write_json(output / "review.json", control)
    subprocess.run([
        sys.executable, str(ROOT / "scripts/review_sustained_workload.py"),
        "--control", str(output / "review.json"), "--cohort", str(output / "cohort.json"),
        "--supply", str(output / "supply.json"),
        "--periodic", str(output / "periodic_decisions.json"),
        "--demand", str(output / "demand.json"),
        "--population", str(Path(comparison["source_arm"]) / "composite_source.json"),
        "--minimum-model-work-p80-p20", str(plan["minimum_model_work_p80_p20"]),
        "--output", str(output / "workload_decision.json"),
    ], cwd=ROOT, check=True)


def watch(registration):
    plan = causal.load(registration)
    for name, digest in plan["inputs_sha256"].items():
        if sha256_file(Path(name)) != digest:
            raise ValueError("cohort termination input changed: " + name)
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or os.getpriority(os.PRIO_PROCESS, 0) != 19:
        raise ValueError("cohort observer must keep CUDA hidden and nice19")
    comparison = causal.load(Path(plan["comparison"]))
    cohort = causal.load(Path(plan["cohort"]))
    arrivals = causal.load(Path(comparison["workflow_arrivals_path"]))
    validate_cohort(cohort, arrivals)
    if cohort["schema_version"] != 2 or comparison["order"] != ["full"]:
        raise ValueError("require the explicit measured-cohort stop protocol")
    output = Path(plan["output_dir"])
    output.mkdir(parents=True, exist_ok=False)
    state_path = registration.with_suffix(".state.json")
    state = {
        "status": "waiting_fixed_measured_cohort", "process": capture.reservation.process(os.getpid()),
        "measured_population": len(cohort["measured_workflow_ids"]),
        "GPU_followup_registered": False, "performance_accepted": False,
    }

    def publish():
        state["checked_at"] = datetime.now(timezone.utc).isoformat()
        capture.reservation.write_json(state_path, state)

    starts, ends, offset, last_count = {}, {}, 0, None
    measured = set(cohort["measured_workflow_ids"])
    allowed = {row["workflow_id"] for row in arrivals["arrival_schedule"]}
    events_path = Path(comparison["run_root"]) / "full/replay/runtime_events.replay.jsonl"
    try:
        deadline = time.monotonic() + 4 * 3600
        published = time.monotonic()
        while True:
            current = causal.load(Path(plan["comparison"]).with_suffix(".state.json"))
            rows, offset = tail_records(events_path, offset)
            boundaries(rows, starts, ends, allowed)
            completed = len(measured & ends.keys())
            if completed != last_count or time.monotonic() - published >= 30.:
                state.update(measured_completed=completed, observed_arrivals=len(starts))
                publish()
                last_count = completed
                published = time.monotonic()
            if measured <= ends.keys():
                break
            if not current["status"].startswith(("running_", "starting_", "waiting_")):
                raise ValueError("comparison exited before the measured-cohort stop: " + current["status"])
            if time.monotonic() >= deadline:
                raise TimeoutError("measured cohort did not finish within four hours")
            time.sleep(plan["poll_seconds"])
        state.update(status="stopping_nonmeasured_background")
        publish()
        intent = {
            "termination_rule": "all_fixed_measured_workflows_completed",
            "comparison_sha256": sha256_file(Path(plan["comparison"])),
            "cohort_sha256": sha256_file(Path(plan["cohort"])),
            "measured_workflow_ids": sorted(measured),
            "measured_end_ms": {key: ends[key] for key in sorted(measured)},
            "measured_JCT_ms": {key: ends[key] - starts[key] for key in sorted(measured)},
            "cohort_completion_monotonic_ms": max(ends[key] for key in measured),
            "stop_observed_monotonic_ms": time.monotonic() * 1000.,
            "completed_nonmeasured": sorted(ends.keys() - measured),
            "right_censored_nonmeasured": sorted(allowed - measured - ends.keys()),
            "never_submitted_background": sorted(allowed - starts.keys()),
            "no_measured_workflow_truncated": True,
            "policy_not_given_cohort_membership": True,
        }
        capture.reservation.write_json(output / "intent.json", intent)
        receipts = stop_owned(comparison, cohort, output, state=current)
        completion = {
            **intent, **receipts, "status": "measured_cohort_complete_background_censored",
            "actual_owned_exit_verified": True,
            "fresh_idle_drain_verified": True,
            "raw_supervisor_interruption_is_expected_for_this_frozen_run": True,
            "inputs_sha256": {
                str(path): sha256_file(path) for path in (
                    output / "intent.json", output / "receipts.json", output / "telemetry_drain.json",
                    Path(comparison["run_root"]) / "full/owned_process_cleanup.json",
                )
            },
        }
        capture.reservation.write_json(output / "completion.json", completion)
        state.update(status="reviewing_completed_measured_cohort")
        publish()
        review(plan, comparison, completion)
        decision = causal.load(Path(plan["analysis_output"]) / "workload_decision.json")
        state.update(
            status="measured_cohort_review_complete",
            accepted_for_oracle_diagnostic=decision["accepted_for_oracle_diagnostic"],
            failed_checks=decision["failed_checks"],
        )
    except BaseException as error:
        state.update(status="stopped_preserve_measured_cohort_review", error=repr(error))
    state["finished_at"] = datetime.now(timezone.utc).isoformat()
    publish()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", type=Path, required=True)
    watch(parser.parse_args().watch)
