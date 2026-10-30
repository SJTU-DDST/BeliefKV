import random
import time
from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.runtime.workflow_work_oracle import (
    WorkflowWorkOracle, cache_remaining_paths, scan_remaining_paths,
)
from tests.test_workflow_work_oracle import add_serial_graphs, node, oracle_file, source


def expected(oracle, workflow, now):
    return scan_remaining_paths(
        oracle.artifact.graphs[workflow], oracle.artifact.requests, oracle._done_at,
        oracle._active, oracle.artifact.model_seconds_per_work_token, now,
    )


def assert_paths(prediction, values):
    assert (
        prediction.remaining_path_s, prediction.model_only_path_s,
        prediction.tool_and_fixed_delay_path_s,
    ) == pytest.approx(values, abs=1e-7)


def parallel():
    raw = source()
    raw["workflow_graphs"]["wf"] = [
        node("start-a", event_id="start-a", kind=Kind.TOOL_START.value),
        node("end-a", (0,), event_id="end-a", kind=Kind.TOOL_END.value, delay=30.),
        node("start-b", event_id="start-b", kind=Kind.TOOL_START.value),
        node("end-b", (2,), event_id="end-b", kind=Kind.TOOL_END.value, delay=20.),
        node("request:one", (1, 3), request_id="one"),
        node("request:two", (4,), request_id="two"),
        node("end", (5,), event_id="end:wf", kind=Kind.WORKFLOW_END.value),
    ]
    return raw


def test_continuous_tool_elapsed_and_critical_branch_switch_without_rescanning(monkeypatch, tmp_path):
    oracle = WorkflowWorkOracle(*oracle_file(tmp_path, parallel()))
    now = [100.]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    for name, started in (("a", 90.), ("b", 105.)):
        oracle.submit("wf", "event", NS(
            kind=Kind.TOOL_START, workflow_id="wf", event_id="start-" + name, ts_ms=started * 1000.,
        ))
    for value in (100., 104., 105., 110., 119., 121., 124., 125., 140.):
        now[0] = value
        assert_paths(oracle.prediction("wf"), expected(oracle, "wf", value))
    counts = oracle.status()["counts"]
    assert counts["path_evaluations"] == 1
    assert counts["path_queries"] == 9
    assert counts["path_future_clock_fallbacks"] == 2


def test_only_changed_workflow_invalidates_and_duplicate_event_does_not(monkeypatch, tmp_path):
    raw = source()
    raw["workflows"]["other"] = {"total_work_units": 110, "requests": 1}
    raw["requests"]["three"] = {
        "workflow_id": "other", "invocation_id": "other-child", "context_id": "other-ctx",
        "prompt_tokens": 10, "output_tokens": 1, "input_increment": 10,
        "predecessor_request_id": None,
    }
    add_serial_graphs(raw)
    oracle = WorkflowWorkOracle(*oracle_file(tmp_path, raw))
    monkeypatch.setattr(time, "monotonic", lambda: 100.)
    oracle.prediction("wf")
    oracle.prediction("other")
    oracle.submit("wf", "started", "one", "child", "ctx", range(500))
    oracle.prediction("wf")
    assert oracle.counts["path_evaluations"] == 2
    oracle.submit("wf", "generated", "one", 10)
    assert_paths(oracle.prediction("wf"), expected(oracle, "wf", 100.))
    oracle.prediction("other")
    assert oracle.counts["path_evaluations"] == 3
    event = NS(kind=Kind.TOOL_START, workflow_id="wf", event_id="unused", ts_ms=90000.)
    oracle.submit("wf", "event", event)
    oracle.prediction("wf")
    assert oracle.counts["path_evaluations"] == 3
    oracle.submit("wf", "completed", "one", "child", "ctx", range(500), range(30))
    oracle.prediction("wf")
    assert oracle.counts["path_evaluations"] == 4
    oracle.submit("wf", "completed", "one", "child", "ctx", range(500), range(30))
    oracle.prediction("wf")
    assert oracle.counts["path_evaluations"] == 4


def test_known_event_cannot_invalidate_or_advance_another_workflow(tmp_path):
    raw = source()
    raw["workflows"]["other"] = {"total_work_units": 110, "requests": 1}
    raw["requests"]["three"] = {
        "workflow_id": "other", "invocation_id": "other-child", "context_id": "other-ctx",
        "prompt_tokens": 10, "output_tokens": 1, "input_increment": 10,
        "predecessor_request_id": None,
    }
    add_serial_graphs(raw)
    oracle = WorkflowWorkOracle(*oracle_file(tmp_path, raw))
    with pytest.raises(ValueError, match="another workflow"):
        # This real event belongs to wf, while the bound observation claims other.
        oracle.submit("other", "event", NS(
            kind=Kind.WORKFLOW_END, workflow_id="other", event_id="end:wf", ts_ms=1000.,
        ))


def test_random_parallel_and_serial_dags_match_literal_scan_across_time_and_partial_progress():
    rng = random.Random(21)
    for _case in range(160):
        graph, requests, done, active = [], {}, {}, {}
        for index in range(40):
            dependencies = tuple(sorted(rng.sample(range(index), min(index, rng.randrange(4)))))
            if index % 4 == 0:
                request = str(index)
                requests[request] = {
                    "input_increment": rng.randrange(1000), "output_tokens": rng.randrange(1, 500),
                }
                graph.append(node("r:" + request, dependencies, request_id=request))
                active[request] = rng.randrange(requests[request]["output_tokens"])
            else:
                graph.append(node(
                    "e:" + str(index), dependencies, event_id=str(index),
                    kind=Kind.TOOL_END.value, delay=rng.random() * 60.,
                ))
            if rng.random() < .4:
                done[graph[-1]["node_id"]] = 7_000_000. + rng.random() * 100.
        cached = cache_remaining_paths(graph, requests, done, active, .02386040346836485)
        for now in (7_000_100., 7_000_101., 7_000_115., 7_000_160., 7_000_300.):
            assert cached.at(now) == pytest.approx(
                scan_remaining_paths(graph, requests, done, active, .02386040346836485, now),
                abs=1e-7,
            )
