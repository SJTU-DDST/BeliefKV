from dataclasses import replace
import asyncio
import json
from pathlib import Path
import sys
import time

import pytest

from beliefkv.core.events import (
    RelationType, RuntimeEvent, RuntimeEventKind as Kind,
)
from beliefkv.experiments.causal_replay import (
    CausalReplayError, compile_causal_replay, evaluate_causal_plan,
    event_node_id, execute_causal_replay, NativeReplayCompletion, request_node_id,
)
from beliefkv.runtime.clock_evidence import local_monotonic_clock_domain
from scripts.audit_causal_replay import audit, main


class Trace:
    def __init__(self, workflow="w"):
        self.workflow, self.events, self.native = workflow, [], []
        self.named = {}

    def add(self, name, kind, when, **kwargs):
        event = RuntimeEvent(
            f"{self.workflow}:{name}", float(when), kind, self.workflow, **kwargs,
        )
        self.events.append(event)
        self.named[name] = event
        return event

    def create(self, invocation, when, *, parent=None, internal=False, context=None):
        return self.add(
            "create-" + invocation, Kind.INVOCATION_CREATE, when,
            invocation_id=invocation, context_id=context or "ctx-" + invocation,
            context_epoch=0, parent_invocation_id=parent,
            relation_type=(
                RelationType.ROOT if parent is None else
                RelationType.CALL if internal else RelationType.SPAWN
            ),
            attributes={"runtime_internal": internal},
        )

    def launch(self, invocation, parent, when, *, internal=False):
        return self.add(
            "launch-" + invocation, Kind.CALL if internal else Kind.SPAWN, when,
            invocation_id=parent, target_invocation_id=invocation,
            attributes={"runtime_internal": internal},
        )

    def model(self, request, invocation, submit, complete, result, *, epoch=0, context=None):
        identity = {
            "invocation_id": invocation, "context_id": context or "ctx-" + invocation,
            "context_epoch": epoch,
        }
        for kind, name, when in (
            (Kind.LLM_SUBMIT, "submit-", submit), (Kind.LLM_RESULT, "result-", result),
        ):
            self.add(
                name + request, kind, when, **identity,
                attributes={"request_id": request},
            )
            self.native.append({
                "workflow_id": self.workflow, **identity, "kind": kind.value,
                "attributes": {
                    "request_id": request,
                    **({"prompt_tokens": 100_000} if kind is Kind.LLM_SUBMIT else {
                        "output_tokens": 4, "complete_monotonic_ms": float(complete),
                        "monotonic_clock_domain": "one-boot-and-time-namespace",
                    }),
                },
            })

    def tool(self, name, invocation, start, duration):
        self.add(
            "tool-start-" + name, Kind.TOOL_START, start, invocation_id=invocation,
            attributes={"tool_run_id": name},
        )
        self.add(
            "tool-end-" + name, Kind.TOOL_END, start + duration, invocation_id=invocation,
            attributes={"tool_run_id": name, "duration_ms": float(duration)},
        )

    def join(self, name, parent, members, created, satisfied):
        self.add(
            "join-create-" + name, Kind.JOIN_CREATE, created, join_id=name,
            member_invocation_ids=tuple(members), attributes={"mode": "all"},
        )
        self.add(
            "join-wait-" + name, Kind.JOIN_WAIT, created + .1,
            join_id=name, invocation_id=parent,
        )
        self.add(
            "join-end-" + name, Kind.JOIN_SATISFIED, satisfied, join_id=name,
        )

    def returned(self, invocation, when):
        self.add("return-" + invocation, Kind.RETURN, when, invocation_id=invocation)

    def ordered(self):
        return sorted(self.events, key=lambda event: event.ts_ms)


