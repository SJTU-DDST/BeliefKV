import asyncio
import json
from contextlib import nullcontext
from dataclasses import replace

import httpx
import pytest

from beliefkv.experiments.agentix_model_probe import (
    FIXTURES, compare_unforced_outputs, fixture_policy, unforced_probe,
)
from beliefkv.core.events import RuntimeEventKind as Kind
from scripts import qualify_agentix_locobench_model as qualifier
from tests.test_paper_baseline_runtime import runtime


def test_parallel_unforced_calls_then_serial_independent_references_keep_one_program(monkeypatch):
    observed, bodies = [], {}
    active, peak, sequence = 0, 0, []
    fixture = replace(FIXTURES["roomy"], prompt_tokens=4, generated_tokens=2)
    live = runtime(monkeypatch)

    async def emit(event):
        observed.append(event)
        live.on_events((event,))

    async def generate(body):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        sequence.append(body["rid"])
        await asyncio.sleep(.001)
        active -= 1
        return body["input_ids"]

    def record(label, result, request, prompt_tokens):
        return {"output_token_ids": result[:2], "output_logprobs": [-1., -2.]}

    rows, sessions = asyncio.run(unforced_probe(
        [[1, 2, 3, 4], [2, 3, 4, 5], [3, 4, 5, 6]], fixture,
        generate=generate, emit=emit, session_for=lambda metadata: metadata.context_id,
        observe_body=lambda label, body: bodies.setdefault(label, body),
        record_response=record,
    ))
    assert peak == 3 and len(rows) == len(sessions) == 6
    assert all("actual" in identity for identity in sequence[:3])
    assert all("reference" in identity for identity in sequence[3:])
    assert len({
        bodies[f"actual-{index}"]["beliefkv_metadata"]["root_workflow_id"]
        for index in range(3)
    }) == 1
    assert len({body["extra_key"] for body in bodies.values()}) == 6
    assert all("custom_logit_processor" not in body for body in bodies.values())
    assert compare_unforced_outputs(rows)["actual_greedy_tokens_equal"]
    assert sum(event.kind is Kind.WORKFLOW_END for event in observed) == 4
    assert len(live.graph.invocations) == 6
    live.close()


@pytest.mark.parametrize("fault", ("missing", "tokens", "logprobs", "nan"))
def test_incomplete_or_numerically_incorrect_resumption_cannot_qualify(fault):
    rows = {
        f"{phase}-{index}": {"output_token_ids": [7], "output_logprobs": [-1.]}
        for phase in ("actual", "reference") for index in range(3)
    }
    if fault == "missing":
        rows.pop("actual-1")
    elif fault == "tokens":
        rows["actual-1"]["output_token_ids"] = [8]
    else:
        rows["actual-1"]["output_logprobs"] = [float("nan") if fault == "nan" else -2.]
    with pytest.raises(ValueError):
        compare_unforced_outputs(rows)


def test_pressure_policy_is_explicit_and_does_not_change_formal_defaults():
    document = qualifier.controls.load(qualifier.ROOT / qualifier.POLICY)
    pressure = fixture_policy(document, FIXTURES["pressure"])
    assert pressure["parameters"]["context_limit"] == 32768
    assert document["parameters"]["context_limit"] == 262144
    assert pressure["max_requests"] == 6 and pressure["parameters"]["running_limit"] == 1


@pytest.mark.parametrize("alive", (False, True))
def test_user_skip_requires_failed_tokencake_and_actual_owned_exit(tmp_path, monkeypatch, alive):
    path = tmp_path / "previous.json"
    prior = {"run_root": str(tmp_path)}
    state = {
        "status": "stopped_for_investigation", "completed_arms": [], "error": "native exception",
        "watcher": {"pid": 1}, "server": {"pid": 2}, "driver": {"pid": 3},
    }
    cleanup = {"remaining_processes": [], "finished_at": "done", "identities": [{"pid": 4}]}
    def load(requested):
        return prior if requested == path else state if str(requested).endswith(".state.json") else cleanup
    monkeypatch.setattr(qualifier.controls, "load", load)
    monkeypatch.setattr(qualifier.controls.reservation, "alive", lambda row: alive and row["pid"] == 4)
    assert qualifier.completed_predecessor(path, allow_failed=True) is not alive
    monkeypatch.setattr(qualifier.controls, "predecessor_ready", lambda _: False)
    assert qualifier.completed_predecessor(path, allow_failed=False) is False


def test_model_environment_isolates_full_and_uses_actual_agentix_engine(tmp_path, monkeypatch):
    config = qualifier.ROOT / "configs/baselines/kvflow_locobench_v230_v311_20261028.json"
    previous = {"reference_environment": json.loads(config.read_bytes())["common_environment"]}
    monkeypatch.setattr(qualifier.controls, "load", lambda _: previous)
    monkeypatch.setattr(qualifier, "git_source_state", lambda _: {"commit": "agentix-base"})
    monkeypatch.setattr(qualifier, "prefix_qualification_environment", lambda *_: {})
    plan = {"predecessor": "/previous", "application": "/frozen", "engine": "/native", "port": 18708}
    settings, flags, socket = qualifier.model_environment(
        plan, FIXTURES["roomy"], tmp_path / "policy.json", tmp_path,
    )
    assert settings["SGLANG_PATCH_FLAVOR"] == "staging"
    assert settings["SGLANG_SOURCE_BASE_COMMIT"] == "agentix-base"
    assert settings["BELIEFKV_ENABLE_TOOL_PREFETCH"] == "0"
    assert settings["BELIEFKV_AGENT_ADMISSION"] == "off"
    assert settings["BELIEFKV_PAPER_BASELINE"] == "agentix_atlas_native_qualification"
    assert settings["SGLANG_PLUGINS"] == "" and settings["BELIEFKV_CAUSAL_REPLAY_BRIDGE"] == "0"
    assert "--enable-custom-logit-processor" not in flags
    assert "--enable-deterministic-inference" in flags and socket is not None


def test_model_probe_drives_six_http_calls_with_real_session_leases(tmp_path, monkeypatch):
    requests, closed = [], []
    client_type = httpx.AsyncClient
    fixture = replace(FIXTURES["roomy"], prompt_tokens=4, generated_tokens=2)

    def handle(request):
        body = json.loads(request.content)
        if request.url.path == "/close_session":
            closed.append(body["session_id"])
            return httpx.Response(200, json={})
        assert request.url.path == "/generate"
        requests.append(body)
        return httpx.Response(200, json={"meta_info": {
            "id": body["rid"], "prompt_tokens": 4, "completion_tokens": 2,
            "finish_reason": {"type": "length", "length": 2},
            "output_token_logprobs": [[-1., 7], [-2., 8]],
        }})

    monkeypatch.setattr(qualifier.httpx, "AsyncClient", lambda **kwargs: client_type(
        **kwargs, transport=httpx.MockTransport(handle),
    ))
    class Sink:
        def emit_batch(self, events):
            pass
    monkeypatch.setattr(qualifier, "UnixDatagramRuntimeEventSink", lambda _: nullcontext(Sink()))
    result = asyncio.run(qualifier.model_probe(
        {"port": 18708}, fixture, tmp_path, tmp_path / "unused.sock",
        [[1, 2, 3, 4]] * 3, lambda: None,
    ))
    assert result["actual_greedy_tokens_equal"]
    assert len(requests) == len(closed) == len(set(closed)) == 6
    assert {body["session_id"] for body in requests} == set(closed)
    assert all(identity.startswith("beliefkv-") for identity in closed)
