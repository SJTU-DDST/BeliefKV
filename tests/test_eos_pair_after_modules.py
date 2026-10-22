import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts import run_eos_pair_after_modules as pair


WATCHER = {"pid": 123, "start_ticks": "456"}
DRIVER = {"pid": 234, "start_ticks": "567"}
ORDER = ["one", "two"]


def state(status="running_two"):
    return {
        "status": status, "watcher": dict(WATCHER), "driver": dict(DRIVER),
        "order": list(ORDER), "completed_arms": list(ORDER),
    }


def plan():
    return {"reference_watcher": dict(WATCHER), "reference_order": list(ORDER)}


def test_pair_waits_for_the_actual_live_matrix_and_its_driver_to_exit():
    assert not pair.reference_ready(plan(), state(), alive=lambda _: True)
    completed = state("collected_performance_review_pending")
    assert not pair.reference_ready(plan(), completed, alive=lambda _: True)
    assert pair.reference_ready(plan(), completed, alive=lambda _: False)
    with pytest.raises(ValueError, match="exited"):
        pair.reference_ready(plan(), state(), alive=lambda _: False)


@pytest.mark.parametrize("fault", ("pid_reused", "order", "missing_arm", "error"))
def test_pair_never_treats_failed_or_changed_reference_as_complete(fault):
    row = state("collected_performance_review_pending")
    if fault == "pid_reused":
        row["watcher"]["start_ticks"] = "other"
    elif fault == "order":
        row["order"].reverse()
    elif fault == "missing_arm":
        row["completed_arms"].pop()
    else:
        row["error"] = "failed telemetry"
    with pytest.raises(ValueError):
        pair.reference_ready(plan(), row, alive=lambda _: False)


def test_pair_environment_uses_same_workload_capacity_seed_and_frontend_for_two_arms():
    spec = pair.load(pair.ROOT / "configs/migration/v108_eos_reference_policy_20261022.json")
    reference = {
        "engine_checkout": "/engine",
        "arms": {"no_tool_continuity": {"environment": {}}},
    }
    arms = pair.environments(reference, spec, Path("/frozen"), 18538)
    assert list(arms) == list(pair.ORDER)
    assert not {"no_tool_continuity", "no_join_priority", "complete_restore"} & arms.keys()
    for name in (
        "ROOT_COUNT", "MAX_RUNNING_REQUESTS", "SAMPLING_SEED", "SERVER_RANDOM_SEED",
        "PYTHONHASHSEED", "WORKLOAD_MANIFEST", "MODEL_CONTEXT_TOKENS",
        "HOST_SPLIT", "HICACHE_SIZE_GB", "NATIVE_CHAT_TOKEN_BLOCK_CACHE",
    ):
        assert arms["full"][name] == arms["native_matched"][name]
    assert arms["full"]["BELIEFKV_ENABLE_RESIDENT_FIRST"] == "1"
    assert arms["full"]["BELIEFKV_ENABLE_FINAL_STAGE_PRIORITY"] == "1"
    assert arms["full"]["TOOL_RESTORE_MODE"] == "speculative"
    assert arms["full"]["JOIN_PREFETCH_TRIGGER"] == "observed_eos"
    assert arms["full"]["BELIEFKV_ENABLE_TOOL_CONTINUITY"] == "0"
    assert arms["native_matched"]["NATIVE_POLICY_BASELINE"] == "1"
    assert arms["no_join_prefetch"]["BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH"] == "0"
    assert arms["no_join_prefetch"]["BELIEFKV_ENABLE_TOOL_PREFETCH"] == "1"
    assert arms["no_join_prefetch"]["AB_MODE"] == "predictive_h2d"
    assert arms["no_background_prepare"]["BELIEFKV_ENABLE_BACKGROUND_PREPARE"] == "0"
    assert arms["no_tool_duration_model"]["ENABLE_TOOL_TIMING"] == "0"
    assert arms["no_child_reclaim"]["BELIEFKV_ENABLE_TERMINAL_CHILD_RECLAIM"] == "0"


def test_explicit_user_cancellation_preserves_completed_evidence_and_exit_code():
    item = state("stopped_for_investigation")
    item.update(
        current_arm="no_tool_continuity", completed_arms=["schedule_only", "kv_only"],
        original_exit_codes={"no_tool_continuity": 143}, error="missing cancelled summary",
    )
    settings = {**plan(), "reference_cancel_record": "/authorized-cancellation.json"}
    assert pair.reference_ready(settings, item, alive=lambda _: False)
    assert not pair.reference_ready(settings, item, alive=lambda _: True)
    item["original_exit_codes"]["no_tool_continuity"] = 1
    with pytest.raises(ValueError):
        pair.reference_ready(settings, item, alive=lambda _: False)


@pytest.mark.parametrize("fault", (
    None, "sampling", "hash", "cuda", "missing", "server", "missing_server", "numpy",
))
def test_observed_seed_receipts_are_required_and_drift_fails(tmp_path, fault):
    client = tmp_path / "client_36"
    client.mkdir()
    receipt = {
        "config": {"sampling_seed": 21},
        "randomness": {
            "seed": 21, "configured": True, "python_hash_seed": "21",
            "cuda_initialized": False,
            "python_random": 21, "numpy_legacy_random": 21, "torch_cpu": 21,
        },
    }
    server = tmp_path / "server"
    server.mkdir()
    if fault != "missing_server":
        (server / "launch_configuration.json").write_text(json.dumps({
            "server_random_seed": 22 if fault == "server" else 21,
            "python_hash_seed": "21",
        }))
    if fault == "sampling":
        receipt["config"]["sampling_seed"] = 22
    elif fault == "hash":
        receipt["randomness"]["python_hash_seed"] = None
    elif fault == "cuda":
        receipt["randomness"]["cuda_initialized"] = True
    elif fault == "numpy":
        receipt["randomness"]["numpy_legacy_random"] = None
    if fault != "missing":
        (client / "manifest.json").write_text(json.dumps(receipt))
    if fault:
        with pytest.raises(ValueError):
            pair.verify_seeds(tmp_path, final=True)
    else:
        pair.verify_seeds(tmp_path, final=True)


