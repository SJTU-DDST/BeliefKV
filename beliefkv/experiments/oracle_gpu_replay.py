from __future__ import annotations

import asyncio
import gzip
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

import httpx

from beliefkv.core.events import (
    ContextMode,
    EventConfidence,
    ExecutionMode,
    RelationType,
    RuntimeEvent,
    RuntimeEventKind,
)
from beliefkv.oracle.contracts import (
    FrozenActionBoundaryKind,
    FrozenAgentDemand,
    FrozenInvocationDemand,
    FrozenJoinDemand,
    FrozenJoinKey,
    FrozenLLMCallDemand,
    FrozenToolKey,
    LogicalInvocationKey,
    OracleCallPhase,
    OracleInvocationProgress,
    OracleReplayCursor,
    PerfectFutureOracleArm,
)
from beliefkv.oracle.physical_sidecar import (
    FrozenPhysicalCall,
    FrozenPhysicalSidecar,
)
from beliefkv.oracle.truth_provider import OracleTruthProvider
from beliefkv.policy.perfect_future_joint import (
    OracleReadyRequest,
    OracleSemanticResidency,
    PerfectFutureJointPlanner,
)
from beliefkv.policy.reference.base import ResidencyAction
from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata


class RuntimeEventSink(Protocol):
    def emit_batch(self, events: tuple[RuntimeEvent, ...]) -> None: ...


@dataclass
class _MutableProgress:
    call_ordinal: int
    phase: OracleCallPhase = OracleCallPhase.NOT_STARTED
    prefilled_prompt_tokens: int = 0
    generated_tokens: int = 0
    active_tool_ordinal: int | None = None
    tool_elapsed_ms: float = 0.0


@dataclass(frozen=True)
class _ContextCommit:
    frozen: tuple[int, ...]
    actual: tuple[int, ...]


@dataclass(frozen=True)
class OracleGPUReplayResult:
    arm: PerfectFutureOracleArm
    replay_id: str
    truth_id: str
    truth_digest: str
    started_monotonic_ms: float
    finished_monotonic_ms: float
    completed_workflows: int
    failed_workflows: int
    completed_requests: int
    prompt_tokens: int
    output_tokens: int
    oracle_access_summary: Mapping[str, object]
    failures: tuple[Mapping[str, object], ...]

    @property
    def makespan_s(self) -> float:
        return (self.finished_monotonic_ms - self.started_monotonic_ms) / 1000.0

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "arm": self.arm.value,
            "replay_id": self.replay_id,
            "truth_id": self.truth_id,
            "truth_digest": self.truth_digest,
            "started_monotonic_ms": self.started_monotonic_ms,
            "finished_monotonic_ms": self.finished_monotonic_ms,
            "makespan_s": self.makespan_s,
            "completed_workflows": self.completed_workflows,
            "failed_workflows": self.failed_workflows,
            "completed_requests": self.completed_requests,
            "prompt_tokens": self.prompt_tokens,
            "output_tokens": self.output_tokens,
            "workflows_per_hour": (
                self.completed_workflows * 3600.0 / self.makespan_s
                if self.makespan_s > 0
                else 0.0
            ),
            "oracle_access_summary": dict(self.oracle_access_summary),
            "failures": [dict(item) for item in self.failures],
        }


class _Emitter:
    def __init__(
        self,
        sink: RuntimeEventSink,
        *,
        replay_id: str,
        trace_path: Path,
    ) -> None:
        self.sink = sink
        self.replay_id = replay_id
        self.sequence = 0
        self._lock = threading.Lock()
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        self._trace = trace_path.open("w", encoding="utf-8", buffering=1)

    def emit(
        self,
        kind: RuntimeEventKind,
        *,
        workflow_id: str,
        attributes: Mapping[str, object] | None = None,
        **kwargs: Any,
    ) -> RuntimeEvent:
        with self._lock:
            self.sequence += 1
            event = RuntimeEvent(
                event_id=f"{self.replay_id}:event:{self.sequence:09d}",
                ts_ms=time.monotonic() * 1000.0,
                kind=kind,
                workflow_id=workflow_id,
                confidence=EventConfidence.OBSERVED_EXACT,
                attributes={
                    "source": "oracle_gpu_replay",
                    "replay_id": self.replay_id,
                    **dict(attributes or {}),
                },
                **kwargs,
            )
            self.sink.emit_batch((event,))
            self._trace.write(
                json.dumps(event.to_dict(), sort_keys=True, allow_nan=False) + "\n"
            )
            return event

    def close(self) -> None:
        self._trace.flush()
        self._trace.close()


