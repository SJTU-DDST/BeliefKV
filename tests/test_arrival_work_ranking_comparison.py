from pathlib import Path
from copy import deepcopy
import json

import pytest

from scripts import run_arrival_work_ranking_comparison as runner


def setup(monkeypatch):
    preparation, cpu = Path("preparation.json"), Path("cpu.json")
    plan = {
        "inputs_sha256": {}, "mean_interval_seconds": 60.,
        "workflows": 83, "initial_workflows": 20,
        "static_completion_coverage_screen": {"full": {"candidate_schedule_covers_static_tail": True}},
    }
    evidence = {
        "returncode": 0, "failed_error_skipped_cases": 0,
        "all_retained_literal_identities_present": True,
        "sources_unchanged_during_execution": True,
    }
    monkeypatch.setattr(runner.causal, "load", lambda path: {
        preparation: plan, cpu: evidence,
    }[path])
    monkeypatch.setattr(runner.causal.fresh, "_require_clean", lambda path: None)
    monkeypatch.setattr(runner.finish, "verify_pins", lambda pins: None)
    monkeypatch.setattr(
        runner.causal.qualified, "resources", lambda *_: pytest.fail("GPU checked before preparation gates"),
    )
    monkeypatch.setattr(runner, "released", lambda: pytest.fail("GPU launch reached before CPU gates"))
    return preparation, cpu, plan, evidence


def test_registration_name_cannot_silently_describe_another_arrival_rate(tmp_path, monkeypatch):
    preparation, cpu, plan, _ = setup(monkeypatch)
    plan["mean_interval_seconds"] = 50.
    with pytest.raises(ValueError, match="identity requires"):
        runner.start(preparation, cpu, tmp_path / "execution")
    assert not (tmp_path / "execution").exists()


def test_shortened_background_must_cover_the_static_natural_tails(tmp_path, monkeypatch):
    preparation, cpu, plan, _ = setup(monkeypatch)
    plan["static_completion_coverage_screen"]["full"]["candidate_schedule_covers_static_tail"] = False
    with pytest.raises(ValueError, match="static measured tails"):
        runner.start(preparation, cpu, tmp_path / "execution")
    assert not (tmp_path / "execution").exists()


def test_failed_or_stale_cpu_evidence_does_not_reach_gpu_launch(tmp_path, monkeypatch):
    preparation, cpu, _, evidence = setup(monkeypatch)
    evidence["sources_unchanged_during_execution"] = False
    with pytest.raises(ValueError, match="source-bound CPU"):
        runner.start(preparation, cpu, tmp_path / "execution")
    assert not (tmp_path / "execution").exists()


@pytest.mark.parametrize("label", ("full", "oracle", "learned"))
def test_all_stages_replace_template_arrivals_with_the_declared_followup(label):
    flags = (
        "--registration", "--run-root", "--port", "--experiment-config",
        "--workflow-arrivals", "--cpu-report", "--cpu-sources", "--engine-sources",
    )
    template = ["/python", "/old/run_causal_comparison.py"]
    for flag in flags:
        template.extend((flag, "old-" + flag.removeprefix("--")))
    previous = list(template)
    stage = {
        "registration": f"/new/{label}.registration.json",
        "run_root": f"/new/{label}", "port": 19035,
        "configuration": f"/new/{label}.json",
    }
    prep = {"arrivals": "/new/mean60/arrivals.json"}
    cpu = {"report": {"path": "/new/cpu.xml"}, "source_manifest": {"path": "/new/sources.json"}}
    command = runner.stage_command(template, stage, prep, cpu, Path("/new/native.json"))
    assert command[command.index("--workflow-arrivals") + 1] == prep["arrivals"]
    assert command[command.index("--experiment-config") + 1] == stage["configuration"]
    assert command[command.index("--cpu-sources") + 1] == cpu["source_manifest"]["path"]
    assert template == previous


@pytest.mark.parametrize("suffix", ("", "../old", "new/run", "New"))
def test_new_run_identity_cannot_escape_or_alias_its_registration(tmp_path, monkeypatch, suffix):
    preparation, cpu, _, _ = setup(monkeypatch)
    with pytest.raises(ValueError, match="run suffix"):
        runner.start(preparation, cpu, tmp_path / "execution", run_suffix=suffix)
    assert not (tmp_path / "execution").exists()


