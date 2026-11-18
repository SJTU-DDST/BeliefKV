import json

import pytest

from scripts import stop_after_measured_cohort as stop
from tests.test_measured_cohort_stop import partial_fixture


@pytest.mark.parametrize("limit", (None, 1, 14400, 86400.5))
def test_only_an_explicit_observation_limit_creates_a_deadline(limit):
    plan = {} if limit is None else {"maximum_observation_seconds": limit}
    assert stop.observation_deadline(plan, 10) == (
        None if limit is None else 10 + limit
    )


@pytest.mark.parametrize("limit", (True, False, 0, -1, float("inf"), float("nan"), "14400"))
def test_invalid_observation_limits_are_rejected(limit):
    with pytest.raises(ValueError, match="maximum_observation_seconds"):
        stop.observation_deadline({"maximum_observation_seconds": limit}, 10)


@pytest.mark.parametrize("mode", ("default", "explicit_limit", "dead_owner"))
def test_actual_observer_waits_past_four_hours_only_with_live_owners(
    tmp_path, monkeypatch, mode,
):
    arrivals, cohort, events, _ = partial_fixture()
    registration = tmp_path / "observer.json"
    comparison_path = tmp_path / "comparison.json"
    cohort_path, arrivals_path = tmp_path / "cohort.json", tmp_path / "arrivals.json"
    output, analysis = tmp_path / "termination", tmp_path / "analysis"
    arm = tmp_path / "full"
    (arm / "replay").mkdir(parents=True)
    event_path = arm / "replay/runtime_events.replay.jsonl"
    final = next(row for row in events if row["kind"] == "workflow_end" and row["workflow_id"] == "w2")
    event_path.write_text("".join(
        json.dumps(row) + "\n" for row in events if row is not final
    ))
    plan = {
        "comparison": str(comparison_path), "cohort": str(cohort_path),
        "output_dir": str(output), "analysis_output": str(analysis),
        "inputs_sha256": {}, "poll_seconds": 2,
    }
    if mode == "explicit_limit":
        plan["maximum_observation_seconds"] = 3600
    comparison = {
        "run_root": str(tmp_path), "order": ["full"],
        "workflow_arrivals_path": str(arrivals_path),
    }
    current = {
        "status": "running_full",
        **{name: {"pid": i + 10} for i, name in enumerate(("watcher", "driver", "server"))},
    }
    documents = {
        registration: plan, comparison_path: comparison, cohort_path: cohort,
        arrivals_path: arrivals, comparison_path.with_suffix(".state.json"): current,
    }
    monkeypatch.setattr(stop.causal, "load", lambda path: documents[path])
    monkeypatch.setattr(stop.os, "getpriority", lambda *_: 19)
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "99999")
    monkeypatch.setattr(stop.capture.reservation, "process", lambda _: {"pid": 1})
    monkeypatch.setattr(stop.capture.reservation, "alive", lambda _: mode != "dead_owner")
    monkeypatch.setattr(stop, "sha256_file", lambda _: "digest")
    clock, sleeps, shutdowns = [100.], [], []
    monkeypatch.setattr(stop.time, "monotonic", lambda: clock[0])

    def sleep(_):
        sleeps.append(clock[0])
        assert len(sleeps) <= 2
        clock[0] += 4 * 3600 + 1
        if len(sleeps) == 2:
            with event_path.open("a") as stream:
                stream.write(json.dumps(final) + "\n")

    monkeypatch.setattr(stop.time, "sleep", sleep)

    def shutdown(*args, **kwargs):
        shutdowns.append(clock[0])
        return {"complete_measured_native_work_verified": True}

    monkeypatch.setattr(stop, "stop_owned", shutdown)

    def review(*_):
        documents[analysis / "workload_decision.json"] = {
            "accepted_for_oracle_diagnostic": False, "failed_checks": [],
        }

    monkeypatch.setattr(stop, "review", review)
    stop.watch(registration)
    state = json.loads(registration.with_suffix(".state.json").read_bytes())
    if mode == "default":
        assert state["status"] == "measured_cohort_review_complete"
        assert len(sleeps) == 2 and len(shutdowns) == 1
        assert shutdowns[0] > 4 * 3600
        assert json.loads((output / "completion.json").read_bytes())["no_measured_workflow_truncated"] is True
    else:
        assert state["status"] == "stopped_preserve_measured_cohort_review"
        assert not shutdowns and not (output / "completion.json").exists()
        assert ("declared observation limit" if mode == "explicit_limit" else "owner exited") in state["error"]
