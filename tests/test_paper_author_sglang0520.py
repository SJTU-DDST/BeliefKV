import asyncio
from copy import deepcopy
import json
from pathlib import Path
import time
from types import SimpleNamespace

import httpx
import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.causal_replay_sampling import PARAMETER_KEY
from beliefkv.experiments.paper_baseline_native import token_sha256
from beliefkv.experiments.paper_baseline_replay import NativeOutput, PaperReplayBackend, baseline_payload, program_id
from beliefkv.experiments.paper_baseline_sglang import (
    ARM_ENV, EMIT_KEY, ENGINE, IDENTITY_FIELDS, MARS_AUTHOR_ENV, MORI_CAP_ENV, STATE_KEY, TERMINAL_KEY,
    paper_streamer_class, paper_tokenizer_metadata,
)
from beliefkv.experiments.paper_mars_admission import MarsAdmission
from beliefkv.experiments.workflow_metrics import StreamingWorkflowMetrics
from beliefkv.runtime.clock_evidence import local_monotonic_clock_domain
from scripts import run_paper_author_baseline as launcher
from tests.test_paper_author_baseline import captured


def launch_plan(mode="author"):
    arm_sources = {
        "mars": {"checkout": "/engine/mars", "author_source": {"checkout": "/author/mars"}},
        "mori": {"checkout": "/engine/mori"},
    }
    archive = {
        "common_environment": {
            "SERVED_MODEL_NAME": "qwen", "HICACHE_SIZE_GB": "200",
            "SERVER_RANDOM_SEED": "21", "PYTHONHASHSEED": "21", "CUDA_HOME": "/cuda",
        },
        "server_info": {
            "dtype": "bfloat16", "kv_cache_dtype": "bfloat16", "mem_fraction_static": 0.94,
            "context_length": 262144, "max_running_requests": 24, "chunked_prefill_size": 2048,
            "hicache_write_policy": "write_back", "hicache_io_backend": "kernel",
            "hicache_mem_layout": "page_first", "hicache_host_memory_mode": "cache",
        },
        "capacity": {"device_full_tokens": 805198, "host_full_tokens": 2034506},
    }
    execution = {
        "model_path": "/model/qwen", "graph": launcher.native_execution.GRAPH,
        "flags": ["--cuda-graph-config", json.dumps(launcher.native_execution.GRAPH)],
        "environment": {"SGLANG_MOE_CONFIG_DIR": "/moe"}, "kernel_path": "/moe/config.json",
        "identity": {"kernel_configuration_sha256": "moe-digest"},
    }
    launcher.common_launches(arm_sources, archive, execution, mode)
    return {"arms": arm_sources, "adapter_sources_sha256": {
        str(path): str(path) for path in launcher.WORKER_SOURCES
    }}, execution


def terminal(capture, arm, stamp):
    return {
        "schema_version": 1, "request_id": capture.request_id, "identity": list(capture.identity),
        "program_id": program_id(capture.identity, capture.session_id) if arm == "mori" else None,
        "job_id": capture.identity[0] if arm == "mars" else None,
        "sampling_path": "position", "native_sampler_preserved": True,
        "completion_boundary": "native_terminal_output_emit", "complete_monotonic_ms": stamp,
        "clock_domain": local_monotonic_clock_domain(), "prompt_tokens": len(capture.prompt_tokens),
        "prompt_sha256": capture.prompt_sha256, "output_token_ids": list(capture.output_tokens),
        "native_finish_reason": capture.native_finish_reason, "sampling_parameters": capture.sampling_parameters,
        "generation_parameters": capture.generation_parameters,
    }


def chunk(capture, ids, count, times, receipt=None):
    meta = {
        "id": capture.request_id, "prompt_tokens": len(capture.prompt_tokens),
        "cached_tokens": 0, "completion_tokens": count,
        "finish_reason": receipt and receipt["native_finish_reason"],
        EMIT_KEY: [[stamp, local_monotonic_clock_domain()] for stamp in times],
    }
    if receipt:
        meta[TERMINAL_KEY] = receipt
    return {"output_ids": ids, "meta_info": meta}


def test_legacy_default_and_explicit_old_draft_compatibility(monkeypatch):
    monkeypatch.setattr(launcher, "adapter_hashes", lambda _: {**dict.fromkeys(launcher.LEGACY_DRAFT_SHA256, "new"),
                                                             "shared.py": "still-current"})
    frozen = {"adapter_sources_sha256": {**launcher.LEGACY_DRAFT_SHA256, "shared.py": "still-current"}}
    assert launcher.plan_engine(frozen, None) == "legacy"
    launcher.verify_adapters(frozen, "legacy")
    with pytest.raises(ValueError, match="recorded"):
        launcher.plan_engine(frozen, ENGINE)
    with pytest.raises(ValueError, match="changed"):
        launcher.verify_adapters({**frozen, "engine": "legacy"}, "legacy")
    frozen["adapter_sources_sha256"]["shared.py"] = "unknown"
    with pytest.raises(ValueError, match="changed"):
        launcher.verify_adapters(frozen, "legacy")


