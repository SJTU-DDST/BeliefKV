import json
from pathlib import Path
import socket
from unittest.mock import Mock

import pytest

from scripts import launch_semantic_h2d_after_pair as launcher
from tests.test_resume_semantic_h2d_ab import plan


def completed_pair(root):
    configuration = plan()
    configuration["order"] = ["predictive_h2d", "native"]
    for arm in configuration["order"]:
        summary = root / arm / "client_156" / "summary.json"
        summary.parent.mkdir(parents=True)
        summary.write_text(json.dumps({"workflow_count": 156}))
        (root / f"{arm}.timeline_export.json").write_text("{}")
        (root / f"{arm}.workspace_cleanup.json").write_text("{}")
        timeline = root / "timelines" / f"{arm}.html"
        timeline.parent.mkdir(exist_ok=True)
        timeline.write_text("<html></html>")
    (root / "native_policy_comparison.json").write_text("{}")
    return configuration


def test_new_pair_uses_same_workload_and_artifacts_with_fresh_predictive_first():
    configuration = plan()
    snapshot = json.dumps(configuration, sort_keys=True)
    environment = launcher.fresh_pair_environment(
        configuration, Path("/fresh-run"), python="/python", port=18454,
    )
    assert environment["RESUME_PENDING"] == "0"
    assert environment["ARM_ORDER"] == "predictive_h2d native"
    assert environment["RUN_ROOT"] == "/fresh-run"
    assert environment["ROOT_COUNT"] == "156"
    assert environment["ARRIVAL_BATCH_SIZE"] == "108"
    assert environment["ARRIVAL_BATCH_INTERVAL_MS"] == "3600000"
    assert environment["SEMANTIC_REPORT_ARTIFACT"] == "/models/semantic.json"
    assert environment["H2D_SEED_ARTIFACT"] == "/models/h2d.json"
    assert environment["TOOL_TIMING_ARTIFACT"] == "/models/tool.json"
    assert environment["TRANSFER_SERVICE_SEED"] == "/models/service.json"
    assert environment["HOST_SPLIT"] == "80:20"
    assert environment["PREFETCH_LEAD_MS"] == "500"
    assert environment["CHILD_FINAL_REPORT_SHADOW"] == "1"
    assert json.dumps(configuration, sort_keys=True) == snapshot


def test_complete_pair_requires_both_arms_and_finished_postprocessing(tmp_path):
    configuration = completed_pair(tmp_path)
    launcher.validate_completed_pair(tmp_path, configuration)
    (tmp_path / "native.workspace_cleanup.json").unlink()
    with pytest.raises(ValueError, match="postprocessing"):
        launcher.validate_completed_pair(tmp_path, configuration)


def test_next_pair_lead_override_preserves_previous_plan_and_every_other_setting():
    configuration = plan()
    snapshot = json.dumps(configuration, sort_keys=True)
    arguments = {"python": "/python", "port": 18454}
    original = launcher.fresh_pair_environment(configuration, Path("/fresh-run"), **arguments)
    revised = launcher.fresh_pair_environment(
        configuration, Path("/fresh-run"), **arguments, prefetch_lead_ms=2000,
    )
    assert revised == {**original, "PREFETCH_LEAD_MS": "2000"}
    assert json.dumps(configuration, sort_keys=True) == snapshot


def test_partial_arrival_table_does_not_deploy_new_code(tmp_path):
    configuration = completed_pair(tmp_path)
    (tmp_path / "native/client_156/summary.json").write_text(
        json.dumps({"workflow_count": 155}),
    )
    with pytest.raises(ValueError, match="complete arrival table"):
        launcher.validate_completed_pair(tmp_path, configuration)


def test_missing_final_comparison_does_not_deploy_new_code(tmp_path):
    configuration = completed_pair(tmp_path)
    (tmp_path / "native_policy_comparison.json").unlink()
    with pytest.raises(ValueError, match="final native-policy comparison"):
        launcher.validate_completed_pair(tmp_path, configuration)


def test_wait_keeps_engine_frozen_until_original_driver_and_scheduler_exit(monkeypatch):
    driver, scheduler = ("S", 1, "100"), ("S", 2, "101")
    observations = iter((driver, scheduler, None, scheduler, None, None))
    monkeypatch.setattr(launcher, "process_identity", lambda _: next(observations))
    sleep = Mock()
    monkeypatch.setattr(launcher.time, "sleep", sleep)
    launcher.wait_for_processes({10: driver, 11: scheduler})
    assert sleep.call_count == 2


def test_reused_pid_and_zombie_do_not_hold_the_next_pair(monkeypatch):
    observations = {10: ("S", 1, "200"), 11: ("Z", 2, "101")}
    monkeypatch.setattr(launcher, "process_identity", observations.get)
    sleep = Mock(side_effect=AssertionError("unexpected wait"))
    monkeypatch.setattr(launcher.time, "sleep", sleep)
    launcher.wait_for_processes({10: ("S", 1, "100"), 11: ("S", 2, "101")})


def test_development_stale_socket(tmp_path):
    path = tmp_path / "c"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.bind(str(path))
    assert launcher._remove_stale_control_socket(path)
    assert not path.exists()
    assert not launcher._remove_stale_control_socket(path)


def test_development_live_socket(tmp_path):
    path = tmp_path / "c"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.bind(str(path))
        connection.listen()
        with pytest.raises(ValueError, match="still serving"):
            launcher._remove_stale_control_socket(path)
        assert path.is_socket()
