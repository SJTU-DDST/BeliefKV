from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace as NS
import time

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.reference_thunder_admission import validate_reference_admission
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from beliefkv.runtime.workflow_work_worker import WorkPrediction
from scripts.prepare_workflow_size_candidate import prepare
from scripts.run_causal_comparison import preflight_runtime
from tests.test_internal_comparison import observed
from tests.test_paper_baseline_runtime import request
from tests.test_workflow_size_admission import settings

ROOT = Path(__file__).resolve().parents[1]


def queued(monkeypatch, tmp_path):
    environment = arm_environment({
        **settings(tmp_path), "AGENT_FREE_DISPATCH_INTERVAL_MS": "500",
    }, "full")
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "")
    runtime = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "dispatch.sock"))
    runtime.attach_native_cache(NS(
        tree_components=(0,), token_to_kv_pool_allocator=NS(size_full=500),
    ))
    adapter = runtime._agent_admission
    adapter.next_tick = float("inf")
    for index, (identity, workflow, size) in enumerate((
        ("active", "background", 400), ("long", "long-work", 100),
        ("short", "short-work", 300),
    )):
        runtime.on_events((
            replace(observed(index * 2, Kind.WORKFLOW_START), workflow_id=workflow),
            replace(observed(
                index * 2 + 1, Kind.INVOCATION_CREATE,
                invocation_id=identity, context_id=identity + "-context",
            ), workflow_id=workflow),
        ))
        req = request(identity + "-request", identity, tokens=size, workflow=workflow)
        req.output_ids.clear()
        runtime.register_visible_request(req)
    adapter._work.close()
    adapter._work._stopped = False
    now = time.monotonic()
    adapter._work._predictions.update({
        "long-work": WorkPrediction((.01, .01, .98), 2, 1, now, "model"),
        "short-work": WorkPrediction((.98, .01, .01), 0, 1, now, "model"),
    })
    assert adapter.policy.programs["active"].active
    assert not adapter.policy.programs["long"].active
    assert not adapter.policy.programs["short"].active
    return runtime, adapter, environment


def test_ranked_dispatch_changes_actual_eligibility_and_native_candidate_selection(
    monkeypatch, tmp_path,
):
    runtime, adapter, _ = queued(monkeypatch, tmp_path)
    try:
        adapter.forget("active")
        adapter.tick()
        assert adapter.policy.programs["short"].active
        assert not adapter.policy.programs["long"].active
        assert adapter.policy.remaining_capacity() == 100
        assert adapter.counts["control_ticks"] == 0
        assert adapter.counts["workflow_size_dispatch_order_changes"] == 1
        requests = [adapter._live_requests[name + "-request"] for name in ("long", "short")]
        plan = runtime.plan_native_prefill(requests, running_batch=NS(reqs=[]), adder=None)
        selected = select_native_prefill_candidates(
            requests, plan=plan, current_semantic_revision=runtime.semantic_revision,
        )
        assert selected.candidates == (requests[1],)
        assert not runtime.enable_tool_prefetch and not runtime.enable_frontier_restore
    finally:
        runtime.close()


def test_free_dispatch_never_pauses_an_active_agent_to_follow_work_prediction(monkeypatch, tmp_path):
    runtime, adapter, _ = queued(monkeypatch, tmp_path)
    try:
        adapter.tick()
        assert adapter.policy.programs["active"].active
        assert not adapter.policy.programs["active"].marked_for_pause
        assert not adapter.policy.programs["short"].active
        assert adapter.policy.remaining_capacity() == 0
    finally:
        runtime.close()


@pytest.mark.parametrize("case", ("join", "continuation"))
def test_real_join_and_original_resume_category_precede_work_bucket(
    monkeypatch, tmp_path, case,
):
    runtime, adapter, _ = queued(monkeypatch, tmp_path)
    try:
        adapter.forget("active")
        if case == "join":
            runtime._join_priority_pending["long"] = "real-join"
            monkeypatch.setattr(
                runtime, "_ready_join_parent", lambda _req, key: key.invocation_id == "long",
            )
        else:
            adapter.policy.programs["long"].steps = 2
        adapter.tick()
        assert adapter.policy.programs["long"].active
        assert not adapter.policy.programs["short"].active
        assert adapter.policy.remaining_capacity() == 300
    finally:
        runtime.close()


@pytest.mark.parametrize("unsupported", ("absent", "uncertain", "error", "stale"))
def test_unavailable_predictions_keep_original_rotating_window_order(
    monkeypatch, tmp_path, unsupported,
):
    runtime, adapter, _ = queued(monkeypatch, tmp_path)
    try:
        adapter.forget("active")
        if unsupported == "absent":
            adapter._work._predictions.clear()
        elif unsupported == "uncertain":
            for workflow in tuple(adapter._work._predictions):
                adapter._work._predictions[workflow] = WorkPrediction(
                    (.34, .33, .33), 1, 1, time.monotonic(), "uncertain_medium",
                )
        elif unsupported == "error":
            adapter._work.error = "model_unavailable"
        else:
            adapter._work._pending_since.update({"long-work": -100., "short-work": -100.})
        adapter.tick()
        assert adapter.policy.programs["long"].active
        assert not adapter.policy.programs["short"].active
        assert adapter.counts["workflow_size_dispatch_order_changes"] == 0
    finally:
        runtime.close()


