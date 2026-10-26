import asyncio
from copy import deepcopy
from dataclasses import replace
import json
import time
from types import SimpleNamespace as NS

import httpx
import pytest

from beliefkv.core.events import (
    ContextMode, ExecutionMode, RelationType, RuntimeEvent, RuntimeEventKind as Kind,
)
from beliefkv.experiments.causal_replay_backend import (
    CausalHTTPBackend, request_payload, verify_native_receipt,
)
from beliefkv.experiments.causal_replay_native import RECEIPT_KEY, terminal_receipt
from beliefkv.experiments.causal_replay_sampling import PARAMETER_KEY
from beliefkv.experiments.causal_replay_tokens import CapturedTokenCorpus
from beliefkv.experiments.workflow_metrics import StreamingWorkflowMetrics
from beliefkv.runtime.clock_evidence import local_monotonic_clock_domain
from beliefkv.runtime.replay_token_capture import encode_tokens
from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata
from tests.test_causal_replay_tokens import source, write_source


def created():
    return RuntimeEvent(
        "create", time.monotonic() * 1000., Kind.INVOCATION_CREATE, "w",
        invocation_id="i", context_id="c", context_epoch=0,
        relation_type=RelationType.ROOT, context_mode=ContextMode.FRESH,
        execution_mode=ExecutionMode.FOREGROUND, agent_definition_id="explorer",
        agent_instance_id="i",
    )


def submitted(request="r0", epoch=0):
    return RuntimeEvent(
        "submit-" + request, time.monotonic() * 1000., Kind.LLM_SUBMIT, "w",
        invocation_id="i", context_id="c", context_epoch=epoch,
        attributes={"request_id": request, "sampling_seed": 21},
    )


def native_request(captured):
    parameters = captured.sampling_parameters
    return NS(
        rid=captured.request_id, origin_input_ids=list(captured.prompt_tokens),
        output_ids=list(captured.output_tokens), finished=lambda: True,
        finished_reason=NS(to_json=lambda: deepcopy(captured.native_finish_reason)),
        beliefkv_metadata=dict(zip(
            ("root_workflow_id", "invocation_id", "context_id", "context_epoch"),
            captured.identity,
        )),
        sampling_params=NS(
            **{key: parameters[key] for key in ("max_new_tokens", "min_new_tokens", "ignore_eos")},
            stop_strs=parameters["stop"], stop_regex_strs=parameters["stop_regex"],
            stop_token_ids=parameters["stop_token_ids"],
            custom_params={PARAMETER_KEY: {"request_id": captured.request_id}},
        ),
        session_id=captured.session_id, session_generation=captured.session_generation,
        extra_key=captured.extra_key, cache_salt=captured.cache_salt,
    )


def response_payload(captured):
    return {"text": "not used to infer tokens", "meta_info": {
        "id": captured.request_id, "prompt_tokens": len(captured.prompt_tokens),
        "completion_tokens": len(captured.output_tokens),
        "finish_reason": deepcopy(captured.native_finish_reason),
        RECEIPT_KEY: [terminal_receipt(native_request(captured))],
    }}


@pytest.fixture
def corpus(tmp_path):
    records, expected, _ = source(count=2, width=1000)
    path = tmp_path / "tokens.jsonl"
    write_source(path, records)
    with CapturedTokenCorpus(path, expected) as result:
        yield result


def test_payload_keeps_original_stopping_budget_seed_and_exact_token_work(corpus):
    captured = corpus.get("r0")
    metadata = BeliefKVRequestMetadata("w", "i", "c", 0, full_prompt_replay_guaranteed=True)
    payload = request_payload(captured, submitted(), metadata, processor="processor")
    assert payload["input_ids"] == list(captured.prompt_tokens)
    assert payload["sampling_params"]["max_new_tokens"] == 32
    assert payload["sampling_params"]["min_new_tokens"] == 0
    assert payload["sampling_params"]["ignore_eos"] is False
    assert payload["sampling_params"]["sampling_seed"] == 21
    assert payload["sampling_params"]["custom_params"][PARAMETER_KEY]["output_token_ids"] == [7, 9]
    assert payload["session_id"] == "s" and payload["extra_key"] == "ns"
    assert not payload["return_logprob"] and not payload["stream"]
    payload["sampling_params"]["stop_token_ids"].append(99)
    assert captured.sampling_parameters["stop_token_ids"] == [9]


