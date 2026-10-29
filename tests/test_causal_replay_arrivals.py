import asyncio
from dataclasses import replace
import json
import random
import time

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.causal_replay import (
    NativeReplayCompletion, compile_causal_replay, evaluate_causal_plan, execute_causal_replay,
)
from beliefkv.experiments.replay_arrivals import (
    arrival_offsets_ms, load_arrival_plan, poisson_arrival_plan, validate_arrival_plan,
)
from beliefkv.experiments.replay_timing import AGENTX_TIMING
from beliefkv.runtime.clock_evidence import local_monotonic_clock_domain
from scripts import run_causal_comparison as comparison
from scripts.plot_causal_decode_diagnostics import match_contract
from tests.test_causal_replay import sequential_trace
from tests.test_causal_comparison import result_fixture


def two_workflows():
    trace = sequential_trace()
    events = [
        replace(event, event_id="b:" + event.event_id, workflow_id="b",
                attributes={**event.attributes, **(
                    {"request_id": "b:" + event.attributes["request_id"]}
                    if "request_id" in event.attributes else {}
                )})
        for event in trace.events
    ]
    native = [
        {**event, "workflow_id": "b", "attributes": {
            **event["attributes"], "request_id": "b:" + event["attributes"]["request_id"],
        }}
        for event in trace.native
    ]
    return compile_causal_replay([*trace.events, *events], [*trace.native, *native])


def test_common_poisson_plan_is_independent_of_policy_and_model_randomness(tmp_path):
    expected = poisson_arrival_plan(["b", "w"], rate_per_second=.2, seed=21)
    random.seed(999)
    for _ in range(100):
        random.random()
    assert expected == poisson_arrival_plan(["b", "w"], rate_per_second=.2, seed=21)
    faster = poisson_arrival_plan(["b", "w"], rate_per_second=.5, seed=21)
    assert arrival_offsets_ms(expected)["w"] / arrival_offsets_ms(faster)["w"] == pytest.approx(2.5)
    path = tmp_path / "arrivals.json"
    path.write_text(json.dumps(expected))
    assert load_arrival_plan(path, ["b", "w"]) == expected
    with pytest.raises(ValueError):
        load_arrival_plan(path, ["w", "b"])
    expected["arrival_schedule"][0]["offset_seconds"] += .1
    with pytest.raises(ValueError):
        validate_arrival_plan(expected, ["b", "w"])


def test_warm_start_uses_zero_time_initial_roots_without_consuming_random_intervals(tmp_path):
    identities = [f"workflow-{index}" for index in range(18)]
    plan = poisson_arrival_plan(
        identities, rate_per_second=1 / 60, seed=21, initial_workflows=10,
    )
    rows = plan["arrival_schedule"]
    assert plan["mode"] == "warm_start_poisson" and plan["schema_version"] == 2
    assert [row["offset_seconds"] for row in rows[:10]] == [0.] * 10
    generator, offset = random.Random(21), 0.
    for row in rows[10:]:
        offset += generator.expovariate(1 / 60)
        assert row["offset_seconds"] == offset
    assert offset == pytest.approx(431.7154977718898)
    path = tmp_path / "warm.json"
    path.write_text(json.dumps(plan))
    assert load_arrival_plan(path, identities) == plan
    assert poisson_arrival_plan(identities, rate_per_second=1 / 60, seed=21) == (
        poisson_arrival_plan(identities, rate_per_second=1 / 60, seed=21, initial_workflows=0)
    )


@pytest.mark.parametrize("initial", (True, -1, 3, 1.5, None))
def test_invalid_initial_population_cannot_release_any_root(initial):
    with pytest.raises(ValueError, match="initial workflows"):
        poisson_arrival_plan(["b", "w"], rate_per_second=.2, seed=21, initial_workflows=initial)


@pytest.mark.parametrize("fault", ("initial", "version", "zero_time", "seed", "mode"))
def test_modified_warm_start_manifest_cannot_change_comparison_work(fault):
    identities = ["a", "b", "c"]
    plan = poisson_arrival_plan(identities, rate_per_second=.2, seed=21, initial_workflows=2)
    if fault == "initial":
        plan["initial_workflows"] = 1
    elif fault == "version":
        plan["schema_version"] = 1
    elif fault == "zero_time":
        plan["arrival_schedule"][0]["offset_seconds"] = .1
    elif fault == "seed":
        plan["seed"] = 22
    else:
        plan["mode"] = "poisson"
    with pytest.raises(ValueError):
        validate_arrival_plan(plan, identities)


