from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest

from experiments.reports.workflow_mix_20261029 import review_burst as review
from experiments.reports.workflow_mix_20261029 import prepare_burst_oracle as prepare


def fixture():
    plan = {
        "run_root": "/test/new-mix", "order": ["full"], "workflow_ids": ["a", "b"],
        "source_plan": {"workflows": 2, "requests": 3, "prompt_tokens": 21, "output_tokens": 9},
    }
    work = {str(index): (7, 3, "prompt", "output", {}) for index in range(3)}
    cleanup = {"remaining_processes": [], "finished_at": "done", "identities": [{"pid": 123}]}
    drain = {"previous_zero_snapshot_ts_ms": 1., "drained_status": {"snapshot_ts_ms": 2.}}
    return plan, work, cleanup, drain


def run_complete(items, *, ready=True, alive=False):
    plan, work, cleanup, drain = items

    def load(path):
        name = Path(path).name
        if name == "registration.json":
            return plan
        if name == "owned_process_cleanup.json":
            return cleanup
        if name == "telemetry_drain.json":
            return drain
        raise AssertionError(path)

    with (
        patch.object(review.causal, "load", side_effect=load),
        patch.object(review.causal, "predecessor_ready", return_value=ready),
        patch.object(review.causal, "predecessor_work", return_value=work) as predecessor,
        patch.object(review.causal.reservation, "alive", return_value=alive),
    ):
        result = review.complete_work(Path("/test/registration.json"))
        if not ready:
            predecessor.assert_not_called()
        return result


def test_completion_binds_new_population_instead_of_historical_fixed_counts():
    data = fixture()
    assert run_complete(data) == data[1]
    assert run_complete(data, ready=False) is None


@pytest.mark.parametrize("change", ("requests", "prompt_tokens", "output_tokens", "workflows"))
def test_completion_refuses_incomplete_or_foreign_work(change):
    data = deepcopy(fixture())
    data[0]["source_plan"][change] += 1
    with pytest.raises(ValueError):
        run_complete(data)


@pytest.mark.parametrize("change", ("live_owner", "missing_owner", "remaining", "unfinished", "stale_drain"))
def test_complete_status_does_not_replace_actual_exit_and_fresh_drain(change):
    data = deepcopy(fixture())
    if change == "missing_owner":
        data[2]["identities"] = []
    elif change == "remaining":
        data[2]["remaining_processes"] = [{"pid": 123}]
    elif change == "unfinished":
        data[2]["finished_at"] = None
    elif change == "stale_drain":
        data[3]["drained_status"]["snapshot_ts_ms"] = 1.
    with pytest.raises(ValueError):
        run_complete(data, alive=change == "live_owner")


def test_model_cost_uses_complete_request_weighted_decode_time():
    assert review.model_cost({"request_steps": 10, "request_step_ms": 230.}) == .023


@pytest.mark.parametrize("steps,milliseconds", (
    (0, 100.), (True, 100.), (10., 100.), (10, 0.), (10, True),
    (10, float("nan")), (10, float("inf")), (10, -1.),
))
def test_model_cost_rejects_missing_or_invalid_service(steps, milliseconds):
    with pytest.raises(ValueError):
        review.model_cost({"request_steps": steps, "request_step_ms": milliseconds})


def control_fixture():
    settings = {name: "0" for name in (
        "WORKFLOW_SIZE_RESUME", "BELIEFKV_WORKFLOW_SIZE_RESUME",
        "AGENT_FREE_DISPATCH_INTERVAL_MS", "BELIEFKV_AGENT_FREE_DISPATCH_INTERVAL_MS",
        "TOOL_PREFETCH", "EXECUTION_FRONTIER_RESTORE", "LAYER_HANDOFF", "ENABLE_MIXED_CHUNK",
    )}
    settings["AGENT_CONTROL_INTERVAL_MS"] = "2000"
    configuration = {"order": ["full"], "common_environment": settings}
    plan = {
        "order": ["full"], "source_plan": {"requests": 3}, "token_source_sha256": "tokens",
        "workflow_arrivals": {"mode": "simultaneous"}, "tool_timing_overlay": {"sha256": "tools"},
    }
    evidence = {
        "registration": "/test/registration.json", "registration_sha256": "registration",
        "complete_work_and_actual_exit_verified": True,
        "match_contract": {
            "source_plan": {"requests": 3}, "token_source_sha256": "tokens",
            "workflow_arrivals": {"mode": "simultaneous"}, "tool_timing_overlay_sha256": "tools",
        },
        "metrics": {"decode": {"request_steps": 10, "request_step_ms": 230.}},
        "model_cost_calibration": {"model_seconds_per_work_token": .023},
    }
    return configuration, plan, evidence


def test_oracle_calibration_uses_matching_new_control_without_enabling_dispatch():
    with patch.object(prepare, "sha256_file", return_value="registration"):
        assert prepare.validate_control(*control_fixture()) == .023


@pytest.mark.parametrize("change", ("work", "tokens", "arrivals", "tools", "cost", "dispatch", "cadence"))
def test_oracle_rejects_stale_calibration_or_policy_changes(change):
    configuration, plan, evidence = control_fixture()
    if change in ("work", "tokens", "arrivals", "tools"):
        key = {
            "work": "source_plan", "tokens": "token_source_sha256",
            "arrivals": "workflow_arrivals", "tools": "tool_timing_overlay_sha256",
        }[change]
        evidence["match_contract"][key] = "changed"
    elif change == "cost":
        evidence["model_cost_calibration"]["model_seconds_per_work_token"] = .024
    elif change == "dispatch":
        configuration["common_environment"]["AGENT_FREE_DISPATCH_INTERVAL_MS"] = "500"
    else:
        configuration["common_environment"]["AGENT_CONTROL_INTERVAL_MS"] = "500"
    with patch.object(prepare, "sha256_file", return_value="registration"):
        with pytest.raises(ValueError):
            prepare.validate_control(configuration, plan, evidence)
