import ast
import __future__
import os
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from beliefkv.policy.agentix import AgentixScheduler
from beliefkv.runtime.agentix_executor import AgentixWorkerExecution
from tests.test_agentix import configuration
from tests.test_agentix_executor import Device


class Worker:
    def __init__(self, device):
        self.device, self.calls, self.outputs = device, [], []
        self.duration, self.error = 3., None

    def forward_batch_generation(self, batch, *args, **kwargs):
        self.calls.append((batch, args, kwargs))
        self.device.position += self.duration
        if self.error is not None:
            raise self.error
        result = NS(delay_sample_func=lambda: self.outputs.append("sample"))
        return result


def batch(*identities, decode=True):
    return NS(
        reqs=[
            NS(rid=identity, beliefkv_metadata={"root_workflow_id": "workflow"})
            for identity in identities
        ],
        spec_algorithm=NS(is_none=lambda: True),
        forward_mode=NS(is_decode=lambda: decode, is_extend=lambda: not decode),
    )


@pytest.fixture
def binding():
    policy = AgentixScheduler(configuration())
    for identity in ("a", "b"):
        policy.arrive(identity, "workflow", now_ms=0.)
    device, stream = Device(), object()
    device.current_stream = lambda: stream
    clock = NS(now=1.)
    execution = AgentixWorkerExecution(
        policy, device=device, compute_stream=stream, clock_ms=lambda: clock.now,
    )
    worker = Worker(device)
    execution.install(worker)
    yield execution, worker, policy, device, stream, clock
    device.completed_position = device.position
    execution.close()


def test_native_launch_commits_admission_binds_membership_and_preserves_sampling(binding):
    execution, worker, policy, device, stream, clock = binding
    first, second = batch("a", decode=False), batch("b")
    proxy = object()
    result = worker.forward_batch_generation(first, proxy, is_verify=False)
    clock.now = 2.
    worker.forward_batch_generation(batch=second)
    assert worker.calls[0] == (first, (proxy,), {"is_verify": False})
    assert worker.calls[1] == (second, (), {})
    assert not worker.outputs
    assert policy.executing == {"a", "b"}
    assert len(execution.executor.pending) == 2
    assert [launch.request_ids for launch in execution.executor.pending] == [("a",), ("b",)]
    assert all(launch.stream is stream for launch in execution.executor.pending)
    with pytest.raises(ValueError, match="unaccounted"):
        policy.complete("a", now_ms=3.)
    device.completed_position = 3.
    assert execution.poll() == (("a",),)
    assert policy.calls["a"].total_model_ms == 3.
    assert policy.calls["b"].total_model_ms == 0.
    result.delay_sample_func()
    assert worker.outputs == ["sample"]
    assert policy.complete("a", now_ms=4.) == 3.
    device.completed_position = 6.
    assert execution.poll() == (("b",),)
    assert policy.complete("b", now_ms=6.) == 3.
    assert execution.status()["counts"]["native_worker_forwards"] == 2
    assert execution.executor.status()["counts"]["accounted_decode_batches"] == 1


def test_reused_native_batch_cannot_change_an_already_submitted_forward(binding):
    execution, worker, policy, device, _, clock = binding
    native = batch("a", "b")
    worker.forward_batch_generation(native)
    native.reqs.pop()
    native.reqs[0].rid = "b"
    clock.now = 2.
    worker.forward_batch_generation(native)
    device.completed_position = 6.
    assert execution.poll() == (("a", "b"), ("b",))
    assert policy.calls["a"].total_model_ms == 3.
    assert policy.calls["b"].total_model_ms == 6.


@pytest.mark.parametrize("invalid", (
    "unknown", "duplicate", "foreign_program", "missing_program", "untagged",
    "speculative", "idle", "no_batch", "no_members", "wrong_stream", "capturing",
))
def test_unsupported_or_misbound_forwards_never_run_the_worker(binding, invalid):
    execution, worker, policy, device, _, _ = binding
    native = batch("a")
    if invalid == "unknown":
        native.reqs[0].rid = "unknown"
    elif invalid == "duplicate":
        native.reqs.append(native.reqs[0])
    elif invalid == "foreign_program":
        native.reqs[0].beliefkv_metadata["root_workflow_id"] = "other"
    elif invalid == "missing_program":
        native.reqs[0].beliefkv_metadata.clear()
    elif invalid == "untagged":
        native.reqs[0].beliefkv_metadata = None
    elif invalid == "speculative":
        native.spec_algorithm.is_none = lambda: False
    elif invalid == "idle":
        native.forward_mode = NS(is_decode=lambda: False, is_extend=lambda: False)
    elif invalid == "no_batch":
        native = None
    elif invalid == "no_members":
        native.reqs.clear()
    elif invalid == "capturing":
        device.capturing = True
    else:
        device.current_stream = object
    with pytest.raises(ValueError):
        worker.forward_batch_generation(native)
    assert not worker.calls and not policy.executing
    assert not policy.status()["pending_executor_launches"]


