from copy import deepcopy
from types import SimpleNamespace as NS
import time

import pytest

from beliefkv.policy.workflow_size_resume import WorkflowToolState
from beliefkv.runtime.workflow_work_oracle import (
    CONTINUOUS_PRESSURE_WORK_KIND, TOOL_BAND_PRESSURE_ORDER,
    WorkflowWorkOracle, WorkflowWorkOracleArtifact,
)
from beliefkv.predictor.workflow_work import R5_SCHEMA
from beliefkv.predictor.workflow_continuous_work import remaining_work_weight
from scripts.prepare_model_work_combination_oracle import prepare
from scripts.run_model_work_pressure_oracle import verify_pressure_extension
from tests.test_model_work_combination_oracle import artifact as binned_artifact, continuous_artifact, queued
from tests.test_workflow_work_oracle import oracle_file, source


def artifact(tmp_path):
    raw = continuous_artifact(tmp_path)
    raw.update(
        kind=CONTINUOUS_PRESSURE_WORK_KIND,
        admission_order="r5_combination_and_tool_band_pressure",
        pressure_work_order=TOOL_BAND_PRESSURE_ORDER,
    )
    return raw


def test_launch_only_adds_the_declared_pressure_scope(tmp_path):
    previous, candidate = continuous_artifact(tmp_path), artifact(tmp_path)
    before = deepcopy((previous, candidate))
    verify_pressure_extension(previous, candidate)
    assert (previous, candidate) == before


@pytest.mark.parametrize("change", (
    "old_kind", "new_kind", "scope", "order", "requests", "reference", "costs",
))
def test_launch_rejects_pressure_scope_or_original_work_changes(tmp_path, change):
    previous, candidate = continuous_artifact(tmp_path), artifact(tmp_path)
    if change == "old_kind":
        previous["kind"] = "another"
    elif change == "new_kind":
        candidate["kind"] = previous["kind"]
    elif change == "scope":
        candidate["pressure_work_order"] = "global"
    elif change == "order":
        candidate["admission_order"] = previous["admission_order"]
    elif change == "requests":
        candidate["requests"] = {}
    elif change == "reference":
        candidate["combination_work_reference"]["reference_work"] = 999.
    else:
        candidate["admission_cost_profile"]["identity"] = "another"
    with pytest.raises(ValueError):
        verify_pressure_extension(previous, candidate)


def pressure(admission, states):
    policy = admission.policy
    policy.capacity_tokens, policy.buffer_tokens = 100, 0
    policy.observe_ready = lambda item: False
    for item in policy.programs.values():
        item.active = item.pending = item.acting = False
        item.marked_for_pause = False
    for owner, tokens in (("long", 90), ("short", 20)):
        item = policy.programs[owner]
        item.tokens, item.active, item.acting = tokens, True, True
    policy.tool_pressure_state = lambda item, now: states.get(
        item.identity, WorkflowToolState(True, True, protected=True),
    )
    admission._rank_cache.clear()
    return policy


def test_pressure_truth_has_an_explicit_separate_kind_and_no_future_tool_path(tmp_path, monkeypatch):
    raw = artifact(tmp_path)
    original = deepcopy(raw)
    parsed = WorkflowWorkOracleArtifact(raw)
    assert parsed.model_work_only and parsed.continuous_combination
    assert not parsed.combination_only and parsed.pressure_work_ranking
    oracle = WorkflowWorkOracle(*oracle_file(tmp_path, raw))
    monkeypatch.setattr(
        "beliefkv.runtime.workflow_work_oracle.cache_remaining_paths",
        lambda *_args, **_kwargs: pytest.fail("pressure truth evaluated future tool paths"),
    )
    assert oracle.prediction("wf").remaining_work == 47.
    state = oracle.status()
    assert state["future_tool_durations_used"] is False
    assert state["pressure_work_order"] == TOOL_BAND_PRESSURE_ORDER
    assert not state["combination_only"] and state["model_work_only"]
    assert raw == original
    oracle.close()


@pytest.mark.parametrize("change", ("legacy_kind", "missing_scope", "wrong_scope", "old_order"))
def test_pressure_scope_cannot_be_smuggled_into_legacy_oracles(tmp_path, change):
    raw = artifact(tmp_path)
    if change == "legacy_kind":
        raw["kind"] = continuous_artifact(tmp_path)["kind"]
    elif change == "missing_scope":
        raw.pop("pressure_work_order")
    elif change == "wrong_scope":
        raw["pressure_work_order"] = "global_preemption"
    else:
        raw["admission_order"] = "r5_combination_only"
    with pytest.raises(ValueError):
        WorkflowWorkOracleArtifact(raw)