@pytest.mark.parametrize("port", (-1, 0, 65536))
def test_registration_rejects_invalid_port_before_any_freeze(port):
    with pytest.raises(ValueError, match="port"):
        pair.register(type("Arguments", (), {"port": port})())


def test_removed_module_activity_is_checked_in_addition_to_runtime_flags(tmp_path, monkeypatch):
    monkeypatch.setattr(pair.fresh, "observe", Mock())
    monkeypatch.setattr(pair, "verify_seeds", Mock())
    monkeypatch.setattr(pair.qualified, "last_runtime_state", lambda _: {
        "counts": {"idle_full_prepare_native_calls": 1},
    })
    settings = {
        "run_root": str(tmp_path),
        "arms": {"no_background_prepare": {"BELIEFKV_INTERNAL_COMPARISON_MODE": "full"}},
    }
    with pytest.raises(ValueError, match="disabled module"):
        pair.observe(settings, "no_background_prepare")
    pair.fresh.observe.assert_called_once_with(
        "full", tmp_path / "no_background_prepare", final=False,
        runtime_overrides={"background_prepare": False},
    )


@pytest.mark.parametrize("fail_observation", (False, True))
def test_selected_queue_collects_in_order_and_stops_on_bad_runtime_evidence(
    tmp_path, monkeypatch, fail_observation,
):
    run = tmp_path / "runs"
    previous = tmp_path / "previous.state.json"
    previous.write_text("{}")
    spec = pair.load(pair.ROOT / "configs/migration/v108_eos_reference_policy_20261022.json")
    settings = {
        "run_root": str(run), "reference_state": str(previous), "port": 18538,
        "order": list(pair.ORDER),
        "arms": pair.environments(
            {"engine_checkout": "/engine",
             "arms": {"no_tool_continuity": {"environment": {}}}},
            spec, tmp_path / "frozen", 18538,
        ),
    }
    registration = tmp_path / "next.registration.json"
    registration.write_text(json.dumps(settings))
    monkeypatch.setattr(pair.reservation, "process", Mock(return_value=WATCHER))
    monkeypatch.setattr(pair.fresh, "check_hold", Mock())
    monkeypatch.setattr(pair.fresh, "verify", Mock())
    monkeypatch.setattr(pair, "reference_ready", Mock(return_value=True))
    monkeypatch.setattr(pair, "verify_completed_reference", Mock(return_value={}))
    monkeypatch.setattr(pair.modules.full, "verify_candidate", Mock(return_value="revision"))
    monkeypatch.setattr(pair.modules, "wait_resources", Mock())
    monkeypatch.setattr(pair.qualified, "resources", Mock(return_value={"ready": True}))
    monkeypatch.setattr(pair.qualified, "machine_helper", Mock())
    observed = Mock(side_effect=ValueError("bad runtime") if fail_observation else None)
    monkeypatch.setattr(pair, "observe", observed)
    stopped = Mock()
    monkeypatch.setattr(pair.fresh, "stop_arm", stopped)
    monkeypatch.setattr(pair.fresh, "completed_arm", Mock(return_value={"status": "completed"}))
    processed = Mock()
    monkeypatch.setattr(pair.comparison, "postprocess", processed)

    def command(arguments, **kwargs):
        if arguments[-1] == str(run / "pair_comparison.json"):
            work = {"input_tokens": 100, "output_tokens": 10, "llm_requests": 1}
            (run / "pair_comparison.json").write_text(json.dumps({
                "runs": {"full": work, "native_matched": work},
            }))

    monkeypatch.setattr(pair.subprocess, "run", Mock(side_effect=command))

    def launch(arguments, **kwargs):
        client = Path(kwargs["env"]["RUN_ROOT"]) / "client_36"
        client.mkdir(parents=True)
        (client / "summary.json").write_text("{}")
        return Mock(pid=234, poll=Mock(return_value=0), wait=Mock(return_value=0))

    launched = Mock(side_effect=launch)
    monkeypatch.setattr(pair.subprocess, "Popen", launched)
    pair.watch(registration)
    result = pair.load(registration.with_suffix(".state.json"))
    if fail_observation:
        assert result["status"] == "stopped_for_investigation"
        assert result["original_exit_codes"] == {"no_join_prefetch": 0}
        assert result["completed_arms"] == []
        assert launched.call_count == 1
        processed.assert_not_called()
        assert "bad runtime" in result["error"]
    else:
        assert result["status"] == "collected_performance_review_pending"
        assert result["completed_arms"] == list(pair.ORDER)
        assert list(result["original_exit_codes"]) == list(pair.ORDER)
        assert launched.call_count == 6
        for label, call in zip(pair.ORDER, processed.call_args_list, strict=True):
            assert call.args[1] == label
            assert call.kwargs["runtime_mode"] == (
                "native_matched" if label == "native_matched" else "full"
            )
        for label, call in zip(pair.ORDER, launched.call_args_list, strict=True):
            environment = call.kwargs["env"]
            assert environment["RUN_ROOT"] == str(run / label)
            assert environment["PYTHONHASHSEED"] == "21"
        assert result["performance_accepted"] is False
    stopped.assert_not_called()
