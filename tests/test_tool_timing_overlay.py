import asyncio
from copy import deepcopy
from dataclasses import replace
import gzip
import json
import time
from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.causal_replay import (
    NativeReplayCompletion, compile_causal_replay, evaluate_causal_plan,
    event_node_id, execute_causal_replay,
)
from beliefkv.experiments.tool_timing_overlay import (
    apply_overlay, build_overlay, fingerprint, load_overlay, plan_fingerprint,
    tracelab_samples,
)
from beliefkv.runtime.clock_evidence import local_monotonic_clock_domain
from scripts import run_causal_comparison as comparison
from scripts import prepare_empirical_tool_timing as preparation
from beliefkv.experiments.source_provenance import sha256_file
from tests.test_causal_comparison import result_fixture
from tests.test_causal_replay import mixed_trace, sequential_trace


def named_plan(trace, *, names=None):
    events = [
        replace(event, attributes={
            **event.attributes,
            "tool_name": (names or {}).get(event.attributes["tool_run_id"], "execute"),
        }) if event.kind in (Kind.TOOL_START, Kind.TOOL_END) else event
        for event in trace.ordered()
    ]
    return compile_causal_replay(events, trace.native)


def document_for(plan, *, durations=(0., 5., 20.)):
    samples = [
        {"sample_id": fingerprint(["fixture", index]), "duration_ms": value}
        for index, value in enumerate(durations)
    ]
    samples.sort(key=lambda item: item["sample_id"])
    return build_overlay(
        plan, samples,
        public_source={"name": "CPU fixture only", "sha256": "a" * 64},
        sample_audit={"sample_sha256": fingerprint(samples)},
        target_tool_names=["execute"],
    )


def public_row(value, *, provider="codex", name="shell_command", call="call"):
    return {
        "provider": provider, "session_id": "public-session",
        "tools": [{
            "tool_name": name, "tool_call_id": call,
            "tool_internal_latency_ms": value, "tool_wall_latency_ms": 900_000,
        }],
    }


def test_public_sampling_preserves_zero_errors_and_tail_without_gap_fallback():
    error = public_row(120_000, call="long")
    error["tools"][0]["is_error"] = True
    zero = public_row(0.)
    samples, audit = tracelab_samples(
        [zero, error, deepcopy(zero), public_row(None, call="unknown"),
         public_row(2., name="write_stdin"), public_row(3., provider="claude")],
        provider="codex", tool_names=["shell_command"],
    )
    assert sorted(sample["duration_ms"] for sample in samples) == [0., 120_000.]
    assert audit["counts"]["duplicate_tool_records"] == 1
    assert audit["counts"]["missing_internal_latency"] == 1
    assert audit["counts"]["observed_error_calls"] == 1
    assert audit["durations"]["over_60s"] == 1
    assert not audit["selection"]["wall_time_fallback"]
    assert not audit["selection"]["tails_clipped"]
    reordered, other = tracelab_samples(
        [error, zero], provider="codex", tool_names=["shell_command"],
    )
    assert reordered == samples
    assert other["sample_sha256"] == audit["sample_sha256"]


@pytest.mark.parametrize("value", (-1., float("nan"), float("inf"), True, "1"))
def test_invalid_public_timing_is_not_imputed(value):
    with pytest.raises(ValueError, match="finite"):
        tracelab_samples(
            [public_row(value)], provider="codex", tool_names=["shell_command"],
        )


def test_missing_internal_times_and_conflicting_duplicates_are_rejected():
    with pytest.raises(ValueError, match="no reported"):
        tracelab_samples(
            [public_row(None)], provider="codex", tool_names=["shell_command"],
        )
    with pytest.raises(ValueError, match="conflicting"):
        tracelab_samples(
            [public_row(1.), public_row(2.)],
            provider="codex", tool_names=["shell_command"],
        )


