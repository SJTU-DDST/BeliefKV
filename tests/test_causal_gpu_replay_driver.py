import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import httpx
import pytest

from beliefkv.experiments.causal_replay_tokens import CapturedTokenCorpus
from beliefkv.runtime.replay_token_capture import capture_record, native_request_expectations
from scripts import run_causal_gpu_replay as driver
from tests.test_causal_replay import sequential_trace
from tests.test_causal_replay_backend import response_payload


def prepare(tmp_path):
    arm = tmp_path / "source"
    client = arm / "client_1"
    workflow = client / "workflows/w"
    workflow.mkdir(parents=True)
    (client / "summary.json").write_text(json.dumps({
        "workflow_count": 1, "completed_workflows": 1,
    }))
    trace = sequential_trace(original_service=1000.)
    events = [row.to_dict() for row in trace.ordered()]
    for row in events:
        if row["kind"] == "llm_submit":
            row["attributes"]["sampling_seed"] = 21
    (workflow / "runtime_events.deepagents.jsonl").write_text("".join(
        json.dumps(row) + "\n" for row in events
    ))
    server = arm / "server"
    server.mkdir()
    (server / "runtime_events.sglang.jsonl").write_text("".join(
        json.dumps(row) + "\n" for row in trace.native
    ))
    tokens = []
    for index, request in enumerate(("r0", "r1")):
        record = {
            "ts_ms": 1., "workflow_id": "w", "invocation_id": "root",
            "context_id": "ctx-root", "context_epoch": 0, "request_id": request,
            "session_id": "s", "session_generation": 1,
            "extra_key": None, "cache_salt": None,
            "_replay_request_parameters": {
                "max_new_tokens": 32, "min_new_tokens": 0, "ignore_eos": False,
                "stop": [], "stop_regex": [], "stop_token_ids": [9],
            },
            "_replay_finish_reason": {"type": "stop", "matched": 9},
        }
        tokens.extend((
            capture_record(
                record, [3] * 100_000, event="request_prompt",
                base_request_id="r0" if index else None,
                common_prefix_tokens=100_000 if index else 0,
            ),
            capture_record(record, [7, 8, 3, 9], event="request_output"),
        ))
    (server / "request_tokens.jsonl").write_text("".join(
        json.dumps(row) + "\n" for row in tokens
    ))
    return arm, native_request_expectations(trace.native)


def test_driver_executes_the_complete_causal_population_with_current_native_receipts(
    tmp_path, monkeypatch,
):
    arm, expected = prepare(tmp_path)
    output = tmp_path / "output"
    original_client = httpx.AsyncClient
    calls = []
    with CapturedTokenCorpus(arm / "server/request_tokens.jsonl", expected) as corpus:
        async def handler(request):
            body = json.loads(request.content)
            calls.append((request.url.path, body))
            return httpx.Response(200, json=(
                response_payload(corpus.get(body["rid"]))
                if request.url.path == "/generate" else {"accepted": True}
            ))

        monkeypatch.setattr(
            driver.httpx, "AsyncClient",
            lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
        )
        monkeypatch.setattr(sys, "argv", [
            "run_causal_gpu_replay", "--source-arm", str(arm),
            "--output-dir", str(output), "--base-url", "http://127.0.0.1:18546",
        ])
        driver.main()
    result = json.loads((output / "summary.json").read_bytes())
    assert result["completed_requests"] == 2 and result["exact_tokens_verified"]
    assert result["original_native_stopping_verified"]
    assert not result["server_source_and_gpu_qualified"]
    assert not result["gpu_replay_qualified"] and not result["performance_accepted"]
    assert len(result["workflow_elapsed_ms"]) == 1
    assert result["token_source"]["requests"] == 2
    assert len(result["session_closes"]) == 1
    receipts = [json.loads(row) for row in (output / "native_requests.jsonl").read_text().splitlines()]
    assert len(receipts) == 2
    assert all(row["native_receipt"]["output_token_ids"] == [7, 8, 3, 9] for row in receipts)
    emitted = [json.loads(row) for row in (output / "runtime_events.replay.jsonl").read_text().splitlines()]
    tools = [row for row in emitted if row["kind"] in ("tool_start", "tool_end")]
    assert "duration_ms" not in tools[0]["attributes"]
    assert tools[1]["attributes"]["duration_ms"] >= 10.
    assert [path for path, _ in calls] == ["/generate", "/generate", "/close_session"]


@pytest.mark.parametrize("change", ("unfinished", "missing_summary", "duplicate_summary", "population"))
def test_incomplete_source_is_rejected_before_contacting_a_server(tmp_path, change):
    arm, _ = prepare(tmp_path)
    summary = arm / "client_1/summary.json"
    if change == "unfinished":
        summary.write_text('{"workflow_count":1,"completed_workflows":0}')
    elif change == "missing_summary":
        summary.unlink()
    elif change == "duplicate_summary":
        other = arm / "client_2"
        other.mkdir()
        (other / "summary.json").write_bytes(summary.read_bytes())
    else:
        summary.write_text('{"workflow_count":2,"completed_workflows":2}')
    with pytest.raises(ValueError):
        driver.source_plan(arm)


def test_existing_output_is_preserved_when_an_attempt_is_refused(tmp_path):
    arm, _ = prepare(tmp_path)
    output = tmp_path / "existing"
    output.mkdir()
    marker = output / "summary.json"
    marker.write_text('{"original":true}')
    with pytest.raises(FileExistsError):
        asyncio.run(driver.run(NS(
            source_arm=arm, output_dir=output, base_url="http://127.0.0.1:18546",
            event_socket=None, request_timeout_s=30.,
        )))
    assert json.loads(marker.read_bytes()) == {"original": True}


def test_cli_refusal_does_not_add_failure_artifacts_to_an_existing_run(tmp_path, monkeypatch):
    arm, _ = prepare(tmp_path)
    output = tmp_path / "existing"
    output.mkdir()
    (output / "summary.json").write_text('{"original":true}')
    monkeypatch.setattr(sys, "argv", [
        "run_causal_gpu_replay", "--source-arm", str(arm),
        "--output-dir", str(output), "--base-url", "http://127.0.0.1:18546",
    ])
    with pytest.raises(SystemExit) as error:
        driver.main()
    assert error.value.code == 2
    assert sorted(path.name for path in output.iterdir()) == ["summary.json"]
