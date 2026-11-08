from copy import deepcopy
from functools import partial
import json
import hashlib
from pathlib import Path
from types import SimpleNamespace as NS
import time

import pytest

from beliefkv.runtime.workflow_work_oracle import (
    COMBINATION_WORK_KIND, WorkflowWorkOracle, WorkflowWorkOracleArtifact,
)
from beliefkv.predictor.workflow_work import R5_SCHEMA
from scripts.prepare_model_work_combination_oracle import prepare
from tests import test_global_workflow_oracle as fixture
from tests.test_workflow_combination_runtime import configured
from tests.test_workflow_work_oracle import oracle_file, source


def artifact(tmp_path):
    profile_path = Path(configured(tmp_path)["WORKFLOW_WORK_MODEL"])
    costs = json.loads(profile_path.read_bytes())["admission_cost_profile"]
    costs["kind"] = "r5_frozen_batch_cost_proxy_capacity_v7"
    costs["costs"].update(decode_cost_basis="full_batch", selection_objective="benefit")
    return {
        **source(), "kind": COMBINATION_WORK_KIND,
        "admission_order": "r5_combination_only",
        "remaining_work_ranking_enabled": True,
        "combination_work_readout": "three_bins",
        "admission_cost_profile": costs,
        "free_dispatch_coalescing_window_ms": 2000,
    }


def queued(monkeypatch, tmp_path):
    contract = artifact(tmp_path)
    original_file, original_settings = fixture.oracle_file, fixture.settings

    def adapted(path, raw):
        return original_file(path, {
            **raw, **{name: contract[name] for name in (
                "kind", "admission_order", "remaining_work_ranking_enabled",
                "combination_work_readout", "admission_cost_profile",
            )},
        })

    monkeypatch.setattr(fixture, "oracle_file", adapted)
    monkeypatch.setattr(fixture, "settings", lambda path: {
        **original_settings(path), "WORKFLOW_COMBINATION_ADMISSION": "1",
        "AGENT_PRESSURE_PAUSE_POLICY": "workflow_tool_aware",
    })
    from beliefkv.runtime import workflow_combination as combination

    monkeypatch.setattr(combination, "choose_combination", partial(
        combination.choose_combination, clock=lambda: 0,
    ))
    monkeypatch.setattr(combination, "observe_static_full_mamba_headroom", lambda cache: NS(
        observable=True, device_full_free_tokens=500, device_mamba_free_slots=0,
    ))
    runtime, admission = fixture.queued(
        monkeypatch, tmp_path, global_order=False, activate_first=False, window_ms=2000,
    )
    cache = runtime._native_cache
    cache.req_to_token_pool = NS(available_size=lambda: 24)
    runtime._native_running_batch = NS(reqs=[])
    runtime._native_max_running = 24
    runtime._native_input_reserve = runtime._native_chunk_input_reserve = 2048
    monkeypatch.setattr(runtime, "_read_request_reentry", lambda req, **kwargs: {
        "checkpoint_tokens": 0, "missing_full_tokens": 0, "missing_mamba_slots": 0,
    })
    return runtime, admission


@pytest.mark.parametrize("mutate", (
    lambda raw: raw.update(kind="workflow_model_tool_critical_path_oracle_v1"),
    lambda raw: raw.update(admission_order="global_ready_remaining_path"),
    lambda raw: raw.update(combination_work_readout="continuous"),
    lambda raw: raw.update(remaining_work_ranking_enabled=False),
    lambda raw: raw["admission_cost_profile"].update(kind="r5_frozen_batch_cost_proxy_capacity_v6"),
))
def test_new_oracle_contract_rejects_ambiguous_or_different_policy(tmp_path, mutate):
    raw = deepcopy(artifact(tmp_path))
    mutate(raw)
    with pytest.raises(ValueError):
        WorkflowWorkOracleArtifact(raw)


def test_work_truth_tracks_observed_service_without_using_future_tool_paths(tmp_path, monkeypatch):
    raw = artifact(tmp_path)
    oracle = WorkflowWorkOracle(*oracle_file(tmp_path, raw))
    monkeypatch.setattr(
        "beliefkv.runtime.workflow_work_oracle.cache_remaining_paths",
        lambda *args, **kwargs: pytest.fail("model-work oracle evaluated future tool paths"),
    )
    assert oracle.prediction("wf").remaining_work == 47.
    oracle.submit("wf", "started", "one", "child", "ctx", range(500))
    oracle.submit("wf", "generated", "one", 17)
    assert oracle.prediction("wf").remaining_work == 30.
    oracle.submit("wf", "completed", "one", "child", "ctx", range(500), range(30))
    assert oracle.prediction("wf").remaining_work == 12.
    assert oracle.status()["future_tool_durations_used"] is False
    assert oracle.status()["combination_only"] is True
    assert oracle.status()["deployable"] is False


