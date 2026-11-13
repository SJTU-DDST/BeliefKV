from copy import deepcopy
from pathlib import Path
import os
import signal
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import hold_campaign_at_replay as holder
from scripts.hold_gpu_campaign_access import process as proc_stat
from scripts.hold_campaign_at_replay import ready_for_hold, same_process


def fixture():
    live = {
        pid: {"pid": pid, "start_ticks": str(pid), "state": "S", "argv": []}
        for pid in range(1, 5)
    }
    cohort = Path("/ops/oracle.cohort.registration.json")
    live[4]["argv"] = ["python", "stop_after_measured_cohort.py", "--watch", str(cohort)]
    serving = {
        "status": "running_full", "watcher": live[1], "server": live[2], "driver": live[3],
    }
    state = {
        "status": "waiting_fixed_18_oracle", "current_stage": "oracle", "cohort_observer": live[4],
    }
    return live, serving, state, cohort


def test_hold_requires_actual_replay_and_its_independent_fixed_cohort_observer():
    live, serving, state, cohort = fixture()
    assert ready_for_hold(state, serving, "oracle", live.get, cohort)
    changed = {**state, "status": "waiting_actual_replay_oracle"}
    assert not ready_for_hold(changed, serving, "oracle", live.get, cohort)
    assert not ready_for_hold(state, serving, "oracle", live.get, Path("/wrong/cohort.json"))


@pytest.mark.parametrize("pid", range(1, 5))
def test_missing_reused_or_zombie_serving_or_observer_process_is_not_ready(pid):
    original, serving, state, cohort = fixture()
    for changed in (None, {"pid": pid, "start_ticks": "reused", "state": "S"},
                    {"pid": pid, "start_ticks": str(pid), "state": "Z"}):
        live = deepcopy(original)
        live[pid] = changed
        assert not ready_for_hold(state, serving, "oracle", live.get, cohort)


def test_coordinator_may_be_stopped_while_its_identity_remains_live():
    expected = {"pid": 100, "start_ticks": "200"}
    assert same_process(expected, {**expected, "state": "T"})
    assert not same_process(expected, {**expected, "start_ticks": "201", "state": "T"})


def test_real_cpu_process_hold_preserves_serving_and_cohort_processes(tmp_path, monkeypatch):
    cohort = tmp_path / "oracle.cohort.registration.json"
    children = [
        subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)", "--watch", str(cohort)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ) for _ in range(5)
    ]

    def process(pid):
        value = proc_stat(pid)
        if value is not None:
            value["argv"] = Path(f"/proc/{pid}/cmdline").read_bytes().decode().rstrip("\0").split("\0")
        return value

    try:
        coordinator, watcher, server, driver, observer = [process(child.pid) for child in children]
        stage_registration = tmp_path / "oracle.registration.json"
        serving = {
            "status": "running_full", "watcher": watcher, "server": server, "driver": driver,
        }
        plan_path = tmp_path / "plan.json"
        holder.campaign.write(plan_path, {
            "operator": str(tmp_path), "stages": {"oracle": {"registration": str(stage_registration)}},
        })
        holder.campaign.write(stage_registration.with_suffix(".state.json"), serving)
        holder.campaign.write(tmp_path / "state.json", {
            "status": "waiting_fixed_18_oracle", "current_stage": "oracle",
            "cohort_observer": observer,
        })
        output = tmp_path / "observer"
        output.mkdir()
        registration = output / "registration.json"
        holder.campaign.write(registration, {
            "plan": str(plan_path), "stage": "oracle", "coordinator": coordinator,
            "source_sha256": holder.campaign.digest(Path(holder.__file__)),
            "plan_sha256": holder.campaign.digest(plan_path),
        })
        monkeypatch.setattr(holder.campaign, "services", lambda _: (
            SimpleNamespace(reservation=SimpleNamespace(process=process)), None, None,
        ))
        holder.watch(registration)
        assert process(coordinator["pid"])["state"] in ("T", "t")
        assert all(process(child.pid)["state"] not in ("T", "t", "Z", "X") for child in children[1:])
        assert holder.campaign.load(output / "state.json")["status"] == "coordinator_held_GPU_replay_continues"
    finally:
        for child in children:
            if child.poll() is None:
                os.kill(child.pid, signal.SIGCONT)
                child.terminate()
            child.wait(timeout=5)
