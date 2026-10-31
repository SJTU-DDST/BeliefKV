"""Verify completed measured work, explicit censoring, fresh drain and owned exit."""

from pathlib import Path

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workflow_cohort import validate_cohort
from scripts import run_causal_comparison as causal
from scripts import stop_after_measured_cohort as stop
from scripts.audit_sealed_wait_prefetch import SealedRecords


def verify_measured_execution(registration, cohort_path):
    registration, cohort_path = Path(registration), Path(cohort_path)
    plan, cohort = causal.load(registration), causal.load(cohort_path)
    arrivals = causal.load(Path(plan["workflow_arrivals_path"]))
    if arrivals != plan["workflow_arrivals"]:
        raise ValueError("completed execution's frozen arrival table changed")
    validate_cohort(cohort, arrivals)
    if cohort["schema_version"] != 2 or plan["order"] != ["full"]:
        raise ValueError("require the explicit fixed-measured completion protocol")
    arm = Path(plan["run_root"]) / "full"
    directory = Path(plan["run_root"]) / "cohort_termination"
    certificate_path = directory / "completion.json"
    certificate = causal.load(certificate_path)
    measured = set(cohort["measured_workflow_ids"])
    if (
        certificate.get("status") != "measured_cohort_complete_background_censored"
        or certificate.get("termination_rule") != "all_fixed_measured_workflows_completed"
        or certificate.get("comparison_sha256") != sha256_file(registration)
        or certificate.get("cohort_sha256") != sha256_file(cohort_path)
        or certificate.get("measured_workflow_ids") != sorted(measured)
        or any(certificate.get(name) is not True for name in (
            "actual_owned_exit_verified", "fresh_idle_drain_verified",
            "no_measured_workflow_truncated", "policy_not_given_cohort_membership",
            "complete_measured_native_work_verified",
        ))
    ):
        raise ValueError("measured completion certificate belongs to another or incomplete execution")
    pinned = certificate.get("inputs_sha256") or {}
    required = {
        str(path) for path in (
            directory / "intent.json", directory / "receipts.json",
            directory / "telemetry_drain.json", arm / "owned_process_cleanup.json",
        )
    }
    if not required <= pinned.keys() or any(
        sha256_file(Path(name)) != digest for name, digest in pinned.items()
    ):
        raise ValueError("completion certificate lacks unchanged raw shutdown evidence")
    intent = causal.load(directory / "intent.json")
    if any(certificate.get(key) != value for key, value in intent.items()):
        raise ValueError("completion certificate differs from its original shutdown intent")
    cleanup = causal.load(arm / "owned_process_cleanup.json")
    state = causal.load(registration.with_suffix(".state.json"))
    if (
        not cleanup.get("finished_at") or cleanup.get("remaining_processes") != []
        or any(stop.capture.reservation.alive(row) for row in cleanup["identities"])
        or any(stop.capture.reservation.alive(state[name]) for name in ("watcher", "driver", "server"))
    ):
        raise ValueError("completed execution still has a live registered process")
    drain = causal.load(directory / "telemetry_drain.json")
    status = drain["drained_status"]
    if (
        not drain["snapshot_ts_ms_at_drain_entry"]
        < drain["previous_zero_snapshot_ts_ms"] < status["snapshot_ts_ms"]
        or any(status.get(name) != 0 for name in (
            "writer_queue_depth", "pending_batch_count", "pending_request_count",
        ))
        or any(status.get(name) for name in ("writer_error", "failed_records", "dropped_records"))
    ):
        raise ValueError("measured completion lacks two fresh healthy idle snapshots")
    reader, starts, ends = SealedRecords(), {}, {}
    event_path = arm / "replay/runtime_events.replay.jsonl"
    stop.boundaries(
        reader(event_path), starts, ends, {row["workflow_id"] for row in arrivals["arrival_schedule"]},
    )
    ids = [row["workflow_id"] for row in arrivals["arrival_schedule"]]
    offsets = {row["workflow_id"]: row["offset_seconds"] for row in arrivals["arrival_schedule"]}
    origins = [when - offsets[key] * 1000. for key, when in starts.items()]
    prefix = reader.prefixes[str(event_path)]
    if (
        event_path.stat().st_size != prefix["complete_prefix_bytes"]
        or sha256_file(event_path) != prefix["complete_prefix_sha256"]
        or not origins or max(origins) - min(origins) > 1e-5
        or set(starts) != set(ids[:len(starts)])
        or not measured <= ends.keys()
        or certificate["measured_end_ms"] != {key: ends[key] for key in measured}
        or certificate["measured_JCT_ms"] != {key: ends[key] - starts[key] for key in measured}
        or certificate["cohort_completion_monotonic_ms"] != max(ends[key] for key in measured)
    ):
        raise ValueError("measured completion differs from unchanged actual workflow boundaries")
    if (
        any(origins[0] + offsets[key] * 1000. <= certificate["cohort_completion_monotonic_ms"]
            and key not in starts for key in ids)
        or certificate.get("completed_nonmeasured") != sorted(ends.keys() - measured)
        or certificate.get("right_censored_nonmeasured") != sorted(set(ids) - measured - ends.keys())
        or certificate.get("never_submitted_background") != sorted(set(ids) - starts.keys())
    ):
        raise ValueError("background arrivals or right-censoring differ from the frozen protocol")
    proof = stop.receipt_proof(plan, cohort, arm)
    if any(certificate.get(key) != value for key, value in proof.items()) or (
        proof["source_token_sha256"] != plan["token_source_sha256"]
    ):
        raise ValueError("complete measured native requests do not match the certificate")
    expected = causal.load(Path(plan["source_arm"]) / "expected.json")
    required_requests = {
        key for key, row in expected.items() if row["llm_submit"][0][0] in measured
    }
    completed = causal.receipt_work(
        arm / "replay/native_requests.jsonl", sampling_mode=plan["sampling_mode"],
    )
    return {
        "protocol": "all_fixed_measured_workflows_completed",
        "registration_sha256": sha256_file(registration),
        "cohort": cohort, "cohort_path": str(cohort_path.resolve()),
        "cohort_sha256": sha256_file(cohort_path),
        "certificate": str(certificate_path.resolve()),
        "certificate_sha256": sha256_file(certificate_path),
        "measured_work": {key: completed[key] for key in sorted(required_requests)},
        "measured_requests": proof["measured_requests"],
        "measured_output_tokens": proof["measured_output_tokens"],
        "completed_receipt_requests": sorted(completed),
        "completed_workflow_ids": sorted(ends),
        "all_workflows_completed": set(ends) == set(ids),
        "actual_owned_exit_verified": True,
    }