def mixed_trace():
    trace = Trace()
    trace.add("start", Kind.WORKFLOW_START, 0)
    trace.create("root", 1)
    trace.model("root-first", "root", 2, 10, 12)
    for invocation, created in (("a", 13), ("b", 13.2)):
        trace.create(invocation, created, parent="root")
        trace.launch(invocation, "root", created + .1)
    trace.join("root-join", "root", ("a", "b"), 14, 73)
    # These starts deliberately follow JOIN_WAIT, as in the latest Full trace.
    trace.tool("parallel-one", "root", 14.2, 55)
    trace.tool("parallel-two", "root", 14.3, 10)
    trace.model("a-first", "a", 16, 25, 26)
    trace.model("b-last", "b", 16.5, 40, 42)
    trace.create("grandchild", 27, parent="a")
    trace.launch("grandchild", "a", 27.1)
    trace.join("nested-join", "a", ("grandchild",), 28, 61)
    trace.model("grandchild-last", "grandchild", 29, 55, 57)
    trace.returned("b", 45)
    trace.returned("grandchild", 60)
    trace.model("a-last", "a", 62, 68, 69)
    trace.returned("a", 72)
    trace.model("root-joined", "root", 75, 82, 85)
    trace.create("summary", 86, parent="root", internal=True)
    trace.launch("summary", "root", 86.1, internal=True)
    trace.model("summary-call", "summary", 87, 90, 91)
    trace.returned("summary", 92)
    trace.add(
        "compact", Kind.CONTEXT_COMPACT, 93, invocation_id="root",
        context_id="ctx-root", context_epoch=1,
    )
    trace.model("root-last", "root", 95, 100, 102, epoch=1)
    trace.returned("root", 103)
    trace.add("end", Kind.WORKFLOW_END, 104)
    return trace


def sequential_trace(original_service=100.):
    trace = Trace()
    trace.add("start", Kind.WORKFLOW_START, 0)
    trace.create("root", 1)
    trace.model("r0", "root", 2, 2 + original_service, 7 + original_service)
    trace.tool("wait", "root", 8 + original_service, 10)
    trace.model(
        "r1", "root", 19 + original_service,
        19 + 2 * original_service, 24 + 2 * original_service,
    )
    trace.returned("root", 25 + 2 * original_service)
    trace.add("end", Kind.WORKFLOW_END, 26 + 2 * original_service)
    return trace


def ancestors(plan, node):
    graph = plan.dependency_graph()
    seen, remaining = set(), list(graph[node])
    while remaining:
        current = remaining.pop()
        if current not in seen:
            seen.add(current)
            remaining.extend(graph[current])
    return seen


def test_mixed_parallel_tools_spawn_nested_join_and_compression_are_all_preserved():
    trace = mixed_trace()
    plan = compile_causal_replay(trace.ordered(), trace.native)
    summary = plan.summary()
    assert summary["requests"] == 8
    assert summary["prompt_tokens"] == 800_000
    assert summary["output_tokens"] == 32
    assert summary["event_counts"]["join_create"] == 2
    assert summary["event_counts"]["tool_start"] == 2
    assert summary["event_counts"]["context_compact"] == 1
    assert summary["native_completion_boundaries_verified"]
    assert not summary["gpu_replay_qualified"]
    assert not summary["performance_accepted"]
    graph = plan.dependency_graph()
    first = event_node_id(trace.named["tool-start-parallel-one"].event_id)
    second = event_node_id(trace.named["tool-start-parallel-two"].event_id)
    join = event_node_id(trace.named["join-end-root-join"].event_id)
    assert join not in ancestors(plan, first)
    assert first not in ancestors(plan, second)
    assert second not in ancestors(plan, first)
    parent = event_node_id(trace.named["submit-root-joined"].event_id)
    assert join in ancestors(plan, parent)
    for tool in ("parallel-one", "parallel-two"):
        assert event_node_id(trace.named["tool-end-" + tool].event_id) in ancestors(plan, parent)
    compact = event_node_id(trace.named["compact"].event_id)
    assert event_node_id(trace.named["return-summary"].event_id) in ancestors(plan, compact)
    assert compact in ancestors(plan, request_node_id("root-last"))
    assert graph[request_node_id("summary-call")] == (
        event_node_id(trace.named["submit-summary-call"].event_id),
    )


