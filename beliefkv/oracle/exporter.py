from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from beliefkv.core.events import ContextMode, RelationType, RuntimeEvent, RuntimeEventKind
from beliefkv.experiments.counterfactual_trace import (
    CounterfactualTraceBuilder,
    CounterfactualTraceError,
)
from beliefkv.oracle.contracts import (
    FrozenActionBoundary,
    FrozenActionBoundaryKind,
    FrozenAgentDemand,
    FrozenContextMode,
    FrozenDemandProvenance,
    FrozenInvocationDemand,
    FrozenInvocationRelation,
    FrozenJoinDemand,
    FrozenJoinKey,
    FrozenJoinMode,
    FrozenLLMCallDemand,
    FrozenToolDemand,
    FrozenToolOutcome,
    LogicalInvocationKey,
)
from beliefkv.oracle.physical_sidecar import (
    FrozenPhysicalCall,
    FrozenPhysicalSidecar,
)


class OracleTruthExportError(ValueError):
    """Raised when a runtime trace cannot support exact Oracle v2 export."""


@dataclass(frozen=True)
class OracleTruthExportResult:
    truth: FrozenAgentDemand
    physical_sidecar: FrozenPhysicalSidecar
    workflow_release_ms: Mapping[str, float]
    actual_call_count: int
    synthetic_join_boundary_count: int
    synthetic_terminal_boundary_count: int
    tool_count: int
    spawn_count: int
    join_count: int

    def summary(self) -> dict[str, object]:
        return {
            "truth_id": self.truth.truth_id,
            "truth_digest": self.truth.truth_digest,
            "workflow_count": len(
                {item.key.workload_instance for item in self.truth.invocations}
            ),
            "invocation_count": len(self.truth.invocations),
            "actual_llm_call_count": self.actual_call_count,
            "synthetic_join_boundary_count": self.synthetic_join_boundary_count,
            "synthetic_terminal_boundary_count": (
                self.synthetic_terminal_boundary_count
            ),
            "tool_count": self.tool_count,
            "spawn_count": self.spawn_count,
            "join_count": self.join_count,
            "physical_call_count": len(self.physical_sidecar.calls),
            "exact_incremental_action_boundary": False,
            "action_boundary_semantics": "complete LLM_RESULT",
            "workflow_release_ms": dict(sorted(self.workflow_release_ms.items())),
        }


@dataclass(frozen=True)
class _CallRecord:
    request_id: str
    submit: RuntimeEvent
    result: RuntimeEvent
    prompt_tokens: int
    output_tokens: int
    observed_cache_hit_tokens: int


@dataclass(frozen=True)
class _ToolRecord:
    start: RuntimeEvent
    end: RuntimeEvent
    tool_ordinal: int
    starts_after_actual_call: int


