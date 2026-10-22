from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.experiments import full_regression_recovery as policy
from beliefkv.experiments.internal_comparison import arm_environment
from scripts import run_full_regression_recovery as operator


def reference():
    spec = json.loads((
        Path(__file__).resolve().parents[1]
        / "configs/migration/next_predictive_baselines_20261022.json"
    ).read_bytes())
    return {
        "execution_checkout": "/old/frozen",
        "environment": arm_environment({
            **spec["common_environment"],
            "WORKLOAD_MANIFEST": "/old/frozen/configs/workloads/source.json",
            "TOOL_CONTINUITY": "1",
        }, "full"),
    }


def test_repair_preserves_policy_except_unproven_continuity_and_frontier():
    original = reference()
    before = deepcopy(original)
    result = policy.environment(original, Path("/new/frozen"), 18534, fallback=False)
    assert result["TOOL_CONTINUITY"] == result["BELIEFKV_ENABLE_TOOL_CONTINUITY"] == "0"
    assert result["EXECUTION_FRONTIER_RESTORE"] == result["BELIEFKV_ENABLE_FRONTIER_RESTORE"] == "0"
    assert result["PREFILL_SCHEDULING_POLICY"] == "join_priority"
    assert result["TOOL_RESTORE_MODE"] == "execution"
    assert result["RESTORE_TIERS"] == "dependency"
    assert result["PREFETCH_RESIDENCY_PROTECTION"] == "0"
    assert result["JOIN_PREFETCH_TRIGGER"] == "observed_eos"
    assert result["BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH"] == "1"
    assert result["BELIEFKV_ENABLE_PREPARE_HOST"] == "1"
    assert result["WORKLOAD_MANIFEST"] == "/new/frozen/configs/workloads/source.json"
    assert result["BELIEFKV_SUBAGENT_RETENTION"] == "off"
    assert original == before


def test_v108_fallback_restores_effective_policy_without_undoing_engineering_or_seed():
    result = policy.environment(reference(), Path("/new/frozen"), 18536, fallback=True)
    expected = {
        "PREFILL_SCHEDULING_POLICY": "workflow",
        "BELIEFKV_ENABLE_RESIDENT_FIRST": "1",
        "BELIEFKV_ENABLE_FINAL_STAGE_PRIORITY": "1",
        "TOOL_RESTORE_MODE": "speculative", "IDLE_PREPARE_POLICY": "all",
        "RESTORE_TIERS": "complete", "PREFETCH_RESIDENCY_PROTECTION": "1",
        "JOIN_PREFETCH_TRIGGER": "selective_report_or_eos",
        "SERVER_RANDOM_SEED": "21", "SAMPLING_SEED": "21",
        "ROOT_COUNT": "36", "MAX_RUNNING_REQUESTS": "8",
        "HICACHE_SIZE_GB": "200", "HOST_SPLIT": "80:20",
        "MODEL_CONTEXT_TOKENS": "262144", "CONTEXT_WINDOW_TOKENS": "262144",
        "FANOUT_PROFILE": "native_in_graph_nested_2to4",
        "BELIEFKV_CHAT_TOKEN_BLOCK_CACHE": "1",
        "BELIEFKV_ENABLE_TOOL_CONTINUITY": "0",
        "BELIEFKV_ENABLE_FRONTIER_RESTORE": "0",
        "BELIEFKV_SUBAGENT_RETENTION": "off",
    }
    assert {name: result[name] for name in expected} == expected


