"""Screen child-materialization workloads using sealed, compact CPU evidence."""

import argparse
from bisect import bisect_right
import csv
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.host_numa import FULL_BYTES_PER_TOKEN
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workflow_cohort import validate_cohort
from scripts.prepare_arrival_rate_followup import completion_coverage_screen, followup_protocol
from scripts.train_workflow_work import write


def host_tokens(gb):
    if type(gb) is not int or gb <= 0:
        raise ValueError("require a positive integer decimal Host GB")
    return gb * 1_000_000_000 // FULL_BYTES_PER_TOKEN + 1


def weighted_quantile(values, probability):
    rows = sorted((value, duration) for value, duration in values if duration > 0)
    if not rows or not 0 <= probability <= 1:
        raise ValueError("require observed positive-duration values and a valid quantile")
    limit = probability * sum(duration for _, duration in rows)
    cumulative = 0.
    for value, duration in rows:
        cumulative += duration
        if cumulative >= limit:
            return value
    return rows[-1][0]


def joined_steps(demand, supply, begin, end):
    """Join last-observed samples without extending beyond either evidence end."""
    demand = sorted(demand, key=lambda row: row["arrival_offset_s"])
    supply = sorted(supply, key=lambda row: row["offset_s"])
    times_d = [row["arrival_offset_s"] for row in demand]
    times_s = [row["offset_s"] for row in supply]
    if not (
        math.isfinite(begin) and math.isfinite(end) and begin < end
        and times_d[0] <= begin < end <= times_d[-1]
        and times_s[0] <= begin < end <= times_s[-1]
        and all(left < right for times in (times_d, times_s)
                for left, right in zip(times, times[1:]))
    ):
        raise ValueError("both compact streams must cover the whole requested window")
    edges = sorted({begin, end, *(
        value for value in times_d + times_s if begin < value < end
    )})
    return [
        (left, right, demand[bisect_right(times_d, left) - 1],
         supply[bisect_right(times_s, left) - 1])
        for left, right in zip(edges, edges[1:])
    ]


def capacity_screen(steps, device_tokens, gb):
    combined = device_tokens + host_tokens(gb)
    duration = sum(right - left for left, right, _, _ in steps)
    pressure_s = ready_pressure_s = pending_pressure_s = no_ready_s = 0.
    ready_area = active_area = 0.
    reusable_values, pending_values, host_values = [], [], []
    peak_deficit = pending_peak_deficit = 0
    for left, right, demand, supply in steps:
        dt = right - left
        reusable = demand["next_request_reusable_prior_prefix_union_tokens"]
        pending = demand["pending_input_or_latest_served_union_tokens"]
        ready = supply["ready_paused"]
        active = supply["active_ready"]
        if reusable > combined:
            pressure_s += dt
            ready_pressure_s += dt * (ready > 0)
        pending_pressure_s += dt * (pending > combined)
        no_ready_s += dt * (ready == 0)
        ready_area += ready * dt
        active_area += active * dt
        peak_deficit = max(peak_deficit, reusable - combined)
        pending_peak_deficit = max(pending_peak_deficit, pending - combined)
        reusable_values.append((reusable, dt))
        pending_values.append((pending, dt))
        host_values.append((max(0, reusable - device_tokens), dt))
    unit_gb = FULL_BYTES_PER_TOKEN / 1_000_000_000
    return {
        "Host_decimal_GB": gb, "combined_tokens": combined,
        "combined_decimal_GB": combined * unit_gb, "window_seconds": duration,
        "reusable_union_exceeds_capacity_seconds": pressure_s,
        "reusable_union_exceeds_capacity_fraction": pressure_s / duration,
        "reusable_pressure_and_ready_paused_seconds": ready_pressure_s,
        "pending_union_exceeds_capacity_fraction": pending_pressure_s / duration,
        "peak_reusable_union_deficit_decimal_GB": peak_deficit * unit_gb,
        "peak_pending_union_deficit_decimal_GB": pending_peak_deficit * unit_gb,
        "time_mean_ready_paused": ready_area / duration,
        "time_mean_active_ready": active_area / duration,
        "no_ready_paused_fraction": no_ready_s / duration,
        "reusable_union_decimal_GB_quantiles": {
            str(q): weighted_quantile(reusable_values, q) * unit_gb for q in (.5, .8, .9, .95)
        },
        "minimum_Host_for_observed_reusable_union_decimal_GB_quantiles": {
            str(q): weighted_quantile(host_values, q) * unit_gb for q in (.5, .8, .9, .95)
        },
        "pending_union_decimal_GB_p90": weighted_quantile(pending_values, .9) * unit_gb,
        "counterfactual_capacity_only_with_observed_service_and_states_fixed": True,
        "new_pressure_policy_or_arrival_service_not_simulated": True,
        "observed_ready_includes_unmaterialized_first_calls": True,
        "actual_extra_prefill_or_Host_only_bytes_not_established": True,
    }


