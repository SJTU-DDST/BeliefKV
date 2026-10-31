from copy import deepcopy
import json

import pytest

from scripts.prepare_original_node_projects import candidates
from tests.test_original_node_projects import inventory


@pytest.mark.parametrize("difficulty", ("hard", "expert"))
def test_explicit_difficulty_policy_preserves_the_original_task_without_relabeling(difficulty):
    value = inventory()
    value["selected_short_context_candidates"][0]["original_difficulty"] = difficulty
    original = json.dumps(value, sort_keys=True)
    with pytest.raises(ValueError, match="selection"):
        candidates(value)
    selected = candidates(value, allowed_difficulties=("easy", "medium", "hard", "expert"))
    assert selected[0]["original_difficulty"] == difficulty
    assert json.dumps(value, sort_keys=True) == original


@pytest.mark.parametrize("policy", ((), ("easy", "easy"), ("Easy",), ("unknown",)))
def test_invalid_explicit_difficulty_policies_are_rejected(policy):
    with pytest.raises(ValueError, match="difficulty policy"):
        candidates(inventory(), allowed_difficulties=policy)


@pytest.mark.parametrize("mutation", (
    "task", "source", "outcome", "family", "duplicate", "syntax", "oversized",
))
def test_extended_difficulty_policy_keeps_all_other_original_selection_guards(mutation):
    value = inventory()
    row = value["selected_short_context_candidates"][0]
    row["original_difficulty"] = "hard"
    if mutation == "task":
        value["original_task_requirements_modified"] = True
    elif mutation == "source":
        value["source_and_tests_modified"] = True
    elif mutation == "outcome":
        value["selection_uses_native_or_policy_outcomes"] = True
    elif mutation == "family":
        value["already_captured_project_families"].append("003")
    elif mutation == "duplicate":
        value["selected_short_context_candidates"].append(deepcopy(row))
    elif mutation == "syntax":
        row["incidental_python_syntax_diagnostics_not_repaired"] = ["original syntax issue"]
    else:
        row["complete_context_tokens"] = 96001
    with pytest.raises(ValueError):
        candidates(value, allowed_difficulties=("easy", "medium", "hard", "expert"))
