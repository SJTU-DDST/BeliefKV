from types import SimpleNamespace as NS

import pytest

from beliefkv.control.causal_graph import ExecutionMode, InvocationState as State
from beliefkv.core.events import RuntimeEvent, RuntimeEventKind as Kind
from scripts.audit_program_scheduling_signals import observed_features, snapshots, summarize


def graph_fixture():
    graph = NS(workflows={"wf": NS(
        invocation_ids={"root", "child", "grandchild", "tool"}, end_ts_ms=None,
    )})
    graph.invocations = {
        "root": NS(
            workflow_id="wf", state=State.WAIT_JOIN,
            execution_mode=ExecutionMode.FOREGROUND, parent_invocation_id=None,
            child_invocation_ids={"child", "tool"}, active_tool_calls={}, join_id="outer",
        ),
        "child": NS(
            workflow_id="wf", state=State.WAIT_JOIN,
            execution_mode=ExecutionMode.FOREGROUND, parent_invocation_id="root",
            child_invocation_ids={"grandchild"}, active_tool_calls={}, join_id="inner",
        ),
        "grandchild": NS(
            workflow_id="wf", state=State.READY,
            execution_mode=ExecutionMode.FOREGROUND, parent_invocation_id="child",
            child_invocation_ids=set(), active_tool_calls={},
        ),
        "tool": NS(
            workflow_id="wf", state=State.WAIT_TOOL,
            execution_mode=ExecutionMode.FOREGROUND, parent_invocation_id="root",
            child_invocation_ids=set(), active_tool_calls={"t": ("execute", 1.)},
        ),
    }
    graph.joins = {
        "outer": NS(
            satisfied=False, member_invocation_ids={"child", "tool"},
            completed_member_ids=set(), waiter_invocation_ids={"root"},
        ),
        "inner": NS(
            satisfied=False, member_invocation_ids={"grandchild"},
            completed_member_ids=set(), waiter_invocation_ids={"child"},
        ),
    }
    return graph


def test_visible_width_and_runnable_width_differ_while_a_sibling_waits_for_tools():
    graph = graph_fixture()
    result = observed_features(graph, "grandchild")
    assert result == {
        "foreground_leaf_count": 2, "runnable_leaf_count": 1,
        "other_tool_blocked_leaves": 1, "sole_observed_blocker_depth": 1,
        "completion_leaf_count": 2, "completion_runnable_leaves": 1,
        "other_completion_tool_blocked_leaves": 1, "completion_scope_unknown_dependencies": 0,
    }
    graph.invocations["tool"].state = State.DONE
    graph.joins["outer"].completed_member_ids.add("tool")
    assert observed_features(graph, "grandchild")["sole_observed_blocker_depth"] == 2


@pytest.mark.parametrize("restriction", ("tools", "timeout", "no_waiter"))
def test_sole_chain_does_not_treat_a_join_record_as_proof_of_current_dependency(restriction):
    graph = graph_fixture()
    if restriction == "tools":
        graph.invocations["child"].active_tool_calls["t"] = ("execute", 1.)
    elif restriction == "timeout":
        graph.invocations["child"].state = State.READY
    else:
        graph.joins["inner"].waiter_invocation_ids.clear()
    assert observed_features(graph, "grandchild")["sole_observed_blocker_depth"] == 0


def trace():
    rows = [
        RuntimeEvent("e1", 1., Kind.WORKFLOW_START, "wf"),
        RuntimeEvent("e2", 2., Kind.INVOCATION_CREATE, "wf", invocation_id="root", context_id="ctx"),
        RuntimeEvent(
            "e3", 3., Kind.LLM_SUBMIT, "wf", invocation_id="root",
            context_id="ctx", context_epoch=0, attributes={"request_id": "r"},
        ),
        RuntimeEvent("e4", 4., Kind.TOOL_START, "wf", invocation_id="root"),
    ]
    return [{**row.to_dict(), "sequence": index} for index, row in enumerate(rows, 1)]


def target():
    return {
        "r": {"workflow_id": "wf", "invocation_id": "root", "context_id": "ctx", "context_epoch": 0}
    }


def test_snapshot_is_not_changed_by_a_later_tool_event():
    events = trace()
    assert snapshots(events, target()) == snapshots(events[:3], target())
    assert snapshots(events, target())["r"]["other_tool_blocked_leaves"] == 0
    assert snapshots(events, target())["r"]["runnable_leaf_count"] == 1


def test_small_clock_regression_preserves_the_original_available_event_sequence():
    events = trace()
    events[1]["ts_ms"] = 3.5
    snapshot = snapshots(events, target())["r"]
    assert snapshot["earlier_events_clock_ahead_ms"] == .5
    assert snapshot["completion_runnable_leaves"] == 1


def test_reordered_events_and_stale_successor_identity_are_rejected():
    events = trace()
    with pytest.raises(ValueError):
        snapshots([events[0], events[1], events[3], events[2]], target())
    wrong = target()
    wrong["r"]["context_epoch"] = 1
    with pytest.raises(ValueError, match="identity"):
        snapshots(events, wrong)


def test_same_episode_duration_comparison_preserves_unserved_and_unknown_population():
    rows = [
        {"status": "first_service_observed", "tool_round_duration_ms": 10., "native_queue_to_service_ms": 1200.},
        {"status": "first_service_observed", "tool_round_duration_ms": 2000., "native_queue_to_service_ms": 100.},
        {"status": "submitted_no_native_service_in_prefix", "tool_round_duration_ms": 1.},
    ]
    result = summarize(rows)
    assert result["episodes"] == 3
    assert result["first_service_observed"] == result["paired_tool_and_queue_durations"] == 2
    assert result["queue_longer_than_preceding_tool"] == 1
    assert result["tool_le_200ms_queue_ge_1000ms"] == 1
    assert result["online_snapshot_unavailable"] == 3
