"""Require real complete work, drained telemetry and actual cleanup."""

import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "mirror_complete_review", Path(__file__).with_name("complete_review.py"),
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.fixture
def completed(monkeypatch):
    records = {
        "registration.json": {"run_root": "/fixture", "quality_exclusions": []},
        "summary.json": {"workflow_elapsed_ms": {"workflow": 1000.}},
        "telemetry_drain.json": {
            "previous_zero_snapshot_ts_ms": 1.,
            "drained_status": {
                "snapshot_ts_ms": 2., "pending_request_count": 0,
                "pending_batch_count": 0, "writer_queue_depth": 0,
                "failed_records": 0, "dropped_records": 0, "writer_error": None,
            },
        },
        "owned_process_cleanup.json": {"remaining_processes": [], "identities": []},
    }
    monkeypatch.setattr(MODULE.causal, "load", lambda path: records[Path(path).name])
    monkeypatch.setattr(MODULE.causal.comparison, "verify_sources", lambda _plan: None)
    monkeypatch.setattr(MODULE.causal, "predecessor_ready", lambda _plan: True)
    monkeypatch.setattr(MODULE.causal, "validate_result", lambda *_args, **_kwargs: {"request": (1, 1)})
    monkeypatch.setattr(MODULE.causal, "observed_configuration", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(MODULE.causal.reservation, "alive", lambda _identity: False)
    return records


def test_complete_boundary_accepts_two_distinct_idle_snapshots(completed):
    _plan, _arm, _summary, work = MODULE.completed_arm(Path("/fixture/registration.json"), "full")
    assert work == {"request": (1, 1)}


def test_live_predecessor_is_not_complete_evidence(completed, monkeypatch):
    monkeypatch.setattr(MODULE.causal, "predecessor_ready", lambda _plan: False)
    with pytest.raises(ValueError, match="owned exit"):
        MODULE.completed_arm(Path("/fixture/registration.json"), "full")


def test_deleting_a_normal_tail_is_rejected(completed):
    completed["registration.json"]["quality_exclusions"].append("workflow")
    with pytest.raises(ValueError, match="normal workflow"):
        MODULE.completed_arm(Path("/fixture/registration.json"), "full")


@pytest.mark.parametrize("field,value", (
    ("pending_request_count", 1), ("pending_batch_count", 1), ("writer_queue_depth", 1),
    ("failed_records", 1), ("dropped_records", 1), ("writer_error", "failed"),
    ("snapshot_ts_ms", 1.), ("snapshot_ts_ms", .5),
))
def test_busy_failed_or_stale_final_snapshot_is_rejected(completed, field, value):
    completed["telemetry_drain.json"]["drained_status"][field] = value
    with pytest.raises(ValueError, match="idle snapshots"):
        MODULE.completed_arm(Path("/fixture/registration.json"), "full")


def test_cleanup_cannot_hide_a_still_live_owned_process(completed, monkeypatch):
    completed["owned_process_cleanup.json"]["identities"].append({"pid": 123})
    monkeypatch.setattr(MODULE.causal.reservation, "alive", lambda _identity: True)
    with pytest.raises(ValueError, match="process cleanup"):
        MODULE.completed_arm(Path("/fixture/registration.json"), "full")


def test_nonempty_remaining_process_list_is_not_complete(completed):
    completed["owned_process_cleanup.json"]["remaining_processes"].append({"pid": 123})
    with pytest.raises(ValueError, match="process cleanup"):
        MODULE.completed_arm(Path("/fixture/registration.json"), "full")


@pytest.mark.parametrize("name,label", (
    ("tool_offload_before_admission_20261027_r1.registration.json", "full"),
    ("v345_locobench_categorized_thunder_decay_20261027.registration.json", "thunderagent_acting_decay"),
))
def test_actual_complete_full_and_control_meet_the_boundary(name, label):
    plan, _arm, summary, work = MODULE.completed_arm(MODULE.OPS / name, label)
    assert len(work) == 3674
    assert len(summary["workflow_elapsed_ms"]) == len(plan["workflow_ids"]) == 18
