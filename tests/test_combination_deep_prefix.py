from array import array
from collections import Counter
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from beliefkv.runtime.workflow_combination import WorkflowCombinationController
from scripts.prepare_calibrated_combination_oracle import calibrated_artifact
from tests.test_calibrated_combination_preparation import calibration, original
from tests.test_full_only_native_lifecycle import full_native
from tests.test_workflow_combination_runtime import configured, runtime_with_queue


def checkpoint(tokens=10, *, missing=None, anchor=(77, 2.)):
    return {
        "checkpoint_tokens": tokens, "reusable_input_tokens": tokens,
        "device_checkpoint_tokens": 0,
        "missing_full_tokens": tokens if missing is None else missing,
        "missing_mamba_slots": 0, "component_leaves": ((0, (anchor,)),),
    }


def deep_queue(monkeypatch, tmp_path, *, enabled=True, reason="node ancestry exceeds bound"):
    from beliefkv.runtime import workflow_combination as combination
    from tests import test_workflow_combination_runtime as fixtures

    original_request = fixtures.request

    def request(*args, **kwargs):
        req = original_request(*args, **kwargs)
        req.session_id = "session-" + req.rid
        req.session_generation = 0
        return req

    monkeypatch.setattr(fixtures, "request", request)

    runtime, admission, env = runtime_with_queue(monkeypatch, tmp_path)
    controller = admission._combination
    controller.include_unreferenced_capacity = True
    controller.allow_deep_prefix_aggregate = enabled
    node = NS(id=77, creation_time=2.)
    runtime._native_cache.tree_core = NS(
        node_by_id=lambda identity: node, evictable_device_leaves=(),
    )
    monkeypatch.setattr(combination, "observe_unified_node_closure", lambda *args, **kwargs: NS(
        observable=False, nodes=(), reason=reason,
    ))
    return runtime, admission, env


def test_deep_aggregate_has_an_explicit_fingerprint_without_changing_intrinsic_work():
    kwargs = {"calibration_path": "/costs", "calibration_sha256": "a" * 64}
    before = calibrated_artifact(
        original(), calibration(), include_unreferenced_capacity=True, **kwargs,
    )
    after = calibrated_artifact(
        original(), calibration(), include_unreferenced_capacity=True,
        allow_deep_prefix_aggregate=True, **kwargs,
    )
    assert after["admission_cost_profile"]["kind"] == "r5_frozen_batch_cost_proxy_capacity_v3"
    assert after["admission_cost_profile"]["identity"] != before["admission_cost_profile"]["identity"]
    assert after["admission_cost_profile"]["costs"] == before["admission_cost_profile"]["costs"]
    assert after["requests"] == before["requests"]
    assert after["workflow_graphs"] == before["workflow_graphs"]


def test_deep_summary_requires_the_new_capacity_contract():
    with pytest.raises(ValueError, match="capacity"):
        calibrated_artifact(
            original(), calibration(), calibration_path="/costs", calibration_sha256="a" * 64,
            allow_deep_prefix_aggregate=True,
        )


@pytest.mark.parametrize("kind,enabled", (
    ("r5_frozen_batch_cost_proxy", False),
    ("r5_frozen_batch_cost_proxy_capacity_v2", False),
    ("r5_frozen_batch_cost_proxy_capacity_v3", True),
))
def test_actual_constructor_isolates_deep_summary_from_old_profiles(monkeypatch, tmp_path, kind, enabled):
    config = configured(tmp_path)
    path = Path(config["WORKFLOW_WORK_MODEL"])
    raw = json.loads(path.read_bytes())
    raw["admission_cost_profile"]["kind"] = kind
    path.write_text(json.dumps(raw))
    config["WORKFLOW_WORK_MODEL_SHA256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name, value in arm_environment(config, "full").items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "")
    runtime = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "deep.sock"))
    try:
        assert runtime._agent_admission._combination.allow_deep_prefix_aggregate is enabled
    finally:
        runtime.close()