def arrival_screen(arrivals, cohort, complete, assessment, expected, intervals):
    workflows = {row["workflow_id"]: row for row in assessment["workflows"]}
    schedule = arrivals["arrival_schedule"]
    if set(workflows) != {row["workflow_id"] for row in schedule}:
        raise ValueError("work assessment and arrival identities differ")
    following = schedule[arrivals["initial_workflows"]:]
    mean_work = sum(workflows[row["workflow_id"]]["model_work_units"] for row in following) / len(following)
    outputs = {workflow: 0 for workflow in workflows}
    for call in expected.values():
        identity, tokens = call["llm_result"]
        if (
            identity != call["llm_submit"][0] or identity[0] not in outputs
            or type(tokens) is not int or tokens < 0
        ):
            raise ValueError("source request identities or output counts are invalid")
        outputs[identity[0]] += tokens
    mean_output = sum(outputs[row["workflow_id"]] for row in following) / len(following)
    last_old = schedule[-1]["offset_seconds"]
    minimum_interval = max(
        complete["measured_workflow_JCT_ms"][row["workflow_id"]] / 1000.
        / (last_old - row["offset_seconds"]) / arrivals["rate_per_second"]
        for row in schedule if row["workflow_id"] in cohort["measured_workflow_ids"]
    )
    result = []
    for interval in intervals:
        candidate, _ = followup_protocol(
            arrivals, cohort, mean_interval_seconds=interval,
        )
        screen = completion_coverage_screen(arrivals, cohort, complete, candidate)
        screen.update(
            mean_interval_s=interval,
            arrival_rate_change_percent=(1. / interval / arrivals["rate_per_second"] - 1.) * 100.,
            following_mean_model_work_units=mean_work,
            nominal_new_work_units_per_second=mean_work / interval,
            nominal_new_output_tokens_per_second=mean_output / interval,
            nominal_output_to_observed_common_window_throughput_ratio=(
                mean_output / interval / complete["common_window_output_tokens_per_second"]
            ),
            observed_finite_window_throughput_is_not_sustainable_capacity=True,
            observed_output_tps_is_not_model_work_capacity=True,
            common_window_decode_tps_reference=complete["common_window_decode_tokens_per_second"],
            no_paused_supply_or_JCT_prediction=True,
        )
        result.append(screen)
    return {
        "minimum_interval_for_static_observed_measured_JCT_coverage_s": minimum_interval,
        "source_requests": len(expected),
        "source_output_tokens": sum(outputs.values()),
        "candidates": result,
    }


