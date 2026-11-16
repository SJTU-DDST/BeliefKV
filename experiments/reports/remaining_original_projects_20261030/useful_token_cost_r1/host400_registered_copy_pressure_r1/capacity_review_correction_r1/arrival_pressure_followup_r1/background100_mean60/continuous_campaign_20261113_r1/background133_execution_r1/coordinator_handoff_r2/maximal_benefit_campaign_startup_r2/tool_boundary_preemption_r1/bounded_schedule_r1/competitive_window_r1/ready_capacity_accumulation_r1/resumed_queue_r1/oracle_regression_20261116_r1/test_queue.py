"""CPU checks for predecessor ownership and the actual two-percent acceptance call."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("tested_strict_queue", HERE / "strict_oracle_queue.py")
queue = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(queue)


def test_acceptance_overrides_old_one_percent_threshold():
    calls = []
    module = SimpleNamespace(evaluate_pair_cohorts=lambda *args, **kwargs: calls.append(kwargs))
    queue.apply_acceptance(module)
    module.evaluate_pair_cohorts({}, {}, {}, minimum_jct_gain=.01)
    assert calls == [{"minimum_jct_gain": .02}]


@pytest.mark.parametrize("status", (
    "waiting_resources_control", "waiting_fixed_18_control", "complete_pressure",
))
def test_unfinished_materialization_never_releases_oracle(tmp_path, status):
    owner = {"pid": 123, "start_ticks": "42"}
    (tmp_path / "state.json").write_text(json.dumps({"status": status, "process": owner}))
    assert not queue.predecessor_ready({
        "materialization_campaign": str(tmp_path), "materialization_owner": owner,
    }, alive=lambda _: True)


def test_owner_change_rejected_even_with_terminal_status(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({
        "status": "complete_four_matched_arms", "process": {"pid": 123, "start_ticks": "43"},
    }))
    with pytest.raises(ValueError, match="owner changed"):
        queue.predecessor_ready({
            "materialization_campaign": str(tmp_path),
            "materialization_owner": {"pid": 123, "start_ticks": "42"},
        }, alive=lambda _: False)


def test_preceding_failure_is_not_an_automatic_gpu_permission(tmp_path):
    owner = {"pid": 123, "start_ticks": "42"}
    (tmp_path / "state.json").write_text(json.dumps({
        "status": "stopped_preserve_materialization_campaign", "process": owner,
    }))
    with pytest.raises(RuntimeError, match="investigate"):
        queue.predecessor_ready({
            "materialization_campaign": str(tmp_path), "materialization_owner": owner,
        }, alive=lambda _: False)


def test_final_label_requires_actual_owner_exit(tmp_path):
    owner = {"pid": 123, "start_ticks": "42"}
    (tmp_path / "state.json").write_text(json.dumps({
        "status": "complete_four_matched_arms", "process": owner,
    }))
    assert not queue.predecessor_ready({
        "materialization_campaign": str(tmp_path), "materialization_owner": owner,
    }, alive=lambda _: True)


def test_mutable_queue_state_retains_second_heartbeat(tmp_path):
    path = tmp_path / "state.json"
    queue.write(path, {"polls": 1})
    queue.write(path, {"polls": 2})
    assert queue.load(path) == {"polls": 2}


def test_actual_fresh_preparation_retains_cpu_qualified_serving_inputs():
    result = queue.prepared_serving_parity()
    assert set(result) == {"oracle", "learned"}
    assert all(row["only_configuration_provider_path_changed"] for row in result.values())