def test_simultaneous_warm_roots_keep_all_jct_and_dependency_boundaries():
    plan = two_workflows()
    arrivals = poisson_arrival_plan(["b", "w"], rate_per_second=1., seed=21, initial_workflows=2)
    observed = []

    async def emit(event):
        observed.append(event)

    async def serve(request, _submit):
        prompt, output = plan.request_usage[request]
        return NativeReplayCompletion(
            request, time.monotonic() * 1000., local_monotonic_clock_domain(), prompt, output,
        )

    result = asyncio.run(execute_causal_replay(
        plan, serve, emit, workflow_arrival_offsets_ms=arrival_offsets_ms(arrivals),
    ))
    starts = {event.workflow_id: event for event in observed if event.kind is Kind.WORKFLOW_START}
    ends = {event.workflow_id: event for event in observed if event.kind is Kind.WORKFLOW_END}
    assert starts["b"].ts_ms == starts["w"].ts_ms
    for workflow in starts:
        assert result["workflow_elapsed_ms"][workflow] == pytest.approx(
            ends[workflow].ts_ms - starts[workflow].ts_ms,
        )
    assert result["replay_timing"]["shifts"] == []


@pytest.mark.parametrize("rate", (True, None, 0, -1, float("inf"), float("nan")))
def test_invalid_arrivals_fail_before_delivery(rate):
    with pytest.raises(ValueError):
        poisson_arrival_plan(["w"], rate_per_second=rate, seed=21)


def test_new_arrivals_shift_workflows_without_changing_tools_or_dependencies():
    plan = two_workflows()
    work = dict.fromkeys(plan.request_usage, 5.)
    original = evaluate_causal_plan(plan, work)
    shifted = evaluate_causal_plan(plan, work, workflow_arrival_offsets_ms={"w": 0., "b": 2000.})
    assert shifted["workflow_elapsed_ms"] == original["workflow_elapsed_ms"]
    for node in plan.nodes:
        assert shifted["node_completed_ms"][node.node_id] == pytest.approx(
            original["node_completed_ms"][node.node_id] + (2000. if node.workflow_id == "b" else 0.)
        )


def test_arrivals_deliver_only_on_time_and_jct_retains_driver_release_lag():
    plan = two_workflows()
    events = []

    async def emit(event):
        events.append(event)
        # Simulate a slow control handler as the later root becomes due.
        if event.kind is Kind.WORKFLOW_START and event.workflow_id == "w":
            time.sleep(.015)

    async def serve(request, _submit):
        prompt, output = plan.request_usage[request]
        return NativeReplayCompletion(
            request, time.monotonic() * 1000., local_monotonic_clock_domain(), prompt, output,
        )

    result = asyncio.run(execute_causal_replay(
        plan, serve, emit, workflow_arrival_offsets_ms={"w": 0., "b": 5.},
    ))
    starts = {event.workflow_id: event for event in events if event.kind is Kind.WORKFLOW_START}
    ends = {event.workflow_id: event for event in events if event.kind is Kind.WORKFLOW_END}
    assert starts["b"].ts_ms - starts["w"].ts_ms == pytest.approx(5.)
    assert starts["b"].attributes["arrival_delivery_lag_ms"] >= 5.
    for workflow, start in starts.items():
        assert start.attributes["arrival_delivery_monotonic_ms"] >= start.ts_ms
        assert result["workflow_elapsed_ms"][workflow] == pytest.approx(
            ends[workflow].ts_ms - start.ts_ms,
        )
    assert result["replay_timing"]["shifts"] == []


@pytest.mark.parametrize("offsets", ({}, {"w": 0.}, {"w": True, "b": 0.}, {"w": -1., "b": 0.}))
def test_incomplete_or_invalid_release_maps_cannot_execute(offsets):
    async def unused(*_args):
        pytest.fail("invalid arrivals must not deliver any event or request")

    with pytest.raises(ValueError):
        asyncio.run(execute_causal_replay(
            two_workflows(), unused, unused, workflow_arrival_offsets_ms=offsets,
        ))