def test_deep_prefix_comparison_reaches_actual_eligibility_and_is_replayable(monkeypatch, tmp_path):
    from scripts.audit_workflow_combination_choices import replay_choice

    runtime, admission, _env = deep_queue(monkeypatch, tmp_path)
    records, reads = [], []
    runtime._opportunity_writer = NS(record=records.append, close=lambda: None)

    def inspect(req, **kwargs):
        reads.append((req.rid, kwargs.get("refresh", False)))
        return checkpoint()

    monkeypatch.setattr(runtime, "_read_request_reentry", inspect)
    try:
        admission.tick()
        assert {owner.identity for owner in admission.policy.programs.values() if owner.active} == {"b", "c"}, (
            admission._combination._prefix_issue_detail
        )
        row = next(row for row in records if row["event"] == "workflow_combination_selection")
        assert set(row["decision"]["selected"]) == {"b", "c"}
        replayed = replay_choice(
            json.loads(json.dumps(row)), admission._combination.costs, admission._combination.cost_identity,
        )
        assert replayed["search_replayed"] and replayed["selected_set_changed"]
        assert len(reads) == 6 and sum(refresh for _, refresh in reads) == 3
        assert all(detail["kind"] == "fresh_native_aggregate" for detail in row["prefix_summaries"].values())
        assert row["unavailable_summaries"] == []
        assert row["physical_capacity_credit"] is False
        assert row["protected_prefix_anchor_ids"] == [77]
        rows = {entry["identity"]: entry for entry in row["candidates"]}
        b, c = rows["b"]["missing_extents"][0], rows["c"]["missing_extents"][0]
        assert b[0] != c[0] and b[1] + c[1] == 20
        assert admission.counts["workflow_combination_deep_prefix_refresh_attempts"] == 3
    finally:
        runtime.close()


@pytest.mark.parametrize("reason", (
    "pending_write_transfer", "pending_load_transfer", "invalid node lock count",
    "unrecognized FULL/MAMBA pool layout",
))
def test_other_native_rejections_do_not_enable_aggregate_fallback(monkeypatch, tmp_path, reason):
    runtime, admission, _env = deep_queue(monkeypatch, tmp_path, reason=reason)
    reads = []
    monkeypatch.setattr(runtime, "_read_request_reentry", lambda req, **kwargs: (
        reads.append(kwargs.get("refresh", False)) or checkpoint()
    ))
    try:
        admission.tick()
        assert admission.policy.programs["a"].active
        assert not admission.policy.programs["b"].active
        assert reads == [False]
        assert admission.counts["workflow_combination_deep_prefix_refresh_attempts"] == 0
    finally:
        runtime.close()


def test_legacy_profile_preserves_depth_fallback_without_a_second_native_read(monkeypatch, tmp_path):
    runtime, admission, _env = deep_queue(monkeypatch, tmp_path, enabled=False)
    reads = []
    monkeypatch.setattr(runtime, "_read_request_reentry", lambda req, **kwargs: (
        reads.append(kwargs.get("refresh", False)) or checkpoint()
    ))
    try:
        admission.tick()
        assert admission.policy.programs["a"].active
        assert not admission.policy.programs["b"].active
        assert reads == [False]
    finally:
        runtime.close()


@pytest.mark.parametrize("invalid", (
    "absent", "checkpoint", "missing", "excess_missing", "mamba", "reusable",
    "device", "device_missing", "anchors", "other_component", "stale_anchor",
    "negative_anchor", "nan_anchor",
))
def test_invalid_refreshed_aggregate_keeps_the_reference_and_never_retries(
    monkeypatch, tmp_path, invalid,
):
    runtime, admission, _env = deep_queue(monkeypatch, tmp_path)
    fresh = checkpoint()
    if invalid == "absent":
        fresh = None
    elif invalid == "checkpoint":
        fresh["checkpoint_tokens"] = True
    elif invalid == "missing":
        fresh["missing_full_tokens"] = -1
    elif invalid == "excess_missing":
        fresh["missing_full_tokens"] = 11
    elif invalid == "mamba":
        fresh["missing_mamba_slots"] = 1
    elif invalid == "reusable":
        fresh["reusable_input_tokens"] = 9
    elif invalid == "device":
        fresh["device_checkpoint_tokens"] = 11
    elif invalid == "device_missing":
        fresh["device_checkpoint_tokens"] = 1
    elif invalid == "anchors":
        fresh["component_leaves"] = ((0, ()),)
    elif invalid == "other_component":
        fresh["component_leaves"] += ((2, ((77, 2.),)),)
    elif invalid == "negative_anchor":
        fresh["component_leaves"] = ((0, ((77, -1.),)),)
    elif invalid == "nan_anchor":
        fresh["component_leaves"] = ((0, ((77, float("nan")),)),)
    else:
        fresh["component_leaves"] = ((0, ((77, 3.),)),)
    reads = []
    monkeypatch.setattr(runtime, "_read_request_reentry", lambda req, **kwargs: (
        reads.append(kwargs.get("refresh", False))
        or (fresh if kwargs.get("refresh", False) else checkpoint())
    ))
    try:
        admission.tick()
        assert admission.policy.programs["a"].active
        assert not admission.policy.programs["b"].active
        assert reads == [False, True]
        assert admission.counts["workflow_combination_deep_prefix_refresh_rejected"] == 1
    finally:
        runtime.close()


