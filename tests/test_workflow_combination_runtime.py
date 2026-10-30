from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS
import time

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import MODES, arm_environment
from beliefkv.runtime.reference_thunder_admission import validate_reference_admission
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from beliefkv.runtime.workflow_work_worker import WorkPrediction
from tests.test_internal_comparison import observed
from tests.test_paper_baseline_runtime import request
from tests.test_workflow_size_admission import settings


def configured(tmp_path):
    config = {
        **settings(tmp_path), "AGENT_FREE_DISPATCH_INTERVAL_MS": "500",
        "WORKFLOW_COMBINATION_ADMISSION": "1",
    }
    path = Path(config["WORKFLOW_WORK_MODEL"])
    raw = json.loads(path.read_bytes())
    raw["admission_cost_profile"] = {
        "kind": "r5_frozen_batch_cost_proxy", "identity": "cpu_fixture_only",
        "costs": {
            "prefill_ms_per_token": .0001, "h2d_ms_per_token": .001,
            "decode_ms_by_width": [1 + index * .1 for index in range(24)],
            "reference_context_tokens": 100000.,
        },
    }
    path.write_text(json.dumps(raw))
    config["WORKFLOW_WORK_MODEL_SHA256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return config


def runtime_with_queue(monkeypatch, tmp_path):
    env = arm_environment(configured(tmp_path), "full")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "")
    runtime = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "r5.sock"))
    runtime.attach_native_cache(NS(
        tree_components=(0,), token_to_kv_pool_allocator=NS(size_full=500),
        req_to_token_pool=NS(available_size=lambda: 24),
    ))
    a = runtime._agent_admission
    a.next_tick = float("inf")
    runtime._native_running_batch = NS(reqs=[])
    runtime._native_max_running = 24
    runtime._native_input_reserve = runtime._native_chunk_input_reserve = 2048
    from beliefkv.runtime import workflow_combination as combination
    monkeypatch.setattr(combination, "observe_static_full_mamba_headroom", lambda cache: NS(
        observable=True, device_full_free_tokens=10000, device_mamba_free_slots=0,
    ))
    for index, (identity, tokens) in enumerate((("a", 300), ("b", 150), ("c", 150))):
        runtime.on_events((
            replace(observed(index * 2, Kind.WORKFLOW_START), workflow_id=identity),
            replace(observed(
                index * 2 + 1, Kind.INVOCATION_CREATE, invocation_id=identity,
                context_id=identity + "-context",
            ), workflow_id=identity),
        ))
        req = request(identity + "-request", identity, tokens=tokens, workflow=identity)
        req.output_ids.clear()
        runtime.register_visible_request(req)
    a._work.close()
    a._work._stopped = False
    a._work._predictions.update({
        identity: WorkPrediction(probabilities, bucket, 1, time.monotonic(), "model")
        for identity, probabilities, bucket in (
            ("a", (.8, .2, 0.), 0), ("b", (.2, .8, 0.), 1), ("c", (.2, .8, 0.), 1),
        )
    })
    return runtime, a, env


def test_shared_event_selection_changes_actual_eligibility_and_native_service_candidates(
    monkeypatch, tmp_path,
):
    runtime, a, env = runtime_with_queue(monkeypatch, tmp_path)
    try:
        assert not any(item.active for item in a.policy.programs.values())
        assert a.counts["workflow_combination_first_registrations_deferred"] == 3
        a.tick()
        assert {item.identity for item in a.policy.programs.values() if item.active} == {"b", "c"}
        assert a.policy.remaining_capacity() == 0
        assert a.counts["workflow_combination_combination_changed"] == 1
        requests = [a._live_requests[name + "-request"] for name in ("a", "b", "c")]
        plan = runtime.plan_native_prefill(requests, running_batch=NS(reqs=[]), adder=None)
        actual = select_native_prefill_candidates(
            requests, plan=plan, current_semantic_revision=runtime.semantic_revision,
        )
        assert actual.candidates == tuple(requests[1:])
        validate_reference_admission(
            a.status(), free_continuation_admission=True, free_dispatch_interval_ms=500,
            control_interval_ms=2000, workflow_size_resume=True,
            workflow_work_model_sha256=env["BELIEFKV_WORKFLOW_WORK_MODEL_SHA256"],
            workflow_combination_admission=True,
        )
        with pytest.raises(ValueError, match="silently enabled"):
            validate_reference_admission(
                a.status(), free_continuation_admission=True, free_dispatch_interval_ms=500,
                control_interval_ms=2000, workflow_size_resume=True,
                workflow_work_model_sha256=env["BELIEFKV_WORKFLOW_WORK_MODEL_SHA256"],
            )
    finally:
        runtime.close()


