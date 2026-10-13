from collections import Counter

import pytest

from beliefkv.control.causal_graph import CausalGraphError, InvocationState
from beliefkv.core.events import ExecutionMode, RuntimeEventKind
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_sglang_v0520_runtime import event


class CountedInvocations(dict):
    def __init__(self, values):
        super().__init__(values)
        self.full_scans = 0

    def values(self):
        self.full_scans += 1
        return super().values()


@pytest.fixture
def runtime():
    runtime = NativeAdmissionRuntime()
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        *(event(
            index, RuntimeEventKind.INVOCATION_CREATE,
            invocation_id=name, context_id=f"ctx-{name}",
        ) for index, name in enumerate(("parent", "child", "peer"), start=1)),
    ))
    runtime.graph.invocations = CountedInvocations(runtime.graph.invocations)
    assert_census(runtime)
    yield runtime
    runtime.close()


def assert_census(runtime):
    expected = Counter(
        item.active_tool_family for item in dict.values(runtime.graph.invocations)
        if item.state is InvocationState.WAIT_TOOL
    )
    for invocation in dict.values(runtime.graph.invocations):
        features = runtime._local_frontier_features(
            invocation, runtime.graph.contexts[invocation.context_id].epoch,
            now_ms=1000.,
        )
        assert features.active_tool_count == sum(expected.values())
        if invocation.active_tool_family:
            assert features.backend_pressure == (
                f"active_family:{expected[invocation.active_tool_family]}"
            )
    assert runtime._active_tool_census == expected


def test_tool_census_does_not_rescan_history_for_observation_events(runtime):
    before = runtime.graph.invocations.full_scans
    runtime.on_events((event(
        10, RuntimeEventKind.TOOL_WAIT_OBSERVATION, invocation_id="parent",
    ),))
    assert_census(runtime)
    assert runtime.graph.invocations.full_scans == before
    runtime.on_events((event(
        11, RuntimeEventKind.STRUCTURED_ACTION, invocation_id="child",
        context_epoch=0, attributes={"unrelated_observation": True},
    ),))
    assert_census(runtime)
    assert runtime.graph.invocations.full_scans == before


def test_tool_census_tracks_parallel_family_changes_and_batch_final_state(runtime):
    before = runtime.graph.invocations.full_scans
    batches = (
        (event(
            10, RuntimeEventKind.TOOL_START, invocation_id="parent",
            attributes={"tool_run_id": "one", "tool_family": "file"},
        ),),
        (event(
            11, RuntimeEventKind.TOOL_START, invocation_id="parent",
            attributes={"tool_run_id": "two", "tool_family": "shell"},
        ), event(
            12, RuntimeEventKind.TOOL_START, invocation_id="peer",
            attributes={"tool_run_id": "peer", "tool_family": "shell"},
        )),
        (event(
            13, RuntimeEventKind.TOOL_END, invocation_id="parent",
            attributes={"tool_run_id": "two"},
        ),),
        (event(
            14, RuntimeEventKind.TOOL_END, invocation_id="parent",
            attributes={"tool_run_id": "one"},
        ), event(
            15, RuntimeEventKind.TOOL_START, invocation_id="parent",
            attributes={"tool_run_id": "next", "tool_family": "other"},
        )),
        (event(
            16, RuntimeEventKind.TOOL_END, invocation_id="peer",
            attributes={"tool_run_id": "peer"},
        ), event(
            17, RuntimeEventKind.TOOL_END, invocation_id="parent",
            attributes={"tool_run_id": "next"},
        )),
    )
    for batch in batches:
        runtime.on_events(batch)
        assert_census(runtime)
    assert runtime.graph.invocations.full_scans == before


@pytest.mark.parametrize("completion", [
    RuntimeEventKind.RETURN, RuntimeEventKind.INVOCATION_CANCEL,
    RuntimeEventKind.LLM_RESULT,
])
def test_tool_census_restores_waiting_caller_without_an_awakening_delta(runtime, completion):
    runtime.on_events((
        event(
            10, RuntimeEventKind.TOOL_START, invocation_id="parent",
            attributes={"tool_run_id": "parent-tool", "tool_family": "shell"},
        ),
        event(
            11, RuntimeEventKind.CALL, invocation_id="parent",
            target_invocation_id="child", execution_mode=ExecutionMode.FOREGROUND,
        ),
    ))
    assert runtime.graph.invocations["parent"].state is InvocationState.WAIT_CHILD
    assert_census(runtime)
    runtime.on_events((event(
        12, completion, invocation_id="child",
        attributes={"terminal": True} if completion is RuntimeEventKind.LLM_RESULT else {},
    ),))
    assert runtime.graph.invocations["parent"].state is InvocationState.WAIT_TOOL
    assert runtime._active_tool_census == Counter(shell=1)
    assert_census(runtime)


@pytest.mark.parametrize("kind", [
    RuntimeEventKind.LLM_SUBMIT, RuntimeEventKind.REACTIVATE,
    RuntimeEventKind.INVOCATION_CANCEL, RuntimeEventKind.RETURN,
])
def test_tool_census_removes_waits_for_non_tool_state_transitions(runtime, kind):
    runtime.on_events((event(
        10, RuntimeEventKind.TOOL_START, invocation_id="child",
        attributes={"tool_run_id": "tool", "tool_family": "shell"},
    ),))
    assert_census(runtime)
    runtime.on_events((event(11, kind, invocation_id="child"),))
    assert runtime._active_tool_census == Counter()
    assert_census(runtime)


def test_tool_census_workflow_end_and_duplicates_preserve_counts(runtime):
    start = event(
        10, RuntimeEventKind.TOOL_START, invocation_id="child",
        attributes={"tool_run_id": "tool", "tool_family": "shell"},
    )
    runtime.on_events((start, start))
    assert_census(runtime)
    assert runtime._active_tool_census == Counter(shell=1)
    runtime.on_events((event(11, RuntimeEventKind.WORKFLOW_END),))
    assert not runtime._active_tool_census
    assert_census(runtime)


def test_tool_census_rebuilds_after_out_of_band_graph_change(runtime):
    before = runtime.graph.invocations.full_scans
    runtime.graph.apply(event(
        10, RuntimeEventKind.TOOL_START, invocation_id="peer",
        attributes={"tool_run_id": "tool", "tool_family": "shell"},
    ))
    runtime.on_events((event(
        11, RuntimeEventKind.STRUCTURED_ACTION, invocation_id="parent",
    ),))
    assert_census(runtime)
    assert runtime.graph.invocations.full_scans == before + 1


def test_tool_census_discards_partial_batch_state_on_graph_error(runtime):
    runtime.on_events((event(
        10, RuntimeEventKind.TOOL_START, invocation_id="child",
        attributes={"tool_run_id": "tool", "tool_family": "shell"},
    ),))
    assert_census(runtime)
    with pytest.raises(CausalGraphError):
        runtime.on_events((
            event(
                11, RuntimeEventKind.TOOL_START, invocation_id="parent",
                attributes={"tool_run_id": "parent", "tool_family": "file"},
            ),
            event(12, RuntimeEventKind.TOOL_END, invocation_id="missing"),
        ))
    assert not runtime.graph.invocations
    assert not runtime._active_tool_census
    assert not runtime._active_tool_census_invocations
    assert runtime._active_tool_census_version is None