@pytest.mark.parametrize("change", ("kind", "request", "context", "epoch", "metadata", "seed", "bool_seed"))
def test_payload_refuses_a_foreign_or_unseeded_ready_request(corpus, change):
    captured, submit = corpus.get("r0"), submitted()
    metadata = BeliefKVRequestMetadata("w", "i", "c", 0)
    if change == "kind":
        submit = replace(submit, kind=Kind.LLM_RESULT)
    elif change == "request":
        submit = replace(submit, attributes={"request_id": "other", "sampling_seed": 21})
    elif change == "context":
        submit = replace(submit, context_id="other")
    elif change == "epoch":
        submit = replace(submit, context_epoch=1)
    elif change == "metadata":
        metadata = replace(metadata, context_id="other")
    else:
        submit = replace(submit, attributes={
            "request_id": "r0", "sampling_seed": True if change == "bool_seed" else None,
        })
    with pytest.raises(ValueError):
        request_payload(captured, submit, metadata, processor="processor")


@pytest.mark.parametrize("change", (
    None, "missing", "duplicate", "request", "http_id", "context", "bool_epoch",
    "clock", "nan", "early", "future", "boundary", "schema",
    "prompt_digest", "prompt_length", "http_prompt", "output_tokens", "bool_token",
    "http_output", "budget", "ignore_eos", "finish", "http_finish",
    "session", "generation", "bool_generation", "namespace", "salt",
))
def test_receipt_requires_actual_exact_tokens_native_stopping_clock_and_session(corpus, change):
    captured = corpus.get("r1")
    started = time.monotonic() * 1000.
    payload = response_payload(captured)
    meta = payload["meta_info"]
    receipt = meta[RECEIPT_KEY][0]
    if change == "missing":
        del meta[RECEIPT_KEY]
    elif change == "duplicate":
        meta[RECEIPT_KEY].append(deepcopy(receipt))
    elif change == "request":
        receipt["request_id"] = "other"
    elif change == "http_id":
        meta["id"] = "other"
    elif change == "context":
        receipt["identity"][2] = "other"
    elif change == "bool_epoch":
        receipt["identity"][3] = True
    elif change == "clock":
        receipt["clock_domain"] = "other-boot"
    elif change == "nan":
        receipt["complete_monotonic_ms"] = float("nan")
    elif change == "early":
        receipt["complete_monotonic_ms"] = started - 1.
    elif change == "future":
        receipt["complete_monotonic_ms"] += 1000.
    elif change == "boundary":
        receipt["completion_boundary"] = "client_received"
    elif change == "schema":
        receipt["schema_version"] = True
    elif change == "prompt_digest":
        receipt["prompt_sha256"] = "0" * 64
    elif change == "prompt_length":
        receipt["prompt_tokens"] -= 1
    elif change == "http_prompt":
        meta["prompt_tokens"] += 1
    elif change == "output_tokens":
        receipt["output_token_ids"][0] = 8
    elif change == "bool_token":
        receipt["output_token_ids"][0] = True
    elif change == "http_output":
        meta["completion_tokens"] -= 1
    elif change == "budget":
        receipt["sampling_parameters"]["max_new_tokens"] = 2
    elif change == "ignore_eos":
        receipt["sampling_parameters"]["ignore_eos"] = 0
    elif change == "finish":
        receipt["native_finish_reason"] = {"type": "length", "length": 2}
    elif change == "http_finish":
        meta["finish_reason"] = {"type": "abort"}
    elif change == "session":
        receipt["session_id"] = "other"
    elif change == "generation":
        receipt["session_generation"] = 2
    elif change == "bool_generation":
        receipt["session_generation"] = True
    elif change == "namespace":
        receipt["extra_key"] = "other"
    elif change == "salt":
        receipt["cache_salt"] = "other"
    kwargs = dict(
        submitted_ms=started, now_ms=time.monotonic() * 1000.,
        clock_domain=local_monotonic_clock_domain(),
    )
    if change is None:
        actual = verify_native_receipt(captured, payload, **kwargs)
        assert (actual.prompt_tokens, actual.output_tokens) == (1001, 2)
        assert actual.complete_monotonic_ms == receipt["complete_monotonic_ms"]
    else:
        with pytest.raises(ValueError):
            verify_native_receipt(captured, payload, **kwargs)


