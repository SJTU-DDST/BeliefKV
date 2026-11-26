import asyncio
from array import array
from dataclasses import replace
import importlib
import json
from pathlib import Path
import time
from types import SimpleNamespace

import httpx
import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.causal_replay import compile_causal_replay, execute_causal_replay
from beliefkv.experiments.causal_replay_tokens import CapturedReplayRequest
from beliefkv.experiments.paper_baseline_native import MARS_OUTPUT_KEY, MORI_RECEIPT_KEY, token_sha256
from beliefkv.experiments.paper_baseline_replay import (
    NativeOutput, PaperReplayBackend, baseline_payload, program_id,
)
from beliefkv.experiments.paper_mars_admission import MarsAdmission
from beliefkv.experiments.workflow_metrics import StreamingWorkflowMetrics
from beliefkv.runtime.clock_evidence import local_monotonic_clock_domain
from scripts import run_paper_author_baseline
from tests.test_causal_replay import Trace


WORKTREES = Path(__file__).resolve().parents[2] / ".worktrees"


@pytest.fixture
def captured():
    prompt, output = array("I", [3, 4]), array("I", [7, 9])
    return CapturedReplayRequest(
        "r1", ("root", "invocation", "context", 0), prompt, output,
        token_sha256(prompt), token_sha256(output),
        {"max_new_tokens": 32, "min_new_tokens": 0, "ignore_eos": False,
         "stop": [], "stop_regex": [], "stop_token_ids": None},
        {"type": "stop", "matched": 9}, "session", 1, None, None,
        {"sampling_seed": 21, "temperature": 0.7, "top_k": 20, "top_p": 0.8},
    )


def test_payload_uses_original_token_budgets_and_program_identity(captured):
    mars = baseline_payload("mars", "qwen", captured)
    mori = baseline_payload("mori", "qwen", captured, "processor")
    assert mars["prompt"] == mori["input_ids"] == [3, 4]
    assert mars["max_tokens"] == mori["sampling_params"]["max_new_tokens"] == 32
    assert json.loads(mars["vllm_xargs"][MARS_OUTPUT_KEY]) == [7, 9]
    assert mars["job_id"] == "root"
    assert mori["program_id"] == program_id(captured.identity, "session")
    assert mori["program_id"] == program_id(("root", "next-activation", "context", 1), "session")
    assert mori["program_id"] != program_id(("root", "child", "child-context", 0), "other")


def test_cohort_summary_requires_receipts_for_completed_warmup(monkeypatch):
    monkeypatch.setattr(run_paper_author_baseline, "REPORT_RECEIPTS", {1: (1, 7), 2: (2, 10)})
    cohort = {
        "measured_workflow_ids": ["measured"], "warmup_workflow_ids": ["warmup"],
        "throughput_window_offset_seconds": [10., 20.],
    }
    arrivals = {"arrival_schedule": [
        {"workflow_id": "warmup", "offset_seconds": 0.},
        {"workflow_id": "measured", "offset_seconds": 10.},
        {"workflow_id": "background", "offset_seconds": 20.},
    ]}
    observer = run_paper_author_baseline.CohortObserver(None, cohort, arrivals)
    observer.origin = 100.
    observer.arrivals = {key: observer.origin + offset for key, offset in observer.offsets.items()}
    observer.completed = {"measured": 11000.}
    expected = {"r1": {"llm_submit": [("measured",)]}, "r2": {"llm_submit": [("warmup",)]}}
    usage = {"r1": (20, 7), "r2": (15, 3)}

    partial = observer.summary(expected, usage, {"r1"})
    assert partial["groups"]["fixed38"]["missing"] == ["warmup"]
    assert partial["groups"]["fixed38"]["complete_jct_ms"] is None
    assert partial["background_submission_coverage"]["uncovered_seconds_by_measured_workflow"] == {
        "measured": 1.,
    }

    observer.completed["warmup"] = 11000.
    with pytest.raises(ValueError, match="fixed38 native receipts"):
        observer.summary(expected, usage, {"r1"})
    complete = observer.summary(expected, usage, {"r1", "r2"})
    assert complete["groups"]["fixed38"]["verified_output_tokens_in_completed_workflows"] == 10


