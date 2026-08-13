from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from beliefkv.experiments.p6_coverage import P6CoverageError, _read_object
from beliefkv.experiments.p6_dataset import _sha256


@dataclass(frozen=True)
class CanonicalSourceSpec:
    source_id: str
    run_dir: Path
    workload_dirs: tuple[Path, ...]
    instance_ids: tuple[str, ...]
    source_kind: str


def _resolve(repository_root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (repository_root / path).resolve()


def _verify_sha256(path: Path, expected: str | None, *, label: str) -> None:
    if not path.is_file():
        raise P6CoverageError(f"missing canonical {label}: {path}")
    if expected and _sha256(path) != expected:
        raise P6CoverageError(f"canonical {label} digest mismatch: {path}")


def _workloads_parent(result_path: Path) -> Path:
    for parent in result_path.parents:
        if parent.name == "workloads":
            return parent
    raise P6CoverageError(f"replacement result has no workloads parent: {result_path}")


def build_canonical_source_specs(
    selection_manifest: Mapping[str, Any],
    *,
    repository_root: str | Path,
) -> tuple[CanonicalSourceSpec, ...]:
    root = Path(repository_root).resolve()
    source_plan_raw = selection_manifest.get("source_plan") or {}
    source_plan_path = _resolve(root, str(source_plan_raw.get("path") or ""))
    _verify_sha256(
        source_plan_path,
        str(source_plan_raw.get("sha256") or "") or None,
        label="collection plan",
    )
    plan = _read_object(source_plan_path)
    batches = {
        str(item.get("batch_id") or ""): item for item in plan.get("batches", ())
    }
    if "" in batches or len(batches) != len(plan.get("batches", ())):
        raise P6CoverageError("collection plan has invalid batch identities")

    replacement_rows = tuple(selection_manifest.get("replacements", ()))
    replacements = {
        str(item.get("instance_id") or ""): item for item in replacement_rows
    }
    if "" in replacements or len(replacements) != len(replacement_rows):
        raise P6CoverageError("selection manifest has invalid replacement identities")
    original_replacements: dict[str, set[str]] = {}
    replacement_results: list[Path] = []
    for instance_id, item in replacements.items():
        batch_id = str(item.get("original_batch_id") or "")
        original_replacements.setdefault(batch_id, set()).add(instance_id)
        selected_result = _resolve(root, str(item.get("selected_result") or ""))
        _verify_sha256(
            selected_result,
            str(item.get("selected_result_sha256") or "") or None,
            label=f"replacement result {instance_id}",
        )
        replacement_results.append(selected_result)

    source_rows = {
        str(item.get("batch_id") or ""): item
        for item in selection_manifest.get("source_runs", ())
    }
    if set(source_rows) != set(batches):
        raise P6CoverageError("selection source runs do not match collection batches")

    specs: list[CanonicalSourceSpec] = []
    selected: set[str] = set()
    for batch_id, batch in batches.items():
        source = source_rows[batch_id]
        run_dir = _resolve(root, str(source.get("run_path") or ""))
        _verify_sha256(
            run_dir / "workloads" / "summary.json",
            str(source.get("summary_sha256") or "") or None,
            label=f"summary {batch_id}",
        )
        _verify_sha256(
            run_dir / "server" / "runtime_profile_contract.json",
            str(source.get("runtime_contract_sha256") or "") or None,
            label=f"runtime contract {batch_id}",
        )
        instances = tuple(
            sorted(
                set(str(item) for item in batch.get("instance_ids", ()))
                - original_replacements.get(batch_id, set())
            )
        )
        if not instances:
            raise P6CoverageError(f"canonical source {batch_id} is empty")
        overlap = selected.intersection(instances)
        if overlap:
            raise P6CoverageError(f"duplicate original selections: {sorted(overlap)}")
        selected.update(instances)
        specs.append(
            CanonicalSourceSpec(
                source_id=f"original-{batch_id}",
                run_dir=run_dir,
                workload_dirs=(run_dir / "workloads",),
                instance_ids=instances,
                source_kind="original",
            )
        )

    replacement_run = selection_manifest.get("replacement_run") or {}
    replacement_run_dir = _resolve(root, str(replacement_run.get("run_path") or ""))
    _verify_sha256(
        replacement_run_dir / "server" / "runtime_profile_contract.json",
        str(replacement_run.get("runtime_contract_sha256") or "") or None,
        label="replacement runtime contract",
    )
    replacement_instances = tuple(sorted(replacements))
    overlap = selected.intersection(replacement_instances)
    if overlap:
        raise P6CoverageError(f"replacement duplicated original rows: {sorted(overlap)}")
    selected.update(replacement_instances)
    specs.append(
        CanonicalSourceSpec(
            source_id="replacement-run",
            run_dir=replacement_run_dir,
            workload_dirs=tuple(
                sorted({_workloads_parent(path) for path in replacement_results})
            ),
            instance_ids=replacement_instances,
            source_kind="replacement",
        )
    )

    expected = {
        str(item)
        for batch in batches.values()
        for item in batch.get("instance_ids", ())
    }
    expected_count = int(
        (selection_manifest.get("selection_rule") or {}).get(
            "expected_unique_instances", len(expected)
        )
    )
    if selected != expected or len(selected) != expected_count:
        raise P6CoverageError(
            "canonical instance conservation failed: "
            f"selected={len(selected)}, expected={len(expected)}"
        )
    return tuple(specs)


P6_TABLES = (
    "request_calls",
    "gpu_service_intervals",
    "gpu_batch_service_intervals",
    "external_waits",
    "reentries",
    "pcie_operations",
    "censor_events",
    "frontier_decision_points",
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _table_identity(table: str, row: Mapping[str, Any]) -> tuple[Any, ...]:
    run_id = row.get("run_id")
    fields = {
        "request_calls": (run_id, row.get("request_id")),
        "gpu_service_intervals": (
            run_id,
            row.get("sample_id"),
            row.get("request_sample_index"),
        ),
        "gpu_batch_service_intervals": (run_id, row.get("sample_id")),
        "external_waits": (run_id, row.get("tool_call_id")),
        "reentries": (run_id, row.get("reentry_id")),
        "pcie_operations": (run_id, row.get("command_id")),
        "censor_events": (run_id, row.get("censor_event_id")),
        "frontier_decision_points": (run_id, row.get("decision_id")),
    }
    return fields[table]


def _runtime_environment_contract(
    source_dirs: Sequence[tuple[CanonicalSourceSpec, Path]],
) -> dict[str, Any]:
    contracts = []
    for spec, _ in source_dirs:
        path = spec.run_dir / "server" / "runtime_profile_contract.json"
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
            raise P6CoverageError(f"runtime contract was not validated: {path}")
        contracts.append(
            {
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
            }
        )
    fingerprints = {
        json.dumps(item, sort_keys=True, separators=(",", ":"))
        for item in contracts
    }
    if len(fingerprints) != 1:
        raise P6CoverageError("canonical sources disagree on runtime environment")
    contract = contracts[0]
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
        raise P6CoverageError("canonical runtime environment is incomplete")
    contract["physical_source_count"] = len(contracts)
    contract["uniform"] = True
    return contract


def merge_p6_source_datasets(
    source_dirs: Sequence[tuple[CanonicalSourceSpec, Path]],
    output_dir: str | Path,
    *,
    selection_manifest_path: str | Path,
    expected_instance_ids: Sequence[str],
) -> dict[str, Any]:
    from beliefkv.experiments.p6_dataset import (
        P6_DATASET_SCHEMA_VERSION,
        _dataset_integrity,
        _write_json_atomic,
        _write_jsonl_atomic,
    )

    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    tables: dict[str, list[dict[str, Any]]] = {name: [] for name in P6_TABLES}
    manifests: list[dict[str, Any]] = []
    source_entries: list[dict[str, Any]] = []
    physical_run_ids: set[str] = set()
    environment_contract = _runtime_environment_contract(source_dirs)
    for spec, directory in source_dirs:
        manifest_path = directory / "dataset_manifest.json"
        manifest = _read_object(manifest_path)
        if manifest.get("formal_local_training_eligible") is not True:
            raise P6CoverageError(
                f"canonical source is not formal local evidence: {directory}"
            )
        run_id = str((manifest.get("source") or {}).get("run_id") or "")
        if not run_id or run_id in physical_run_ids:
            raise P6CoverageError(f"duplicate or missing physical source run: {run_id}")
        physical_run_ids.add(run_id)
        manifests.append(manifest)
        source_entries.append(
            {
                "source_id": spec.source_id,
                "source_kind": spec.source_kind,
                "run_id": run_id,
                "run_dir": str(spec.run_dir),
                "workload_dirs": [str(path) for path in spec.workload_dirs],
                "dataset_manifest_sha256": _sha256(manifest_path),
                "instance_ids": list(spec.instance_ids),
            }
        )
        provenance = (manifest.get("source") or {}).get("runtime_provenance") or {}
        for table in P6_TABLES:
            table_path = directory / f"{table}.jsonl"
            for row in _read_jsonl(table_path):
                row["canonical_source_id"] = spec.source_id
                row.setdefault("harness_revision", provenance.get("harness_revision"))
                tables[table].append(row)

    for table, rows in tables.items():
        identities = [_table_identity(table, row) for row in rows]
        if any(
            any(value in {None, ""} for value in identity)
            for identity in identities
        ):
            raise P6CoverageError(f"canonical {table} contains missing identities")
        if len(identities) != len(set(identities)):
            raise P6CoverageError(f"canonical {table} contains duplicate identities")

    expected = set(expected_instance_ids)
    observed = {
        str(row.get("instance_id") or "")
        for row in tables["request_calls"]
        if row.get("instance_id")
    }
    if observed != expected:
        raise P6CoverageError(
            "canonical request instances mismatch: "
            f"missing={sorted(expected - observed)}, extra={sorted(observed - expected)}"
        )
    integrity = _dataset_integrity(tables)
    if not integrity["passes"]:
        raise P6CoverageError(
            f"canonical dataset integrity failed: {integrity['violations']}"
        )

    table_manifest: dict[str, Any] = {}
    for table, rows in tables.items():
        path = destination / f"{table}.jsonl"
        _write_jsonl_atomic(path, rows)
        table_manifest[table] = {
            "path": path.name,
            "row_count": len(rows),
            "sha256": _sha256(path),
        }

    split_digests = {
        str((item.get("split_contract") or {}).get("manifest_digest") or "")
        for item in manifests
    }
    if len(split_digests) != 1 or "" in split_digests:
        raise P6CoverageError("canonical sources disagree on frozen split manifest")
    selection_path = Path(selection_manifest_path).resolve()
    selection_sha = _sha256(selection_path)
    canonical_run_id = "canonical-" + selection_sha[:24]
    all_workflows = {
        str(row.get("workflow_id")) for row in tables["request_calls"]
    }
    clean_workflows = {
        str(row.get("workflow_id"))
        for row in tables["request_calls"]
        if (row.get("workflow_quality") or {}).get("native_agent_jct_eligible")
    }
    harness_revisions = sorted(
        {
            str(
                ((item.get("source") or {}).get("runtime_provenance") or {}).get(
                    "harness_revision"
                )
            )
            for item in manifests
        }
    )
    collection_contract = {
        "plan_id": "h200-bf16-formal-train-v1",
        "split": "train",
        "training_eligible": len(clean_workflows) == len(all_workflows),
        "formal_local_training_eligible": True,
        "runtime_source_stable": True,
        "runtime_source_uniform": False,
        "runtime_policy": "frozen_p5_observed",
        "predictor_enabled": False,
        "predictive_actions_enabled": False,
        "harness_revisions": harness_revisions,
    }
    split_counts = Counter(
        str(row.get("split"))
        for row in tables["request_calls"]
        if row.get("split") is not None
    )
    workload_hashes = [
        value
        for item in manifests
        for value in (
            (item.get("source") or {}).get("workload_manifest_sha256") or []
        )
    ]
    manifest = {
        "schema_version": P6_DATASET_SCHEMA_VERSION,
        "canonical_schema_version": 1,
        "dataset_kind": "beliefkv_p6_training_evidence",
        "characterization_only": True,
        "formal_training_eligible": len(clean_workflows) == len(all_workflows),
        "formal_local_training_eligible": True,
        "clean_trajectory_eligible": len(clean_workflows) == len(all_workflows),
        "clean_episode_eligible": len(clean_workflows) == len(all_workflows),
        "workflow_jct_eligible": False,
        "terminal_outcome_eligible": False,
        "formal_ineligibility_reasons": (
            []
            if len(clean_workflows) == len(all_workflows)
            else ["not_all_workflows_clean_trajectory"]
        ),
        "formal_local_ineligibility_reasons": [],
        "evaluation_role": "frozen_split_local_training_evidence",
        "source": {
            "run_id": canonical_run_id,
            "dataset": "princeton-nlp/SWE-bench_Verified",
            "selection_manifest": str(selection_path),
            "selection_manifest_sha256": selection_sha,
            "workload_manifest_sha256": workload_hashes,
            "collection_contract": collection_contract,
            "runtime_environment_contract": environment_contract,
            "physical_source_run_count": len(physical_run_ids),
            "sources": source_entries,
            "canonical_instance_ids": sorted(expected),
            "canonical_instance_count": len(expected),
            "clean_trajectory_workflow_count": len(clean_workflows),
            "workflow_count": len(all_workflows),
        },
        "identity_contract": {
            "semantic_unit": "one selected trajectory per pre-frozen instance",
            "physical_unit": "one telemetry copy per physical SGLang run",
            "replacement_duplicates_allowed": False,
        },
        "split_contract": {
            "unit": "dataset plus repository",
            "source": "explicit frozen split manifest",
            "development_only": False,
            "counts_on_request_calls": dict(sorted(split_counts.items())),
            "manifest_digest": next(iter(split_digests)),
        },
        "label_contract": {
            "formal_local_training": "row and invocation-target eligibility",
            "clean_trajectory": "native_agent_jct_eligible only",
            "runtime_intervention": (
                "target horizons crossing cutoff are censored independently"
            ),
            "right_censored_tool_wait": (
                "observed duration ends at intervention cutoff"
            ),
        },
        "training_readiness": {
            "remaining_decode_demand_eligible_request_count": sum(
                bool(row.get("training_eligible_remaining_decode_demand"))
                for row in tables["request_calls"]
            ),
            "external_survival_eligible_count": sum(
                bool(row.get("training_eligible_survival"))
                for row in tables["external_waits"]
            ),
            "join_reentry_eligible_count": sum(
                row.get("reentry_kind") == "join"
                and bool(row.get("training_eligible"))
                for row in tables["reentries"]
            ),
            "frontier_decision_eligible_count": sum(
                bool(row.get("training_eligible"))
                for row in tables["frontier_decision_points"]
            ),
        },
        "integrity": integrity,
        "tables": table_manifest,
    }
    _write_json_atomic(destination / "dataset_manifest.json", manifest)
    return manifest


def export_canonical_p6_training_dataset(
    selection_manifest_path: str | Path,
    output_dir: str | Path,
    *,
    repository_root: str | Path,
) -> dict[str, Any]:
    """Export and merge the one-trajectory-per-instance canonical train set."""

    root = Path(repository_root).resolve()
    selection_path = _resolve(root, selection_manifest_path)
    selection = _read_object(selection_path)
    if (
        selection.get("selection_status") != "complete"
        or selection.get("plan_id") != "h200-bf16-formal-train-v1"
    ):
        raise P6CoverageError("canonical selection manifest is not frozen/complete")
    specs = build_canonical_source_specs(selection, repository_root=root)
    plan_path = _resolve(root, str((selection.get("source_plan") or {}).get("path")))
    plan = _read_object(plan_path)
    split_path = _resolve(root, str(plan.get("split_manifest") or ""))
    _verify_sha256(
        split_path,
        str(plan.get("split_manifest_sha256") or "") or None,
        label="frozen split manifest",
    )
    expected = tuple(
        sorted({instance for spec in specs for instance in spec.instance_ids})
    )
    destination = Path(output_dir).resolve()
    if destination.exists():
        raise P6CoverageError(f"canonical output already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    from beliefkv.experiments.p6_dataset import export_p6_training_dataset

    with tempfile.TemporaryDirectory(
        prefix=f".{destination.name}.staging-", dir=destination.parent
    ) as temporary:
        temporary_root = Path(temporary)
        sources: list[tuple[CanonicalSourceSpec, Path]] = []
        for spec in specs:
            source_output = temporary_root / "sources" / spec.source_id
            export_p6_training_dataset(
                spec.run_dir,
                source_output,
                split_manifest=split_path,
                workload_dirs=spec.workload_dirs,
                selected_instance_ids=spec.instance_ids,
                allow_formal_local_training=True,
                formal_local_expected_split="train",
            )
            sources.append((spec, source_output))
        canonical_output = temporary_root / "canonical"
        manifest = merge_p6_source_datasets(
            sources,
            canonical_output,
            selection_manifest_path=selection_path,
            expected_instance_ids=expected,
        )
        os.replace(canonical_output, destination)
    return manifest


_TARGET_STATES = {
    "action_boundary": {"running_llm"},
    "remaining_decode_demand": {"running_llm"},
    "prompt_growth": {
        "created", "ready", "wait_tool", "wait_child", "wait_join", "wait_message"
    },
    "next_output_demand": {
        "created", "ready", "wait_tool", "wait_child", "wait_join", "wait_message"
    },
    "external_wait": {"wait_tool"},
    "join_wait": {"wait_join"},
}


def _coverage_bucket() -> Counter[str]:
    return Counter(
        applicable=0,
        eligible=0,
        right_censored=0,
        censored=0,
        unavailable=0,
    )


def _boundary_class(value: Any) -> str:
    text = str(value or "unavailable").lower()
    if "spawn" in text or "subagent" in text or "agent" == text:
        return "SPAWN"
    if "return" in text or text == "final":
        return "RETURN"
    if "join" in text or "reactivate" in text:
        return "JOIN"
    if "tool" in text or "function" in text:
        return "TOOL"
    if "handoff" in text:
        return "HANDOFF"
    if "message" in text:
        return "MESSAGE"
    return text.upper()


def characterize_canonical_p6_dataset(
    dataset_dir: str | Path,
) -> dict[str, Any]:
    root = Path(dataset_dir).resolve()
    manifest = _read_object(root / "dataset_manifest.json")
    decisions = _read_jsonl(root / "frontier_decision_points.jsonl")
    requests = _read_jsonl(root / "request_calls.jsonl")
    external = _read_jsonl(root / "external_waits.jsonl")
    reentries = _read_jsonl(root / "reentries.jsonl")

    target_totals: defaultdict[str, Counter[str]] = defaultdict(_coverage_bucket)
    fanout_targets: defaultdict[str, Counter[str]] = defaultdict(_coverage_bucket)
    project_targets: defaultdict[str, Counter[str]] = defaultdict(_coverage_bucket)
    boundary_classes: Counter[str] = Counter()
    clean_rows = 0
    intervention_affected_rows = 0
    local_pre_intervention_rows = 0
    fully_censored_intervention_rows = 0
    decision_workflows: set[str] = set()
    projects: set[str] = set()
    fanouts: set[str] = set()
    for row in decisions:
        workflow_id = str(row.get("workflow_id") or "")
        project = str(row.get("project") or "unknown")
        fanout = str(row.get("fanout_profile") or "unknown")
        if workflow_id:
            decision_workflows.add(workflow_id)
        projects.add(project)
        fanouts.add(fanout)
        if row.get("clean_episode_eligible") is False:
            intervention_affected_rows += 1
            if row.get("training_eligible") is False:
                fully_censored_intervention_rows += 1
            else:
                local_pre_intervention_rows += 1
        else:
            clean_rows += 1
        features = {
            str(item.get("invocation_id") or ""): item
            for item in row.get("invocations", ())
        }
        for label in row.get("labels", ()):
            invocation_id = str(label.get("invocation_id") or "")
            state = str((features.get(invocation_id) or {}).get("state") or "unknown")
            eligibility = label.get("target_training_eligible") or {}
            right_censored = label.get("target_right_censored") or {}
            reasons = label.get("target_censor_reasons") or {}
            for target, allowed_states in _TARGET_STATES.items():
                if state not in allowed_states:
                    continue
                counters = (
                    target_totals[target],
                    fanout_targets[f"{fanout}|{target}"],
                    project_targets[f"{project}|{target}"],
                )
                for counter in counters:
                    counter["applicable"] += 1
                if eligibility.get(target):
                    category = (
                        "right_censored"
                        if right_censored.get(target)
                        else "eligible"
                    )
                elif reasons.get(target):
                    category = "censored"
                else:
                    category = "unavailable"
                for counter in counters:
                    counter[category] += 1
            if eligibility.get("action_boundary"):
                boundary_classes[_boundary_class(label.get("next_boundary_kind"))] += 1

    action_requests = [
        row
        for row in requests
        if row.get("action_kinds") and not row.get("runtime_internal", False)
    ]
    exact_action_requests = [
        row
        for row in action_requests
        if row.get("action_boundary_token_index") is not None
        and row.get("action_boundary_source") not in {None, "", "unavailable"}
    ]
    eligible_joins = [
        row
        for row in reentries
        if row.get("reentry_kind") == "join" and row.get("training_eligible")
    ]
    closure_complete_joins = [
        row
        for row in eligible_joins
        if all(
            member.get("return_ts_ms") is not None
            for member in row.get("member_outcomes", ())
        )
    ]
    eligible_tools = [
        row for row in external if row.get("training_eligible_survival")
    ]
    right_censored_tools = [
        row for row in eligible_tools if row.get("survival_censored")
    ]

    blockers: list[str] = []
    warnings: list[str] = []
    instance_count = int(
        (manifest.get("source") or {}).get("canonical_instance_count") or 0
    )
    if instance_count != 64:
        blockers.append("canonical_instance_count_not_64")
    if len(projects) < 5:
        blockers.append("fewer_than_5_projects")
    if len(decision_workflows) < 40:
        blockers.append("fewer_than_40_workflows_with_decisions")
    required_fanouts = {"natural", "parallel_analysis_2to3"}
    if not required_fanouts.issubset(fanouts):
        blockers.append("missing_required_fanout_profile")
    for target in _TARGET_STATES:
        if target_totals[target]["eligible"] + target_totals[target]["right_censored"] == 0:
            blockers.append(f"no_eligible_{target}")
    if not eligible_tools:
        blockers.append("no_tool_survival_evidence")
    if not eligible_joins:
        blockers.append("no_join_reentry_evidence")
    if len(eligible_joins) != len(closure_complete_joins):
        blockers.append("eligible_join_not_closure_complete")
    if not exact_action_requests:
        warnings.append("exact_incremental_action_boundary_unavailable")
    for profile in required_fanouts:
        for target in ("action_boundary", "external_wait"):
            bucket = fanout_targets[f"{profile}|{target}"]
            if bucket["eligible"] + bucket["right_censored"] == 0:
                warnings.append(f"profile_target_gap:{profile}:{target}")
    for event_class in ("TOOL", "SPAWN"):
        if boundary_classes[event_class] == 0:
            warnings.append(f"action_boundary_class_missing:{event_class}")

    runtime_return_ids = {
        str(member.get("invocation_id"))
        for row in reentries
        if row.get("reentry_kind") == "join"
        for member in row.get("member_outcomes", ())
        if member.get("return_ts_ms") is not None
    }
    runtime_transition_counts = {
        "RETURN": len(runtime_return_ids),
        "JOIN": sum(row.get("reentry_kind") == "join" for row in reentries),
    }
    for event_class, count in runtime_transition_counts.items():
        if count == 0:
            warnings.append(f"runtime_transition_missing:{event_class}")

    return {
        "schema_version": 1,
        "dataset_dir": str(root),
        "dataset_manifest_sha256": _sha256(root / "dataset_manifest.json"),
        "coverage_gate_passed": not blockers,
        "training_blockers": blockers,
        "coverage_warnings": warnings,
        "sampling_units": {
            "canonical_instances": instance_count,
            "decision_workflows": len(decision_workflows),
            "projects": len(projects),
            "fanout_profiles": sorted(fanouts),
            "clean_decision_rows": clean_rows,
            "intervention_affected_decision_rows": intervention_affected_rows,
            "local_pre_intervention_eligible_rows": local_pre_intervention_rows,
            "fully_censored_intervention_rows": fully_censored_intervention_rows,
        },
        "target_coverage": {
            key: dict(value) for key, value in sorted(target_totals.items())
        },
        "fanout_profile_by_target": {
            key: dict(value) for key, value in sorted(fanout_targets.items())
        },
        "project_by_target": {
            key: dict(value) for key, value in sorted(project_targets.items())
        },
        "action_boundaries": {
            "class_counts": dict(sorted(boundary_classes.items())),
            "action_request_count": len(action_requests),
            "exact_incremental_count": len(exact_action_requests),
            "exact_incremental_coverage": (
                len(exact_action_requests) / len(action_requests)
                if action_requests
                else 0.0
            ),
            "claim_gate": "required_only_for_early_dispatch_or_run_to_action",
        },
        "runtime_transitions": runtime_transition_counts,
        "tool_survival": {
            "eligible_count": len(eligible_tools),
            "right_censored_count": len(right_censored_tools),
        },
        "reentry": {
            "eligible_join_count": len(eligible_joins),
            "closure_complete_join_count": len(closure_complete_joins),
            "closure_complete_ratio": (
                len(closure_complete_joins) / len(eligible_joins)
                if eligible_joins
                else 0.0
            ),
        },
    }
