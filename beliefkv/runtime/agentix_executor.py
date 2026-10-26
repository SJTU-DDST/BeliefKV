"""Lossless, bounded executor accounting for the isolated Agentix paper adapter.

Events surround one actual model-worker forward on its compute stream, after
native stream dependencies are installed. Their elapsed interval includes that
forward's device dependency waits and host dispatch gaps, not exclusive kernels.
No model time is inferred from HTTP latency, token counts or external intervals.
"""

from collections import Counter, deque
from dataclasses import dataclass
from functools import wraps
import time


def _measured(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        started = time.thread_time_ns()
        try:
            return method(self, *args, **kwargs)
        finally:
            self.cpu_ns[method.__name__] += time.thread_time_ns() - started
    return wrapped


@dataclass(frozen=True)
class ExecutorLaunch:
    ticket: int
    request_ids: tuple[str, ...]
    decode: bool
    start: object
    finish: object
    stream: object


class AgentixExecutor:
    """Never drop a service interval or synchronize the GPU to obtain its time."""

    def __init__(self, policy, *, limit=8):
        if type(limit) is not int or limit <= 0:
            raise ValueError("Agentix executor accounting requires a positive queue bound")
        self.policy, self.limit = policy, limit
        self.pending = deque()
        self.open: ExecutorLaunch | None = None
        self.counts, self.cpu_ns = Counter(), Counter()
        self.total_executor_ms = 0.

    @_measured
    def begin(self, requests, *, decode: bool, device, stream, now_ms):
        """Called at the actual worker forward; membership must already be admitted."""
        if type(decode) is not bool:
            raise ValueError("Agentix executor needs an observed forward phase")
        if self.open is not None:
            raise ValueError("Agentix executor forward is already open")
        if len(self.pending) >= self.limit:
            raise ValueError("Agentix executor accounting backlog reached its bound")
        identities = tuple(requests)
        if not identities:
            raise ValueError("Agentix executor needs actual request membership")
        if stream is None or device.is_current_stream_capturing():
            raise ValueError("Agentix executor requires a known non-capturing compute stream")
        start, finish = device.Event(enable_timing=True), device.Event(enable_timing=True)
        # Validate the policy binding before recording an event or mutating time.
        self.policy._ids(identities)
        self.policy._time(now_ms)
        if any(request not in self.policy.executing for request in identities):
            raise ValueError("Agentix executor request has not actually been admitted")
        start.record(stream=stream)
        ticket = self.policy.begin_executor(identities, now_ms=now_ms)
        launch = ExecutorLaunch(ticket, identities, decode, start, finish, stream)
        self.open = launch
        self.counts["launched"] += 1
        return launch

    @_measured
    def end(self, launch):
        if launch is not self.open or launch is None:
            raise ValueError("Agentix executor end lacks its actual open forward")
        launch.finish.record(stream=launch.stream)
        self.pending.append(launch)
        self.open = None
        self.counts["submitted_end_events"] += 1

    @_measured
    def poll(self):
        completed = []
        while self.pending:
            launch = self.pending[0]
            if not launch.finish.query():
                break
            elapsed = launch.start.elapsed_time(launch.finish)
            self.policy.finish_executor(launch.ticket, executor_ms=elapsed)
            if launch.decode:
                self.policy.decode_completed()
            self.total_executor_ms += elapsed
            self.pending.popleft()
            completed.append(launch.request_ids)
            self.counts["accounted"] += 1
            self.counts["accounted_decode_batches"] += launch.decode
        return tuple(completed)

    def assert_drained(self):
        if self.open is not None or self.pending or self.policy.status()["pending_executor_launches"]:
            raise ValueError("Agentix executor service remains unaccounted")

    def status(self):
        return {
            "pending_launches": len(self.pending),
            "open_forward": self.open is not None,
            "counts": dict(self.counts), "measured_executor_ms": self.total_executor_ms,
            "controller_thread_cpu_ms": {
                name: value / 1e6 for name, value in self.cpu_ns.items()
            },
            "gpu_synchronization_for_accounting": False,
            "dropped_or_estimated_executor_intervals": 0,
            "service_time_scope": (
                "compute-stream worker-forward interval including dependency waits "
                "and dispatch gaps; not exclusive CUDA kernels or HTTP latency"
            ),
            "runtime_hook_installed": False,
        }


class AgentixWorkerExecution:
    """Bind service intervals at the native worker call, after stream setup.

    Installation is explicit and per worker. Native result processing still owns
    completion, cancellation and sampling; the paper adapter must settle these
    intervals before changing the corresponding policy calls.
    """

    def __init__(self, policy, *, device, compute_stream, clock_ms, limit=8):
        if compute_stream is None or not callable(clock_ms):
            raise ValueError("Agentix worker binding requires a compute stream and clock")
        self.executor = AgentixExecutor(policy, limit=limit)
        self.device, self.compute_stream, self.clock_ms = device, compute_stream, clock_ms
        self.worker = self.original = self._hook = None
        self.scheduler = None
        self._previous_override = None
        self._had_override = False
        self.failed = False
        self.counts, self.cpu_ns = Counter(), Counter()

    def install(self, worker):
        original = getattr(worker, "forward_batch_generation", None)
        if self.worker is not None or not callable(original):
            raise ValueError("Agentix worker execution requires one unbound native worker")
        if getattr(original, "_agentix_execution_owner", None) is not None:
            raise ValueError("Agentix worker already has an executor binding")

        @wraps(original)
        def forward(batch=None, *args, **kwargs):
            return self._forward(batch, args, kwargs)

        forward._agentix_execution_owner = self
        self._had_override = "forward_batch_generation" in vars(worker)
        self._previous_override = vars(worker).get("forward_batch_generation")
        self.original, self._hook = original, forward
        worker.forward_batch_generation = forward
        self.worker = worker

    def install_scheduler(self, scheduler):
        """Bind only the qualified single-rank, ordinary generation path."""
        stream = (
            scheduler.forward_stream if scheduler.enable_overlap else scheduler.schedule_stream
        )
        if (
            scheduler.ps.tp_size != 1 or scheduler.ps.pp_size != 1
            or scheduler.is_generation is not True
            or not scheduler.spec_algorithm.is_none()
            or scheduler.dllm_config is not None or scheduler.enable_pdmux
            or scheduler.device_module is not self.device or stream != self.compute_stream
            or scheduler.model_worker is not scheduler.tp_worker
        ):
            raise ValueError("Agentix worker binding requires the declared native generation path")
        self.install(scheduler.model_worker)
        self.scheduler = scheduler

    @_measured
    def _begin_forward(self, batch):
        if self.worker is None or self.failed:
            raise ValueError("Agentix worker execution is closed or its forward failed")
        if self.device.current_stream() != self.compute_stream:
            raise ValueError("Agentix worker forward is not on its declared compute stream")
        if self.device.is_current_stream_capturing():
            raise ValueError("Agentix worker binding cannot run inside CUDA graph capture")
        if batch is None or not getattr(batch, "reqs", None):
            raise ValueError("Agentix worker forward requires actual native batch requests")
        if not batch.spec_algorithm.is_none():
            raise ValueError("Agentix worker accounting does not support speculative execution")
        decode = batch.forward_mode.is_decode()
        if type(decode) is not bool or not (decode or batch.forward_mode.is_extend()):
            raise ValueError("Agentix worker accounting requires a normal prefill or decode")
        identities = tuple(req.rid for req in batch.reqs)
        policy = self.executor.policy
        policy._ids(identities)
        for req in batch.reqs:
            metadata = getattr(req, "beliefkv_metadata", None)
            if (
                type(metadata) is not dict
                or metadata.get("root_workflow_id") != policy.calls[req.rid].program_id
            ):
                raise ValueError("Agentix worker request does not belong to its root program")
        self.executor.poll()
        now = self.clock_ms()
        # A real worker launch proves admission. Retain peers still owned by
        # another overlapped launch until the adapter explicitly pauses them.
        policy.running(tuple(policy.executing | set(identities)), now_ms=now)
        launch = self.executor.begin(
            identities, decode=decode, device=self.device,
            stream=self.compute_stream, now_ms=now,
        )
        self.counts["native_worker_forwards"] += 1
        return launch

    def _forward(self, batch, args, kwargs):
        launch = self._begin_forward(batch)
        try:
            return self.original(batch, *args, **kwargs)
        except BaseException:
            self.failed = True
            self.counts["failed_worker_forwards"] += 1
            raise
        finally:
            self.executor.end(launch)

    def poll(self):
        return self.executor.poll()

    def assert_drained(self):
        self.executor.assert_drained()

    def close(self):
        if self.worker is None:
            return
        self.poll()
        self.assert_drained()
        if self.worker.forward_batch_generation is not self._hook:
            raise ValueError("Agentix cannot overwrite a subsequently installed worker hook")
        if self._had_override:
            self.worker.forward_batch_generation = self._previous_override
        else:
            del self.worker.forward_batch_generation
        self.worker = self.original = self._hook = None
        self.scheduler = None
        self._previous_override = None

    def status(self):
        return {
            "worker_forward_hook_installed": self.worker is not None,
            "native_scheduler_bound": self.scheduler is not None,
            "worker_forward_failed": self.failed,
            "counts": dict(self.counts),
            "controller_thread_cpu_ms": {
                name: value / 1e6 for name, value in self.cpu_ns.items()
            },
            "controller_cpu_scope": (
                "pre-forward binding only, including executor begin/poll helpers; "
                "native worker dispatch and sampling excluded; end recorded by executor"
            ),
            "executor": self.executor.status(),
            "completion_and_preemption_authority": "native_paper_adapter_after_executor_drain",
            "full_runtime_adapter_installed": False,
        }