@pytest.mark.parametrize("arm", ("mars", "mori"))
@pytest.mark.parametrize("broken", ("none", "missing_delta", "wrong_order", "wrong_stop", "wrong_prompt"))
def test_validate_native_coalesced_receipt(captured, arm, broken):
    deltas = [[20., 1], [21., 1]]
    if broken == "wrong_order":
        deltas.reverse()
    elif broken == "missing_delta":
        deltas.pop()
    prompt = [3, 5] if broken == "wrong_prompt" else [3, 4]
    finish = "length" if broken == "wrong_stop" else "stop"
    evidence = {"deltas": deltas, "clock_domain": local_monotonic_clock_domain()}
    if arm == "mars":
        chunk = {
            "id": "cmpl-r1", "beliefkv_native_output": evidence,
            "choices": [{"index": 0, "token_ids": [7, 9], "prompt_token_ids": prompt,
                         "finish_reason": finish}],
        }
    else:
        evidence.update(
            program_id="p1", sampler_preserved=True, write_back_max_nodes=1,
            prompt_sha256=token_sha256(prompt),
        )
        chunk = {"output_ids": [7, 9], "meta_info": {
            "id": "r1", MORI_RECEIPT_KEY: evidence, "cached_tokens": 1,
            "prompt_tokens": 2, "completion_tokens": 2,
            "finish_reason": {"type": finish, "matched": 9},
        }}
    output = NativeOutput(arm, captured, "p1", 10.)

    def verify():
        assert output.consume(chunk, 25.) == deltas
        if arm == "mars":
            output.consume({"id": "cmpl-r1", "choices": [],
                            "usage": {"prompt_tokens": 2, "completion_tokens": 2}}, 26.)
        return output.verify()

    if broken == "none":
        receipt = verify()
        assert receipt.complete_monotonic_ms == 21.
        assert receipt.output_tokens == 2
    else:
        with pytest.raises(ValueError):
            verify()


def test_mars_post_sample_uses_committed_position_after_chunked_prefill():
    torch = pytest.importorskip("torch")
    from beliefkv.experiments.paper_baseline_native import replace_mars_samples

    request = SimpleNamespace(
        sampling_params=SimpleNamespace(extra_args={MARS_OUTPUT_KEY: "[7,9]"}),
        num_tokens=3, output_token_ids=[7],
    )
    runner = SimpleNamespace(
        input_batch=SimpleNamespace(req_ids=["r1"]),
        requests={"r1": request}, seq_lens=SimpleNamespace(np=[2]),
        use_async_scheduling=False,
    )
    sampled = torch.tensor([[8]])
    replace_mars_samples(runner, sampled, speculative=False)
    assert sampled.item() == 8
    runner.seq_lens.np[0] = 3
    replace_mars_samples(runner, sampled, speculative=False)
    assert sampled.item() == 9


def test_mars_author_policy_retains_root_slot_until_workflow_end():
    checkout = WORKTREES / "mars-paper-replay-20261123"
    if not checkout.is_dir():
        pytest.skip("optional MARS author checkout unavailable")

    async def run():
        wire = httpx.MockTransport(lambda request: httpx.Response(200, json={
            "block_size": 16, "num_gpu_blocks": 50325, "num_free_blocks": 50324,
            "total_tokens": 805184, "scheduling_policy": "mars", "max_num_seqs": 24,
            "usage": 0.,
        }))
        grants = []
        async with httpx.AsyncClient(base_url="http://127.0.0.1:19140", transport=wire) as client:
            admission = MarsAdmission(checkout, client, grants.append, device_tokens=805184, deadline_s=2.)
            for idx in range(9):
                admission.arrived(str(idx))
            try:
                for idx in range(8):
                    await admission.enter(str(idx), root=True, prompt_tokens=8192)
                waiting = asyncio.create_task(admission.enter("8", root=True, prompt_tokens=8192))
                await asyncio.sleep(0)
                assert not waiting.done()
                # A child of an admitted root does not consume another slot.
                await admission.enter("0", root=False, prompt_tokens=4096)
                assert len(admission.active) == 8
                admission.ended("0")
                await waiting
            finally:
                await admission.close()
        assert [row["workflow_id"] for row in grants if row["event"] == "mars_admit"] == list(map(str, range(9)))

    asyncio.run(run())


def test_mori_author_router_uses_native_input_length_and_keeps_size_order(monkeypatch):
    checkout = WORKTREES / "mori-paper-replay-20261123"
    if not checkout.is_dir():
        pytest.skip("optional MORI author checkout unavailable")
    monkeypatch.syspath_prepend(str(checkout / "ThunderAgent"))
    module = importlib.import_module("ThunderAgent.scheduler.router")
    assert Path(module.__file__).is_relative_to(checkout)

    async def run():
        url = "http://127.0.0.1:19141"
        router = module.MultiBackendRouter(url, backend_type="sglang")
        backend = router.get_default_backend()
        backend.metrics_client.cache_config = SimpleNamespace(
            total_tokens_capacity=1000, cpu_total_tokens_capacity=2200,
        )
        seen = []
        await router.client.aclose()
        def response(request):
            seen.append(request.url.path)
            return httpx.Response(200, json={"success": True})

        router.client = httpx.AsyncClient(transport=httpx.MockTransport(response))
        try:
            requests = [("active", 850), ("larger", 700), ("smaller", 160)]
            states = {pid: router.get_or_create_program(pid) for pid, _ in requests}
            tasks = [asyncio.create_task(router.update_program_before_request(
                pid, states[pid], {}, replay_prompt_tokens=tokens, replay_prefix_group=pid,
            )) for pid, tokens in requests]
            await asyncio.sleep(0)
            assert tasks[0].result() is True
            assert set(router.global_waiting_queue) == {"larger", "smaller"}
            router._reestimate_parked()
            assert states["larger"].total_tokens == 700
            assert await router.release_program("active")
            marks = router._step_admit_waiting(0.)
            assert marks == [(url, "smaller", "INTERACTIVE")]
            assert await tasks[2] is True and not tasks[1].done()
            tasks[1].cancel()
            await asyncio.gather(tasks[1], return_exceptions=True)
            assert await router.release_program("larger")
            assert await router.release_program("smaller")
        finally:
            await router.stop()
        assert seen == ["/release_program", "/release_program"]

    asyncio.run(run())


