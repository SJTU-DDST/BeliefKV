#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from beliefkv.experiments.p6_canonical import (  # noqa: E402
    _TARGET_STATES,
    _boundary_class,
    _coverage_bucket,
)
from beliefkv.experiments.p6_dataset import _sha256, _write_json_atomic  # noqa: E402
from beliefkv.predictor.structured_frontier import (  # noqa: E402
    runtime_environment_digest,
)


REQUIRED_FANOUTS = {"natural", "parallel_analysis_2to3"}
KEY_TARGETS = {"action_boundary", "remaining_decode_demand", "external_wait"}


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def _supported(counter: Mapping[str, int]) -> int:
    return int(counter.get("eligible", 0)) + int(
        counter.get("right_censored", 0)
    )


def audit_calibration_coverage(
    dataset_dirs: Iterable[str | Path],
    source_coverages: Iterable[str | Path],
    *,
    expected_instances: int = 16,
) -> dict[str, Any]:
    roots = tuple(Path(item).resolve() for item in dataset_dirs)
    coverage_paths = tuple(Path(item).resolve() for item in source_coverages)
    if not roots or len(roots) != len(coverage_paths):
        raise ValueError(
            "dataset-dir and source-coverage must be non-empty and one-to-one"
        )

    manifests = []
    environment_digests: set[str] = set()
    run_ids: set[str] = set()
    manifest_digests: list[str] = []
    harness_revisions: set[str] = set()
    for root in roots:
        manifest_path = root / "dataset_manifest.json"
        manifest = _read_object(manifest_path)
        source = manifest.get("source") or {}
        contract = source.get("collection_contract") or {}
        split_counts = (manifest.get("split_contract") or {}).get(
            "counts_on_request_calls"
        ) or {}
        if (
            manifest.get("formal_local_training_eligible") is not True
            or manifest.get("evaluation_role")
            != "frozen_split_local_training_evidence"
            or contract.get("plan_id")
            != "h200-bf16-formal-calibration-v1"
            or contract.get("split") != "calibration"
            or contract.get("runtime_source_stable") is not True
            or contract.get("runtime_policy") != "frozen_p5_observed"
            or bool(contract.get("predictor_enabled"))
            or bool(contract.get("predictive_actions_enabled"))
            or set(split_counts) != {"calibration"}
        ):
            raise ValueError(f"invalid frozen calibration contract: {root}")
        run_id = str(source.get("run_id") or "")
        if not run_id or run_id in run_ids:
            raise ValueError(f"duplicate or missing calibration run_id: {root}")
        run_ids.add(run_id)
        environment = source.get("runtime_environment_contract") or {}
        environment_digests.add(runtime_environment_digest(environment))
        provenance = source.get("runtime_provenance") or {}
        harness_revision = str(provenance.get("harness_revision") or "")
        if not harness_revision:
            raise ValueError(f"missing harness revision: {root}")
        harness_revisions.add(harness_revision)
        manifest_digests.append(_sha256(manifest_path))
        manifests.append(manifest)
    if len(environment_digests) != 1:
        raise ValueError("calibration shards disagree on runtime environment")

    source_reentry = []
    for path in coverage_paths:
        coverage = _read_object(path)
        action = coverage.get("action_frontier") or {}
        source_reentry.append(
            {
                "path": str(path),
                "sha256": _sha256(path),
                "eligible": int(action.get("reentry_eligible_call_count") or 0),
                "observed": int(action.get("reentry_observed_count") or 0),
                "right_censored": int(
                    action.get("reentry_censored_count") or 0
                ),
                "coverage": float(action.get("reentry_cause_coverage") or 0.0),
                "missing_by_action": action.get(
                    "reentry_missing_by_action_kind"
                )
                or {},
            }
        )

    target_totals: defaultdict[str, Counter[str]] = defaultdict(
        _coverage_bucket
    )
    fanout_targets: defaultdict[str, Counter[str]] = defaultdict(
        _coverage_bucket
    )
    project_targets: defaultdict[str, Counter[str]] = defaultdict(
        _coverage_bucket
    )
    boundary_classes: Counter[str] = Counter()
    decision_ids: set[str] = set()
    workflows: set[str] = set()
    instances: set[str] = set()
    projects: set[str] = set()
    fanouts: set[str] = set()
    clean_rows = 0
    local_rows = 0
    fully_censored_rows = 0
    right_censored_targets = 0
    for root in roots:
        for row in _iter_jsonl(root / "frontier_decision_points.jsonl"):
            decision_id = str(row.get("decision_id") or "")
            if not decision_id or decision_id in decision_ids:
                raise ValueError(f"duplicate or missing decision_id: {decision_id}")
            decision_ids.add(decision_id)
            workflow = str(row.get("workflow_id") or "")
            instance = str(row.get("instance_id") or "")
            project = str(row.get("project") or "unknown")
            fanout = str(row.get("fanout_profile") or "unknown")
            if workflow:
                workflows.add(workflow)
            if instance:
                instances.add(instance)
            projects.add(project)
            fanouts.add(fanout)
            if row.get("clean_episode_eligible") is False:
                if row.get("training_eligible") is False:
                    fully_censored_rows += 1
                else:
                    local_rows += 1
            else:
                clean_rows += 1
            features = {
                str(item.get("invocation_id") or ""): item
                for item in row.get("invocations", ())
            }
            for label in row.get("labels", ()):
                invocation_id = str(label.get("invocation_id") or "")
                state = str(
                    (features.get(invocation_id) or {}).get("state") or "unknown"
                )
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
                    if category == "right_censored":
                        right_censored_targets += 1
                    for counter in counters:
                        counter[category] += 1
                if eligibility.get("action_boundary"):
                    boundary_classes[
                        _boundary_class(label.get("next_boundary_kind"))
                    ] += 1

    request_ids: set[tuple[str, str]] = set()
    action_requests = 0
    exact_actions = 0
    for root, manifest in zip(roots, manifests):
        run_id = str((manifest.get("source") or {}).get("run_id"))
        for row in _iter_jsonl(root / "request_calls.jsonl"):
            identity = (run_id, str(row.get("request_id") or ""))
            if not identity[1] or identity in request_ids:
                raise ValueError(f"duplicate or missing request identity: {identity}")
            request_ids.add(identity)
            if row.get("action_kinds") and not row.get("runtime_internal", False):
                action_requests += 1
                if (
                    row.get("action_boundary_token_index") is not None
                    and row.get("action_boundary_source")
                    not in {None, "", "unavailable"}
                ):
                    exact_actions += 1

    eligible_tools = 0
    right_censored_tools = 0
    eligible_joins = 0
    complete_joins = 0
    return_ids: set[str] = set()
    explicit_censors = 0
    for root in roots:
        for row in _iter_jsonl(root / "external_waits.jsonl"):
            if row.get("training_eligible_survival"):
                eligible_tools += 1
                right_censored_tools += bool(row.get("survival_censored"))
        for row in _iter_jsonl(root / "reentries.jsonl"):
            if row.get("reentry_kind") != "join":
                continue
            if row.get("training_eligible"):
                eligible_joins += 1
                complete = all(
                    item.get("return_ts_ms") is not None
                    for item in row.get("member_outcomes", ())
                )
                complete_joins += complete
            return_ids.update(
                str(item.get("invocation_id"))
                for item in row.get("member_outcomes", ())
                if item.get("return_ts_ms") is not None
            )
        explicit_censors += sum(
            1 for _ in _iter_jsonl(root / "censor_events.jsonl")
        )

    blockers: list[str] = []
    warnings: list[str] = []
    if len(instances) != expected_instances:
        blockers.append(
            f"instance_count:{len(instances)}!=expected:{expected_instances}"
        )
    if len(workflows) != expected_instances:
        blockers.append(
            f"workflow_count:{len(workflows)}!=expected:{expected_instances}"
        )
    if not REQUIRED_FANOUTS.issubset(fanouts):
        blockers.append("missing_required_fanout_profile")
    if len(projects) < 2 or "unknown" in projects:
        blockers.append("insufficient_identified_projects")
    if any(item["coverage"] != 1.0 for item in source_reentry):
        blockers.append("reentry_cause_not_fully_attributed")
    for target in _TARGET_STATES:
        if _supported(target_totals[target]) == 0:
            blockers.append(f"no_supported_target:{target}")
    for fanout in REQUIRED_FANOUTS:
        for target in KEY_TARGETS:
            if _supported(fanout_targets[f"{fanout}|{target}"]) == 0:
                blockers.append(f"fanout_target_gap:{fanout}:{target}")
    for project in projects:
        for target in KEY_TARGETS:
            if _supported(project_targets[f"{project}|{target}"]) == 0:
                blockers.append(f"project_target_gap:{project}:{target}")
    if not eligible_tools:
        blockers.append("no_tool_survival_evidence")
    if not eligible_joins or eligible_joins != complete_joins:
        blockers.append("join_reentry_not_closure_complete")
    for fanout in REQUIRED_FANOUTS:
        for target in _TARGET_STATES:
            if _supported(fanout_targets[f"{fanout}|{target}"]) == 0:
                warnings.append(f"fanout_optional_target_gap:{fanout}:{target}")
    for project in projects:
        for target in _TARGET_STATES:
            if _supported(project_targets[f"{project}|{target}"]) == 0:
                warnings.append(f"project_optional_target_gap:{project}:{target}")
    if not exact_actions:
        warnings.append("exact_incremental_action_boundary_unavailable")
    for event_class in ("TOOL", "SPAWN"):
        if boundary_classes[event_class] == 0:
            warnings.append(f"action_boundary_class_missing:{event_class}")

    return {
        "schema_version": 1,
        "split": "calibration",
        "coverage_gate_passed": not blockers,
        "calibration_blockers": blockers,
        "coverage_warnings": warnings,
        "source": {
            "dataset_dirs": [str(root) for root in roots],
            "dataset_manifest_sha256s": manifest_digests,
            "source_coverages": source_reentry,
            "runtime_environment_digest": next(iter(environment_digests)),
            "run_count": len(run_ids),
            "harness_revisions": sorted(harness_revisions),
        },
        "sampling_units": {
            "instances": len(instances),
            "workflows": len(workflows),
            "projects": sorted(projects),
            "fanout_profiles": sorted(fanouts),
            "decision_rows": len(decision_ids),
            "clean_decision_rows": clean_rows,
            "local_pre_intervention_eligible_rows": local_rows,
            "fully_censored_intervention_rows": fully_censored_rows,
            "right_censored_target_count": right_censored_targets,
            "explicit_censor_event_count": explicit_censors,
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
            "action_request_count": action_requests,
            "exact_incremental_count": exact_actions,
            "exact_incremental_coverage": (
                exact_actions / action_requests if action_requests else 0.0
            ),
            "claim_gate": "required_only_for_early_dispatch_or_run_to_action",
        },
        "tool_survival": {
            "eligible_count": eligible_tools,
            "right_censored_count": right_censored_tools,
        },
        "reentry": {
            "eligible_join_count": eligible_joins,
            "closure_complete_join_count": complete_joins,
            "return_count": len(return_ids),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit target-level coverage across frozen P6 calibration shards."
    )
    parser.add_argument("--dataset-dir", type=Path, action="append", required=True)
    parser.add_argument(
        "--source-coverage", type=Path, action="append", required=True
    )
    parser.add_argument("--expected-instances", type=int, default=16)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit_calibration_coverage(
        args.dataset_dir,
        args.source_coverage,
        expected_instances=args.expected_instances,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(args.output, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["coverage_gate_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
