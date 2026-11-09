"""Plot observed logical paused agents with replay blockers on the same clock."""

import argparse
from collections import Counter
import csv
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.audit_sustained_supply import bound_arrivals
from scripts.plot_workflow_cohort_comparison import COLORS
from scripts.train_workflow_work import write

COUNTS = (
    "ready_paused", "join_paused", "tool_paused", "other_paused",
    "paused_total", "active_ready", "inflight_workflows", "ready_paused_workflows",
)


def samples(events, states, arrivals):
    events = sorted(events, key=lambda row: row["ts_ms"])
    offsets = {row["workflow_id"]: row["offset_seconds"] for row in arrivals["arrival_schedule"]}
    origins = [
        row["ts_ms"] - 1000 * offsets[row["workflow_id"]]
        for row in events if row["kind"] == "workflow_start"
    ]
    if not origins or max(origins) - min(origins) > 1e-5:
        raise ValueError("replay clock differs from the bound arrival table")
    origin, owners = origins[0], {}
    for row in events:
        if row.get("invocation_id"):
            owner = row["invocation_id"]
            if owners.setdefault(owner, row["workflow_id"]) != row["workflow_id"]:
                raise ValueError("invocation identity crosses workflows")
    pending, tools, joins, arrived, ended = {}, set(), {}, set(), set()
    cursor, result, previous = 0, [], -math.inf
    for state in states:
        when = state["monotonic_ms"]
        if not math.isfinite(when) or when <= previous:
            raise ValueError("native observations must have an increasing finite clock")
        previous = when
        while cursor < len(events) and events[cursor]["ts_ms"] <= when:
            row = events[cursor]
            cursor += 1
            kind, invocation, workflow = row["kind"], row.get("invocation_id"), row["workflow_id"]
            attrs = row.get("attributes") or {}
            if kind == "workflow_start":
                arrived.add(workflow)
            elif kind == "workflow_end":
                ended.add(workflow)
            elif kind == "llm_submit":
                if invocation in pending:
                    raise ValueError("serial invocation has overlapping model submissions")
                pending[invocation] = attrs["request_id"]
            elif kind == "llm_result":
                if pending.pop(invocation, None) != attrs["request_id"]:
                    raise ValueError("model completion differs from the submitted identity")
            elif kind in ("tool_start", "tool_end"):
                key = invocation, attrs.get("tool_run_id") or attrs.get("tool_call_id")
                if kind == "tool_start":
                    tools.add(key)
                else:
                    tools.discard(key)
            elif kind == "join_wait":
                joins[workflow, row.get("join_id")] = invocation
            elif kind in ("join_satisfied", "join_timeout"):
                joins.pop((workflow, row.get("join_id")), None)
        if when < origin:
            continue
        a = state.get("agent_admission") or {}
        if state.get("physical_disabled") is not False or any(
            type(a.get(name)) is not list for name in ("paused_invocation_ids", "active_invocation_ids")
        ):
            raise ValueError("healthy exact native eligibility snapshots are required")
        active, all_paused = set(a["active_invocation_ids"]), set(a["paused_invocation_ids"])
        if active & all_paused or (active | all_paused) - owners.keys():
            raise ValueError("native membership is conflicting or has an unknown owner")
        paused = {owner for owner in all_paused if owners[owner] not in ended}
        tool_ids, join_ids = {key[0] for key in tools}, set(joins.values())
        blocked = tool_ids | join_ids
        ready = (pending.keys() & paused) - blocked
        join = paused & join_ids
        tool = (paused & tool_ids) - join
        other = paused - ready - join - tool
        if len(paused) != len(ready) + len(join) + len(tool) + len(other):
            raise ValueError("paused state partition is not conserved")
        result.append({
            "offset_s": (when - origin) / 1000.,
            "wall_ms": state["ts_ms"], "monotonic_ms": when,
            "ready_paused": len(ready), "join_paused": len(join), "tool_paused": len(tool),
            "other_paused": len(other), "paused_total": len(paused),
            "active_ready": len((pending.keys() & active) - blocked),
            "inflight_workflows": len(arrived - ended),
            "ready_paused_workflows": len({owners[owner] for owner in ready}),
            "join_and_tool_paused_overlap": len(paused & tool_ids & join_ids),
            "ended_native_paused_records": len(all_paused - paused),
            "logical_free_tokens": max(0, a["capacity_tokens"] - a["logical_active_tokens"]),
        })
    if len(result) < 2:
        raise ValueError("need at least two aligned native observations")
    return result, origin


def aggregate(rows, start, end, *, maximum_gap_s=1.5):
    total, area = Counter(), Counter()
    maxima = {name: 0 for name in COUNTS}
    for left, right in zip(rows, rows[1:]):
        lo, hi = max(start, left["offset_s"]), min(end, right["offset_s"])
        if hi <= lo or right["offset_s"] - left["offset_s"] > maximum_gap_s:
            continue
        duration = hi - lo
        total["covered_s"] += duration
        for name in COUNTS:
            area[name] += duration * left[name]
            maxima[name] = max(maxima[name], left[name])
        total["ready_present_s"] += duration * bool(left["ready_paused"])
        total["multiple_ready_workflows_s"] += duration * (left["ready_paused_workflows"] >= 2)
        total["free_logical_budget_no_ready_s"] += duration * (
            left["logical_free_tokens"] > 100 and not left["ready_paused"]
        )
    covered = total["covered_s"]
    return {
        "start_s": start, "end_s": end, "covered_s": covered,
        "coverage_fraction": covered / (end - start),
        "time_weighted_mean": {name: area[name] / covered if covered else None for name in COUNTS},
        "observed_max": maxima,
        "ready_present_fraction": total["ready_present_s"] / covered if covered else None,
        "multiple_ready_workflows_fraction": (
            total["multiple_ready_workflows_s"] / covered if covered else None
        ),
        "free_logical_budget_no_ready_fraction": (
            total["free_logical_budget_no_ready_s"] / covered if covered else None
        ),
    }


