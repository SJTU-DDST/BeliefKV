from dataclasses import asdict, replace
import random
import time
from types import SimpleNamespace as NS

import pytest

from beliefkv.control.causal_graph import InvocationState
from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.policy.paper_baselines import ThunderActingDecayWorkingSet
from beliefkv.policy.predicted_acting_pressure import PredictedActingPressureWorkingSet
from beliefkv.runtime.predicted_acting_pressure import PredictedActingPressureAdmission
from beliefkv.runtime.reference_thunder_admission import validate_reference_admission
from beliefkv.runtime.sglang_v0520_prediction import NativeToolWaitHint
from tests.test_internal_comparison import observed
from tests.test_join_blocked_handoff import bind
from tests.test_paper_baseline_runtime import request
from tests.test_reference_thunder_admission import full_runtime


def blocked_set(policy):
    for name, size in (("short", 200), ("long", 400)):
        policy.arrive(name, size, now=0.)
        policy.completed(name, size, now=0.)
    policy.arrive("waiting", 500, now=.1)
    return policy


def test_supported_long_tool_can_preserve_a_short_tool_owner():
    baseline = blocked_set(ThunderActingDecayWorkingSet(1000))
    candidate = blocked_set(PredictedActingPressureWorkingSet(
        1000, acting_preference=lambda item, now: {"supported": True}
        if item.identity == "long" else None,
    ))
    assert baseline.tick(now=5.) == candidate.tick(now=5.) == ("waiting",)
    assert not baseline.programs["short"].active
    assert candidate.programs["short"].active
    assert not candidate.programs["long"].active
    assert candidate.programs["waiting"].active
    assert candidate.remaining_capacity() == 100
    assert len(candidate.last_pressure) == 1
    assert candidate.last_pressure[0]["reference_selected"] == "short"
    assert candidate.last_pressure[0]["selected"] == "long"


@pytest.mark.parametrize("seed", (21, 43, 79, 107))
def test_missing_predictions_match_the_reference_on_all_observed_lifecycle_steps(seed):
    baseline = ThunderActingDecayWorkingSet(2000)
    candidate = PredictedActingPressureWorkingSet(
        2000, acting_preference=lambda item, now: None,
    )
    rng, now = random.Random(seed), 0.
    for _ in range(400):
        now += rng.choice((.05, .2, 1., 5.))
        identity = rng.choice(tuple("abcdef"))
        item = baseline.programs.get(identity)
        if item is None or not item.pending:
            size = rng.randrange(100, 1400)
            for policy in (baseline, candidate):
                policy.arrive(identity, size, now=now)
        elif item.active:
            for policy in (baseline, candidate):
                policy.completed(identity, item.tokens, now=now)
        if rng.random() < .4:
            assert baseline.tick(now=now) == candidate.tick(now=now)
        assert {k: asdict(v) for k, v in baseline.programs.items()} == {
            k: asdict(v) for k, v in candidate.programs.items()
        }
        assert baseline.acting_since == candidate.acting_since
        assert baseline.remaining_capacity() == candidate.remaining_capacity()


def test_no_pressure_makes_no_prediction_queries_and_reasoning_marks_release_no_capacity():
    calls = []
    policy = PredictedActingPressureWorkingSet(
        1000, acting_preference=lambda *args: calls.append(args),
    )
    policy.arrive("a", 100, now=0.)
    policy.tick(now=1.)
    assert not calls
    policy.generated("a", 1200)
    policy.tick(now=2.)
    assert not calls and policy.programs["a"].marked_for_pause
    assert policy.programs["a"].active and policy.remaining_capacity() == -300


def runtime(monkeypatch, tmp_path):
    live = full_runtime(
        monkeypatch, tmp_path, capacity=1000, free_continuation_admission=True,
        pressure_pause_policy="predicted_acting",
        tool_prefetch=False,
    )
    live.on_events((observed(1, Kind.WORKFLOW_START),))
    live._residency_pool_bytes = (98304, 0)
    return live


def tool_owner(live, identity, size, *, duration=10000.):
    adapter = live._agent_admission
    sequence = 100 + 10 * len(adapter.policy.programs)
    live.on_events((observed(
        sequence, Kind.INVOCATION_CREATE, invocation_id=identity,
        context_id=identity + "-context",
    ),))
    req = request(identity, identity, tokens=size, workflow="wf")
    req.session_id, req.session_generation = identity, 1
    live.register_visible_request(req)
    adapter.policy.programs[identity].active = True
    live.on_batch_completed(NS(reqs=[req]))
    key = bind(live, identity)
    live.on_events((observed(
        sequence + 1, Kind.TOOL_START, invocation_id=identity,
        context_id=key.context_id,
        attributes={"tool_run_id": identity + "-tool", "tool_name": "execute"},
    ),))
    now = time.monotonic() * 1000.
    hint = NativeToolWaitHint(
        key, duration, duration, duration, now, now + 60000.,
        live.predictor_sha256,
        invocation_revision_ts_ms=live.graph.invocations[identity].updated_ts_ms,
        release_cdf=((duration, 1.),),
        residual_support_detail="online_run_command",
    )
    live.tool_wait_hints[key.context_id] = hint
    return key, hint