@pytest.mark.parametrize("periodic", (False, True))
def test_exact_work_changes_same_R5_selector_but_not_other_ranking_paths(
    monkeypatch, tmp_path, periodic,
):
    runtime, admission = queued(monkeypatch, tmp_path)
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    try:
        assert admission._conditional_distribution
        assert not admission.rank_before_category
        assert admission._priority(None) == 1
        keys = [admission.latest[name] for name in ("long", "short")]
        assert admission.order_tool_replacement(keys) == keys
        assert all(
            admission._pressure_preference(item, 0.) is None
            for item in admission.policy.programs.values()
        )
        admission.tick(now=time.monotonic())
        assert not any(item.active for item in admission.policy.programs.values())
        deadline = admission._combination.next_dispatch
        if periodic:
            admission.next_tick = 0.
        monkeypatch.setattr(time, "monotonic", lambda: deadline + .001)
        admission.tick(now=deadline + .001)
        assert admission.policy.programs["short"].active
        assert not admission.policy.programs["long"].active
        decisions = [row for row in records if row["event"] == "workflow_combination_selection"]
        weights = {item["identity"]: item["weight"] for row in decisions for item in row["candidates"]}
        assert weights["short"] == 1.
        assert weights["long"] == .25
        state = admission.status()
        assert state["workflow_distribution_changes_pressure_victims"] is False
        assert state["workflow_distribution_changes_tool_replacement_order"] is False
        assert state["workflow_oracle_admission_order"] == "r5_combination_only"
        assert admission.policy.remaining_capacity() >= 0
    finally:
        runtime.close()


def test_ready_JOIN_and_still_blocked_work_keep_existing_authority(monkeypatch, tmp_path):
    runtime, admission = queued(monkeypatch, tmp_path)
    try:
        runtime._join_priority_pending["long"] = "observed-join"
        monkeypatch.setattr(
            runtime, "_ready_join_parent", lambda req, key: key.invocation_id == "long",
        )
        admission.tick(now=time.monotonic())
        assert admission.policy.programs["long"].active
        assert not admission.policy.programs["short"].active
    finally:
        runtime.close()


def test_still_tool_blocked_short_work_cannot_enter_the_comparison(monkeypatch, tmp_path):
    runtime, admission = queued(monkeypatch, tmp_path)
    try:
        runtime.graph.invocations["short"].active_tool_calls["tool"] = "execute"
        admission.tick(now=time.monotonic())
        deadline = admission._combination.next_dispatch
        monkeypatch.setattr(time, "monotonic", lambda: deadline + .001)
        admission.next_tick = 0.
        admission.tick(now=deadline + .001)
        assert not admission.policy.programs["short"].active
    finally:
        runtime.close()


def test_preparation_preserves_tokens_thresholds_resources_and_noncombination_controls(tmp_path):
    raw = artifact(tmp_path)
    labels = source()
    env = {
        "WORKFLOW_WORK_SOURCE": "learned", "BELIEFKV_WORKFLOW_WORK_SOURCE": "learned",
        "WORKFLOW_COMBINATION_ADMISSION": "1",
        "WORKFLOW_WORK_MODEL": "original", "BELIEFKV_WORKFLOW_WORK_MODEL": "original",
        "WORKFLOW_WORK_MODEL_SHA256": "old", "BELIEFKV_WORKFLOW_WORK_MODEL_SHA256": "old",
        "HICACHE_SIZE_GB": "400", "AGENT_CONTROL_INTERVAL_MS": "2000",
        "TOOL_PREFETCH": "0", "LAYER_HANDOFF": "0", "AGENT_PRESSURE_PAUSE_POLICY": "workflow_tool_aware",
    }
    config = {"common_environment": env, "comparison_contract": {}, "source_CPU_binding": {}}
    original = deepcopy((labels, config))
    model = NS(schema=R5_SCHEMA, thresholds=(10., 20.), raw=raw)
    truth, candidate = prepare(config, model, labels, tmp_path / "future.json")
    assert (labels, config) == original
    assert truth["work_thresholds"] == [10., 20.]
    assert truth["requests"] == labels["requests"]
    assert truth["workflow_graphs"] == labels["workflow_graphs"]
    assert truth["admission_cost_profile"] == raw["admission_cost_profile"]
    for name in ("HICACHE_SIZE_GB", "AGENT_CONTROL_INTERVAL_MS", "TOOL_PREFETCH",
                 "LAYER_HANDOFF", "AGENT_PRESSURE_PAUSE_POLICY"):
        assert candidate["common_environment"][name] == env[name]
    assert "source_CPU_binding" not in candidate
    assert candidate["common_environment"]["WORKFLOW_WORK_MODEL_SHA256"] == hashlib.sha256(
        (json.dumps(truth, ensure_ascii=False, allow_nan=False) + "\n").encode()
    ).hexdigest()