class _TokenPathMapper:
    def __init__(self, *, vocab_size: int) -> None:
        if vocab_size <= 0:
            raise ValueError("vocab_size must be positive")
        self.vocab_size = vocab_size
        self._symbol_to_token: dict[int, int] = {}
        self._next_token = 0
        self._commit_by_owner: dict[LogicalInvocationKey, _ContextCommit] = {}

    def prompt(
        self,
        *,
        owner: LogicalInvocationKey,
        frozen_prompt: tuple[int, ...],
    ) -> list[int]:
        previous = self._commit_by_owner.get(owner)
        shared = 0
        if previous is not None:
            for old, new in zip(previous.frozen, frozen_prompt):
                if old != new:
                    break
                shared += 1
        actual = list(previous.actual[:shared]) if previous is not None else []
        actual.extend(self._token_for(item) for item in frozen_prompt[shared:])
        return actual

    def commit(
        self,
        *,
        owner: LogicalInvocationKey,
        physical: FrozenPhysicalCall,
        actual_prompt: list[int],
        output_token_ids: list[int],
    ) -> None:
        expected = len(physical.cache_commit_token_symbols)
        actual = tuple(actual_prompt + output_token_ids)
        if len(actual) < expected:
            raise RuntimeError("generated output cannot cover frozen cache commit")
        self._commit_by_owner[owner] = _ContextCommit(
            frozen=physical.cache_commit_token_symbols,
            actual=actual[:expected],
        )

    def _token_for(self, symbol: int) -> int:
        value = self._symbol_to_token.get(symbol)
        if value is not None:
            return value
        if self._next_token >= self.vocab_size:
            raise RuntimeError(
                "frozen prompt identity exceeds the configured model vocabulary"
            )
        value = self._next_token
        self._next_token += 1
        self._symbol_to_token[symbol] = value
        return value


