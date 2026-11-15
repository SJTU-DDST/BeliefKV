import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit_ready_capacity_service.py"
SPEC = importlib.util.spec_from_file_location("capacity_service_audit", SCRIPT)
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


def key(owner):
    return {
        "request_id": "request-" + owner, "root_workflow_id": owner,
        "invocation_id": owner, "context_id": "context-" + owner,
        "context_epoch": 2, "attempt_id": 0, "session_id": "session-" + owner,
        "session_generation": 3,
    }


def identity(row, *, grant=False):
    assert grant
    return tuple(row[name] for name in key("focus"))


def replay(row):
    return row["capacity_accumulation"]["decision"]


POLICY = SimpleNamespace(identity=identity, replay_capacity_hold=replay)


def hold(when=100., *, continuing=False):
    decision = {
        "focus": "focus", "selected": [], "held_reference": ["long"],
        "focus_logical_gap_tokens": 20, "held_reference_tokens": 100,
        "continuing_existing_hold": continuing,
    }
    return {
        "event": "workflow_combination_selection", "source": "periodic_decay_resume",
        "selection_policy": "maximal_benefit",
        "physical_capacity_credit": False,
        "decision": {**decision, "reason": "ready_capacity_accumulation"},
        "metadata_and_selection_cpu_ns": 1000, "metadata_and_selection_wall_ns": 1200,
        "capacity_accumulation": {
            "decision": decision, "max_wait_ms": 2000, "cooldown_ms": 30000,
            "decision_monotonic_s": when, "started_monotonic_s": 100.,
            "deadline_monotonic_s": 102., "reference": ["long"],
            "keys": {"focus": key("focus"), "long": key("long")},
            "logical_capacity_tokens": 100, "running_width": 2,
        },
    }


def grant(owner="focus", when=101., kind="agent_dispatch_free_capacity_admitted"):
    return {
        "event": kind, "monotonic_s": when, "programs": [{"key": key(owner)}],
    }


def episode(owner="focus", sequence=1, when=101., *, positive=True):
    return {
        "key": identity(key(owner), grant=True), "journal_sequence": sequence,
        "selection_ms": when * 1000., "selection_retained": True,
        "following_native_service_ms": 1.,
        "first_new_useful_launch_ms": 1. if positive else None,
        "first_new_useful_service_ms": 2. if positive else None,
        "first_new_useful_completion_ms": 3. if positive else None,
        "new_service_before_next_control_or_intervention": positive,
    }


def inspect(rows, episodes=()):
    return AUDIT.inspect(enumerate(rows), {"ready_grant_episodes": episodes}, {"focus"}, POLICY)


def test_hold_is_not_counted_as_new_service_and_tracks_withheld_wait():
    rows = [hold(), grant(), grant("long", 103.)]
    result = inspect(rows, [episode(), episode("long", 2, 103.)])
    cycle = result["cycles"][0]
    assert result["counts"]["focus_cycles_with_later_positive_service"] == 1
    assert cycle["focus_followup"]["first_later_positive_progress"]["hold_to_positive_service_ms"] == 1002.
    withheld = cycle["withheld_requests"][0]
    assert withheld["followup"]["first_later_grant"]["observed_delay_from_hold_ms"] == 3000.
    assert result["scope"]["withheld_wait_not_counterfactual_added_delay"]
    assert not result["performance_accepted"]


def test_zero_progress_service_cannot_validate_a_focus():
    result = inspect([hold(), grant()], [episode(positive=False)])
    assert result["counts"]["focus_cycles_with_later_grant"] == 1
    assert result["counts"]["focus_cycles_with_later_positive_service"] == 0


def test_later_grant_service_does_not_validate_an_earlier_grant():
    result = inspect(
        [hold(), grant(), grant(when=103.)],
        [episode(positive=False), episode(sequence=2, when=103.)],
    )
    follow = result["cycles"][0]["focus_followup"]
    assert follow["first_later_grant"]["journal_sequence"] == 1
    assert follow["first_later_positive_progress"]["journal_sequence"] == 2
    assert not follow["first_later_positive_progress"]["progress_in_original_first_later_grant"]


def test_foreign_epoch_and_pre_hold_grants_do_not_establish_followup():
    other = grant()
    other["programs"][0]["key"]["context_epoch"] += 1
    result = inspect([grant(when=99.), hold(), other], [episode(sequence=0, when=99.)])
    assert result["cycles"][0]["focus_followup"]["first_later_grant"] is None


