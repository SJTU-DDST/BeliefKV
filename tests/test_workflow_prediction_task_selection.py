from copy import deepcopy

import pytest

from scripts.prepare_workflow_prediction_tasks import CATEGORIES, select_tasks


def catalog():
    return [
        {"project": str(index), "original_id": f"{index}-{category}",
         "task_template": f"template-{index}-{category}", "category": category,
         "difficulty": "original"}
        for index in range(20) for category in CATEGORIES
    ]


def test_original_task_selection_excludes_all_held_out_projects_and_used_task_texts():
    values = catalog()
    used = {row["task_template"] for row in values if row["project"] == "2"}
    selected = select_tasks(
        values, training_projects={str(index) for index in range(18)},
        held_out_projects={"18", "19"}, used_task_templates=used, count=15,
    )
    assert len(selected) == len({row["project"] for row in selected}) == 15
    assert not {"2", "18", "19"} & {row["project"] for row in selected}
    assert not used & {row["task_template"] for row in selected}
    assert all(row in values for row in selected)


def test_selection_is_independent_of_candidate_order_and_does_not_rewrite_original_tasks():
    values = catalog()
    original = deepcopy(values)
    options = {
        "training_projects": {str(index) for index in range(20)},
        "held_out_projects": {"evaluation"}, "used_task_templates": set(), "count": 18,
    }
    assert select_tasks(values, **options) == select_tasks(list(reversed(values)), **options)
    assert values == original


def test_selection_fails_instead_of_reusing_a_held_out_or_duplicate_project():
    with pytest.raises(ValueError, match="disjoint"):
        select_tasks(
            catalog(), training_projects={"1"}, held_out_projects={"1"},
            used_task_templates=set(), count=1,
        )
    with pytest.raises(ValueError, match="not enough"):
        select_tasks(
            catalog(), training_projects={"1"}, held_out_projects={"2"},
            used_task_templates=set(), count=2,
        )