def test_arrivals_are_not_shortened_by_external_idle_guards():
    async def unused(*_args):
        pytest.fail("arrival validation must precede online events")

    with pytest.raises(ValueError, match="idle timer compression"):
        asyncio.run(execute_causal_replay(
            two_workflows(), unused, unused, timing_policy=AGENTX_TIMING,
            workflow_arrival_offsets_ms={"w": 0., "b": 10000.},
        ))


def test_comparison_command_and_matcher_bind_identical_external_arrivals(tmp_path):
    arrivals = poisson_arrival_plan(["w"], rate_per_second=.35, seed=21)
    plan = {
        "source_arm": "/source", "port": 18554, "workflow_ids": ["w"],
        "workflow_arrivals": arrivals, "workflow_arrivals_path": "/frozen/arrivals.json",
        "reference_environment": {},
    }
    command = comparison.replay_command(plan, tmp_path, None)
    assert command[command.index("--workflow-arrivals") + 1] == "/frozen/arrivals.json"
    assert match_contract(plan, {})["workflow_arrivals"] == arrivals
    assert match_contract({**plan, "workflow_arrivals": None}, {}) != match_contract(plan, {})


def test_complete_results_cannot_reuse_a_burst_or_different_poisson_control(tmp_path):
    plan, result, arm = result_fixture(tmp_path)
    plan["workflow_arrivals"] = poisson_arrival_plan(["w"], rate_per_second=.35, seed=21)
    with pytest.raises(ValueError, match="same-work"):
        comparison.validate_result(plan, result, arm, live_control=True)
    result["workflow_arrivals"] = plan["workflow_arrivals"]
    result["workflow_arrival_origin_monotonic_ms"] = 1000.
    result["workflow_arrival_clock_domain"] = "fixture"
    when = 1000. + plan["workflow_arrivals"]["arrival_schedule"][0]["offset_seconds"] * 1000.
    event = {"kind": "workflow_start", "workflow_id": "w", "ts_ms": when, "attributes": {
        "scheduled_arrival_monotonic_ms": when, "arrival_delivery_monotonic_ms": when + 5.,
        "arrival_delivery_lag_ms": 5.,
    }}
    events = arm / "replay/runtime_events.replay.jsonl"
    events.write_text(json.dumps(event) + "\n")
    assert comparison.validate_result(plan, result, arm, live_control=True)
    event["ts_ms"] += 5.
    events.write_text(json.dumps(event) + "\n")
    with pytest.raises(ValueError, match="observed workflow arrivals"):
        comparison.validate_result(plan, result, arm, live_control=True)
    result["workflow_arrivals"] = poisson_arrival_plan(["w"], rate_per_second=.5, seed=21)
    with pytest.raises(ValueError, match="same-work"):
        comparison.validate_result(plan, result, arm, live_control=True)


@pytest.mark.parametrize("chunk", ("1024", "2048", "4096"))
@pytest.mark.parametrize("mixed", ("0", "1"))
def test_native_prefill_profile_changes_only_declared_controls(chunk, mixed):
    original = {"chunked_prefill_size": 4096, "enable_mixed_chunk": False,
                "max_running_requests": 24, "model_path": "/model", "hicache_size": 200}
    actual = comparison.prefill_server_parameters(
        original, {"CHUNKED_PREFILL_SIZE": chunk, "ENABLE_MIXED_CHUNK": mixed},
    )
    assert actual == {**original, "chunked_prefill_size": int(chunk), "enable_mixed_chunk": mixed == "1"}
    assert original["chunked_prefill_size"] == 4096


@pytest.mark.parametrize("value", (
    {"CHUNKED_PREFILL_SIZE": "0"}, {"CHUNKED_PREFILL_SIZE": "2048.0"},
    {"ENABLE_MIXED_CHUNK": "true"}, {"ENABLE_MIXED_CHUNK": True},
))
def test_unqualified_prefill_profiles_fail_before_launch(value):
    with pytest.raises(ValueError, match="prefill candidates"):
        comparison.prefill_server_parameters({}, value)
