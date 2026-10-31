from copy import deepcopy
import json
import signal
from unittest.mock import patch

import pytest

from beliefkv.experiments.replay_arrivals import poisson_arrival_plan
from beliefkv.experiments.workflow_cohort import declare_cohort, summarize_cohort, validate_cohort
from scripts import stop_after_measured_cohort as stop
from scripts.review_sustained_workload import inspect
from tests.test_sustained_workload_review import reports


def partial_fixture():
    arrivals = poisson_arrival_plan(
        [f"w{i}" for i in range(6)], rate_per_second=.1, seed=31, initial_workflows=1,
    )
    slots = [row["offset_seconds"] for row in arrivals["arrival_schedule"]]
    cohort = declare_cohort(
        arrivals, warmup=1, measured=2, throughput_start_s=slots[1],
        throughput_end_s=slots[2], stop_after_measured=True,
    )
    origin, cutoff = 10000., 10000. + (slots[3] + slots[4]) * 500.
    events = [
        {"kind": "workflow_start", "workflow_id": f"w{i}", "ts_ms": origin + slots[i] * 1000.}
        for i in range(4)
    ]
    events += [
        {"kind": "workflow_end", "workflow_id": "w0", "ts_ms": origin + .01},
        {"kind": "workflow_end", "workflow_id": "w1", "ts_ms": cutoff},
        {"kind": "workflow_end", "workflow_id": "w2", "ts_ms": origin + slots[2] * 1000. + .01},
    ]
    samples = [{
        "event": "gpu_service_sample", "forward_iter": i, "phase": "decode",
        "complete_monotonic_ms": origin + slots[i] * 1000. + .001,
        "monotonic_clock_domain": "clock", "batch_size": 1,
        "request_samples": [{"workflow_id": f"w{i}", "token_delta": 1, "output_token_delta": 1}],
    } for i in range(1, 4)]
    return arrivals, cohort, events, samples


def test_complete_measured_JCT_does_not_require_background_or_warmup_tails():
    arrivals, cohort, events, samples = partial_fixture()
    result = summarize_cohort(cohort, arrivals, events, samples)
    assert result["all_measured_completed"] and not result["all_workflows_completed"]
    assert result["measured_complete_workflow_JCT_ms"]["count"] == 2
    assert result["nonmeasured_workflows_right_censored"] == ["w3", "w4", "w5"]
    assert result["unarrived_workflows_at_observation_end"] == ["w4", "w5"]
    assert result["background_workflow_JCT_ms"] == {}
    assert not result["normal_tails_removed"]
    assert result["common_window_all_workflows_output_tokens"] == 1
    assert result["all_service_output_tokens"] == 3


def test_even_one_unfinished_fixed_measured_member_prevents_termination():
    arrivals, cohort, events, samples = partial_fixture()
    events = [row for row in events if not (
        row["kind"] == "workflow_end" and row["workflow_id"] == "w1"
    )]
    with pytest.raises(ValueError, match="all fixed measured"):
        summarize_cohort(cohort, arrivals, events, samples)


def test_arrival_prefix_and_declared_stop_window_remain_enforced():
    arrivals, cohort, events, samples = partial_fixture()
    changed = deepcopy(cohort)
    changed["throughput_window_offset_seconds"][1] = arrivals["arrival_schedule"][-1]["offset_seconds"]
    with pytest.raises(ValueError):
        validate_cohort(changed, arrivals)
    events = [row for row in events if row["workflow_id"] != "w2"]
    with pytest.raises(ValueError):
        summarize_cohort(cohort, arrivals, events, samples)


def test_new_workload_gate_requires_explicit_censoring_and_measured_receipt_proof():
    values = reports()
    values[1]["cohort"]["schema_version"] = 2
    values[1].update(
        all_workflows_completed=False, all_measured_completed=True,
        measured_service_output_matches_complete_receipts=True,
        measured_exact_receipt_requests=100,
    )
    values[2]["all_workflows_completed"] = False
    assert not inspect(*values)["accepted_for_oracle_diagnostic"]
    values[0].update(
        measured_cohort_and_owned_exit_verified=True, complete_measured_native_work_verified=True,
        termination_protocol="all_fixed_measured_workflows_completed",
        background_right_censoring_explicit=True,
        evaluation_cohort=deepcopy(values[1]["cohort"]), measured_native_requests=100,
    )
    result = inspect(*values)
    assert result["accepted_for_oracle_diagnostic"]
    assert result["evaluation_termination"].startswith("all_fixed_measured")
    values[1]["measured_service_output_matches_complete_receipts"] = False
    assert not inspect(*values)["accepted_for_oracle_diagnostic"]