@pytest.mark.parametrize("field", [None, *launcher.MARS_REQUIRED_ZERO])
def test_mars_preflight_keeps_repetition_penalty_and_rejects_unsupported_fields(tmp_path, field):
    row = {
        "event": "request_prompt", "request_id": "r1", "token_count": 100,
        "extra_key": None, "cache_salt": None,
        "sampling_parameters": {
            "max_new_tokens": 32, "min_new_tokens": 0, "stop": [], "stop_regex": [],
        },
        "generation_parameters": {"frequency_penalty": 0., "presence_penalty": 0., "repetition_penalty": 1.05},
    }
    if field:
        section = "sampling_parameters" if field == "min_new_tokens" else "generation_parameters"
        row[section][field] = 1
    tokens = tmp_path / "tokens.jsonl"
    tokens.write_text(json.dumps(row) + "\n")
    if field:
        with pytest.raises(ValueError, match=field):
            launcher.prompt_limits(tokens, ENGINE)
    else:
        assert launcher.prompt_limits(tokens, ENGINE) == (132, 1)
    assert launcher.prompt_limits(tokens, "legacy") == (132, 1)


@pytest.mark.parametrize("mode,cap", [("author", None), ("matched", 1)])
def test_common_launch_uses_the_same_execution_profile_and_separate_cap(mode, cap):
    plan, execution = launch_plan(mode)
    mars, mori = (plan["arms"][arm] for arm in ("mars", "mori"))
    assert mars["server_argv"][:5] == mori["server_argv"][:5]
    assert mars["server_argv"][3:5] == execution["flags"]
    assert mars["device_tokens"] == 805184 and mars["num_gpu_blocks_with_sentinel"] == 50325
    assert mars["host_tokens"] == 0 and "--disable-overlap-schedule" in mars["server_argv"]
    assert mori["device_tokens"] == 805198 and mori["host_tokens"] == 2034506
    assert mori["write_back_max_nodes"] == cap and mori["write_back_mode"] == mode
    assert mori["server_environment"][MORI_CAP_ENV] == str(cap or 0)
    assert mars["server_environment"][MARS_AUTHOR_ENV] == "/author/mars"
    assert mori["server_environment"][MARS_AUTHOR_ENV] == ""
    for name, arm in plan["arms"].items():
        assert arm["server_environment"][ARM_ENV] == name
        assert arm["server_environment"]["BELIEFKV_CAUSAL_REPLAY_SAMPLING"] == "position"


@pytest.mark.parametrize("broken", [None, "graph", "clock", "source", "moe", "capacity", "cap"])
def test_live_config_validation_rejects_mismatched_effective_state(broken):
    plan, execution = launch_plan()
    arm = plan["arms"]["mori"]
    proof = {
        "arm": "mori", "clock_domain": local_monotonic_clock_domain(), "sampling_mode": "position",
        "python": str(launcher.COMMON_PYTHON.resolve()), "engine_package": "/engine/mori/python/sglang",
        "adapter_sources_sha256": {
            str(launcher.ROOT / path): str(path) for path in launcher.WORKER_SOURCES
        },
        "mars_author_source": None, "moe_configurations": {"/moe/config.json": "moe-digest"},
    }
    mori = {"enabled": True, "cache_class": "HiRadixCache", "write_policy": "write_back",
            "write_back_batch_cap": None, "registered_programs": 0, "pending_h2d_host_releases": 0}
    state = {"world_size": 1, "effective_max_running_requests_per_dp": 24,
             "memory_usage": {"token_capacity": 805198}, STATE_KEY: proof, "mori": mori}
    info = {**arm["effective_settings"], "version": "0.5.20", "internal_states": [state],
            "cuda_graph_config": deepcopy(execution["graph"])}
    if broken == "graph":
        info["cuda_graph_config"]["prefill"]["backend"] = "disabled"
    elif broken == "clock":
        proof["clock_domain"] = "other"
    elif broken == "source":
        proof["engine_package"] = "/site-packages/sglang"
    elif broken == "moe":
        proof["moe_configurations"].clear()
    elif broken == "capacity":
        state["memory_usage"]["token_capacity"] -= 1
    elif broken == "cap":
        mori["write_back_batch_cap"] = 1

    async def run():
        transport = httpx.MockTransport(lambda _: httpx.Response(200, json=info))
        async with httpx.AsyncClient(base_url="http://127.0.0.1:19141", transport=transport) as client:
            if broken:
                with pytest.raises(ValueError):
                    await launcher.common_server_info(client, plan, "mori", execution)
            else:
                await launcher.common_server_info(client, plan, "mori", execution)

    asyncio.run(run())


