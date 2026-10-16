from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from beliefkv.experiments.arrival_schedule import arrival_schedule_digest
from scripts import start_v77_native_close_20261016 as baseline


@pytest.fixture
def reference():
    ids = [f"task-{index}" for index in range(156)]
    arrivals = [
        {"instance_id": identity, "offset_seconds": float(index)}
        for index, identity in enumerate(ids)
    ]
    return {
        **deepcopy(baseline.EXPECTED_SETTINGS),
        "arrival_schedule": arrivals,
        "arrival_schedule_sha256": arrival_schedule_digest(arrivals),
        "workload_instance_ids_in_manifest_order": ids,
        "workload_sha256": "workload",
        "shared_system_configuration": {"write_back_batch_nodes": 32},
        "in_graph_initial_tool_choice": "task",
        "in_graph_initial_parallel_tool_calls": True,
        "in_graph_initial_fanout_generation_bounds": [2, 4],
        "in_graph_named_task_constraint": "unchanged",
    }


def make_plan(reference):
    return baseline.build_plan(
        reference, commit="application", patch_sha="patch", manifest=Path("/manifest"),
    )


def test_plan_preserves_tasks_arrivals_and_budgets_without_mutating_reference(reference):
    original = deepcopy(reference)
    plan = make_plan(reference)
    assert reference == original
    assert all(plan[key] == reference[key] for key in baseline.MATCHED_FIELDS)
    assert plan["order"] == ["native"]
    assert plan["policy_configuration"]["native"] == {
        "control": False, "resident_first": False, "final_stage_priority": False,
        "prepare_host": False, "predictive_h2d": False, "execution_handoff": False,
        "native_session_close_reclaim": True,
    }
    plan["arrival_schedule"][0]["offset_seconds"] = 999
    assert reference == original


@pytest.mark.parametrize(
    "key,value",
    (
        ("root_count", 108), ("host_split", "70:30"), ("max_completion_tokens", 16384),
        ("sandbox_command_timeout_s", 3600), ("completion_gate_enabled", True),
        ("workflow_arrival_rate_per_second", 1), ("fanout_profile", "native_dynamic_1to4"),
    ),
)
def test_plan_rejects_unmatched_model_application_and_resource_settings(reference, key, value):
    reference[key] = value
    with pytest.raises(ValueError, match=key):
        make_plan(reference)


@pytest.mark.parametrize("change", ("digest", "order", "duplicate", "batch"))
def test_plan_rejects_unmatched_arrivals_or_common_writeback(reference, change):
    if change == "digest":
        reference["arrival_schedule_sha256"] = "other"
    elif change == "order":
        reference["arrival_schedule"].reverse()
    elif change == "duplicate":
        reference["workload_instance_ids_in_manifest_order"][1] = "task-0"
    else:
        reference["shared_system_configuration"]["write_back_batch_nodes"] = 1
    with pytest.raises(ValueError):
        make_plan(reference)


def test_environment_enables_only_native_close_and_preserves_notice(reference):
    env = baseline.collection_environment(make_plan(reference))
    assert env["AB_MODE"] == "off"
    assert env["NATIVE_POLICY_BASELINE"] == "1"
    assert env["CHILD_FINAL_REPORT_SHADOW"] == "1"
    assert env["SGLANG_NATIVE_SESSION_CLOSE_RECLAIM"] == "1"
    assert env["SGLANG_PATCH_FLAVOR"] == "native_session_close"
    assert env["SGLANG_HICACHE_WRITE_BACK_BATCH_NODES"] == "32"
    assert env["WORKFLOW_ARRIVAL_SCHEDULE"].endswith("/ab_plan.json")
    for name, value in env.items():
        if name.startswith("BELIEFKV_") and name != "BELIEFKV_CONTEXT_SUMMARY_INPUT_MODE":
            assert value == "0", name


@pytest.mark.parametrize("supervisor,driver", ((True, False), (False, True), (True, True)))
def test_live_predecessor_is_waited_for_even_if_state_says_completed(supervisor, driver):
    assert baseline.prerequisite_status(
        {"status": "completed"}, supervisor, driver,
    ) == "waiting_for_complete_v76"


@pytest.mark.parametrize("status", ("running_reactive_without_prefetch", "stopped_for_diagnosis", "driver_failed"))
def test_missing_predecessor_without_terminal_success_does_not_start(status):
    with pytest.raises(ValueError, match="V76 ended"):
        baseline.prerequisite_status({"status": status}, False, False)


def test_completed_predecessor_allows_resource_check():
    assert baseline.prerequisite_status(
        {"status": "completed"}, False, False,
    ) == "ready_for_resource_check"


def test_main_checkout_cannot_substitute_for_the_registered_frozen_helper():
    with pytest.raises(ValueError, match="exact frozen helper"):
        baseline.verify_source({"helper": {"path": "/another/helper.py", "sha256": "other"}})


