#!/usr/bin/env python3
"""Read complete historical evidence; distinguish service time from control cost."""

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "control_regression_audit.json"
SOURCES = {}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load(path, expected=None):
    path = Path(path).resolve()
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    require(expected is None or digest == expected, f"changed source: {path}")
    SOURCES[str(path)] = digest
    return json.loads(raw)


def sealed_rows(path, seal, markers):
    path = Path(path).resolve()
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        for line in stream:
            require(line.endswith(b"\n"), f"partial historical record: {path}")
            digest.update(line)
            size += len(line)
            if any(marker in line for marker in markers):
                yield json.loads(line)
    require(size == seal["complete_prefix_bytes"], f"changed file size: {path}")
    require(digest.hexdigest() == seal["complete_prefix_sha256"], f"changed seal: {path}")
    SOURCES[str(path)] = digest.hexdigest()


def stats(values):
    values = list(values)
    return {
        "count": len(values),
        "sum": math.fsum(values),
        "mean": math.fsum(values) / len(values) if values else None,
    }


def critical_partition(service, paths, cohort=None):
    workflows = paths["workflows"]
    require(len(workflows) == 18, "changed workflow population")
    rows = []
    for workflow, path in workflows.items():
        for request_id in path["critical_path_requests"]:
            row = service["requests"][request_id]
            require(row["workflow_id"] == workflow, "changed critical request owner")
            if cohort is None or row["cohort"] == cohort:
                rows.append(row)
    fields = (
        "submit_to_first_launch", "first_launch_to_complete",
        "prefill_member_interval_union", "decode_member_interval_union",
        "outside_member_worker_intervals",
    )
    means = {
        field + "_s": math.fsum(row[field + "_ms"] for row in rows) / 18000.
        for field in fields
    }
    require(math.isclose(
        means["first_launch_to_complete_s"],
        math.fsum(means[field + "_s"] for field in fields[2:]),
        abs_tol=1e-6,
    ), "request lifetime does not reconcile")
    return {
        "request_count": len(rows),
        "output_tokens": sum(row["output_tokens"] for row in rows),
        "workflow_equal_weight_s": means,
    }


def raw_worker_review(service):
    arm = Path(service["arm"])
    path = arm / "server/runtime_audit.jsonl"
    seal = service["input_jsonl_prefixes"][str(path)]
    known = service["requests"]
    turnover, skipped = [], 0
    groups, phases = defaultdict(list), Counter()
    clock = service["work"]["clock"]["monotonic_clock_domain"]
    for row in sealed_rows(
        path, seal, (b'"prefill_turnover_sample"', b'"gpu_service_sample"'),
    ):
        if row["event"] == "gpu_service_sample":
            require(row["monotonic_clock_domain"] == clock, "worker clock changed")
            members = row["request_samples"]
            require(all(item["request_id"] in known for item in members), "unknown member")
            require(len(members) == row["batch_size"], "incomplete worker geometry")
            phases[row["phase"]] += 1
            if row["phase"] == "decode":
                lengths = [item["sequence_tokens_before"] for item in members]
                require(all(type(n) is int and n >= 0 for n in lengths), "unknown context")
                require(type(row["cuda_graph_replayed"]) is bool, "unknown graph mode")
                group = (
                    row["batch_size"],
                    int(math.fsum(lengths) / len(lengths)) // 2048,
                    row["cuda_graph_replayed"],
                )
                groups[group].append(row["service_elapsed_ms"])
            continue
        ids = row["request_ids"]
        if not ids or not any(request_id in known for request_id in ids):
            skipped += 1
            continue
        require(all(request_id in known for request_id in ids), "mixed turnover ownership")
        require(row["monotonic_clock_domain"] == clock, "turnover clock changed")
        for phase in row["phases"].values():
            for metric in ("thread_cpu_ms", "wall_ms"):
                require(
                    isinstance(phase[metric], (int, float))
                    and math.isfinite(phase[metric]) and phase[metric] >= 0,
                    "invalid measured scheduler phase",
                )
        turnover.append(row)
    names = sorted({name for row in turnover for name in row["phases"]})
    return {
        "worker_batches": dict(phases),
        "turnover_samples": len(turnover),
        "non_workload_turnover_samples_excluded": skipped,
        "turnover_phases_ms": {
            name: {
                metric: stats(
                    row["phases"][name][metric]
                    for row in turnover if name in row["phases"]
                ) for metric in ("thread_cpu_ms", "wall_ms")
            } for name in names
        },
    }, groups


