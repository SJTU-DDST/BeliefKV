from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from beliefkv.experiments.arrival_schedule import WorkflowArrival
from beliefkv.experiments.deepagents_swebench import (
    DeepAgentsExperimentConfig, _execute_scheduled_root_pool,
)


def test_scheduled_roots_arrive_while_earlier_roots_are_still_running(monkeypatch):
    clock = [100.]
    release = threading.Event()
    records = []
    workloads = [SimpleNamespace(instance_id=f"task-{index}") for index in range(3)]
    arrivals = tuple(
        WorkflowArrival(index, index, 0, offset)
        for index, offset in enumerate((2., 4., 7.))
    )

    def sleep(seconds):
        clock[0] += seconds

    def emit(event, **fields):
        records.append({"event": event, **fields})

    def run_one(workload):
        if workload.instance_id == "task-2":
            release.set()
        assert release.wait(5), "submission waited for an earlier root to finish"
        return workload.instance_id

    monkeypatch.setattr("beliefkv.experiments.deepagents_swebench.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("beliefkv.experiments.deepagents_swebench.time.sleep", sleep)
    completed = _execute_scheduled_root_pool(
        workloads, arrivals=arrivals, concurrency=3, run_one=run_one,
        started=100., audit=SimpleNamespace(emit=emit),
    )
    assert {future.result() for future, _ in completed} == {"task-0", "task-1", "task-2"}
    submissions = [row for row in records if row["event"] == "workflow_submitted"]
    assert [row["instance_id"] for row in submissions] == ["task-0", "task-1", "task-2"]
    assert [row["actual_offset_seconds"] for row in submissions] == [2., 4., 7.]
    assert sum(row["event"] == "workflow_started" for row in records) == 3
    assert sum(row["event"] == "workflow_finished" for row in records) == 3


@pytest.mark.parametrize("conflict", (
    {"workflow_arrival_interval_ms": 10.},
    {"workflow_arrival_batch_size": 2, "workflow_arrival_batch_interval_ms": 100.},
    {"saturated_root_backlog": True},
))
def test_frozen_arrivals_cannot_be_overridden_by_a_second_arrival_mode(tmp_path, conflict):
    with pytest.raises(ValueError, match="cannot be combined"):
        DeepAgentsExperimentConfig(
            mode="planned", base_url="http://localhost/v1", model="model",
            output_dir=tmp_path, workload_manifest=tmp_path / "tasks.json",
            docker_image="fixture", workflow_arrival_schedule=Path("/run/ab_plan.json"),
            **conflict,
        )