def test_gpu_time_is_not_baked_into_later_request_release():
    plans = []
    for original in (100., 10_000.):
        trace = sequential_trace(original)
        plans.append(compile_causal_replay(trace.ordered(), trace.native))
    for plan in plans:
        assert all(node.delay_ms == 0. for node in plan.nodes if node.request_id)
    expected = evaluate_causal_plan(plans[0], {"r0": 5., "r1": 7.})
    actual = evaluate_causal_plan(plans[1], {"r0": 5., "r1": 7.})
    assert actual == expected
    assert expected["workflow_elapsed_ms"] == {"w": 38.}


@pytest.mark.parametrize("slow_request", ("a-last", "b-last", "grandchild-last"))
def test_parent_unlocks_after_actual_replay_members_not_recorded_join_time(slow_request):
    trace = mixed_trace()
    plan = compile_causal_replay(trace.ordered(), trace.native)
    service = dict.fromkeys(plan.request_usage, 0.)
    baseline = evaluate_causal_plan(plan, service)
    service[slow_request] = 500.
    delayed = evaluate_causal_plan(plan, service)
    parent = event_node_id(trace.named["submit-root-joined"].event_id)
    unrelated_tool = event_node_id(trace.named["tool-end-parallel-two"].event_id)
    assert delayed["node_completed_ms"][parent] > baseline["node_completed_ms"][parent] + 400.
    assert delayed["node_completed_ms"][unrelated_tool] == baseline["node_completed_ms"][unrelated_tool]


def test_diagnostic_progress_does_not_create_a_frozen_decode_timer():
    trace = sequential_trace()
    trace.add(
        "diagnostic", Kind.STRUCTURED_ACTION, 50., invocation_id="root",
        attributes={"diagnostic_only": True, "request_id": "r0"},
    )
    plan = compile_causal_replay(trace.ordered(), trace.native)
    assert plan.omitted_diagnostic_events == 1
    assert event_node_id(trace.named["diagnostic"].event_id) not in plan.dependency_graph()


def test_non_diagnostic_completion_notice_stays_after_real_native_completion():
    trace = sequential_trace()
    trace.add(
        "final-notice", Kind.STRUCTURED_ACTION, 104., invocation_id="root",
        attributes={"request_id": "r0", "beliefkv_child_completion_intent": True},
    )
    plan = compile_causal_replay(trace.ordered(), trace.native)
    notice = event_node_id(trace.named["final-notice"].event_id)
    node = next(node for node in plan.nodes if node.node_id == notice)
    assert node.dependencies == (request_node_id("r0"),)
    assert node.delay_ms == 2.


def test_post_join_stage_notice_does_not_reintroduce_original_child_service_time():
    trace = mixed_trace()
    trace.add(
        "joined-stage", Kind.STRUCTURED_ACTION, 74., invocation_id="root",
        attributes={"beliefkv_child_completion_intent": True},
    )
    plan = compile_causal_replay(trace.ordered(), trace.native)
    stage = next(
        node for node in plan.nodes
        if node.node_id == event_node_id(trace.named["joined-stage"].event_id)
    )
    assert stage.delay_ms == 1.
    assert event_node_id(trace.named["join-end-root-join"].event_id) in stage.dependencies


def test_early_semantic_action_without_a_request_boundary_is_unknown():
    trace = sequential_trace()
    trace.add(
        "unbound-action", Kind.STRUCTURED_ACTION, 50., invocation_id="root",
        attributes={"beliefkv_child_completion_intent": True},
    )
    with pytest.raises(CausalReplayError, match="token boundary"):
        compile_causal_replay(trace.ordered(), trace.native)


