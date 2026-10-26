import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace as NS

import httpx
import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.causal_replay import compile_causal_replay
from beliefkv.experiments.causal_replay_native import RECEIPT_KEY
from scripts import run_causal_comparison as comparison
from scripts import run_causal_gpu_replay as runner
from tests.test_causal_replay import Trace
from tests.test_causal_replay_backend import response_payload
from tests.test_causal_replay_tokens import source, write_source


@pytest.mark.parametrize("enabled", (None, False, True))
def test_captured_session_replay_keeps_exact_work_and_stopping_with_optional_metrics(
    monkeypatch, tmp_path, enabled,
):
    trace = Trace()
    trace.add("start", Kind.WORKFLOW_START, 0.)
    trace.create("i", 1., context="c")
    for index in range(2):
        trace.model(
            f"r{index}", "i", 2. + index * 10., 4. + index * 10., 5. + index * 10.,
            epoch=index, context="c",
        )
    trace.returned("i", 16.)
    trace.add("end", Kind.WORKFLOW_END, 17.)
    trace.events = [
        replace(event, attributes={**event.attributes, "sampling_seed": 21})
        if event.kind is Kind.LLM_SUBMIT else event for event in trace.events
    ]
    for row in trace.native:
        row["attributes"].update(
            {"prompt_tokens": 5} if row["kind"] == "llm_submit" else {"output_tokens": 2},
        )
    plan = compile_causal_replay(trace.ordered(), trace.native)
    records, expected, _ = source(count=2, width=4)
    arm = tmp_path / "source"
    (arm / "server").mkdir(parents=True)
    write_source(arm / "server/request_tokens.jsonl", records)
    monkeypatch.setattr(runner, "source_plan", lambda _: (plan, expected, {}))
    real_client, corpus_type = httpx.AsyncClient, runner.CapturedTokenCorpus
    calls = []

    with corpus_type(arm / "server/request_tokens.jsonl", expected) as corpus:
        class Stream(httpx.AsyncByteStream):
            def __init__(self, request):
                self.request = request

            async def __aiter__(self):
                for count in (1, 2):
                    await asyncio.sleep(0)
                    payload = response_payload(corpus.get(self.request))
                    payload["meta_info"]["completion_tokens"] = count
                    if count == 1:
                        del payload["meta_info"][RECEIPT_KEY]
                    yield ("data: " + json.dumps(payload) + "\n\n").encode()
                yield b"data: [DONE]\n\n"

        async def handler(request):
            body = json.loads(request.content)
            calls.append((request.url.path, body))
            if request.url.path == "/close_session":
                return httpx.Response(200, json={})
            assert body["sampling_params"]["ignore_eos"] is False
            assert body["sampling_params"]["max_new_tokens"] == 32
            assert body["sampling_params"]["stop_token_ids"] == [9]
            assert body["stream"] is (enabled is True)
            if body["stream"]:
                return httpx.Response(200, stream=Stream(body["rid"]))
            return httpx.Response(200, json=response_payload(corpus.get(body["rid"])))

        monkeypatch.setattr(
            runner.httpx, "AsyncClient",
            lambda *args, **kwargs: real_client(
                *args, **kwargs, transport=httpx.MockTransport(handler),
            ),
        )
        args = NS(
            source_arm=arm, output_dir=tmp_path / "result",
            base_url="http://127.0.0.1:18000", event_socket=None, request_timeout_s=5.,
        )
        if enabled is not None:
            args.stream_response_metrics = enabled
        result = asyncio.run(runner.run(args))
    assert result["completed_requests"] == 2
    assert result["original_native_stopping_verified"] and result["exact_tokens_verified"]
    assert result["native_session_http_lifecycle_completed"]
    assert not result["published_length_stopping_verified"]
    assert len(result["session_closes"]) == 1
    assert [path for path, _ in calls] == ["/generate", "/generate", "/close_session"]
    common = {
        "stream_response_metrics": enabled is True, "source_plan": plan.summary(),
        "workflow_ids": ["w"],
    }
    comparison.validate_response_metrics(common, result)
    if enabled:
        metrics = result["streaming_response_metrics"]
        assert metrics["coverage"]["requests_with_output_timing"] == 2
        assert metrics["output_tokens"] == 4
        assert metrics["workflows"]["w"]["jct_ms"] == result["workflow_elapsed_ms"]["w"]
    else:
        assert result["streaming_response_metrics"] is None


@pytest.mark.parametrize("enabled", (False, True))
@pytest.mark.parametrize("label", ("full", "thunderagent", "native_matched"))
def test_frozen_response_protocol_applies_to_every_arm(tmp_path, enabled, label):
    plan = {"source_arm": "/source", "port": 18000, "stream_response_metrics": enabled}
    command = comparison.replay_command(
        plan, tmp_path / label, None if label == "native_matched" else tmp_path / "events.sock",
    )
    assert ("--stream-response-metrics" in command) is enabled
    assert command[command.index("--source-arm") + 1] == "/source"
    assert ("--event-socket" in command) is (label != "native_matched")


@pytest.mark.parametrize("change", (
    "flag", "disabled_with_metrics", "missing", "unfinished", "requests", "output_timing",
    "workflow", "workflow_requests", "output_tokens", "workflow_tokens", "jct",
))
def test_incomplete_or_mixed_protocol_metrics_cannot_pass_comparison_validation(change):
    from beliefkv.experiments.workflow_metrics import StreamingWorkflowMetrics
    from tests.test_workflow_metrics import event

    metrics = StreamingWorkflowMetrics()
    event(metrics, Kind.WORKFLOW_START, 0.)
    metrics.submit("r", "w", 1.)
    metrics.progress("r", 2, 2.)
    metrics.complete("r", 2, 3.)
    event(metrics, Kind.WORKFLOW_END, 4.)
    summary = metrics.summary()
    plan = {
        "stream_response_metrics": True, "workflow_ids": ["w"],
        "source_plan": {"requests": 1, "output_tokens": 2},
    }
    result = {
        "streaming_response_metrics_enabled": True, "streaming_response_metrics": summary,
        "workflow_elapsed_ms": {"w": 4.},
    }
    if change == "flag":
        result["streaming_response_metrics_enabled"] = False
    elif change == "disabled_with_metrics":
        plan["stream_response_metrics"] = False
        result["streaming_response_metrics_enabled"] = False
    elif change == "missing":
        del result["streaming_response_metrics"]
    elif change == "unfinished":
        summary["all_workflows_completed"] = False
    elif change in ("requests", "output_timing"):
        summary["coverage"]["completed_requests" if change == "requests" else "requests_with_output_timing"] = 0
    elif change == "workflow":
        summary["workflows"]["foreign"] = summary["workflows"].pop("w")
    elif change == "workflow_requests":
        summary["workflows"]["w"]["request_count"] = 0
    elif change == "output_tokens":
        summary["output_tokens"] = 1
    elif change == "workflow_tokens":
        summary["workflows"]["w"]["output_tokens"] = 1
    elif change == "jct":
        summary["workflows"]["w"]["jct_ms"] = 1.
    with pytest.raises(ValueError, match="streaming"):
        comparison.validate_response_metrics(plan, result)