def replay_wait_setup(tmp_path, monkeypatch):
    path = tmp_path / "full.registration.json"
    path.with_suffix(".state.json").touch()
    owner = {"pid": 42, "start_ticks": "123", "argv": ["/python", "/frozen/watch.py"]}
    snapshot = {"status": "waiting_idle_gpu", "watcher": dict(owner)}
    monkeypatch.setattr(runner.causal, "load", lambda path: snapshot)
    monkeypatch.setattr(runner.causal.reservation, "alive", lambda owner: True)
    return {"registration": str(path)}, owner, snapshot, {"current_stage": "full"}


@pytest.mark.parametrize("waiting_status", (
    "waiting_idle_gpu", "waiting_resources_full", "waiting_previous_causal_comparison",
))
def test_live_resource_wait_does_not_consume_actual_startup_deadline(
    tmp_path, monkeypatch, waiting_status,
):
    stage, owner, snapshot, state = replay_wait_setup(tmp_path, monkeypatch)
    snapshot["status"] = waiting_status
    clock, waits = [0.], []
    monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])

    def wait():
        waits.append(state["status"])
        clock[0] += 3600.
        if len(waits) == 4:
            snapshot.update(status="starting_full")
        elif len(waits) == 5:
            clock[0] -= 3590.
            snapshot.update(status="running_full", server=owner, driver=owner)

    assert runner.wait_for_actual_replay(stage, owner, state, wait) is snapshot
    assert waits[:4] == ["waiting_resources_full"] * 4
    assert waits[-1] == "waiting_actual_replay_full"


def test_actual_startup_still_has_a_bounded_deadline(tmp_path, monkeypatch):
    stage, owner, snapshot, state = replay_wait_setup(tmp_path, monkeypatch)
    snapshot["status"] = "starting_full"
    clock = [0.]
    monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])

    def wait():
        clock[0] += 600.

    with pytest.raises(RuntimeError, match="startup timed out outside resource waiting"):
        runner.wait_for_actual_replay(stage, owner, state, wait)


@pytest.mark.parametrize("failure", ("dead", "foreign", "stopped"))
def test_replay_wait_rejects_dead_replaced_or_terminal_supervisors(tmp_path, monkeypatch, failure):
    stage, owner, snapshot, state = replay_wait_setup(tmp_path, monkeypatch)
    if failure == "dead":
        monkeypatch.setattr(runner.causal.reservation, "alive", lambda owner: False)
    elif failure == "foreign":
        snapshot["watcher"]["start_ticks"] = "456"
    else:
        snapshot["status"] = "stopped_for_investigation"
    with pytest.raises((RuntimeError, ValueError)):
        runner.wait_for_actual_replay(
            stage, owner, state, lambda: pytest.fail("invalid supervisor remained waiting"),
        )


def continuation_setup(tmp_path, monkeypatch):
    root = tmp_path / "old"
    root.mkdir()
    plan_path = root / "execution_plan.json"
    owner = {
        "pid": 42, "start_ticks": "123",
        "argv": ["/python", str(tmp_path / "frozen/scripts/watch.py")],
    }
    stages = {
        label: {
            "registration": str(root / f"{label}.registration.json"),
            "run_root": str(root / f"{label}_raw"), "port": 19035 + index,
            "configuration": str(root / f"{label}_configuration.json"),
            "review": str(root / f"{label}_review"),
            "command": ["/python", "/original/frozen/launcher.py", "--arms", "full"],
        } for index, label in enumerate(runner.LABELS)
    }
    plan = {"inputs_sha256": {}, "stages": stages}
    previous = {
        "status": "stopped_preserve_arrival_work_ranking_comparison",
        "current_stage": "full", "GPU_registered_stages": ["full"],
        "error": "RuntimeError('serving supervisor exited or startup timed out')",
        "process": {"pid": 99}, "serving_watcher": owner,
    }
    serving = {
        "status": "waiting_idle_gpu", "watcher": owner, "completed_arms": [], "owned": [],
    }
    registration = {
        "order": ["full"], "run_root": stages["full"]["run_root"],
        "port": stages["full"]["port"],
        "experiment_configuration": stages["full"]["configuration"],
        "execution_checkout": str(tmp_path / "frozen"),
    }
    documents = {
        plan_path: plan, root / "state.json": previous,
        Path(stages["full"]["registration"]): registration,
        Path(stages["full"]["registration"]).with_suffix(".state.json"): serving,
    }
    for path, document in documents.items():
        path.write_text(json.dumps(document))
    monkeypatch.setattr(runner.causal, "load", lambda path: documents[path])
    monkeypatch.setattr(runner.causal.fresh, "_require_clean", lambda path: None)
    monkeypatch.setattr(runner.finish, "verify_pins", lambda pins: None)
    monkeypatch.setattr(runner.causal.comparison, "verify_sources", lambda registration: None)
    monkeypatch.setattr(runner, "released", lambda: None)
    monkeypatch.setattr(runner.causal.reservation, "alive", lambda item: item["pid"] == 42)
    monkeypatch.setattr(runner.causal.reservation, "process", lambda pid: {"pid": pid})
    launches = []

    def launch(command, **kwargs):
        launches.append(command)
        return type("Child", (), {"pid": 100})()

    monkeypatch.setattr(runner.subprocess, "Popen", launch)
    return plan_path, documents, launches


