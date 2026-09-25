from __future__ import annotations

import json
from pathlib import Path

import pytest

from beliefkv.experiments.p6_dataset import (
    _apply_runtime_intervention_censors,
    _apply_partial_episode_eligibility,
    _apply_workflow_exclusions,
    _read_workflow_exclusions,
    _invalid_source_markers,
    _join_reentry_row,
    _merge_collection_contracts,
    _validate_collection_contract,
    export_native_reactive_p6_dataset,
    export_p6_training_dataset,
)
from beliefkv.experiments.p6_coverage import P6CoverageError
from beliefkv.experiments.p6_decision_points import _event_triggers
from beliefkv.core.events import RuntimeEvent


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(item) + "\n" for item in records),
        encoding="utf-8",
    )


def test_workflow_training_exclusions_fail_closed_per_instance(tmp_path: Path) -> None:
    (tmp_path / "TRAINING_EXCLUSIONS.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "workflows": [
                    {
                        "instance_id": "django__django-11138",
                        "reason": "harness_path_contract_contamination",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    exclusions = _read_workflow_exclusions(tmp_path)
    tables = {
        "request_calls": [
            {
                "instance_id": "django__django-11138",
                "split": "train",
                "training_eligible_remaining_decode_demand": True,
            },
            {
                "instance_id": "django__django-11400",
                "split": "train",
                "training_eligible_remaining_decode_demand": True,
            },
        ]
    }

    _apply_workflow_exclusions(tables, exclusions)

    excluded, retained = tables["request_calls"]
    assert excluded["split"] is None
    assert excluded["training_eligible_remaining_decode_demand"] is False
    assert excluded["training_excluded"] is True
    assert excluded["training_exclusion_reason"] == (
        "harness_path_contract_contamination"
    )
    assert retained["split"] == "train"
    assert retained["training_eligible_remaining_decode_demand"] is True


def test_runtime_intervention_censors_only_crossing_label_targets() -> None:
    rows = [
        {
            "workflow_id": "workflow",
            "timestamp_ms": 90.0,
            "training_eligible": True,
            "censor_reasons": [],
            "labels": [
                {
                    "invocation_id": "crossing",
                    "next_boundary_timestamp_ms": 105.0,
                    "target_horizon_timestamp_ms": {
                        "action_boundary": 105.0,
                        "remaining_decode_demand": 99.0,
                    },
                    "target_training_eligible": {
                        "action_boundary": True,
                        "remaining_decode_demand": True,
                    },
                },
                {
                    "invocation_id": "completed",
                    "next_boundary_timestamp_ms": 99.0,
                    "target_horizon_timestamp_ms": {"action_boundary": 99.0},
                    "target_training_eligible": {"action_boundary": True},
                },
            ],
        },
        {
            "workflow_id": "workflow",
            "timestamp_ms": 110.0,
            "training_eligible": True,
            "censor_reasons": [],
            "labels": [
                {
                    "invocation_id": "post",
                    "target_horizon_timestamp_ms": {"action_boundary": 120.0},
                    "target_training_eligible": {"action_boundary": True},
                }
            ],
        },
    ]

    summary = _apply_runtime_intervention_censors(
        rows,
        {
            "workflow": {
                "ts_ms": 100.0,
                "event_id": "sandbox-audit:8",
                "event": "agent_stuck_detected",
                "reason": "loop_guard_finalization",
                "agent_scope": "autonomous:supervisor",
            }
        },
    )

    crossing, completed = rows[0]["labels"]
    assert rows[0]["training_eligible"] is True
    assert crossing["target_training_eligible"] == {
        "action_boundary": False,
        "remaining_decode_demand": True,
    }
    assert completed["target_training_eligible"]["action_boundary"] is True
    assert rows[1]["training_eligible"] is False
    assert rows[1]["labels"][0]["censored"] is True
    assert summary["decision_row_count"] == 2
    assert summary["invocation_label_count"] == 2


def test_partial_episode_retains_completed_waits_and_right_censors_crossing() -> None:
    tables = {
        "request_calls": [
            {
                "workflow_id": "workflow",
                "result_ts_ms": 90.0,
                "training_eligible_remaining_decode_demand": True,
                "training_eligible_unlock_hazard": True,
            },
            {
                "workflow_id": "workflow",
                "result_ts_ms": 110.0,
                "training_eligible_remaining_decode_demand": True,
                "training_eligible_unlock_hazard": True,
            },
        ],
        "external_waits": [
            {
                "workflow_id": "workflow",
                "start_ts_ms": 70.0,
                "terminal_ts_ms": 90.0,
                "training_eligible_survival": True,
            },
            {
                "workflow_id": "workflow",
                "start_ts_ms": 80.0,
                "terminal_ts_ms": 120.0,
                "training_eligible_survival": True,
            },
        ],
        "reentries": [
            {
                "workflow_id": "workflow",
                "wait_start_ts_ms": 70.0,
                "reentry_ts_ms": 90.0,
                "training_eligible": True,
            },
            {
                "workflow_id": "workflow",
                "wait_start_ts_ms": 80.0,
                "reentry_ts_ms": 120.0,
                "training_eligible": True,
            },
        ],
        "frontier_decision_points": [],
    }
    summary = _apply_partial_episode_eligibility(
        tables,
        {
            "workflow": {
                "ts_ms": 100.0,
                "event_id": "sandbox-audit:4",
                "reason": "loop_guard_finalization",
            }
        },
    )

    before, after = tables["request_calls"]
    assert before["training_eligible_remaining_decode_demand"] is True
    assert before["training_eligible_unlock_hazard"] is True
    assert after["training_eligible_remaining_decode_demand"] is False
    completed_tool, crossing_tool = tables["external_waits"]
    assert completed_tool["training_eligible_survival"] is True
    assert crossing_tool["training_eligible_survival"] is True
    assert crossing_tool["survival_censored"] is True
    assert crossing_tool["survival_duration_ms"] == 20.0
    completed_reentry, crossing_reentry = tables["reentries"]
    assert completed_reentry["training_eligible"] is True
    assert crossing_reentry["training_eligible"] is False
    assert crossing_reentry["right_censored"] is True
    assert summary["counts"]["external_survival_retained"] == 1


def _event(
    sequence: int,
    kind: str,
    *,
    invocation_id: str | None = "root",
    attributes: dict[str, object] | None = None,
    **extra: object,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "sequence": sequence,
        "event_id": f"event-{sequence}",
        "ts_ms": float(sequence * 10),
        "kind": kind,
        "workflow_id": "workflow",
        "invocation_id": invocation_id,
        "context_id": "context" if invocation_id else None,
        "context_epoch": 0 if invocation_id else None,
        "attributes": attributes or {},
        **extra,
    }


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_execute_category_is_available_for_future_decision_export():
    trigger = _event_triggers([RuntimeEvent.from_dict(_event(
        1, "tool_start", attributes={
            "tool_call_id": "call-one", "tool_name": "execute",
            "observed_command_class": "test_suite",
            "command": "sensitive text must not leak",
        },
    ))])[0]
    assert trigger["attributes"]["observed_command_class"] == "test_suite"
    assert trigger["invocation_id"] == "root"
    assert "command" not in trigger["attributes"]


@pytest.mark.parametrize(
    "marker_name",
    ("PILOT_INVALID.json", "COLLECTION_INVALID.json", "STARTUP_FAILED.json"),
)
def test_all_invalid_collection_markers_fail_closed(
    tmp_path: Path, marker_name: str
) -> None:
    marker = tmp_path / marker_name
    marker.write_text("{}\n", encoding="utf-8")

    assert _invalid_source_markers(tmp_path) == (marker,)
    with pytest.raises(P6CoverageError, match="marked ineligible"):
        export_p6_training_dataset(tmp_path, tmp_path / "dataset")


@pytest.mark.parametrize("workload_layout", ["workloads", "autonomous"])
def test_export_training_tables_preserves_identity_censoring_and_join_closure(
    tmp_path: Path, workload_layout: str,
) -> None:
    run_dir = tmp_path / "run"
    workloads = run_dir / workload_layout
    server = run_dir / "server"
    output = tmp_path / "dataset"
    workloads.mkdir(parents=True)
    (workloads / "manifest.json").write_text(
        json.dumps(
            {
                "dataset": "swebench",
                "dataset_revision": "revision",
                "workload_manifest_sha256": "manifest",
            }
        ),
        encoding="utf-8",
    )
    (workloads / "summary.json").write_text(
        json.dumps(
            {
                "workflow_count": 1,
                "system_jct_eligible_workflows": 1,
                "native_agent_jct_eligible_workflows": 1,
                "measurement_valid_workflows": 1,
                "workflows": [
                    {
                        "workflow_id": "workflow",
                        "instance_id": "project__task",
                        "system_jct_eligible": True,
                        "native_agent_jct_eligible": True,
                        "measurement_valid": True,
                        "task_correctness_valid": True,
                        "agent_control": {"guard_intervened_completions": 0},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    _write_jsonl(
        workloads
        / "workflows"
        / "project__task"
        / "runtime_events.deepagents.jsonl",
        [
            _event(1, "workflow_start", invocation_id=None),
            _event(2, "invocation_create"),
            _event(3, "llm_submit", attributes={"request_id": "request"}),
            _event(
                4,
                "llm_result",
                attributes={
                    "request_id": "request",
                    "parser_status": "valid",
                    "structured_action_kinds": ["function_call"],
                    "structured_action_names": ["search"],
                    "action_boundary_source": "runtime_structured_output",
                    "action_boundary_token_index": None,
                    "output_tokens": 4,
                },
            ),
            _event(
                5,
                "tool_start",
                attributes={
                    "tool_call_id": "tool",
                    "tool_name": "search",
                    "tool_family": "search",
                    "is_child": True,
                    "observed_command_class": "test_suite",
                    "parameter_signature": "signature",
                },
            ),
            _event(
                6,
                "tool_end",
                attributes={
                    "tool_call_id": "tool",
                    "tool_name": "search",
                    "status": "success",
                    "duration_ms": 10.0,
                },
            ),
            _event(7, "invocation_create", invocation_id="child"),
            _event(
                8,
                "join_create",
                invocation_id=None,
                join_id="join",
                member_invocation_ids=["child"],
            ),
            _event(9, "join_wait", join_id="join"),
            _event(10, "return", invocation_id="child"),
            _event(11, "join_satisfied", invocation_id=None, join_id="join"),
        ],
    )
    _write_jsonl(
        server / "runtime_events.sglang.jsonl",
        [
            _event(
                1,
                "llm_submit",
                attributes={
                    "request_id": "request",
                    "prompt_tokens": 100,
                    "cache_hit_tokens": 80,
                    "context_tokens": 100,
                    "expected_output_tokens": 16,
                },
            ),
            _event(
                2,
                "llm_result",
                attributes={"request_id": "request", "output_tokens": 4},
            ),
        ],
    )
    _write_jsonl(
        server / "runtime_audit.jsonl",
        [
            {
                "event": "gpu_service_sample",
                "sample_id": "sample",
                "phase": "decode",
                "batch_size": 1,
                "service_start_ts_ms": 1.0,
                "complete_ts_ms": 2.0,
                "service_elapsed_ms": 1.0,
                "timing_semantics_version": "gpu_service_interval_v1",
                "request_samples": [
                    {
                        "request_id": "request",
                        "workflow_id": "workflow",
                        "invocation_id": "root",
                        "context_id": "context",
                        "context_epoch": 0,
                        "phase": "decode",
                        "token_delta": 1,
                        "token_delta_semantics": "observed_output_ids_delta",
                        "sequence_tokens_before": 100,
                        "output_tokens_before": 0,
                    }
                ],
            }
        ],
    )
    _write_jsonl(
        server / "transfer_telemetry.jsonl",
        [
            {
                "status": "completed",
                "command_id": "transfer",
                "command_kind": "offload_context",
                "telemetry_origin": "backend_telemetry",
                "direction": "d2h",
                "actual_bytes": 4096,
                "closure_bytes": 4096,
                "page_count": 1,
                "source_tier": "gpu",
                "target_tier": "host",
                "host_copy_state": "missing",
                "pinned_host": True,
                "native_concurrent_bytes": 0,
                "allocator_submit_ms": 0.1,
                "callback_overhead_ms": 0.2,
                "submit_ts_ms": 1.0,
                "complete_ts_ms": 2.0,
                "start_timestamp_semantics": "unavailable",
            }
        ],
    )
    (server / "latest_runtime_summary.json").write_text(
        json.dumps({"run_id": "run"}), encoding="utf-8"
    )

    manifest = export_p6_training_dataset(run_dir, output)

    assert manifest["identity_contract"]["ordinal_fallback"] is False
    assert manifest["source"]["collection_status"] == (
        "complete"
        if workload_layout == "workloads"
        else "complete_legacy_autonomous_layout"
    )
    assert manifest["integrity"]["passes"] is True
    assert manifest["tables"]["request_calls"]["row_count"] == 1
    assert manifest["tables"]["gpu_service_intervals"]["row_count"] == 1
    assert manifest["tables"]["gpu_batch_service_intervals"]["row_count"] == 1
    assert manifest["tables"]["external_waits"]["row_count"] == 1
    assert _read_jsonl(output / "external_waits.jsonl")[0][
        "observed_command_class"
    ] == "test_suite"
    assert _read_jsonl(output / "external_waits.jsonl")[0]["is_child"] is True
    assert manifest["tables"]["reentries"]["row_count"] == 2
    assert manifest["tables"]["pcie_operations"]["row_count"] == 1
    assert manifest["tables"]["frontier_decision_points"]["row_count"] >= 1
    assert manifest["tables"]["censor_events"]["row_count"] == 0
    assert manifest["training_readiness"] == {
        "remaining_decode_demand_eligible_request_count": 1,
        "unlock_hazard_eligible_request_count": 0,
        "external_survival_eligible_count": 1,
        "join_reentry_eligible_count": 1,
        "pcie_service_eligible_count": 1,
        "runtime_batch_characterization_count": 1,
        "frontier_decision_eligible_count": 4,
        "explicit_censor_event_count": 0,
    }
    request = _read_jsonl(output / "request_calls.jsonl")[0]
    assert request["matching_method"] == "exact_native_request_id"
    assert request["training_eligible_remaining_decode_demand"] is True
    assert request["training_eligible_unlock_hazard"] is False
    join = next(
        row
        for row in _read_jsonl(output / "reentries.jsonl")
        if row["reentry_kind"] == "join"
    )
    assert join["training_eligible"] is True
    assert join["member_outcomes"][0]["return_ts_ms"] == 100.0


def test_join_label_is_not_trainable_when_one_member_has_not_returned() -> None:
    row = _join_reentry_row(
        "run",
        "join",
        {
            "workflow_id": "workflow",
            "waiter_invocation_id": "parent",
            "wait_ts_ms": 10.0,
            "member_invocation_ids": ("child-a", "child-b"),
        },
        terminal_ts_ms=30.0,
        terminal_status="satisfied",
        return_ts={"child-a": 20.0},
        invocation_start_ts={"child-a": 0.0, "child-b": 0.0},
        workflow_metadata={},
    )

    assert row["training_eligible"] is False
    assert row["member_outcomes"][1]["return_ts_ms"] is None


def test_collection_contract_fails_closed_on_predictive_or_invalid_evidence() -> None:
    base = {
        "predictor_enabled": False,
        "predictive_actions_enabled": False,
        "runtime_policy": "frozen_p5_observed",
        "training_eligible": True,
    }
    _validate_collection_contract(base, allow_censored=False)
    with pytest.raises(P6CoverageError, match="predictor enabled"):
        _validate_collection_contract(
            {**base, "predictor_enabled": True}, allow_censored=False
        )
    with pytest.raises(P6CoverageError, match="system eligibility"):
        _validate_collection_contract(
            {**base, "training_eligible": False}, allow_censored=False
        )
    _validate_collection_contract(
        {**base, "training_eligible": False}, allow_censored=True
    )
    _validate_collection_contract(
        {**base, "training_eligible": False},
        allow_censored=False,
        allow_formal_local_training=True,
    )
    with pytest.raises(P6CoverageError, match="runtime source"):
        _validate_collection_contract(
            {**base, "runtime_source_stable": False},
            allow_censored=False,
            allow_formal_local_training=True,
        )


def test_native_trace_coverage_merges_across_batches_by_workflow_count() -> None:
    merged = _merge_collection_contracts(
        [
            {
                "plan_id": "plan",
                "split": "train",
                "runtime_policy": "frozen_native_reactive_v0520",
                "predictor_enabled": False,
                "predictive_actions_enabled": False,
                "training_eligible": False,
                "raw_trace_eligible": False,
                "trace_complete_workflows": 15,
                "workflow_count": 16,
                "raw_trace_min_coverage": 0.95,
                "runtime_source_stable": True,
                "model_revision_stable": True,
                "batch_id": "small",
            },
            {
                "plan_id": "plan",
                "split": "train",
                "runtime_policy": "frozen_native_reactive_v0520",
                "predictor_enabled": False,
                "predictive_actions_enabled": False,
                "training_eligible": False,
                "raw_trace_eligible": True,
                "trace_complete_workflows": 48,
                "workflow_count": 48,
                "raw_trace_min_coverage": 0.95,
                "runtime_source_stable": True,
                "model_revision_stable": True,
                "batch_id": "large",
            },
        ]
    )

    assert merged["trace_complete_workflows"] == 63
    assert merged["workflow_count"] == 64
    assert merged["raw_trace_coverage"] == pytest.approx(63 / 64)
    assert merged["raw_trace_eligible"] is True


def _native_run(tmp_path: Path) -> Path:
    test_export_training_tables_preserves_identity_censoring_and_join_closure(
        tmp_path, "workloads"
    )
    run = tmp_path / "run"
    (run / "workloads" / "p6_collection_contract.json").write_text(
        json.dumps(
            {
                "runtime_policy": "frozen_native_reactive_v0520",
                "raw_trace_eligible": True,
                "runtime_source_stable": True,
                "model_revision_stable": True,
                "predictor_enabled": False,
                "predictive_actions_enabled": False,
            }
        ),
        encoding="utf-8",
    )
    server = run / "server"
    _write_jsonl(
        server / "runtime_audit.jsonl",
        [
            {
                "event": "gpu_service_sample",
                "sample_id": f"sample-{phase}",
                "phase": phase,
                "batch_size": 1,
                "service_start_ts_ms": timestamp,
                "complete_ts_ms": timestamp + 1,
                "service_elapsed_ms": 1.0,
                "timing_semantics_version": "gpu_service_interval_v1",
                "request_samples": [
                    {
                        "request_id": "request",
                        "workflow_id": "workflow",
                        "invocation_id": "root",
                        "context_id": "context",
                        "context_epoch": 0,
                        "phase": phase,
                        "token_delta": delta,
                        "token_delta_semantics": semantics,
                        "sequence_tokens_before": 100,
                        "output_tokens_before": 0,
                    }
                ],
            }
            for phase, timestamp, delta, semantics in (
                ("prefill", 1.0, 20, "prefill_extend_input_len"),
                ("decode", 2.0, 4, "observed_output_ids_delta"),
            )
        ],
    )
    _write_jsonl(
        server / "transfer_telemetry.jsonl",
        [
            {
                "status": "completed",
                "command_id": "native-ack",
                "command_kind": "restore_context",
                "direction": "h2d",
                "actual_bytes": None,
                "start_ts_ms": None,
                "start_timestamp_semantics": "unavailable",
                "pool_token_count": 20,
                "node_ids": [1],
            }
        ],
    )
    _write_jsonl(server / "host_pool_telemetry.jsonl", [])
    _write_jsonl(server / "eviction_attribution.jsonl", [])
    (server / "native_telemetry_status.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": "native_sglang_v0520",
                "record_counts": {
                    "events": 2,
                    "audit": 2,
                    "transfer": 1,
                    "host_pool": 0,
                    "eviction_attribution": 0,
                },
                "pending_request_count": 0,
                "pending_batch_count": 0,
                "writer_error": None,
                "failed_records": 0,
                "dropped_records": 0,
            }
        ),
        encoding="utf-8",
    )
    return run


def test_native_reactive_exports_independent_heads_without_dma_claim(
    tmp_path: Path,
) -> None:
    run = _native_run(tmp_path)
    output = tmp_path / "native"
    manifest = export_native_reactive_p6_dataset(run, output)

    request = _read_jsonl(output / "request_calls.jsonl")[0]
    labels = [
        label
        for row in _read_jsonl(output / "frontier_decision_points.jsonl")
        for label in row["labels"]
    ]
    assert request["native_request_telemetry_complete"] is True
    assert request["training_eligible_remaining_decode_demand"] is True
    assert any(label["target_training_eligible"]["action_boundary"] for label in labels)
    assert any(label["target_training_eligible"]["remaining_decode_demand"] for label in labels)
    assert _read_jsonl(output / "external_waits.jsonl")[0]["training_eligible_survival"]
    assert next(
        row for row in _read_jsonl(output / "reentries.jsonl")
        if row["reentry_kind"] == "join"
    )["training_eligible"]
    transfer = _read_jsonl(output / "pcie_operations.jsonl")[0]
    assert transfer["actual_bytes"] is None
    assert transfer["direct_dma_duration_ms"] is None
    assert transfer["training_eligible_service_curve"] is False
    assert all(
        row["timing_boundary"] == "scheduler/worker interval; not CUDA-event kernel time"
        for row in _read_jsonl(output / "gpu_batch_service_intervals.jsonl")
    )
    assert manifest["formal_local_training_eligible"] is False
    assert manifest["source"]["collection_contract"]["runtime_policy"] == (
        "frozen_native_reactive_v0520"
    )
    assert manifest["source"]["run_id"] == "run"
    assert manifest["source"]["run_id_source"] == "runtime_summary"


def test_native_transfer_service_requires_measured_stream_interval(
    tmp_path: Path,
) -> None:
    from beliefkv.experiments.p6_dataset import _pcie_rows

    path = tmp_path / "transfer.jsonl"
    base = {
        "command_id": "native-1",
        "telemetry_origin": "native_hicache_ack_v0520",
        "direction": "h2d",
        "status": "completed",
        "actual_bytes": 8192,
        "submit_ts_ms": 1000.0,
        "complete_ts_ms": 1010.0,
        "submit_to_ack_ms": 9.0,
        "native_unacked_bytes_at_submit": 1024,
        "transfer_stream_elapsed_ms": 2.0,
        "start_timestamp_semantics": "device_event_no_wall_anchor",
    }
    path.write_text(
        "".join(json.dumps({**base, **change}) + "\n" for change in (
            {},
            {"command_id": "native-2", "transfer_stream_elapsed_ms": None},
            {"command_id": "native-3", "actual_bytes": None},
        )),
        encoding="utf-8",
    )
    rows = _pcie_rows(path, run_id="train")

    assert rows[0]["training_eligible_service_curve"] is True
    assert rows[0]["duration_label_kind"] == "native_transfer_stream"
    assert rows[0]["transfer_stream_elapsed_ms"] == 2.0
    assert rows[0]["submit_to_complete_ms"] == 9.0
    assert rows[0]["direct_dma_duration_ms"] is None
    assert all(not row["training_eligible_service_curve"] for row in rows[1:])


def test_native_export_retains_measured_transfer_labels_only_when_healthy(
    tmp_path: Path,
) -> None:
    run = _native_run(tmp_path)
    server = run / "server"
    _write_jsonl(
        server / "transfer_telemetry.jsonl",
        [{
            "command_id": "native-ack",
            "command_kind": "native_hicache_ack",
            "telemetry_origin": "native_hicache_ack_v0520",
            "direction": "h2d",
            "status": "completed",
            "actual_bytes": 8192,
            "submit_ts_ms": 1000.0,
            "complete_ts_ms": 1010.0,
            "submit_to_ack_ms": 9.0,
            "native_unacked_bytes_at_submit": 0,
            "transfer_stream_elapsed_ms": 2.0,
            "start_timestamp_semantics": "device_event_no_wall_anchor",
        }],
    )
    good = export_native_reactive_p6_dataset(run, tmp_path / "good")
    assert good["training_readiness"]["pcie_service_eligible_count"] == 1
    assert _read_jsonl(tmp_path / "good/pcie_operations.jsonl")[0][
        "training_eligible_service_curve"
    ] is True

    status = server / "native_telemetry_status.json"
    raw = json.loads(status.read_text(encoding="utf-8"))
    raw["writer_error"] = "writer failed"
    status.write_text(json.dumps(raw), encoding="utf-8")
    bad = export_native_reactive_p6_dataset(run, tmp_path / "bad")
    assert bad["training_readiness"]["pcie_service_eligible_count"] == 0


def test_native_reactive_uses_stable_trace_fingerprint_for_legacy_run(
    tmp_path: Path,
) -> None:
    run = _native_run(tmp_path)
    (run / "server" / "latest_runtime_summary.json").unlink()

    first = export_native_reactive_p6_dataset(run, tmp_path / "native-first")
    second = export_native_reactive_p6_dataset(run, tmp_path / "native-second")

    assert first["source"]["run_id"].startswith("legacy-trace-")
    assert first["source"]["run_id"] == second["source"]["run_id"]
    assert first["source"]["run_id_source"] == "server_trace_fingerprint"


def test_native_reactive_prefers_root_run_manifest_identity(
    tmp_path: Path,
) -> None:
    run = _native_run(tmp_path)
    (run / "manifest.json").write_text(
        json.dumps({"run_id": "root-run-id"}),
        encoding="utf-8",
    )
    (run / "server" / "latest_runtime_summary.json").write_text(
        json.dumps({"run_id": "root-run-id"}),
        encoding="utf-8",
    )

    manifest = export_native_reactive_p6_dataset(run, tmp_path / "native")

    assert manifest["source"]["run_id"] == "root-run-id"
    assert manifest["source"]["run_id_source"] == "root_manifest"


@pytest.mark.parametrize(
    "plan_id",
    (
        "qwen35-native-reactive-v0520-v1",
        "qwen35-native-reactive-v0520-v2",
        "qwen35-native-reactive-v0520-v3",
        "qwen35-native-reactive-v0520-v4-128root",
    ),
)
def test_native_train_export_loads_only_frozen_local_heads(
    tmp_path: Path, plan_id: str
) -> None:
    from beliefkv.predictor.structured_frontier import load_decision_rows

    run = _native_run(tmp_path)
    server = run / "server"
    collection_path = run / "workloads" / "p6_collection_contract.json"
    collection = json.loads(collection_path.read_text(encoding="utf-8"))
    collection.update({
        "plan_id": plan_id,
        "split": "train",
        "model_revision_sha256": {
            "config.json": "config-hash", "tokenizer.json": "tokenizer-hash"
        },
        "server_identity": {
            "sglang_version": "0.5.20",
            "weight_dtype": "bfloat16",
            "resolved_kv_dtype": "bfloat16",
        },
        "runtime_source_fingerprint_start": {"digest": "source-hash"},
    })
    runtime = {
        "schema_version": 1,
        "contract_state": "validated",
        "runtime_kind": "native_reactive_v0520",
        "model_revision_sha256": collection["model_revision_sha256"],
        "server_identity": collection["server_identity"],
        "hardware": {"uuid": "GPU-test"},
        "sglang_commit": "checkout-hash",
        "sglang_patch_sha256": "patch-hash",
        "beliefkv_source_sha256": "source-hash",
    }
    collection["native_runtime_contract"] = runtime
    collection_path.write_text(json.dumps(collection))
    (server / "native_runtime_contract.json").write_text(json.dumps(runtime))
    split_path = tmp_path / "frozen-split.json"
    split_path.write_text(json.dumps({
        "schema_version": 1,
        "frozen": True,
        "dataset": "swebench",
        "projects": [{
            "dataset": "swebench", "project": "project", "split": "train",
            "task_count": 1,
            "tasks": [{"instance_id": "project__task", "base_commit": "abc"}],
        }],
    }))
    output = tmp_path / "native"
    manifest = export_native_reactive_p6_dataset(
        run, output, split_manifest=split_path
    )
    assert manifest["formal_local_training_eligible"] is True
    assert manifest["formal_training_eligible"] is False
    assert manifest["training_readiness"]["pcie_service_eligible_count"] == 0
    rows, _ = load_decision_rows(
        (output,), allowed_splits=("train",), allow_formal_local=True
    )
    assert rows
    with pytest.raises(ValueError, match="native reactive input"):
        load_decision_rows((output,), allowed_splits=("train",))


def test_native_reactive_bad_decode_only_censors_demand(tmp_path: Path) -> None:
    run = _native_run(tmp_path)
    audit = run / "server" / "runtime_audit.jsonl"
    records = _read_jsonl(audit)
    records[1]["request_samples"][0]["token_delta"] = 3
    _write_jsonl(audit, records)

    output = tmp_path / "native"
    export_native_reactive_p6_dataset(run, output)
    request = _read_jsonl(output / "request_calls.jsonl")[0]
    assert request["training_eligible_remaining_decode_demand"] is False
    assert any(
        label["target_training_eligible"]["action_boundary"]
        for row in _read_jsonl(output / "frontier_decision_points.jsonl")
        for label in row["labels"]
    )
    assert all(
        not label["target_training_eligible"]["remaining_decode_demand"]
        for row in _read_jsonl(output / "frontier_decision_points.jsonl")
        for label in row["labels"]
    )


def test_native_reactive_service_scope_conflict_censors_demand_not_action(
    tmp_path: Path,
) -> None:
    run = _native_run(tmp_path)
    audit = run / "server" / "runtime_audit.jsonl"
    records = _read_jsonl(audit)
    records[1]["request_samples"][0]["context_epoch"] = 1
    _write_jsonl(audit, records)

    output = tmp_path / "native"
    export_native_reactive_p6_dataset(run, output)
    request = _read_jsonl(output / "request_calls.jsonl")[0]
    assert request["training_eligible_remaining_decode_demand"] is False
    service = _read_jsonl(output / "gpu_service_intervals.jsonl")
    assert service[1]["training_eligible"] is False
    assert any(
        label["target_training_eligible"]["action_boundary"]
        for row in _read_jsonl(output / "frontier_decision_points.jsonl")
        for label in row["labels"]
    )


def test_native_reactive_request_cache_features_are_observed(tmp_path: Path) -> None:
    run = _native_run(tmp_path)
    events = run / "server" / "runtime_events.sglang.jsonl"
    rows = _read_jsonl(events)
    rows[0]["attributes"].update(
        {
            "cached_tokens_device": 40,
            "cached_tokens_host": 40,
            "uncached_prompt_tokens": 20,
            "enqueue_ts_ms": 15.0,
        }
    )
    _write_jsonl(events, rows)
    output = tmp_path / "native"
    export_native_reactive_p6_dataset(run, output)
    request = _read_jsonl(output / "request_calls.jsonl")[0]
    assert (request["cached_tokens_device"], request["cached_tokens_host"]) == (40, 40)
    assert request["uncached_prompt_tokens"] == 20
    assert request["enqueue_ts_ms"] == 15.0


@pytest.mark.parametrize(
    "fault", ["missing", "failed", "dropped", "pending", "truncated"]
)
def test_native_reactive_missing_or_dropped_telemetry_fails_closed(
    tmp_path: Path, fault: str,
) -> None:
    run = _native_run(tmp_path)
    path = run / "server" / "native_telemetry_status.json"
    if fault == "missing":
        path.unlink()
    else:
        status = json.loads(path.read_text(encoding="utf-8"))
        if fault == "failed":
            status["failed_records"] = 1
        elif fault == "dropped":
            status["dropped_records"] = 1
        elif fault == "pending":
            status["pending_batch_count"] = 1
        else:
            status["record_counts"]["audit"] += 1
        path.write_text(json.dumps(status), encoding="utf-8")

    output = tmp_path / "native"
    manifest = export_native_reactive_p6_dataset(run, output)
    assert manifest["source"]["native_request_evidence"]["telemetry_complete"] is False
    assert "native_telemetry_incomplete" in manifest["formal_ineligibility_reasons"]
    assert manifest["training_readiness"]["remaining_decode_demand_eligible_request_count"] == 0
    assert manifest["training_readiness"]["external_survival_eligible_count"] == 0
    assert manifest["training_readiness"]["join_reentry_eligible_count"] == 0
    assert all(
        not row["training_eligible"]
        for row in _read_jsonl(output / "frontier_decision_points.jsonl")
    )


def test_native_eviction_attribution_expands_one_input_into_multiple_rows(
    tmp_path: Path,
) -> None:
    run = _native_run(tmp_path)
    server = run / "server"
    status_path = server / "native_telemetry_status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["record_counts"]["eviction_attribution"] = 1
    status_path.write_text(json.dumps(status), encoding="utf-8")
    _write_jsonl(
        server / "eviction_attribution.jsonl",
        [{"event": "host_block_evicted"}, {"event": "host_block_reaccess"}],
    )
    manifest = export_native_reactive_p6_dataset(run, tmp_path / "expanded")
    assert manifest["source"]["native_request_evidence"]["telemetry_complete"]

    status["record_counts"]["eviction_attribution"] = 3
    status_path.write_text(json.dumps(status), encoding="utf-8")
    manifest = export_native_reactive_p6_dataset(run, tmp_path / "truncated")
    assert not manifest["source"]["native_request_evidence"]["telemetry_complete"]


def test_native_reactive_rejects_unstable_raw_trace(tmp_path: Path) -> None:
    run = _native_run(tmp_path)
    path = run / "workloads" / "p6_collection_contract.json"
    contract = json.loads(path.read_text(encoding="utf-8"))
    contract["raw_trace_eligible"] = False
    path.write_text(json.dumps(contract), encoding="utf-8")
    with pytest.raises(P6CoverageError, match="raw trace provenance"):
        export_native_reactive_p6_dataset(run, tmp_path / "native")
