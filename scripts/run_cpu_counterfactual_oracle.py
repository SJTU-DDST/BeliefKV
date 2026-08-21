#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import defaultdict
import gzip
import json
import multiprocessing
from pathlib import Path
import sys
import time
from typing import Mapping


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from beliefkv.oracle.contracts import (
    FrozenAgentDemand,
    FrozenDemandProvenance,
)
from beliefkv.oracle.cpu_estimator import (
    CPUCounterfactualOracleEstimator,
    CPUOracleArm,
    CPUOracleConfig,
    PlannerOverhead,
    ServiceEnvelope,
    WholeRunExecutionPolicy,
)
from beliefkv.oracle.physical_sidecar import (
    FrozenPhysicalCall,
    FrozenPhysicalSidecar,
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the CPU Counterfactual Oracle Estimate (not GPU O0-O3)."
    )
    parser.add_argument("--input-dir", type=Path, action="append", required=True)
    parser.add_argument("--observed-run-dir", type=Path, action="append", default=[])
    parser.add_argument("--gpu-service-artifact", type=Path, required=True)
    parser.add_argument("--transfer-summary", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--hbm-tokens", type=int, default=850_000)
    parser.add_argument("--host-gib", type=int, default=96)
    parser.add_argument("--max-running", type=int, default=32)
    parser.add_argument("--prefill-chunk", type=int, default=16_384)
    parser.add_argument("--c0-validation-only", action="store_true")
    parser.add_argument("--matrix-workers", type=int, default=1)
    return parser.parse_args()


def _read_bytes(path: Path) -> bytes:
    if path.suffix == ".gz":
        with gzip.open(path, "rb") as source:
            return source.read()
    return path.read_bytes()


def _load_input(path: Path) -> tuple[FrozenAgentDemand, FrozenPhysicalSidecar, dict[str, float]]:
    root = path.expanduser().resolve()
    truth = FrozenAgentDemand.from_json_bytes(
        _read_bytes(root / "frozen_agent_demand_v2.json")
    )
    sidecar_candidates = (
        root / "frozen_physical_sidecar_v1.json.gz",
        root / "frozen_physical_sidecar_v1.json",
    )
    sidecar_path = next((item for item in sidecar_candidates if item.exists()), None)
    if sidecar_path is None:
        raise FileNotFoundError("frozen physical sidecar is missing")
    sidecar = FrozenPhysicalSidecar.from_json_bytes(_read_bytes(sidecar_path))
    manifest = json.loads((root / "export_manifest.json").read_text(encoding="utf-8"))
    releases = {
        str(key): float(value)
        for key, value in manifest["workflow_release_ms"].items()
    }
    return truth, sidecar, releases


def _merge_inputs(
    values: list[tuple[FrozenAgentDemand, FrozenPhysicalSidecar, dict[str, float]]]
) -> tuple[FrozenAgentDemand, FrozenPhysicalSidecar, dict[str, float]]:
    if len(values) == 1:
        return values[0]
    workloads = [
        {item.key.workload_instance for item in truth.invocations}
        for truth, _, _ in values
    ]
    for index, left in enumerate(workloads):
        if any(left.intersection(right) for right in workloads[index + 1 :]):
            raise ValueError("load-scaling inputs contain duplicate workflow instances")
    provenance = FrozenDemandProvenance(
        truth_id="h200-bf16-oracle-v2-cpu-load-scaling",
        source_trace_id="+".join(item.provenance.source_trace_id for item, _, _ in values),
        workload_manifest_id="cpu-overlay-of-pre-frozen-inputs",
        model_revision=values[0][0].provenance.model_revision,
        tokenizer_revision=values[0][0].provenance.tokenizer_revision,
        runtime_revision=values[0][0].provenance.runtime_revision,
        harness_revision=values[0][0].provenance.harness_revision,
        exporter_revision="oracle-v2-1.5-overlay",
    )
    truth = FrozenAgentDemand(
        provenance=provenance,
        invocations=tuple(
            item for source, _, _ in values for item in source.invocations
        ),
        joins=tuple(item for source, _, _ in values for item in source.joins),
    )
    calls = []
    ordinal = 0
    for _, sidecar, _ in values:
        for item in sidecar.calls:
            calls.append(
                FrozenPhysicalCall(
                    invocation=item.invocation,
                    call_ordinal=item.call_ordinal,
                    trace_request_ordinal=ordinal,
                    runtime_context_epoch=item.runtime_context_epoch,
                    observed_cache_hit_tokens=item.observed_cache_hit_tokens,
                    observed_unique_growth_bytes=item.observed_unique_growth_bytes,
                    prompt_token_symbols=item.prompt_token_symbols,
                    cache_commit_token_symbols=item.cache_commit_token_symbols,
                    partial_cache_commit_token_symbols=(
                        item.partial_cache_commit_token_symbols
                    ),
                )
            )
            ordinal += 1
    sidecar = FrozenPhysicalSidecar(
        truth_id=truth.truth_id,
        truth_digest=truth.truth_digest,
        source_trace_id=provenance.source_trace_id,
        kv_bytes_per_token=values[0][1].kv_bytes_per_token,
        initial_radix_state="empty_server_boot",
        calls=tuple(calls),
    )
    # Each source run starts at t=0. Anonymous token bijections are independent;
    # preserving them deliberately avoids fabricating cross-run prefix equality.
    releases = {key: value for _, _, source in values for key, value in source.items()}
    return truth, sidecar, releases


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(quantile * (len(ordered) - 1)))
    return ordered[index]