def review_matches_measured_execution(review, evidence):
    if (
        review.get("measured_cohort_and_owned_exit_verified") is not True
        or review.get("registration_sha256") != evidence["registration_sha256"]
        or review.get("complete_measured_native_work_verified") is not True
        or review.get("background_right_censoring_explicit") is not True
        or review.get("termination_protocol") != evidence["protocol"]
        or review.get("evaluation_cohort") != evidence["cohort"]
        or review.get("cohort_sha256") != evidence["cohort_sha256"]
        or review.get("measured_native_requests") != evidence["measured_requests"]
        or review.get("termination_certificate_sha256") != evidence["certificate_sha256"]
    ):
        raise ValueError("review does not describe the verified complete measured execution")


def verify_execution(registration, cohort_path=None):
    if cohort_path is not None and causal.load(Path(cohort_path)).get("schema_version") == 2:
        result = verify_measured_execution(registration, cohort_path)
        return {**result, "work": result["measured_work"]}
    from experiments.reports.workflow_mix_20261029.review_burst import complete_work

    work = complete_work(Path(registration))
    if work is None:
        raise ValueError("wait for complete control work, fresh drain and actual owned exit")
    plan = causal.load(Path(registration))
    return {
        "protocol": "all_workflows_completed", "work": work,
        "all_workflows_completed": True,
        "completed_receipt_requests": sorted(work),
        "completed_workflow_ids": sorted(plan["workflow_ids"]),
    }


def review_matches_execution(review, evidence):
    if evidence["protocol"] == "all_fixed_measured_workflows_completed":
        review_matches_measured_execution(review, evidence)
    elif review.get("complete_work_and_actual_exit_verified") is not True:
        raise ValueError("review lacks complete population work and actual owned exit")