class OracleGPUReplay:
    """Replay frozen semantic/token demand through the real SGLang data plane."""

    def __init__(
        self,
        *,
        truth: FrozenAgentDemand,
        sidecar: FrozenPhysicalSidecar,
        arm: PerfectFutureOracleArm,
        replay_id: str,
        base_url: str,
        event_sink: RuntimeEventSink,
        output_dir: Path,
        vocab_size: int = 151_936,
        request_timeout_s: float = 3600.0,
        proactive_min_wait_ms: float = 5_000.0,
        prefetch_lead_ms: float = 2_000.0,
    ) -> None:
        if sidecar.truth_id != truth.truth_id or (
            sidecar.truth_digest != truth.truth_digest
        ):
            raise ValueError("physical sidecar does not match frozen truth")
        if sidecar.initial_radix_state not in {
            "empty_server_boot",
            "explicit_cache_reset",
        }:
            raise ValueError("GPU replay requires a known empty initial Radix state")
        if arm not in {
            PerfectFutureOracleArm.O0_CURRENT,
            PerfectFutureOracleArm.O3_JOINT,
        }:
            raise ValueError("GPU replay currently supports only O0 and O3")
        self.truth = truth
        self.sidecar = sidecar
        self.arm = arm
        self.replay_id = replay_id
        self.base_url = base_url.rstrip("/")
        self.output_dir = output_dir.resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.request_timeout_s = request_timeout_s
        self.proactive_min_wait_ms = proactive_min_wait_ms
        self.prefetch_lead_ms = prefetch_lead_ms
        self.emitter = _Emitter(
            event_sink,
            replay_id=replay_id,
            trace_path=self.output_dir / "replay_control_events.jsonl",
        )
        self.path_mapper = _TokenPathMapper(vocab_size=vocab_size)
        self.invocation_by_key = {item.key: item for item in truth.invocations}
        self.join_by_key = {item.key: item for item in truth.joins}
        self.physical_by_call = {
            (item.invocation, item.call_ordinal): item for item in sidecar.calls
        }
        physical_by_owner: dict[
            LogicalInvocationKey, list[FrozenPhysicalCall]
        ] = {}
        for item in sidecar.calls:
            owner = self.invocation_by_key[item.invocation].semantic_owner
            physical_by_owner.setdefault(owner, []).append(item)
        self.physical_by_owner = {
            owner: tuple(sorted(items, key=lambda item: item.trace_request_ordinal))
            for owner, items in physical_by_owner.items()
        }
        self.progress = {
            item.key: _MutableProgress(item.calls[0].call_ordinal)
            for item in truth.invocations
        }
        self.completed_invocations: set[LogicalInvocationKey] = set()
        self.completed_tools: set[FrozenToolKey] = set()
        self.satisfied_joins: set[FrozenJoinKey] = set()
        self.pending: dict[str, OracleReadyRequest] = {}
        self.join_tasks: dict[FrozenJoinKey, dict[LogicalInvocationKey, asyncio.Task]] = {}
        self.current_epoch: dict[LogicalInvocationKey, int] = {
            item.key: 0 for item in truth.invocations
        }
        self._cursor_revision = 0
        self._state_lock = asyncio.Lock()
        self._completed_requests = 0
        self._prompt_tokens = 0
        self._output_tokens = 0
        self._completed_workflows = 0
        self._failures: list[dict[str, object]] = []
        self._owners_with_prefetch_debt: set[LogicalInvocationKey] = set()
        self.provider = (
            OracleTruthProvider(
                truth,
                arm=arm,
                expected_truth_id=truth.truth_id,
                expected_truth_digest=truth.truth_digest,
                access_record_sink=self._write_access_record,
            )
            if arm == PerfectFutureOracleArm.O3_JOINT
            else None
        )
        self.planner = (
            PerfectFutureJointPlanner(self.provider, replay_id=replay_id)
            if self.provider is not None
            else None
        )
        self._access_path = self.output_dir / "oracle_access.jsonl"
        self._access_output = self._access_path.open(
            "w", encoding="utf-8", buffering=1
        )
        self._request_output = (self.output_dir / "requests.jsonl").open(
            "w", encoding="utf-8", buffering=1
        )

    @classmethod
    def from_paths(
        cls,
        *,
        truth_path: Path,
        sidecar_path: Path,
        **kwargs: Any,
    ) -> "OracleGPUReplay":
        truth = FrozenAgentDemand.from_json_bytes(truth_path.read_bytes())
        with gzip.open(sidecar_path, "rb") as source:
            sidecar = FrozenPhysicalSidecar.from_json_bytes(source.read())
        return cls(truth=truth, sidecar=sidecar, **kwargs)

    async def run(self) -> OracleGPUReplayResult:
        started = time.monotonic() * 1000.0
        limits = httpx.Limits(max_connections=128, max_keepalive_connections=64)
        timeout = httpx.Timeout(self.request_timeout_s)
        roots = [item for item in self.truth.invocations if item.parent is None]
        try:
            async with httpx.AsyncClient(limits=limits, timeout=timeout) as client:
                tasks = [
                    asyncio.create_task(self._run_root(item, client)) for item in roots
                ]
                outcomes = await asyncio.gather(*tasks, return_exceptions=True)
                for invocation, outcome in zip(roots, outcomes):
                    if isinstance(outcome, BaseException):
                        self._failures.append(
                            {
                                "workload_instance": invocation.key.workload_instance,
                                "error_type": type(outcome).__name__,
                                "message": str(outcome),
                            }
                        )
        finally:
            finished = time.monotonic() * 1000.0
            self._request_output.flush()
            self._request_output.close()
            self._access_output.flush()
            self._access_output.close()
            self.emitter.close()
        return OracleGPUReplayResult(
            arm=self.arm,
            replay_id=self.replay_id,
            truth_id=self.truth.truth_id,
            truth_digest=self.truth.truth_digest,
            started_monotonic_ms=started,
            finished_monotonic_ms=finished,
            completed_workflows=self._completed_workflows,
            failed_workflows=len(self._failures),
            completed_requests=self._completed_requests,
            prompt_tokens=self._prompt_tokens,
            output_tokens=self._output_tokens,
            oracle_access_summary=(
                self.provider.access_summary if self.provider is not None else {
                    "total_queries": 0,
                    "counts": {},
                }
            ),
            failures=tuple(self._failures),
        )

    async def _run_root(
        self,
        invocation: FrozenInvocationDemand,
        client: httpx.AsyncClient,
    ) -> None:
        workflow_id = self._workflow_id(invocation.key)
        metadata = self._metadata(invocation, context_epoch=0)
        self.emitter.emit(RuntimeEventKind.WORKFLOW_START, workflow_id=workflow_id)
        self._emit_invocation_create(invocation, metadata)
        await self._run_invocation(invocation, client)
        self.emitter.emit(RuntimeEventKind.WORKFLOW_END, workflow_id=workflow_id)
        self._completed_workflows += 1

    async def _run_invocation(
        self,
        invocation: FrozenInvocationDemand,
        client: httpx.AsyncClient,
    ) -> None:
        for index, call in enumerate(invocation.calls):
            if call.prompt_tokens or call.output_tokens:
                await self._run_llm_call(invocation, call, client)
            else:
                await self._set_boundary_wait(invocation, call)
            terminal = await self._apply_boundary(invocation, call, client)
            if terminal:
                return
            if index + 1 < len(invocation.calls):
                await self._set_next_call(invocation, invocation.calls[index + 1])
        raise RuntimeError("frozen invocation did not reach a terminal boundary")

    async def _run_llm_call(
        self,
        invocation: FrozenInvocationDemand,
        call: FrozenLLMCallDemand,
        client: httpx.AsyncClient,
    ) -> None:
        physical = self.physical_by_call[(invocation.key, call.call_ordinal)]
        prompt = self.path_mapper.prompt(
            owner=invocation.semantic_owner,
            frozen_prompt=physical.prompt_token_symbols,
        )
        if len(prompt) != call.prompt_tokens:
            raise RuntimeError("reconstructed prompt length differs from frozen demand")
        request_id = self._request_id(invocation.key, call.call_ordinal)
        metadata = self._metadata(
            invocation, context_epoch=physical.runtime_context_epoch
        )
        async with self._state_lock:
            state = self.progress[invocation.key]
            state.phase = OracleCallPhase.PREFILL
            state.prefilled_prompt_tokens = 0
            state.generated_tokens = 0
            self.current_epoch[invocation.key] = physical.runtime_context_epoch
            self.pending[request_id] = OracleReadyRequest(
                request_id=request_id,
                logical_key=invocation.key,
                call_ordinal=call.call_ordinal,
                prompt_tokens=call.incremental_prompt_tokens,
                output_tokens=call.output_tokens,
            )
            self._advance_cursor()
        await self._publish_directive()
        started = time.monotonic() * 1000.0
        response = await client.post(
            f"{self.base_url}/generate",
            json={
                "input_ids": prompt,
                "sampling_params": {
                    "temperature": 0.0,
                    "max_new_tokens": call.output_tokens,
                    "min_new_tokens": call.output_tokens,
                    "ignore_eos": True,
                    "skip_special_tokens": False,
                },
                "rid": request_id,
                "return_logprob": True,
                "logprob_start_len": -1,
                "top_logprobs_num": 0,
                "return_text_in_logprobs": False,
                "beliefkv_metadata": metadata.to_wire(),
            },
        )
        response.raise_for_status()
        payload = response.json()
        output_ids = self._output_token_ids(payload)
        if len(output_ids) != call.output_tokens:
            raise RuntimeError(
                f"request {request_id} produced {len(output_ids)} tokens; "
                f"expected {call.output_tokens}"
            )
        finished = time.monotonic() * 1000.0
        self.path_mapper.commit(
            owner=invocation.semantic_owner,
            physical=physical,
            actual_prompt=prompt,
            output_token_ids=output_ids,
        )
        async with self._state_lock:
            state = self.progress[invocation.key]
            state.phase = OracleCallPhase.BOUNDARY_WAIT
            state.prefilled_prompt_tokens = call.incremental_prompt_tokens
            state.generated_tokens = call.output_tokens
            self.pending.pop(request_id, None)
            self._completed_requests += 1
            self._prompt_tokens += call.prompt_tokens
            self._output_tokens += call.output_tokens
            self._advance_cursor()
        self._request_output.write(
            json.dumps(
                {
                    "schema_version": 1,
                    "request_id": request_id,
                    "workload_instance": invocation.key.workload_instance,
                    "call_ordinal": call.call_ordinal,
                    "prompt_tokens": call.prompt_tokens,
                    "incremental_prompt_tokens": call.incremental_prompt_tokens,
                    "output_tokens": call.output_tokens,
                    "started_monotonic_ms": started,
                    "finished_monotonic_ms": finished,
                    "latency_ms": finished - started,
                    "context_epoch": physical.runtime_context_epoch,
                },
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        )
        await self._publish_directive()

    async def _apply_boundary(
        self,
        invocation: FrozenInvocationDemand,
        call: FrozenLLMCallDemand,
        client: httpx.AsyncClient,
    ) -> bool:
        boundary = call.boundary
        if boundary.kind == FrozenActionBoundaryKind.TOOL:
            await self._run_tools(invocation, call)
        elif boundary.kind == FrozenActionBoundaryKind.SPAWN:
            await self._spawn_children(invocation, boundary.target_invocations, client)
        elif boundary.kind == FrozenActionBoundaryKind.JOIN:
            for join_key in boundary.joins:
                await self._wait_join(invocation, self.join_by_key[join_key])
        elif boundary.kind in {
            FrozenActionBoundaryKind.RETURN,
            FrozenActionBoundaryKind.FINAL,
        }:
            await self._finish_invocation(invocation, boundary.kind)
            return True
        elif boundary.kind not in {FrozenActionBoundaryKind.CONTINUE}:
            raise RuntimeError(f"unsupported GPU replay boundary: {boundary.kind.value}")
        return False

    async def _run_tools(
        self,
        invocation: FrozenInvocationDemand,
        call: FrozenLLMCallDemand,
    ) -> None:
        tools = [
            item
            for item in invocation.tools
            if item.tool_ordinal in call.boundary.tool_ordinals
        ]
        if not tools:
            raise RuntimeError("TOOL boundary lacks frozen tool demand")
        longest = max(tools, key=lambda item: item.service_duration_ms)
        workflow_id = self._workflow_id(invocation.key)
        for tool in tools:
            self.emitter.emit(
                RuntimeEventKind.TOOL_START,
                workflow_id=workflow_id,
                invocation_id=self._invocation_id(invocation.key),
                context_id=self._context_id(invocation.semantic_owner),
                context_epoch=self.current_epoch[invocation.key],
                attributes={
                    "tool_ordinal": tool.tool_ordinal,
                    "tool_family": tool.tool_family,
                    "backend_class": tool.backend_class,
                },
            )
        async with self._state_lock:
            state = self.progress[invocation.key]
            state.active_tool_ordinal = longest.tool_ordinal
            state.tool_elapsed_ms = 0.0
            self._advance_cursor()
        wait_ms = longest.service_duration_ms
        await self._publish_parked_commit(invocation, wait_ms=wait_ms)
        if self.arm == PerfectFutureOracleArm.O3_JOINT and (
            wait_ms > self.prefetch_lead_ms
        ):
            await asyncio.sleep((wait_ms - self.prefetch_lead_ms) / 1000.0)
            async with self._state_lock:
                self.progress[invocation.key].tool_elapsed_ms = (
                    wait_ms - self.prefetch_lead_ms
                )
                self._advance_cursor()
            await self._publish_prefetch(invocation)
            await asyncio.sleep(self.prefetch_lead_ms / 1000.0)
        else:
            await asyncio.sleep(wait_ms / 1000.0)
        for tool in tools:
            self.emitter.emit(
                RuntimeEventKind.TOOL_END,
                workflow_id=workflow_id,
                invocation_id=self._invocation_id(invocation.key),
                context_id=self._context_id(invocation.semantic_owner),
                context_epoch=self.current_epoch[invocation.key],
                attributes={
                    "tool_ordinal": tool.tool_ordinal,
                    "tool_family": tool.tool_family,
                    "backend_class": tool.backend_class,
                    "outcome": tool.outcome.value,
                    "service_duration_ms": tool.service_duration_ms,
                },
            )
        async with self._state_lock:
            self.completed_tools.update(
                FrozenToolKey(invocation.key, item.tool_ordinal) for item in tools
            )
            state = self.progress[invocation.key]
            state.active_tool_ordinal = None
            state.tool_elapsed_ms = 0.0
            self._advance_cursor()
        await self._publish_directive()

    async def _spawn_children(
        self,
        parent: FrozenInvocationDemand,
        child_keys: tuple[LogicalInvocationKey, ...],
        client: httpx.AsyncClient,
    ) -> None:
        children = [self.invocation_by_key[item] for item in child_keys]
        join = next(
            (
                item
                for item in self.truth.joins
                if parent.key in item.waiters
                and set(item.members) == set(child_keys)
            ),
            None,
        )
        if join is None:
            raise RuntimeError("SPAWN boundary lacks matching frozen JOIN")
        workflow_id = self._workflow_id(parent.key)
        join_id = self._join_id(join.key)
        for child in children:
            metadata = self._metadata(child, context_epoch=0, join_id=join_id)
            self._emit_invocation_create(child, metadata)
            self.emitter.emit(
                RuntimeEventKind.SPAWN,
                workflow_id=workflow_id,
                invocation_id=self._invocation_id(parent.key),
                target_invocation_id=self._invocation_id(child.key),
                execution_mode=ExecutionMode.BACKGROUND,
                return_target_id=self._invocation_id(parent.key),
            )
        self.emitter.emit(
            RuntimeEventKind.JOIN_CREATE,
            workflow_id=workflow_id,
            join_id=join_id,
            member_invocation_ids=tuple(
                self._invocation_id(item.key) for item in children
            ),
            attributes={"mode": join.mode.value},
        )
        self.emitter.emit(
            RuntimeEventKind.JOIN_WAIT,
            workflow_id=workflow_id,
            invocation_id=self._invocation_id(parent.key),
            context_id=self._context_id(parent.semantic_owner),
            context_epoch=self.current_epoch[parent.key],
            join_id=join_id,
        )
        self.join_tasks[join.key] = {
            child.key: asyncio.create_task(self._run_invocation(child, client))
            for child in children
        }
        await self._publish_parked_commit(parent, wait_ms=float("inf"))

    async def _wait_join(
        self,
        parent: FrozenInvocationDemand,
        join: FrozenJoinDemand,
    ) -> None:
        pending = dict(self.join_tasks[join.key])
        prefetch_published = False
        while pending:
            done, _ = await asyncio.wait(
                tuple(pending.values()), return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                key = next(key for key, value in pending.items() if value is task)
                await task
                pending.pop(key)
            if (
                self.arm == PerfectFutureOracleArm.O3_JOINT
                and len(pending) <= 1
                and not prefetch_published
            ):
                await self._publish_prefetch(parent)
                prefetch_published = True
        self.emitter.emit(
            RuntimeEventKind.JOIN_SATISFIED,
            workflow_id=self._workflow_id(parent.key),
            invocation_id=self._invocation_id(parent.key),
            context_id=self._context_id(parent.semantic_owner),
            context_epoch=self.current_epoch[parent.key],
            join_id=self._join_id(join.key),
        )
        async with self._state_lock:
            self.satisfied_joins.add(join.key)
            self._advance_cursor()
        await self._publish_directive()

    async def _finish_invocation(
        self,
        invocation: FrozenInvocationDemand,
        kind: FrozenActionBoundaryKind,
    ) -> None:
        async with self._state_lock:
            call = invocation.calls[-1]
            state = self.progress[invocation.key]
            state.phase = OracleCallPhase.INVOCATION_COMPLETE
            state.prefilled_prompt_tokens = call.incremental_prompt_tokens
            state.generated_tokens = call.output_tokens
            state.active_tool_ordinal = None
            state.tool_elapsed_ms = 0.0
            self.completed_invocations.add(invocation.key)
            self._advance_cursor()
            cursor = self._cursor()
        self.emitter.emit(
            RuntimeEventKind.RETURN,
            workflow_id=self._workflow_id(invocation.key),
            invocation_id=self._invocation_id(invocation.key),
            context_id=self._context_id(invocation.semantic_owner),
            context_epoch=self.current_epoch[invocation.key],
            return_target_id=(
                self._invocation_id(invocation.parent)
                if invocation.parent is not None
                else None
            ),
            attributes={"outcome": kind.value},
        )
        if (
            self.planner is not None
            and invocation.parent is not None
            and self.planner.no_future_use(invocation.key, cursor=cursor)
        ):
            await self._publish_directive(
                residency=(
                    self._residency_target(
                        invocation,
                        action=ResidencyAction.DROP,
                        reason="oracle-perfect-future:no-future-use",
                    ),
                )
            )
        else:
            await self._publish_directive()

    async def _set_boundary_wait(
        self,
        invocation: FrozenInvocationDemand,
        call: FrozenLLMCallDemand,
    ) -> None:
        async with self._state_lock:
            state = self.progress[invocation.key]
            state.call_ordinal = call.call_ordinal
            state.phase = OracleCallPhase.BOUNDARY_WAIT
            state.prefilled_prompt_tokens = 0
            state.generated_tokens = 0
            self._advance_cursor()

    async def _set_next_call(
        self,
        invocation: FrozenInvocationDemand,
        call: FrozenLLMCallDemand,
    ) -> None:
        async with self._state_lock:
            state = self.progress[invocation.key]
            state.call_ordinal = call.call_ordinal
            state.phase = OracleCallPhase.PREFILL
            state.prefilled_prompt_tokens = 0
            state.generated_tokens = 0
            state.active_tool_ordinal = None
            state.tool_elapsed_ms = 0.0
            self._advance_cursor()

    async def _publish_parked_commit(
        self,
        invocation: FrozenInvocationDemand,
        *,
        wait_ms: float,
    ) -> None:
        if self.planner is None or wait_ms < self.proactive_min_wait_ms:
            await self._publish_directive()
            return
        async with self._state_lock:
            cursor = self._cursor()
        if not self.planner.has_future_reuse(invocation.key, cursor=cursor):
            await self._publish_directive()
            return
        action, target_bytes_hint, reason = self._parked_residency_action(
            invocation
        )
        if action == ResidencyAction.COMMIT_CPU:
            self._owners_with_prefetch_debt.add(invocation.semantic_owner)
        await self._publish_directive(
            residency=(
                self._residency_target(
                    invocation,
                    action=action,
                    reason=reason,
                    target_bytes_hint=target_bytes_hint,
                ),
            )
        )

    async def _publish_prefetch(
        self,
        invocation: FrozenInvocationDemand,
    ) -> None:
        if (
            self.planner is None
            or invocation.semantic_owner
            not in self._owners_with_prefetch_debt
        ):
            return
        self._owners_with_prefetch_debt.discard(invocation.semantic_owner)
        await self._publish_directive(
            residency=(
                self._residency_target(
                    invocation,
                    action=ResidencyAction.PREFETCH_GPU,
                    reason="oracle-perfect-future:latest-feasible-reentry",
                ),
            )
        )

    async def _publish_directive(
        self,
        *,
        residency: tuple[OracleSemanticResidency, ...] = (),
    ) -> None:
        if self.planner is None:
            return
        async with self._state_lock:
            cursor = self._cursor()
            ready = dict(self.pending)
            directive = self.planner.compile(
                cursor=cursor,
                ready=ready,
                residency=residency,
            )
            roots = [
                item
                for item in self.truth.invocations
                if item.parent is None
                and item.key not in self.completed_invocations
            ]
        if not roots:
            return
        anchor = roots[0]
        self.emitter.emit(
            RuntimeEventKind.STRUCTURED_ACTION,
            workflow_id=self._workflow_id(anchor.key),
            invocation_id=self._invocation_id(anchor.key),
            context_id=self._context_id(anchor.semantic_owner),
            context_epoch=self.current_epoch[anchor.key],
            attributes={"oracle_joint_directive": directive.to_dict()},
        )

    def _residency_target(
        self,
        invocation: FrozenInvocationDemand,
        *,
        action: ResidencyAction,
        reason: str,
        target_bytes_hint: int = 0,
    ) -> OracleSemanticResidency:
        now_ms = time.monotonic() * 1000.0
        return OracleSemanticResidency(
            context_id=self._context_id(invocation.semantic_owner),
            context_epoch=self.current_epoch[invocation.key],
            action=action,
            target_bytes_hint=target_bytes_hint,
            deadline_ms=now_ms + max(5_000.0, self.prefetch_lead_ms),
            reason=reason,
            service_deadline_ms=now_ms + max(5_000.0, self.prefetch_lead_ms),
        )

    def _parked_residency_action(
        self,
        invocation: FrozenInvocationDemand,
    ) -> tuple[ResidencyAction, int, str]:
        state = self.progress[invocation.key]
        current = self.physical_by_call.get(
            (invocation.key, state.call_ordinal)
        )
        if current is None:
            return (
                ResidencyAction.COMMIT_CPU,
                0,
                "oracle-perfect-future:parked-slack:no-physical-shape",
            )
        calls = self.physical_by_owner[invocation.semantic_owner]
        next_use = next(
            (
                item
                for item in calls
                if item.trace_request_ordinal > current.trace_request_ordinal
            ),
            None,
        )
        if next_use is None:
            return (
                ResidencyAction.DROP,
                len(current.cache_commit_token_symbols)
                * self.sidecar.kv_bytes_per_token,
                "oracle-perfect-future:no-physical-next-use",
            )
        reusable_tokens = 0
        for old, new in zip(
            current.cache_commit_token_symbols,
            next_use.prompt_token_symbols,
        ):
            if old != new:
                break
            reusable_tokens += 1
        current_tokens = len(current.cache_commit_token_symbols)
        target_bytes = current_tokens * self.sidecar.kv_bytes_per_token
        if reusable_tokens * 2 < current_tokens:
            return (
                ResidencyAction.DROP,
                target_bytes,
                (
                    "oracle-perfect-future:low-next-use-prefix:"
                    f"{reusable_tokens}-of-{current_tokens}"
                ),
            )
        return (
            ResidencyAction.COMMIT_CPU,
            target_bytes,
            (
                "oracle-perfect-future:parked-slack:"
                f"{reusable_tokens}-of-{current_tokens}"
            ),
        )

    def _cursor(self) -> OracleReplayCursor:
        return OracleReplayCursor(
            cursor_revision=self._cursor_revision,
            invocation_progress=tuple(
                OracleInvocationProgress(
                    logical_key=key,
                    current_call_ordinal=value.call_ordinal,
                    call_phase=value.phase,
                    prefilled_prompt_tokens=value.prefilled_prompt_tokens,
                    generated_tokens=value.generated_tokens,
                    active_tool_ordinal=value.active_tool_ordinal,
                    tool_elapsed_ms=float(value.tool_elapsed_ms),
                )
                for key, value in self.progress.items()
            ),
            completed_invocations=tuple(self.completed_invocations),
            completed_tools=tuple(self.completed_tools),
            satisfied_joins=tuple(self.satisfied_joins),
        )

    def _advance_cursor(self) -> None:
        self._cursor_revision += 1

    def _emit_invocation_create(
        self,
        invocation: FrozenInvocationDemand,
        metadata: BeliefKVRequestMetadata,
    ) -> None:
        self.emitter.emit(
            RuntimeEventKind.INVOCATION_CREATE,
            workflow_id=metadata.root_workflow_id,
            invocation_id=metadata.invocation_id,
            context_id=metadata.context_id,
            context_epoch=metadata.context_epoch,
            parent_invocation_id=metadata.parent_invocation_id,
            parent_context_id=metadata.parent_context_id,
            agent_definition_id=metadata.agent_definition_id,
            agent_instance_id=metadata.agent_instance_id,
            relation_type=RelationType(metadata.relation_type),
            context_mode=ContextMode(metadata.context_mode),
            execution_mode=ExecutionMode(metadata.execution_mode),
            return_target_id=metadata.return_target_id,
            join_id=metadata.join_id,
            attributes={"persistent": True},
        )

    def _metadata(
        self,
        invocation: FrozenInvocationDemand,
        *,
        context_epoch: int,
        join_id: str | None = None,
    ) -> BeliefKVRequestMetadata:
        parent = self.invocation_by_key.get(invocation.parent)
        return BeliefKVRequestMetadata(
            root_workflow_id=self._workflow_id(invocation.key),
            invocation_id=self._invocation_id(invocation.key),
            context_id=self._context_id(invocation.semantic_owner),
            context_epoch=context_epoch,
            agent_definition_id=invocation.agent_definition_id,
            agent_instance_id=self._invocation_id(invocation.key),
            parent_invocation_id=(
                self._invocation_id(invocation.parent)
                if invocation.parent is not None
                else None
            ),
            parent_context_id=(
                self._context_id(parent.semantic_owner)
                if parent is not None
                else None
            ),
            relation_type=invocation.relation.value,
            context_mode=invocation.context_mode.value,
            execution_mode=(
                "foreground" if invocation.parent is None else "background"
            ),
            return_target_id=(
                self._invocation_id(invocation.parent)
                if invocation.parent is not None
                else None
            ),
            join_id=join_id,
            full_prompt_replay_guaranteed=True,
            execution_timeout_s=self.request_timeout_s,
        )

    def _write_access_record(self, record: Any) -> None:
        self._access_output.write(
            json.dumps(record.to_dict(), sort_keys=True, allow_nan=False) + "\n"
        )

    @staticmethod
    def _output_token_ids(payload: object) -> list[int]:
        if isinstance(payload, list):
            if len(payload) != 1:
                raise RuntimeError("single replay request returned a batch response")
            payload = payload[0]
        if not isinstance(payload, Mapping):
            raise RuntimeError("SGLang generate response is not an object")
        meta = payload.get("meta_info")
        if not isinstance(meta, Mapping):
            raise RuntimeError("SGLang response lacks meta_info")
        values = meta.get("output_token_logprobs")
        if not isinstance(values, list):
            raise RuntimeError("SGLang response lacks output token IDs")
        result = []
        for item in values:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                token_id = item[1]
            elif isinstance(item, Mapping):
                token_id = item.get("token_id")
            else:
                raise RuntimeError("unsupported output logprob record")
            if type(token_id) is not int:
                raise RuntimeError("output logprob token ID is not an integer")
            result.append(token_id)
        return result

    def _workflow_id(self, key: LogicalInvocationKey) -> str:
        return f"{self.replay_id}:workflow:{key.workload_instance}"

    def _invocation_id(self, key: LogicalInvocationKey) -> str:
        path = ".".join(key.canonical_agent_path)
        return (
            f"{self.replay_id}:invocation:{key.workload_instance}:"
            f"{path}:{key.parent_spawn_ordinal}:{key.invocation_ordinal}"
        )

    def _context_id(self, owner: LogicalInvocationKey) -> str:
        path = ".".join(owner.canonical_agent_path)
        return (
            f"{self.replay_id}:context:{owner.workload_instance}:"
            f"{path}:{owner.parent_spawn_ordinal}:{owner.invocation_ordinal}"
        )

    def _join_id(self, key: FrozenJoinKey) -> str:
        return (
            f"{self.replay_id}:join:{key.workload_instance}:"
            f"{key.join_ordinal}"
        )

    def _request_id(self, key: LogicalInvocationKey, call_ordinal: int) -> str:
        return f"{self._invocation_id(key)}:call:{call_ordinal}"