@pytest.mark.parametrize("fallback", (False, True))
def test_recovery_profiles_reach_the_effective_runtime(monkeypatch, tmp_path, fallback):
    from beliefkv.experiments.internal_comparison import validate_runtime
    from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime

    environment = policy.environment(reference(), Path("/new/frozen"), 18534, fallback=fallback)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    for name in ("ONLINE_TOOL_TIMING", "BORROWABLE_PARENT_HEADROOM"):
        monkeypatch.setenv("BELIEFKV_" + name, environment[name])
    runtime = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "runtime.sock"))
    runtime._opportunity_writer = Mock()
    try:
        runtime.attach_native_cache(NS(supports_beliefkv_restore_tiers=lambda: True))
        runtime._record_runtime_state()
        state = runtime._opportunity_writer.record.call_args.args[0]
        validate_runtime(
            "full", state, tool_continuity=False, execution_frontier_restore=False,
            scheduling_policy="workflow" if fallback else "join_priority",
            tool_restore_mode="speculative" if fallback else "execution",
            idle_prepare_policy="all" if fallback else "cold",
            restore_tiers="complete" if fallback else "dependency",
            residency_protection=fallback, join_last_child_priority=False,
        )
        assert state["resident_first"] is fallback
        assert state["tool_prefetch"] is fallback
        assert state["final_stage_prefetch"] is True
    finally:
        runtime._opportunity_writer = None
        runtime.close()


@pytest.mark.parametrize("field", (
    "ROOT_COUNT", "MAX_RUNNING_REQUESTS", "SERVER_RANDOM_SEED",
    "SAMPLING_SEED", "FANOUT_PROFILE", "MODEL_CONTEXT_TOKENS", "HOST_SPLIT",
))
def test_workload_or_resource_changes_cannot_silently_enter_recovery(field):
    original = reference()
    original["environment"][field] = "different"
    with pytest.raises(ValueError, match="workload"):
        policy.environment(original, Path("/new"), 18534, fallback=False)


WATCHER = {"pid": 123, "start_ticks": "456"}
DRIVER = {"pid": 234, "start_ticks": "567"}


def preceding_state():
    return {
        "status": "waiting_for_idle_resources", "watcher": dict(WATCHER),
        "driver": dict(DRIVER), "order": ["schedule_only", "kv_only", "no_tool_continuity"],
        "completed_arms": ["schedule_only", "kv_only"],
        "original_exit_codes": {"schedule_only": 1, "kv_only": 1},
        "arm_outcomes": {"kv_only": {"original_exit_code": 1}},
    }


def handover_plan():
    return {
        "predecessor_watcher": dict(WATCHER),
        "preserve_arms": ["schedule_only", "kv_only"],
    }


@pytest.mark.parametrize("status", (
    "running_kv_only", "postprocessing_kv_only", "running_no_tool_continuity",
))
def test_handover_never_interrupts_collection_or_postprocessing(status):
    state = preceding_state()
    state["status"] = status
    assert not policy.handover_ready(handover_plan(), state, driver_live=False)


def test_handover_requires_preserved_results_and_no_active_driver():
    state = preceding_state()
    assert policy.handover_ready(handover_plan(), state, driver_live=False)
    assert not policy.handover_ready(handover_plan(), state, driver_live=True)
    state["completed_arms"].pop()
    assert not policy.handover_ready(handover_plan(), state, driver_live=False)


@pytest.mark.parametrize("fault", ("pid_reused", "failure", "cancelled"))
def test_handover_preserves_failed_or_changed_predecessor(fault):
    state = preceding_state()
    if fault == "pid_reused":
        state["watcher"]["start_ticks"] = "other"
    elif fault == "failure":
        state["error"] = "unexpected failure"
    else:
        state["status"] = "cancelled_hold_changed"
    with pytest.raises(ValueError):
        policy.handover_ready(handover_plan(), state, driver_live=False)


