from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from scripts.run_tool_path_followup import (
    AuditCalls, choice_command, followup_configuration, make_plan, promising, review_ready,
    same_owner,
)


def owner():
    return {"pid": 123, "start_ticks": "456", "argv": ["python", "current_queue.py"], "state": "S"}


def test_only_the_exact_live_preceding_process_is_accepted():
    assert same_owner(owner(), owner())


@pytest.mark.parametrize("field,value", (
    ("pid", 999), ("start_ticks", "789"), ("argv", ["python", "foreign.py"]),
    ("state", "Z"), ("state", "X"),
))
def test_changed_or_dead_owner_is_rejected(field, value):
    assert not same_owner(owner(), {**owner(), field: value})


def test_live_review_never_counts_as_complete_even_after_all18_receipts():
    assert not review_ready({
        "status": "waiting_fixed_18_oracle", "measured_completed": 18, "process": owner(),
    }, owner())


@pytest.mark.parametrize("status", (
    "oracle_complete_no_promising_gain", "oracle_complete_separate_learned_preparation_required",
))
def test_completed_review_accepts_the_still_live_bridge_owner(status):
    assert review_ready({"status": status, "measured_completed": 18, "process": owner()}, owner())


def test_partial_completion_and_changed_review_identity_are_rejected():
    state = {
        "status": "oracle_complete_no_promising_gain", "measured_completed": 17,
        "process": owner(),
    }
    with pytest.raises(ValueError, match="all18"):
        review_ready(state, owner())
    with pytest.raises(ValueError, match="owner changed"):
        review_ready(state, {**owner(), "start_ticks": "changed"})


def test_failed_review_does_not_start_another_method():
    with pytest.raises(ValueError, match="review failed"):
        review_ready({"status": "stopped_preserve_failed_run", "process": owner()}, owner())


def arguments():
    root = Path("/qualified")
    command = [
        "taskset", "-c", "0", "nice", "-n", "19", "python",
        str(root / "scripts/audit_workflow_combination_choices.py"),
        "--registration", "oracle.reg", "--control", "full.reg", "--cohort", "fixed18",
        "--complete", "--policy-kind", "oracle", "--output", "choices.json",
        "--model-work-reference", "learned-reference",
    ]
    return root, command


def test_global_collector_removes_only_inapplicable_combination_arguments():
    root, old = arguments()
    new = choice_command(old, root, {"registration": "oracle.reg"}, "full.reg", "fixed18", "learned-reference")
    assert str(root / "scripts/audit_ranked_oracle_service.py") in new
    assert "--policy-kind" not in new and "--model-work-reference" not in new
    assert new[new.index("--output") + 1] == "choices.json"
    assert new[:7] == old[:7] and "--complete" in new
    assert "--model-work-reference" in old


@pytest.mark.parametrize("flag", ("--registration", "--control", "--cohort", "--policy-kind", "--model-work-reference"))
def test_foreign_collector_binding_is_never_silently_rewritten(flag):
    root, command = arguments()
    command[command.index(flag) + 1] = "changed"
    with pytest.raises(ValueError, match="declared method or fixed work"):
        choice_command(command, root, {"registration": "oracle.reg"}, "full.reg", "fixed18", "learned-reference")


def test_other_CPU_observers_and_process_commands_pass_through():
    seen = []
    plan = {
        "cohort": "fixed18",
        "stages": {
            "oracle": {"registration": "oracle.reg"}, "full": {"registration": "full.reg"},
            "learned": {"configuration": "learned-reference"},
        },
    }
    calls = AuditCalls(NS(Popen=lambda command, **kwargs: seen.append((command, kwargs))), Path("/qualified"), plan)
    command = ["python", "stop_after_measured_cohort.py", "--watch", "cohort.reg"]
    calls.Popen(command, env={"CUDA_VISIBLE_DEVICES": "99999"})
    assert seen == [(command, {"env": {"CUDA_VISIBLE_DEVICES": "99999"}})]


def accepted():
    return {
        "checks": {
            "fixed_measured_mean_JCT_gain": True,
            "common_window_output_throughput_noninferior": True,
            "background_covers_both_measured_tails": True,
        },
    }, {
        "complete_measured_execution_verified": True,
        "counts": {"selected_set_changes": 1, "newly_selected_with_bounded_useful_service": 1},
    }


def test_global_gain_requires_complete_work_and_bounded_actual_service():
    comparison, choices = accepted()
    assert promising(comparison, choices)
    choices["counts"]["newly_selected_with_bounded_useful_service"] = 0
    assert not promising(comparison, choices)
    choices["counts"]["newly_selected_with_bounded_useful_service"] = 1
    choices["complete_measured_execution_verified"] = False
    assert not promising(comparison, choices)


@pytest.mark.parametrize("check", (
    "fixed_measured_mean_JCT_gain", "common_window_output_throughput_noninferior",
    "background_covers_both_measured_tails",
))
def test_changed_sets_do_not_override_a_failed_performance_gate(check):
    comparison, choices = accepted()
    comparison["checks"][check] = False
    assert not promising(comparison, choices)


def test_plan_reuses_Full_and_binds_only_one_prepared_next_method(tmp_path):
    previous = {
        "stages": {
            "full": {"reuse_completed_execution": True, "registration": "full.reg"},
            "oracle": {"registration": "capacity.reg", "existing_watcher": owner()},
            "learned": {"configuration": "learned-reference"},
        },
        "configurations": {"oracle": "old.json"},
    }
    ready = {
        "scope": {"same_fixed133_initial20_mean60_Host400_fixed18": True},
        "previous_Oracle_registration": "capacity.reg",
        "previous_existing_queue": "/previous/plan.json", "registrar_was_called": False,
        "registration": "tool.reg", "run_root": "/new/raw",
        "register_command": ["python", "register.py", "--experiment-config", "prepared.json"],
    }
    before = deepcopy(previous)
    plan = make_plan(previous, ready, tmp_path, tmp_path / "oracle.json")
    assert plan["stages"]["full"] == before["stages"]["full"]
    assert plan["stages"]["oracle"]["registration"] == "tool.reg"
    assert plan["stages"]["oracle"]["existing_watcher"] is None
    assert plan["previous_registration"] == "capacity.reg"
    assert plan["previous_execution_plan"] == "/previous/execution_plan.json"
    assert plan["previous_memory_resident_owner"] == "/previous/resident_owner.json"
    assert plan["only_next_GPU_stage"] == "oracle"
    assert plan["no_automatic_learned_GPU_stage"] is True
    assert previous == before


def test_single_method_stop_metadata_does_not_change_serving_configuration():
    old = {
        "common_environment": {
            "WORKFLOW_COMBINATION_ADMISSION": "0", "TOOL_PREFETCH": "0",
            "PREDICTED_TOOL_OFFLOAD": "1", "LAYER_HANDOFF": "0",
        },
        "comparison_contract": {"workflow_work_ranking": True},
    }
    before = deepcopy(old)
    new = followup_configuration(old)
    assert new["common_environment"] == old["common_environment"]
    assert new["comparison_contract"]["tool_boundary_oracle_only_followup"] is True
    assert new["comparison_contract"]["legacy_oracle_only_flag_does_not_enable_tool_boundary_preemption"] is True
    assert old == before