def test_continuation_adopts_existing_full_without_registering_or_restarting_it(tmp_path, monkeypatch):
    plan_path, documents, launches = continuation_setup(tmp_path, monkeypatch)
    previous = deepcopy(documents[plan_path])
    output = tmp_path / "continued"
    result = runner.continue_waiting_full(plan_path, output)
    resumed = json.loads((output / "execution_plan.json").read_text())
    assert result["GPU_registration_reused"] is True
    assert resumed["stages"]["full"]["existing_watcher"]["pid"] == 42
    assert resumed["coordinator_lock"] == str(plan_path.parent / "coordinator.lock")
    for label in runner.LABELS:
        assert resumed["stages"][label]["command"] == previous["stages"][label]["command"]
    assert documents[plan_path] == previous
    assert len(launches) == 1 and "--watch" in launches[0]


def test_continued_watcher_reuses_registration_before_any_gpu_resource_or_launch_call(
    tmp_path, monkeypatch,
):
    plan_path, documents, launches = continuation_setup(tmp_path, monkeypatch)
    plan = deepcopy(documents[plan_path])
    owner = documents[plan_path.parent / "state.json"]["serving_watcher"]
    plan["stages"]["full"]["existing_watcher"] = owner
    monkeypatch.setattr(
        runner.causal.qualified, "resources",
        lambda *_: pytest.fail("existing supervisor already owns resource waiting"),
    )
    monkeypatch.setattr(
        runner.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("duplicate GPU registration"),
    )
    observed = []

    def wait_for_replay(stage, actual_owner, state, wait):
        observed.append(actual_owner)
        raise RuntimeError("test ends after verifying the live-supervisor handoff")

    monkeypatch.setattr(runner, "wait_for_actual_replay", wait_for_replay)
    output = tmp_path / "continued"
    output.mkdir()
    runner.watch_locked(plan, output)
    state = json.loads((output / "state.json").read_text())
    assert observed == [owner]
    assert state["GPU_registered_stages"] == ["full"]
    assert not launches


@pytest.mark.parametrize("failure", (
    "coordinator_alive", "wrong_owner", "full_started", "later_registered", "observer_exists",
))
def test_continuation_refuses_duplicate_or_changed_campaign_state(tmp_path, monkeypatch, failure):
    plan_path, documents, launches = continuation_setup(tmp_path, monkeypatch)
    stage = documents[plan_path]["stages"]["full"]
    serving = documents[Path(stage["registration"]).with_suffix(".state.json")]
    if failure == "coordinator_alive":
        monkeypatch.setattr(runner.causal.reservation, "alive", lambda owner: True)
    elif failure == "wrong_owner":
        serving["watcher"] = {**serving["watcher"], "start_ticks": "456"}
    elif failure == "full_started":
        Path(stage["run_root"]).mkdir()
    elif failure == "later_registered":
        Path(documents[plan_path]["stages"]["oracle"]["registration"]).touch()
    else:
        Path(stage["registration"].replace(".registration.json", ".cohort.registration.json")).touch()
    output = tmp_path / "continued"
    with pytest.raises(ValueError):
        runner.continue_waiting_full(plan_path, output)
    assert not output.exists()
    assert not launches