def test_http_execution_and_terminal_close_use_native_receipts_without_reading_logs(corpus):
    emitted, calls, observations = [], [], []

    async def run():
        async def handler(request):
            body = __import__("json").loads(request.content)
            calls.append((request.url.path, body))
            if request.url.path == "/generate":
                return httpx.Response(200, json=response_payload(corpus.get(body["rid"])))
            return httpx.Response(200, json={"accepted": True})

        async with httpx.AsyncClient(
            base_url="http://127.0.0.1", transport=httpx.MockTransport(handler),
        ) as client:
            backend = CausalHTTPBackend(corpus, client, emitted.append, observe_request=observations.append)
            await backend.emit(created())
            for index in range(2):
                submit = submitted(f"r{index}", index)
                await backend.emit(submit)
                receipt = await backend.execute(f"r{index}", submit)
                assert receipt.request_id == f"r{index}"
            await backend.emit(RuntimeEvent(
                "return", time.monotonic() * 1000., Kind.RETURN, "w",
                invocation_id="i", context_id="c",
            ))
            await backend.drain()
            assert backend.verified_requests == {"r0", "r1"}
            assert len(backend.session_closes) == 1
            assert not backend.session_closes[0]["physical_release_acknowledged"]
            with pytest.raises(ValueError, match="terminal"):
                await backend.execute("r1", submitted("r1", 1))

    asyncio.run(run())
    assert [path for path, _ in calls] == ["/generate", "/generate", "/close_session"]
    assert len(emitted) == 4 and len(observations) == 2
    assert all(row["exact_tokens_verified"] for row in observations)


def test_streaming_backend_measures_output_and_checks_the_same_native_terminal_receipt(corpus):
    calls, observations = [], []
    metrics = StreamingWorkflowMetrics()

    class OutputStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for count in (0, 1, 2):
                await asyncio.sleep(.001)
                payload = response_payload(corpus.get("r0"))
                payload["meta_info"]["completion_tokens"] = count
                if count < 2:
                    del payload["meta_info"][RECEIPT_KEY]
                yield ("data: " + json.dumps(payload) + "\n\n").encode()
            yield b"data: [DONE]\n\n"

    async def run():
        async def handler(request):
            body = json.loads(request.content)
            calls.append((request.url.path, body))
            if request.url.path == "/generate":
                assert body["stream"] is True
                return httpx.Response(200, stream=OutputStream())
            return httpx.Response(200, json={})

        async with httpx.AsyncClient(
            base_url="http://127.0.0.1", transport=httpx.MockTransport(handler),
        ) as client:
            backend = CausalHTTPBackend(
                corpus, client, lambda events: None, observe_request=observations.append,
                streaming=True, response_metrics=metrics,
            )
            await backend.emit(RuntimeEvent(
                "start", time.monotonic() * 1000., Kind.WORKFLOW_START, "w",
            ))
            await backend.emit(created())
            submit = submitted()
            await backend.emit(submit)
            await backend.execute("r0", submit)
            await backend.emit(RuntimeEvent(
                "end", time.monotonic() * 1000., Kind.WORKFLOW_END, "w",
            ))
            await backend.drain()

    asyncio.run(run())
    row = observations[0]["response_metrics"]
    assert row["output_tokens"] == row["output_chunks"] == 2
    assert row["ttft_ms"] > 0 and row["interactivity_tps"] > 0
    assert row["first_output_monotonic_ms"] < row["last_output_monotonic_ms"]
    assert metrics.summary()["all_workflows_completed"]
    assert metrics.summary()["coverage"]["completed_requests"] == 1
    assert [path for path, _ in calls] == ["/generate", "/close_session"]