def inspect(registration_path, cohort):
    reg = json.loads(registration_path.read_bytes())
    _, arrivals = bound_arrivals(reg)
    arm = Path(reg["run_root"]) / "full"
    certificate = json.loads((Path(reg["run_root"]) / "cohort_termination/completion.json").read_bytes())
    if any(certificate.get(name) is not True for name in (
        "complete_measured_native_work_verified", "no_measured_workflow_truncated",
        "fresh_idle_drain_verified", "actual_owned_exit_verified",
    )):
        raise ValueError("require complete measured work, natural tails, fresh drain and actual exit")
    reader = SealedRecords()
    events = list(reader(arm / "replay/runtime_events.replay.jsonl"))
    states = [
        row for row in reader(arm / "opportunities/admission_opportunities.jsonl")
        if row.get("event") == "admission_runtime_state"
    ]
    rows, origin = samples(events, states, arrivals)
    members = set(cohort["measured_workflow_ids"])
    ends = {row["workflow_id"]: row["ts_ms"] for row in events if row["kind"] == "workflow_end"}
    if not members <= ends.keys():
        raise ValueError("fixed measured members did not naturally complete")
    end = max(ends[member] for member in members) / 1000. - origin / 1000.
    start, common_end = cohort["throughput_window_offset_seconds"]
    if rows[-1]["offset_s"] < end or end < common_end:
        raise ValueError("observations do not cover the complete measurement population")
    summaries = [aggregate(rows, lo, min(lo + 30., end)) for lo in range(0, math.ceil(end), 30)]
    return rows, {
        "registration": str(registration_path.resolve()),
        "cohort_completion_s": end, "common_window": aggregate(rows, start, common_end),
        "through_measured_completion": aggregate(rows, 0., end), "bins": summaries,
        "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {
            str(path): sha256_file(path) for path in (
                registration_path, Path(reg["workflow_arrivals_path"]),
                Path(reg["run_root"]) / "cohort_termination/completion.json",
            )
        },
    }


def render(reports, cohort, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(3, 1, figsize=(12, 9), sharex=True, constrained_layout=True)
    start, end = cohort["throughput_window_offset_seconds"]
    for ax, name, title in zip(
        axes, COUNTS[:3], ("Ready-paused Agents", "JOIN-paused Agents", "Tool-paused Agents"),
    ):
        for label, report in reports.items():
            bins = report["bins"]
            values = [
                row["time_weighted_mean"][name] if row["coverage_fraction"] >= .5 else math.nan
                for row in bins
            ]
            ax.stairs(
                values, [bins[0]["start_s"], *(row["end_s"] for row in bins)],
                color=COLORS[label], linewidth=1.4, label=label, baseline=None,
            )
        ax.axvspan(start, end, color="#999999", alpha=.10)
        ax.set(title=title, ylabel="Agents")
        ax.set_ylim(bottom=0)
        ax.grid(alpha=.2)
    axes[0].legend(loc="upper right", ncol=2, fontsize=9)
    axes[-1].set_xlabel("Seconds Since First Workflow Arrival")
    axes[-1].set_xlim(left=0)
    fig.suptitle(
        "All agents including background; 30-second time-weighted means\n"
        "Observed logical paused membership; JOIN takes precedence over overlapping tool waits. "
        "Shading: common throughput window.",
        fontsize=10,
    )
    for suffix in ("png", "pdf"):
        fig.savefig(output / ("paused_agents_over_time." + suffix), dpi=180)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="append", required=True, help="METHOD=REGISTRATION_JSON")
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("preserve previous reports and hide CUDA for plotting")
    cohort, reports, raw = json.loads(args.cohort.read_bytes()), {}, {}
    for argument in args.report:
        label, separator, path = argument.partition("=")
        if not separator or label not in COLORS or label in reports:
            raise ValueError("require distinct known methods")
        raw[label], reports[label] = inspect(Path(path), cohort)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    for label, rows in raw.items():
        path = args.output_dir / (label.lower().replace(" ", "_") + "_samples.csv")
        with path.open("x", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    render(reports, cohort, args.output_dir)
    write(args.output_dir / "report.json", {
        "methods": reports, "cohort": cohort,
        "inputs_sha256": {
            str(path.resolve()): sha256_file(path) for path in (Path(__file__), args.cohort)
        },
        "maximum_native_observation_gap_s": 1.5,
        "partition": "ready; observed JOIN; observed tool without JOIN; other paused",
        "logical_pause_does_not_prove_physical_host_residency": True,
        "multiple_ready_workflows_does_not_prove_capacity_competition": True,
        "no_interpolation_across_observation_gaps": True,
        "GPU_action": False,
    })
    print(json.dumps({name: report["common_window"] for name, report in reports.items()}))


if __name__ == "__main__":
    main()