@pytest.mark.parametrize("change", (
    "missing_result", "missing_tool_end", "missing_return", "missing_join_end",
    "duplicate_event", "foreign_identity", "missing_clock", "different_clock",
    "completion_before_submit", "completion_after_client", "tool_duration",
    "any_join", "foreign_join_member", "early_join_end", "blocked_submit",
    "unsupported_message", "request_population",
))
def test_incomplete_or_inconsistent_causal_evidence_is_rejected(change):
    trace = mixed_trace()
    if change in ("missing_result", "missing_tool_end", "missing_return", "missing_join_end"):
        name = {
            "missing_result": "result-a-last", "missing_tool_end": "tool-end-parallel-one",
            "missing_return": "return-a", "missing_join_end": "join-end-root-join",
        }[change]
        trace.events.remove(trace.named[name])
    elif change == "duplicate_event":
        trace.events.append(trace.events[0])
    elif change == "request_population":
        trace.native.pop()
    elif change in ("missing_clock", "different_clock", "completion_before_submit",
                    "completion_after_client", "foreign_identity"):
        for row in trace.native:
            if row["attributes"]["request_id"] != "a-last":
                continue
            if change == "foreign_identity":
                row["context_id"] = "foreign"
            elif row["kind"] == "llm_result":
                if change == "missing_clock":
                    del row["attributes"]["complete_monotonic_ms"]
                elif change == "different_clock":
                    row["attributes"]["monotonic_clock_domain"] = "other-boot"
                else:
                    row["attributes"]["complete_monotonic_ms"] = (
                        0. if change == "completion_before_submit" else 1000.
                    )
    elif change == "tool_duration":
        old = trace.named["tool-end-parallel-one"]
        trace.events[trace.events.index(old)] = replace(
            old, attributes={**old.attributes, "duration_ms": 1000.},
        )
    elif change in ("any_join", "foreign_join_member", "early_join_end"):
        name = "join-end-root-join" if change == "early_join_end" else "join-create-root-join"
        old = trace.named[name]
        kwargs = (
            {"attributes": {"mode": "any"}} if change == "any_join" else
            {"member_invocation_ids": ("grandchild",)} if change == "foreign_join_member" else
            {"ts_ms": 30.}
        )
        trace.events[trace.events.index(old)] = replace(old, **kwargs)
    elif change == "blocked_submit":
        old = trace.named["submit-root-joined"]
        trace.events[trace.events.index(old)] = replace(old, ts_ms=20.)
    else:
        trace.add("message", Kind.MESSAGE, 1.5, invocation_id="root", target_invocation_id="root")
    with pytest.raises(CausalReplayError):
        compile_causal_replay(trace.ordered(), trace.native)


@pytest.mark.parametrize("service", (
    {}, {"r0": 1.}, {"r0": 1., "r1": -1.},
    {"r0": 1., "r1": float("nan")}, {"r0": 1., "r1": True},
))
def test_virtual_clock_requires_complete_finite_service_fixture(service):
    trace = sequential_trace()
    plan = compile_causal_replay(trace.ordered(), trace.native)
    with pytest.raises(CausalReplayError, match="service fixture"):
        evaluate_causal_plan(plan, service)


def test_workflows_are_not_serialized_by_global_trace_order():
    first, second = sequential_trace(), Trace("other")
    second.add("start", Kind.WORKFLOW_START, 3.)
    second.create("root", 4.)
    second.model("independent", "root", 5., 10., 11.)
    second.returned("root", 12.)
    second.add("end", Kind.WORKFLOW_END, 13.)
    plan = compile_causal_replay(
        first.ordered() + second.ordered(), first.native + second.native,
    )
    service = dict.fromkeys(plan.request_usage, 0.)
    before = evaluate_causal_plan(plan, service)
    service["r0"] = 1000.
    after = evaluate_causal_plan(plan, service)
    assert after["workflow_elapsed_ms"]["other"] == before["workflow_elapsed_ms"]["other"]
    assert after["workflow_elapsed_ms"]["w"] > before["workflow_elapsed_ms"]["w"]