def profile_review(arm, seals):
    path = Path(arm) / "opportunities/admission_opportunities.jsonl"
    enabled, observed, states, last = 0, 0, 0, None
    for row in sealed_rows(path, seals[str(path)], (b'"admission_runtime_state"',)):
        require(row["event"] == "admission_runtime_state", "unexpected state row")
        states += 1
        enabled += row.get("profile_shared_path") is True
        observed += len(row.get("shared_path_timing", {}).get("phases", {}))
        last = row
    require(states > 0, "no recorded control state")
    return {
        "snapshots": states,
        "enabled_profile_snapshots": enabled,
        "recorded_profile_phases": observed,
        "whole_run_exclusive_control_cost_available": enabled > 0 and observed > 0,
        "last_snapshot_final": last["final"],
        "last_snapshot_counts_not_shutdown_final": {
            key: last["counts"].get(key, 0) for key in (
                "tool_wait_accepted", "prediction_maintenance_sweeps",
                "idle_full_prepare_checks", "idle_full_prepare_native_calls",
                "tool_prefetch_scans", "predicted_tool_offload_context_checks",
            )
        },
    }


def balance(left, right):
    result = []
    for support in (1, 16, 32, 64, 128):
        common = [
            group for group in left.keys() & right.keys()
            if min(len(left[group]), len(right[group])) >= support
        ]
        weights = {group: min(len(left[group]), len(right[group])) for group in common}
        total = sum(weights.values())
        means = [
            math.fsum(
                math.fsum(groups[group]) / len(groups[group]) * weights[group]
                for group in common
            ) / total if total else None for groups in (left, right)
        ]
        result.append({
            "minimum_batches_per_arm_per_bin": support,
            "common_bins": len(common),
            "common_batch_weight": total,
            "before_worker_mean_ms": means[0],
            "latest_worker_mean_ms": means[1],
            "latest_mean_increase_percent": (
                (means[1] / means[0] - 1) * 100 if means[0] else None
            ),
        })
    return result


