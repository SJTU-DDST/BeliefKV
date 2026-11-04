from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from beliefkv.control.causal_graph import RuntimeCausalContextGraph
from beliefkv.core.events import RuntimeEventKind as Kind
from scripts.audit_reference_sibling_order import memberships, replay
from tests.test_internal_comparison import observed
from tests.test_reference_sibling_coadmission import group


def example():
    graph = RuntimeCausalContextGraph(strict_timestamps=False)
    group(NS(on_events=lambda events: graph.apply_batch(events)), waiting=True)
    metadata = [
        {"key": {
            "invocation_id": owner, "root_workflow_id": workflow,
            "context_id": owner + "-context", "context_epoch": 0,
        }}
        for owner, workflow in (("child-0", "wf"), ("peer", "other"), ("child-1", "wf"))
    ]
    return graph, {
        "monotonic_s": 10., "capacity_tokens": 207,
        "ordered": [
            ["child-0", 0, 1, 2, 1, None],
            ["peer", 0, 1, 3, 2, None],
            ["child-1", 0, 1, 5, 3, None],
        ],
        "selected_in_priority_order": ["child-0", "peer"], "selected_tokens": 205,
        "logical_ready_paused_before_resume": {
            "child-0": True, "peer": True, "child-1": True,
        },
        "candidates": metadata,
    }


def test_real_core_replays_reference_and_changes_only_sibling_adjacency():
    graph, row = example()
    before = deepcopy(row)
    groups = memberships(graph, row)
    assert groups == {"child-0": "join", "child-1": "join"}
    assert replay(row, {})["selected"] == ["child-0", "peer"]
    assert replay(row, groups)["selected"] == ["child-0", "child-1"]
    assert replay(row, groups)["selected_tokens"] == 207
    assert row == before


@pytest.mark.parametrize("case", ("not_ready", "unknown_ready", "tool", "satisfied", "not_waiting"))
def test_unknown_or_blocked_members_do_not_supply_a_ready_sibling_pair(case):
    graph, row = example()
    if case == "not_ready":
        row["logical_ready_paused_before_resume"]["child-1"] = False
    elif case == "unknown_ready":
        row.pop("logical_ready_paused_before_resume")
    elif case == "tool":
        graph.apply(observed(
            20, Kind.TOOL_START, invocation_id="child-1",
            attributes={"tool_run_id": "t"},
        ))
    elif case == "satisfied":
        graph.apply(observed(20, Kind.JOIN_SATISFIED, join_id="join"))
    else:
        graph.invocations["parent"].join_id = "another-join"
    groups = memberships(graph, row)
    assert len(groups) < 2
    assert replay(row, groups)["selected"] == row["selected_in_priority_order"]
