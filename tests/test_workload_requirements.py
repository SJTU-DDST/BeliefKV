from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from beliefkv.experiments.workload_mix import agent_work_breakdown
from beliefkv.experiments.workload_requirements import DEFAULT_DIAGNOSTICS, assess


def population():
    workflows, children, joins = [], [], []
    for index, scale in enumerate((10, 20, 40, 80, 160, 320)):
        identity = "workflow-" + str(index)
        agents = [{
            "invocation_id": identity, "kind": "root", "model_calls": 4,
            "model_work_units": 100 * scale, "generated_tokens": 100 * scale,
        }]
        for member in range(4):
            child = identity + "-child-" + str(member)
            agents.append({
                "invocation_id": child, "kind": "child", "model_calls": 6,
                "generated_tokens": 10 * scale, "model_work_units": 10 * scale,
            })
            children.append({
                "workflow_id": identity, "invocation_id": child, "model_calls": 6,
                "output_tokens": 10 * scale,
            })
        joins.extend({"workflow_id": identity, "members": 2} for _ in range(2))
        workflows.append({
            "workflow_id": identity, "project": "project-" + str(index),
            "protocol_sha256": "one-protocol", "model_work_units": 140 * scale,
            "generated_tokens": 140 * scale, "initial_prompt_tokens": 100000 - index * 10000,
            "eligible": True, "joins": 2, "completed_parallel_root_joins": 2,
            "agent_work_breakdown": {
                "agents": agents, "child_model_work_fraction": 40 / 140,
                "largest_child_model_work_fraction": 10 / 140,
            },
        })
    return (
        {"capture": "/same-source", "workflows": workflows},
        {"source": "/same-source", "children": children, "joins": joins},
    )


def test_good_shape_is_not_a_claim_of_pressure_or_formal_experiment_readiness():
    inventory, shape = population()
    report = assess(inventory, shape)
    assert report["shape_status"] == "source_shape_candidate"
    assert report["shape_shortfalls"] == []
    assert report["summary"]["root_calls"]["p50"] == 4
    assert report["formal_comparison_ready"] is False
    assert report["serving_evidence_status"].startswith("pending")
    assert report["scope"]["native_recomputation_or_predictive_win_used_to_select"] is False


def test_source_size_uses_intrinsic_work_not_jct_or_policy_outcomes():
    inventory, shape = population()
    previous = assess(inventory, shape)
    for index, row in enumerate(inventory["workflows"]):
        row["source_jct_s"] = 1e12 / (index + 1)
        row["native_recomputation"] = 1e9
        row["predictive_speedup"] = 2.
    assert assess(inventory, shape) == previous


@pytest.mark.parametrize("ratio,accepted", ((5., True), (4.999, False)))
def test_current_work_diversity_threshold_is_five_without_rounding(ratio, accepted):
    # Preserve the explicit historical fivefold boundary independently of the new default.
    inventory, shape = population()
    for index, row in enumerate(inventory["workflows"]):
        work = 1000. if index < 3 else 1000. * ratio
        factor = work / row["model_work_units"]
        row["model_work_units"] = work
        for agent in row["agent_work_breakdown"]["agents"]:
            agent["model_work_units"] *= factor
    report = assess(inventory, shape, diagnostics={
        **DEFAULT_DIAGNOSTICS, "model_work_p80_p20": 5.,
    })
    assert report["diagnostic_targets"]["model_work_p80_p20"] == 5.
    assert ("insufficient_intrinsic_work_diversity" not in report["shape_shortfalls"]) is accepted


@pytest.mark.parametrize("ratio,accepted", ((3., True), (2.999, False)))
def test_default_p80_p20_boundary_is_three_without_rounding(ratio, accepted):
    inventory, shape = population()
    for index, row in enumerate(inventory["workflows"]):
        work = 1000. if index < 3 else 1000. * ratio
        factor = work / row["model_work_units"]
        row["model_work_units"] = work
        for agent in row["agent_work_breakdown"]["agents"]:
            agent["model_work_units"] *= factor
    report = assess(inventory, shape)
    assert report["diagnostic_targets"]["model_work_p80_p20"] == 3.
    assert report["model_work_diversity"]["metric"] == "p80_p20"
    assert report["model_work_diversity"]["p90_p10_is_reference_only"]
    assert ("insufficient_intrinsic_work_diversity" not in report["shape_shortfalls"]) is accepted


def test_long_single_child_sources_are_retained_with_diagnostic_shortfalls():
    inventory, shape = population()
    for row in inventory["workflows"]:
        row["completed_parallel_root_joins"] = 1
        for agent in row["agent_work_breakdown"]["agents"]:
            if agent["kind"] == "child":
                agent["model_calls"] = 32
    for row in shape["children"]:
        row["model_calls"] = 32
    for row in shape["joins"]:
        row["members"] = 1
    before = deepcopy((inventory, shape))
    report = assess(inventory, shape)
    assert set(report["shape_shortfalls"]) == {
        "mostly_single_child_joins", "child_calls_median_too_large",
        "child_calls_p90_too_large", "insufficient_multi_round_parallel_joins",
    }
    assert all(row["source_eligible"] for row in report["workflows"])
    assert (inventory, shape) == before


@pytest.mark.parametrize("bad", ("project", "protocol", "missing_breakdown", "nonconserved_work"))
def test_identity_and_work_attribution_are_checked_before_screening(bad):
    inventory, shape = population()
    if bad == "project":
        inventory["workflows"][1]["project"] = inventory["workflows"][0]["project"]
    elif bad == "protocol":
        inventory["workflows"][0]["protocol_sha256"] = "changed"
    elif bad == "missing_breakdown":
        inventory["workflows"][0].pop("agent_work_breakdown")
    else:
        inventory["workflows"][0]["model_work_units"] += 1
    with pytest.raises(ValueError):
        assess(inventory, shape)


def test_intrinsic_attribution_counts_new_input_and_reports_internal_work_separately():
    creates = {
        name: NS(attributes={"runtime_internal": name == "internal"}, agent_definition_id=name)
        for name in ("root", "child", "internal")
    }
    calls = [
        {
            "invocation_id": name, "output_tokens": output,
            "necessary_input_tokens": new_input, "prompt_tokens": 1000000,
        }
        for name, output, new_input in (
            ("root", 100, 1000), ("child", 10, 100), ("child", 20, 0), ("internal", 50, 200),
        )
    ]
    value = agent_work_breakdown(calls, "root", creates)
    assert value["root_model_work_units"] == 110.
    assert value["child_model_work_units"] == 31.
    assert value["runtime_internal_model_work_units"] == 52.
    assert value["child_model_work_fraction"] == pytest.approx(31 / 141)
    assert value["largest_child_model_work_fraction"] == pytest.approx(31 / 141)
    assert sum(row["model_calls"] for row in value["agents"]) == 4
    assert sum(row["model_work_units"] for row in value["agents"]) == 193.
