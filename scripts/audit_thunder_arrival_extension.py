#!/usr/bin/env python3
"""Compare completed ThunderAgent runs before and after a background extension."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import gc
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_cohort_jct_paths import audit_arm
from scripts.run_causal_gpu_replay import source_plan


def read(path):
    return json.loads(Path(path).read_bytes())


def differences(left, right, prefix=""):
    if left == right:
        return []
    if isinstance(left, dict) and isinstance(right, dict):
        return [
            row
            for key in sorted(left.keys() | right.keys())
            for row in differences(
                left.get(key), right.get(key), f"{prefix}.{key}" if prefix else key,
            )
        ]
    return [{"field": prefix, "old": left, "new": right}]


def source_checks(old, new):
    paths = [Path(reg["source_arm"]) for reg in (old, new)]
    plans = [read(path / "plan.json") for path in paths]
    members = set(old["workflow_ids"])
    nodes = [
        {
            row["node_id"]: row for row in plan["nodes"]
            if row["workflow_id"] in members
        }
        for plan in plans
    ]
    usage = [
        {row["request_id"]: plan["request_usage"][row["request_id"]]
         for row in selected.values() if row.get("request_id")}
        for plan, selected in zip(plans, nodes)
    ]
    result = {
        "old_workflows": len(members),
        "original_nodes": len(nodes[0]),
        "original_requests": len(usage[0]),
        "original_nodes_identical": nodes[0] == nodes[1],
        "original_request_usage_identical": usage[0] == usage[1],
    }
    start_differences = []
    effective_nodes = [dict(indexed) for indexed in nodes]
    for key, before in nodes[0].items():
        after = nodes[1].get(key)
        if before == after:
            continue
        if (
            after is None or before.get("dependencies") or after.get("dependencies")
            or (before.get("event") or {}).get("kind") != "workflow_start"
            or (after.get("event") or {}).get("kind") != "workflow_start"
        ):
            raise ValueError("source changed a node other than an external arrival")
        left, right = dict(before), dict(after)
        start_differences.append({
            "node_id": key, "old_delay_ms": left.pop("delay_ms"),
            "new_delay_ms": right.pop("delay_ms"),
        })
        effective_nodes[0][key], effective_nodes[1][key] = left, right
    result.update({
        "original_start_delay_differences": start_differences,
        "effective_nodes_identical_with_frozen_arrival_override":
            effective_nodes[0] == effective_nodes[1],
        "initial_raw_node_equality_check_failed": not result["original_nodes_identical"],
        "start_delay_override_basis": (
            "Both frozen causal_replay.py versions set workflow_start deadlines "
            "to origin + workflow_arrival_offsets_ms, replacing raw node.delay_ms."
        ),
    })
    overlays = [read(reg["tool_timing_overlay"]["path"]) for reg in (old, new)]
    entries = [
        {tuple(row["tool_identity"]): row for row in overlay["entries"]
         if row["tool_identity"][0] in members}
        for overlay in overlays
    ]
    result.update({
        "original_overlay_entries": len(entries[0]),
        "original_overlay_entries_identical": entries[0] == entries[1],
    })
    del plans, nodes, effective_nodes, usage, overlays, entries
    gc.collect()

    # Bind the source token rows, including encoded token material, by request.
    token_indices = []
    for path in paths:
        indexed = {}
        with (path / "server/request_tokens.jsonl").open("rb") as stream:
            for line in stream:
                row = json.loads(line)
                if row.get("workflow_id") not in members:
                    continue
                key = (row["event"], row["request_id"])
                if key in indexed:
                    raise ValueError("duplicate source token row identity")
                row.pop("ts_ms", None)
                canonical = json.dumps(
                    row, sort_keys=True, separators=(",", ":"), allow_nan=False,
                ).encode()
                indexed[key] = hashlib.sha256(canonical).hexdigest()
        token_indices.append(indexed)
    result.update({
        "original_token_rows": len(token_indices[0]),
        "original_token_rows_identical_ignoring_source_observation_clock":
            token_indices[0] == token_indices[1],
        "token_row_events": dict(Counter(key[0] for key in token_indices[0])),
    })
    if not all(result[key] for key in (
        "effective_nodes_identical_with_frozen_arrival_override",
        "original_request_usage_identical",
        "original_overlay_entries_identical",
        "original_token_rows_identical_ignoring_source_observation_clock",
    )):
        raise ValueError(f"background extension changed original work: {result}")
    return result


def path_comparison(old, new):
    if old["workflows"].keys() != new["workflows"].keys():
        raise ValueError("path audit changed measured workflows")
    if old["requests"].keys() != new["requests"].keys():
        raise ValueError("path audit changed measured requests")
    for request, before in old["requests"].items():
        if any(before[key] != new["requests"][request][key] for key in (
            "workflow_id", "invocation_id", "context_id", "context_epoch",
            "prompt_tokens", "output_tokens",
        )):
            raise ValueError("path audit changed measured identity or work")
    rows = []
    for workflow, before in old["workflows"].items():
        after = new["workflows"][workflow]
        components = {
            key: (after["components_ms"].get(key, 0.)
                  - before["components_ms"].get(key, 0.)) / 1000.
            for key in sorted(before["components_ms"].keys() | after["components_ms"].keys())
        }
        delta = (after["jct_ms"] - before["jct_ms"]) / 1000.
        if not math.isclose(sum(components.values()), delta, abs_tol=1e-7):
            raise ValueError("observed path components do not reconcile JCT")
        rows.append({
            "workflow_id": workflow, "new_minus_old_jct_s": delta,
            "new_minus_old_components_s": components,
            "new_minus_old_first_native_launch_s": (
                after["workflow_to_first_native_launch_s"]
                - before["workflow_to_first_native_launch_s"]
            ),
            "observed_path_membership_changed": (
                set(before["critical_path_requests"])
                != set(after["critical_path_requests"])
            ),
        })
    return {
        "new_minus_old_mean_jct_s": statistics.mean(row["new_minus_old_jct_s"] for row in rows),
        "new_minus_old_mean_components_s": {
            key: statistics.mean(row["new_minus_old_components_s"].get(key, 0.) for row in rows)
            for key in sorted({
                key for row in rows for key in row["new_minus_old_components_s"]
            })
        },
        "workflows": rows,
        "scope": (
            "Observed last-completing paths; submit-to-launch includes local waiting, "
            "launch-to-complete includes interleaving. No causal attribution or "
            "counterfactual JCT. Original cohort metadata is retained in each arm."
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-registration", type=Path, required=True)
    parser.add_argument("--new-registration", type=Path, required=True)
    parser.add_argument("--old-cohort-report", type=Path, required=True)
    parser.add_argument("--new-cohort-report", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise ValueError("use a new evidence directory")
    regs = [read(path) for path in (args.old_registration, args.new_registration)]
    reports = [read(path) for path in (args.old_cohort_report, args.new_cohort_report)]
    old, new = regs
    arrivals = [reg["workflow_arrivals"]["arrival_schedule"] for reg in regs]
    if len(arrivals[1]) <= len(arrivals[0]) or arrivals[1][:len(arrivals[0])] != arrivals[0]:
        raise ValueError("require an identical original arrival prefix and added background")
    measured = reports[0]["cohort"]["measured_workflow_ids"]
    if measured != reports[1]["cohort"]["measured_workflow_ids"]:
        raise ValueError("measured arrival-selected membership differs")
    if (
        reports[0]["cohort"]["throughput_window_offset_seconds"]
        != reports[1]["cohort"]["throughput_window_offset_seconds"]
    ):
        raise ValueError("common throughput windows differ")
    source = source_checks(old, new)
    first_extra = arrivals[1][len(arrivals[0])]["offset_seconds"]
    offsets = {row["workflow_id"]: row["offset_seconds"] for row in arrivals[0]}
    rows = []
    for workflow in measured:
        jcts = [report["measured_workflow_JCT_ms"][workflow] / 1000. for report in reports]
        rows.append({
            "workflow_id": workflow, "old_jct_s": jcts[0], "new_jct_s": jcts[1],
            "old_minus_new_s": jcts[0] - jcts[1],
            "old_completion_offset_s": offsets[workflow] + jcts[0],
            "new_completion_offset_s": offsets[workflow] + jcts[1],
            "both_complete_before_first_extra": offsets[workflow] + max(jcts) < first_extra,
        })
    configs = [read(reg["experiment_configuration"]) for reg in regs]
    arms = [Path(reg["run_root"]) / reg["order"][0] for reg in regs]
    observed = [
        {name: read(arm / f"server/{name}.json") for name in (
            "server_info", "launch_configuration", "native_capacity_census",
        )}
        for arm in arms
    ]
    numa = [
        read(next((arm / "server").glob("host_numa_diagnostic_*.json"))) for arm in arms
    ]
    mean_delta = statistics.mean(row["old_minus_new_s"] for row in rows)
    pre_extra_mean_delta = sum(
        row["old_minus_new_s"] for row in rows if row["both_complete_before_first_extra"]
    ) / len(measured)
    output = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "old_registration": str(args.old_registration.resolve()),
        "new_registration": str(args.new_registration.resolve()),
        "source_work": source,
        "original_arrivals_identical": True,
        "first_extra_arrival_offset_s": first_extra,
        "measured_members_identical": True,
        "common_window_identical": True,
        "expected_capacity_identical": old["expected_replay_capacity"] == new["expected_replay_capacity"],
        "native_engine_tree_identical": old["engine_tree"] == new["engine_tree"],
        "native_execution_configuration_identical":
            old["native_execution_configuration"] == new["native_execution_configuration"],
        "runtime_preflight_identical": old["runtime_cpu_preflight"] == new["runtime_cpu_preflight"],
        "environment_differences": differences(
            configs[0]["common_environment"], configs[1]["common_environment"],
        ),
        "actual_server_argument_differences": differences(
            observed[0]["server_info"], observed[1]["server_info"],
        ),
        "actual_launch_differences": differences(
            observed[0]["launch_configuration"], observed[1]["launch_configuration"],
        ),
        "serving_commits": [reg["code_commit"] for reg in regs],
        "paper_policy_sources_identical": {
            name: old["source_sha256"][name] == new["source_sha256"][name] for name in (
                "beliefkv/runtime/paper_baseline.py", "beliefkv/policy/paper_baselines.py",
                "beliefkv/runtime/sglang_v0520_admission.py",
            )
        },
        "NUMA": [{
            key: diagnostic[key] for key in (
                "configuration", "reported_remote_fraction", "reported_resident_bytes_by_node", "scope",
            )
        } for diagnostic in numa],
        "old_mean_JCT_s": reports[0]["measured_complete_workflow_JCT_ms"]["mean"] / 1000.,
        "new_mean_JCT_s": reports[1]["measured_complete_workflow_JCT_ms"]["mean"] / 1000.,
        "old_minus_new_mean_JCT_s": mean_delta,
        "new_mean_JCT_reduction_percent": 100. * mean_delta / (
            reports[0]["measured_complete_workflow_JCT_ms"]["mean"] / 1000.
        ),
        "improved_workflows": sum(row["old_minus_new_s"] > 0. for row in rows),
        "both_complete_before_extra_workflows":
            sum(row["both_complete_before_first_extra"] for row in rows),
        "pre_extra_workflows_contribution_to_mean_delta_s": pre_extra_mean_delta,
        "pre_extra_workflows_fraction_of_total_delta": pre_extra_mean_delta / mean_delta,
        "common_window_output_tokens_per_second": [
            report["common_window_output_tokens_per_second"] for report in reports
        ],
        "background_submission_coverage": [
            report["background_submission_coverage"] for report in reports
        ],
        "workflows": sorted(rows, key=lambda row: -row["old_minus_new_s"]),
        "historical_complete_JCT_is_not_identical_83_workload_control": True,
        "GPU_action": False,
    }
    plan, _, evidence = source_plan(Path(new["source_arm"]))
    path_results = [
        audit_arm(plan, arm, report_path)
        for arm, report_path in zip(arms, (args.old_cohort_report, args.new_cohort_report))
    ]
    output["observed_paths"] = path_comparison(*path_results)
    paths = [
        Path(__file__), ROOT / "scripts/audit_cohort_jct_paths.py",
        ROOT / "scripts/audit_causal_jct_paths.py",
        args.old_registration, args.new_registration,
        args.old_cohort_report, args.new_cohort_report,
        *[Path(reg["source_arm"]) / "plan.json" for reg in regs],
        *[Path(reg["tool_timing_overlay"]["path"]) for reg in regs],
        *[Path(reg["experiment_configuration"]) for reg in regs],
    ]
    output["inputs_sha256"] = {str(path.resolve()): sha256_file(path) for path in paths}
    output["source_evidence"] = evidence
    args.output_dir.mkdir(parents=True, exist_ok=False)
    for name, result in zip(("old73_paths", "new83_paths", "comparison"), (*path_results, output)):
        with (args.output_dir / f"{name}.json").open("x") as stream:
            json.dump(result, stream, indent=2, allow_nan=False)
    print(json.dumps({
        "old_mean_JCT_s": output["old_mean_JCT_s"],
        "new_mean_JCT_s": output["new_mean_JCT_s"],
        "pre_extra_workflows_fraction_of_total_delta":
            output["pre_extra_workflows_fraction_of_total_delta"],
        "observed_path_mean_components": output["observed_paths"]["new_minus_old_mean_components_s"],
    }))


if __name__ == "__main__":
    main()