def test_response_metrics_require_streaming_output(corpus):
    with pytest.raises(ValueError, match="streaming"):
        CausalHTTPBackend(corpus, None, None, response_metrics=StreamingWorkflowMetrics())


@pytest.mark.parametrize("changed", (False, True))
def test_backend_binds_fresh_native_generation_and_rejects_changes_inside_one_incarnation(
    corpus, changed,
):
    calls = []

    async def run():
        async def handler(request):
            body = __import__("json").loads(request.content)
            calls.append((request.url.path, body))
            if request.url.path == "/generate":
                payload = response_payload(corpus.get(body["rid"]))
                payload["meta_info"][RECEIPT_KEY][0]["session_generation"] = (
                    78 if changed and body["rid"] == "r1" else 77
                )
                return httpx.Response(200, json=payload)
            return httpx.Response(200, json={"accepted": True})

        async with httpx.AsyncClient(
            base_url="http://127.0.0.1", transport=httpx.MockTransport(handler),
        ) as client:
            backend = CausalHTTPBackend(corpus, client, lambda events: None)
            await backend.emit(created())
            await backend.execute("r0", submitted())
            if changed:
                with pytest.raises(ValueError, match="session"):
                    await backend.execute("r1", submitted("r1", 1))
            else:
                await backend.execute("r1", submitted("r1", 1))
            assert backend.session_generation_bindings == [{
                "session_id": "s", "source_generation": 1, "native_generation": 77,
            }]
            await backend.drain()
    asyncio.run(run())
    assert [body for path, body in calls if path == "/abort_request"] == (
        [{"rid": "r1"}] if changed else []
    )


@pytest.mark.parametrize("changed_inside_activation", (False, True))
def test_reusable_activation_can_rebind_after_physical_loss_but_not_during_execution(
    tmp_path, changed_inside_activation,
):
    records, expected, _ = source(count=3, width=16)
    for row in records[2:]:
        row["invocation_id"] = "resumed"
    for request in ("r1", "r2"):
        expected[request] = {
            kind: ((identity[0], "resumed", *identity[2:]), tokens)
            for kind, (identity, tokens) in expected[request].items()
        }
    path = tmp_path / "reusable.jsonl"
    write_source(path, records)
    calls = []

    async def run(corpus):
        async def handler(request):
            body = __import__("json").loads(request.content)
            calls.append((request.url.path, body))
            if request.url.path == "/generate":
                payload = response_payload(corpus.get(body["rid"]))
                payload["meta_info"][RECEIPT_KEY][0]["session_generation"] = (
                    77 if body["rid"] == "r0" else
                    79 if changed_inside_activation and body["rid"] == "r2" else 78
                )
                return httpx.Response(200, json=payload)
            return httpx.Response(200, json={"accepted": True})

        async with httpx.AsyncClient(
            base_url="http://127.0.0.1", transport=httpx.MockTransport(handler),
        ) as client:
            backend = CausalHTTPBackend(corpus, client, lambda events: None)
            await backend.emit(created())
            await backend.execute("r0", submitted())
            await backend.emit(RuntimeEvent(
                "dormant", time.monotonic() * 1000., Kind.RETURN, "w",
                invocation_id="i", context_id="c", context_epoch=0,
                attributes={
                    "reusable_subagent": True, "agent_handle": "handle",
                    "context_retired": False,
                },
            ))
            assert not backend.session_closes
            await backend.emit(replace(
                created(), event_id="resume", invocation_id="resumed",
                attributes={"reusable_subagent": True, "resumed": True, "agent_handle": "handle"},
            ))
            await backend.execute("r1", replace(submitted("r1", 1), invocation_id="resumed"))
            assert backend.session_generation_rebindings == [{
                "workflow_id": "w", "context_id": "c", "invocation_id": "resumed",
                "request_id": "r1", "session_id": "s", "source_generation": 1,
                "previous_native_generation": 77, "native_generation": 78,
                "reason": "first_request_of_observed_reusable_activation",
            }]
            final_submit = replace(submitted("r2", 2), invocation_id="resumed")
            if changed_inside_activation:
                with pytest.raises(ValueError, match="session"):
                    await backend.execute("r2", final_submit)
            else:
                await backend.execute("r2", final_submit)
            await backend.drain()

    with CapturedTokenCorpus(path, expected) as corpus:
        asyncio.run(run(corpus))
    assert sum(url == "/close_session" for url, _ in calls) == 1