@pytest.mark.parametrize("broken", [None, "emission", "clock", "job", "seed", "sampler"])
def test_sglang_stream_checks_same_clock_grouping_and_sampler(captured, broken):
    receipt = terminal(captured, "mars", 23.)
    output = NativeOutput("mars", captured, None, 10., engine=ENGINE)
    frame = chunk(captured, [7, 9], 2, [20., 21.], receipt)
    if broken == "emission":
        frame["meta_info"][EMIT_KEY].pop()
    elif broken == "clock":
        frame["meta_info"][EMIT_KEY][0][1] = "other"
    elif broken == "job":
        receipt["job_id"] = "other"
    elif broken == "seed":
        receipt["generation_parameters"] = {**captured.generation_parameters, "sampling_seed": 7}
    elif broken == "sampler":
        receipt["native_sampler_preserved"] = False
    if broken:
        with pytest.raises(ValueError):
            output.consume(frame, 25.)
    else:
        assert output.consume(frame, 25.) == [(20., 1), (21., 1), (23., 0)]
        assert output.verify().complete_monotonic_ms == 23.


def test_output_plugin_keeps_terminal_evidence_out_of_token_slices(captured, monkeypatch):
    monkeypatch.setenv(ARM_ENV, "mori")
    from beliefkv.experiments import causal_replay_native

    monkeypatch.setattr(causal_replay_native, "terminal_receipt", lambda _: terminal(captured, "mori", 0.))

    class Streamer:
        def build_additional_customized_info(self, reqs):
            return {}

    request = SimpleNamespace(
        sampling_params=SimpleNamespace(custom_params={PARAMETER_KEY: {}}),
        finished=lambda: False, send_token_offset=1, program_id=program_id(captured.identity, captured.session_id),
    )
    plugin = paper_streamer_class(Streamer)()
    accumulated = {}

    def accumulate(_, meta, state, info, index, finish_reason):
        for key, values in info.items():
            meta[key] = accumulated.setdefault(key, [])
            meta[key].extend(values[index])

    first = plugin.build_additional_customized_info([request])
    request.send_token_offset = 2
    next_output = plugin.build_additional_customized_info([request])
    request.finished = lambda: True
    last = plugin.build_additional_customized_info([request])
    assert [len(row[EMIT_KEY][0]) for row in (first, next_output, last)] == [1, 1, 0]
    for values in (first, next_output, last):
        meta = {}
        reason = captured.native_finish_reason if values is last else None
        paper_tokenizer_metadata(accumulate, None, meta, None, values, 0, reason)
    assert TERMINAL_KEY not in accumulated
    assert meta[EMIT_KEY][request.send_token_offset:] == []
    assert meta[TERMINAL_KEY]["program_id"] == request.program_id
    assert meta[TERMINAL_KEY]["complete_monotonic_ms"] >= meta[EMIT_KEY][-1][0]


