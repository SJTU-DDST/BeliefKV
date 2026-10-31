import json
from unittest.mock import patch

import pytest

from beliefkv.experiments.source_provenance import sha256_file
from scripts import stop_after_measured_cohort as stop
from scripts.verify_measured_execution import verify_measured_execution
from tests.test_measured_cohort_stop import partial_fixture


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


def fixture(tmp_path):
    arrivals, cohort, events, _samples = partial_fixture()
    source, run = tmp_path / "source", tmp_path / "run"
    arm, directory = run / "full", run / "cohort_termination"
    arrival_path = put(tmp_path / "arrivals.json", arrivals)
    cohort_path = put(tmp_path / "cohort.json", cohort)
    expected, tokens, receipts = {}, [], []
    for index in (1, 2, 3):
        request, workflow = f"r{index}", f"w{index}"
        identity = [workflow, workflow + ":a", workflow + ":c", 0]
        expected[request] = {"llm_submit": [identity, 2], "llm_result": [identity, 1]}
        if index == 3:
            continue
        tokens.extend([
            {"request_id": request, "event": "request_prompt", "token_sha256": "p" + request},
            {"request_id": request, "event": "request_output", "token_sha256": "o" + request,
             "token_count": 1, "native_finish_reason": {"type": "stop"}},
        ])
        receipts.append({
            "request_id": request, "prompt_tokens": 2, "output_tokens": 1,
            "prompt_sha256": "p" + request, "output_sha256": "o" + request,
            "native_finish_reason": {"type": "stop"}, "exact_tokens_verified": True,
            "native_receipt": {
                "sampling_path": "position", "identity": identity, "output_token_ids": [1],
            },
        })
    put(source / "expected.json", expected)
    token_path = source / "server/request_tokens.jsonl"
    token_path.parent.mkdir(parents=True)
    token_path.write_text("".join(json.dumps(row) + "\n" for row in tokens))
    replay = arm / "replay"
    replay.mkdir(parents=True)
    (replay / "native_requests.jsonl").write_text("".join(json.dumps(row) + "\n" for row in receipts))
    (replay / "runtime_events.replay.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in events),
    )
    registration = put(tmp_path / "comparison.json", {
        "run_root": str(run), "source_arm": str(source), "order": ["full"],
        "workflow_arrivals": arrivals,
        "workflow_arrivals_path": str(arrival_path), "sampling_mode": "position",
        "token_source_sha256": sha256_file(token_path),
    })
    put(registration.with_suffix(".state.json"), {
        "watcher": {"pid": 11}, "driver": {"pid": 12}, "server": {"pid": 13},
    })
    cleanup = put(arm / "owned_process_cleanup.json", {
        "finished_at": "done", "remaining_processes": [], "identities": [{"pid": 13}],
    })
    drain = put(directory / "telemetry_drain.json", {
        "snapshot_ts_ms_at_drain_entry": 10., "previous_zero_snapshot_ts_ms": 11.,
        "drained_status": {
            "snapshot_ts_ms": 12., "writer_queue_depth": 0,
            "pending_batch_count": 0, "pending_request_count": 0,
        },
    })
    measured = cohort["measured_workflow_ids"]
    starts = {row["workflow_id"]: row["ts_ms"] for row in events if row["kind"] == "workflow_start"}
    ends = {row["workflow_id"]: row["ts_ms"] for row in events if row["kind"] == "workflow_end"}
    intent = put(directory / "intent.json", {
        "termination_rule": "all_fixed_measured_workflows_completed",
        "measured_end_ms": {key: ends[key] for key in measured},
    })
    proof = stop.receipt_proof(json.loads(registration.read_bytes()), cohort, arm)
    receipt_proof = put(directory / "receipts.json", proof)
    certificate = {
        **json.loads(intent.read_bytes()), **proof,
        "status": "measured_cohort_complete_background_censored",
        "comparison_sha256": sha256_file(registration), "cohort_sha256": sha256_file(cohort_path),
        "actual_owned_exit_verified": True, "fresh_idle_drain_verified": True,
        "no_measured_workflow_truncated": True, "policy_not_given_cohort_membership": True,
        "measured_JCT_ms": {key: ends[key] - starts[key] for key in measured},
        "cohort_completion_monotonic_ms": max(ends[key] for key in measured),
        "completed_nonmeasured": ["w0"], "right_censored_nonmeasured": ["w3", "w4", "w5"],
        "never_submitted_background": ["w4", "w5"],
        "inputs_sha256": {
            str(path): sha256_file(path) for path in (intent, receipt_proof, drain, cleanup)
        },
    }
    put(directory / "completion.json", certificate)
    return registration, cohort_path, directory, arm


def test_actual_measured_requests_are_complete_while_background_is_censored(tmp_path):
    registration, cohort, _directory, _arm = fixture(tmp_path)
    with patch.object(stop.capture.reservation, "alive", return_value=False):
        result = verify_measured_execution(registration, cohort)
    assert result["measured_requests"] == result["measured_output_tokens"] == 2
    assert set(result["measured_work"]) == {"r1", "r2"}
    assert result["actual_owned_exit_verified"]
    assert not result["all_workflows_completed"]


@pytest.mark.parametrize("change", (
    "cohort", "receipt", "boundary", "censoring", "drain", "live", "missing_pin",
))
def test_partial_or_stale_measured_completion_is_never_accepted(tmp_path, change):
    registration, cohort, directory, arm = fixture(tmp_path)
    certificate_path = directory / "completion.json"
    certificate = json.loads(certificate_path.read_bytes())
    if change == "cohort":
        certificate["cohort_sha256"] = "wrong"
    elif change == "receipt":
        (arm / "replay/native_requests.jsonl").write_text("")
    elif change == "boundary":
        path = arm / "replay/runtime_events.replay.jsonl"
        lines = path.read_text().splitlines()
        path.write_text("\n".join(lines + [lines[-1]]) + "\n")
    elif change == "censoring":
        certificate["right_censored_nonmeasured"] = ["w3"]
    elif change == "drain":
        path = directory / "telemetry_drain.json"
        value = json.loads(path.read_bytes())
        value["previous_zero_snapshot_ts_ms"] = 10.
        put(path, value)
        certificate["inputs_sha256"][str(path)] = sha256_file(path)
    elif change == "missing_pin":
        certificate["inputs_sha256"].pop(str(directory / "receipts.json"))
    put(certificate_path, certificate)
    with patch.object(stop.capture.reservation, "alive", return_value=change == "live"):
        with pytest.raises(ValueError):
            verify_measured_execution(registration, cohort)