def main():
    require(not OUTPUT.exists(), "preserve previous audit")
    comparison = load(HERE / "recent_comparison.json")
    require(comparison["same_exact_work_verified"] is True, "work not verified")

    def evidence(relative):
        path = (ROOT / relative).resolve()
        return load(path, comparison["inputs_sha256"][str(path)])

    before_service = evidence(
        "experiments/reports/tool_offload_before_admission_20261027/"
        "recent_preadmission_service_interleaving.json"
    )["arms"]["Full"]
    latest_service = evidence(
        "experiments/reports/tool_offload_before_admission_20261027/"
        "recent_grouped_and_curve_service_interleaving.json"
    )["arms"]["GroupedFull"]
    before_paths = evidence(
        "experiments/reports/tool_offload_before_admission_20261027/complete_jct_paths.json"
    )["arms"]["Full"]
    latest_paths = evidence(
        "experiments/reports/tool_offload_before_admission_20261027/"
        "recent_grouped_and_curve_jct_paths.json"
    )["arms"]["GroupedFull"]
    before_offload = load(HERE / "complete_offload_paths.json")
    latest_mechanism = evidence(
        "experiments/reports/join_blocked_layer_groups_20261028/mechanism_review.json"
    )
    require(
        before_paths["workflows"].keys() == latest_paths["workflows"].keys(),
        "changed critical workflow population",
    )
    require(all(
        before_paths["workflows"][workflow]["critical_path_requests"]
        == latest_paths["workflows"][workflow]["critical_path_requests"]
        for workflow in before_paths["workflows"]
    ), "critical request chains changed")
    result, geometry = {}, {}
    for label, service, paths, seals, config in (
        ("before", before_service, before_paths, before_offload["input_jsonl_prefixes"],
         comparison["arms"]["before_admission"]),
        ("latest", latest_service, latest_paths, latest_mechanism["input_jsonl_prefixes"],
         comparison["arms"]["grouped_layer"]),
    ):
        require(service["arm"] == paths["arm"] == config["arm"], "arm identity changed")
        raw, geometry[label] = raw_worker_review(service)
        require(len(service["requests"]) == 3674, "changed request count")
        require(raw["worker_batches"]["decode"] > 0, "decode coverage missing")
        result[label] = {
            "arm": service["arm"], "code_commit": config["code_commit"],
            "engine_tree": config["engine_tree"],
            "mean_jct_s": config["mean_jct_s"],
            "all_critical_requests": critical_partition(service, paths),
            "critical_tool_successors": critical_partition(service, paths, "post_tool"),
            "raw_worker_review": raw,
            "control_profile": profile_review(service["arm"], seals),
            "global_decode": config["decode"],
            "h2d_gb_decimal": config["h2d_gb_decimal"],
            "d2h_gb_decimal": config["d2h_gb_decimal"],
        }
    deltas = {}
    for population in ("all_critical_requests", "critical_tool_successors"):
        left = result["before"][population]
        right = result["latest"][population]
        require(
            (left["request_count"], left["output_tokens"])
            == (right["request_count"], right["output_tokens"]),
            "changed critical work",
        )
        deltas[population] = {
            key: right["workflow_equal_weight_s"][key] - value
            for key, value in left["workflow_equal_weight_s"].items()
        }
    report = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "arms": result, "critical_delta_workflow_equal_weight_s": deltas,
        "balanced_decode_worker_intervals": balance(geometry["before"], geometry["latest"]),
        "grouped_handoff_submit_to_journal_wall_ms_not_exclusive_cpu": (
            latest_mechanism["post_submit_to_journal_wall_ms_not_exclusive_cpu"]
        ),
        "same_exact_work_and_same_critical_request_chains_verified": True,
        "sources_sha256": SOURCES,
        "operator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "gpu_experiment_started": False, "production_code_changed": False,
        "isolated_control_cost_attribution": False,
        "limitations": [
            "Each configuration has one run; the native engines and several policies differ.",
            "The 24.981 seconds are tool-successor critical-chain sums divided by 18 workflows.",
            "Worker membership intervals include runtime and transfer dependencies, not pure kernels.",
            "Outside-member intervals are not measured control CPU or exposed GPU idle.",
            "Shared-path profiling was disabled; unrecorded exclusive control costs remain unknown.",
            "Turnover samples cover different sparse prefill populations, not all decode steps.",
            "Turnover phases may nest or overlap; their sums are not exclusive whole-run CPU.",
            "Matched bins use actual batch size, 2048-token mean-context bins and graph mode.",
            "Balanced worker intervals are descriptive, not identical batches or isolated CPU costs.",
            "Final recorded control counts are not shutdown-final counters.",
            "Transfer bytes and submit-to-journal intervals are not removable workflow time.",
        ],
    }
    with OUTPUT.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({
        "output": str(OUTPUT), "critical_deltas_s": deltas,
        "balanced_decode": report["balanced_decode_worker_intervals"],
        "profile_available": {
            label: arm["control_profile"]["whole_run_exclusive_control_cost_available"]
            for label, arm in result.items()
        },
    }, indent=2))


if __name__ == "__main__":
    main()