def test_failed_forward_keeps_its_interval_for_accounting_and_blocks_new_launches(binding):
    execution, worker, policy, device, _, _ = binding
    failure = RuntimeError("native model failure")
    worker.error = failure
    with pytest.raises(RuntimeError) as error:
        worker.forward_batch_generation(batch("a"))
    assert error.value is failure
    assert execution.status()["worker_forward_failed"]
    assert policy.status()["pending_executor_launches"] == 1
    with pytest.raises(ValueError, match="forward failed"):
        worker.forward_batch_generation(batch("b"))
    with pytest.raises(ValueError, match="drain"):
        policy.cancel("a", now_ms=2.)
    device.completed_position = 3.
    execution.poll()
    policy.cancel("a", now_ms=4.)
    assert policy.status()["pending_executor_launches"] == 0


def test_close_keeps_live_events_bound_then_restores_the_native_class_method(binding):
    execution, worker, _, device, _, _ = binding
    worker.forward_batch_generation(batch("a"))
    with pytest.raises(ValueError, match="unaccounted"):
        execution.close()
    assert "forward_batch_generation" in vars(worker)
    device.completed_position = 3.
    execution.close()
    assert "forward_batch_generation" not in vars(worker)
    assert worker.forward_batch_generation.__func__ is Worker.forward_batch_generation
    assert not execution.status()["worker_forward_hook_installed"]
    execution.close()


def test_close_restores_an_existing_instance_override_exactly(binding):
    execution, worker, _, device, stream, clock = binding
    execution.close()
    original = worker.forward_batch_generation
    worker.forward_batch_generation = original
    second = AgentixWorkerExecution(
        execution.executor.policy, device=device, compute_stream=stream,
        clock_ms=lambda: clock.now,
    )
    second.install(worker)
    second.close()
    assert worker.forward_batch_generation is original


def test_a_second_binding_cannot_steal_the_live_native_hook(binding):
    execution, worker, _, device, stream, clock = binding
    second = AgentixWorkerExecution(
        execution.executor.policy, device=device, compute_stream=stream,
        clock_ms=lambda: clock.now,
    )
    with pytest.raises(ValueError, match="already has"):
        second.install(worker)
    assert worker.forward_batch_generation._agentix_execution_owner is execution


def test_close_does_not_erase_a_later_external_worker_override(binding):
    execution, worker, *_ = binding
    original = worker.forward_batch_generation
    foreign = lambda *_args, **_kwargs: None
    worker.forward_batch_generation = foreign
    with pytest.raises(ValueError, match="subsequently installed"):
        execution.close()
    assert worker.forward_batch_generation is foreign
    worker.forward_batch_generation = original


def test_controller_cpu_measurement_does_not_include_native_model_dispatch(binding, monkeypatch):
    execution, worker, _, device, _, _ = binding
    cpu = NS(now=0)
    monkeypatch.setattr(
        "beliefkv.runtime.agentix_executor.time.thread_time_ns", lambda: cpu.now,
    )
    original = execution.original

    def expensive_dispatch(*args, **kwargs):
        cpu.now += 1_000_000
        return original(*args, **kwargs)

    execution.original = expensive_dispatch
    worker.forward_batch_generation(batch("a"))
    assert execution.status()["controller_thread_cpu_ms"] == {"_begin_forward": 0.}
    assert all(value == 0 for value in execution.executor.cpu_ns.values())
    device.completed_position = device.position


@pytest.mark.parametrize("overlap", (False, True))
def test_scheduler_binding_selects_the_actual_native_compute_stream(binding, overlap):
    execution, worker, _, device, stream, _ = binding
    execution.close()
    scheduler = NS(
        enable_overlap=overlap, forward_stream=stream if overlap else object(),
        schedule_stream=object() if overlap else stream,
        ps=NS(tp_size=1, pp_size=1), is_generation=True,
        spec_algorithm=NS(is_none=lambda: True), dllm_config=None, enable_pdmux=False,
        device_module=device, model_worker=worker, tp_worker=worker,
    )
    execution.install_scheduler(scheduler)
    worker.forward_batch_generation(batch("a"))
    device.completed_position = device.position
    assert execution.poll() == (("a",),)
    assert execution.status()["native_scheduler_bound"]