def test_unobserved_join_progress_remains_unknown():
    result = inspect([hold(), grant(kind="agent_join_free_capacity_admitted")])
    follow = result["cycles"][0]["focus_followup"]
    assert follow["first_later_grant"]["event"] == "agent_join_free_capacity_admitted"
    assert follow["first_later_positive_progress"] is None
    assert follow["unobserved_progress_grants"] == 1


def test_fixed_deadline_continuations_are_one_cycle():
    result = inspect([hold(), hold(101., continuing=True)])
    assert result["counts"]["cycles"] == 1
    assert result["counts"]["hold_decisions_replayed"] == 2
    assert len(result["cycles"][0]["withheld_requests"]) == 1


@pytest.mark.parametrize("change", ("missing_start", "duplicate_start", "cooldown"))
def test_invalid_cycle_histories_are_rejected(change):
    rows = [hold(), hold()]
    if change == "missing_start":
        rows = [hold(101., continuing=True)]
    elif change == "cooldown":
        later = copy.deepcopy(rows[1]["capacity_accumulation"])
        later.update(decision_monotonic_s=110., started_monotonic_s=110., deadline_monotonic_s=112.)
        rows[1]["capacity_accumulation"] = later
    with pytest.raises(ValueError):
        inspect(rows)


def test_empty_hold_population_is_explicitly_no_evidence_of_gain():
    result = inspect([grant()], [episode(sequence=0)])
    assert result["counts"]["cycles"] == 0
    assert result["counts"]["distinct_focus_request_attempts"] == 0
    assert result["cycles"] == []
    assert result["hold_selection_thread_cpu_ms"] == 0
    assert not result["performance_accepted"]


def test_executed_runtime_hold_replays_with_the_real_policy(monkeypatch, tmp_path):
    root = Path("/home/longhao/experiment/.worktrees/beliefkv-ready-capacity-guard-20261115")
    policy = AUDIT.load_policy(root)
    from tests.test_workflow_capacity_accumulation import runtime_fixture

    runtime, admission, _control, _busy, clock, records = runtime_fixture(monkeypatch, tmp_path)
    try:
        admission.tick(now=clock[0])
        row = next(item for item in records if item.get("capacity_accumulation"))
        result = AUDIT.inspect(
            [(0, row)], {"ready_grant_episodes": []}, set(), policy,
        )
        assert result["counts"]["hold_decisions_replayed"] == 1
        assert result["counts"]["focus_cycles_with_later_positive_service"] == 0
        changed = copy.deepcopy(row)
        changed["decision"]["selected"] = [
            *changed["decision"]["selected"], changed["capacity_accumulation"]["decision"]["focus"],
        ]
        with pytest.raises(ValueError, match="does not reproduce"):
            AUDIT.inspect([(0, changed)], {"ready_grant_episodes": []}, set(), policy)
    finally:
        runtime._opportunity_writer = None
        runtime.close()


def test_independent_completed_proof_accepts_a_legacy_progress_marker():
    cohort = {"measured_workflow_ids": ["workflow-" + str(index) for index in range(18)]}
    proof = {
        "registration_sha256": "registration", "cohort_sha256": "cohort", "cohort": cohort,
        "protocol": "all_fixed_measured_workflows_completed", "actual_owned_exit_verified": True,
    }
    progress = {
        "registration_sha256": "registration",
        "complete_measured_work_and_owned_exit_verified": False,
    }
    AUDIT.validate_completion(progress, proof, cohort, "registration", "cohort")
    proof["actual_owned_exit_verified"] = False
    with pytest.raises(ValueError, match="actual native exit"):
        AUDIT.validate_completion(progress, proof, cohort, "registration", "cohort")


def test_journal_filter_retains_sequence_and_both_supported_json_formats(tmp_path):
    import hashlib

    rows = [
        json_row for json_row in (
            b'{"event":"unrelated"}\n',
            b'{"event": "workflow_combination_selection"}\n',
            b'{"event":"agent_join_free_capacity_admitted"}\n',
        )
    ]
    path = tmp_path / "journal.jsonl"
    raw = b"".join(rows)
    path.write_bytes(raw)
    prefix = {"complete_prefix_bytes": len(raw), "complete_prefix_sha256": hashlib.sha256(raw).hexdigest()}
    result = list(AUDIT.sealed_records(path, prefix))
    assert [sequence for sequence, _row in result] == [1, 2]
    prefix["complete_prefix_sha256"] = "changed"
    with pytest.raises(ValueError, match="differs"):
        list(AUDIT.sealed_records(path, prefix))