def test_incremental_boundary_reader_does_not_consume_partial_json(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"kind":"workflow_start","workflow_id":"w1","ts_ms":1}\n{"kind":')
    rows, offset = stop.tail_records(path, 0)
    starts, ends = {}, {}
    stop.boundaries(rows, starts, ends, {"w1"})
    assert starts == {"w1": 1} and not ends
    path.write_bytes(path.read_bytes() + b'"workflow_end","workflow_id":"w1","ts_ms":2}\n')
    rows, following = stop.tail_records(path, offset)
    stop.boundaries(rows, starts, ends, {"w1"})
    assert following == path.stat().st_size and ends == {"w1": 2}
    with pytest.raises(ValueError):
        stop.boundaries(rows, starts, ends, {"w1"})


@pytest.mark.parametrize("broken_receipts", (False, True))
def test_supervision_is_resumed_even_if_receipt_validation_fails(tmp_path, broken_receipts):
    state = {
        "watcher": {"pid": 11}, "driver": {"pid": 12}, "server": {"pid": 13},
    }
    comparison = {
        "run_root": str(tmp_path), "token_source_sha256": "source",
    }
    signals = []
    with (
        patch.object(stop.capture.reservation, "alive", return_value=True),
        patch.object(stop.capture.reservation, "process", return_value={"state": "T"}),
        patch.object(stop.os, "kill", side_effect=lambda pid, sig: signals.append((pid, sig))),
        patch.object(stop, "wait_exit"),
        patch.object(stop, "idle_snapshots", return_value={}),
        patch.object(stop, "receipt_proof", **(
            {"side_effect": ValueError("incomplete measured receipt")}
            if broken_receipts else {"return_value": {"source_token_sha256": "source"}}
        )),
        patch.object(stop.capture.reservation, "write_json"),
        patch.object(stop.causal, "load", return_value={"remaining_processes": [], "identities": []}),
    ):
        if broken_receipts:
            with pytest.raises(ValueError, match="incomplete"):
                stop.stop_owned(comparison, {}, tmp_path, state=state)
        else:
            stop.stop_owned(comparison, {}, tmp_path, state=state)
    assert signals[:2] == [(11, signal.SIGSTOP), (12, signal.SIGINT)]
    assert signals[-1] == (11, signal.SIGCONT)
    assert ((11, signal.SIGTERM) in signals) is not broken_receipts


def test_unverified_or_reused_process_identity_is_never_signalled():
    with patch.object(stop.capture.reservation, "alive", return_value=False), patch.object(stop.os, "kill") as kill:
        with pytest.raises(RuntimeError, match="identity changed"):
            stop.send({"pid": 123}, signal.SIGINT)
        kill.assert_not_called()


def test_shutdown_drain_requires_two_new_idle_snapshots_after_client_exit(tmp_path):
    def snapshot(when):
        return {
            "snapshot_ts_ms": when, "writer_queue_depth": 0,
            "pending_batch_count": 0, "pending_request_count": 0,
        }
    with (
        patch.object(stop.capture.reservation, "alive", return_value=True),
        patch.object(stop.causal, "load", side_effect=[
            snapshot(10), snapshot(10), snapshot(11), snapshot(12),
        ]),
        patch.object(stop.time, "sleep"),
    ):
        result = stop.idle_snapshots(tmp_path, {"pid": 123})
    assert result["snapshot_ts_ms_at_drain_entry"] == 10
    assert result["previous_zero_snapshot_ts_ms"] == 11
    assert result["drained_status"]["snapshot_ts_ms"] == 12


@pytest.mark.parametrize("mutation", ("none", "missing", "hash", "stop", "duplicate", "partial_line"))
def test_receipt_proof_requires_every_measured_request_but_allows_incomplete_background(tmp_path, mutation):
    source, arm = tmp_path / "source", tmp_path / "arm"
    (source / "server").mkdir(parents=True)
    (arm / "replay").mkdir(parents=True)
    identity = ["measured", "agent", "context", 0]
    (source / "expected.json").write_text(json.dumps({
        "r": {"llm_submit": [identity, 2], "llm_result": [identity, 1]},
        "background": {"llm_submit": [["background", "a", "c", 0], 2]},
    }))
    tokens = [
        {"request_id": "r", "event": "request_prompt", "token_sha256": "prompt"},
        {"request_id": "r", "event": "request_output", "token_sha256": "output",
         "token_count": 1, "native_finish_reason": {"type": "stop"}},
    ]
    (source / "server/request_tokens.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in tokens),
    )
    row = {
        "request_id": "r", "prompt_tokens": 2, "output_tokens": 1,
        "prompt_sha256": "prompt", "output_sha256": "output",
        "native_finish_reason": {"type": "stop"}, "exact_tokens_verified": True,
        "native_receipt": {
            "sampling_path": "position", "identity": identity, "output_token_ids": [1],
        },
    }
    if mutation == "hash":
        row["output_sha256"] = "changed"
    elif mutation == "stop":
        row["native_finish_reason"] = {"type": "length"}
    data = "" if mutation == "missing" else json.dumps(row) + "\n"
    if mutation == "duplicate":
        data += data
    elif mutation == "partial_line":
        data += '{"unfinished":'
    (arm / "replay/native_requests.jsonl").write_text(data)
    comparison = {"source_arm": str(source), "sampling_mode": "position"}
    cohort = {"measured_workflow_ids": ["measured"]}
    if mutation != "none":
        with pytest.raises(ValueError):
            stop.receipt_proof(comparison, cohort, arm)
    else:
        result = stop.receipt_proof(comparison, cohort, arm)
        assert result["complete_measured_native_work_verified"]
        assert result["measured_requests"] == 1
        assert not result["complete_population_work_conserved"]