def write_arm(tmp_path, trace):
    arm = tmp_path / "arm"
    server, client = arm / "server", arm / "client_1"
    server.mkdir(parents=True)
    directory = client / "workflows/task"
    directory.mkdir(parents=True)
    (directory / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(event.to_dict()) + "\n" for event in trace.ordered()),
    )
    (server / "runtime_events.sglang.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in trace.native),
    )
    (client / "summary.json").write_text(json.dumps({
        "completed_workflows": 1, "workflow_count": 1,
    }))
    return arm


def test_historical_graph_without_exact_tokens_cannot_claim_gpu_replay(tmp_path):
    arm = write_arm(tmp_path, mixed_trace())
    report = audit(arm)
    assert report["causal_graph_compiled"] and report["requests"] == 8
    assert report["zero_model_service_fixture"]["completed_workflows"] == 1
    assert not report["token_evidence_complete"]
    assert report["token_evidence_reason"] == "exact_prompt_and_output_capture_unavailable"
    assert not report["gpu_replay_qualified"]
    assert report["input_jsonl_prefixes"] and report["client_summary_sha256"]


def test_graph_cli_preserves_a_negative_report(tmp_path, monkeypatch):
    trace = mixed_trace()
    trace.events.remove(trace.named["return-grandchild"])
    arm = write_arm(tmp_path, trace)
    output = tmp_path / "negative.json"
    monkeypatch.setattr(sys, "argv", [
        "audit_causal_replay", "--arm", str(arm), "--output", str(output), "--require-graph",
    ])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    report = json.loads(output.read_text())
    assert not report["causal_graph_compiled"] and not report["gpu_replay_qualified"]
    assert report["reason"] == "invalid_causal_dependencies"


def test_async_driver_waits_for_actual_child_completion_and_reissues_only_visible_events():
    trace = mixed_trace()
    plan = compile_causal_replay(trace.ordered(), trace.native)

    async def scenario():
        blocked, release, seen = asyncio.Event(), asyncio.Event(), []
        emitted = []

        async def backend(request, submit):
            seen.append(request)
            assert submit.kind is Kind.LLM_SUBMIT
            assert submit.attributes["request_id"] == request
            if request == "b-last":
                blocked.set()
                await release.wait()
            prompt, output = plan.request_usage[request]
            return NativeReplayCompletion(
                request, time.monotonic() * 1000., local_monotonic_clock_domain(),
                prompt, output,
            )

        async def emit(event):
            emitted.append(event)

        task = asyncio.create_task(execute_causal_replay(plan, backend, emit))
        await asyncio.wait_for(blocked.wait(), timeout=2.)
        await asyncio.sleep(.02)
        assert "root-joined" not in seen
        assert not any(
            event.kind is Kind.JOIN_SATISFIED and event.join_id == "root-join"
            for event in emitted
        )
        release.set()
        report = await asyncio.wait_for(task, timeout=2.)
        assert report["completed_requests"] == 8
        assert set(seen) == set(plan.request_usage)
        assert not report["exact_tokens_verified"] and not report["gpu_replay_qualified"]
        assert report["causal_execution_completed"]
        by_id = {event.event_id: event for event in emitted}
        joined = by_id[trace.named["join-end-root-join"].event_id]
        parent = by_id[trace.named["submit-root-joined"].event_id]
        returned = by_id[trace.named["return-b"].event_id]
        assert returned.ts_ms <= joined.ts_ms <= parent.ts_ms
        for tool in ("parallel-one", "parallel-two"):
            start = by_id[trace.named["tool-start-" + tool].event_id]
            end = by_id[trace.named["tool-end-" + tool].event_id]
            assert end.attributes["duration_ms"] == end.ts_ms - start.ts_ms

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ("request", "clock", "count", "future", "missing"))
def test_bad_backend_receipts_stop_before_any_result_or_successor(change):
    trace = sequential_trace()
    plan = compile_causal_replay(trace.ordered(), trace.native)

    async def scenario():
        emitted, dispatched = [], []

        async def backend(request, submit):
            dispatched.append(request)
            receipt = NativeReplayCompletion(
                request, time.monotonic() * 1000., local_monotonic_clock_domain(), 100_000, 4,
            )
            if change == "request":
                return replace(receipt, request_id="foreign")
            if change == "clock":
                return replace(receipt, clock_domain="foreign")
            if change == "count":
                return replace(receipt, output_tokens=3)
            if change == "future":
                return replace(receipt, complete_monotonic_ms=receipt.complete_monotonic_ms + 10_000.)
            return None

        async def emit(event):
            emitted.append(event)

        with pytest.raises(CausalReplayError, match="native replay receipt"):
            await execute_causal_replay(plan, backend, emit)
        assert dispatched == ["r0"]
        assert not any(event.kind is Kind.LLM_RESULT for event in emitted)

    asyncio.run(scenario())