@pytest.mark.parametrize("race", (None, "new_driver", "child", "not_stopped"))
def test_retire_rechecks_frozen_supervisor_and_never_signals_an_active_driver(
    tmp_path, monkeypatch, race,
):
    path = tmp_path / "predecessor.state.json"
    original = preceding_state()
    path.write_text(json.dumps(original))
    plan = {**handover_plan(), "predecessor_state": str(path), "registration": "/new/plan.json"}
    active = {WATCHER["pid"]}
    monkeypatch.setattr(
        operator.reservation, "alive", lambda identity: identity["pid"] in active,
    )
    monkeypatch.setattr(operator.reservation, "process", lambda _: {
        **WATCHER, "state": "R" if race == "not_stopped" else "T",
    })
    monkeypatch.setattr(operator, "live_children", lambda _: [DRIVER] if race == "child" else [])
    monkeypatch.setattr(operator, "verify_preserved", Mock())
    signals = []

    def send(identity, sig):
        assert identity == WATCHER
        signals.append(sig)
        if sig == operator.signal.SIGSTOP and race == "new_driver":
            changed = deepcopy(original)
            changed["status"] = "running_no_tool_continuity"
            path.write_text(json.dumps(changed))
            active.add(DRIVER["pid"])
        if sig == operator.signal.SIGCONT and operator.signal.SIGTERM in signals:
            active.remove(WATCHER["pid"])

    monkeypatch.setattr(operator, "signal_owned", send)
    assert operator.retire_at_boundary(plan) is (race is None)
    assert signals[0] == operator.signal.SIGSTOP
    assert signals[-1] == operator.signal.SIGCONT
    after = json.loads(path.read_bytes())
    assert after["original_exit_codes"] == original["original_exit_codes"]
    assert after["arm_outcomes"] == original["arm_outcomes"]
    if race is None:
        assert operator.signal.SIGTERM in signals
        assert after["status"] == "superseded_after_completed_arm"
        assert after["skipped_pending_arms"] == ["no_tool_continuity"]
    else:
        assert operator.signal.SIGTERM not in signals


def gate_fixture():
    names = ("repaired_full", "native_matched", "join_priority_only")
    comparison = {"runs": {
        name: {
            "jct_mean_seconds": jct, "output_tokens_per_second": throughput,
            "output_tokens": 1000000, "input_tokens": 100000000,
            "llm_requests": 4000, "tool_calls": 5000,
        } for name, jct, throughput in zip(names, (1000., 1100., 1050.), (600., 500., 510.))
    }}
    busy = {"runs": {
        name: {"windows": [{
            "start_seconds": 600., "end_seconds": 1800.,
            "service_window_observed_complete": True,
            "decode_output_tokens_per_second": throughput,
        }]} for name, throughput in zip(names, (600., 500., 510.))
    }}
    summaries = {
        name: {
            "native_agent_jct_eligible_workflows": 35,
            "workflows": [{
                "instance_id": str(i), "outcome": "completed", "measurement_valid": True,
                "system_jct_eligible": True, "native_agent_jct_eligible": i != 35,
                "duration_seconds": comparison["runs"][name]["jct_mean_seconds"],
            } for i in range(36)],
        } for name in names
    }
    return comparison, busy, summaries


def test_gate_requires_jct_and_throughput_without_declaring_causal_acceptance():
    report = policy.performance_gate(*gate_fixture(), "repaired_full")
    assert report["development_gate_passed"]
    assert not report["fallback_required"]
    assert report["common_eligible_workflows"] == 35
    assert report["performance_accepted"] is False


@pytest.mark.parametrize("regression", (
    "raw_jct", "eligible_jct", "total_throughput", "decode", "guards",
))
def test_fallback_is_required_when_any_performance_condition_fails(regression):
    comparison, busy, summaries = gate_fixture()
    if regression == "raw_jct":
        comparison["runs"]["repaired_full"]["jct_mean_seconds"] = 1200.
    elif regression == "eligible_jct":
        summaries["repaired_full"]["workflows"][0]["duration_seconds"] = 10000.
    elif regression == "total_throughput":
        comparison["runs"]["repaired_full"]["output_tokens_per_second"] = 400.
    elif regression == "decode":
        busy["runs"]["repaired_full"]["windows"][0]["decode_output_tokens_per_second"] = 400.
    else:
        summaries["repaired_full"]["native_agent_jct_eligible_workflows"] = 34
        summaries["repaired_full"]["workflows"][34]["native_agent_jct_eligible"] = False
    report = policy.performance_gate(comparison, busy, summaries, "repaired_full")
    assert report["fallback_required"]
    assert report["performance_accepted"] is False