def analyze(paths, output, *, hosts, intervals):
    if output.exists():
        raise FileExistsError("preserve previous workload screens")
    pins = {str(path.resolve()): sha256_file(path) for path in paths.values()}
    documents = {
        name: json.loads(path.read_bytes()) for name, path in paths.items() if name != "supply"
    }
    with paths["supply"].open(newline="") as stream:
        supply = [
            {name: float(value) for name, value in row.items()}
            for row in csv.DictReader(stream)
        ]
    arrivals, cohort = documents["arrivals"], documents["cohort"]
    validate_cohort(cohort, arrivals)
    complete, demand = documents["complete"], documents["demand"]
    if complete["cohort"] != cohort or not complete["all_measured_completed"]:
        raise ValueError("require complete evidence for the exact fixed cohort")
    capacity = demand["capacity_evidence"]["capacity"]
    if (
        capacity["pool_layout"] != "static_full_only"
        or capacity["device_full_bytes"] not in (
            capacity["device_full_tokens"] * FULL_BYTES_PER_TOKEN,
            (capacity["device_full_tokens"] + 1) * FULL_BYTES_PER_TOKEN,
        )
        or demand["regular_sample_interval_s"] != 60
    ):
        raise ValueError("require the observed FULL geometry and declared sample cadence")
    begin, common_end = cohort["throughput_window_offset_seconds"]
    end = complete["measured_cohort_completion_offset_s"]
    points = demand["observations"]
    windows = {
        "common_throughput_window": (begin, common_end),
        "complete_measured_lifetimes": (begin, end),
    }
    tables = {
        name: [
            capacity_screen(joined_steps(points, supply, left, right), capacity["device_full_tokens"], gb)
            for gb in hosts
        ]
        for name, (left, right) in windows.items()
    }
    initial_ids = {
        row["workflow_id"] for row in arrivals["arrival_schedule"][:arrivals["initial_workflows"]]
    }
    initial_tokens = sum(
        row["initial_prompt_tokens"] for row in documents["assessment"]["workflows"]
        if row["workflow_id"] in initial_ids
    )
    result = {
        "scope": "sealed_compact_evidence_joint_Host_arrival_CPU_screen",
        "inputs_sha256": pins, "source_script_sha256": sha256_file(Path(__file__)),
        "workflows": len(arrivals["arrival_schedule"]),
        "initial_workflows": arrivals["initial_workflows"],
        "measured_workflow_ids": cohort["measured_workflow_ids"],
        "device_FULL_tokens": capacity["device_full_tokens"],
        "device_buffer_bytes_include_reserved_slot_not_available_for_KV": (
            capacity["device_full_bytes"]
            > capacity["device_full_tokens"] * FULL_BYTES_PER_TOKEN
        ),
        "initial_root_prompt_tokens": initial_tokens,
        "initial_root_prompt_decimal_GB": initial_tokens * FULL_BYTES_PER_TOKEN / 1e9,
        "capacity_screens": tables,
        "arrival_screen": arrival_screen(
            arrivals, cohort, complete, documents["assessment"], documents["expected"], intervals,
        ),
        "interpretation": {
            "unique_prefix_union_is_not_physical_Host_occupancy": True,
            "capacity_overflow_is_not_a_recomputation_or_policy_gain_estimate": True,
            "sixty_second_demand_samples_cannot_locate_subsecond_capacity_actions": True,
            "all_ready_paused_are_not_existing_KV_restore_candidates": True,
            "no_inference_of_stability_from_a_finite_arrival_plan": True,
            "native_Thunder_and_Full_require_same_resource_and_arrival_contract": True,
        },
        "GPU_action": False, "GPU_followup_registered": False, "performance_accepted": False,
    }
    if pins != {str(path.resolve()): sha256_file(path) for path in paths.values()}:
        raise ValueError("compact evidence changed during analysis")
    write(output / "screen.json", result)
    return result