def test_missing_resource_summary_preserves_native_reference_grants(monkeypatch, tmp_path):
    runtime, a, _env = runtime_with_queue(monkeypatch, tmp_path)
    try:
        runtime._native_max_running = None
        a.tick()
        assert a.policy.programs["a"].active
        assert not a.policy.programs["b"].active
        assert not a.policy.programs["c"].active
    finally:
        runtime.close()


def test_periodic_resume_uses_the_same_set_evaluation(monkeypatch, tmp_path):
    runtime, a, _env = runtime_with_queue(monkeypatch, tmp_path)
    try:
        a.next_tick = 0.
        a.tick()
        assert set(a.policy.last_selection["selected_in_priority_order"]) == {"b", "c"}
        assert {item.identity for item in a.policy.programs.values() if item.active} == {"b", "c"}
    finally:
        runtime.close()


def test_combination_journal_serializes_exact_request_and_prediction_snapshots(monkeypatch, tmp_path):
    runtime, a, _env = runtime_with_queue(monkeypatch, tmp_path)
    records = []
    runtime._opportunity_writer = NS(record=lambda row: records.append(json.loads(json.dumps(row))))
    try:
        a.tick()
        journal = [row for row in records if row["event"] == "workflow_combination_selection"]
        assert len(journal) == 1
        choice = journal[0]
        assert set(choice["decision"]["selected"]) == {"b", "c"}
        assert choice["keys"]["b"]["request_id"] == "b-request"
        assert choice["predictions"]["b"]["revision"] == 1
        assert choice["resources"]["chunked_input"] is True
        assert a.counts["workflow_combination_total_cpu_ns"] > 0
    finally:
        runtime._opportunity_writer = None
        runtime.close()


def test_observed_join_recovery_keeps_its_blocked_donor_authority(monkeypatch, tmp_path):
    from tests.test_join_blocked_handoff import bind, blocked
    from tests.test_reference_join_free_admission import join_wait

    env = arm_environment({
        **configured(tmp_path), "AGENT_JOIN_FREE_ADMISSION": "1",
        "AGENT_JOIN_BLOCKED_HANDOFF": "1", "AGENT_COADMIT_JOIN_CHILDREN": "1",
    }, "full")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "")
    runtime = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "r5-join.sock"))
    runtime.attach_native_cache(NS(
        tree_components=(0,), token_to_kv_pool_allocator=NS(size_full=700),
    ))
    try:
        a, _key = join_wait(runtime, tokens=200)
        key = bind(runtime, "parent")
        donor = blocked(runtime)
        assert a.claim_restore(key, source="join_ticket")
        assert a.holds_restore(key) and not donor.active
        assert a.policy.programs["parent"].active
        assert a.counts["join_blocked_handoff_grants"] == 1
        assert not runtime.enable_tool_prefetch and not runtime.layer_handoff_enabled
    finally:
        runtime.close()


def test_an_active_agent_is_not_replaced_by_a_better_prediction(monkeypatch, tmp_path):
    runtime, a, _env = runtime_with_queue(monkeypatch, tmp_path)
    try:
        a.policy.programs["a"].active = True
        a.tick()
        assert a.policy.programs["a"].active
        assert not a.policy.programs["a"].marked_for_pause
        assert not a.policy.programs["b"].active and not a.policy.programs["c"].active
    finally:
        runtime.close()


@pytest.mark.parametrize("mode", MODES)
def test_r5_is_explicit_and_isolated_from_native_and_paper_arms(tmp_path, mode):
    env = arm_environment(configured(tmp_path), mode)
    assert env["BELIEFKV_WORKFLOW_COMBINATION_ADMISSION"] == ("1" if mode == "full" else "0")
