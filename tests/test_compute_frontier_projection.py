from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from scripts.audit_compute_frontier_projection import (
    candidate_windows, changes, dependency_model, propagate,
)
from scripts.audit_dependency_prefill_exposure import critical_requests
from tests.test_resident_decode_prefill_frontiers import member


def model(*, sibling_return=8.):
    rows, events, requests = [], [], {}

    def event(key, workflow, kind, when, deps=()):
        rows.append(NS(
            node_id=key, workflow_id=workflow, dependencies=deps, request_id=None,
            event=NS(event_id=key, kind=kind),
        ))
        events.append({"event_id": key, "workflow_id": workflow, "kind": kind.value, "ts_ms": when})

    def request(key, workflow, when, deps):
        rows.append(NS(
            node_id=key, workflow_id=workflow, dependencies=deps, request_id=key, event=None,
        ))
        requests[key] = {"native_complete_monotonic_ms": when}

    event("arrival-a", "a", Kind.WORKFLOW_START, 0.)
    event("arrival-b", "b", Kind.WORKFLOW_START, 0.)
    request("decode", "a", 4., ("arrival-a",))
    event("tool-start", "a", Kind.TOOL_START, 4., ("decode",))
    event("tool-end", "a", Kind.TOOL_END, 8., ("tool-start",))
    event("sibling-return", "a", Kind.RETURN, sibling_return, ("arrival-a",))
    event("join", "a", Kind.JOIN_SATISFIED, max(8., sibling_return), ("tool-end", "sibling-return"))
    event("end-a", "a", Kind.WORKFLOW_END, max(8., sibling_return), ("join",))
    request("prefill", "b", 3., ("arrival-b",))
    request("prefill-successor", "b", 6., ("prefill",))
    event("end-b", "b", Kind.WORKFLOW_END, 6., ("prefill-successor",))
    return dependency_model(NS(nodes=rows), events, requests)


def test_baseline_exactly_reproduces_all_observed_nodes_and_arrivals():
    graph = model()
    assert propagate(graph) == graph["reference"]


def test_early_noncritical_branch_does_not_advance_join_but_pays_prefill_delay():
    graph = model(sibling_return=10.)
    projected = propagate(graph, advance={"decode": 2.}, delay={"prefill": 1.})
    assert changes(graph, projected, ("a", "b")) == {"a": 0., "b": 1.}
    assert critical_requests(graph, "a") == set()
    assert critical_requests(graph, "b") == {"prefill", "prefill-successor"}


def test_tool_duration_is_preserved_and_join_can_switch_its_last_predecessor():
    graph = model(sibling_return=7.)
    projected = propagate(graph, advance={"decode": 2.}, delay={"prefill": .5})
    assert changes(graph, projected, ("a", "b")) == {"a": -1., "b": .5}
    assert projected[graph["ends"]["a"]] == 7.


def test_multiple_delayed_requests_on_one_branch_do_not_pay_the_same_shift_twice():
    graph = model()
    projected = propagate(
        graph, delay={"prefill": 1., "prefill-successor": 1.},
    )
    assert changes(graph, projected, ("a", "b")) == {"a": 0., "b": 1.}


@pytest.mark.parametrize("advance,delay", (
    ({"decode": 4.1}, {}), ({"decode": -1.}, {}), ({}, {"prefill": -1.}),
    ({"missing": 1.}, {}), ({"decode": 1.}, {"decode": 1.}),
))
def test_invalid_or_conflicting_interventions_are_rejected(advance, delay):
    with pytest.raises(ValueError):
        propagate(model(), advance=advance, delay=delay)


@pytest.mark.parametrize("closed_victim", (False, True))
def test_new_decode_members_need_prefill_and_censored_victims_are_not_ignored(closed_victim):
    before = {"phase": "decode", "service_elapsed_ms": 1., "request_samples": [member()]}
    victim = member(request_id="prefilling", workflow_id="victim", phase="prefill")
    prefill = {
        "phase": "prefill", "sample_id": "p", "launch_monotonic_ms": 100.,
        "complete_monotonic_ms": 110., "request_samples": [victim],
    }
    after = {
        "phase": "decode", "service_elapsed_ms": 1.,
        "request_samples": [member(output_tokens_before=10), {**victim, "phase": "decode"}],
    }
    requests = {
        "request": {"output_tokens": 11, "native_receipt": {"output_token_ids": [0] * 9 + [2, 0]}},
    }
    candidates, counts = candidate_windows(
        (before, prefill, after), requests,
        {"workflow", "victim"} if closed_victim else {"workflow"},
        {"<tool_call>": 1, "</tool_call>": 2}, {}, {}, budget_ms=500.,
    )
    assert counts["whole_following_decode_can_move_without_new_members"] == 0
    assert len(candidates) == int(closed_victim)
    assert counts["opportunities_with_censored_direct_participants"] == int(not closed_victim)
    if closed_victim:
        assert candidates[0]["victim_requests"] == ["prefilling"]
        assert candidates[0]["tool_close_already_visible"]
