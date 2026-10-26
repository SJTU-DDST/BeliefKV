import ast
import __future__
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from beliefkv.experiments.paper_baselines import RUNTIME_ARMS, environment
from beliefkv.runtime.agentix_baseline import AgentixAdapter
from beliefkv.runtime.paper_baseline import create_adapter
from beliefkv.runtime.sglang_v0520_admission import _request_key
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_agentix import configuration
from tests.test_agentix_executor import Device
from tests.test_agentix_native_capacity import waiting
from tests.test_agentix_native_preemption import native_batch
from tests.test_agentix_swap import ack, running
from tests.test_agentix_worker_execution import Worker
from tests.test_full_only_native_lifecycle import full_native


@pytest.fixture
def adapter(full_native, monkeypatch):
    from sglang.srt.server_args import ServerArgs, set_global_server_args_for_scheduler

    set_global_server_args_for_scheduler(ServerArgs(model_path="dummy"))
    for name, value in environment({}, "thunderagent").items():
        monkeypatch.setenv(name, value)
    cache, allocator, writes, loads, commits = full_native
    runtime = NativeAdmissionRuntime()
    runtime._paper_baseline.close()
    paper = AgentixAdapter(
        runtime, configuration(), context_limit=64, running_limit=1, decode_growth_tokens=6,
    )
    clock, device, stream = NS(now=0.), Device(), object()
    device.current_stream = lambda: stream
    paper.clock_ms = lambda: clock.now
    runtime._paper_baseline = paper
    runtime.attach_native_cache(cache)
    worker = Worker(device)
    scheduler = NS(
        enable_overlap=True, forward_stream=stream, schedule_stream=object(),
        ps=NS(tp_size=1, pp_size=1), is_generation=True,
        spec_algorithm=NS(is_none=lambda: True), dllm_config=None, enable_pdmux=False,
        device_module=device, model_worker=worker, tp_worker=worker, tree_cache=cache,
        server_args=NS(context_length=64, max_running_requests=1),
        chunked_req=None, result_queue=[], waiting_queue=[],
        _beliefkv_before_batch_selection=lambda *_args: False,
    )

    def enqueue(req, *, is_retracted):
        runtime.on_requests_requeued([req], is_retracted=is_retracted)
        scheduler.waiting_queue.append(req)

    scheduler._add_request_to_queue = enqueue
    native_commit = cache.on_hicache_transfer_commit

    def commit(receipt):
        native_commit(receipt)
        runtime.on_native_transfer_commit(receipt)

    cache.on_hicache_transfer_commit = commit
    yield NS(
        runtime=runtime, paper=paper, scheduler=scheduler, cache=cache,
        allocator=allocator, writes=writes, loads=loads, commits=commits,
        device=device, clock=clock, worker=worker,
    )
    device.completed_position = device.position
    clock.now = max(clock.now, device.position)
    runtime.close()


def test_unregistered_adapter_rejects_requests_until_native_scheduler_is_bound(adapter):
    state = adapter
    req = waiting("a", (1, 2, 3))
    with pytest.raises(ValueError, match="attach its native scheduler"):
        state.paper.registered(req, _request_key(req))
    assert not state.paper.policy.calls and not state.writes
    assert "agentix_atlas" not in RUNTIME_ARMS
    with pytest.raises(ValueError, match="qualified runtime"):
        create_adapter(state.runtime, state.paper.policy_name)


def test_common_runtime_binds_native_agentix_selection_execution_ack_and_requeue(adapter):
    state = adapter
    runtime, scheduler, paper = state.runtime, state.scheduler, state.paper
    runtime.attach_native_scheduler(scheduler)
    active, cold = running(state.cache, "active"), waiting("cold", (10, 11, 12))
    assert runtime.register_visible_request(active)
    assert runtime.register_visible_request(cold)
    arrival = runtime._visible_since[active.rid]
    scheduler.waiting_queue.append(cold)
    batch = native_batch(state.cache, [active])
    assert scheduler._beliefkv_before_batch_selection(batch, None) is False
    runtime.on_batch_selected(batch)
    for _ in range(4):
        state.worker.forward_batch_generation(batch)
        state.device.completed_position = state.device.position
        state.clock.now += 3.
        paper.tick()
    assert scheduler._beliefkv_before_batch_selection(batch, None) is True
    assert active.kv.holds_kv and not runtime.physical_disabled
    ack(state.cache)
    assert scheduler._beliefkv_before_batch_selection(batch, None) is False
    assert not runtime.physical_disabled and batch.is_empty()
    assert not active.kv.holds_kv and active in scheduler.waiting_queue
    assert runtime._visible_since[active.rid] == arrival
    assert paper.calls.requests[active.rid].arrived_ms == 0.
    plan = runtime.plan_native_prefill(scheduler.waiting_queue, running_batch=batch, adder=None)
    assert tuple(key.request_id for key in plan.prioritized) == (cold.rid,)
    assert paper.capacity.counts["prepared_rounds"] == 2
    assert paper.policy.counts["arrived"] == 2 and paper.policy.counts["completed"] == 0
    status = paper.status()
    assert status["program_identity"] == "root_workflow_id"
    assert status["native_scheduling"]["native_selection_hook_installed"]
    assert not status["complete_baseline"] and not status["gpu_llm_qualified"]