@pytest.mark.parametrize("enabled", (False, True))
def test_same_band_pressure_keeps_short_work_instead_of_only_the_smallest_kv(
    monkeypatch, tmp_path, enabled,
):
    runtime, admission = queued(monkeypatch, tmp_path, continuous=True, pressure=enabled)
    try:
        states = {owner: WorkflowToolState(True, True, .1) for owner in ("long", "short")}
        policy = pressure(admission, states)
        policy.tick(now=10.)
        selected = "long" if enabled else "short"
        assert not policy.programs[selected].active
        assert policy.programs["short" if enabled else "long"].active
        assert policy.last_pressure[0]["prediction_used"] is enabled
        assert policy.remaining_capacity() >= 0
        state = admission.status()
        assert state["workflow_distribution_changes_pressure_victims"] is enabled
        keys = [admission.latest[name] for name in ("long", "short")]
        assert admission.order_tool_replacement(keys) == keys
        if enabled:
            record = policy.last_pressure[0]
            assert record["same_band_reference_selected"] == "short"
            assert {item["invocation_id"] for item in record["eligible_candidates"]} == {"long", "short"}
            assert all(item["tool_state"]["retention"] == .1 for item in record["eligible_candidates"])
        else:
            assert "eligible_candidates" not in policy.last_pressure[0]
    finally:
        runtime.close()


@pytest.mark.parametrize("guard", ("soon_tool", "protected", "unsafe"))
def test_tool_retention_and_native_protections_precede_remaining_work(monkeypatch, tmp_path, guard):
    runtime, admission = queued(monkeypatch, tmp_path, continuous=True, pressure=True)
    try:
        states = {
            "short": WorkflowToolState(True, True, .1),
            "long": WorkflowToolState(
                True, guard != "unsafe", 1. if guard == "soon_tool" else .1,
                protected=guard == "protected",
            ),
        }
        policy = pressure(admission, states)
        policy.tick(now=10.)
        assert policy.programs["long"].active
        assert not policy.programs["short"].active
        assert policy.last_pressure[0]["selected"] == "short"
    finally:
        runtime.close()


def test_mutable_compute_is_only_marked_and_cannot_create_capacity(monkeypatch, tmp_path):
    runtime, admission = queued(monkeypatch, tmp_path, continuous=True, pressure=True)
    try:
        policy = pressure(admission, {owner: WorkflowToolState() for owner in ("long", "short")})
        for owner in ("long", "short"):
            item = policy.programs[owner]
            item.acting, item.pending = False, True
        policy.tick(now=10.)
        assert policy.programs["long"].marked_for_pause
        assert not policy.programs["short"].marked_for_pause
        assert policy.remaining_capacity() == -10
        assert all(policy.programs[owner].active for owner in ("long", "short"))
        assert policy.last_pressure[0]["physical_capacity_credit"] is False
    finally:
        runtime.close()


def test_unknown_work_falls_back_within_the_tool_band(monkeypatch, tmp_path):
    runtime, admission = queued(monkeypatch, tmp_path, continuous=True, pressure=True)
    try:
        policy = pressure(admission, {
            owner: WorkflowToolState(True, True, .1) for owner in ("long", "short")
        })
        monkeypatch.setattr(admission, "_prediction", lambda *_args: None)
        policy.tick(now=10.)
        assert not policy.programs["short"].active
        assert not policy.last_pressure[0]["prediction_used"]
    finally:
        runtime.close()


def test_pressure_extension_keeps_continuous_weights_in_ordinary_combination_admission(
    monkeypatch, tmp_path,
):
    runtime, admission = queued(monkeypatch, tmp_path, continuous=True, pressure=True)
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    try:
        keys = [admission.latest[owner] for owner in ("long", "short")]
        selected = admission._combination.select(
            keys, keys[:1], capacity=500, maximum=2, source="periodic_decay_resume",
        )
        assert [key.invocation_id for key in selected] == ["short"]
        choices = [row for row in records if row["event"] == "workflow_combination_selection"]
        weights = {item["identity"]: item["weight"] for item in choices[0]["candidates"]}
        for owner in ("long", "short"):
            prediction = admission._work.prediction(admission.latest[owner].root_workflow_id)
            assert weights[owner] == remaining_work_weight(
                prediction.remaining_work, prediction.reference_work,
            )
        assert weights["long"] < weights["short"] < 1.
    finally:
        runtime.close()


