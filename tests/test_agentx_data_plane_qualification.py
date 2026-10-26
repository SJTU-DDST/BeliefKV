from copy import deepcopy
import json

import pytest

from scripts import qualify_agentx_data_plane as qualifier
from scripts.qualify_agentx_data_plane import predecessor_finished, validate_cache_observations


def observations():
    result = {
        "agentx-data-plane:" + name: {
            "prompt_tokens": 65536, "cached_tokens_device": 0, "cached_tokens_host": 0,
        }
        for name in (
            "cold", "resident_repeat", "other_workflow", "host_restore",
            *(f"pressure_{ordinal:02d}" for ordinal in range(11)),
        )
    }
    result["agentx-data-plane:resident_repeat"]["cached_tokens_device"] = 65535
    result["agentx-data-plane:host_restore"]["cached_tokens_host"] = 65535
    return result


def test_bounded_model_evidence_requires_residency_isolation_and_real_host_recovery():
    validate_cache_observations(observations(), 65536)


@pytest.mark.parametrize("change", ("missing", "unrelated", "resident", "device_leak", "host_leak", "restore", "unknown"))
def test_incomplete_or_ambiguous_native_evidence_cannot_qualify_the_engine(change):
    observed = deepcopy(observations())
    if change == "missing":
        observed.pop("agentx-data-plane:cold")
    elif change == "unrelated":
        observed["other"] = observed.pop("agentx-data-plane:cold")
    elif change == "resident":
        observed["agentx-data-plane:resident_repeat"]["cached_tokens_device"] = 0
    elif change == "device_leak":
        observed["agentx-data-plane:other_workflow"]["cached_tokens_device"] = 1
    elif change == "host_leak":
        observed["agentx-data-plane:other_workflow"]["cached_tokens_host"] = 1
    elif change == "restore":
        observed["agentx-data-plane:host_restore"]["cached_tokens_host"] = 0
    else:
        observed["agentx-data-plane:cold"]["cached_tokens_host"] = None
    with pytest.raises(ValueError, match="namespace isolation or Host recovery"):
        validate_cache_observations(observed, 65536)


def test_gpu_queue_waits_for_live_predecessor_even_with_a_terminal_state_file(tmp_path, monkeypatch):
    (tmp_path / "supervisor.json").write_text(json.dumps({"pid": 123, "start_ticks": "456"}))
    (tmp_path / "state.json").write_text(json.dumps({
        "status": "complete_review_pending", "finished_at": "recorded",
    }))
    monkeypatch.setattr(qualifier.controls.reservation, "alive", lambda _: True)
    assert not predecessor_finished(tmp_path)
    (tmp_path / "registration.json").write_text("{}")
    (tmp_path / "owned_process_cleanup.json").write_text(json.dumps({
        "remaining_processes": [], "identities": [], "finished_at": "cleaned",
    }))
    monkeypatch.setattr(qualifier.controls.reservation, "alive", lambda _: False)
    assert predecessor_finished(tmp_path)
    assert predecessor_finished(None)


def test_missing_predecessor_process_does_not_turn_unfinished_artifacts_into_completion(tmp_path, monkeypatch):
    (tmp_path / "supervisor.json").write_text(json.dumps({"pid": 123, "start_ticks": "456"}))
    (tmp_path / "state.json").write_text(json.dumps({"status": "starting_server"}))
    monkeypatch.setattr(qualifier.controls.reservation, "alive", lambda _: False)
    assert not predecessor_finished(tmp_path)


@pytest.mark.parametrize("change", ("missing", "unfinished", "remaining", "live_child", "cleanup_error"))
def test_predecessor_exit_does_not_bypass_actual_process_cleanup(tmp_path, monkeypatch, change):
    (tmp_path / "supervisor.json").write_text(json.dumps({"pid": 1, "start_ticks": "old"}))
    (tmp_path / "registration.json").write_text(json.dumps({"arm": "baseline"}))
    state = {"status": "complete_review_pending", "finished_at": "finished"}
    if change == "cleanup_error":
        state["cleanup_error"] = "failed"
    (tmp_path / "state.json").write_text(json.dumps(state))
    cleanup = {"remaining_processes": [], "identities": [], "finished_at": "cleaned"}
    if change == "unfinished":
        cleanup.pop("finished_at")
    elif change == "remaining":
        cleanup["remaining_processes"] = [{"pid": 2}]
    elif change == "live_child":
        cleanup["identities"] = [{"pid": 2}]
    (tmp_path / "baseline").mkdir()
    if change != "missing":
        (tmp_path / "baseline/owned_process_cleanup.json").write_text(json.dumps(cleanup))
    monkeypatch.setattr(qualifier.controls.reservation, "alive", lambda row: row["pid"] == 2)
    assert not predecessor_finished(tmp_path)