def test_common_runtime_completion_retains_overlapped_execution_and_program_inheritance(adapter):
    from sglang.srt.managers.schedule_batch import FINISH_LENGTH

    state = adapter
    state.runtime.attach_native_scheduler(state.scheduler)
    req = running(state.cache)
    assert state.runtime.register_visible_request(req)
    batch = native_batch(state.cache, [req])
    state.scheduler._beliefkv_before_batch_selection(batch, None)
    state.worker.forward_batch_generation(batch)
    state.worker.forward_batch_generation(batch)
    state.device.completed_position, state.clock.now = 3., 4.
    req.finished_reason = FINISH_LENGTH(len(req.output_ids))
    assert state.runtime.on_batch_completed(batch) == (req.rid,)
    assert state.paper.calls.terminals == {req.rid: "complete"}
    assert state.paper.policy.service.service_ms["workflow"] == 0.
    state.device.completed_position, state.clock.now = 6., 7.
    state.paper.tick()
    assert state.paper.policy.service.service_ms["workflow"] == 6.
    assert state.paper.policy.counts["completed"] == 1
    assert not state.paper.calls.requests


def test_native_binding_checks_effective_common_serving_parameters_before_install(adapter):
    state = adapter
    state.scheduler.server_args.max_running_requests = 2
    original = state.worker.forward_batch_generation
    with pytest.raises(ValueError, match="declared data plane"):
        state.runtime.attach_native_scheduler(state.scheduler)
    assert state.worker.forward_batch_generation == original
    assert state.paper.controller is None and state.paper.swap is None


def test_uncheckpointed_native_retraction_is_rejected_before_any_row_release(adapter):
    state = adapter
    state.runtime.attach_native_scheduler(state.scheduler)
    req = running(state.cache)
    before, row = state.allocator.available_size(), req.kv.req_pool_idx
    with pytest.raises(RuntimeError, match="uncheckpointed"):
        state.runtime.before_native_request_release(req, reason="decode_retraction")
    assert req.kv.req_pool_idx == row and req.kv.holds_kv
    assert state.allocator.available_size() == before


def test_agentix_adapter_cannot_inherit_full_join_or_resident_priorities(adapter):
    state = adapter
    state.runtime.enable_join_parent_priority = True
    with pytest.raises(ValueError, match="inherit BeliefKV"):
        AgentixAdapter(
            state.runtime, configuration(), context_limit=64, running_limit=1,
            decode_growth_tokens=6,
        )


def test_isolated_native_constructor_binds_agentix_before_accepting_requests(adapter, monkeypatch, tmp_path):
    checkout = os.environ.get("SGLANG_SOURCE_CHECKOUT")
    if not checkout:
        pytest.skip("set SGLANG_SOURCE_CHECKOUT to the isolated Agentix engine")
    source = Path(checkout) / "python/sglang/srt/managers/scheduler.py"
    tree = ast.parse(source.read_text())
    native_class = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Scheduler"
    )
    method = next(
        node for node in native_class.body if isinstance(node, ast.FunctionDef)
        and node.name == "init_beliefkv_runtime"
    )
    if not any(
        isinstance(node, ast.Attribute) and node.attr == "attach_native_scheduler"
        for node in ast.walk(method)
    ):
        pytest.skip("this preserved native engine predates the Agentix constructor hook")
    for name in ("BELIEFKV_NATIVE_TELEMETRY_DIR", "BELIEFKV_ADMISSION_TELEMETRY_DIR"):
        monkeypatch.delenv(name, raising=False)
    declaration = json.loads((
        Path(__file__).resolve().parents[1]
        / "configs/baselines/agentix_native_qualification_20261025.json"
    ).read_bytes())
    declaration["parameters"]["context_limit"] = 64
    configuration_path = tmp_path / "qualification.json"
    configuration_path.write_text(json.dumps(declaration))
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", declaration["policy"])
    monkeypatch.setenv("BELIEFKV_AGENTIX_QUALIFICATION_ONLY", "1")
    monkeypatch.setenv("BELIEFKV_AGENTIX_QUALIFICATION_CONFIG", str(configuration_path))
    order = []
    attach, bind = AgentixAdapter.attach, AgentixAdapter.bind_scheduler

    def attach_cache(paper, cache):
        order.append("cache")
        attach(paper, cache)

    def bind_scheduler(paper, scheduler):
        order.append("scheduler")
        assert paper.cache is scheduler.tree_cache
        bind(paper, scheduler)

    monkeypatch.setattr(AgentixAdapter, "attach", attach_cache)
    monkeypatch.setattr(AgentixAdapter, "bind_scheduler", bind_scheduler)
    scheduler = adapter.scheduler
    scheduler.server_args.enable_beliefkv_admission = True
    scheduler.server_args.tp_size = 1
    scheduler.idle_sleeper = None
    null = object()
    scheduler.disaggregation_mode = null
    scheduler._beliefkv_on_native_transfer_commit = lambda receipt: scheduler.beliefkv_runtime.on_native_transfer_commit(receipt)
    scope = {"os": os, "DisaggregationMode": NS(NULL=null), "__file__": str(source)}
    exec(compile(
        ast.Module(body=[method], type_ignores=[]), str(source), "exec",
        flags=__future__.annotations.compiler_flag,
    ), scope)
    try:
        scope["init_beliefkv_runtime"](scheduler)
        assert order == ["cache", "scheduler"]
        runtime = scheduler.beliefkv_runtime
        assert scheduler.beliefkv_runtime is runtime
        assert runtime._paper_baseline.status()["native_scheduling"]["native_selection_hook_installed"]
        assert runtime.register_visible_request(waiting("first", (1, 2, 3)))
        assert runtime._paper_baseline.policy.counts["arrived"] == 1
    finally:
        if getattr(scheduler, "beliefkv_runtime", None) is not None:
            scheduler.beliefkv_runtime.close()