class OracleTruthExporter:
    """Export schedule-independent semantic demand and a separate KV sidecar."""

    def export(
        self,
        *,
        runtime_event_path: Path,
        runtime_audit_path: Path,
        request_token_trace_path: Path,
        workload_manifest_path: Path,
        provenance: FrozenDemandProvenance,
        source_trace_id: str,
        initial_radix_state: str,
    ) -> OracleTruthExportResult:
        events = CounterfactualTraceBuilder._runtime_events(runtime_event_path)
        legacy = CounterfactualTraceBuilder().build(
            runtime_event_path,
            runtime_audit_path,
            request_token_trace_path=request_token_trace_path,
        )
        manifest = self._load_manifest(workload_manifest_path)
        workflow_instance = self._workflow_instance_map(events, manifest)
        invocation_keys, creation_events = self._logical_keys(
            events, workflow_instance
        )
        calls_by_invocation = self._calls(events)
        joins, join_by_runtime_id, join_wait_events = self._joins(
            events, invocation_keys
        )
        tools_by_invocation = self._tools(events, calls_by_invocation)
        legacy_requests = {item.request_id: item for item in legacy.workload.requests}

        frozen_invocations: list[FrozenInvocationDemand] = []
        physical_calls: list[FrozenPhysicalCall] = []
        trace_request_ordinal = 0
        synthetic_join_count = 0
        synthetic_terminal_count = 0
        spawn_count = 0
        for runtime_invocation, key in sorted(
            invocation_keys.items(), key=lambda item: item[1]
        ):
            creation = creation_events[runtime_invocation]
            actual_calls = calls_by_invocation.get(runtime_invocation, ())
            if not actual_calls:
                raise OracleTruthExportError(
                    f"invocation has no complete LLM call: {runtime_invocation}"
                )
            call_ordinals: dict[int, int] = {}
            next_frozen_ordinal = 0
            for actual_index, call in enumerate(actual_calls):
                call_ordinals[actual_index] = next_frozen_ordinal
                next_frozen_ordinal += 1
                if self._join_waits_for_call(
                    call,
                    actual_calls,
                    join_wait_events.get(runtime_invocation, ()),
                ):
                    next_frozen_ordinal += 1

            invocation_tools = tools_by_invocation.get(runtime_invocation, ())
            tools_by_actual_call: dict[int, list[_ToolRecord]] = defaultdict(list)
            for tool in invocation_tools:
                tools_by_actual_call[tool.starts_after_actual_call].append(tool)

            frozen_calls: list[FrozenLLMCallDemand] = []
            frozen_tools: list[FrozenToolDemand] = []
            previous_commit: tuple[int, ...] = ()
            for actual_index, call in enumerate(actual_calls):
                legacy_request = legacy_requests.get(call.request_id)
                if legacy_request is None:
                    raise OracleTruthExportError(
                        f"LLM request lacks physical demand: {call.request_id}"
                    )
                frozen_ordinal = call_ordinals[actual_index]
                prompt_path = legacy_request.prompt_token_symbols
                commit_path = legacy_request.cache_commit_token_symbols
                if not prompt_path or not commit_path:
                    raise OracleTruthExportError(
                        f"LLM request lacks exact token identity: {call.request_id}"
                    )
                prefix = self._common_prefix(previous_commit, prompt_path)
                incremental_prompt = len(prompt_path) - prefix
                previous_commit = commit_path
                interval_events = self._events_after_call(
                    events, call, actual_calls, actual_index
                )
                tool_records = tools_by_actual_call.get(actual_index, ())
                spawn_targets = tuple(
                    sorted(
                        {
                            invocation_keys[event.target_invocation_id]
                            for event in interval_events
                            if event.kind == RuntimeEventKind.SPAWN
                            and event.target_invocation_id in invocation_keys
                        }
                    )
                )
                waits = self._join_waits_for_call(
                    call,
                    actual_calls,
                    join_wait_events.get(runtime_invocation, ()),
                )
                terminal = any(
                    event.kind == RuntimeEventKind.RETURN
                    and event.invocation_id == runtime_invocation
                    for event in interval_events
                )
                if tool_records and spawn_targets:
                    raise OracleTruthExportError(
                        "one LLM result contains TOOL and SPAWN, which schema v2 "
                        f"cannot represent atomically: {call.request_id}"
                    )
                composite_terminal = terminal and bool(
                    tool_records or spawn_targets or waits
                )
                if composite_terminal and actual_index + 1 < len(actual_calls):
                    raise OracleTruthExportError(
                        "composite terminal action must be the invocation's final "
                        f"LLM result: {call.request_id}"
                    )

                if spawn_targets:
                    boundary = FrozenActionBoundary(
                        kind=FrozenActionBoundaryKind.SPAWN,
                        target_invocations=spawn_targets,
                    )
                    spawn_count += len(spawn_targets)
                elif tool_records:
                    boundary = FrozenActionBoundary(
                        kind=FrozenActionBoundaryKind.TOOL,
                        tool_ordinals=tuple(
                            sorted(item.tool_ordinal for item in tool_records)
                        ),
                    )
                elif waits:
                    boundary = FrozenActionBoundary(
                        kind=FrozenActionBoundaryKind.JOIN,
                        joins=tuple(
                            sorted(
                                join_by_runtime_id[item.join_id]
                                for item in waits
                            )
                        ),
                    )
                elif terminal:
                    parent = creation.parent_invocation_id
                    boundary = FrozenActionBoundary(
                        kind=(
                            FrozenActionBoundaryKind.FINAL
                            if parent is None
                            else FrozenActionBoundaryKind.RETURN
                        ),
                        target_invocations=(
                            (invocation_keys[parent],)
                            if parent is not None
                            else ()
                        ),
                    )
                else:
                    boundary = FrozenActionBoundary(
                        kind=FrozenActionBoundaryKind.CONTINUE
                    )

                next_prompt_growth = 0
                if actual_index + 1 < len(actual_calls):
                    next_request = legacy_requests[actual_calls[actual_index + 1].request_id]
                    next_prompt_growth = max(
                        0,
                        len(next_request.prompt_token_symbols)
                        - self._common_prefix(commit_path, next_request.prompt_token_symbols),
                    )
                frozen_calls.append(
                    FrozenLLMCallDemand(
                        call_ordinal=frozen_ordinal,
                        prompt_tokens=call.prompt_tokens,
                        incremental_prompt_tokens=incremental_prompt,
                        output_tokens=call.output_tokens,
                        parent_reentry_prompt_growth_tokens=(
                            next_prompt_growth if spawn_targets or waits else 0
                        ),
                        boundary=boundary,
                    )
                )
                physical_calls.append(
                    FrozenPhysicalCall(
                        invocation=key,
                        call_ordinal=frozen_ordinal,
                        trace_request_ordinal=trace_request_ordinal,
                        runtime_context_epoch=(call.submit.context_epoch or 0),
                        observed_cache_hit_tokens=call.observed_cache_hit_tokens,
                        observed_unique_growth_bytes=legacy_request.kv_growth_bytes,
                        prompt_token_symbols=prompt_path,
                        cache_commit_token_symbols=commit_path,
                        partial_cache_commit_token_symbols=(
                            legacy_request.partial_cache_commit_token_symbols
                        ),
                    )
                )
                trace_request_ordinal += 1
                frozen_tools.extend(
                    self._freeze_tools(
                        tool_records,
                        starts_after_call_ordinal=frozen_ordinal,
                        result_prompt_tokens=next_prompt_growth,
                    )
                )

                if spawn_targets and waits:
                    frozen_calls.append(
                        FrozenLLMCallDemand(
                            call_ordinal=frozen_ordinal + 1,
                            prompt_tokens=0,
                            incremental_prompt_tokens=0,
                            output_tokens=0,
                            parent_reentry_prompt_growth_tokens=next_prompt_growth,
                            boundary=FrozenActionBoundary(
                                kind=FrozenActionBoundaryKind.JOIN,
                                joins=tuple(
                                    sorted(
                                        join_by_runtime_id[item.join_id]
                                        for item in waits
                                    )
                                ),
                            ),
                        )
                    )
                    synthetic_join_count += 1

                if composite_terminal:
                    parent = creation.parent_invocation_id
                    synthetic_ordinal = frozen_ordinal + 1 + int(
                        bool(spawn_targets and waits)
                    )
                    frozen_calls.append(
                        FrozenLLMCallDemand(
                            call_ordinal=synthetic_ordinal,
                            prompt_tokens=0,
                            incremental_prompt_tokens=0,
                            output_tokens=0,
                            parent_reentry_prompt_growth_tokens=0,
                            boundary=FrozenActionBoundary(
                                kind=(
                                    FrozenActionBoundaryKind.FINAL
                                    if parent is None
                                    else FrozenActionBoundaryKind.RETURN
                                ),
                                target_invocations=(
                                    (invocation_keys[parent],)
                                    if parent is not None
                                    else ()
                                ),
                            ),
                        )
                    )
                    synthetic_terminal_count += 1

            relation = FrozenInvocationRelation(
                (creation.relation_type or RelationType.ROOT).value
            )
            context_mode = FrozenContextMode(
                (creation.context_mode or ContextMode.FRESH).value
            )
            parent_key = (
                invocation_keys[creation.parent_invocation_id]
                if creation.parent_invocation_id is not None
                else None
            )
            semantic_owner = (
                key
                if context_mode in {FrozenContextMode.FRESH, FrozenContextMode.FORK}
                else self._semantic_owner(parent_key, frozen_invocations)
            )
            frozen_invocations.append(
                FrozenInvocationDemand(
                    key=key,
                    agent_definition_id=creation.agent_definition_id or "unknown-agent",
                    relation=relation,
                    context_mode=context_mode,
                    parent=parent_key,
                    semantic_owner=semantic_owner,
                    calls=tuple(frozen_calls),
                    tools=tuple(frozen_tools),
                )
            )

        truth = FrozenAgentDemand(
            provenance=provenance,
            invocations=tuple(frozen_invocations),
            joins=tuple(joins),
        )
        sidecar = FrozenPhysicalSidecar(
            truth_id=truth.truth_id,
            truth_digest=truth.truth_digest,
            source_trace_id=source_trace_id,
            kv_bytes_per_token=int(legacy.workload.metadata["kv_bytes_per_token"]),
            initial_radix_state=initial_radix_state,
            calls=tuple(physical_calls),
        )
        releases = self._workflow_releases(events, workflow_instance)
        return OracleTruthExportResult(
            truth=truth,
            physical_sidecar=sidecar,
            workflow_release_ms=releases,
            actual_call_count=len(physical_calls),
            synthetic_join_boundary_count=synthetic_join_count,
            synthetic_terminal_boundary_count=synthetic_terminal_count,
            tool_count=sum(len(item.tools) for item in frozen_invocations),
            spawn_count=spawn_count,
            join_count=len(joins),
        )

    @staticmethod
    def _load_manifest(path: Path) -> dict[str, object]:
        raw = json.loads(path.expanduser().read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("workloads"), list):
            raise OracleTruthExportError("workload manifest is invalid")
        return raw

    @staticmethod
    def _workflow_instance_map(
        events: tuple[RuntimeEvent, ...], manifest: Mapping[str, object]
    ) -> dict[str, str]:
        instances = {
            str(item["instance_id"])
            for item in manifest["workloads"]  # type: ignore[index]
            if isinstance(item, Mapping) and item.get("instance_id")
        }
        workflows = sorted({event.workflow_id for event in events})
        result = {}
        for workflow in workflows:
            matches = [item for item in instances if item in workflow]
            if len(matches) != 1:
                raise OracleTruthExportError(
                    f"workflow does not map to exactly one manifest instance: {workflow}"
                )
            result[workflow] = matches[0]
        if len(set(result.values())) != len(result):
            raise OracleTruthExportError("runtime trace repeats a workload instance")
        return result

    @staticmethod
    def _logical_keys(
        events: tuple[RuntimeEvent, ...], workflow_instance: Mapping[str, str]
    ) -> tuple[dict[str, LogicalInvocationKey], dict[str, RuntimeEvent]]:
        creates = [event for event in events if event.kind == RuntimeEventKind.INVOCATION_CREATE]
        by_runtime = {}
        for event in creates:
            if event.invocation_id is None or event.invocation_id in by_runtime:
                raise OracleTruthExportError("invocation create identity is missing or duplicate")
            by_runtime[event.invocation_id] = event
        child_order: dict[str, dict[str, int]] = defaultdict(dict)
        for parent in by_runtime:
            children = sorted(
                (
                    item
                    for item in creates
                    if item.parent_invocation_id == parent
                ),
                key=lambda item: (item.ts_ms, item.event_id),
            )
            child_order[parent] = {
                item.invocation_id: index  # type: ignore[misc]
                for index, item in enumerate(children)
            }
        per_workflow_order: dict[str, dict[str, int]] = defaultdict(dict)
        for workflow in workflow_instance:
            ordered = sorted(
                (item for item in creates if item.workflow_id == workflow),
                key=lambda item: (item.ts_ms, item.event_id),
            )
            per_workflow_order[workflow] = {
                item.invocation_id: index  # type: ignore[misc]
                for index, item in enumerate(ordered)
            }

        cache: dict[str, LogicalInvocationKey] = {}

        def make(runtime_id: str) -> LogicalInvocationKey:
            if runtime_id in cache:
                return cache[runtime_id]
            event = by_runtime.get(runtime_id)
            if event is None:
                raise OracleTruthExportError(f"orphan invocation: {runtime_id}")
            if event.parent_invocation_id is None:
                path = (event.agent_definition_id or "root",)
                spawn_ordinal = 0
            else:
                parent = make(event.parent_invocation_id)
                spawn_ordinal = child_order[event.parent_invocation_id][runtime_id]
                path = parent.canonical_agent_path + (
                    f"{event.agent_definition_id or 'unknown'}[{spawn_ordinal}]",
                )
            key = LogicalInvocationKey(
                workload_instance=workflow_instance[event.workflow_id],
                canonical_agent_path=path,
                parent_spawn_ordinal=spawn_ordinal,
                invocation_ordinal=per_workflow_order[event.workflow_id][runtime_id],
                context_epoch=event.context_epoch or 0,
            )
            cache[runtime_id] = key
            return key

        for runtime_id in by_runtime:
            make(runtime_id)
        if len(set(cache.values())) != len(cache):
            raise OracleTruthExportError("stable logical invocation key collision")
        return cache, by_runtime

    @staticmethod
    def _calls(events: tuple[RuntimeEvent, ...]) -> dict[str, tuple[_CallRecord, ...]]:
        submits: dict[str, RuntimeEvent] = {}
        results: dict[str, RuntimeEvent] = {}
        for event in events:
            if event.kind not in {RuntimeEventKind.LLM_SUBMIT, RuntimeEventKind.LLM_RESULT}:
                continue
            request_id = event.attributes.get("request_id")
            if not isinstance(request_id, str):
                raise OracleTruthExportError("LLM event lacks request_id")
            target = submits if event.kind == RuntimeEventKind.LLM_SUBMIT else results
            if request_id in target:
                raise OracleTruthExportError(f"duplicate LLM event: {request_id}")
            target[request_id] = event
        if submits.keys() != results.keys():
            raise OracleTruthExportError("LLM submit/result closure is incomplete")
        by_invocation: dict[str, list[_CallRecord]] = defaultdict(list)
        for request_id, submit in submits.items():
            if submit.invocation_id is None:
                raise OracleTruthExportError("LLM submit lacks invocation identity")
            result = results[request_id]
            if result.ts_ms < submit.ts_ms:
                raise OracleTruthExportError("LLM result precedes submit")
            by_invocation[submit.invocation_id].append(
                _CallRecord(
                    request_id=request_id,
                    submit=submit,
                    result=result,
                    prompt_tokens=int(submit.attributes["prompt_tokens"]),
                    output_tokens=int(result.attributes["output_tokens"]),
                    observed_cache_hit_tokens=int(
                        submit.attributes.get("cache_hit_tokens", 0)
                    ),
                )
            )
        return {
            invocation: tuple(sorted(values, key=lambda item: item.submit.ts_ms))
            for invocation, values in by_invocation.items()
        }

    @staticmethod
    def _tools(
        events: tuple[RuntimeEvent, ...],
        calls_by_invocation: Mapping[str, tuple[_CallRecord, ...]],
    ) -> dict[str, tuple[_ToolRecord, ...]]:
        starts = {}
        ends = {}
        for event in events:
            if event.kind not in {RuntimeEventKind.TOOL_START, RuntimeEventKind.TOOL_END}:
                continue
            tool_id = event.attributes.get("tool_call_id")
            if not isinstance(tool_id, str):
                raise OracleTruthExportError("tool event lacks tool_call_id")
            target = starts if event.kind == RuntimeEventKind.TOOL_START else ends
            if tool_id in target:
                raise OracleTruthExportError(f"duplicate tool event: {tool_id}")
            target[tool_id] = event
        if starts.keys() != ends.keys():
            raise OracleTruthExportError("tool start/end closure is incomplete")
        by_invocation: dict[str, list[tuple[RuntimeEvent, RuntimeEvent, int]]] = defaultdict(list)
        for tool_id, start in starts.items():
            if start.invocation_id is None or ends[tool_id].ts_ms < start.ts_ms:
                raise OracleTruthExportError("invalid tool interval")
            calls = calls_by_invocation.get(start.invocation_id, ())
            eligible = [
                index for index, call in enumerate(calls) if call.result.ts_ms <= start.ts_ms + 1e-3
            ]
            if not eligible:
                raise OracleTruthExportError("tool starts before invocation LLM result")
            actual_index = max(eligible)
            next_submit = (
                calls[actual_index + 1].submit.ts_ms
                if actual_index + 1 < len(calls)
                else float("inf")
            )
            if start.ts_ms > next_submit + 1e-3:
                raise OracleTruthExportError("tool cannot be bound to an LLM boundary")
            by_invocation[start.invocation_id].append((start, ends[tool_id], actual_index))
        result = {}
        for invocation, values in by_invocation.items():
            ordered = sorted(values, key=lambda item: (item[0].ts_ms, item[0].event_id))
            result[invocation] = tuple(
                _ToolRecord(start, end, ordinal, actual_index)
                for ordinal, (start, end, actual_index) in enumerate(ordered)
            )
        return result

    @staticmethod
    def _joins(
        events: tuple[RuntimeEvent, ...],
        invocation_keys: Mapping[str, LogicalInvocationKey],
    ) -> tuple[
        tuple[FrozenJoinDemand, ...],
        dict[str, FrozenJoinKey],
        dict[str, tuple[RuntimeEvent, ...]],
    ]:
        creates = {
            event.join_id: event
            for event in events
            if event.kind == RuntimeEventKind.JOIN_CREATE and event.join_id is not None
        }
        satisfied = {
            event.join_id
            for event in events
            if event.kind == RuntimeEventKind.JOIN_SATISFIED and event.join_id is not None
        }
        waits_by_join: dict[str, list[RuntimeEvent]] = defaultdict(list)
        waits_by_invocation: dict[str, list[RuntimeEvent]] = defaultdict(list)
        for event in events:
            if event.kind != RuntimeEventKind.JOIN_WAIT:
                continue
            if event.join_id is None or event.invocation_id is None:
                raise OracleTruthExportError("JOIN_WAIT identity is incomplete")
            waits_by_join[event.join_id].append(event)
            waits_by_invocation[event.invocation_id].append(event)
        if creates.keys() != satisfied or creates.keys() != waits_by_join.keys():
            raise OracleTruthExportError("JOIN create/wait/satisfied closure is incomplete")
        join_key_by_runtime: dict[str, FrozenJoinKey] = {}
        owner_counts: dict[LogicalInvocationKey, int] = defaultdict(int)
        for join_id, create in sorted(creates.items(), key=lambda item: item[1].ts_ms):
            waiters = waits_by_join[join_id]
            owner_runtime = waiters[0].invocation_id
            assert owner_runtime is not None
            owner = invocation_keys[owner_runtime]
            ordinal = owner_counts[owner]
            owner_counts[owner] += 1
            join_key_by_runtime[join_id] = FrozenJoinKey(
                workload_instance=owner.workload_instance,
                owner=owner,
                join_ordinal=ordinal,
            )
        joins = []
        for join_id, create in sorted(creates.items(), key=lambda item: item[1].ts_ms):
            members = tuple(
                invocation_keys[item] for item in create.member_invocation_ids
            )
            waiters = tuple(
                invocation_keys[item.invocation_id]  # type: ignore[index]
                for item in waits_by_join[join_id]
            )
            joins.append(
                FrozenJoinDemand(
                    key=join_key_by_runtime[join_id],
                    mode=FrozenJoinMode(str(create.attributes.get("mode", "all"))),
                    members=members,
                    waiters=waiters,
                )
            )
        return (
            tuple(joins),
            join_key_by_runtime,
            {
                invocation: tuple(sorted(values, key=lambda item: item.ts_ms))
                for invocation, values in waits_by_invocation.items()
            },
        )

    @staticmethod
    def _events_after_call(
        events: tuple[RuntimeEvent, ...],
        call: _CallRecord,
        calls: tuple[_CallRecord, ...],
        actual_index: int,
    ) -> tuple[RuntimeEvent, ...]:
        end = (
            calls[actual_index + 1].submit.ts_ms
            if actual_index + 1 < len(calls)
            else float("inf")
        )
        return tuple(
            event
            for event in events
            if event.workflow_id == call.submit.workflow_id
            and call.result.ts_ms - 1e-3 <= event.ts_ms <= end + 1e-3
            and event.kind
            in {
                RuntimeEventKind.SPAWN,
                RuntimeEventKind.JOIN_WAIT,
                RuntimeEventKind.RETURN,
            }
            and (
                event.invocation_id == call.submit.invocation_id
                or event.kind == RuntimeEventKind.JOIN_WAIT
            )
        )

    @staticmethod
    def _join_waits_for_call(
        call: _CallRecord,
        calls: tuple[_CallRecord, ...],
        waits: Iterable[RuntimeEvent],
    ) -> tuple[RuntimeEvent, ...]:
        actual_index = calls.index(call)
        end = (
            calls[actual_index + 1].submit.ts_ms
            if actual_index + 1 < len(calls)
            else float("inf")
        )
        return tuple(
            item
            for item in waits
            if call.result.ts_ms - 1e-3 <= item.ts_ms <= end + 1e-3
        )

    @staticmethod
    def _freeze_tools(
        records: Iterable[_ToolRecord],
        *,
        starts_after_call_ordinal: int,
        result_prompt_tokens: int,
    ) -> tuple[FrozenToolDemand, ...]:
        values = tuple(records)
        if not values:
            return ()
        output_chars = [max(0, int(item.end.attributes.get("output_chars", 0))) for item in values]
        total_chars = sum(output_chars)
        remaining = result_prompt_tokens
        result = []
        for index, item in enumerate(values):
            if index == len(values) - 1:
                tokens = remaining
            elif total_chars:
                tokens = min(
                    remaining,
                    round(result_prompt_tokens * output_chars[index] / total_chars),
                )
            else:
                tokens = 0
            remaining -= tokens
            status = str(item.end.attributes.get("status", "success"))
            result.append(
                FrozenToolDemand(
                    tool_ordinal=item.tool_ordinal,
                    starts_after_call_ordinal=starts_after_call_ordinal,
                    tool_family=str(item.start.attributes.get("tool_family", "unknown")),
                    backend_class=str(item.start.attributes.get("backend_class", "unknown")),
                    service_duration_ms=float(item.end.ts_ms - item.start.ts_ms),
                    result_prompt_tokens=tokens,
                    outcome=(
                        FrozenToolOutcome.SUCCESS
                        if status == "success"
                        else FrozenToolOutcome.ERROR
                    ),
                )
            )
        return tuple(result)

    @staticmethod
    def _semantic_owner(
        parent: LogicalInvocationKey | None,
        already_built: Iterable[FrozenInvocationDemand],
    ) -> LogicalInvocationKey:
        if parent is None:
            raise OracleTruthExportError("RESUME invocation has no parent")
        for item in already_built:
            if item.key == parent:
                return item.semantic_owner
        raise OracleTruthExportError("RESUME parent has not been exported")

    @staticmethod
    def _common_prefix(left: tuple[int, ...], right: tuple[int, ...]) -> int:
        count = 0
        for left_item, right_item in zip(left, right):
            if left_item != right_item:
                break
            count += 1
        return count

    @staticmethod
    def _workflow_releases(
        events: tuple[RuntimeEvent, ...], workflow_instance: Mapping[str, str]
    ) -> dict[str, float]:
        starts = {
            event.workflow_id: event.ts_ms
            for event in events
            if event.kind == RuntimeEventKind.WORKFLOW_START
        }
        if starts.keys() != workflow_instance.keys():
            raise OracleTruthExportError("workflow start coverage is incomplete")
        origin = min(starts.values())
        return {
            workflow_instance[workflow]: timestamp - origin
            for workflow, timestamp in starts.items()
        }