def test_real_control_records_same_band_candidates_with_exact_request_keys(monkeypatch, tmp_path):
    runtime, admission = queued(monkeypatch, tmp_path, continuous=True, pressure=True)
    records = []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)
    try:
        policy = admission.policy
        policy.buffer_tokens = 0
        policy.observe_ready = lambda item: False
        for owner, item in policy.programs.items():
            item.active = item.acting = owner in ("active", "short")
            item.pending = False
        policy.tool_pressure_state = lambda item, now: WorkflowToolState(True, True, .1)
        admission.next_tick = 0.
        admission._tick_working_set(now=time.monotonic())
        choices = [row for row in records if row["event"] == "workflow_remaining_pressure_pause"]
        assert len(choices) == 1
        choice = choices[0]
        assert choice["selected"] == "active"
        assert choice["same_band_reference_selected"] == "short"
        assert set(choice["eligible_candidate_keys"]) == {"active", "short"}
        for owner, recorded in choice["eligible_candidate_keys"].items():
            assert recorded["request_id"] == admission.latest[owner].request_id
            assert recorded["root_workflow_id"] == admission.latest[owner].root_workflow_id
        assert choice["physical_capacity_credit"] is False
    finally:
        runtime.close()


def preparation_inputs(tmp_path):
    raw, digest = binned_artifact(tmp_path), "f" * 64
    common = {}
    for prefix in ("", "BELIEFKV_"):
        common.update({
            prefix + "WORKFLOW_WORK_SOURCE": "learned",
            prefix + "WORKFLOW_WORK_MODEL": "old",
            prefix + "WORKFLOW_WORK_MODEL_SHA256": digest,
            prefix + "WORKFLOW_COMBINATION_ADMISSION": "1",
            prefix + "AGENT_PRESSURE_PAUSE_POLICY": "workflow_tool_aware",
        })
    model = NS(schema=R5_SCHEMA, raw=raw, thresholds=tuple(raw["work_thresholds"]))
    continuous = NS(raw={
        **deepcopy(raw),
        "continuous_work_readout": continuous_artifact(tmp_path)["combination_work_reference"],
        "metadata": {"decision_eligible": False, "continuous_work_source_model_sha256": digest},
    })
    return {"common_environment": common, "comparison_contract": {}}, model, continuous


def test_preparation_preserves_all_work_and_declares_pressure_as_an_extension(tmp_path):
    config, model, continuous = preparation_inputs(tmp_path)
    labels = source()
    before = deepcopy((config, model.raw, continuous.raw, labels))
    truth, candidate = prepare(
        config, model, labels, tmp_path / "pressure.json", continuous_model=continuous,
        tool_band_pressure=True,
    )
    assert truth["kind"] == CONTINUOUS_PRESSURE_WORK_KIND
    for field in ("requests", "workflows", "workflow_graphs"):
        assert truth[field] == labels[field]
    assert truth["admission_cost_profile"] == model.raw["admission_cost_profile"]
    contract = candidate["comparison_contract"]
    assert contract["ordinary_combination_selector_unchanged"]
    assert contract["oracle_future_work_used_outside_combination"]
    assert contract["oracle_future_tool_durations_used"] is False
    assert contract["pressure_change_is_pure_prediction_effect"] is False
    assert (config, model.raw, continuous.raw, labels) == before
    assert continuous.raw["metadata"]["decision_eligible"] is False


@pytest.mark.parametrize("change", ("no_continuous", "different_tool_policy", "nonboolean"))
def test_preparation_rejects_implicit_or_unsafe_pressure_extensions(tmp_path, change):
    config, model, continuous = preparation_inputs(tmp_path)
    flag = True
    if change == "no_continuous":
        continuous = None
    elif change == "different_tool_policy":
        config["common_environment"]["AGENT_PRESSURE_PAUSE_POLICY"] = "workflow_remaining"
    else:
        flag = 1
    with pytest.raises(ValueError, match="pressure work order"):
        prepare(
            config, model, source(), tmp_path / "bad.json", continuous_model=continuous,
            tool_band_pressure=flag,
        )