def test_refreshed_geometry_replaces_the_old_cached_checkpoint(monkeypatch, tmp_path):
    runtime, admission, _env = deep_queue(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime, "_read_request_reentry", lambda req, **kwargs: (
        checkpoint(20) if kwargs.get("refresh", False) else checkpoint()
    ))
    try:
        key = admission.latest["a"]
        summary = admission._combination._prefix(key, admission.policy.programs["a"])
        assert summary is not None, admission._combination._prefix_issue_detail
        extents, inputs = summary
        assert sum(tokens for _, tokens in extents) == 20
        assert inputs == len(admission._live_requests[key.request_id].origin_input_ids) - 20
        assert admission._combination._prefix_summary_detail["checkpoint_tokens"] == 20
    finally:
        runtime.close()


@pytest.mark.parametrize("full_native", (512,), indirect=True)
def test_actual_deep_native_checkpoint_participates_without_allocation_or_transfer(full_native):
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, writes, loads, _commits = full_native
    for size in range(1, 81):
        leaf, generation = insert_session(cache, list(range(1, size + 1)), "deep-session")
        assert cache.prepare_beliefkv_spawn_full(
            session_id="deep-session", session_generation=generation,
        )["issued"]
        cache.writing_check(finish_count=1)
    cache.beliefkv_cold_full_owner_validator = lambda owners: owners == (("deep-session", generation),)
    free = allocator.available_size()
    assert cache.reclaim_beliefkv_handoff_capacity(
        full_tokens=free + 80, mamba_slots=0,
    ) == {0: 64}
    request = NS(
        origin_input_ids=array("q", range(1, 82)), session_id="deep-session",
        session_generation=generation, session=None, extra_key=None, cache_salt=None,
    )
    observed = cache.inspect_beliefkv_reentry(request)
    assert observed is not None
    assert observed["checkpoint_tokens"] == 80 and observed["missing_full_tokens"] == 64
    key = PrefillCandidateKey("deep", "w", "i", "c", 0, 0, "deep-session", generation)
    reads = []

    def inspect(req, **kwargs):
        reads.append(kwargs.get("refresh", False))
        return cache.inspect_beliefkv_reentry(req)

    controller = object.__new__(WorkflowCombinationController)
    controller.include_unreferenced_capacity = True
    controller.allow_deep_prefix_aggregate = True
    controller.admission = NS(
        runtime=NS(_native_cache=cache, _read_request_reentry=inspect),
        _live_requests={"deep": request}, counts=Counter(),
    )
    before = allocator.available_size(), len(writes), len(loads)
    summary = controller._prefix(key, NS(steps=2))
    assert summary is not None, controller._prefix_issue_detail.get("aggregate_rejection")
    extents, inputs = summary
    assert sum(tokens for _, tokens in extents) == 64 and inputs == 1
    assert reads == [False, True]
    assert controller._prefix_summary_detail["kind"] == "fresh_native_aggregate"
    assert (allocator.available_size(), len(writes), len(loads)) == before
    assert leaf.component_data[0].value is None and len(leaf.component_data[0].host_value) == 1
