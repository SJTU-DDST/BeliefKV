from copy import deepcopy
import json

import pytest

from scripts.audit_free_capacity_choices import service_progress
from scripts.audit_oracle_admission_entries import compare_periodic_progress, compare_progress, entries
from tests.test_free_choice_progress import progress_evidence
from tests.test_tool_replacement_intervention_audit import append_replacement


def test_replacement_marker_is_a_subset_of_published_grants(monkeypatch, tmp_path):
    _choices, rows, _batches = progress_evidence(monkeypatch, tmp_path)
    append_replacement(rows)
    result = entries(rows, {"w0"})
    grants = result["free_dispatch_grants"]
    assert grants["published_grant_records"] == 2
    assert grants["linked_tool_replacement_grant_records"] == 1
    assert grants["remaining_grant_records"] == 1
    assert grants["linked_marker_is_another_admission"] is False
    assert result["published_extra_grants_by_entry"]["agent_dispatch_free_capacity_admitted"][
        "measured_member_episodes"
    ] == 1
    assert result["free_choice_candidate_metadata"]["with_resume_category_and_queue_sequence"] == 0


def compared_reports(monkeypatch, tmp_path):
    choices, rows, batches = progress_evidence(monkeypatch, tmp_path)
    append_replacement(rows)
    current = {
        "registration": "/same/execution",
        "input_jsonl_prefixes": {"/journal": {"complete_prefix_sha256": "a" * 64}},
        "observations": choices["observations"],
        "native_progress": service_progress(choices, rows, batches),
    }
    previous = deepcopy(current)
    episode = previous["native_progress"]["ready_grant_episodes"][-1]
    episode["new_completion_before_next_control_or_intervention"] = False
    episode["observation_window_end_ms"] -= 500.
    return previous, current


def test_attribution_repair_reports_completion_change_without_inventing_service(monkeypatch, tmp_path):
    result = compare_progress(*compared_reports(monkeypatch, tmp_path))
    assert result["same_original_grants_choices_and_following_exact_service"]
    assert result["bounded_useful_completions_before"] == 1
    assert result["bounded_useful_completions_after"] == 2
    assert result["observation_windows_changed"] == 1


@pytest.mark.parametrize("mutation", ("execution", "prefix", "choice", "grant", "service"))
def test_different_execution_or_service_cannot_be_called_a_reader_repair(
    monkeypatch, tmp_path, mutation,
):
    previous, current = compared_reports(monkeypatch, tmp_path)
    if mutation == "execution":
        current["registration"] = "/different/execution"
    elif mutation == "prefix":
        current["input_jsonl_prefixes"] = {}
    elif mutation == "choice":
        current["observations"] = []
    elif mutation == "grant":
        current["native_progress"]["ready_grant_episodes"].pop()
    else:
        current["native_progress"]["ready_grant_episodes"][0]["following_native_service_ms"] += 1.
    with pytest.raises(ValueError):
        compare_progress(previous, current)


def test_periodic_revalidation_compares_JSON_fields_across_tuple_and_list_storage():
    episode = {"key": ("request", "wf", "agent"), "selection_ms": 100., "following_native_service_ms": 5.}
    current = {"ready_grant_episodes": [episode], "counts": {"completed": 1}}
    original = {
        "periodic_progress_counts": {"completed": 1},
        "decisions": [{"periodic_service": [{"episode": json.loads(json.dumps(episode))}]}],
    }
    result = compare_periodic_progress(original, current)
    assert result["all_original_newly_ranked_episodes_reproduced"]
    assert result["all_periodic_progress_counts_unchanged"]
    current["ready_grant_episodes"][0]["following_native_service_ms"] += 1
    assert not compare_periodic_progress(original, current)["all_original_newly_ranked_episodes_reproduced"]