def test_native_completion_clock_prevents_transport_delay_from_being_added_twice(monkeypatch):
    trace = sequential_trace()
    plan = compile_causal_replay(trace.ordered(), trace.native)

    async def scenario():
        finished, returned, result_times, result_sleeps = {}, {}, {}, []
        original_sleep = asyncio.sleep
        result_tasks = {
            "causal_replay:" + event_node_id(trace.named["result-" + request].event_id)
            for request in ("r0", "r1")
        }

        async def observed_sleep(seconds):
            if asyncio.current_task().get_name() in result_tasks:
                result_sleeps.append(seconds)
            await original_sleep(seconds)

        monkeypatch.setattr(asyncio, "sleep", observed_sleep)

        async def backend(request, submit):
            finished[request] = time.monotonic() * 1000.
            await original_sleep(.02)
            returned[request] = time.monotonic() * 1000.
            return NativeReplayCompletion(
                request, finished[request], local_monotonic_clock_domain(), 100_000, 4,
            )

        async def emit(event):
            if event.kind is Kind.LLM_RESULT:
                result_times[event.attributes["request_id"]] = event.ts_ms

        await execute_causal_replay(plan, backend, emit)
        for request in finished:
            assert result_times[request] >= returned[request]
        assert not result_sleeps

    asyncio.run(scenario())


def test_backend_failure_cancels_parallel_requests_and_tool_timers():
    trace = mixed_trace()
    plan = compile_causal_replay(trace.ordered(), trace.native)

    async def scenario():
        entered, cancelled = asyncio.Event(), asyncio.Event()
        seen = []

        async def backend(request, submit):
            seen.append(request)
            if request == "a-first":
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            if request == "b-last":
                await entered.wait()
                raise RuntimeError("fixture backend failed")
            prompt, output = plan.request_usage[request]
            return NativeReplayCompletion(
                request, time.monotonic() * 1000., local_monotonic_clock_domain(), prompt, output,
            )

        async def emit(event):
            pass

        with pytest.raises(RuntimeError, match="fixture backend failed"):
            await asyncio.wait_for(execute_causal_replay(plan, backend, emit), timeout=2.)
        assert cancelled.is_set()
        assert "root-joined" not in seen and "root-last" not in seen

    asyncio.run(scenario())


def test_graph_cli_records_a_bad_source_without_claiming_token_or_gpu_fidelity(
    tmp_path, monkeypatch,
):
    arm = tmp_path / "missing-source"
    output = tmp_path / "missing.json"
    monkeypatch.setattr(sys, "argv", [
        "audit_causal_replay", "--arm", str(arm), "--output", str(output), "--require-graph",
    ])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    report = json.loads(output.read_text())
    assert report["reason"] == "invalid_causal_source"
    assert not report["causal_graph_compiled"] and not report["gpu_replay_qualified"]
