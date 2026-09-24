from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.experiments.p6_coverage import (
    P6CoverageError,
    _iter_jsonl,
    _match_server_calls,
    _read_jsonl,
    _read_object,
    _replay_agent_trace,
    discover_p6_agent_traces,
)
from beliefkv.runtime.action_frontier import ActionFrontierObserver
from beliefkv.experiments.p6_split import load_split_manifest, resolve_split
from beliefkv.experiments.p6_decision_points import build_frontier_decision_points


P6_DATASET_SCHEMA_VERSION = 4
P6_INVALID_SOURCE_MARKERS = (
    "PILOT_INVALID.json",
    "COLLECTION_INVALID.json",
    "STARTUP_FAILED.json",
)
P6_WORKFLOW_EXCLUSIONS_FILENAME = "TRAINING_EXCLUSIONS.json"


def _invalid_source_markers(source: Path) -> tuple[Path, ...]:
    return tuple(
        source / name
        for name in P6_INVALID_SOURCE_MARKERS
        if (source / name).is_file()
    )


def export_p6_training_dataset(
    run_dir: str | Path,
    output_dir: str | Path,
    *,
    allow_censored: bool = False,
    allow_development_only: bool = False,
    split_manifest: str | Path | Mapping[str, Any] | None = None,
    workload_dirs: Sequence[str | Path] | None = None,
    selected_instance_ids: Iterable[str] | None = None,
    allow_formal_local_training: bool = False,
    formal_local_expected_split: str | None = None,
    _native_reactive: bool = False,
) -> dict[str, Any]:
    """Export versioned P6 labels from one physical server run."""

    source = Path(run_dir).resolve()
    invalid_markers = _invalid_source_markers(source)
    if invalid_markers and not allow_censored:
        raise P6CoverageError(
            "run is explicitly marked ineligible for training: "
            + ", ".join(str(path) for path in invalid_markers)
        )
    workload_roots, collection_status = _resolve_workload_roots(
        source, workload_dirs=workload_dirs, allow_censored=allow_censored
    )
    workloads = workload_roots[0]
    # collection_status is resolved together with workload_roots.
    server = source / "server"
    manifest_paths = tuple(root / "manifest.json" for root in workload_roots)
    summary_paths = tuple(root / "summary.json" for root in workload_roots)
    server_events_path = server / "runtime_events.sglang.jsonl"
    audit_path = server / "runtime_audit.jsonl"
    transfer_path = server / "transfer_telemetry.jsonl"
    host_pool_path = server / "host_pool_telemetry.jsonl"
    eviction_attribution_path = server / "eviction_attribution.jsonl"
    runtime_summary_path = server / "latest_runtime_summary.json"
    required = (server_events_path, audit_path, transfer_path)
    if _native_reactive:
        required = (*required, host_pool_path)
    if collection_status.startswith("complete"):
        required = (*manifest_paths, *summary_paths, *required)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise P6CoverageError(f"run is missing dataset evidence: {missing}")

    selected_instances = (
        frozenset(str(item) for item in selected_instance_ids)
        if selected_instance_ids is not None
        else None
    )
    if selected_instances is not None and (
        not selected_instances or "" in selected_instances
    ):
        raise P6CoverageError("selected instance allowlist must be non-empty")
    agent_paths = tuple(
        path
        for root in workload_roots
        for path in discover_p6_agent_traces(root)
        if selected_instances is None or _trace_instance_id(path) in selected_instances
    )
    if not agent_paths:
        raise P6CoverageError("run has no selected per-workflow event traces")
    selected_trace_instances = Counter(_trace_instance_id(path) for path in agent_paths)
    if selected_instances is not None:
        missing_instances = selected_instances.difference(selected_trace_instances)
        duplicate_instances = sorted(
            item for item, count in selected_trace_instances.items() if count != 1
        )
        if missing_instances or duplicate_instances:
            raise P6CoverageError(
                "selected trace identity mismatch: "
                f"missing={sorted(missing_instances)}, duplicates={duplicate_instances}"
            )
    manifests = tuple(
        _read_object(path) if path.is_file() else {} for path in manifest_paths
    )
    summaries = tuple(
        _read_object(path) if path.is_file() else {} for path in summary_paths
    )
    manifest = _merge_workload_manifests(manifests)
    summary = _merge_workload_summaries(
        summaries, selected_instance_ids=selected_instances
    )
    runtime_summary = (
        _read_object(runtime_summary_path)
        if runtime_summary_path.is_file()
        else {}
    )
    run_id, run_id_source = _resolve_run_identity(
        source,
        workload_manifest=manifest,
        runtime_summary=runtime_summary,
        evidence_paths=(
            server_events_path,
            audit_path,
            transfer_path,
        ),
    )
    collection_contracts = tuple(
        _read_collection_contract(root) for root in workload_roots
    )
    collection_contract = _merge_collection_contracts(collection_contracts)
    _validate_collection_contract(
        collection_contract,
        allow_censored=allow_censored,
        allow_development_only=allow_development_only,
        allow_formal_local_training=allow_formal_local_training,
        native_reactive=_native_reactive,
    )
    workflow_exclusions = _read_workflow_exclusions(source)
    runtime_provenance = _read_runtime_provenance(server)
    runtime_environment_contract = (
        _native_runtime_environment_contract(server, collection_contract)
        if _native_reactive else _runtime_environment_contract(server)
    )
    workflow_source_metadata = _workflow_source_metadata(
        summaries,
        collection_contracts,
        runtime_provenance=runtime_provenance,
    )

    agent_records = [_read_jsonl(path) for path in agent_paths]
    calls: list[dict[str, Any]] = []
    observer = ActionFrontierObserver()
    event_counts: Counter[str] = Counter()
    workflow_stats: dict[str, Counter[str]] = defaultdict(Counter)
    for path, records in zip(agent_paths, agent_records):
        _replay_agent_trace(
            records,
            observer=observer,
            calls=calls,
            event_kind_counts=event_counts,
            workflow_stats=workflow_stats,
            source_path=path,
        )
    server_events = _read_jsonl(server_events_path)
    _match_server_calls(calls, server_events)

    dataset_name = str(manifest.get("dataset") or "unknown")
    frozen_split = (
        load_split_manifest(split_manifest)
        if isinstance(split_manifest, (str, Path))
        else split_manifest
    )
    formal_local_ineligibility_reasons = []
    if invalid_markers:
        formal_local_ineligibility_reasons.append("source_marked_invalid")
    if collection_status == "censored":
        formal_local_ineligibility_reasons.append("collection_censored")
    if collection_contract is None:
        formal_local_ineligibility_reasons.append("collection_contract_missing")
    elif collection_contract.get("runtime_source_stable") is not True:
        formal_local_ineligibility_reasons.append("runtime_source_unstable")
    if not runtime_environment_contract:
        formal_local_ineligibility_reasons.append(
            "runtime_environment_contract_missing"
        )
    if frozen_split is None:
        formal_local_ineligibility_reasons.append("frozen_split_missing")
    if allow_development_only:
        formal_local_ineligibility_reasons.append(
            "predictor_shadow_development_only"
        )
    formal_local_training_eligible = not formal_local_ineligibility_reasons
    effective_split = (
        frozen_split
        if (formal_local_training_eligible or allow_development_only or _native_reactive)
        else None
    )
    workflow_metadata = _workflow_metadata(
        summary,
        dataset_name=dataset_name,
        split_manifest=effective_split,
        workflow_exclusions=workflow_exclusions,
        workflow_source_metadata=workflow_source_metadata,
    )
    if allow_formal_local_training:
        if formal_local_expected_split not in {"train", "calibration"}:
            raise P6CoverageError(
                "formal-local export requires an explicit train/calibration split"
            )
        actual_splits = {
            str(row.get("split"))
            for row in workflow_metadata.values()
            if row.get("split") is not None
        }
        if actual_splits != {formal_local_expected_split}:
            raise P6CoverageError(
                "formal-local export split mismatch: "
                f"expected={formal_local_expected_split!r}, "
                f"actual={sorted(actual_splits)!r}"
            )
    service_rows, batch_service_rows, service_summary = _service_rows(
        audit_path,
        calls=calls,
        run_id=run_id,
        workflow_metadata=workflow_metadata,
    )
    request_rows = _request_rows(
        calls,
        run_id=run_id,
        workflow_metadata=workflow_metadata,
        service_summary=service_summary,
    )
    if _native_reactive:
        _attach_native_request_features(request_rows, server_events)
    external_rows, reentry_rows = _external_and_reentry_rows(
        agent_records,
        run_id=run_id,
        workflow_metadata=workflow_metadata,
    )
    pcie_rows = _pcie_rows(transfer_path, run_id=run_id)
    censor_rows = _censor_rows(
        agent_records,
        run_id=run_id,
        workflow_metadata=workflow_metadata,
    )
    decision_rows = build_frontier_decision_points(
        agent_records,
        calls=request_rows,
        service_rows=service_rows,
        audit_records=_iter_jsonl(audit_path),
        transfer_records=_iter_jsonl(transfer_path),
        run_id=run_id,
        workflow_metadata=workflow_metadata,
    )
    intervention_cutoffs = _runtime_intervention_cutoffs_many(workload_roots)
    intervention_censor_summary = _apply_runtime_intervention_censors(
        decision_rows,
        intervention_cutoffs,
    )
    clean_episode_eligible = not intervention_cutoffs
    clean_trajectory_ineligibility_reasons = list(
        formal_local_ineligibility_reasons
    )
    if (
        collection_contract is not None
        and collection_contract.get(
            "raw_trace_eligible" if _native_reactive else "training_eligible"
        ) is not True
    ):
        clean_trajectory_ineligibility_reasons.append("collection_gate_failed")
    if intervention_cutoffs:
        clean_trajectory_ineligibility_reasons.append("runtime_intervention_observed")
    clean_trajectory_eligible = not clean_trajectory_ineligibility_reasons

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    tables = {
        "request_calls": request_rows,
        "gpu_service_intervals": service_rows,
        "gpu_batch_service_intervals": batch_service_rows,
        "external_waits": external_rows,
        "reentries": reentry_rows,
        "pcie_operations": pcie_rows,
        "censor_events": censor_rows,
        "frontier_decision_points": decision_rows,
    }
    partial_episode_summary = _apply_partial_episode_eligibility(
        tables,
        intervention_cutoffs,
    )
    native_evidence = None
    if _native_reactive:
        native_path = server / "native_telemetry_status.json"
        audit_records = _read_jsonl(audit_path)
        native_evidence = _apply_native_reactive_evidence(
            tables,
            _read_object(native_path) if native_path.is_file() else {},
            audit_records=audit_records,
            record_counts={
                "events": len(server_events),
                "audit": len(audit_records),
                "transfer": len(_read_jsonl(transfer_path)),
                "host_pool": len(
                    _read_jsonl(host_pool_path)
                ),
                "eviction_attribution": (
                    len(_read_jsonl(eviction_attribution_path))
                    if eviction_attribution_path.is_file()
                    else -1
                ),
            },
            server_events=server_events,
        )
        if not native_evidence["telemetry_complete"]:
            formal_local_ineligibility_reasons.append("native_telemetry_incomplete")
            clean_trajectory_ineligibility_reasons.append("native_telemetry_incomplete")
        formal_local_training_eligible = not formal_local_ineligibility_reasons
        # Train-only native evidence is locally fit-eligible; calibration/test
        # and end-to-end performance claims still require separate frozen runs.
        clean_trajectory_eligible = False
        clean_trajectory_ineligibility_reasons.append(
            "native_reactive_train_only_no_heldout_validation"
        )
    _apply_workflow_exclusions(tables, workflow_exclusions)
    integrity = _dataset_integrity(tables)
    if not integrity["passes"]:
        raise P6CoverageError(
            f"P6 dataset integrity check failed: {integrity['violations']}"
        )
    table_manifest: dict[str, Any] = {}
    for name, rows in tables.items():
        path = destination / f"{name}.jsonl"
        _write_jsonl_atomic(path, rows)
        table_manifest[name] = {
            "path": path.name,
            "row_count": len(rows),
            "sha256": _sha256(path),
        }

    split_counts = Counter(
        row["split"] for row in request_rows if row.get("split") is not None
    )
    output_manifest = {
        "schema_version": P6_DATASET_SCHEMA_VERSION,
        "dataset_kind": "beliefkv_p6_training_evidence",
        "characterization_only": True,
        "formal_training_eligible": clean_trajectory_eligible,
        "formal_local_training_eligible": formal_local_training_eligible,
        "clean_trajectory_eligible": clean_trajectory_eligible,
        "clean_episode_eligible": clean_trajectory_eligible,
        "workflow_jct_eligible": clean_trajectory_eligible,
        "terminal_outcome_eligible": clean_trajectory_eligible,
        "formal_ineligibility_reasons": clean_trajectory_ineligibility_reasons,
        "formal_local_ineligibility_reasons": formal_local_ineligibility_reasons,
        "evaluation_role": (
            "frozen_split_local_training_evidence"
            if formal_local_training_eligible
            else "development_diagnostic"
        ),
        "source": {
            "run_dir": str(source),
            "run_id": run_id,
            "run_id_source": run_id_source,
            "collection_status": collection_status,
            "invalid_source_markers": [path.name for path in invalid_markers],
            "workload_roots": [str(path) for path in workload_roots],
            "selected_instance_ids": (
                sorted(selected_instances) if selected_instances is not None else None
            ),
            "runtime_provenance": runtime_provenance,
            "runtime_environment_contract": runtime_environment_contract,
            "workflow_exclusions": workflow_exclusions,
            "runtime_intervention_censors": intervention_censor_summary,
            "partial_episode_eligibility": partial_episode_summary,
            "collection_contract": collection_contract,
            "native_request_evidence": native_evidence,
            "dataset": dataset_name,
            "dataset_revision": manifest.get("dataset_revision"),
            "workload_manifest_sha256": [
                _sha256(path) for path in manifest_paths if path.is_file()
            ],
            "system_jct_eligible_workflows": summary.get(
                "system_jct_eligible_workflows"
            ),
            "native_agent_jct_eligible_workflows": summary.get(
                "native_agent_jct_eligible_workflows"
            ),
            "measurement_valid_workflows": summary.get(
                "measurement_valid_workflows"
            ),
            "agent_trace_layout_counts": dict(
                sorted(
                    Counter(
                        "deepagents"
                        if path.name == "runtime_events.deepagents.jsonl"
                        else "agentic"
                        for path in agent_paths
                    ).items()
                )
            ),
            "artifact_sha256": {
                str(path.relative_to(source)): _sha256(path)
                for path in (
                    *manifest_paths,
                    *summary_paths,
                    server_events_path,
                    audit_path,
                    transfer_path,
                    *((host_pool_path,) if _native_reactive else ()),
                    *((eviction_attribution_path,) if _native_reactive else ()),
                    runtime_summary_path,
                    *agent_paths,
                    source / P6_WORKFLOW_EXCLUSIONS_FILENAME,
                    *((server / "native_telemetry_status.json",) if _native_reactive else ()),
                )
                if path.is_file()
            },
        },
        "identity_contract": {
            "request_join": (
                "exact native request_id plus workflow_id and invocation_id"
            ),
            "ordinal_fallback": False,
            "context_key": "context_id plus context_epoch",
        },
        "split_contract": {
            "unit": "dataset plus repository",
            "source": (
                "explicit frozen split manifest"
                if effective_split is not None
                else "none"
            ),
            "development_only": effective_split is None,
            "counts_on_request_calls": dict(sorted(split_counts.items())),
            "manifest_digest": (
                hashlib.sha256(
                    json.dumps(
                        effective_split, sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest()
                if effective_split is not None
                else None
            ),
            "warning": (
                "development-only evidence cannot establish generalization"
                if effective_split is None
                else None
            ),
        },
        "label_contract": {
            "request_calls": "one row per agent-visible LLM call",
            "gpu_service_intervals": (
                "one request row per shared batch service interval; batch elapsed "
                "time is not attributed as isolated request service"
            ),
            "gpu_batch_service_intervals": (
                "one unique row per sample_id; runtime timing is validation-only"
            ),
            "external_waits": (
                "tool start to terminal return with status and censoring"
            ),
            "reentries": "tool-return, join, message, or reactivate boundary",
            "pcie_operations": (
                "physical transfer attempt with explicit timestamp semantics"
            ),
            "censor_events": "identity-bearing runtime censor observations",
            "frontier_decision_points": (
                "event/32-token sampled local-state inputs and finite-horizon labels"
            ),
        },
        "training_readiness": {
            "remaining_decode_demand_eligible_request_count": sum(
                bool(row["training_eligible_remaining_decode_demand"])
                for row in request_rows
            ),
            "unlock_hazard_eligible_request_count": sum(
                bool(row["training_eligible_unlock_hazard"])
                for row in request_rows
            ),
            "external_survival_eligible_count": sum(
                bool(row["training_eligible_survival"])
                for row in external_rows
            ),
            "join_reentry_eligible_count": sum(
                row["reentry_kind"] == "join" and bool(row["training_eligible"])
                for row in reentry_rows
            ),
            "pcie_service_eligible_count": sum(
                bool(row["training_eligible_service_curve"])
                for row in pcie_rows
            ),
            "runtime_batch_characterization_count": len(batch_service_rows),
            "frontier_decision_eligible_count": sum(
                bool(row["training_eligible"]) for row in decision_rows
            ),
            "explicit_censor_event_count": len(censor_rows),
        },
        "integrity": integrity,
        "tables": table_manifest,
    }
    manifest_output = destination / "dataset_manifest.json"
    _write_json_atomic(manifest_output, output_manifest)
    return output_manifest


def export_native_reactive_p6_dataset(
    run_dir: str | Path,
    output_dir: str | Path,
    *,
    split_manifest: str | Path | Mapping[str, Any] | None = None,
    workload_dirs: Sequence[str | Path] | None = None,
    selected_instance_ids: Iterable[str] | None = None,
    expected_split: str = "train",
) -> dict[str, Any]:
    """Export split-isolated v0.5.20 native reactive evidence."""

    if expected_split not in {"train", "calibration"}:
        raise ValueError("native reactive export requires train or calibration")
    return export_p6_training_dataset(
        run_dir,
        output_dir,
        split_manifest=split_manifest,
        workload_dirs=workload_dirs,
        selected_instance_ids=selected_instance_ids,
        allow_formal_local_training=split_manifest is not None,
        formal_local_expected_split=expected_split if split_manifest is not None else None,
        _native_reactive=True,
    )


def _attach_native_request_features(
    requests: list[dict[str, Any]], server_events: Sequence[Mapping[str, Any]]
) -> None:
    by_request = {
        str(row["request_id"]): row
        for row in requests
        if row.get("request_id")
        and row.get("matching_method") == "exact_native_request_id"
    }
    for raw in server_events:
        if raw.get("kind") not in {"llm_submit", "llm_result"}:
            continue
        attrs = raw.get("attributes") or {}
        row = by_request.get(str(attrs.get("request_id") or ""))
        if row is None or any(
            raw.get(key) != row.get(key)
            for key in ("workflow_id", "invocation_id", "context_id", "context_epoch")
        ):
            continue
        for key in (
            "cached_tokens_device",
            "cached_tokens_host",
            "uncached_prompt_tokens",
            "enqueue_ts_ms",
        ):
            if attrs.get(key) is not None:
                row[key] = attrs[key]


def _apply_native_reactive_evidence(
    tables: Mapping[str, list[dict[str, Any]]],
    status: Mapping[str, Any],
    *,
    audit_records: Iterable[Mapping[str, Any]] = (),
    record_counts: Mapping[str, int] | None = None,
    server_events: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Gate independent heads on observed identity, token conservation and audit health."""

    requests = {str(row["request_id"]): row for row in tables["request_calls"]}
    counts = status.get("record_counts")
    healthy = bool(
        status.get("schema_version") == 1
        and status.get("source") == "native_sglang_v0520"
        and status.get("writer_error") is None
        and status.get("pending_request_count") == 0
        and status.get("pending_batch_count") == 0
        and type(status.get("failed_records")) is int
        and status["failed_records"] == 0
        and type(status.get("dropped_records")) is int
        and status["dropped_records"] == 0
        and isinstance(counts, Mapping)
        and record_counts is not None
        and all(
            type(counts.get(stream, 0)) is int
            and (
                count >= counts[stream]
                if stream == "eviction_attribution"
                else count == counts[stream]
            )
            for stream, count in record_counts.items()
        )
        and not (status.get("host_block_eviction_attribution") or {})
        .get("counts", {})
        .get("processing_errors", 0)
        and not any(
            row.get("event") == "gpu_service_sample_failed"
            for row in audit_records
        )
    )
    native_events: dict[str, dict[str, list[Mapping[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for raw in server_events:
        if raw.get("kind") in {"llm_submit", "llm_result"}:
            rid = str((raw.get("attributes") or {}).get("request_id") or "")
            if rid:
                native_events[rid][str(raw["kind"])].append(raw)

    service_by_request: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in tables["gpu_service_intervals"]:
        request_id = str(row.get("request_id") or "")
        request = requests.get(request_id)
        scope_matches = bool(
            request
            and all(
                row.get(key) == request.get(key)
                for key in ("workflow_id", "invocation_id", "context_id", "context_epoch")
            )
            and row.get("token_delta_semantics") == (
                "observed_output_ids_delta"
                if row.get("phase") == "decode"
                else "prefill_extend_input_len"
            )
            and type(row.get("token_delta")) is int
            and row["token_delta"] >= 0
            and row.get("phase") in {"prefill", "decode"}
            and type(row.get("sequence_tokens_before")) is int
            and row["sequence_tokens_before"] >= 0
            and row.get("batch_service_start_ts_ms") is not None
            and row.get("batch_service_complete_ts_ms") is not None
            and float(row["batch_service_start_ts_ms"])
            <= float(row["batch_service_complete_ts_ms"])
        )
        row["training_eligible"] = bool(
            healthy and row.get("training_eligible") and scope_matches
        )
        service_by_request[request_id].append(row)

    demand_proven: set[str] = set()
    action_proven: set[str] = set()
    calls_by_invocation: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for request_id, request in requests.items():
        calls_by_invocation[
            (str(request.get("workflow_id")), str(request.get("invocation_id")))
        ].append(request)
        rows = service_by_request.get(request_id, ())
        output = request.get("output_tokens")
        events = native_events.get(request_id, {})
        native_identity = bool(
            len(events.get("llm_submit", ())) == 1
            and len(events.get("llm_result", ())) == 1
            and all(
                event.get(key) == request.get(key)
                for kind in ("llm_submit", "llm_result")
                for event in events[kind]
                for key in ("workflow_id", "invocation_id", "context_id", "context_epoch")
            )
        )
        identity = bool(
            healthy
            and native_identity
            and request.get("matching_method") == "exact_native_request_id"
            and not request.get("runtime_internal")
            and not request.get("censored")
            and request.get("submit_ts_ms") is not None
            and request.get("result_ts_ms") is not None
            and float(request["submit_ts_ms"]) <= float(request["result_ts_ms"])
        )
        demand_valid = bool(
            identity
            and rows
            and any(row["phase"] == "prefill" for row in rows)
            and any(row["phase"] == "decode" for row in rows)
            and all(row["training_eligible"] for row in rows)
            and type(output) is int
            and sum(row["token_delta"] for row in rows if row["phase"] == "decode")
            == output
            and request.get("prompt_tokens") is not None
            and request.get("cache_hit_tokens") is not None
            and request.get("context_tokens") is not None
        )
        request["native_request_telemetry_complete"] = demand_valid
        request["training_eligible_remaining_decode_demand"] = bool(
            request["training_eligible_remaining_decode_demand"] and demand_valid
        )
        request["training_eligible_unlock_hazard"] = bool(
            request["training_eligible_unlock_hazard"] and demand_valid
        )
        if demand_valid:
            demand_proven.add(request_id)
        if identity and request.get("parser_status") == "valid" and request.get("action_kinds"):
            action_proven.add(request_id)

    for calls in calls_by_invocation.values():
        calls.sort(key=lambda item: float(item.get("submit_ts_ms") or 0))
    eligible_joins = {
        (row.get("workflow_id"), row.get("invocation_id"))
        for row in tables["reentries"]
        if row.get("reentry_kind") == "join"
        and row.get("training_eligible")
        and row.get("member_invocation_ids")
    }
    for row in tables["frontier_decision_points"]:
        features = {
            str(item.get("invocation_id")): item
            for item in row.get("invocations", ())
        }
        for label in row.get("labels", ()):
            invocation = features.get(str(label.get("invocation_id")), {})
            request_id = str(invocation.get("request_id") or "")
            request = requests.get(request_id, {})
            eligible = label["target_training_eligible"]
            boundary_proven = bool(
                request_id in action_proven
                and label.get("next_boundary_kind") in request["action_kinds"]
                and label.get("next_boundary_status") == "observed"
            )
            eligible["action_boundary"] = bool(eligible["action_boundary"] and boundary_proven)
            eligible["remaining_decode_demand"] = bool(
                eligible["remaining_decode_demand"] and request_id in demand_proven
            )
            invocation_calls = calls_by_invocation.get(
                (str(row.get("workflow_id")), str(label.get("invocation_id"))), ()
            )
            next_request = next(
                (
                    item for item in invocation_calls
                    if item.get("submit_ts_ms") is not None
                    and float(item["submit_ts_ms"]) > float(row["timestamp_ms"])
                ),
                {},
            )
            next_proven = str(next_request.get("request_id") or "") in demand_proven
            eligible["next_output_demand"] = bool(
                eligible["next_output_demand"] and next_proven
            )
            eligible["prompt_growth"] = bool(
                eligible["prompt_growth"] and next_proven
                and any(
                    item.get("invocation_id") == label.get("invocation_id")
                    and item.get("result_ts_ms") is not None
                    and float(item["result_ts_ms"]) <= float(row["timestamp_ms"])
                    and str(item.get("request_id") or "") in demand_proven
                    for item in invocation_calls
                )
            )
            eligible["external_wait"] = bool(
                healthy and eligible["external_wait"]
                and label.get("next_boundary_kind") == "tool_end"
                and label.get("next_boundary_status") in {"success", "error"}
            )
            eligible["join_wait"] = bool(
                healthy and eligible["join_wait"]
                and label.get("next_boundary_kind") == "join_satisfied"
                and (row.get("workflow_id"), label.get("invocation_id"))
                in eligible_joins
            )
        row["training_eligible"] = any(
            any(label["target_training_eligible"].values())
            for label in row["labels"]
        )

    for row in tables["external_waits"]:
        row["training_eligible_survival"] = bool(
            healthy and row["training_eligible_survival"]
            and row.get("status") in {"success", "error"}
        )
    for row in tables["reentries"]:
        if row.get("reentry_kind") == "tool_return":
            row["training_eligible"] = bool(
                healthy and row["training_eligible"]
                and row.get("terminal_status") in {"success", "error"}
            )
        if row.get("reentry_kind") == "join":
            row["training_eligible"] = bool(
                healthy and row["training_eligible"]
                and row.get("member_invocation_ids")
                and row.get("member_outcomes")
            )
        if row.get("reentry_kind") not in {"join", "tool_return"}:
            row["training_eligible"] = bool(healthy and row["training_eligible"])
    for row in tables["pcie_operations"]:
        row["training_eligible_service_curve"] = False
    for row in tables["censor_events"]:
        row["training_eligible"] = bool(healthy and row["training_eligible"])
    return {
        "schema_version": 1,
        "telemetry_complete": healthy,
        "status": dict(status),
        "complete_request_count": len(demand_proven),
        "missing_or_incomplete_request_count": len(requests) - len(demand_proven),
        "format": "server/native_telemetry_status.json",
    }



def _resolve_workload_roots(
    source: Path,
    *,
    workload_dirs: Sequence[str | Path] | None,
    allow_censored: bool,
) -> tuple[tuple[Path, ...], str]:
    if workload_dirs is not None:
        roots = tuple(Path(item).resolve() for item in workload_dirs)
        if not roots or any(not path.is_dir() for path in roots):
            raise P6CoverageError(f"invalid explicit workload roots: {roots}")
        status = "complete_multi_workload_layout" if len(roots) > 1 else "complete"
        return roots, status
    workloads = source / "workloads"
    if workloads.is_dir():
        return (workloads,), "complete"
    legacy = source / "autonomous"
    if legacy.is_dir():
        return (legacy,), "complete_legacy_autonomous_layout"
    incomplete = source / "workloads.incomplete"
    if allow_censored and incomplete.is_dir():
        return (incomplete,), "censored"
    raise P6CoverageError(f"run has no completed workloads: {source}")


def _trace_instance_id(path: Path) -> str:
    return path.parent.name


def _merge_workload_manifests(
    manifests: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if not manifests:
        return {}
    datasets = {str(item.get("dataset") or "unknown") for item in manifests}
    revisions = {str(item.get("dataset_revision") or "") for item in manifests}
    if len(datasets) != 1 or len(revisions) != 1:
        raise P6CoverageError("workload roots use inconsistent dataset revisions")
    merged = dict(manifests[0])
    merged["workload_manifest_sha256"] = [
        item.get("workload_manifest_sha256") for item in manifests
    ]
    return merged


def _merge_workload_summaries(
    summaries: Sequence[Mapping[str, Any]],
    *,
    selected_instance_ids: frozenset[str] | None,
) -> dict[str, Any]:
    workflows = [
        dict(item)
        for summary in summaries
        for item in summary.get("workflows", ())
        if isinstance(item, Mapping)
        and (
            selected_instance_ids is None
            or str(item.get("instance_id") or "") in selected_instance_ids
        )
    ]
    instances = [str(item.get("instance_id") or "") for item in workflows]
    if not workflows or "" in instances or len(instances) != len(set(instances)):
        raise P6CoverageError(
            "merged workload summaries have missing or duplicate instances"
        )
    if selected_instance_ids is not None and set(instances) != set(selected_instance_ids):
        raise P6CoverageError(
            "merged workload summaries do not match instance allowlist"
        )
    return {
        "workflow_count": len(workflows),
        "completed_workflows": sum(
            item.get("outcome") == "completed" for item in workflows
        ),
        "system_jct_eligible_workflows": sum(
            bool(item.get("system_jct_eligible")) for item in workflows
        ),
        "native_agent_jct_eligible_workflows": sum(
            bool(item.get("native_agent_jct_eligible")) for item in workflows
        ),
        "measurement_valid_workflows": sum(
            bool(item.get("measurement_valid")) for item in workflows
        ),
        "workflows": workflows,
    }


def _merge_collection_contracts(
    contracts: Sequence[Mapping[str, Any] | None],
) -> dict[str, Any] | None:
    if not contracts or any(item is None for item in contracts):
        return None
    values = [dict(item or {}) for item in contracts]
    for key in (
        "plan_id",
        "split",
        "runtime_policy",
        "predictor_enabled",
        "predictive_actions_enabled",
    ):
        encoded = {json.dumps(item.get(key), sort_keys=True) for item in values}
        if len(encoded) != 1:
            raise P6CoverageError(f"collection contracts disagree on {key}")
    merged = dict(values[0])
    merged["training_eligible"] = all(
        item.get("training_eligible") is True for item in values
    )
    merged["runtime_source_stable"] = all(
        item.get("runtime_source_stable") is True for item in values
    )
    merged["model_revision_stable"] = (
        all(item.get("model_revision_stable") is True for item in values)
        if any("model_revision_stable" in item for item in values)
        else None
    )
    merged["workflow_count"] = sum(
        int(item.get("workflow_count") or 0) for item in values
    )
    if any("raw_trace_eligible" in item for item in values):
        coverage_fields_present = all(
            type(item.get("trace_complete_workflows")) is int
            and type(item.get("workflow_count")) is int
            and type(item.get("raw_trace_min_coverage")) in {int, float}
            for item in values
        )
        if coverage_fields_present:
            complete_count = sum(
                int(item["trace_complete_workflows"]) for item in values
            )
            trace_coverage = (
                complete_count / merged["workflow_count"]
                if merged["workflow_count"]
                else 0.0
            )
            minimum_coverage = max(
                float(item["raw_trace_min_coverage"]) for item in values
            )
            merged["trace_complete_workflows"] = complete_count
            merged["raw_trace_coverage"] = trace_coverage
            merged["raw_trace_min_coverage"] = minimum_coverage
            merged["raw_trace_eligible"] = bool(
                merged["workflow_count"] > 0
                and trace_coverage >= minimum_coverage
                and merged["runtime_source_stable"]
                and (
                    merged["model_revision_stable"] is True
                    if any("model_revision_stable" in item for item in values)
                    else True
                )
            )
        else:
            merged["raw_trace_eligible"] = all(
                item.get("raw_trace_eligible") is True for item in values
            )
    merged["batch_ids"] = [item.get("batch_id") for item in values]
    merged["source_workload_manifests"] = [
        item.get("source_workload_manifest") for item in values
    ]
    return merged


def _resolve_run_identity(
    source: Path,
    *,
    workload_manifest: Mapping[str, Any],
    runtime_summary: Mapping[str, Any],
    evidence_paths: Sequence[Path],
) -> tuple[str, str]:
    candidates: list[tuple[str, str]] = []
    root_manifest_path = source / "manifest.json"
    if root_manifest_path.is_file():
        root_manifest = _read_object(root_manifest_path)
        value = str(root_manifest.get("run_id") or "").strip()
        if value:
            candidates.append((value, "root_manifest"))

    for origin, raw in (
        ("workload_manifest", workload_manifest),
        ("runtime_summary", runtime_summary),
    ):
        value = str(raw.get("run_id") or "").strip()
        if value:
            candidates.append((value, origin))

    distinct_ids = {value for value, _ in candidates}
    if len(distinct_ids) > 1:
        raise P6CoverageError(
            "run identity metadata conflicts: "
            + ", ".join(f"{origin}={value}" for value, origin in candidates)
        )
    if candidates:
        return candidates[0][0], candidates[0][1]

    # Legacy batch collections did not persist a run UUID. Fingerprinting the
    # frozen server streams gives the same evidence set a reproducible identity.
    digest = hashlib.sha256()
    hashed_paths = 0
    for path in evidence_paths:
        if not path.is_file():
            continue
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256(path).encode("ascii"))
        digest.update(b"\0")
        hashed_paths += 1
    if not hashed_paths:
        raise P6CoverageError("run has no stable run_id or fingerprintable evidence")
    return f"legacy-trace-{digest.hexdigest()[:24]}", "server_trace_fingerprint"


def _read_runtime_provenance(server: Path) -> dict[str, Any]:
    path = server / "runtime_profile_contract.json"
    if not path.is_file():
        return {"available": False}
    raw = _read_object(path)
    source = raw.get("source") or {}
    beliefkv = source.get("beliefkv") or {}
    sglang = source.get("sglang") or {}
    patch = sglang.get("canonical_patch") or {}
    return {
        "available": True,
        "contract_sha256": _sha256(path),
        "harness_revision": beliefkv.get("commit"),
        "beliefkv_state_sha256": beliefkv.get("state_sha256"),
        "sglang_commit": sglang.get("commit"),
        "sglang_state_sha256": sglang.get("state_sha256"),
        "sglang_patch_sha256": patch.get("sha256"),
    }


def _runtime_environment_contract(server: Path) -> dict[str, Any]:
    path = server / "runtime_profile_contract.json"
    if not path.is_file():
        return {}
    raw = _read_object(path)
    source = raw.get("source") or {}
    sglang = source.get("sglang") or {}
    revision_checks = raw.get("model", {}).get("revision_checks", ())
    if (
        raw.get("contract_state") != "validated"
        or raw.get("model", {}).get("passed") is not True
        or raw.get("hardware", {}).get("passed") is not True
        or raw.get("server", {}).get("passed") is not True
        or source.get("passed") is not True
        or any(item.get("passed") is not True for item in revision_checks)
    ):
        raise P6CoverageError(f"runtime profile contract was not validated: {path}")
    contract = {
        "runtime_profile": raw.get("runtime_profile"),
        "model_revision_sha256": {
            str(item.get("path")): str(item.get("actual_sha256"))
            for item in revision_checks
        },
        "hardware": raw.get("hardware", {}).get("actual"),
        "server_identity": raw.get("server", {}).get("identity"),
        "sglang_commit": sglang.get("commit"),
        "sglang_patch_sha256": (
            sglang.get("canonical_patch") or {}
        ).get("sha256"),
        "physical_source_count": 1,
        "uniform": True,
    }
    required = (
        contract.get("runtime_profile", {}).get("sha256"),
        contract.get("model_revision_sha256", {}).get("config.json"),
        contract.get("model_revision_sha256", {}).get("tokenizer.json"),
        contract.get("server_identity", {}).get("weight_dtype"),
        contract.get("server_identity", {}).get("resolved_kv_dtype"),
        contract.get("sglang_commit"),
        contract.get("sglang_patch_sha256"),
        contract.get("hardware", {}).get("uuid"),
    )
    if any(value in {None, ""} for value in required):
        raise P6CoverageError("runtime environment contract is incomplete")
    return contract


def _native_runtime_environment_contract(
    server: Path, collection: Mapping[str, Any] | None
) -> dict[str, Any]:
    path = server / "native_runtime_contract.json"
    if not path.is_file() or collection is None:
        return {}
    raw = _read_object(path)
    identity = raw.get("server_identity") or {}
    revisions = raw.get("model_revision_sha256") or {}
    hardware = raw.get("hardware") or {}
    if (
        raw.get("contract_state") != "validated"
        or raw.get("runtime_kind") != "native_reactive_v0520"
        or raw != collection.get("native_runtime_contract")
        or revisions != collection.get("model_revision_sha256")
        or identity != collection.get("server_identity")
        or raw.get("beliefkv_source_sha256")
        != (collection.get("runtime_source_fingerprint_start") or {}).get("digest")
        or not revisions.get("config.json")
        or not revisions.get("tokenizer.json")
        or not hardware.get("uuid")
        or identity.get("sglang_version") != "0.5.20"
        or not raw.get("sglang_patch_sha256")
        or not raw.get("sglang_commit")
    ):
        raise P6CoverageError(f"native runtime provenance is incomplete: {path}")
    return {
        "runtime_kind": "native_reactive_v0520",
        "runtime_profile": None,
        "model_revision_sha256": revisions,
        "hardware": hardware,
        "server_identity": identity,
        "sglang_commit": raw["sglang_commit"],
        "sglang_patch_sha256": raw["sglang_patch_sha256"],
        "physical_source_count": 1,
        "uniform": True,
    }


def _workflow_source_metadata(
    summaries: Sequence[Mapping[str, Any]],
    contracts: Sequence[Mapping[str, Any] | None],
    *,
    runtime_provenance: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for summary, contract in zip(summaries, contracts):
        source = contract or {}
        for workflow in summary.get("workflows", ()):
            workflow_id = str(workflow.get("workflow_id") or "")
            if not workflow_id:
                continue
            if workflow_id in result:
                raise P6CoverageError(f"duplicate workflow provenance: {workflow_id}")
            result[workflow_id] = {
                "source_batch_id": source.get("batch_id"),
                "fanout_profile": source.get("subagent_fanout_profile"),
                "harness_revision": runtime_provenance.get("harness_revision"),
                "runtime_contract_sha256": runtime_provenance.get(
                    "contract_sha256"
                ),
            }
    return result


def _runtime_intervention_cutoffs_many(
    workload_roots: Sequence[Path],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for root in workload_roots:
        for workflow_id, cutoff in _runtime_intervention_cutoffs(root).items():
            current = result.get(workflow_id)
            if current is None or float(cutoff["ts_ms"]) < float(current["ts_ms"]):
                result[workflow_id] = cutoff
    return result
def _read_collection_contract(workloads: Path) -> dict[str, Any] | None:
    contract_path = workloads / "p6_collection_contract.json"
    summary_path = workloads / "p6_collection_summary.json"
    contract = _read_object(contract_path) if contract_path.is_file() else None
    if summary_path.is_file():
        collection_summary = _read_object(summary_path).get("p6_collection")
        if isinstance(collection_summary, dict):
            contract = dict(collection_summary)
    return contract


def _read_workflow_exclusions(source: Path) -> dict[str, str]:
    path = source / P6_WORKFLOW_EXCLUSIONS_FILENAME
    if not path.is_file():
        return {}
    raw = _read_object(path)
    if raw.get("schema_version") != 1 or not isinstance(raw.get("workflows"), list):
        raise P6CoverageError(f"invalid workflow training exclusions: {path}")
    result: dict[str, str] = {}
    for item in raw["workflows"]:
        if not isinstance(item, Mapping):
            raise P6CoverageError(f"invalid workflow exclusion entry: {item!r}")
        instance_id = str(item.get("instance_id") or "")
        reason = str(item.get("reason") or "")
        if not instance_id or not reason:
            raise P6CoverageError(
                "workflow exclusion requires non-empty instance_id and reason"
            )
        if instance_id in result:
            raise P6CoverageError(f"duplicate workflow exclusion: {instance_id}")
        result[instance_id] = reason
    return result


def _apply_workflow_exclusions(
    tables: Mapping[str, list[dict[str, Any]]],
    exclusions: Mapping[str, str],
) -> None:
    """Fail closed for labels from a known harness-contaminated workflow."""

    if not exclusions:
        return
    for rows in tables.values():
        for row in rows:
            reason = exclusions.get(str(row.get("instance_id") or ""))
            if reason is None:
                continue
            row["split"] = None
            row["training_excluded"] = True
            row["training_exclusion_reason"] = reason
            for key in tuple(row):
                if key == "training_eligible" or key.startswith("training_eligible_"):
                    row[key] = False
            for label in row.get("labels", ()):
                for target in label.get("target_training_eligible", {}):
                    label["target_training_eligible"][target] = False


def _validate_collection_contract(
    contract: Mapping[str, Any] | None,
    *,
    allow_censored: bool,
    allow_development_only: bool = False,
    allow_formal_local_training: bool = False,
    native_reactive: bool = False,
) -> None:
    if native_reactive:
        if (
            contract is None
            or contract.get("runtime_policy") != "frozen_native_reactive_v0520"
            or contract.get("raw_trace_eligible") is not True
            or contract.get("runtime_source_stable") is not True
            or contract.get("model_revision_stable") is not True
            or bool(contract.get("predictor_enabled"))
            or bool(contract.get("predictive_actions_enabled"))
        ):
            raise P6CoverageError("native reactive raw trace provenance is ineligible")
        return
    if contract is None:
        return
    if bool(contract.get("predictor_enabled")) and not allow_development_only:
        raise P6CoverageError("P6 training evidence was collected with predictor enabled")
    if (
        bool(contract.get("predictive_actions_enabled"))
        and not allow_development_only
    ):
        raise P6CoverageError(
            "P6 training evidence was collected with predictive actions enabled"
        )
    if contract.get("runtime_policy") != "frozen_p5_observed":
        raise P6CoverageError("P6 collection did not use frozen_p5_observed")
    if contract.get("runtime_source_stable") is False:
        raise P6CoverageError("P6 collection runtime source was not stable")
    if (
        contract.get("training_eligible") is False
        and not allow_censored
        and not allow_formal_local_training
    ):
        raise P6CoverageError("P6 collection batch did not pass system eligibility")


def _workflow_metadata(
    summary: Mapping[str, Any],
    *,
    dataset_name: str,
    split_manifest: Mapping[str, Any] | None = None,
    workflow_exclusions: Mapping[str, str] | None = None,
    workflow_source_metadata: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    source_metadata = workflow_source_metadata or {}
    exclusions = workflow_exclusions or {}
    result: dict[str, dict[str, Any]] = {}
    for raw in summary.get("workflows", ()):
        if not isinstance(raw, Mapping):
            continue
        workflow_id = str(raw.get("workflow_id") or "")
        if not workflow_id:
            continue
        instance_id = str(raw.get("instance_id") or "unknown")
        project = str(raw.get("repo") or "")
        if not project:
            project = (
                instance_id.split("__", 1)[0]
                if "__" in instance_id
                else "unknown"
            )
        base_commit = str(raw.get("base_commit") or "") or None
        project_group_id = f"{dataset_name}:{project}"
        split = (
            resolve_split(
                split_manifest,
                dataset=dataset_name,
                project=project,
                instance_id=instance_id,
                base_commit=base_commit,
            )
            if split_manifest is not None
            else "development"
        )
        provenance = source_metadata.get(workflow_id, {})
        result[workflow_id] = {
            "instance_id": instance_id,
            "project": project,
            "base_commit": base_commit,
            "project_group_id": project_group_id,
            "workload_group_id": f"{dataset_name}:{instance_id}",
            "source_batch_id": provenance.get("source_batch_id"),
            "fanout_profile": provenance.get("fanout_profile"),
            "harness_revision": provenance.get("harness_revision"),
            "runtime_contract_sha256": provenance.get("runtime_contract_sha256"),
            "split": None if instance_id in exclusions else split,
            "training_excluded": instance_id in exclusions,
            "training_exclusion_reason": exclusions.get(instance_id),
            "system_jct_eligible": bool(raw.get("system_jct_eligible", False)),
            "native_agent_jct_eligible": bool(
                raw.get("native_agent_jct_eligible", False)
            ),
            "measurement_valid": bool(raw.get("measurement_valid", False)),
            "task_correctness_valid": bool(
                raw.get("task_correctness_valid", False)
            ),
            "guard_intervened": bool(
                (raw.get("agent_control") or {}).get(
                    "guard_intervened_completions", 0
                )
            ),
        }
    return result


def _metadata_fields(
    workflow_id: str,
    metadata: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    value = metadata.get(workflow_id, {})
    return {
        "instance_id": value.get("instance_id"),
        "source_batch_id": value.get("source_batch_id"),
        "fanout_profile": value.get("fanout_profile"),
        "harness_revision": value.get("harness_revision"),
        "runtime_contract_sha256": value.get("runtime_contract_sha256"),
        "project": value.get("project"),
        "base_commit": value.get("base_commit"),
        "project_group_id": value.get("project_group_id"),
        "workload_group_id": value.get("workload_group_id"),
        "split": value.get("split"),
        "training_excluded": bool(value.get("training_excluded", False)),
        "training_exclusion_reason": value.get("training_exclusion_reason"),
        "workflow_quality": {
            key: value.get(key)
            for key in (
                "system_jct_eligible",
                "native_agent_jct_eligible",
                "measurement_valid",
                "task_correctness_valid",
                "guard_intervened",
            )
        },
    }


def _request_rows(
    calls: list[dict[str, Any]],
    *,
    run_id: str,
    workflow_metadata: Mapping[str, Mapping[str, Any]],
    service_summary: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for call in calls:
        native = call.get("native") or {}
        request_id = str(call.get("agent_request_id") or "")
        service = service_summary.get(request_id, {})
        completed = call.get("result_ts_ms") is not None and not call.get(
            "censored", False
        )
        exact_identity = call.get("matching_method") == "exact_native_request_id"
        complete_demand = exact_identity and all(
            native.get(key) is not None
            for key in (
                "prompt_tokens",
                "cache_hit_tokens",
                "output_tokens",
                "submit_ts_ms",
                "result_ts_ms",
            )
        )
        exact_boundary = (
            call.get("action_boundary_token_index") is not None
            and call.get("action_boundary_source") == "native_incremental_parser"
        )
        workflow_id = str(call["workflow_id"])
        rows.append(
            {
                "schema_version": P6_DATASET_SCHEMA_VERSION,
                "row_type": "request_call",
                "run_id": run_id,
                "request_id": request_id or None,
                "workflow_id": workflow_id,
                "invocation_id": call.get("invocation_id"),
                "context_id": call.get("context_id"),
                "context_epoch": call.get("context_epoch"),
                "ordinal": call.get("ordinal"),
                "runtime_internal": bool(call.get("runtime_internal", False)),
                "censored": not completed,
                "censor_reason": call.get("censor_reason"),
                "matching_method": call.get("matching_method"),
                "matching_failure": call.get("matching_failure"),
                "submit_ts_ms": call.get("submit_ts_ms"),
                "result_ts_ms": call.get("result_ts_ms"),
                "wall_clock_ms": _duration(
                    call.get("submit_ts_ms"), call.get("result_ts_ms")
                ),
                "prompt_tokens": native.get("prompt_tokens"),
                "cache_hit_tokens": native.get("cache_hit_tokens"),
                "context_tokens": native.get("context_tokens"),
                "expected_output_tokens": native.get("expected_output_tokens"),
                "output_tokens": native.get("output_tokens"),
                "parser_status": call.get("parser_status"),
                "action_kinds": list(call.get("action_kinds", ())),
                "action_names": list(call.get("action_names", ())),
                "action_boundary_source": call.get("action_boundary_source"),
                "action_boundary_token_index": call.get(
                    "action_boundary_token_index"
                ),
                "service_interval_count": int(
                    service.get("service_interval_count", 0)
                ),
                "prefill_service_interval_count": int(
                    service.get("prefill_service_interval_count", 0)
                ),
                "decode_service_interval_count": int(
                    service.get("decode_service_interval_count", 0)
                ),
                "training_eligible_remaining_decode_demand": bool(
                    not call.get("runtime_internal", False)
                    and completed
                    and complete_demand
                    and service.get("decode_service_interval_count", 0) > 0
                ),
                "training_eligible_unlock_hazard": bool(
                    not call.get("runtime_internal", False)
                    and completed
                    and complete_demand
                    and exact_boundary
                    and service.get("decode_service_interval_count", 0) > 0
                ),
                **_metadata_fields(workflow_id, workflow_metadata),
            }
        )
    return rows


def _service_rows(
    audit_path: Path,
    *,
    calls: list[dict[str, Any]],
    run_id: str,
    workflow_metadata: Mapping[str, Mapping[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    call_by_request = {
        str(call["native"]["request_id"]): call
        for call in calls
        if call.get("matching_method") == "exact_native_request_id"
    }
    rows: list[dict[str, Any]] = []
    batch_rows: list[dict[str, Any]] = []
    summaries: dict[str, Counter[str]] = defaultdict(Counter)
    for record in _iter_jsonl(audit_path):
        if record.get("event") != "gpu_service_sample":
            continue
        raw_samples = tuple(
            raw
            for raw in (record.get("request_samples") or ())
            if isinstance(raw, Mapping)
        )
        normalized_samples: list[dict[str, Any]] = []
        for index, raw in enumerate(raw_samples):
            if not isinstance(raw, Mapping):
                continue
            request_id = str(raw.get("request_id") or "")
            call = call_by_request.get(request_id)
            workflow_id = str(raw.get("workflow_id") or "")
            if workflow_id not in workflow_metadata or call is None:
                continue
            phase = str(raw.get("phase") or record.get("phase") or "unknown")
            required = (
                request_id
                and workflow_id
                and raw.get("invocation_id") is not None
                and raw.get("context_id") is not None
                and raw.get("context_epoch") is not None
                and raw.get("token_delta") is not None
                and raw.get("sequence_tokens_before") is not None
                and record.get("service_start_ts_ms") is not None
                and record.get("complete_ts_ms") is not None
                and record.get("service_elapsed_ms") is not None
                and record.get("timing_semantics_version")
                == "gpu_service_interval_v1"
            )
            rows.append(
                {
                    "schema_version": P6_DATASET_SCHEMA_VERSION,
                    "row_type": "gpu_service_interval",
                    "run_id": run_id,
                    "sample_id": record.get("sample_id"),
                    "request_sample_index": index,
                    "request_id": request_id or None,
                    "workflow_id": workflow_id or None,
                    "invocation_id": raw.get("invocation_id"),
                    "context_id": raw.get("context_id"),
                    "context_epoch": raw.get("context_epoch"),
                    "phase": phase,
                    "token_delta": raw.get("token_delta"),
                    "token_delta_semantics": raw.get("token_delta_semantics"),
                    "sequence_tokens_before": raw.get("sequence_tokens_before"),
                    "output_tokens_before": raw.get("output_tokens_before"),
                    "batch_size": record.get("batch_size"),
                    "batch_service_start_ts_ms": record.get(
                        "service_start_ts_ms"
                    ),
                    "batch_service_complete_ts_ms": record.get("complete_ts_ms"),
                    "batch_service_elapsed_ms": record.get("service_elapsed_ms"),
                    "timing_semantics_version": record.get(
                        "timing_semantics_version"
                    ),
                    "request_identity_matched": call is not None,
                    "training_eligible": bool(required and call is not None),
                    "label_semantics": "shared_batch_service_interval",
                    "service_time_attribution": (
                        "batch elapsed is duplicated for identity joins only; "
                        "never fit per request"
                    ),
                    **_metadata_fields(workflow_id, workflow_metadata),
                }
            )
            normalized_samples.append(
                {
                    "request_id": request_id or None,
                    "workflow_id": workflow_id or None,
                    "invocation_id": raw.get("invocation_id"),
                    "context_id": raw.get("context_id"),
                    "context_epoch": raw.get("context_epoch"),
                    "phase": phase,
                    "token_delta": max(0, int(raw.get("token_delta") or 0)),
                    "sequence_tokens_before": max(
                        0, int(raw.get("sequence_tokens_before") or 0)
                    ),
                    "output_tokens_before": max(
                        0, int(raw.get("output_tokens_before") or 0)
                    ),
                }
            )
            if call is not None:
                summaries[request_id]["service_interval_count"] += 1
                summaries[request_id][f"{phase}_service_interval_count"] += 1
        if not normalized_samples:
            continue
        sample_id = str(record.get("sample_id") or "")
        phases = sorted({str(item["phase"]) for item in normalized_samples})
        sequences = [int(item["sequence_tokens_before"]) for item in normalized_samples]
        workflows = sorted(
            {str(item["workflow_id"]) for item in normalized_samples if item["workflow_id"]}
        )
        split_values = sorted(
            {
                str(workflow_metadata.get(workflow_id, {}).get("split"))
                for workflow_id in workflows
                if workflow_metadata.get(workflow_id, {}).get("split") is not None
            }
        )
        batch_rows.append(
            {
                "schema_version": P6_DATASET_SCHEMA_VERSION,
                "row_type": "gpu_batch_service_interval",
                "run_id": run_id,
                "sample_id": sample_id or None,
                "phase": phases[0] if len(phases) == 1 else "mixed",
                "batch_size": len(normalized_samples),
                "native_batch_size": int(
                    record.get("batch_size") or len(normalized_samples)
                ),
                "request_count": len(normalized_samples),
                "token_delta_total": sum(
                    int(item["token_delta"]) for item in normalized_samples
                ),
                "sequence_tokens_mean": sum(sequences) / len(sequences),
                "sequence_tokens_max": max(sequences),
                "request_samples": normalized_samples,
                "prefill_decode_mixed": len(phases) > 1,
                "chunk_position": record.get("chunk_position") or "unknown",
                "pcie_contention_state": record.get("pcie_contention_state") or "unknown",
                "hicache_inflight_bytes": record.get("hicache_inflight_bytes"),
                "service_start_ts_ms": record.get("service_start_ts_ms"),
                "complete_ts_ms": record.get("complete_ts_ms"),
                "service_elapsed_ms": record.get("service_elapsed_ms"),
                "timing_semantics_version": record.get("timing_semantics_version"),
                "timing_boundary": (
                    "scheduler/worker interval; not CUDA-event kernel time"
                ),
                "evidence_role": "runtime_validation",
                "training_eligible_service_curve": False,
                "workflow_ids": workflows,
                "splits": split_values,
            }
        )
    return (
        rows,
        batch_rows,
        {key: dict(value) for key, value in summaries.items()},
    )


def _external_and_reentry_rows(
    traces: list[list[dict[str, Any]]],
    *,
    run_id: str,
    workflow_metadata: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    external: list[dict[str, Any]] = []
    reentries: list[dict[str, Any]] = []
    for records in traces:
        open_tools: dict[str, tuple[RuntimeEvent, dict[str, Any]]] = {}
        joins: dict[str, dict[str, Any]] = {}
        return_ts: dict[str, float] = {}
        invocation_start_ts: dict[str, float] = {}
        context_by_invocation: dict[str, tuple[str | None, int | None]] = {}
        for raw in records:
            event = RuntimeEvent.from_dict(raw)
            workflow_id = event.workflow_id
            attrs = dict(event.attributes)
            if event.kind == RuntimeEventKind.INVOCATION_CREATE and event.invocation_id:
                invocation_start_ts[event.invocation_id] = event.ts_ms
            if event.kind == RuntimeEventKind.SPAWN and event.target_invocation_id:
                invocation_start_ts.setdefault(event.target_invocation_id, event.ts_ms)
            if event.kind == RuntimeEventKind.LLM_SUBMIT and event.invocation_id:
                context_by_invocation[event.invocation_id] = (
                    event.context_id,
                    event.context_epoch,
                )
            if event.kind == RuntimeEventKind.TOOL_START:
                tool_call_id = str(attrs.get("tool_call_id") or event.event_id)
                if tool_call_id in open_tools:
                    raise P6CoverageError(f"duplicate open tool call: {tool_call_id}")
                open_tools[tool_call_id] = (event, attrs)
            elif event.kind == RuntimeEventKind.TOOL_END:
                tool_call_id = str(attrs.get("tool_call_id") or event.event_id)
                opened = open_tools.pop(tool_call_id, None)
                if opened is None:
                    raise P6CoverageError(
                        f"tool terminal event without start: {tool_call_id}"
                    )
                start, start_attrs = opened
                context_id, context_epoch = context_by_invocation.get(
                    str(event.invocation_id or ""), (None, None)
                )
                observed_duration = _duration(start.ts_ms, event.ts_ms)
                row = {
                    "schema_version": P6_DATASET_SCHEMA_VERSION,
                    "row_type": "external_wait",
                    "run_id": run_id,
                    "tool_call_id": tool_call_id,
                    "workflow_id": workflow_id,
                    "invocation_id": event.invocation_id,
                    "context_id": context_id,
                    "context_epoch": context_epoch,
                    "tool_name": start_attrs.get("tool_name") or attrs.get("tool_name"),
                    "tool_family": start_attrs.get("tool_family"),
                    "backend_class": start_attrs.get("backend_class"),
                    "parameter_signature": start_attrs.get("parameter_signature"),
                    "start_ts_ms": start.ts_ms,
                    "terminal_ts_ms": event.ts_ms,
                    "observed_duration_ms": observed_duration,
                    "reported_duration_ms": attrs.get("duration_ms"),
                    "status": attrs.get("status"),
                    "tool_error_class": attrs.get("tool_error_class"),
                    "exception_type": attrs.get("exception_type"),
                    "output_chars": attrs.get("output_chars"),
                    "censored": False,
                    "training_eligible_survival": observed_duration is not None,
                    **_metadata_fields(workflow_id, workflow_metadata),
                }
                external.append(row)
                reentries.append(
                    {
                        "schema_version": P6_DATASET_SCHEMA_VERSION,
                        "row_type": "reentry",
                        "run_id": run_id,
                        "reentry_id": f"tool:{tool_call_id}",
                        "reentry_kind": "tool_return",
                        "workflow_id": workflow_id,
                        "invocation_id": event.invocation_id,
                        "cause_id": tool_call_id,
                        "wait_start_ts_ms": start.ts_ms,
                        "reentry_ts_ms": event.ts_ms,
                        "wait_duration_ms": observed_duration,
                        "terminal_status": attrs.get("status"),
                        "censored": False,
                        "training_eligible": observed_duration is not None,
                        **_metadata_fields(workflow_id, workflow_metadata),
                    }
                )
            elif event.kind == RuntimeEventKind.JOIN_CREATE and event.join_id:
                joins[event.join_id] = {
                    "workflow_id": workflow_id,
                    "member_invocation_ids": tuple(event.member_invocation_ids),
                    "create_ts_ms": event.ts_ms,
                }
            elif event.kind == RuntimeEventKind.JOIN_WAIT and event.join_id:
                state = joins.setdefault(event.join_id, {"workflow_id": workflow_id})
                state["waiter_invocation_id"] = event.invocation_id
                state["wait_ts_ms"] = event.ts_ms
            elif event.kind == RuntimeEventKind.RETURN and event.invocation_id:
                return_ts[event.invocation_id] = event.ts_ms
            elif event.kind in {
                RuntimeEventKind.JOIN_SATISFIED,
                RuntimeEventKind.JOIN_TIMEOUT,
            } and event.join_id:
                state = joins.setdefault(event.join_id, {"workflow_id": workflow_id})
                terminal = "satisfied" if event.kind == RuntimeEventKind.JOIN_SATISFIED else "timeout"
                reentries.append(
                    _join_reentry_row(
                        run_id,
                        event.join_id,
                        state,
                        terminal_ts_ms=event.ts_ms,
                        terminal_status=terminal,
                        return_ts=return_ts,
                        invocation_start_ts=invocation_start_ts,
                        workflow_metadata=workflow_metadata,
                    )
                )
                state["terminal"] = True
            elif event.kind in {RuntimeEventKind.REACTIVATE, RuntimeEventKind.MESSAGE}:
                reentries.append(
                    {
                        "schema_version": P6_DATASET_SCHEMA_VERSION,
                        "row_type": "reentry",
                        "run_id": run_id,
                        "reentry_id": event.event_id,
                        "reentry_kind": event.kind.value,
                        "workflow_id": workflow_id,
                        "invocation_id": event.invocation_id,
                        "cause_id": event.return_target_id or event.target_invocation_id,
                        "wait_start_ts_ms": None,
                        "reentry_ts_ms": event.ts_ms,
                        "wait_duration_ms": None,
                        "terminal_status": "observed",
                        "censored": False,
                        "training_eligible": True,
                        **_metadata_fields(workflow_id, workflow_metadata),
                    }
                )
        for tool_call_id, (start, attrs) in open_tools.items():
            workflow_id = start.workflow_id
            external.append(
                {
                    "schema_version": P6_DATASET_SCHEMA_VERSION,
                    "row_type": "external_wait",
                    "run_id": run_id,
                    "tool_call_id": tool_call_id,
                    "workflow_id": workflow_id,
                    "invocation_id": start.invocation_id,
                    "tool_name": attrs.get("tool_name"),
                    "tool_family": attrs.get("tool_family"),
                    "start_ts_ms": start.ts_ms,
                    "terminal_ts_ms": None,
                    "observed_duration_ms": None,
                    "status": "censored",
                    "censored": True,
                    "training_eligible_survival": False,
                    **_metadata_fields(workflow_id, workflow_metadata),
                }
            )
        for join_id, state in joins.items():
            if state.get("wait_ts_ms") is None or state.get("terminal"):
                continue
            reentries.append(
                _join_reentry_row(
                    run_id,
                    join_id,
                    state,
                    terminal_ts_ms=None,
                    terminal_status="censored",
                    return_ts=return_ts,
                    invocation_start_ts=invocation_start_ts,
                    workflow_metadata=workflow_metadata,
                )
            )
    return external, reentries


def _join_reentry_row(
    run_id: str,
    join_id: str,
    state: Mapping[str, Any],
    *,
    terminal_ts_ms: float | None,
    terminal_status: str,
    return_ts: Mapping[str, float],
    invocation_start_ts: Mapping[str, float],
    workflow_metadata: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    workflow_id = str(state.get("workflow_id") or "")
    wait_ts = state.get("wait_ts_ms")
    members = tuple(str(item) for item in state.get("member_invocation_ids", ()))
    member_rows = [
        {
            "invocation_id": member,
            "start_ts_ms": invocation_start_ts.get(member),
            "return_ts_ms": return_ts.get(member),
            "lifetime_ms": _duration(
                invocation_start_ts.get(member), return_ts.get(member)
            ),
            "return_offset_from_wait_ms": _duration(wait_ts, return_ts.get(member)),
        }
        for member in members
    ]
    complete = terminal_status == "satisfied" and all(
        row["return_ts_ms"] is not None for row in member_rows
    )
    return {
        "schema_version": P6_DATASET_SCHEMA_VERSION,
        "row_type": "reentry",
        "run_id": run_id,
        "reentry_id": f"join:{join_id}",
        "reentry_kind": "join",
        "workflow_id": workflow_id,
        "invocation_id": state.get("waiter_invocation_id"),
        "cause_id": join_id,
        "member_invocation_ids": list(members),
        "member_outcomes": member_rows,
        "wait_start_ts_ms": wait_ts,
        "reentry_ts_ms": terminal_ts_ms,
        "wait_duration_ms": _duration(wait_ts, terminal_ts_ms),
        "terminal_status": terminal_status,
        "censored": terminal_ts_ms is None,
        "training_eligible": complete and wait_ts is not None,
        **_metadata_fields(workflow_id, workflow_metadata),
    }


def _censor_rows(
    traces: Iterable[Iterable[Mapping[str, Any]]],
    *,
    run_id: str,
    workflow_metadata: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for records in traces:
        for raw in records:
            event = RuntimeEvent.from_dict(raw)
            if event.kind != RuntimeEventKind.CALL_CENSORED:
                continue
            attrs = dict(event.attributes)
            rows.append(
                {
                    "schema_version": P6_DATASET_SCHEMA_VERSION,
                    "row_type": "censor_event",
                    "run_id": run_id,
                    "censor_event_id": event.event_id,
                    "timestamp_ms": event.ts_ms,
                    "workflow_id": event.workflow_id,
                    "invocation_id": event.invocation_id,
                    "context_id": event.context_id,
                    "context_epoch": event.context_epoch,
                    "call_kind": attrs.get("call_kind"),
                    "censor_reason": attrs.get("censor_reason"),
                    "request_id": attrs.get("request_id"),
                    "tool_call_id": attrs.get("tool_call_id"),
                    "tool_name": attrs.get("tool_name"),
                    "parameter_signature": attrs.get("parameter_signature"),
                    "exception_type": attrs.get("exception_type"),
                    "invocation_identity_fallback": bool(
                        attrs.get("invocation_identity_fallback", False)
                    ),
                    "training_eligible": bool(
                        attrs.get("censor_reason")
                        and (attrs.get("request_id") or attrs.get("tool_call_id"))
                    ),
                    **_metadata_fields(event.workflow_id, workflow_metadata),
                }
            )
    return rows


def _pcie_rows(path: Path, *, run_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in _iter_jsonl(path):
        completed = record.get("status") == "completed"
        direct_dma = (
            record.get("start_timestamp_semantics") == "dma_start"
            and record.get("start_ts_ms") is not None
        )
        submit_duration = _duration(
            record.get("submit_ts_ms"), record.get("complete_ts_ms")
        )
        direct_duration = (
            _duration(record.get("start_ts_ms"), record.get("complete_ts_ms"))
            if direct_dma
            else None
        )
        native_stream_ms = record.get("transfer_stream_elapsed_ms")
        native_submit_to_ack_ms = record.get("submit_to_ack_ms")
        native_unacked = record.get("native_unacked_bytes_at_submit")
        native_service = (
            record.get("telemetry_origin") == "native_hicache_ack_v0520"
            and completed
            and type(record.get("actual_bytes")) is int
            and record["actual_bytes"] > 0
            and type(native_stream_ms) in (int, float)
            and math.isfinite(native_stream_ms)
            and native_stream_ms > 0
            and type(native_submit_to_ack_ms) in (int, float)
            and math.isfinite(native_submit_to_ack_ms)
            and native_submit_to_ack_ms > 0
            and type(native_unacked) is int
            and native_unacked >= 0
            and submit_duration is not None
        )
        conditioning_complete = all(
            (
                record.get("direction") is not None,
                record.get("actual_bytes") is not None,
                record.get("page_count") is not None,
                record.get("command_kind") is not None,
                record.get("host_copy_state") not in {None, "unknown"},
                isinstance(record.get("pinned_host"), bool),
                record.get("native_concurrent_bytes") is not None,
                record.get("allocator_submit_ms") is not None,
                record.get("callback_overhead_ms") is not None,
            )
        )
        rows.append(
            {
                "schema_version": P6_DATASET_SCHEMA_VERSION,
                "row_type": "pcie_operation",
                "run_id": run_id,
                "command_id": record.get("command_id"),
                "command_kind": record.get("command_kind"),
                "telemetry_origin": record.get("telemetry_origin"),
                "node_ids": record.get("node_ids"),
                "num_tokens_by_pool": record.get("num_tokens_by_pool"),
                "status": record.get("status"),
                "reason": record.get("reason"),
                "direction": record.get("direction"),
                "actual_bytes": record.get("actual_bytes"),
                "closure_bytes": record.get("closure_bytes"),
                "page_count": record.get("page_count"),
                "source_tier": record.get("source_tier"),
                "target_tier": record.get("target_tier"),
                "host_copy_state": record.get("host_copy_state"),
                "pinned_host": record.get("pinned_host"),
                "native_concurrent_bytes": record.get("native_concurrent_bytes"),
                "native_inflight_operation_count_at_submit": record.get(
                    "native_inflight_operation_count_at_submit"
                ),
                "native_unacked_bytes_at_submit": native_unacked,
                "transfer_stream_elapsed_ms": native_stream_ms,
                "allocator_submit_ms": record.get("allocator_submit_ms"),
                "allocator_wait_ms": record.get("allocator_wait_ms"),
                "compute_wait_ms": record.get("compute_wait_ms"),
                "callback_overhead_ms": record.get("callback_overhead_ms"),
                "submit_ts_ms": record.get("submit_ts_ms"),
                "start_ts_ms": record.get("start_ts_ms"),
                "complete_ts_ms": record.get("complete_ts_ms"),
                "start_timestamp_semantics": record.get(
                    "start_timestamp_semantics"
                ),
                "submit_to_complete_ms": (
                    native_submit_to_ack_ms
                    if native_service else submit_duration
                ),
                "direct_dma_duration_ms": direct_duration,
                "duration_label_kind": (
                    "native_transfer_stream"
                    if native_service else (
                        "direct_dma"
                        if direct_duration is not None else "submit_to_complete"
                    )
                ),
                "training_eligible_service_curve": bool(
                    native_service or (
                        completed and conditioning_complete
                        and submit_duration is not None
                    )
                ),
            }
        )
    return rows


def _dataset_integrity(
    tables: Mapping[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    requests = tables["request_calls"]
    service = tables["gpu_service_intervals"]
    batch_service = tables["gpu_batch_service_intervals"]
    external = tables["external_waits"]
    reentries = tables["reentries"]
    pcie = tables["pcie_operations"]
    censors = tables["censor_events"]
    decisions = tables["frontier_decision_points"]
    def identities(rows: Iterable[Mapping[str, Any]], field: str) -> list[tuple[Any, Any]]:
        return [(row.get("run_id"), row.get(field)) for row in rows]

    request_ids = identities(requests, "request_id")
    known_request_ids = {item for item in request_ids if item[1]}
    service_request_ids = set(identities(service, "request_id"))
    batch_sample_ids = identities(batch_service, "sample_id")
    external_ids = identities(external, "tool_call_id")
    reentry_ids = identities(reentries, "reentry_id")
    command_ids = identities(pcie, "command_id")
    censor_ids = identities(censors, "censor_event_id")
    decision_ids = identities(decisions, "decision_id")
    violations = {
        "missing_request_id_count": sum(item[1] is None for item in request_ids),
        "duplicate_request_id_count": len(request_ids) - len(set(request_ids)),
        "dangling_service_request_id_count": len(
            service_request_ids - known_request_ids
        ),
        "missing_batch_sample_id_count": sum(
            item[1] is None for item in batch_sample_ids
        ),
        "duplicate_batch_sample_id_count": len(batch_sample_ids)
        - len(set(batch_sample_ids)),
        "invalid_batch_request_count": sum(
            int(row.get("request_count") or 0)
            != len(row.get("request_samples") or ())
            for row in batch_service
        ),
        "duplicate_tool_call_id_count": len(external_ids) - len(set(external_ids)),
        "duplicate_reentry_id_count": len(reentry_ids) - len(set(reentry_ids)),
        "missing_pcie_command_id_count": sum(
            item[1] is None for item in command_ids
        ),
        "duplicate_pcie_command_id_count": len(command_ids) - len(set(command_ids)),
        "duplicate_censor_event_id_count": len(censor_ids) - len(set(censor_ids)),
        "duplicate_decision_id_count": len(decision_ids) - len(set(decision_ids)),
        "partial_decision_scope_count": sum(
            not bool((row.get("scope") or {}).get("closure_complete"))
            for row in decisions
        ),
        "invalid_join_eligible_count": sum(
            row.get("reentry_kind") == "join"
            and row.get("training_eligible")
            and any(
                member.get("return_ts_ms") is None
                for member in row.get("member_outcomes", ())
            )
            for row in reentries
        ),
    }
    return {
        "passes": not any(violations.values()),
        "violations": violations,
        "foreign_key_contract": (
            "gpu_service_intervals.request_id -> request_calls.request_id; "
            "gpu_batch_service_intervals.sample_id is unique"
        ),
    }


_RUNTIME_INTERVENTION_EVENTS = {
    "agent_graph_budget_finalization": "graph_step_budget_finalization",
    "agent_stuck_detected": "loop_guard_finalization",
}


def _runtime_intervention_cutoffs(
    workloads: Path,
) -> dict[str, dict[str, Any]]:
    """Find the first runtime prompt intervention in each workflow timeline."""

    cutoffs: dict[str, dict[str, Any]] = {}
    for trace_path in discover_p6_agent_traces(workloads):
        workflow_dir = trace_path.parent
        result_path = workflow_dir / "result.json"
        audit_path = workflow_dir / "sandbox_audit.jsonl"
        if not result_path.is_file() or not audit_path.is_file():
            continue
        result = _read_object(result_path)
        workflow_id = str(result.get("workflow_id") or "")
        if not workflow_id:
            continue
        for record in _iter_jsonl(audit_path):
            event = str(record.get("event") or "")
            reason = _RUNTIME_INTERVENTION_EVENTS.get(event)
            if reason is None or record.get("ts_ms") is None:
                continue
            candidate = {
                "ts_ms": float(record["ts_ms"]),
                "event_id": f"sandbox-audit:{record.get('sequence', 'unknown')}",
                "event": event,
                "reason": reason,
                "agent_scope": record.get("agent_scope"),
            }
            current = cutoffs.get(workflow_id)
            if current is None or candidate["ts_ms"] < current["ts_ms"]:
                cutoffs[workflow_id] = candidate
    return cutoffs



def _apply_runtime_intervention_censors(
    rows: list[dict[str, Any]],
    cutoffs: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Censor only target horizons that cross a runtime prompt intervention."""

    censored_rows = 0
    censored_labels = 0
    right_censored_targets = 0
    reason_counts: Counter[str] = Counter()
    for row in rows:
        cutoff = cutoffs.get(str(row.get("workflow_id") or ""))
        if cutoff is None:
            row["clean_episode_eligible"] = True
            row["episode_training_scope"] = "clean_full_episode"
            continue
        row["eligible_until_event_id"] = cutoff.get("event_id")
        row["clean_episode_eligible"] = False
        row["episode_training_scope"] = "local_pre_intervention_only"
        cutoff_ts = float(cutoff["ts_ms"])
        row_ts = float(row.get("timestamp_ms") or 0.0)
        reason = str(cutoff["reason"])
        row_affected = False
        for label in row.get("labels", ()):
            eligibility = dict(label.get("target_training_eligible") or {})
            horizons = dict(label.get("target_horizon_timestamp_ms") or {})
            if not eligibility:
                boundary = label.get("next_boundary_timestamp_ms")
                eligibility = {"action_boundary": boundary is not None}
                horizons = {"action_boundary": boundary}
            target_reasons = dict(label.get("target_censor_reasons") or {})
            target_right_censored = dict(label.get("target_right_censored") or {})
            label_affected = False
            for target, eligible in tuple(eligibility.items()):
                if not eligible:
                    continue
                horizon = horizons.get(target)
                affected = row_ts >= cutoff_ts or (
                    horizon is not None and float(horizon) >= cutoff_ts
                )
                if not affected:
                    continue
                label_affected = True
                row_affected = True
                target_reasons[target] = reason
                if target == "external_wait" and row_ts < cutoff_ts:
                    target_right_censored[target] = True
                    label["external_wait_observed_duration_ms"] = max(
                        0.0, cutoff_ts - row_ts
                    )
                    right_censored_targets += 1
                else:
                    eligibility[target] = False
            label["target_training_eligible"] = eligibility
            label["target_censor_reasons"] = target_reasons
            label["target_right_censored"] = target_right_censored
            if label_affected:
                censored_labels += 1
            label["censored"] = not any(eligibility.values())
            if label["censored"]:
                label["censor_reason"] = reason
        row["training_eligible"] = any(
            any((label.get("target_training_eligible") or {}).values())
            for label in row.get("labels", ())
        )
        if not row_affected:
            continue
        row["censor_reasons"] = sorted(
            {*row.get("censor_reasons", ()), reason}
        )
        row["runtime_intervention"] = dict(cutoff)
        censored_rows += 1
        reason_counts[reason] += 1
    return {
        "workflow_count": len(cutoffs),
        "decision_row_count": censored_rows,
        "invocation_label_count": censored_labels,
        "right_censored_target_count": right_censored_targets,
        "reason_counts": dict(sorted(reason_counts.items())),
        "semantics": (
            "target horizons crossing the first runtime prompt intervention are "
            "censored independently; completed pre-intervention labels and unrelated "
            "invocations in the same decision row remain formal local evidence"
        ),
    }


def _apply_partial_episode_eligibility(
    tables: Mapping[str, list[dict[str, Any]]],
    cutoffs: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Apply label-level censoring while excluding terminal/JCT trajectory labels."""

    affected_workflows = set(cutoffs)
    counts: Counter[str] = Counter()
    for table_name, rows in tables.items():
        for row in rows:
            workflow_id = str(row.get("workflow_id") or "")
            cutoff = cutoffs.get(workflow_id)
            if cutoff is None:
                continue
            cutoff_ts = float(cutoff["ts_ms"])
            reason = str(cutoff["reason"])
            row["clean_episode_eligible"] = False
            row["episode_training_scope"] = "local_pre_intervention_only"
            row["eligible_until_event_id"] = cutoff.get("event_id")
            if table_name == "request_calls":
                result_ts = row.get("result_ts_ms")
                completed_before = (
                    result_ts is not None and float(result_ts) < cutoff_ts
                )
                row["training_eligible_unlock_hazard"] = bool(
                    row.get("training_eligible_unlock_hazard") and completed_before
                )
                row["training_eligible_remaining_decode_demand"] = bool(
                    row.get("training_eligible_remaining_decode_demand")
                    and completed_before
                )
                if not completed_before:
                    row["censored"] = True
                    row["censor_reason"] = reason
                    counts["request_demand_right_censored"] += 1
                else:
                    counts["request_demand_retained"] += 1
            elif table_name == "external_waits":
                start_ts = row.get("start_ts_ms")
                terminal_ts = row.get("terminal_ts_ms")
                if terminal_ts is not None and float(terminal_ts) < cutoff_ts:
                    counts["external_survival_retained"] += 1
                elif start_ts is not None and float(start_ts) < cutoff_ts:
                    row["survival_censored"] = True
                    row["survival_censor_reason"] = reason
                    row["survival_duration_ms"] = max(
                        0.0, cutoff_ts - float(start_ts)
                    )
                    row["training_eligible_survival"] = True
                    counts["external_survival_right_censored"] += 1
                else:
                    row["training_eligible_survival"] = False
                    counts["external_survival_excluded"] += 1
            elif table_name == "reentries":
                reentry_ts = row.get("reentry_ts_ms")
                wait_ts = row.get("wait_start_ts_ms")
                if reentry_ts is not None and float(reentry_ts) < cutoff_ts:
                    counts["reentry_retained"] += 1
                elif wait_ts is not None and float(wait_ts) < cutoff_ts:
                    row["censored"] = True
                    row["right_censored"] = True
                    row["censor_reason"] = reason
                    row["censor_duration_ms"] = max(0.0, cutoff_ts - float(wait_ts))
                    row["training_eligible"] = False
                    counts["reentry_right_censored"] += 1
                else:
                    row["training_eligible"] = False
                    counts["reentry_excluded"] += 1
            elif table_name == "frontier_decision_points":
                counts["decision_rows_scoped"] += 1
    return {
        "workflow_count": len(affected_workflows),
        "counts": dict(sorted(counts.items())),
        "allowed_targets": [
            "completed_pre_intervention_local_labels",
            "right_censored_tool_survival",
            "local_hardware_service",
        ],
        "disabled_targets": [
            "workflow_jct",
            "terminal_outcome",
            "guard_hazard",
            "post_intervention_labels",
            "complete_trajectory",
        ],
    }


def _duration(start: Any, end: Any) -> float | None:
    if start is None or end is None:
        return None
    return max(0.0, float(end) - float(start))


def _write_jsonl_atomic(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, sort_keys=True) + "\n")
    temporary.replace(path)


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