@pytest.mark.parametrize("invalid", (
    "tensor_parallel", "pipeline_parallel", "embedding", "speculative", "dllm",
    "pdmux", "device", "stream", "different_worker",
))
def test_scheduler_binding_rejects_unqualified_native_paths_before_install(binding, invalid):
    execution, worker, _, device, stream, _ = binding
    execution.close()
    scheduler = NS(
        enable_overlap=True, forward_stream=stream, schedule_stream=object(),
        ps=NS(tp_size=1, pp_size=1), is_generation=True,
        spec_algorithm=NS(is_none=lambda: True), dllm_config=None, enable_pdmux=False,
        device_module=device, model_worker=worker, tp_worker=worker,
    )
    if invalid == "tensor_parallel":
        scheduler.ps.tp_size = 2
    elif invalid == "pipeline_parallel":
        scheduler.ps.pp_size = 2
    elif invalid == "embedding":
        scheduler.is_generation = False
    elif invalid == "speculative":
        scheduler.spec_algorithm.is_none = lambda: False
    elif invalid == "dllm":
        scheduler.dllm_config = object()
    elif invalid == "pdmux":
        scheduler.enable_pdmux = True
    elif invalid == "device":
        scheduler.device_module = object()
    elif invalid == "stream":
        scheduler.forward_stream = object()
    else:
        scheduler.tp_worker = object()
    with pytest.raises(ValueError, match="declared native"):
        execution.install_scheduler(scheduler)
    assert "forward_batch_generation" not in vars(worker)
    assert not execution.status()["native_scheduler_bound"]


@pytest.mark.parametrize("deferred_sampling", (False, True))
def test_binding_preserves_current_native_worker_forward_and_delayed_sampler(
    binding, deferred_sampling,
):
    checkout = os.environ.get("SGLANG_SOURCE_CHECKOUT")
    if not checkout:
        pytest.skip("set SGLANG_SOURCE_CHECKOUT to exercise the native worker source")
    source = Path(checkout) / "python/sglang/srt/managers/tp_worker.py"
    tree = ast.parse(source.read_text())
    native_class = next(
        node for node in tree.body if isinstance(node, ast.ClassDef)
        and node.name == "TpModelWorker"
    )
    method = next(
        node for node in native_class.body if isinstance(node, ast.FunctionDef)
        and node.name == "forward_batch_generation"
    )
    execution, _old_worker, policy, device, stream, clock = binding
    execution.close()
    observed, logits, sampled = [], object(), object()
    forwarded = NS(
        sampling_info=NS(grammars=None), is_prefill_only=False,
        apply_deprecated_skip_attn_backend_init=lambda value: observed.append(("skip", value)),
    )

    def model_forward(native, **kwargs):
        assert native is forwarded
        observed.append(("model", kwargs))
        device.position += 5.
        return NS(
            logits_output=logits, can_run_graph=True, expert_distribution_metrics=None,
            routed_experts_output=None, indexer_topk_output=None,
        )

    def sample(actual_logits, native):
        assert actual_logits is logits and native is forwarded
        observed.append(("sample", None))
        return sampled

    scope = {
        "ForwardBatch": NS(init_new=lambda *_args, **_kwargs: forwarded),
        "GenerationBatchResult": NS,
        "capture_pre_sample_logits": lambda *_args: None,
        "envs": NS(SGLANG_ENABLE_DELAY_SAMPLE=NS(get=lambda: deferred_sampling)),
    }
    # Execute the unmodified pinned worker method. This checks its real
    # control flow with a CPU runner stand-in, not native LLM execution.
    exec(compile(
        ast.Module(body=[method], type_ignores=[]), str(source), "exec",
        flags=__future__.annotations.compiler_flag,
    ), scope)
    native_worker = type("NativeWorkerContract", (), {
        "forward_batch_generation": scope["forward_batch_generation"],
        "set_hicache_consumer": lambda _self, index: observed.append(("consumer", index)),
        "is_dllm": lambda _self: False,
    })()
    native_worker.model_runner = NS(forward=model_forward, sample=sample)
    native_worker.pp_group = NS(is_last_rank=True)
    native_worker.enable_overlap, native_worker.enable_spec = True, False
    bound = AgentixWorkerExecution(
        policy, device=device, compute_stream=stream, clock_ms=lambda: clock.now,
    )
    bound.install(native_worker)
    current = batch("a")
    current.hicache_consumer_index = 7
    try:
        result = native_worker.forward_batch_generation(current, pp_proxy_tensors=None)
        assert result.can_run_cuda_graph
        assert observed[:2] == [("consumer", 7), ("skip", None)]
        assert observed[2] == ("model", {"pp_proxy_tensors": None})
        if deferred_sampling:
            assert len(observed) == 3
            assert result.delay_sample_func() is result
        assert observed[3:] == [("sample", None)]
        assert result.next_token_ids is sampled
        device.completed_position = 5.
        assert bound.poll() == (("a",),)
        assert policy.calls["a"].total_model_ms == 5.
    finally:
        device.completed_position = device.position
        bound.close()