def test_dispatch_scope_and_fresh_background_cache_are_explicit(monkeypatch, tmp_path):
    runtime, adapter, environment = queued(monkeypatch, tmp_path)
    try:
        before = dict(adapter._rank_cache)
        keys = [adapter.latest[name] for name in ("long", "short")]
        assert adapter._order_dispatch_keys(keys, {}) == keys[::-1]
        adapter._work._predictions["long-work"] = WorkPrediction(
            (.99, .005, .005), 0, 2, time.monotonic(), "model",
        )
        assert adapter._order_dispatch_keys(keys, {}) == keys
        assert adapter._rank_cache == before
        state = adapter.status()
        assert state["workflow_size_resume_scope"] == "paused_periodic_and_bounded_free_dispatch"
        validate_reference_admission(
            state, free_continuation_admission=True, free_dispatch_interval_ms=500,
            control_interval_ms=2000, workflow_size_resume=True,
            workflow_work_model_sha256=environment["BELIEFKV_WORKFLOW_WORK_MODEL_SHA256"],
        )
    finally:
        runtime.close()


def test_oversized_short_candidate_does_not_block_a_fitting_peer(monkeypatch, tmp_path):
    runtime, adapter, _ = queued(monkeypatch, tmp_path)
    try:
        adapter.forget("active")
        adapter.policy.programs["short"].tokens = 600
        adapter._live_requests["short-request"].origin_input_ids = list(range(600))
        adapter.tick()
        assert adapter.policy.programs["long"].active
        assert not adapter.policy.programs["short"].active
    finally:
        runtime.close()


@pytest.mark.parametrize("invalid", ("stale", "epoch", "tools", "marked"))
def test_work_bucket_cannot_bypass_native_request_identity_or_executability(
    monkeypatch, tmp_path, invalid,
):
    runtime, adapter, _ = queued(monkeypatch, tmp_path)
    try:
        adapter.forget("active")
        key = adapter.latest["short"]
        if invalid == "stale":
            runtime.visible[key.request_id] = replace(key, context_epoch=1)
        elif invalid == "epoch":
            runtime.graph.contexts[key.context_id].epoch += 1
        elif invalid == "tools":
            runtime.graph.invocations["short"].active_tool_calls["t"] = "execute"
        else:
            adapter.policy.programs["short"].marked_for_pause = True
        adapter.tick()
        assert adapter.policy.programs["long"].active
        assert not adapter.policy.programs["short"].active
    finally:
        runtime.close()


def test_ranked_dispatch_formal_preflight_checks_declared_scope(tmp_path):
    environment = arm_environment({
        **settings(tmp_path), "AGENT_FREE_DISPATCH_INTERVAL_MS": "500",
    }, "full")
    report = preflight_runtime(environment, str(tmp_path / "preflight"))
    assert report["verified_configuration"]["workflow_size_resume"]
    assert report["verified_configuration"]["agent_free_dispatch_interval_ms"] == 500


def test_prepared_dispatch_candidate_preserves_workload_and_shared_serving_parameters(tmp_path):
    configured = settings(tmp_path)
    model = Path(configured["WORKFLOW_WORK_MODEL"])
    raw = json.loads(model.read_bytes())
    raw["metadata"].update({
        "training_projects": ["train"], "calibration_projects": ["calibration"],
        "prefill_token_weight": .01,
    })
    model.write_text(json.dumps(raw))
    report = tmp_path / "evaluation.json"
    report.write_text(json.dumps({
        "model_sha256": sha256_file(model), "test_projects": ["development"],
    }))
    base = ROOT / "configs/migration/predictive_decay_tick2s_no_handoff_20261030.json"
    original = json.loads(base.read_bytes())
    candidate = prepare(base, model, report, free_dispatch_interval_ms=500)
    env = candidate["common_environment"]
    for name in (
        "ROOT_COUNT", "MAX_RUNNING_REQUESTS", "SERVER_RANDOM_SEED", "SAMPLING_SEED",
        "MODEL_PATH", "CHUNKED_PREFILL_SIZE", "ENABLE_MIXED_CHUNK",
        "TOOL_PREFETCH", "LAYER_HANDOFF", "AGENT_CONTROL_INTERVAL_MS",
    ):
        assert env[name] == original["common_environment"][name]
    assert candidate["tool_timing_overlay"] == original["tool_timing_overlay"]
    assert candidate["workflow_arrivals"] == original["workflow_arrivals"]
    assert env["AGENT_FREE_DISPATCH_INTERVAL_MS"] == "500"
    assert env["BELIEFKV_AGENT_FREE_DISPATCH_INTERVAL_MS"] == "500"
    assert not candidate["comparison_contract"]["unchanged_event_grant_paths"]
    assert candidate["candidate_provenance"]["new_policy_scope"] == (
        "paused_periodic_and_bounded_free_dispatch"
    )
    assert not candidate["gpu_execution"]["registered"]