def test_runtime_uses_actual_wait_and_live_hint_for_the_pressure_choice(monkeypatch, tmp_path):
    live = runtime(monkeypatch, tmp_path)
    try:
        adapter = live._agent_admission
        assert isinstance(adapter, PredictedActingPressureAdmission)
        key, _ = tool_owner(live, "long", 400)
        evidence = adapter._acting_preference(adapter.policy.programs["long"], time.monotonic())
        assert evidence is not None and evidence["key"]["context_id"] == key.context_id
        assert evidence["threshold_ms"] == pytest.approx(
            5000. + adapter.policy.programs["long"].tokens * 98304 / 8_000_000.,
        )
        assert adapter.status()["planned_release_grants_capacity"] is False
        assert not live.enable_tool_prefetch and not live.layer_handoff_enabled
    finally:
        live.close()


def test_actual_runtime_pressure_preserves_short_continuation_without_physical_credit(
    monkeypatch, tmp_path,
):
    clock = [100.]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    live = runtime(monkeypatch, tmp_path)
    try:
        adapter = live._agent_admission
        tool_owner(live, "short", 200, duration=600.)
        tool_owner(live, "long", 400, duration=60000.)
        live.on_events((observed(
            500, Kind.INVOCATION_CREATE, invocation_id="waiting", context_id="waiting-context",
        ),))
        live.register_visible_request(request("waiting", "waiting", tokens=500, workflow="wf"))
        clock[0] = 106.
        adapter.next_tick = 0.
        adapter.tick()
        assert adapter.policy.programs["short"].active
        assert not adapter.policy.programs["long"].active
        assert adapter.policy.programs["waiting"].active
        assert adapter.policy.remaining_capacity() == 98
        assert adapter.counts["acting_pressure_changed_victims"] == 1
        assert live.physical_ledger.pending_action_count("PREPARE_HOST") == 0
        assert live._native_cache.token_to_kv_pool_allocator.size_full == 1000
        validate_reference_admission(
            adapter.status(), free_continuation_admission=True,
            pressure_pause_policy="predicted_acting",
        )
        with pytest.raises(ValueError, match="pressure policy"):
            validate_reference_admission(adapter.status(), free_continuation_admission=True)
    finally:
        live.close()


@pytest.mark.parametrize("failure", (
    "short", "expired", "epoch", "pending", "returned", "parallel_tools",
    "protected", "unsupported_tail", "restore_too_slow", "foreign_predictor", "unknown_geometry",
))
def test_stale_short_unready_or_unsupported_prediction_keeps_the_reference_fallback(
    monkeypatch, tmp_path, failure,
):
    live = runtime(monkeypatch, tmp_path)
    try:
        adapter = live._agent_admission
        key, hint = tool_owner(live, "long", 400)
        item = adapter.policy.programs["long"]
        if failure == "short":
            live.tool_wait_hints[key.context_id] = replace(hint, release_cdf=((100., 1.),))
        elif failure == "expired":
            live.tool_wait_hints[key.context_id] = replace(hint, expires_monotonic_ms=0.)
        elif failure == "epoch":
            live.context_sessions[key.context_id] = replace(key, context_epoch=1)
        elif failure == "pending":
            item.pending = True
        elif failure == "returned":
            live.graph.invocations["long"].state = InvocationState.READY
        elif failure == "parallel_tools":
            live.graph.invocations["long"].active_tool_calls["extra"] = {}
        elif failure == "protected":
            live._prefetch_service_leases["protection"] = NS(key=key)
        elif failure == "unsupported_tail":
            live._online_tool_timing = NS(prefetch_supported=lambda *a, **kw: False)
        elif failure == "foreign_predictor":
            live.tool_wait_hints[key.context_id] = replace(hint, predictor_sha256="foreign")
        elif failure == "unknown_geometry":
            live._residency_pool_bytes = None
        else:
            live._residency_pool_bytes = (98304, 0)
            monkeypatch.setattr(live, "_estimate_h2d_service", lambda *a, **kw: NS(
                submit_to_ack_p90_ms=20000., enqueue_to_submit_p90_ms=0.,
            ))
        assert adapter._acting_preference(item, time.monotonic()) is None
    finally:
        live._online_tool_timing = None
        live._prefetch_service_leases.clear()
        live.close()


@pytest.mark.parametrize("mode", ("native_matched", "scheduler_only", "kv_only"))
def test_pressure_prediction_does_not_leak_into_controls(mode):
    settings = arm_environment({
        "AGENT_ADMISSION": "thunderagent_reference",
        "AGENT_FREE_CONTINUATION_ADMISSION": "1",
        "AGENT_PRESSURE_PAUSE_POLICY": "predicted_acting",
    }, mode)
    assert settings["BELIEFKV_AGENT_PRESSURE_PAUSE_POLICY"] == "reference"


@pytest.mark.parametrize("other", (
    {"AGENT_ADMISSION": "thunderagent_retained"},
    {"AGENT_FREE_CONTINUATION_ADMISSION": "0"},
    {"WORKFLOW_SIZE_RESUME": "1"},
))
def test_candidate_rejects_unsupported_compositions(other):
    with pytest.raises(ValueError):
        arm_environment({
            "AGENT_ADMISSION": "thunderagent_reference",
            "AGENT_FREE_CONTINUATION_ADMISSION": "1",
            "AGENT_PRESSURE_PAUSE_POLICY": "predicted_acting", **other,
        }, "full")
