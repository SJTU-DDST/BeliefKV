import json

import pytest

from scripts import watch_workflow_benefit_results as results


def plan(tmp_path, monkeypatch, *, alive=()):
    monkeypatch.setattr(
        results.causal.reservation, "alive",
        lambda handle: bool(handle and handle.get("pid") in alive),
    )
    return {
        "comparison": str(tmp_path / "comparison.json"),
        "resource_waiter": {"pid": 1},
        "cohort_observer": str(tmp_path / "cohort.json"),
        "review_dir": str(tmp_path / "review"),
        "run_root": str(tmp_path / "run"),
    }


def running_files(tmp_path, *, owners=True):
    (tmp_path / "comparison.json").write_text("{}")
    state = {"status": "running_full"}
    if owners:
        state.update(watcher={"pid": 2}, server={"pid": 3}, driver={"pid": 4})
    (tmp_path / "comparison.state.json").write_text(json.dumps(state))


def completed_files(tmp_path):
    directory = tmp_path / "run/cohort_termination"
    directory.mkdir(parents=True)
    (directory / "completion.json").write_text("{}")
    review = tmp_path / "review"
    review.mkdir()
    for name in ("review.json", "cohort.json", "workload_decision.json"):
        (review / name).write_text("{}")


def test_live_resource_waiter_is_a_wait_not_a_finished_experiment(tmp_path, monkeypatch):
    prepared = plan(tmp_path, monkeypatch, alive=(1,))
    assert results.readiness(prepared) == "waiting_resource_waiter"
    results.causal.reservation.alive = lambda _handle: False
    with pytest.raises(RuntimeError, match="exited before"):
        results.readiness(prepared)


def test_completed_files_do_not_allow_results_before_actual_owned_exit(tmp_path, monkeypatch):
    prepared = plan(tmp_path, monkeypatch, alive=(3,))
    running_files(tmp_path)
    completed_files(tmp_path)
    assert results.readiness(prepared) == "waiting_complete_measured_work"


def test_complete_cohort_review_and_owned_exit_enable_only_cpu_analysis(tmp_path, monkeypatch):
    prepared = plan(tmp_path, monkeypatch)
    running_files(tmp_path)
    completed_files(tmp_path)
    assert results.readiness(prepared) == "complete_review_ready"


def test_review_process_remains_authoritative_after_gpu_owners_exit(tmp_path, monkeypatch):
    prepared = plan(tmp_path, monkeypatch, alive=(5,))
    running_files(tmp_path)
    (tmp_path / "cohort.state.json").write_text(json.dumps({
        "process": {"pid": 5}, "status": "reviewing_completed_measured_cohort",
    }))
    assert results.readiness(prepared) == "waiting_measured_cohort_or_review"


def test_failed_cohort_review_is_preserved_instead_of_treated_as_success(tmp_path, monkeypatch):
    prepared = plan(tmp_path, monkeypatch)
    running_files(tmp_path)
    (tmp_path / "cohort.state.json").write_text(json.dumps({
        "process": {"pid": 5}, "status": "stopped_preserve_measured_cohort_review", "error": "incomplete receipts",
    }))
    with pytest.raises(RuntimeError, match="incomplete cohort review"):
        results.readiness(prepared)


def test_unpublished_owner_handles_do_not_establish_terminal_state(tmp_path, monkeypatch):
    prepared = plan(tmp_path, monkeypatch)
    running_files(tmp_path, owners=False)
    assert results.readiness(prepared) == "waiting_observable_owner_handles"


@pytest.mark.parametrize("owners", ("all", "watcher_only", "none"))
def test_real_process_helper_contract_never_receives_an_unpublished_handle(tmp_path, monkeypatch, owners):
    prepared = plan(tmp_path, monkeypatch)
    running_files(tmp_path, owners=False)
    state = {"status": "starting_full"}
    if owners != "none":
        state.update(watcher={"pid": 2}, server=None, driver=None)
    if owners == "all":
        state.update(server={"pid": 3}, driver={"pid": 4})
    (tmp_path / "comparison.state.json").write_text(json.dumps(state))
    called = []

    def strict_alive(handle):
        assert isinstance(handle, dict), "the production process helper requires an actual identity"
        called.append(handle["pid"])
        return handle["pid"] == 2

    monkeypatch.setattr(results.causal.reservation, "alive", strict_alive)
    expected = "waiting_observable_owner_handles" if owners == "none" else "waiting_complete_measured_work"
    assert results.readiness(prepared) == expected
    assert None not in called


def test_complete_files_without_published_gpu_owners_cannot_establish_actual_exit(tmp_path, monkeypatch):
    prepared = plan(tmp_path, monkeypatch)
    running_files(tmp_path, owners=False)
    completed_files(tmp_path)
    assert results.readiness(prepared) == "waiting_observable_owner_handles"