def test_only_target_duration_changes_without_changing_work_events_or_edges():
    plan = named_plan(mixed_trace(), names={"parallel-two": "read_file"})
    document = document_for(plan)
    changed = apply_overlay(plan, document)
    assert changed.summary() == plan.summary()
    assert changed.request_usage is plan.request_usage
    entries = {row["end_node_id"]: row for row in document["entries"]}
    assert len(entries) == 1
    for before, after in zip(plan.nodes, changed.nodes):
        assert before.event is after.event
        assert before.dependencies == after.dependencies
        assert before.request_id == after.request_id
        assert after.delay_ms == (
            entries[before.node_id]["duration_ms"]
            if before.node_id in entries else before.delay_ms
        )
    assert plan_fingerprint(plan) == document["source_plan_sha256"]


def test_identity_sampling_is_independent_of_plan_iteration_order():
    plan = named_plan(mixed_trace())
    first = document_for(plan)
    reordered = replace(plan, nodes=tuple(reversed(plan.nodes)))
    second = document_for(reordered)
    assert first == second


@pytest.mark.parametrize("change", (
    "source", "seed", "sampling", "work", "future", "hash", "missing", "duplicate",
    "foreign_node", "foreign_identity", "negative", "nan", "bool", "original_time",
))
def test_mismatched_or_incomplete_timing_maps_fail_closed(change):
    plan = named_plan(sequential_trace())
    document = document_for(plan)
    entry = document["entries"][0]
    if change == "source":
        document["source_plan_sha256"] = "b" * 64
    elif change == "seed":
        document["seed"] = True
    elif change == "sampling":
        document["sampling"] = "scheduling_order_random"
    elif change == "work":
        document["request_work_changed"] = True
    elif change == "future":
        document["future_timing_delivered_to_policy"] = True
    elif change == "hash":
        document["entries_sha256"] = "c" * 64
    elif change == "missing":
        document["entries"].clear()
    elif change == "duplicate":
        document["entries"].append(deepcopy(entry))
    elif change == "foreign_node":
        entry["end_node_id"] = "request:r0"
    elif change == "foreign_identity":
        entry["tool_identity"][1] = "other-agent"
    elif change in ("negative", "nan", "bool"):
        entry["duration_ms"] = {"negative": -1, "nan": float("nan"), "bool": True}[change]
    elif change == "original_time":
        entry["original_duration_ms"] += 1
    if change not in ("hash", "nan"):
        document["entries_sha256"] = fingerprint(document["entries"])
    with pytest.raises(ValueError):
        apply_overlay(plan, document)


def test_parallel_tools_keep_independent_clocks_and_join_dependencies():
    trace = mixed_trace()
    plan = named_plan(trace)
    document = document_for(plan, durations=(50.,))
    changed = apply_overlay(plan, document)
    measured = evaluate_causal_plan(changed, dict.fromkeys(plan.request_usage, 0.))
    times = measured["node_completed_ms"]
    for name in ("parallel-one", "parallel-two"):
        begin = event_node_id(trace.named["tool-start-" + name].event_id)
        end = event_node_id(trace.named["tool-end-" + name].event_id)
        assert times[end] - times[begin] == pytest.approx(50.)
        assert changed.dependency_graph()[end] == (begin,)
    parent = event_node_id(trace.named["submit-root-joined"].event_id)
    assert times[parent] >= max(
        times[event_node_id(trace.named["tool-end-" + name].event_id)]
        for name in ("parallel-one", "parallel-two")
    )


def test_online_events_do_not_receive_sampled_future_duration():
    plan = named_plan(sequential_trace(original_service=0.))
    document = document_for(plan, durations=(5.,))
    changed = apply_overlay(plan, document)
    observed = []

    async def request(request_id, _submit):
        prompt, output = changed.request_usage[request_id]
        return NativeReplayCompletion(
            request_id, time.monotonic() * 1000.,
            local_monotonic_clock_domain(), prompt, output,
        )

    async def emit(event):
        observed.append(event)

    result = asyncio.run(execute_causal_replay(changed, request, emit))
    assert result["completed_requests"] == len(plan.request_usage)
    starts = [event for event in observed if event.kind is Kind.TOOL_START]
    ends = [event for event in observed if event.kind is Kind.TOOL_END]
    assert "duration_ms" not in starts[0].attributes
    assert ends[0].attributes["duration_ms"] >= 5.
    for event in observed:
        assert not {
            "sample_id", "sampled_duration_ms", "tool_timing_overlay", "expected_end_ts_ms",
        } & event.attributes.keys()


