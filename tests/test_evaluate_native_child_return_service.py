import json

from scripts.evaluate_native_child_return_service import collect, evaluate


def _run(tmp_path, *, with_hint=True):
    run = tmp_path / "run"
    client = run / "client_4"
    server = run / "server"
    server.mkdir(parents=True)
    labels, samples = [], []
    for project, end in (("alpha", 1800), ("beta", 2500),
                         ("gamma", 4300), ("delta", 3000)):
        task = f"{project}__issue"
        workflow = client / "workflows" / task
        workflow.mkdir(parents=True)
        child = f"child-{project}"
        rid = f"request-{project}"
        label = {
            "final_request_id": rid,
            "workflow_id": f"deepagents:autonomous:{task}:run",
            "child_id": child,
            "server_first_service_ts_ms": 1000,
            "server_result_ts_ms": end,
            "client_result_to_return_ms": 30,
            "notice_to_first_service_lower_bound_ms": 150,
        }
        labels.append(label)
        if with_hint:
            (workflow / "runtime_events.deepagents.jsonl").write_text(
                json.dumps({
                    "kind": "structured_action", "invocation_id": child,
                    "attributes": {
                        "child_completion_signal_kind": "stage",
                        "beliefkv_child_completion_intent": True,
                        "estimated_final_report_tokens": 300,
                    },
                }) + "\n"
            )
        for phase, when, tokens, delta in (
            ("prefill", 1100, 0, 0),
            ("decode", 1200, 120, 8),
            ("decode", 1400, 504, 8),
        ):
            samples.append({
                "event": "gpu_service_sample", "phase": phase,
                "service_start_ts_ms": when - 40, "complete_ts_ms": when,
                "request_samples": [{
                    "request_id": rid, "workflow_id": label["workflow_id"],
                    "invocation_id": child, "output_tokens_before": tokens,
                    "token_delta": delta,
                }],
            })
    (run / "final_stage_service_labels.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in labels)
    )
    (server / "runtime_audit.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in samples)
    )
    return run


def test_service_prediction_project_holdout_and_rolling_coverage(tmp_path):
    run = _run(tmp_path)
    rows, coverage = collect(run)
    assert coverage["paired_final_requests"] == 4
    assert coverage["notice_hints"] == 4
    assert coverage["checkpoint_counts"] == {
        "first_service_complete": 4, "decode_128": 4, "decode_512": 4,
    }
    assert all(row["features"][4:6] == [300, 1] for row in rows)
    assert any(row["features"][-1] > 0 for row in rows)
    report = evaluate(run)
    assert report["results"]["first_service_complete"]["evaluated"] == 4
    assert report["results"]["decode_128"]["causal_ridge"]["count"] == 4
    assert report["results"]["decode_128"]["progress_hint_ridge"]["count"] == 4

    # A held-out project's eventual result is not used by its training fold.
    report_before = report["results"]["decode_128"]["projects"]
    assert all(fold["train"] == 3 and fold["test"] == 1 for fold in report_before)
    with (run / "server/runtime_audit.jsonl").open("a") as stream:
        stream.write(json.dumps({
            "event": "gpu_service_sample", "phase": "decode",
            "service_start_ts_ms": 5000, "complete_ts_ms": 5050,
            "request_samples": [{
                "request_id": "request-alpha",
                "workflow_id": "deepagents:autonomous:alpha__issue:run",
                "invocation_id": "child-alpha",
                "output_tokens_before": 9999, "token_delta": 100,
            }],
        }) + "\n")
    assert evaluate(run) == report


def test_service_prediction_rejects_wrong_child_and_missing_hint(tmp_path):
    run = _run(tmp_path, with_hint=False)
    path = run / "server/runtime_audit.jsonl"
    with path.open("a") as stream:
        stream.write(json.dumps({
            "event": "gpu_service_sample", "phase": "decode",
            "service_start_ts_ms": 1490, "complete_ts_ms": 1500,
            "request_samples": [{
                "request_id": "request-alpha",
                "workflow_id": "deepagents:autonomous:alpha__issue:run",
                "invocation_id": "wrong-child",
                "output_tokens_before": 1024, "token_delta": 1,
            }],
        }) + "\n")
    rows, coverage = collect(run)
    assert coverage["rejected"]["identity_mismatch"] == 1
    assert all(row["features"][4:6] == [0, 0] for row in rows)
    assert "decode_1024" not in coverage["checkpoint_counts"]