@pytest.mark.parametrize("arm", ["mars", "mori"])
def test_common_backend_routes_raw_tokens_via_author_admission(arm, captured):
    seen = []
    args = captured.identity
    now = time.monotonic() * 1000.
    submit = SimpleNamespace(
        kind=Kind.LLM_SUBMIT, ts_ms=now, workflow_id=args[0], invocation_id=args[1],
        context_id=args[2], context_epoch=args[3],
        attributes={"request_id": captured.request_id, "sampling_seed": 21},
    )
    pid = program_id(captured.identity, captured.session_id)

    async def run():
        base = "http://127.0.0.1:19140"

        async def enter(workflow, *, root, prompt_tokens):
            seen.append((workflow, root, prompt_tokens))

        async def before(program, state, payload, *, replay_prompt_tokens, replay_prefix_group):
            seen.append((program, payload, replay_prompt_tokens, replay_prefix_group))
            return True

        def generate(request):
            assert request.url.path == "/generate"
            payload = json.loads(request.content)
            assert payload == baseline_payload(arm, "qwen", captured, payload["custom_logit_processor"], engine=ENGINE)
            assert payload["input_ids"] == list(captured.prompt_tokens)
            assert payload["beliefkv_metadata"] == dict(zip(IDENTITY_FIELDS, captured.identity))
            stamp = time.monotonic() * 1000.
            value = chunk(captured, list(captured.output_tokens), 2, [stamp, stamp], terminal(captured, arm, stamp))
            return httpx.Response(200, text=f"data: {json.dumps(value)}\n\ndata: [DONE]\n\n")

        router = SimpleNamespace(
            update_program_before_request=before, get_or_create_program=lambda _: SimpleNamespace(backend_url=base),
            update_program_after_request=lambda *_: None, record_cached_tokens=lambda *_: None,
        )
        async with httpx.AsyncClient(base_url=base, transport=httpx.MockTransport(generate)) as client:
            backend = PaperReplayBackend(
                arm, {captured.request_id: captured}, client, SimpleNamespace(emit_batch=lambda _: None),
                lambda _: None, lambda _: None,
                lambda _: None, StreamingWorkflowMetrics(), model="qwen", engine=ENGINE,
                mars=SimpleNamespace(enter=enter) if arm == "mars" else None,
                mori=router if arm == "mori" else None, timeout_s=5.,
            )
            if arm == "mars":
                backend.mars.arrived = lambda _: None
            await backend.emit(SimpleNamespace(
                kind=Kind.WORKFLOW_START, workflow_id=args[0], context_id=args[2], ts_ms=now - 1.,
                attributes={},
            ))
            await backend.emit(SimpleNamespace(
                kind=Kind.INVOCATION_CREATE, workflow_id=args[0], invocation_id=args[1],
                context_id=args[2], ts_ms=now - .5, parent_invocation_id=None,
            ))
            await backend.emit(submit)
            result = await backend.execute(captured.request_id, submit)
            assert result.output_tokens == len(captured.output_tokens)

    asyncio.run(run())
    assert seen == ([(args[0], True, 2)] if arm == "mars" else [(pid, {}, 2, token_sha256([3, 4]))])


def test_common_mars_timeout_aborts_the_native_request(captured):
    async def run():
        seen = []

        async def enter(*_args, **_kwargs):
            pass

        async def respond(request):
            seen.append(request.url.path)
            if request.url.path == "/generate":
                await asyncio.Future()
            assert request.url.path == "/abort_request"
            assert json.loads(request.content) == {"rid": captured.request_id}
            return httpx.Response(200)

        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:19140", transport=httpx.MockTransport(respond),
        ) as client:
            backend = PaperReplayBackend(
                "mars", {captured.request_id: captured}, client, None, None, None, lambda _: None,
                StreamingWorkflowMetrics(), model="qwen", engine=ENGINE,
                mars=SimpleNamespace(enter=enter), timeout_s=.2,
            )
            workflow, invocation, context, epoch = captured.identity
            backend.invocations[(workflow, invocation)] = SimpleNamespace(parent_invocation_id=None)
            submit = SimpleNamespace(
                workflow_id=workflow, invocation_id=invocation, context_id=context, context_epoch=epoch,
                attributes={"request_id": captured.request_id, "sampling_seed": 21},
            )
            with pytest.raises(TimeoutError):
                await backend.execute(captured.request_id, submit)
            assert not backend.active and not backend.abort_errors
        assert seen == ["/generate", "/abort_request"]

    asyncio.run(run())


def test_mars_controller_admits_from_published_sglang_block_census():
    checkout = Path(__file__).resolve().parents[2] / "references/MARS-official-20261123"
    if not checkout.is_dir():
        pytest.skip("optional pinned MARS author repository unavailable")

    async def run():
        stats = {"block_size": 16, "num_gpu_blocks": 50325, "num_free_blocks": 50324,
                 "total_tokens": 805184, "scheduling_policy": "mars", "max_num_seqs": 24, "usage": 0.,
                 "running_requests": 0, "waiting_requests": 0}

        def server_info(request):
            assert request.url.path == "/server_info"
            return httpx.Response(200, json={"internal_states": [{"mars_kv_cache_stats": stats}]})

        events = []
        async with httpx.AsyncClient(base_url="http://127.0.0.1:19140",
                                     transport=httpx.MockTransport(server_info)) as client:
            admission = MarsAdmission(checkout, client, events.append, device_tokens=805184, deadline_s=1.,
                                      engine=ENGINE)
            try:
                admission.arrived("root")
                await admission.enter("root", root=True, prompt_tokens=8192)
                await admission.enter("root", root=False, prompt_tokens=10000)
                admission.ended("root")
                assert [event["workflow_id"] for event in events if event["event"] == "mars_admit"] == ["root"]
                stats["num_gpu_blocks"] -= 1
                with pytest.raises(ValueError, match="capacity"):
                    await admission.refresh()
            finally:
                await admission.close()

    asyncio.run(run())