@pytest.fixture
def startup(reference):
    plan = make_plan(reference)
    return {
        "serving": {
            "status": "ready", "max_running_requests": 48, "context_length": 131072,
            "chunked_prefill_size": 4096, "prefill_decode_interval": 0,
            "enable_mixed_chunk": False, "enable_prefill_delayer": False,
            "min_free_slots_delay": None, "enable_beliefkv_admission": False,
        },
        "telemetry": {
            "native_session_close_reclaim": {"enabled": True},
            "writer_error": None, "record_output_token_ids": False,
            "write_back_batch": {"max_nodes": 32},
        },
        "manifest": {
            "arrival_schedule": deepcopy(plan["arrival_schedule"]),
            "arrival_schedule_sha256": plan["arrival_schedule_sha256"],
            "client_inflight_root_window": 156, "config": {"concurrency": 156},
        },
        "capacity": {"full": 10, "mamba": 10},
        "reference_capacity": {"full": 10, "mamba": 10},
        "plan": plan,
    }


def test_startup_requires_actual_feature_evidence_and_matching_configuration(startup):
    assert baseline.startup_configuration(**startup)["enable_beliefkv_admission"] is False


@pytest.mark.parametrize(
    "change",
    ("disabled_gc", "missing_gc", "admission", "batch", "arrival", "concurrency",
     "capacity", "writer", "token_collection"),
)
def test_startup_rejects_silent_inactivity_and_unmatched_execution(startup, change):
    if change == "disabled_gc":
        startup["telemetry"]["native_session_close_reclaim"]["enabled"] = False
    elif change == "missing_gc":
        del startup["telemetry"]["native_session_close_reclaim"]
    elif change == "admission":
        startup["serving"]["enable_beliefkv_admission"] = True
    elif change == "batch":
        startup["telemetry"]["write_back_batch"]["max_nodes"] = 1
    elif change == "arrival":
        startup["manifest"]["arrival_schedule"][0]["offset_seconds"] = 999
    elif change == "concurrency":
        startup["manifest"]["config"]["concurrency"] = 108
    elif change == "capacity":
        startup["capacity"]["mamba"] = 1
    elif change == "writer":
        startup["telemetry"]["writer_error"] = "failed"
    else:
        startup["telemetry"]["record_output_token_ids"] = True
    with pytest.raises(ValueError):
        baseline.startup_configuration(**startup)


def test_postprocessing_keeps_incomplete_and_guard_labels_and_collects_native_artifacts(
    tmp_path, monkeypatch, reference,
):
    run = tmp_path / "run"
    arm = run / "native"
    client = arm / "client_156"
    client.mkdir(parents=True)
    (arm / "server").mkdir()
    plan = make_plan(reference)
    rows = [
        {"instance_id": identity, "outcome": "completed", "native_agent_jct_eligible": True}
        for identity in reference["workload_instance_ids_in_manifest_order"]
    ]
    rows[0].update(outcome="incomplete", native_agent_jct_eligible=False)
    rows[1]["native_agent_jct_eligible"] = False
    summary = {"workflow_count": 156, "workflows": rows}
    baseline.write_json(client / "summary.json", summary)
    baseline.write_json(run / "ab_plan.json", plan)
    baseline.write_json(arm / "server/native_telemetry_status.json", {
        "native_session_close_reclaim": {"enabled": True, "counts": {"discarded_nodes": 2}},
    })
    monkeypatch.setattr(baseline, "RUN", run)
    monkeypatch.setattr(baseline, "FINAL", tmp_path / "final.json")
    commands = []

    def execute(argv, **kwargs):
        commands.append(argv)
        assert kwargs["env"]["CUDA_VISIBLE_DEVICES"] == "999"
        if "--output" in argv:
            output = Path(argv[argv.index("--output") + 1])
            baseline.write_json(output, {
                "same_task_set": True, "same_physical_pool_capacity": True,
            })
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(baseline.subprocess, "run", execute)
    baseline.postprocess()
    assert baseline.load(client / "summary.json") == summary
    assert baseline.load(baseline.FINAL)["outcomes"] == {"completed": 155, "incomplete": 1}
    assert {Path(argv[1]).name for argv in commands} >= {
        "render_p6_execution_timeline.py", "compare_native_policy_runs.py",
        "audit_native_memory_opportunity.py",
    }


@pytest.mark.parametrize("invalid", ("missing", "duplicate"))
def test_postprocessing_rejects_missing_or_duplicated_tasks_before_analysis(
    tmp_path, monkeypatch, reference, invalid,
):
    client = tmp_path / "native/client_156"
    client.mkdir(parents=True)
    rows = [{"instance_id": identity} for identity in reference["workload_instance_ids_in_manifest_order"]]
    if invalid == "missing":
        rows.pop()
    else:
        rows[-1] = rows[0]
    baseline.write_json(client / "summary.json", {"workflow_count": 156, "workflows": rows})
    baseline.write_json(tmp_path / "ab_plan.json", make_plan(reference))
    monkeypatch.setattr(baseline, "RUN", tmp_path)
    monkeypatch.setattr(baseline.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("analysis started"))
    with pytest.raises(ValueError, match="every planned task"):
        baseline.postprocess()
