from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from scripts import run_v77_official_quality_after_series as queue


@pytest.fixture
def plan():
    ids = [f"task-{index}" for index in range(156)]
    return {
        "root_count": 156, "workload_instance_ids_in_manifest_order": ids,
        "arrival_schedule": [{"instance_id": identity} for identity in ids],
        "order": ["native"],
    }


def test_quality_cohort_preserves_every_native_task_in_order(plan):
    original = deepcopy(plan)
    assert queue.cohort_ids(plan) == plan["workload_instance_ids_in_manifest_order"]
    assert plan == original


@pytest.mark.parametrize("change", ("subset", "duplicate", "order", "arm"))
def test_quality_cohort_rejects_changed_denominator_or_cohort(plan, change):
    if change == "subset":
        plan["root_count"] = 108
    elif change == "duplicate":
        plan["workload_instance_ids_in_manifest_order"][1] = "task-0"
    elif change == "order":
        plan["arrival_schedule"].reverse()
    else:
        plan["order"] = ["predictive_h2d"]
    with pytest.raises(ValueError, match="complete native task cohort"):
        queue.cohort_ids(plan)


def test_dynamic_waiting_includes_future_workers_and_rejects_pid_reuse(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({
        "supervisor": {"pid": 1, "start_ticks": "10"},
        "driver": {"pid": 2, "start_ticks": "20"},
        "worker": {"pid": 3, "start_ticks": "30"},
    }))
    actual = {
        1: {"pid": 1, "start_ticks": "11", "state": "S"},
        2: {"pid": 2, "start_ticks": "20", "state": "Z"},
        3: {"pid": 3, "start_ticks": "30", "state": "S"},
    }
    assert queue.dynamic_waiters([path, path], actual.get) == [actual[3]]
    actual.clear()
    assert queue.dynamic_waiters([path], actual.get) == []


def test_configure_reuses_the_frozen_evaluator_with_separate_outputs_and_waiters(
    tmp_path,
):
    state = tmp_path / "state.json"
    worker = {"pid": 2, "start_ticks": "20", "state": "S"}
    state.write_text(json.dumps({"worker": worker}))
    parent = {"pid": 1, "start_ticks": "10", "state": "S"}
    writes = []
    series = SimpleNamespace(
        live_waiters=lambda *_args: [parent],
        write=lambda path, value: writes.append((path, value)),
    )
    configured = queue.configure_series(series)
    assert list(configured.ARMS) == ["v77_native_close"]
    assert configured.ARMS["v77_native_close"]["evaluation"] == queue.EVALUATION
    assert configured.FROZEN == queue.FROZEN
    assert configured.RUN_ID == queue.RUN_ID
    assert configured.ENVIRONMENT != queue.ROOT / "experiments/reports/v1184_v75_v76_quality_environment_20261016.json"
    assert configured.live_waiters(
        {"dynamic_wait_states": [str(state)]}, {2: worker}.get,
    ) == [parent, worker]
    configured.write(queue.STATE, {"status": "waiting_for_complete_v75_v76"})
    assert writes[-1][1]["status"] == "waiting_for_complete_v77_and_prior_quality"


@pytest.mark.parametrize("failure", ("failure", "error", "skipped"))
def test_quality_registration_rejects_failed_or_partial_cpu_qualification(tmp_path, failure):
    path = tmp_path / "checks.xml"
    path.write_text(f"<testsuite><testcase><{failure}/></testcase></testsuite>")
    with pytest.raises(ValueError, match="CPU qualification"):
        queue.load_cpu_check(path)


def test_quality_registration_binds_all_passing_cpu_cases(tmp_path):
    path = tmp_path / "checks.xml"
    path.write_text("<testsuite><testcase/><testcase/></testsuite>")
    assert queue.load_cpu_check(path) == {"passed_cases": 2}
