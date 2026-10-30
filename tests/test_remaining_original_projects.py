import json
import zipfile

import pytest

from scripts.prepare_original_task_mix import source_integrity, validate_population
from scripts.prepare_remaining_original_projects import (
    COMPLETED, CONFIG, HERE, MANIFEST, MAX_CONTEXT_TOKENS, select_missing,
)


def test_old_tokenizer_size_cannot_exclude_a_fitting_complete_original():
    rows = [
        {"project_id": name, "context_tokens": old, "syntax_errors": errors}
        for name, old, errors in (
            ("covered", 100, []), ("current_fits", 250000, []),
            ("too_large", 200000, []), ("syntax_problem", 100, ["unmodified_error"]),
        )
    ]
    looked_up = []

    def count(row):
        looked_up.append(row["project_id"])
        return {"current_fits": 231616, "too_large": 250040}[row["project_id"]]

    selected, evidence = select_missing(rows, {"covered"}, count, maximum=MAX_CONTEXT_TOKENS)
    assert [row["project_id"] for row in selected] == ["current_fits"]
    assert set(looked_up) == {"current_fits", "too_large"}
    assert evidence[-1]["reason"] == "insufficient_protocol_headroom"
    assert rows[1]["context_tokens"] == 250000


@pytest.mark.parametrize("count", (True, -1, 1.5, None))
def test_context_selection_requires_real_current_token_counts(count):
    with pytest.raises(ValueError):
        select_missing(
            [{"project_id": "new", "context_tokens": 1, "syntax_errors": []}],
            set(), lambda row: count, maximum=MAX_CONTEXT_TOKENS,
        )


def test_five_new_projects_preserve_complete_original_tasks_and_project_files():
    manifest = json.loads(MANIFEST.read_bytes())
    declaration = json.loads((HERE / "declaration.json").read_bytes())
    old = json.loads((COMPLETED / "composite_source.json").read_bytes())
    prior = {row["project"] for row in old["selected_workflows"]}
    tasks = manifest["workloads"]
    assert len(tasks) == 5
    assert len(prior | {row["repo"] for row in tasks}) == 53
    assert not prior & {row["repo"] for row in tasks}
    with zipfile.ZipFile(manifest["archive"]) as archive:
        validate_population(tasks, archive)
        for task in tasks:
            assert source_integrity(task, archive)["complete_context_matches"]
    assert declaration["difficulty_counts"] == {"easy": 3, "medium": 1, "expert": 1}
    assert all(row["original_category"] == "code_comprehension" for row in declaration["assignments"])
    assert not declaration["source_tests_changed"]
    assert not declaration["formal_workload_qualified"]
    rejected = [row for row in declaration["current_tokenizer_observations"] if not row["selected"]]
    assert len(rejected) == 1
    assert rejected[0]["project"] == "python_data_streaming_expert_085"
    assert rejected[0]["current_context_tokens"] > MAX_CONTEXT_TOKENS


def test_new_full_contexts_are_measured_with_the_actual_current_model_tokenizer():
    from transformers import AutoTokenizer
    from beliefkv.experiments.long_repository_tasks import parse_repository_context

    manifest = json.loads(MANIFEST.read_bytes())
    tokenizer = AutoTokenizer.from_pretrained(manifest["tokenizer"], local_files_only=True)
    counts = []
    for row in manifest["workloads"]:
        context = parse_repository_context(row["frozen_repository_context"])
        measured = len(tokenizer.encode(context.text, add_special_tokens=False))
        assert measured == context.tokens
        counts.append(measured)
    assert min(counts) == 188616 and max(counts) == 231616
    assert max(counts) <= MAX_CONTEXT_TOKENS


def test_collection_population_does_not_reduce_the_native_decode_safety_limit():
    settings = json.loads(CONFIG.read_bytes())["common_environment"]
    assert settings["ROOT_COUNT"] == settings["CLIENT_ROOT_CONCURRENCY"] == "5"
    assert settings["MAX_RUNNING_REQUESTS"] == "24"
    assert settings["MODEL_CONTEXT_TOKENS"] == settings["CONTEXT_WINDOW_TOKENS"] == "262144"
    assert settings["MAX_COMPLETION_TOKENS"] == "16384"
    assert settings["CONTEXT_COMPRESSION_TRIGGER_RATIO"] == "0.95"
    assert settings["CHILD_WORK_BUDGET_WARNING_CALLS"] == "4"
    assert settings["CHILD_WORK_BUDGET_TARGET_CALLS"] == "8"
    assert not settings["WORKFLOW_ARRIVAL_SCHEDULE"]
    assert settings["TOOL_PREFETCH"] == settings["LAYER_HANDOFF"] == "0"
