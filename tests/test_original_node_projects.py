from copy import deepcopy
import json
import shlex

import pytest

from scripts.prepare_original_node_projects import candidates, node_preflight
from scripts.provision_original_node_projects import packages_for


def inventory():
    return {
        "source_and_tests_modified": False,
        "original_task_requirements_modified": False,
        "selection_uses_native_or_policy_outcomes": False,
        "short_workflows_not_proven_before_natural_capture": True,
        "already_captured_project_families": ["001"],
        "selected_short_context_candidates": [{
            "project": "javascript_example_easy_003",
            "original_difficulty": "easy", "complete_context_tokens": 20000,
            "incidental_python_syntax_diagnostics_not_repaired": [],
        }],
    }


@pytest.mark.parametrize("field", [
    "source_and_tests_modified", "original_task_requirements_modified",
    "selection_uses_native_or_policy_outcomes",
])
def test_original_selection_refuses_mutations_or_policy_winner_input(field):
    value = inventory()
    value[field] = True
    with pytest.raises(ValueError, match="original-task inventory"):
        candidates(value)


def test_selection_excludes_cross_language_variants_of_existing_project_families():
    value = inventory()
    assert len(candidates(value)) == 1
    value["already_captured_project_families"].append("003")
    with pytest.raises(ValueError, match="selection"):
        candidates(value)


def test_original_population_rejects_duplicated_projects():
    value = inventory()
    value["selected_short_context_candidates"].append(
        deepcopy(value["selected_short_context_candidates"][0])
    )
    with pytest.raises(ValueError, match="different"):
        candidates(value)


def test_node_preflight_retains_bounded_engine_ranges_as_data():
    packages = [{"engines": {"node": ">=18.18 <22", "npm": ">=9.6.0"}}]
    command = shlex.split(node_preflight(packages))
    assert command[:2] == ["node", "-e"]
    assert json.dumps([packages[0]["engines"]], separators=(",", ":")) in command[2]
    assert "s.satisfies(versions[key],row[key])" in command[2]


def test_private_environment_selection_preserves_public_dependency_and_source_bytes(tmp_path):
    package = tmp_path / "app" / "package.json"
    package.parent.mkdir()
    raw = b'{"dependencies":{"express":"^4.18.0"},"scripts":{"test":"jest"}}'
    package.write_bytes(raw)
    source = package.parent / "index.js"
    source.write_bytes(b"module.exports = 1;\n")
    directories, reason = packages_for({"source_repo": str(tmp_path)})
    assert reason is None
    assert directories == [package.parent.relative_to(tmp_path)]
    assert package.read_bytes() == raw
    assert source.read_bytes() == b"module.exports = 1;\n"


def test_expensive_native_environment_is_reported_without_rewriting_dependencies(tmp_path):
    package = tmp_path / "package.json"
    raw = b'{"devDependencies":{"electron":"^28.0.0"}}'
    package.write_bytes(raw)
    directories, reason = packages_for({"source_repo": str(tmp_path)})
    assert directories == []
    assert reason == "native_ML_or_GUI_environment_outside_this_bounded_supplement"
    assert package.read_bytes() == raw