def test_explicit_subagent_close_retires_dormant_session_without_closing_the_workflow(corpus):
    calls = []

    async def run():
        async def handler(request):
            body = __import__("json").loads(request.content)
            calls.append((request.url.path, body))
            return httpx.Response(200, json=(
                response_payload(corpus.get(body["rid"]))
                if request.url.path == "/generate" else {"accepted": True}
            ))

        async with httpx.AsyncClient(
            base_url="http://127.0.0.1", transport=httpx.MockTransport(handler),
        ) as client:
            backend = CausalHTTPBackend(corpus, client, lambda events: None)
            await backend.emit(created())
            await backend.execute("r0", submitted())
            await backend.emit(RuntimeEvent(
                "return", time.monotonic() * 1000., Kind.RETURN, "w",
                invocation_id="i", context_id="c", context_epoch=0,
                attributes={
                    "reusable_subagent": True, "agent_handle": "handle", "context_retired": False,
                },
            ))
            await backend.emit(RuntimeEvent(
                "close", time.monotonic() * 1000., Kind.STRUCTURED_ACTION, "w",
                invocation_id="i", context_id="c", context_epoch=0,
                attributes={"subagent_context_close": True, "agent_handle": "handle"},
            ))
            with pytest.raises(ValueError, match="terminal"):
                await backend.execute("r1", submitted("r1", 1))
            assert not backend._closed_workflows
            await backend.drain()

    asyncio.run(run())
    assert [url for url, _ in calls] == ["/generate", "/close_session"]


@pytest.mark.parametrize("generation", (True, 0, -1, "1"))
def test_a_session_generation_binding_cannot_be_a_boolean_or_untyped_handle(corpus, generation):
    captured = corpus.get("r0")
    started = time.monotonic() * 1000.
    payload = response_payload(captured)
    payload["meta_info"][RECEIPT_KEY][0]["session_generation"] = generation
    with pytest.raises(ValueError, match="binding"):
        verify_native_receipt(
            captured, payload, submitted_ms=started, now_ms=time.monotonic() * 1000.,
            clock_domain=local_monotonic_clock_domain(),
            expected_session_generation=generation,
        )


@pytest.mark.parametrize("payload", ([], None, {"meta_info": []}))
def test_backend_rejects_malformed_responses_without_binding_a_session(corpus, payload):
    async def run():
        async def handler(request):
            return httpx.Response(
                200, content=__import__("json").dumps(payload),
                headers={"content-type": "application/json"},
            )

        async with httpx.AsyncClient(
            base_url="http://127.0.0.1", transport=httpx.MockTransport(handler),
        ) as client:
            backend = CausalHTTPBackend(corpus, client, lambda events: None)
            await backend.emit(created())
            with pytest.raises(ValueError, match="terminal replay receipt"):
                await backend.execute("r0", submitted())
            assert backend.session_generation_bindings == []
            assert backend.verified_requests == set()
            await backend.drain()
    asyncio.run(run())