def test_synthetic_comparison_removes_natural_baseline_and_pins_shared_artifact(tmp_path):
    plan = named_plan(sequential_trace())
    document = document_for(plan)
    path = tmp_path / "timing.json"
    path.write_text(json.dumps(document))
    baseline = {"mean_jct_ms": 1000.}
    natural, evidence, native = comparison.timing_reference(plan, None, baseline)
    assert natural is plan and evidence is None and native is baseline
    changed, evidence, native = comparison.timing_reference(plan, path, baseline)
    assert native is None
    assert evidence == load_overlay(plan, path)[1]
    assert changed.request_usage == plan.request_usage
    command = comparison.replay_command({
        "source_arm": "source", "port": 18650, "tool_timing_overlay": evidence,
    }, tmp_path / "arm", None)
    index = command.index("--tool-duration-overlay")
    assert command[index + 1] == str(path.resolve())
    with pytest.raises(ValueError, match="natural source"):
        comparison.timing_reference(plan, path, baseline, maximum_replay_slowdown=.1)
    document["seed"] = 22
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="seed 21"):
        comparison.timing_reference(plan, path, baseline)


@pytest.mark.parametrize("change", (None, "missing", "changed", "undeclared", "label"))
def test_comparison_rejects_natural_or_differently_sampled_control(tmp_path, change):
    plan, result, arm = result_fixture(tmp_path)
    evidence = {"sha256": "same frozen timing map"}
    plan["tool_timing_overlay"] = evidence
    result.update(tool_timing_overlay=deepcopy(evidence), benchmark_timing_synthetic=True)
    if change == "missing":
        result.pop("tool_timing_overlay")
    elif change == "changed":
        result["tool_timing_overlay"]["sha256"] = "another map"
    elif change == "undeclared":
        plan.pop("tool_timing_overlay")
    elif change == "label":
        result["benchmark_timing_synthetic"] = False
    if change is None:
        comparison.validate_result(plan, result, arm, live_control=True)
    else:
        with pytest.raises(ValueError, match="same-work"):
            comparison.validate_result(plan, result, arm, live_control=True)


def test_cpu_preparation_pins_public_bytes_and_preserves_complete_source_graph(tmp_path, monkeypatch):
    source = tmp_path / "public.jsonl.gz"
    with gzip.open(source, "wt") as stream:
        stream.write(json.dumps(public_row(5.)) + "\n")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "schema_version": 1, "synthetic_tool_timing": True, "seed": 21,
        "public_source": {"name": "TraceLab", "sha256": sha256_file(source)},
        "sample_selection": {
            "provider": "codex", "tool_names": ["shell_command"],
            "latency_field": "tool_internal_latency_ms", "wall_time_fallback": False,
        },
        "target_tool_names": ["execute"],
    }))
    plan = named_plan(mixed_trace())
    monkeypatch.setattr(preparation, "source_plan", lambda _path: (plan, {}, {"fixture": True}))
    output = tmp_path / "timing.json"
    args = NS(input=source, config=config, source_arm=tmp_path, output=output)
    prepared = preparation.prepare(args)
    assert not prepared["gpu_experiment_registered"]
    document = json.loads(output.read_bytes())
    proof = document["cpu_dependency_evidence"]
    assert proof["unchanged_request_population_and_work_totals"]
    assert proof["unchanged_dependency_graph"]
    assert proof["complete_original_and_transformed_nodes"]
    assert len(proof["transformed_zero_service_fixture_workflow_ms"]) == 1
    load_overlay(plan, output)
    with pytest.raises(FileExistsError):
        preparation.prepare(args)
    with gzip.open(source, "wt") as stream:
        stream.write(json.dumps(public_row(6.)) + "\n")
    with pytest.raises(ValueError, match="pinned"):
        preparation.prepare(NS(**{**vars(args), "output": tmp_path / "other.json"}))