@pytest.mark.parametrize("arm", ("mars", "mori"))
def test_author_replay_runs_child_before_join_successor(captured, arm):
    trace = Trace("workflow")
    trace.add("start", Kind.WORKFLOW_START, 0)
    trace.create("root", 1)
    trace.model("root-first", "root", 2, 3, 4)
    trace.create("child", 5, parent="root")
    trace.launch("child", "root", 6)
    trace.join("join", "root", ("child",), 7, 12)
    trace.model("child-last", "child", 8, 9, 10)
    trace.add("child-return", Kind.RETURN, 11, invocation_id="child", context_id="ctx-child")
    trace.model("root-last", "root", 13, 14, 15)
    trace.returned("root", 16)
    trace.add("finish", Kind.WORKFLOW_END, 17)
    for row in trace.native:
        attributes = row["attributes"]
        key = "prompt_tokens" if row["kind"] == "llm_submit" else "output_tokens"
        attributes[key] = 2
    events = [
        replace(event, attributes={**event.attributes, "sampling_seed": 21})
        if event.kind is Kind.LLM_SUBMIT else event for event in trace.ordered()
    ]
    plan = compile_causal_replay(events, trace.native)
    corpus = {}
    for event in events:
        if event.kind is Kind.LLM_SUBMIT:
            request = event.attributes["request_id"]
            corpus[request] = replace(
                captured, request_id=request, session_id="session-" + event.context_id,
                identity=(event.workflow_id, event.invocation_id, event.context_id, event.context_epoch),
            )
    observed, releases, estimates = [], [], []

    async def run():
        base = "http://127.0.0.1:19143"

        async def before(pid, state, payload, *, replay_prompt_tokens, replay_prefix_group):
            assert payload == {}
            estimates.append((pid, replay_prompt_tokens))
            return True

        async def release(pid):
            releases.append(pid)
            return True

        async def grant(workflow, *, root, prompt_tokens):
            assert workflow == "workflow" and prompt_tokens == 2

        async def close():
            pass

        def generate(http_request):
            body = json.loads(http_request.content)
            stamp = time.monotonic() * 1000.
            evidence = {"deltas": [[stamp, 2]], "clock_domain": local_monotonic_clock_domain()}
            if arm == "mars":
                request = body["request_id"]
                assert body["job_id"] == "workflow"
                chunks = [{
                    "id": "cmpl-" + request, "beliefkv_native_output": evidence,
                    "choices": [{"index": 0, "token_ids": [7, 9], "prompt_token_ids": [3, 4],
                                 "finish_reason": "stop"}],
                }, {
                    "id": "cmpl-" + request, "choices": [],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 2},
                }]
            else:
                request = body["rid"]
                evidence.update(
                    program_id=body["program_id"], sampler_preserved=True,
                    write_back_max_nodes=1, prompt_sha256=captured.prompt_sha256,
                )
                chunks = [{"output_ids": [7, 9], "meta_info": {
                    "id": request, "prompt_tokens": 2, "completion_tokens": 2,
                    "finish_reason": {"type": "stop", "matched": 9}, "cached_tokens": 0,
                    MORI_RECEIPT_KEY: evidence,
                }}]
            observed.append(request)
            return httpx.Response(200, text="".join(
                f"data: {json.dumps(chunk)}\n\n" for chunk in chunks
            ) + "data: [DONE]\n\n")

        mars = SimpleNamespace(arrived=lambda _: None, ended=lambda _: None, enter=grant, close=close)
        mori = SimpleNamespace(
            update_program_before_request=before, release_program=release,
            get_or_create_program=lambda _: SimpleNamespace(backend_url=base),
            update_program_after_request=lambda *_: None, record_cached_tokens=lambda *_: None,
        )
        async with httpx.AsyncClient(base_url=base, transport=httpx.MockTransport(generate)) as client:
            receipts = []
            backend = PaperReplayBackend(
                arm, corpus, client, SimpleNamespace(emit_batch=lambda _: None),
                receipts.append, lambda _: None, lambda _: None, StreamingWorkflowMetrics(),
                model="qwen", mars=mars if arm == "mars" else None, mori=mori if arm == "mori" else None,
            )
            result = await execute_causal_replay(plan, backend.execute, backend.emit)
            await backend.drain()
            assert result["completed_requests"] == len(receipts) == 3
        assert observed == ["root-first", "child-last", "root-last"]
        if arm == "mori":
            assert estimates[0] == estimates[2]
            assert estimates[0][0] != estimates[1][0]
            assert releases == [estimates[1][0], estimates[0][0]]

    asyncio.run(run())