def _runtime_service_profile(
    run_dirs: list[Path],
) -> dict[str, dict[int, dict[str, float]]]:
    samples: dict[str, dict[int, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for run_dir in run_dirs:
        audit_path = (
            run_dir.expanduser().resolve() / "server" / "runtime_audit.jsonl"
        )
        with audit_path.open(encoding="utf-8") as source:
            for line in source:
                if '"gpu_service_sample"' not in line:
                    continue
                row = json.loads(line)
                if row.get("event") != "gpu_service_sample":
                    continue
                phase = str(row.get("calibration_kind", row.get("phase", "")))
                batch = int(row.get("batch_size", 0))
                elapsed = float(
                    row.get("service_elapsed_ms", row.get("elapsed_ms", 0.0))
                )
                if phase in {"prefill", "decode"} and batch > 0 and elapsed > 0:
                    samples[phase][batch].append(elapsed)
    return {
        phase: {
            batch: {
                "sample_count": float(len(values)),
                "mean_ms": sum(values) / len(values),
                "p50_ms": _percentile(values, 0.50),
                "p95_ms": _percentile(values, 0.95),
            }
            for batch, values in sorted(rows.items())
        }
        for phase, rows in samples.items()
    }


def _observed_metrics(run_dir: Path) -> dict[str, object]:
    root = run_dir.expanduser().resolve()
    summary_path = root / "workloads" / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    audit_path = root / "server" / "runtime_audit.jsonl"
    batch_count = 0
    batch_weight = 0
    with audit_path.open(encoding="utf-8") as source:
        for line in source:
            if '"gpu_service_sample"' not in line:
                continue
            row = json.loads(line)
            if (
                row.get("event") != "gpu_service_sample"
                or row.get("calibration_kind", row.get("phase")) != "decode"
            ):
                continue
            batch = int(row.get("batch_size", 0))
            if batch > 0:
                batch_count += 1
                batch_weight += batch
    runtime_audit = summary["server"]["runtime_audit"]
    return {
        "makespan_ms": float(summary["duration_seconds"]) * 1000,
        "resident_peak_pressure": float(summary["sglang"]["max_resident_pressure"]),
        "decode_batch_mean": batch_weight / batch_count if batch_count else 0.0,
        "strategic_kv_transfer_bytes": int(
            runtime_audit.get("acknowledged_kv_transfer_bytes", 0)
        ),
        "workflow_count": int(summary["workflow_count"]),
    }


def _validation(simulated: Mapping[str, object], observed: Mapping[str, object]) -> dict[str, object]:
    makespan_error = abs(
        float(simulated["makespan_ms"]) - float(observed["makespan_ms"])
    ) / float(observed["makespan_ms"])
    pressure_error = abs(
        float(simulated["hbm_peak_pressure"])
        - float(observed["resident_peak_pressure"])
    )
    observed_batch = float(observed["decode_batch_mean"])
    batch_error = abs(float(simulated["decode_batch_mean"]) - observed_batch) / max(
        observed_batch, 1e-9
    )
    transfer_match = (
        int(simulated["d2h_bytes"]) == 0
        and int(simulated["h2d_bytes"]) == 0
        and int(observed["strategic_kv_transfer_bytes"]) == 0
    )
    passed = (
        makespan_error <= 0.30
        and pressure_error <= 0.15
        and batch_error <= 0.20
        and transfer_match
    )
    return {
        "passed": passed,
        "makespan_relative_error": makespan_error,
        "resident_pressure_absolute_error": pressure_error,
        "decode_batch_mean_relative_error": batch_error,
        "zero_strategic_transfer_reproduced": transfer_match,
        "thresholds": {
            "makespan_relative_error_max": 0.30,
            "resident_pressure_absolute_error_max": 0.15,
            "decode_batch_mean_relative_error_max": 0.20,
        },
    }


def _gain_direction(low: float, high: float) -> str:
    if low > 0.0:
        return "positive"
    if high < 0.0:
        return "negative"
    if low == 0.0 and high == 0.0:
        return "zero"
    if low >= 0.0:
        return "nonnegative"
    if high <= 0.0:
        return "nonpositive"
    return "inconclusive"


def _gain_summary(rows: list[dict[str, object]]) -> dict[str, object]:
    grouped: dict[tuple[str, str], dict[str, float]] = {}
    for row in rows:
        key = (str(row["service_envelope"]), str(row["planner_overhead"]))
        grouped.setdefault(key, {})[str(row["arm"])] = float(row["makespan_ms"])
    gains: dict[str, list[float]] = {arm.value: [] for arm in CPUOracleArm if arm != CPUOracleArm.C0_CURRENT}
    synergies = []
    for values in grouped.values():
        if len(values) != 4:
            continue
        c0 = values[CPUOracleArm.C0_CURRENT.value]
        for arm in (CPUOracleArm.C1_AGENT, CPUOracleArm.C2_KV, CPUOracleArm.C3_JOINT):
            gains[arm.value].append(c0 / values[arm.value] - 1.0)
        synergies.append(
            min(
                values[CPUOracleArm.C1_AGENT.value],
                values[CPUOracleArm.C2_KV.value],
            )
            / values[CPUOracleArm.C3_JOINT.value]
            - 1.0
        )
    result = {}
    for arm, values in gains.items():
        if not values:
            continue
        low, high = min(values), max(values)
        result[arm] = {
            "estimated_gain_interval": [low, high],
            "direction": _gain_direction(low, high),
        }
    if synergies:
        low, high = min(synergies), max(synergies)
        result["c3_joint_synergy"] = {
            "estimated_gain_interval": [low, high],
            "direction": _gain_direction(low, high),
        }
    return result


def _joint_opportunity_gate(rows: list[dict[str, object]]) -> dict[str, object]:
    current_rows = [
        row for row in rows if row.get("arm") == CPUOracleArm.C0_CURRENT.value
    ]

    def row_id(row: Mapping[str, object]) -> str:
        return ":".join(
            (
                str(row["arm"]),
                str(row["service_envelope"]),
                str(row["planner_overhead"]),
                str(row.get("whole_run_selected_candidate_id", "c0_observed")),
            )
        )

    row_audit = []
    for row in current_rows:
        windows = int(row.get("stall_free_round_trip_window_count", 0))
        byte_ms = float(row.get("stall_free_unique_victim_byte_ms", 0.0))
        blocked_work_ms = float(
            row.get("stall_free_blocked_beneficiary_work_ms", 0.0)
        )
        row_audit.append(
            {
                "row_id": row_id(row),
                "service_envelope": row["service_envelope"],
                "planner_overhead": row["planner_overhead"],
                "stall_free_round_trip_window_count": windows,
                "stall_free_unique_victim_byte_ms": byte_ms,
                "stall_free_blocked_beneficiary_work_ms": blocked_work_ms,
                "passed": windows >= 10 and byte_ms > 0.0 and blocked_work_ms > 0.0,
            }
        )

    qualification = next(
        (
            item
            for item in row_audit
            if item["service_envelope"] == ServiceEnvelope.NOMINAL.value
            and item["planner_overhead"] == PlannerOverhead.MEASURED_FASTPATH.value
        ),
        None,
    )
    if qualification is None:
        return {
            "passed": False,
            "reason": "nominal_measured_c0_row_missing",
            "qualifying_row_id": None,
            "row_audit": row_audit,
        }
    source = next(
        row for row in current_rows if row_id(row) == qualification["row_id"]
    )
    passed = bool(qualification["passed"])
    return {
        "source_arm": CPUOracleArm.C0_CURRENT.value,
        "source_row_id": qualification["row_id"],
        "qualifying_row_id": qualification["row_id"] if passed else None,
        "qualification_service_envelope": ServiceEnvelope.NOMINAL.value,
        "qualification_planner_overhead": PlannerOverhead.MEASURED_FASTPATH.value,
        "definition": (
            "parked_migratable_victim && HBM_blocked_ready_beneficiary "
            "&& causal_slack_gt_D2H_plus_H2D_plus_guards "
            "&& future_reentry && host_feasible"
        ),
        "opportunity_pair_count": int(source.get("opportunity_pair_count", 0)),
        "eviction_opportunity_window_count": int(
            source.get("eviction_opportunity_window_count", 0)
        ),
        "stall_free_round_trip_window_count": int(
            qualification["stall_free_round_trip_window_count"]
        ),
        "stall_free_unique_victim_byte_ms": float(
            qualification["stall_free_unique_victim_byte_ms"]
        ),
        "stall_free_blocked_beneficiary_work_ms": float(
            qualification["stall_free_blocked_beneficiary_work_ms"]
        ),
        "net_positive_opportunity_window_count": int(
            source.get("net_positive_opportunity_window_count", 0)
        ),
        "max_oracle_reclaimable_bytes": int(
            source.get("max_oracle_reclaimable_bytes", 0)
        ),
        "victim_future_reentry_ratio": float(
            source.get("opportunity_victim_reentry_ratio", 0.0)
        ),
        "minimum_window_count": 10,
        "passed": passed,
        "row_audit": row_audit,
    }


EXECUTION_ORACLE_POLICIES = (
    WholeRunExecutionPolicy.PACKAGE_OBSERVED,
    WholeRunExecutionPolicy.MIN_REMAINING_DEMAND,
    WholeRunExecutionPolicy.ACTION_UNLOCK,
    WholeRunExecutionPolicy.MAX_BATCH_FILL,
)


def _raw_matrix_tasks() -> list[
    tuple[
        str,
        CPUOracleArm,
        ServiceEnvelope,
        PlannerOverhead,
        WholeRunExecutionPolicy,
    ]
]:
    tasks = []
    for envelope in ServiceEnvelope:
        for overhead in PlannerOverhead:
            tasks.extend(
                [
                    (
                        "c0_observed",
                        CPUOracleArm.C0_CURRENT,
                        envelope,
                        overhead,
                        WholeRunExecutionPolicy.C0_OBSERVED,
                    ),
                    (
                        "c2_causal_kv",
                        CPUOracleArm.C2_KV,
                        envelope,
                        overhead,
                        WholeRunExecutionPolicy.C0_OBSERVED,
                    ),
                ]
            )
            for policy in EXECUTION_ORACLE_POLICIES:
                tasks.append(
                    (
                        f"c1:{policy.value}",
                        CPUOracleArm.C1_AGENT,
                        envelope,
                        overhead,
                        policy,
                    )
                )
                tasks.append(
                    (
                        f"c3:{policy.value}",
                        CPUOracleArm.C3_JOINT,
                        envelope,
                        overhead,
                        policy,
                    )
                )
    return tasks


def _run_estimate(
    estimator: CPUCounterfactualOracleEstimator,
    candidate_id: str,
    arm: CPUOracleArm,
    envelope: ServiceEnvelope,
    overhead: PlannerOverhead,
    execution_policy: WholeRunExecutionPolicy,
) -> dict[str, object]:
    started = time.perf_counter()
    row = estimator.run(arm, envelope, overhead, execution_policy).to_dict()
    row["candidate_id"] = candidate_id
    row["estimator_wall_ms"] = (time.perf_counter() - started) * 1000
    row["derived_equivalent_no_kv_opportunity"] = False
    return row


def _candidate_summary(row: Mapping[str, object]) -> dict[str, object]:
    return {
        "candidate_id": row["candidate_id"],
        "simulated_arm": row["arm"],
        "execution_policy": row["execution_policy"],
        "makespan_ms": row["makespan_ms"],
        "workflows_per_hour": row["workflows_per_hour"],
        "transfer_count": row["transfer_count"],
    }


def _select_whole_run_rows(
    raw_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for row in raw_rows:
        grouped[
            (str(row["service_envelope"]), str(row["planner_overhead"]))
        ].append(row)

    selected_rows = []
    for key in sorted(grouped):
        candidates = grouped[key]
        by_id = {str(row["candidate_id"]): row for row in candidates}
        c0 = by_id["c0_observed"]
        c2 = by_id["c2_causal_kv"]
        c1_candidates = [c0] + [
            row
            for row in candidates
            if str(row["candidate_id"]).startswith("c1:")
        ]
        c3_candidates = c1_candidates + [c2] + [
            row
            for row in candidates
            if str(row["candidate_id"]).startswith("c3:")
        ]
        pools = {
            CPUOracleArm.C0_CURRENT: [c0],
            CPUOracleArm.C1_AGENT: c1_candidates,
            CPUOracleArm.C2_KV: [c2],
            CPUOracleArm.C3_JOINT: c3_candidates,
        }
        selected_for_key = {}
        for arm in CPUOracleArm:
            pool = pools[arm]
            winner = min(
                pool,
                key=lambda row: (
                    float(row["makespan_ms"]),
                    str(row["candidate_id"]),
                ),
            )
            result = dict(winner)
            result["simulated_candidate_arm"] = winner["arm"]
            result["arm"] = arm.value
            result["whole_run_selected_candidate_id"] = winner["candidate_id"]
            result["whole_run_candidate_count"] = len(pool)
            result["whole_run_candidates"] = [
                _candidate_summary(row) for row in pool
            ]
            result["no_op_dominance_enforced"] = arm in {
                CPUOracleArm.C1_AGENT,
                CPUOracleArm.C3_JOINT,
            }
            selected_rows.append(result)
            selected_for_key[arm] = float(result["makespan_ms"])

        if (
            selected_for_key[CPUOracleArm.C1_AGENT]
            > selected_for_key[CPUOracleArm.C0_CURRENT] + 1e-9
        ):
            raise AssertionError("C1 whole-run selection violated C0 dominance")
        if selected_for_key[CPUOracleArm.C3_JOINT] > min(
            selected_for_key[CPUOracleArm.C1_AGENT],
            selected_for_key[CPUOracleArm.C2_KV],
        ) + 1e-9:
            raise AssertionError("C3 whole-run selection violated C1/C2 dominance")
    return selected_rows


_MATRIX_ESTIMATOR: CPUCounterfactualOracleEstimator | None = None


def _run_matrix_task(
    task: tuple[
        str,
        CPUOracleArm,
        ServiceEnvelope,
        PlannerOverhead,
        WholeRunExecutionPolicy,
    ],
) -> dict[str, object]:
    if _MATRIX_ESTIMATOR is None:
        raise RuntimeError("matrix estimator was not initialized before fork")
    return _run_estimate(_MATRIX_ESTIMATOR, *task)


def main() -> int:
    args = _args()
    if args.matrix_workers <= 0:
        raise ValueError("matrix-workers must be positive")
    inputs = [_load_input(item) for item in args.input_dir]
    output = args.output_dir.expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"output directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    config = CPUOracleConfig(
        hbm_capacity_tokens=args.hbm_tokens,
        host_capacity_bytes=args.host_gib * 1024**3,
        max_running_requests=args.max_running,
        prefill_chunk_tokens=args.prefill_chunk,
    )

    validation_rows = []
    runtime_profiles = [
        _runtime_service_profile([item]) for item in args.observed_run_dir
    ]
    if args.observed_run_dir:
        if len(args.observed_run_dir) != len(inputs):
            raise ValueError("observed run count must match input count")
        for index, ((truth, sidecar, releases), run_dir) in enumerate(
            zip(inputs, args.observed_run_dir)
        ):
            estimator = CPUCounterfactualOracleEstimator(
                truth,
                sidecar,
                service_model_path=args.gpu_service_artifact,
                transfer_summary_path=args.transfer_summary,
                workflow_release_ms=releases,
                config=config,
                runtime_service_profile=runtime_profiles[index],
                runtime_service_statistic="mean_ms",
            )
            result = estimator.run(
                CPUOracleArm.C0_CURRENT,
                ServiceEnvelope.NOMINAL,
                PlannerOverhead.MEASURED_FASTPATH,
            ).to_dict()
            observed = _observed_metrics(run_dir)
            validation_rows.append(
                {
                    "input_index": index,
                    "validation_service_statistic": "trace_conditioned_mean_ms",
                    "simulated": result,
                    "observed": observed,
                    "gate": _validation(result, observed),
                }
            )

    all_validation_passed = bool(validation_rows) and all(
        item["gate"]["passed"] for item in validation_rows  # type: ignore[index]
    )
    mode = "estimated_throughput" if all_validation_passed else "structural_bound_only"
    matrix_rows: list[dict[str, object]] = []
    if not args.c0_validation_only:
        truth, sidecar, releases = _merge_inputs(inputs)
        combined_runtime_profile = _runtime_service_profile(
            list(args.observed_run_dir)
        )
        estimator = CPUCounterfactualOracleEstimator(
            truth,
            sidecar,
            service_model_path=args.gpu_service_artifact,
            transfer_summary_path=args.transfer_summary,
            workflow_release_ms=releases,
            config=config,
            runtime_service_profile=combined_runtime_profile,
            cross_run_prefix_identity=(
                "exact_within_one_trace"
                if len(inputs) == 1
                else "unavailable_across_runs_conservative_no_share"
            ),
        )
        tasks = _raw_matrix_tasks()
        if args.matrix_workers == 1:
            raw_matrix_rows = [
                _run_estimate(estimator, *task) for task in tasks
            ]
        else:
            global _MATRIX_ESTIMATOR
            _MATRIX_ESTIMATOR = estimator
            context = multiprocessing.get_context("fork")
            with context.Pool(
                processes=min(args.matrix_workers, len(tasks))
            ) as pool:
                raw_matrix_rows = pool.map(_run_matrix_task, tasks)
            _MATRIX_ESTIMATOR = None
        matrix_rows = _select_whole_run_rows(raw_matrix_rows)
        for row in matrix_rows:
            print(
                f"{row['arm']} {row['service_envelope']} "
                f"{row['planner_overhead']}: "
                f"{row['workflows_per_hour']:.3f} workflow/h",
                flush=True,
            )
    report = {
        "schema_version": 3,
        "result_kind": "CPU Counterfactual Finite-Candidate Oracle Lower Bound",
        "not_gpu_o0_o3_result": True,
        "estimate_mode": mode,
        "input_count": len(inputs),
        "workflow_count": sum(len(item[2]) for item in inputs),
        "config": {
            "hbm_capacity_tokens": config.hbm_capacity_tokens,
            "host_capacity_bytes": config.host_capacity_bytes,
            "max_running_requests": config.max_running_requests,
            "prefill_chunk_tokens": config.prefill_chunk_tokens,
            "transfer_commit_guard_ms": config.transfer_commit_guard_ms,
            "execution_package_width": config.execution_package_width,
            "control_quantum_tokens": config.control_quantum_tokens,
            "matrix_workers": args.matrix_workers,
        },
        "oracle_completeness": "finite_candidate_whole_run_lower_bound_not_global_optimum",
        "arm_semantics": {
            "c0_current": "observed-order work-conserving execution plus reactive LRU KV",
            "c1_agent": "minimum whole-run makespan across C0 and four fixed execution policies; C0 dominance is enforced",
            "c2_kv": "causal-next-use KV with proactive shadow and latest-start restore over the visible frontier",
            "c3_joint": "minimum whole-run makespan across C0, C2, all C1 policies, and each execution policy paired with C2 KV; C1/C2 dominance is enforced",
        },
        "whole_run_selection_contract": (
            "each execution policy is simulated to completion; C0 is a C1 "
            "candidate and C0, C2, and all C1 candidates are C3 candidates"
        ),
        "future_pressure_contract": (
            "current active/ready work plus frozen future root releases; child, "
            "tool, and JOIN continuations enter only after their causal event"
        ),
        "control_epoch_contract": (
            "event driven, with a 256 aggregate decode-token audit quantum"
        ),
        "c0_validation": validation_rows,
        "joint_opportunity_gate": _joint_opportunity_gate(matrix_rows),
        "scheduler_service_profile": (
            combined_runtime_profile
            if not args.c0_validation_only
            else runtime_profiles
        ),
        "matrix": matrix_rows,
        "gain_summary": _gain_summary(matrix_rows),
        "limitations": [
            "action boundary is complete LLM_RESULT; no early dispatch",
            "historical scheduler/worker service intervals calibrate graph-16 wall time; they are not interpreted as GPU busy intervals",
            "controlled H200 curves provide unsupported-point fallback and transfer envelopes",
            "GRAPH32 is a bounded sensitivity anchored by measured batch 31/32",
            "cross-run anonymous token maps cannot prove shared prefixes",
            "C1/C3 are finite-candidate whole-run lower bounds with no-op dominance, not globally optimal schedules",
            "proactive pressure is conservative over the visible causal frontier and scheduled root releases",
            "CPU estimate cannot replace final real-GPU O0-O3 evaluation",
        ],
    }
    (output / "cpu_counterfactual_oracle_estimate.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"estimate_mode": mode, "gain_summary": report["gain_summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