def plot_screen(report_path, output):
    """Plot the original observed workload and alternate capacity ceilings."""
    if output.exists():
        raise FileExistsError("preserve previous figures")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    report = json.loads(report_path.read_bytes())
    for name, digest in report["inputs_sha256"].items():
        if sha256_file(Path(name)) != digest:
            raise ValueError("a compact plotted input changed")
    paths = list(report["inputs_sha256"])
    demand_path, = (Path(name) for name in paths if name.endswith("/demand.json"))
    supply_path, = (Path(name) for name in paths if name.endswith("_samples.csv"))
    cohort_path, = (Path(name) for name in paths if name.endswith("/analysis_cohort.json"))
    complete_path, = (Path(name) for name in paths if name.endswith("/cohort.json"))
    demand = json.loads(demand_path.read_bytes())["observations"]
    cohort = json.loads(cohort_path.read_bytes())
    end = json.loads(complete_path.read_bytes())["measured_cohort_completion_offset_s"]
    with supply_path.open(newline="") as stream:
        supply = list(csv.DictReader(stream))
    figure, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True, layout="constrained")
    unit = FULL_BYTES_PER_TOKEN / 1e9
    x = [row["arrival_offset_s"] / 60. for row in demand]
    axes[0].step(x, [
        row["next_request_reusable_prior_prefix_union_tokens"] * unit for row in demand
    ], where="post", label="Old KV reused by a later request", color="#007a5e")
    axes[0].step(x, [
        row["pending_input_or_latest_served_union_tokens"] * unit for row in demand
    ], where="post", label="Pending input / latest served KV", color="#e69f00", alpha=.8)
    for gb, color in ((200, "#c94040"), (224, "#777777"), (256, "#347ab8")):
        axes[0].axhline(
            (host_tokens(gb) + report["device_FULL_tokens"]) * unit,
            linestyle="--", linewidth=1, color=color, label=f"HBM + {gb} GB Host",
        )
    axes[0].set_ylabel("Unique logical KV (decimal GB)")
    axes[0].legend(fontsize=8, ncol=2)
    xs = [float(row["offset_s"]) / 60. for row in supply]
    for name, label, color in (
        ("active_ready", "Active, application ready", "#555555"),
        ("ready_paused", "Ready-paused, including first calls", "#0072b2"),
        ("tool_paused", "Tool-paused", "#009e73"),
        ("join_paused", "JOIN-paused", "#d55e00"),
    ):
        axes[1].step(
            xs, [float(row[name]) for row in supply],
            where="post", label=label, color=color, linewidth=.9,
        )
    axes[1].set_ylabel("Agents")
    axes[1].set_xlabel("Time since first workflow arrival (minutes)")
    axes[1].legend(fontsize=8, ncol=2)
    start, finish = cohort["throughput_window_offset_seconds"]
    for axis in axes:
        axis.axvspan(start / 60., finish / 60., color="#777777", alpha=.08)
        axis.set_xlim(0., end / 60.)
        axis.grid(axis="y", alpha=.15)
    figure.suptitle(
        "Observed 133-workflow run: 20 initial, mean 60 s arrivals, actual Host 400 GB\n"
        "Alternate capacity screen only; no new policy or arrival performance simulated",
        fontsize=11,
    )
    output.mkdir(parents=True)
    for suffix in ("png", "pdf"):
        figure.savefig(output / ("capacity_and_supply." + suffix), dpi=180)
    plt.close(figure)
    write(output / "evidence.json", {
        "screen": str(report_path.resolve()), "screen_sha256": sha256_file(report_path),
        "script_sha256": sha256_file(Path(__file__)),
        "artifacts_sha256": {
            name: sha256_file(output / name)
            for name in ("capacity_and_supply.png", "capacity_and_supply.pdf")
        }, "GPU_action": False,
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("demand", "supply", "arrivals", "cohort", "complete", "assessment", "expected"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--host-gb", type=int, nargs="+", default=(160, 192, 200, 224, 240, 256, 300, 400))
    parser.add_argument("--mean-interval-s", type=float, nargs="+", default=(60., 55., 50., 45., 40.))
    parser.add_argument("--figures", action="store_true")
    args = parser.parse_args()
    report = analyze(
        {name: getattr(args, name) for name in (
            "demand", "supply", "arrivals", "cohort", "complete", "assessment", "expected",
        )},
        args.output_dir, hosts=args.host_gb, intervals=args.mean_interval_s,
    )
    if args.figures:
        plot_screen(args.output_dir / "screen.json", args.output_dir / "figures")
    print(json.dumps({
        "host_screens": len(report["capacity_screens"]["complete_measured_lifetimes"]),
        "arrival_intervals": [row["mean_interval_s"] for row in report["arrival_screen"]["candidates"]],
        "minimum_static_coverage_interval_s": report["arrival_screen"][
            "minimum_interval_for_static_observed_measured_JCT_coverage_s"
        ], "GPU_action": False,
    }))


if __name__ == "__main__":
    main()