@pytest.mark.parametrize("fault", ("missing_task", "failed_task", "incomplete_window", "nan"))
def test_invalid_measurements_stop_evaluation_instead_of_triggering_a_success(fault):
    comparison, busy, summaries = gate_fixture()
    if fault == "missing_task":
        summaries["repaired_full"]["workflows"].pop()
    elif fault == "failed_task":
        summaries["repaired_full"]["workflows"][0]["outcome"] = "error"
    elif fault == "incomplete_window":
        busy["runs"]["repaired_full"]["windows"][0]["service_window_observed_complete"] = False
    else:
        comparison["runs"]["repaired_full"]["jct_mean_seconds"] = float("nan")
    with pytest.raises(ValueError):
        policy.performance_gate(comparison, busy, summaries, "repaired_full")


@pytest.mark.parametrize("fallback", (False, True))
def test_supervisor_runs_fallback_only_after_complete_repair_evaluation(tmp_path, monkeypatch, fallback):
    registration = tmp_path / "recovery.json"
    children = {"repaired_full": "/repair.json", "v108_policy": "/fallback.json"}
    registration.write_text(json.dumps({"children": children}))
    monkeypatch.setattr(operator.reservation, "process", lambda _: WATCHER)
    monkeypatch.setattr(operator.fresh, "check_hold", Mock())
    monkeypatch.setattr(operator.fresh, "verify", Mock())
    monkeypatch.setattr(operator, "retire_at_boundary", Mock(return_value=True))
    order = []
    monkeypatch.setattr(operator.full, "watch", lambda child: order.append(("run", str(child))))

    def evaluate(child):
        order.append(("evaluate", str(child)))
        return {"fallback_required": fallback, "performance_accepted": False}

    monkeypatch.setattr(operator, "evaluate", evaluate)
    operator.watch(registration)
    expected = [("run", "/repair.json"), ("evaluate", "/repair.json")]
    if fallback:
        expected += [("run", "/fallback.json"), ("evaluate", "/fallback.json")]
    assert order == expected
    state = json.loads(registration.with_suffix(".state.json").read_bytes())
    assert state["status"] == "collected_performance_review_pending"
    assert state["performance_accepted"] is False
    assert state["final_policy"] == ("v108_policy" if fallback else "repaired_full")


def test_failed_collection_does_not_run_fallback_or_hide_failure(tmp_path, monkeypatch):
    path = tmp_path / "repair.json"
    path.write_text("{}")
    path.with_suffix(".state.json").write_text(json.dumps({
        "status": "stopped_for_investigation", "error": "driver failed",
    }))
    with pytest.raises(ValueError, match="driver failed"):
        operator.evaluate(path)


@pytest.mark.parametrize("fault", (None, "outcome_changed", "timeline_missing"))
def test_retirement_requires_completed_summaries_and_preserved_postprocessing(
    tmp_path, monkeypatch, fault,
):
    predecessor = tmp_path / "matrix.json"
    predecessor.write_text(json.dumps({"run_root": str(tmp_path / "run")}))
    run = tmp_path / "run"
    run.mkdir()
    (run / "module_comparison.json").write_text("{}")
    (run / "timelines").mkdir()
    state = preceding_state()
    for label in ("schedule_only", "kv_only"):
        client = run / label / "client_36"
        client.mkdir(parents=True)
        (client / "summary.json").write_text(json.dumps({"label": label}))
        (run / "timelines" / f"{label}.json.gz").write_bytes(b"timeline")
        state["arm_outcomes"][label] = {"label": label, "original_exit_code": 1}
    if fault == "outcome_changed":
        state["arm_outcomes"]["kv_only"]["original_exit_code"] = 0
    elif fault == "timeline_missing":
        (run / "timelines/kv_only.json.gz").unlink()
    monkeypatch.setattr(
        operator.fresh, "completed_arm",
        lambda summary, exit_code, roots: {"label": summary["label"], "original_exit_code": exit_code},
    )
    plan = {**handover_plan(), "predecessor_registration": str(predecessor)}
    if fault:
        with pytest.raises(ValueError):
            operator.verify_preserved(plan, state)
    else:
        operator.verify_preserved(plan, state)
