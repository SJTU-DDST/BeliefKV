import asyncio
import json
import time
from types import SimpleNamespace

import httpx

from beliefkv.experiments.agentx_tokens import AgentXTokenCorpus, prepare_agentx_tokens
from beliefkv.experiments.causal_replay_backend import request_payload, verify_native_receipt
from beliefkv.experiments.causal_replay_native import RECEIPT_KEY, terminal_receipt
from beliefkv.runtime.clock_evidence import local_monotonic_clock_domain
from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata
from scripts import run_causal_gpu_replay as runner
from tests.test_agentx_tokens import prepared_source, synthesizer
from tests.test_causal_replay_backend import native_request
from tests.test_causal_replay_native import native_root


def test_direct_published_source_executes_without_a_native_capture_and_reports_streaming_jct(
    monkeypatch, tmp_path,
):
    prepared, _, plan = prepared_source(tmp_path)
    tokens = tmp_path / "tokens"
    prepare_agentx_tokens(prepared, tokens, synthesizer(), corpus_evidence={"CPU_fixture": True})
    calls = []
    real_client = httpx.AsyncClient

    def native_payload(captured, count):
        req = native_request(captured)
        for name, value in captured.generation_parameters.items():
            setattr(req.sampling_params, name, value)
        req._beliefkv_native_sampler_preserved = True
        meta = {
            "id": captured.request_id, "prompt_tokens": len(captured.prompt_tokens),
            "completion_tokens": count,
            "finish_reason": captured.native_finish_reason,
        }
        if count == len(captured.output_tokens):
            meta[RECEIPT_KEY] = [terminal_receipt(req)]
        return {"meta_info": meta, "text": ""}

    with AgentXTokenCorpus(tokens, prepared) as corpus:
        class Stream(httpx.AsyncByteStream):
            def __init__(self, request):
                self.request = request

            async def __aiter__(self):
                captured = corpus.get(self.request)
                for count in (1, len(captured.output_tokens)):
                    await asyncio.sleep(0)
                    yield (
                        "data: " + json.dumps(native_payload(captured, count)) + "\n\n"
                    ).encode()
                yield b"data: [DONE]\n\n"

        async def handler(request):
            body = json.loads(request.content)
            calls.append((request.url.path, body))
            assert request.url.path == "/generate"
            assert body["stream"] is True and body["sampling_params"]["ignore_eos"] is True
            assert "recorded_api_s" not in body["beliefkv_metadata"]
            return httpx.Response(200, stream=Stream(body["rid"]))

        monkeypatch.setattr(
            runner.httpx, "AsyncClient",
            lambda *args, **kwargs: real_client(
                *args, **kwargs, transport=httpx.MockTransport(handler),
            ),
        )
        result = asyncio.run(runner.run(SimpleNamespace(
            source_arm=None, agentx_prepared=prepared, agentx_token_dir=tokens,
            base_url="http://127.0.0.1:18000", output_dir=tmp_path / "result",
            event_socket=None, request_timeout_s=5.,
        )))
    assert {body["rid"] for _, body in calls} == plan.request_usage.keys()
    assert len(calls) == result["completed_requests"] == 4
    assert result["native_source_capture_required"] is False
    assert result["original_native_stopping_verified"] is False
    assert result["published_length_stopping_verified"] is True
    assert result["benchmark_content_synthetic"] is True
    assert result["exact_tokens_verified"] is True
    assert result["streaming_response_metrics"]["all_workflows_completed"] is True
    assert result["streaming_response_metrics"]["workflow_ttft_ms"]["count"] == 2
    assert result["streaming_response_metrics"]["coverage"]["completed_requests"] == 4
    assert result["replay_timing"]["workflow_idle_cap_ms"] == 300_000.
    assert result["replay_timing"]["system_idle_cap_ms"] == 10_000.
    assert not result["performance_accepted"] and not result["gpu_replay_qualified"]


def test_agentx_stopping_matches_actual_native_sampling_parameters(monkeypatch, tmp_path):
    native_root(monkeypatch)
    from sglang.srt.sampling.sampling_params import SamplingParams

    prepared, _, plan = prepared_source(tmp_path)
    tokens = tmp_path / "tokens"
    prepare_agentx_tokens(prepared, tokens, synthesizer(), corpus_evidence={"CPU_fixture": True})
    submit = next(
        node.event for node in plan.nodes
        if node.event is not None and node.event.kind.value == "llm_submit"
    )
    with AgentXTokenCorpus(tokens, prepared) as corpus:
        captured = corpus.get(submit.attributes["request_id"])
    metadata = BeliefKVRequestMetadata(
        *captured.identity, full_prompt_replay_guaranteed=True,
    )
    payload = request_payload(captured, submit, metadata, processor="processor")
    req = native_request(captured)
    req.sampling_params = SamplingParams(**payload["sampling_params"])
    req._beliefkv_native_sampler_preserved = True
    assert req.sampling_params.stop_token_ids is None
    started = time.monotonic() * 1000.
    receipt = terminal_receipt(req)
    actual = verify_native_receipt(captured, {"meta_info": {
        "id": captured.request_id, "prompt_tokens": len(captured.prompt_tokens),
        "completion_tokens": len(captured.output_tokens),
        "finish_reason": captured.native_finish_reason, RECEIPT_KEY: [receipt],
    }}, submitted_ms=started, now_ms=time.monotonic() * 1000.,
        clock_domain=local_monotonic_clock_domain())
    assert actual.output_tokens == len(captured.output_tokens)
