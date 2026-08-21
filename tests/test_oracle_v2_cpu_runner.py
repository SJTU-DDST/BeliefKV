from __future__ import annotations

from beliefkv.oracle.cpu_estimator import (
    CPUOracleArm,
    PlannerOverhead,
    ServiceEnvelope,
    WholeRunExecutionPolicy,
)
from scripts.run_cpu_counterfactual_oracle import (
    _gain_summary,
    _joint_opportunity_gate,
    _select_whole_run_rows,
)


def _row(
    candidate_id: str,
    arm: CPUOracleArm,
    policy: WholeRunExecutionPolicy,
    makespan_ms: float,
) -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "arm": arm.value,
        "execution_policy": policy.value,
        "service_envelope": ServiceEnvelope.NOMINAL.value,
        "planner_overhead": PlannerOverhead.MEASURED_FASTPATH.value,
        "makespan_ms": makespan_ms,
        "workflows_per_hour": 3_600_000.0 / makespan_ms,
        "transfer_count": 0,
    }


def test_whole_run_selection_enforces_c1_and_c3_dominance() -> None:
    rows = [
        _row(
            "c0_observed",
            CPUOracleArm.C0_CURRENT,
            WholeRunExecutionPolicy.C0_OBSERVED,
            100.0,
        ),
        _row(
            "c2_causal_kv",
            CPUOracleArm.C2_KV,
            WholeRunExecutionPolicy.C0_OBSERVED,
            90.0,
        ),
    ]
    for policy in (
        WholeRunExecutionPolicy.PACKAGE_OBSERVED,
        WholeRunExecutionPolicy.MIN_REMAINING_DEMAND,
        WholeRunExecutionPolicy.ACTION_UNLOCK,
        WholeRunExecutionPolicy.MAX_BATCH_FILL,
    ):
        rows.append(_row(f"c1:{policy.value}", CPUOracleArm.C1_AGENT, policy, 110.0))
        rows.append(_row(f"c3:{policy.value}", CPUOracleArm.C3_JOINT, policy, 105.0))

    selected = {row["arm"]: row for row in _select_whole_run_rows(rows)}

    assert selected[CPUOracleArm.C1_AGENT.value]["makespan_ms"] == 100.0
    assert selected[CPUOracleArm.C1_AGENT.value]["whole_run_selected_candidate_id"] == "c0_observed"
    assert selected[CPUOracleArm.C3_JOINT.value]["makespan_ms"] == 90.0
    assert selected[CPUOracleArm.C3_JOINT.value]["whole_run_selected_candidate_id"] == "c2_causal_kv"


def test_gain_summary_reports_nonnegative_interval() -> None:
    rows = []
    for arm, makespan in (
        (CPUOracleArm.C0_CURRENT, 100.0),
        (CPUOracleArm.C1_AGENT, 90.0),
        (CPUOracleArm.C2_KV, 100.0),
        (CPUOracleArm.C3_JOINT, 90.0),
    ):
        rows.append(
            {
                "arm": arm.value,
                "service_envelope": ServiceEnvelope.NOMINAL.value,
                "planner_overhead": PlannerOverhead.MEASURED_FASTPATH.value,
                "makespan_ms": makespan,
            }
        )
    for row in tuple(rows):
        duplicate = dict(row)
        duplicate["service_envelope"] = ServiceEnvelope.SLOW.value
        duplicate["makespan_ms"] = 100.0
        rows.append(duplicate)

    summary = _gain_summary(rows)

    assert summary[CPUOracleArm.C1_AGENT.value]["direction"] == "nonnegative"
    assert summary[CPUOracleArm.C2_KV.value]["direction"] == "zero"
    assert summary["c3_joint_synergy"]["direction"] == "zero"


def test_joint_gate_does_not_splice_metrics_across_rows() -> None:
    rows = []
    for envelope, windows, byte_ms, blocked_ms in (
        (ServiceEnvelope.SLOW, 10, 0.0, 0.0),
        (ServiceEnvelope.NOMINAL, 0, 100.0, 0.0),
        (ServiceEnvelope.GRAPH32_SENSITIVITY, 0, 0.0, 100.0),
    ):
        rows.append(
            {
                "arm": CPUOracleArm.C0_CURRENT.value,
                "service_envelope": envelope.value,
                "planner_overhead": PlannerOverhead.MEASURED_FASTPATH.value,
                "whole_run_selected_candidate_id": "c0_observed",
                "stall_free_round_trip_window_count": windows,
                "stall_free_unique_victim_byte_ms": byte_ms,
                "stall_free_blocked_beneficiary_work_ms": blocked_ms,
            }
        )

    gate = _joint_opportunity_gate(rows)

    assert gate["passed"] is False
    assert gate["qualifying_row_id"] is None
    assert gate["source_row_id"].startswith(
        "c0_current:nominal_p50_graph16:measured_fastpath"
    )
