from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from beliefkv.runtime.tool_boundary_preemption import ToolBoundaryPreemption
from beliefkv.runtime.workflow_combination import WorkflowCombinationController
from beliefkv.runtime.workflow_work_oracle import WorkflowWorkOracle
from scripts.prepare_tool_boundary_preemption import DEFAULT_POLICY, configuration, prepare_artifact
from tests.test_model_work_combination_oracle import continuous_artifact
from tests.test_workflow_work_oracle import oracle_file


def original(tmp_path):
    raw = continuous_artifact(tmp_path)
    raw.update(
        shortest_work_first_admission=True, cost_aware_maximal_admission=True,
        maximal_admission_objective="benefit",
    )
    return raw


def test_preparation_changes_only_the_explicit_action_policy(tmp_path):
    raw = original(tmp_path)
    before = deepcopy(raw)
    prepared = prepare_artifact(raw)
    assert raw == before
    assert prepared.pop("tool_boundary_preemption") == DEFAULT_POLICY
    assert prepared == before


@pytest.mark.parametrize("change", (
    {"shortest_work_first_admission": False}, {"cost_aware_maximal_admission": False},
    {"maximal_admission_objective": "benefit_per_cost"},
    {"tool_boundary_preemption": DEFAULT_POLICY},
))
def test_preparation_refuses_an_unmatched_or_already_modified_source(tmp_path, change):
    raw = original(tmp_path)
    raw.update(change)
    with pytest.raises(ValueError):
        prepare_artifact(raw)


def test_runtime_constructs_the_explicit_Oracle_with_the_same_costs_and_window(tmp_path):
    raw = prepare_artifact(original(tmp_path))
    path, digest = oracle_file(tmp_path, raw)
    work = WorkflowWorkOracle(path, digest)
    admission = NS(
        _work=work,
        runtime=NS(workflow_work_model=path, workflow_work_model_sha256=digest),
    )
    combination = WorkflowCombinationController(admission)
    assert combination.window_ms == 2000
    assert combination.maximal_objective == "benefit"
    assert admission._tool_boundary_preemption.status()["enabled"]
    assert admission._tool_boundary_preemption.seconds_per_work_token == raw["model_seconds_per_work_token"]


@pytest.mark.parametrize("change", (
    {"kind": "ordinary-prefetch"}, {"margin_ms": False}, {"margin_ms": -1.},
    {"cooldown_ms": 0.}, {"ready_wait_protection_ms": float("nan")},
    {"unexpected": True},
))
def test_runtime_rejects_ambiguous_or_invalid_policy_declarations(tmp_path, change):
    raw = prepare_artifact(original(tmp_path))
    raw["tool_boundary_preemption"].update(change)
    path, digest = oracle_file(tmp_path, raw)
    with pytest.raises(ValueError):
        ToolBoundaryPreemption(NS(_work=WorkflowWorkOracle(path, digest)), raw)


def test_learned_predictor_cannot_silently_enable_an_Oracle_only_action(tmp_path):
    raw = prepare_artifact(original(tmp_path))
    with pytest.raises(ValueError):
        ToolBoundaryPreemption(NS(_work=NS(model_work_only=True)), raw)


def test_configuration_preserves_all_traffic_resources_and_other_controls(tmp_path):
    env = {
        "WORKFLOW_WORK_SOURCE": "oracle", "AGENT_RESIDENCY_PAUSE": "1",
        "TOOL_OFFLOAD_BEFORE_ADMISSION": "1", "TOOL_PREFETCH": "0", "LAYER_HANDOFF": "0",
        "WORKFLOW_WORK_MODEL": "old", "BELIEFKV_WORKFLOW_WORK_MODEL": "old",
        "WORKFLOW_WORK_MODEL_SHA256": "a" * 64, "BELIEFKV_WORKFLOW_WORK_MODEL_SHA256": "a" * 64,
        "HICACHE_SIZE_GB": "400", "ROOT_COUNT": "133", "AGENT_CONTROL_INTERVAL_MS": "2000",
    }
    source = {
        "common_environment": env, "comparison_contract": {"keep_JOIN": True},
        "workflow_arrivals": {"path": "arrivals", "sha256": "b" * 64},
        "private_analysis_cohort": {"path": "cohort", "sha256": "c" * 64},
    }
    before = deepcopy(source)
    path = tmp_path / "bound_model.json"
    prepared = configuration(source, path, "d" * 64)
    assert source == before
    changed = {key for key in env if env[key] != prepared["common_environment"][key]}
    assert changed == {
        "WORKFLOW_WORK_MODEL", "BELIEFKV_WORKFLOW_WORK_MODEL",
        "WORKFLOW_WORK_MODEL_SHA256", "BELIEFKV_WORKFLOW_WORK_MODEL_SHA256",
    }
    assert prepared["workflow_arrivals"] == source["workflow_arrivals"]
    assert prepared["private_analysis_cohort"] == source["private_analysis_cohort"]
    assert prepared["comparison_contract"]["keep_JOIN"]
    assert prepared["comparison_contract"]["tool_boundary_oracle_only_followup"]


def test_oracle_fingerprint_still_covers_the_policy(tmp_path):
    raw = prepare_artifact(original(tmp_path))
    path, digest = oracle_file(tmp_path, raw)
    changed = deepcopy(raw)
    changed["tool_boundary_preemption"]["cooldown_ms"] += 1
    Path(path).write_text(json.dumps(changed))
    assert hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest
    with pytest.raises(ValueError, match="hash"):
        WorkflowWorkOracle(path, digest)