def test_compaction_closes_old_session_before_next_submission_and_preserves_new_scope(tmp_path):
    records, expected, prompts = source(count=2, width=4)
    for row in records[2:]:
        row["session_id"] = "new-session"
    records[2].update(encode_tokens(prompts["r1"]))
    path = tmp_path / "tokens.jsonl"
    write_source(path, records)
    calls = []

    async def run(corpus):
        async def handler(request):
            body = __import__("json").loads(request.content)
            calls.append((request.url.path, body))
            return httpx.Response(200, json=(
                response_payload(corpus.get(body["rid"]))
                if request.url.path == "/generate" else {"accepted": True}
            ))

        async with httpx.AsyncClient(
            base_url="http://127.0.0.1", transport=httpx.MockTransport(handler),
        ) as client:
            backend = CausalHTTPBackend(corpus, client, lambda events: None)
            await backend.emit(created())
            await backend.execute("r0", submitted())
            await backend.emit(RuntimeEvent(
                "compact", time.monotonic() * 1000., Kind.CONTEXT_COMPACT, "w",
                invocation_id="i", context_id="c", context_epoch=1,
            ))
            assert calls[-1] == ("/close_session", {"session_id": "s"})
            await backend.execute("r1", submitted("r1", 1))
            await backend.drain()

    with CapturedTokenCorpus(path, expected) as corpus:
        asyncio.run(run(corpus))
    assert [path for path, _ in calls] == ["/generate", "/close_session", "/generate", "/close_session"]
    assert calls[-1][1]["session_id"] == "new-session"


@pytest.mark.parametrize("failure", ("http_error", "bad_receipt", "cancel", "abort_error", "close_error"))
def test_failure_or_cancellation_aborts_only_its_owned_request_and_drains_sessions(corpus, failure):
    calls = []

    async def run():
        posted = asyncio.Event()

        async def handler(request):
            body = __import__("json").loads(request.content)
            calls.append((request.url.path, body))
            if request.url.path == "/generate":
                if failure == "cancel":
                    posted.set()
                    await asyncio.Event().wait()
                if failure in ("http_error", "abort_error"):
                    return httpx.Response(500)
                payload = response_payload(corpus.get("r0"))
                if failure == "bad_receipt":
                    payload["meta_info"][RECEIPT_KEY][0]["prompt_sha256"] = "bad"
                return httpx.Response(200, json=payload)
            if request.url.path == "/abort_request" and failure == "abort_error":
                return httpx.Response(503)
            if request.url.path == "/close_session" and failure == "close_error":
                return httpx.Response(503)
            return httpx.Response(200, json={"accepted": True})

        async with httpx.AsyncClient(
            base_url="http://127.0.0.1", transport=httpx.MockTransport(handler),
        ) as client:
            backend = CausalHTTPBackend(corpus, client, lambda events: None)
            await backend.emit(created())
            if failure == "cancel":
                task = asyncio.create_task(backend.execute("r0", submitted()))
                await posted.wait()
                with pytest.raises(ValueError, match="concurrent"):
                    await backend.execute("r1", submitted("r1", 1))
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            elif failure == "close_error":
                await backend.execute("r0", submitted())
            else:
                with pytest.raises((ValueError, httpx.HTTPStatusError)):
                    await backend.execute("r0", submitted())
            if failure in ("abort_error", "close_error"):
                with pytest.raises((ValueError, httpx.HTTPStatusError)):
                    await backend.drain()
            else:
                await backend.drain()
            if failure != "close_error":
                assert not backend.verified_requests

    asyncio.run(run())
    aborts = [body for path, body in calls if path == "/abort_request"]
    assert aborts == ([] if failure == "close_error" else [{"rid": "r0"}])
    assert [body for path, body in calls if path == "/close_session"] == [{"session_id": "s"}]
