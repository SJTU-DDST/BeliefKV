from copy import deepcopy
from dataclasses import replace

import pytest

from beliefkv.experiments.composite_workload import merge_plans
from beliefkv.experiments.tool_timing_overlay import fingerprint
from scripts.verify_background_tail_materialization import (
    verify_graph_prefix, verify_overlay_prefix, verify_traffic,
)
from tests.test_composite_workload import trace
from tests.test_prepare_background_tail_extension import fixture
from tests.test_tool_timing_overlay import document_for, named_plan
from scripts.prepare_background_tail_extension import extend_population


def plans():
    base = named_plan(trace("original"))
    extra = named_plan(trace("extra", 10_000_000.))
    extended = merge_plans((base, extra))
    before = {key: {"usage": value} for key, value in base.request_usage.items()}
    after = {key: {"usage": value} for key, value in extended.request_usage.items()}
    return base, extended, before, after


def test_original_graph_prefix_and_native_work_are_compared_exactly():
    base, extended, before, after = plans()
    result = verify_graph_prefix(base, extended, before, after)
    assert result["original_workflows"] == 1 and result["original_requests"] == 2
    assert result["original_causal_nodes"] == len(base.nodes)
    assert result["original_expected_native_work_and_causal_nodes_equal"]


@pytest.mark.parametrize("corrupt", ("delay", "dependency", "event", "usage", "expected", "request"))
def test_graph_prefix_refuses_changes_that_counts_alone_would_miss(corrupt):
    base, extended, before, after = plans()
    if corrupt in ("delay", "dependency", "event", "request"):
        node = extended.nodes[0]
        if corrupt == "delay":
            node = replace(node, delay_ms=node.delay_ms + 1.)
        elif corrupt == "dependency":
            node = replace(node, dependencies=("foreign",))
        elif corrupt == "event":
            node = replace(node, event=replace(node.event, ts_ms=node.event.ts_ms + 1.))
        else:
            index = next(index for index, node in enumerate(extended.nodes) if node.request_id)
            node = replace(extended.nodes[index], request_id="changed")
            extended = replace(extended, nodes=(
                *extended.nodes[:index], node, *extended.nodes[index + 1:],
            ))
        if corrupt != "request":
            extended = replace(extended, nodes=(node, *extended.nodes[1:]))
    elif corrupt == "usage":
        key = next(iter(base.request_usage))
        extended = replace(extended, request_usage={**extended.request_usage, key: (0, 0)})
    else:
        after[next(iter(before))] = {"changed": True}
    with pytest.raises(ValueError, match="changed original"):
        verify_graph_prefix(base, extended, before, after)


def test_background_traffic_retains_original_arrivals_fixed_members_and_complete_JCT():
    base, available, arrivals, cohort = fixture()
    population, extended, updated = extend_population(
        base, available, arrivals, cohort, extra_count=2, minimum_ratio=1.,
    )
    additional = [row["workflow_id"] for row in population["additional_workflows"]]
    result = verify_traffic(arrivals, extended, cohort, updated, additional)
    assert result["measured_workflows"] == 2 and result["background_workflows"] == 4
    assert result["original_arrivals_measured_members_and_common_window_equal"]
    changed = deepcopy(updated)
    changed["measured_workflow_ids"].reverse()
    with pytest.raises(ValueError):
        verify_traffic(arrivals, extended, cohort, changed, additional)
    with pytest.raises(ValueError):
        verify_traffic(arrivals, extended, cohort, updated, list(reversed(additional)))


def test_background_tool_sampling_is_identical_for_every_original_call():
    base, extended, _, _ = plans()
    before, after = document_for(base), document_for(extended)
    result = verify_overlay_prefix(base, extended, before, after, ["extra"])
    assert result == {
        "original_tool_samples": 1, "additional_tool_samples": 1,
        "original_tool_samples_and_public_prior_equal": True,
    }


@pytest.mark.parametrize("corrupt", ("sample", "public_pool", "prior", "seed", "foreign_extra"))
def test_background_tool_verifier_rejects_changed_samples_or_prediction_inputs(corrupt):
    base, extended, _, _ = plans()
    before, after = document_for(base), document_for(extended)
    additional = ["extra"]
    if corrupt == "sample":
        old_identity = before["entries"][0]["tool_identity"]
        next(row for row in after["entries"] if row["tool_identity"] == old_identity)["duration_ms"] += 1
        after["entries_sha256"] = fingerprint(after["entries"])
    elif corrupt == "public_pool":
        after["sample_audit"] = {"changed": True}
    elif corrupt == "prior":
        after["offline_prior"] = {"sha256": "changed"}
    elif corrupt == "seed":
        after["seed"] += 1
    else:
        additional = ["foreign"]
    with pytest.raises(ValueError):
        verify_overlay_prefix(base, extended, before, after, additional)
