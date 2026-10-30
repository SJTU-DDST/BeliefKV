from dataclasses import replace
from functools import partial
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
    # Selection assertions use a deterministic clock; deadline fallback has its
    # own adapter test and the policy's explicit deadline regressions.
    monkeypatch.setattr(
        combination, "choose_combination",
        partial(combination.choose_combination, clock=lambda: 0),
    )
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


def test_runtime_soft_deadline_preserves_reference_without_changing_the_budget(monkeypatch, tmp_path):
    from beliefkv.policy.workflow_combination import choose_combination
    from beliefkv.runtime import workflow_combination as combination

    runtime, a, _env = runtime_with_queue(monkeypatch, tmp_path)
    ticks = iter((0, 500_000, 600_000))
    monkeypatch.setattr(combination, "choose_combination", partial(choose_combination, clock=lambda: next(ticks)))
    try:
        a.tick()
        assert {item.identity for item in a.policy.programs.values() if item.active} == {"a"}
        assert a.counts["workflow_combination_soft_deadline_reference_fallback"] == 1
    finally:
        runtime.close()


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
    records = []
    runtime._opportunity_writer = NS(record=records.append)
    try:
        a.next_tick = 0.
        a.tick()
        assert set(a.policy.last_selection["selected_in_priority_order"]) == {"b", "c"}
        assert {item.identity for item in a.policy.programs.values() if item.active} == {"b", "c"}
        journal = [row for row in records if row["event"] == "workflow_combination_selection"]
        periodic = next(row for row in journal if row["source"] == "periodic_decay_resume")
        assert set(periodic["decision"]["reference"]) == {"b", "c"}
    finally:
        runtime._opportunity_writer = None
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

def test_original_dispatch_reference_is_logged_before_any_work_sort(monkeypatch, tmp_path):
    runtime, a, _env = runtime_with_queue(monkeypatch, tmp_path)
    records = []
    runtime._opportunity_writer = NS(record=records.append)
    try:
        a.tick()
        journal = [row for row in records if row["event"] == "workflow_combination_selection"]
        assert journal[0]["decision"]["reference"] == ("a",)
        assert set(journal[0]["decision"]["selected"]) == {"b", "c"}
        assert not any(row["event"] == "workflow_size_free_dispatch_order" for row in records)
    finally:
        runtime._opportunity_writer = None
        runtime.close()


def test_unavailable_resource_summary_is_logged_without_changing_reference(monkeypatch, tmp_path):
    runtime, a, _env = runtime_with_queue(monkeypatch, tmp_path)
    records = []
    runtime._opportunity_writer = NS(record=records.append)
    try:
        runtime._native_max_running = None
        a.tick()
        row = next(row for row in records if row["event"] == "workflow_combination_selection")
        assert row["resources"] is None
        assert row["decision"]["selected"] == ("a",)
        assert row["decision"]["reason"] == "native_resources_unavailable_reference_fallback"
        assert row["cold_device_extents"] == ()
    finally:
        runtime._opportunity_writer = None
        runtime.close()

def test_unreadable_optional_prefix_does_not_hide_a_safe_better_candidate(monkeypatch, tmp_path):
    runtime, a, _env = runtime_with_queue(monkeypatch, tmp_path)
    records = []
    runtime._opportunity_writer = NS(record=records.append)
    try:
        a._work._predictions["c"] = WorkPrediction((1., 0., 0.), 0, 1, time.monotonic(), "model")
        original = a._combination._prefix

        def prefix(key, item):
            if key.invocation_id == "b":
                a._combination._prefix_issue = "native_checkpoint_unavailable"
                return None
            return original(key, item)

        monkeypatch.setattr(a._combination, "_prefix", prefix)
        a.tick()
        assert a.policy.programs["c"].active and not a.policy.programs["a"].active
        selection = next(row for row in records if row["event"] == "workflow_combination_selection")
        assert selection["decision"]["reference"] == ("a",)
        assert selection["decision"]["selected"] == ("c",)
        assert selection["unavailable_summaries"][0]["key"]["invocation_id"] == "b"
        assert a.counts["workflow_combination_optional_summaries_skipped"] == 1
    finally:
        runtime._opportunity_writer = None
        runtime.close()


def test_unreadable_reference_prefix_preserves_original_authority(monkeypatch, tmp_path):
    runtime, a, _env = runtime_with_queue(monkeypatch, tmp_path)
    records = []
    runtime._opportunity_writer = NS(record=records.append)
    try:
        def unavailable(*_args):
            a._combination._prefix_issue = "unobservable_missing_closure"
            return None

        monkeypatch.setattr(a._combination, "_prefix", unavailable)
        a.tick()
        assert a.policy.programs["a"].active and not a.policy.programs["c"].active
        selection = next(row for row in records if row["event"] == "workflow_combination_selection")
        assert selection["decision"]["reason"] == "reference_candidate_summary_unavailable"
        assert selection["unavailable_summaries"][0]["key"]["invocation_id"] == "a"
    finally:
        runtime._opportunity_writer = None
        runtime.close()


@pytest.mark.parametrize("unsafe", (
    "none", "device_lock", "host_lock", "shared", "generation", "write", "load",
    "partial_host", "not_blocked", "not_leaf",
))
def test_cold_capacity_only_counts_settled_private_blocked_leaves(monkeypatch, tmp_path, unsafe):
    runtime, a, _env = runtime_with_queue(monkeypatch, tmp_path)
    try:
        key = a.latest["a"]
        data = NS(value=list(range(200)), host_value=list(range(200)),
                  lock_ref=0, host_lock_ref=0, session_ref=1, session_ids={key.session_id})
        node = NS(id=42, creation_time=1., key=list(range(200)), backuped=True,
                  component_data={0: data}, write_through_pending_id=None,
                  load_back_pending_id=None)
        cache = runtime._native_cache
        cache.tree_core = NS(node_by_id=lambda identity: node, evictable_device_leaves=[node])
        cache.session_refs = NS(_session_generations={key.session_id: key.session_generation})
        runtime._parent_pressure_candidates = {42: (key, 1.)}
        monkeypatch.setattr(runtime, "_live_parent_pressure_node", lambda *_args: unsafe != "not_blocked")
        if unsafe == "device_lock":
            data.lock_ref = 1
        elif unsafe == "host_lock":
            data.host_lock_ref = 1
        elif unsafe == "shared":
            data.session_ref = 2
        elif unsafe == "generation":
            cache.session_refs._session_generations[key.session_id] = -1
        elif unsafe == "write":
            node.write_through_pending_id = 3
        elif unsafe == "load":
            node.load_back_pending_id = 3
        elif unsafe == "partial_host":
            data.host_value.pop()
        elif unsafe == "not_leaf":
            cache.tree_core.evictable_device_leaves.clear()
        assert a._combination._cold_capacity() == (((42, 1., 200),) if unsafe == "none" else ())
        assert len(data.value) == 200
    finally:
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
