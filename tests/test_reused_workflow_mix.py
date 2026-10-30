from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.workload_mix import _paths, stratify
from scripts.prepare_reused_workflow_mix import choose_reuse


def base_inventory():
    return {"workflows": [
        {
            "workflow_id": "workflow-" + str(index), "task_id": "task-" + str(index),
            "project": "project-" + str(index), "eligible": True,
            "protocol_sha256": "same", "source_capture": "/original",
            "model_work_units": work,
        }
        for index, work in enumerate((10., 12., 14., 18., 20., 25.))
    ]}


def alternate(base, index, work, *, new_task=True):
    row = deepcopy(base["workflows"][index])
    row.update(model_work_units=work, source_capture="/different-concurrency")
    if new_task:
        row["task_id"] += "-another-original-task"
    return row


def test_reuse_changes_one_original_task_without_choosing_a_lucky_repeat():
    base = base_inventory()
    alternatives = {"workflows": [
        alternate(base, 0, .001, new_task=False),
        alternate(base, 5, 60.), alternate(base, 0, 1.),
    ]}
    before = deepcopy((base, alternatives))
    report = choose_reuse(base, [alternatives], target_ratio=3.)
    assert (base, alternatives) == before
    assert report["model_work_diversity_met"]
    assert report["target_ratio"] == 3.
    assert len(report["selected_workflows"]) == 6
    assert len({row["project"] for row in report["selected_workflows"]}) == 6
    assert len(report["substitutions"]) == 1
    assert report["substitutions"][0]["project"] == "project-0"
    assert report["selected_workflows"][0]["model_work_units"] == 1.
    assert len(report["retained_duplicate_executions"]) == 1


def test_concurrency_does_not_disqualify_an_existing_complete_trace():
    base = base_inventory()
    candidate = alternate(base, 0, 1.)
    candidate["capture_concurrency"] = 6
    base["workflows"][0]["capture_concurrency"] = 24
    report = choose_reuse(base, [{"workflows": [candidate]}], target_ratio=3.)
    assert report["model_work_diversity_met"]
    assert report["selected_workflows"][0]["source_capture"] == "/different-concurrency"


def test_sufficient_existing_population_is_not_replaced_to_exaggerate_the_spread():
    base = base_inventory()
    base["workflows"][0]["model_work_units"] = 1.
    report = choose_reuse(base, [{"workflows": [alternate(base, 0, .001)]}], target_ratio=3.)
    assert report["substitutions"] == []
    assert report["selected_workflows"] == base["workflows"]


def test_source_jct_and_policy_outcomes_do_not_affect_reuse_selection():
    base = base_inventory()
    candidate = alternate(base, 0, 1.)
    previous = choose_reuse(base, [{"workflows": [candidate]}], target_ratio=3.)
    candidate.update(source_jct_s=1e12, predictive_speedup=1000.)
    current = choose_reuse(base, [{"workflows": [candidate]}], target_ratio=3.)
    assert current["substitutions"] == previous["substitutions"]
    assert current["after_model_work"] == previous["after_model_work"]


def test_current_default_threshold_rejects_a_threefold_reuse_mix():
    base = base_inventory()
    candidate = alternate(base, 0, 1.)
    previous = choose_reuse(base, [{"workflows": [candidate]}], target_ratio=3.)
    assert previous["model_work_diversity_met"]
    current = choose_reuse(base, [{"workflows": [candidate]}])
    assert current["target_ratio"] == 5.
    assert not current["model_work_diversity_met"]


def test_tool_aware_tiers_can_place_small_model_work_after_a_long_tool_path():
    rows = []
    for index in range(6):
        rows.append({
            "workflow_id": str(index), "project": str(index), "protocol_sha256": "same",
            "eligible": True, "model_work_units": index + 1.,
            "critical_model_observed_tool_proxy_s": 100. if index == 0 else index + 1.,
        })
    tiers = stratify(rows, each=2, include_tools=True)
    assert tiers[-1]["workflow_id"] == "0"
    assert tiers[-1]["size_tier"] == "long"
    assert all(row["size_ranking_policy"] == "model_and_observed_tool_critical_path" for row in tiers)
    rows[0].pop("critical_model_observed_tool_proxy_s")
    with pytest.raises(ValueError, match="refreshed"):
        stratify(rows, each=2, include_tools=True)


def test_combined_service_path_uses_parallel_max_and_omits_source_runtime_gaps():
    def node(name, dependencies=(), *, request=None, tool=0., delay=0.):
        return NS(
            node_id=name, workflow_id="w", dependencies=dependencies,
            request_id=request,
            event=NS(kind=Kind.TOOL_END) if tool else None,
            delay_ms=(tool + delay) * 1000.,
        )
    nodes = [
        node("runtime", delay=500.),
        node("root", ("runtime",), request="root"),
        node("a-tool", ("root",), tool=100.),
        node("b-model", ("root",), request="b"),
        node("b-tool", ("b-model",), tool=10.),
        node("join", ("a-tool", "b-tool")),
    ]
    plan = NS(nodes=nodes, dependency_graph=lambda: {
        row.node_id: row.dependencies for row in nodes
    })
    paths = _paths(plan, {"root": 10., "b": 50.}, seconds_per_model_unit=1.)["w"]
    assert paths[0] == 60.
    assert paths[1] == 100.
    assert paths[3] == 610.
    assert paths[4] == 110.
