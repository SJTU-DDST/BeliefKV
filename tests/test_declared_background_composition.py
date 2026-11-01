from copy import deepcopy
import json

import pytest

from beliefkv.experiments.composite_workload import load_composed_source
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workload_mix import summary
from scripts import prepare_composite_workload as prep
from scripts.run_causal_gpu_replay import source_plan
from tests.test_composite_workload import make_capture


def declaration(rows):
    selected = deepcopy(rows[:3])
    return {
        "kind": "original_background_extension_plan_not_executable_source",
        "selected_workflows": selected,
        "additional_workflows": deepcopy(selected[-1:]),
        "projects": len(selected),
        "model_work_units": summary([row["model_work_units"] for row in selected]),
        "minimum_model_work_p80_p20": 1.,
    }


def rows():
    return [
        {
            "workflow_id": f"wf-{index}", "project": f"locobench/project-{index}",
            "model_work_units": float(index + 1), "eligible": True,
            "event_sha256": f"events-{index}", "token_source_sha256": f"tokens-{index}",
        }
        for index in range(4)
    ]


def test_predeclared_population_retains_exact_rows_order_and_all_excluded_originals():
    available = rows()
    plan = declaration(available)
    plan["selected_workflows"].reverse()
    before = deepcopy((available, plan))
    selected, excluded = prep.select_declared_projects(available, plan)
    assert (available, plan) == before
    assert selected == plan["selected_workflows"]
    assert excluded == [{
        **available[-1], "selection_reason": "outside_predeclared_whole_original_population",
    }]
    selected[0]["project"] = "changed"
    assert (available, plan) == before


@pytest.mark.parametrize("corrupt", (
    "kind", "duplicate_workflow", "foreign_workflow", "changed_project", "changed_tokens",
    "changed_work", "missing_row", "no_additions", "all_additions", "foreign_addition",
    "repeated_addition", "size", "summary", "spread", "nan", "duplicate_inventory",
))
def test_changed_or_inconsistent_declared_original_population_is_rejected(corrupt):
    available = rows()
    plan = declaration(available)
    if corrupt == "kind":
        plan["kind"] = "arbitrary"
    elif corrupt == "duplicate_workflow":
        plan["selected_workflows"][1] = deepcopy(plan["selected_workflows"][0])
    elif corrupt == "foreign_workflow":
        plan["selected_workflows"][0]["workflow_id"] = "foreign"
    elif corrupt == "changed_project":
        plan["selected_workflows"][0]["project"] = available[1]["project"]
    elif corrupt == "changed_tokens":
        plan["selected_workflows"][0]["token_source_sha256"] = "changed"
    elif corrupt == "changed_work":
        plan["selected_workflows"][0]["model_work_units"] += 1
    elif corrupt == "missing_row":
        plan["selected_workflows"][0] = None
    elif corrupt == "no_additions":
        plan["additional_workflows"] = []
    elif corrupt == "all_additions":
        plan["additional_workflows"] = deepcopy(plan["selected_workflows"])
    elif corrupt == "foreign_addition":
        plan["additional_workflows"] = [available[-1]]
    elif corrupt == "repeated_addition":
        plan["additional_workflows"] *= 2
    elif corrupt == "size":
        plan["projects"] += 1
    elif corrupt == "summary":
        plan["model_work_units"]["max"] += 1
    elif corrupt == "spread":
        plan["minimum_model_work_p80_p20"] = 100.
    elif corrupt == "nan":
        plan["minimum_model_work_p80_p20"] = float("nan")
    else:
        available.append(deepcopy(available[0]))
    with pytest.raises(ValueError):
        prep.select_declared_projects(available, plan)


def test_declared_background_materialization_reloads_exact_original_tokens_and_dependencies(
    tmp_path, monkeypatch,
):
    inventories = [
        make_capture(tmp_path, name, index * 10_000_000.)
        for index, name in enumerate(("warm", "measured", "extra", "unused"))
    ]
    available = [row for inventory in inventories for row in inventory["workflows"]]
    plan = declaration(available)
    path = tmp_path / "population_plan.json"
    path.write_text(json.dumps(plan))
    monkeypatch.setattr(prep, "original_source_proof", lambda inventory: {"fixture": inventory["capture"]})
    monkeypatch.setattr(prep, "serving_artifacts", lambda *_args: ({}, {}))
    output = tmp_path / "composed"
    result = prep.prepare(inventories, output, population_plan=path)
    assert result["projects"] == 3 and result["requests"] == 6
    graph, expected, _ = source_plan(output)
    assert set(graph.request_usage) == {
        f"{name}-r{index}" for name in ("warm", "measured", "extra") for index in range(2)
    }
    assert set(expected) == set(graph.request_usage)
    assert (output / "server/request_tokens.jsonl").read_bytes() == b"".join(
        (tmp_path / name / "server/request_tokens.jsonl").read_bytes()
        for name in sorted(("warm", "measured", "extra"))
    )
    combined = json.loads((output / "composite_source.json").read_bytes())
    assert combined["selected_workflows"] == plan["selected_workflows"]
    assert combined["candidate_projects"] == 4
    assert combined["original_inputs_sha256"][str(path)] == sha256_file(path)
    assert combined["declared_population_plan"]["additional_workflows"] == ["extra"]
    path.write_text(json.dumps({**plan, "projects": 4}))
    with pytest.raises(ValueError, match="original.*changed"):
        load_composed_source(output)


def test_declared_population_cannot_silently_reselect_by_a_new_ratio(tmp_path, monkeypatch):
    inventory = make_capture(tmp_path, "workflow")
    path = tmp_path / "population.json"
    path.write_text("{}")
    with pytest.raises(ValueError, match="must not be selected again"):
        prep.prepare(
            [inventory], tmp_path / "composed",
            population_plan=path, minimum_work_ratio=3.,
        )
