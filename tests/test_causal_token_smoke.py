import asyncio
import json
from types import SimpleNamespace as NS

import httpx
import pytest

from beliefkv.experiments.causal_replay_tokens import CapturedTokenCorpus
from scripts import run_causal_token_smoke as smoke
from tests.test_causal_gpu_replay_driver import prepare
from tests.test_causal_replay_backend import response_payload


@pytest.mark.parametrize("contexts,turns", ((0, 1), (9, 1), (True, 1), (1, 0), (1, 4)))
def test_smoke_rejects_unbounded_or_implicit_limits(tmp_path, contexts, turns):
    arm, _ = prepare(tmp_path)
    plan, _, _ = smoke.source_plan(arm)
    with pytest.raises(ValueError):
        smoke.select_requests(plan, contexts=contexts, turns=turns)


@pytest.mark.parametrize("contexts,turns", ((2, 1), (1, 3)))
def test_smoke_does_not_substitute_other_work_when_predeclared_roots_or_turns_are_missing(
    tmp_path, contexts, turns,
):
    arm, _ = prepare(tmp_path)
    plan, _, _ = smoke.source_plan(arm)
    with pytest.raises(ValueError):
        smoke.select_requests(plan, contexts=contexts, turns=turns)


def test_smoke_preserves_exact_work_but_never_claims_a_complete_workflow_or_gpu_certificate(
    tmp_path, monkeypatch,
):
    arm, expected = prepare(tmp_path)
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
            smoke.httpx, "AsyncClient",
            lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
        )
        result = asyncio.run(smoke.run(NS(
            source_arm=arm, output_dir=tmp_path / "smoke",
            base_url="http://127.0.0.1:18546", contexts=1, turns=2,
        )))
    assert result["verified_requests"] == 2
    assert result["native_stop_requests"] == 2
    assert result["native_data_plane_sample_verified"]
    assert not result["causal_workflow_replay"]
    assert not result["gpu_process_verified"]
    assert not result["gpu_replay_qualified"] and not result["performance_accepted"]
    assert [path for path, _ in calls] == ["/generate", "/generate", "/close_session"]
