#!/usr/bin/env python3
"""Plot observed paused-agent states alongside diagnostic native KV capacity."""

from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
import csv
import hashlib
import json
import math
import os
from pathlib import Path

os.environ.setdefault("MPLBACKEND", "Agg")

GIB = 1024**3
COUNT_KEYS = (
    "ready_paused", "join_paused", "tool_paused", "other_paused",
    "tool_blocked_active", "tool_blocked_total", "join_blocked_total",
    "active_ready", "paused_total", "terminal_paused", "inflight_workflows",
)
COLORS = {
    "ready_paused": "#0072B2",
    "join_paused": "#D55E00",
    "tool_paused": "#009E73",
    "free": "#0072B2",
    "evictable": "#777777",
}


class Inputs:
    def __init__(self):
        self.files = {}

    def document(self, path):
        path = Path(path).resolve()
        data = path.read_bytes()
        self.files[str(path)] = {
            "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
        }
        return json.loads(data)

    def records(self, path, event_names=None):
        path = Path(path).resolve()
        size = path.stat().st_size
        digest = hashlib.sha256()
        count = 0
        selected = 0
        markers = tuple(
            marker
            for name in event_names or ()
            for marker in (
                f'"event":"{name}"'.encode(),
                f'"event": "{name}"'.encode(),
            )
        )
        with path.open("rb") as stream:
            for raw in stream:
                if not raw.endswith(b"\n"):
                    raise ValueError(f"incomplete JSONL line: {path}")
                digest.update(raw)
                count += 1
                if markers and not any(marker in raw for marker in markers):
                    continue
                row = json.loads(raw)
                if event_names is None or row.get("event") in event_names:
                    selected += 1
                    yield row
            if stream.tell() != size or path.stat().st_size != size:
                raise ValueError(f"input changed during analysis: {path}")
        self.files[str(path)] = {
            "bytes": size, "sha256": digest.hexdigest(),
            "records": count, "selected_records": selected,
        }


def tool_key(row):
    attrs = row.get("attributes") or {}
    key = attrs.get("tool_run_id") or attrs.get("tool_call_id")
    if key is None:
        raise ValueError("tool event has no stable call identity")
    return row["invocation_id"], key


def weighted_quantile(values, quantile):
    values = sorted((value, weight) for value, weight in values if weight > 0)
    total = sum(weight for _, weight in values)
    if total == 0:
        return None
    target = quantile * total
    cumulative = 0.
    for value, weight in values:
        cumulative += weight
        if cumulative >= target:
            return value
    return values[-1][0]


def distribution(values):
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "mean": sum(values) / len(values),
        **{
            f"p{int(q * 100)}": weighted_quantile([(v, 1) for v in values], q)
            for q in (.5, .9, .95, .99)
        },
        "max": max(values),
    }


def observed_segments(rows, start, end, max_gap_s):
    for left, right in zip(rows, rows[1:]):
        lo = max(start, left["offset_s"])
        hi = min(end, right["offset_s"])
        if hi <= lo or right["offset_s"] - left["offset_s"] > max_gap_s:
            continue
        yield left, lo, hi


def summarize(rows, start, end, max_gap_s):
    segments = list(observed_segments(rows, start, end, max_gap_s))
    covered = sum(hi - lo for _, lo, hi in segments)
    result = {
        "start_s": start, "end_s": end, "duration_s": end - start,
        "covered_s": covered,
        "coverage_fraction": covered / (end - start),
        "state_snapshot_count": sum(start <= row["offset_s"] < end for row in rows),
        "agents": {},
    }
    for key in COUNT_KEYS:
        values = [(row[key], hi - lo) for row, lo, hi in segments]
        result["agents"][key] = {
            "time_weighted_mean": (
                sum(value * weight for value, weight in values) / covered
                if covered else None
            ),
            "time_weighted_p95": weighted_quantile(values, .95),
            "sampled_peak": max((v for v, _ in values), default=0),
            "present_s": sum(weight for value, weight in values if value > 0),
            "present_fraction": (
                sum(weight for value, weight in values if value > 0) / covered
                if covered else None
            ),
            "agent_seconds": sum(value * weight for value, weight in values),
        }
    physical = [
        (row, hi - lo) for row, lo, hi in segments
        if row["pool_free_gib"] is not None
    ]
    physical_s = sum(weight for _, weight in physical)
    result["diagnostic_capacity"] = {
        "covered_s": physical_s,
        "coverage_fraction": physical_s / (end - start),
    }
    for name in ("pool_free_gib", "pool_evictable_gib", "pool_non_evictable_gib"):
        values = [(row[name], weight) for row, weight in physical]
        result["diagnostic_capacity"][name] = {
            "time_weighted_mean": (
                sum(value * weight for value, weight in values) / physical_s
                if physical_s else None
            ),
            "time_weighted_p50": weighted_quantile(values, .5),
            "time_weighted_p95": weighted_quantile(values, .95),
            "sampled_max": max((value for value, _ in values), default=None),
        }
    opportunities = {}
    for threshold in (0., 1., 2., 4., 8.):
        selected = [
            (row, weight) for row, weight in physical
            if row["tool_paused"] > 0 and row["pool_free_gib"] > threshold
        ]
        duration = sum(weight for _, weight in selected)
        opportunities[str(threshold)] = {
            "tool_paused_and_free_above_gib_s": duration,
            "fraction_of_physical_covered_s": duration / physical_s if physical_s else None,
            "including_no_ready_paused_s": sum(
                weight for row, weight in selected if row["ready_paused"] == 0
            ),
            "including_no_ready_paused_or_active_s": sum(
                weight for row, weight in selected
                if row["ready_paused"] == 0 and row["active_ready"] == 0
            ),
            "paused_tool_agent_seconds": sum(
                weight * row["tool_paused"] for row, weight in selected
            ),
            "sampled_max_simultaneous_tool_paused": max(
                (row["tool_paused"] for row, _ in selected), default=0,
            ),
        }
    result["cooccurrence_not_verified_prefetch_fit"] = opportunities
    result["join_tool_blocking_overlap_agent_seconds"] = sum(
        row["join_tool_overlap"] * (hi - lo) for row, lo, hi in segments
    )
    return result


def write_csv(path, rows):
    with path.open("x", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def plot(rows, output, end_s, window, *, stem, xlim=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties
    from matplotlib.ticker import MaxNLocator
    import numpy as np

    font = FontProperties(
        fname="/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    )
    plt.rcParams.update({
        "font.family": font.get_name(), "axes.unicode_minus": False,
        "font.size": 11, "axes.spines.top": False, "axes.spines.right": False,
        "axes.titleweight": "bold", "savefig.facecolor": "white",
        "pdf.fonttype": 42, "ps.fonttype": 42,
    })
    fig, axes = plt.subplots(
        4, 1, figsize=(13.5, 10.4), sharex=True,
        gridspec_kw={"height_ratios": [1, 1, 1, 1.25]},
    )
    fig.subplots_adjust(left=.085, right=.98, top=.86, bottom=.095, hspace=.38)
    titles = {
        "ready_paused": "ready-paused：模型请求已登记、依赖已就绪、尚无 active 资格",
        "join_paused": "join-paused：无 active 资格，且仍有真实 JOIN 依赖",
        "tool_paused": "tool-paused：无 active 资格，且仍有真实工具调用未结束",
    }
    times = np.array([row["offset_s"] for row in rows])
    gaps = np.r_[False, np.diff(times) > 2.5]
    for ax, key in zip(axes[:3], titles):
        values = np.array([row[key] for row in rows], dtype=float)
        values[gaps] = np.nan
        ax.step(times, values, where="post", linewidth=.65, alpha=.46,
                color=COLORS[key], label="约 1 s 状态快照")
        means, mids = [], []
        for start in range(0, math.ceil(end_s), 15):
            segments = list(observed_segments(rows, start, min(start + 15, end_s), 2.5))
            duration = sum(hi - lo for _, lo, hi in segments)
            if duration:
                mids.append(start + min(15, end_s - start) / 2)
                means.append(sum(row[key] * (hi - lo) for row, lo, hi in segments) / duration)
        ax.plot(mids, means, linewidth=1.65, color=COLORS[key],
                label="15 s 时间加权均值")
        ax.set_title(titles[key], loc="left", fontsize=11, pad=8)
        ax.set_ylabel("agent 数")
        ax.set_ylim(bottom=-.15)
        ax.yaxis.set_major_locator(MaxNLocator(integer=True, nbins=5))
        ax.grid(axis="y", alpha=.17)
    axes[0].legend(loc="upper right", frameon=False, ncol=2, fontsize=9)
    physical = axes[3]
    for key, label, color in (
        ("pool_free_gib", "allocator 实际空闲容量", COLORS["free"]),
        ("pool_evictable_gib", "可驱逐缓存容量", COLORS["evictable"]),
    ):
        values = np.array([
            row[key] if row[key] is not None else np.nan for row in rows
        ])
        values[gaps] = np.nan
        physical.plot(times, values, linewidth=.9, color=color, label=label)
    physical.set_title(
        "HBM KV 池容量诊断：可驱逐页不等于可直接用于预取的空闲页",
        loc="left", fontsize=11, pad=8,
    )
    physical.set_ylabel("容量 / GiB")
    physical.set_ylim(bottom=-.25)
    physical.grid(axis="y", alpha=.17)
    physical.legend(loc="upper right", frameon=False, ncol=2, fontsize=9)
    for ax in axes:
        ax.axvspan(*window, color="#e5e5e5", alpha=.4, zorder=-5)
    axes[-1].set_xlabel("距首批 workflow 到达的时间 / s")
    axes[-1].set_xlim(*(xlim or (0, end_s)))
    fig.suptitle("最新实验：等待 agent 随时间的变化", x=.085, ha="left",
                 fontsize=19, fontweight="bold", y=.975)
    fig.text(
        .085, .936,
        "2026-11-08 tool-band-pressure oracle | 全部已到达 workflow，含暖机与背景任务",
        fontsize=10.5, color="#444444",
    )
    fig.text(
        .085, .912,
        "灰色背景为冻结共同窗口；三类 paused 互斥；曲线是采样观测，不是精确事件持续时间。",
        fontsize=9.5, color="#666666",
    )
    fig.text(
        .085, .028,
        "截止于第 18 个测量 workflow 完成；非测量背景任务被右删失。"
        "HBM 曲线不是有效 active-KV 利用率。",
        fontsize=9, color="#666666",
    )
    for extension in ("png", "pdf"):
        fig.savefig(output / f"{stem}.{extension}", dpi=180)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-gap-s", type=float, default=2.5)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve earlier analysis artifacts")
    inputs = Inputs()
    registration = inputs.document(args.registration)
    cohort = inputs.document(args.cohort)
    arrivals = inputs.document(registration["workflow_arrivals_path"])
    if arrivals != registration["workflow_arrivals"]:
        raise ValueError("arrival schedule changed since registration")
    run = Path(registration["run_root"])
    arm = run / "full"
    completion = inputs.document(run / "cohort_termination/completion.json")
    if not completion["no_measured_workflow_truncated"]:
        raise ValueError("require complete measured workflows")
    capacity = inputs.document(arm / "server/native_capacity_census.json")["capacity"]
    bytes_per_token = capacity["host_full_bytes"] // capacity["host_full_tokens"]
    if bytes_per_token * capacity["host_full_tokens"] != capacity["host_full_bytes"]:
        raise ValueError("Host byte geometry is not integral")
    replay = []
    for row in inputs.records(arm / "replay/runtime_events.replay.jsonl"):
        attrs = row.get("attributes") or {}
        replay.append({
            **{key: row.get(key) for key in (
                "kind", "ts_ms", "workflow_id", "invocation_id", "join_id",
                "target_invocation_id",
            )},
            "attributes": {
                key: attrs[key] for key in (
                    "tool_run_id", "tool_call_id", "tool_name", "request_id",
                )
                if key in attrs
            },
        })
    inversions = sum(a["ts_ms"] > b["ts_ms"] for a, b in zip(replay, replay[1:]))
    replay.sort(key=lambda row: row["ts_ms"])
    offsets = {
        row["workflow_id"]: row["offset_seconds"]
        for row in arrivals["arrival_schedule"]
    }
    origin_values = [
        row["ts_ms"] - offsets[row["workflow_id"]] * 1000
        for row in replay if row["kind"] == "workflow_start"
    ]
    if max(origin_values) - min(origin_values) > 1e-4:
        raise ValueError("replay clock does not match frozen arrivals")
    origin = origin_values[0]
    end = (completion["cohort_completion_monotonic_ms"] - origin) / 1000
    window = cohort["throughput_window_offset_seconds"]
    snapshots = []
    action_counts = Counter()
    action_bytes = Counter()
    observed_flags = {}
    for row in inputs.records(
        arm / "opportunities/admission_opportunities.jsonl",
        {"admission_runtime_state", "predicted_tool_kv_evicted"},
    ):
        if row["event"] == "predicted_tool_kv_evicted":
            action_counts["predicted_tool_kv_evicted"] += 1
            action_bytes["predicted_tool_kv_evicted"] += row["freed_bytes"]
            continue
        a = row["agent_admission"]
        if row["physical_disabled"] is not False:
            raise ValueError("physical serving was disabled")
        snapshots.append({
            "t": row["monotonic_ms"], "wall": row["ts_ms"],
            "active": set(a["active_invocation_ids"]),
            "paused": set(a["paused_invocation_ids"]),
            "paused_pending": set(a["paused_pending_invocation_ids"]),
            "marked": set(a["marked_for_pause_invocation_ids"]),
            "logical_active_tokens": a["logical_active_tokens"],
            "capacity_tokens": a["capacity_tokens"],
        })
        observed_flags = {
            "tool_prefetch": row["tool_prefetch"],
            "tool_warming_eligibility": a.get("tool_warming_eligibility"),
            "acting_decay": a.get("acting_token_decay"),
            "future_information_used": a.get("future_information_used"),
        }
    snapshots.sort(key=lambda row: row["t"])
    if any(a["t"] >= b["t"] for a, b in zip(snapshots, snapshots[1:])):
        raise ValueError("native state clocks are not strictly increasing")
    walls = [row["wall"] for row in snapshots]
    if any(a >= b for a, b in zip(walls, walls[1:])):
        raise ValueError("wall clock is not increasing")
    clock_offsets = [row["wall"] - row["t"] for row in snapshots]

    def wall_to_mono(when):
        index = max(0, min(bisect_right(walls, when) - 1, len(walls) - 1))
        return when - clock_offsets[index]

    physical = []
    for row in inputs.records(arm / "server/host_pool_telemetry.jsonl"):
        if row["event"] != "host_pool_usage":
            continue
        pool = (row.get("device_pools") or {}).get("full") or {}
        if pool.get("observable") is not True:
            continue
        if pool["free_units"] + pool["resident_units"] != pool["capacity_units"]:
            raise ValueError("native physical capacity does not conserve units")
        if pool["evictable_units"] + pool["non_evictable_units"] != pool["resident_units"]:
            raise ValueError("native resident capacity does not conserve units")
        physical.append({
            "t": wall_to_mono(row["ts_ms"]),
            **{
                name: pool[name] for name in (
                    "free_units", "evictable_units", "non_evictable_units", "capacity_units",
                )
            },
        })
    physical.sort(key=lambda row: row["t"])
    physical_times = [row["t"] for row in physical]
    native = []
    for row in inputs.records(arm / "server/runtime_events.sglang.jsonl"):
        if row["kind"] == "llm_submit":
            native.append({
                "invocation_id": row["invocation_id"],
                "t": wall_to_mono(row["ts_ms"]),
                "host_tokens": (row.get("attributes") or {}).get("cached_tokens_host", 0),
                "request_id": (row.get("attributes") or {}).get("request_id"),
            })
    native.sort(key=lambda row: row["t"])
    native_by_request = {}
    for row in native:
        native_by_request[row["request_id"]] = row
    replay_requests = defaultdict(list)
    for event in replay:
        if event["kind"] == "llm_submit":
            replay_requests[event["invocation_id"]].append(event)
    tools = defaultdict(set)
    tool_intervals = []
    tool_starts = {}
    joins = {}
    foreground = {}
    terminal = set()
    ended_workflows = set()
    invocation_workflow = {}
    arrived = set()
    model_pending = set()
    cursor = 0
    rows = []
    overlap_samples = 0
    for state in snapshots:
        when = state["t"]
        while cursor < len(replay) and replay[cursor]["ts_ms"] <= when:
            event = replay[cursor]
            cursor += 1
            kind, invocation, workflow = (
                event["kind"], event["invocation_id"], event["workflow_id"],
            )
            if invocation is not None:
                invocation_workflow[invocation] = workflow
            if kind == "workflow_start":
                arrived.add(workflow)
            elif kind == "workflow_end":
                ended_workflows.add(workflow)
            elif kind == "llm_submit":
                model_pending.add(invocation)
            elif kind == "llm_result":
                model_pending.discard(invocation)
            elif kind in ("tool_start", "tool_end"):
                _, key = tool_key(event)
                if kind == "tool_start":
                    if not tools[invocation]:
                        tool_starts[invocation] = event["ts_ms"]
                    tools[invocation].add(key)
                else:
                    if key not in tools[invocation]:
                        raise ValueError("tool end has no corresponding start")
                    tools[invocation].remove(key)
                    if not tools[invocation]:
                        tool_intervals.append({
                            "invocation_id": invocation, "workflow_id": workflow,
                            "start_ms": tool_starts.pop(invocation),
                            "end_ms": event["ts_ms"],
                        })
            elif kind == "join_wait":
                joins[workflow, event["join_id"]] = invocation
            elif kind in ("join_satisfied", "join_timeout"):
                joins.pop((workflow, event["join_id"]), None)
            elif kind == "call":
                foreground[event["target_invocation_id"]] = invocation
            elif kind == "return":
                terminal.add(invocation)
                foreground.pop(invocation, None)
        offset = (when - origin) / 1000
        if not 0 <= offset <= end:
            continue
        active, raw_paused = state["active"], state["paused"]
        if active & raw_paused or not state["paused_pending"] <= raw_paused:
            raise ValueError("native eligibility sets are inconsistent")
        retired = terminal | {
            identity for identity in raw_paused
            if invocation_workflow.get(identity) in ended_workflows
        }
        paused = raw_paused - retired
        tool_blocked = {identity for identity, calls in tools.items() if calls}
        join_blocked = set(joins.values())
        call_blocked = set(foreground.values())
        all_blocked = tool_blocked | join_blocked | call_blocked
        join_paused = paused & join_blocked
        tool_paused = (paused & tool_blocked) - join_paused
        ready_paused = (paused & state["paused_pending"]) - all_blocked
        other = paused - join_paused - tool_paused - ready_paused
        if sum(map(len, (join_paused, tool_paused, ready_paused, other))) != len(paused):
            raise ValueError("paused classification is not a partition")
        overlap = paused & tool_blocked & join_blocked
        overlap_samples += bool(overlap)
        index = bisect_right(physical_times, when) - 1
        pool = physical[index] if index >= 0 else None
        age = (when - pool["t"]) / 1000 if pool else None
        valid_pool = pool is not None and 0 <= age <= args.max_gap_s
        row = {
            "offset_s": offset,
            "monotonic_ms": when,
            "ready_paused": len(ready_paused),
            "join_paused": len(join_paused),
            "tool_paused": len(tool_paused),
            "other_paused": len(other),
            "paused_total": len(paused),
            "terminal_paused": len(raw_paused & retired),
            "tool_blocked_active": len(tool_blocked & active),
            "tool_blocked_total": len(tool_blocked - retired),
            "join_blocked_total": len(join_blocked - retired),
            "join_tool_overlap": len(overlap),
            "active_ready": len((active & model_pending) - all_blocked - retired),
            "active_total": len(active),
            "inflight_workflows": len(arrived - ended_workflows),
            "logical_free_tokens": state["capacity_tokens"] - state["logical_active_tokens"],
            "pool_sample_age_s": age,
            "pool_free_gib": pool["free_units"] * bytes_per_token / GIB if valid_pool else None,
            "pool_evictable_gib": pool["evictable_units"] * bytes_per_token / GIB if valid_pool else None,
            "pool_non_evictable_gib": (
                pool["non_evictable_units"] * bytes_per_token / GIB if valid_pool else None
            ),
            "ready_paused_ids": json.dumps(sorted(ready_paused), separators=(",", ":")),
            "join_paused_ids": json.dumps(sorted(join_paused), separators=(",", ":")),
            "tool_paused_ids": json.dumps(sorted(tool_paused), separators=(",", ":")),
        }
        rows.append(row)
    while cursor < len(replay) and replay[cursor]["ts_ms"] <= origin + end * 1000:
        event = replay[cursor]
        cursor += 1
        if event["kind"] == "tool_end":
            invocation, key = tool_key(event)
            tools[invocation].discard(key)
            if not tools[invocation] and invocation in tool_starts:
                tool_intervals.append({
                    "invocation_id": invocation, "workflow_id": event["workflow_id"],
                    "start_ms": tool_starts.pop(invocation), "end_ms": event["ts_ms"],
                })
    for identity, start in tool_starts.items():
        tool_intervals.append({
            "invocation_id": identity,
            "workflow_id": invocation_workflow[identity], "start_ms": start, "end_ms": None,
        })
    interval_by_invocation = defaultdict(list)
    for interval in tool_intervals:
        interval_by_invocation[interval["invocation_id"]].append(interval)
    opportunity_details = []
    for row in rows:
        if row["pool_free_gib"] is None or row["pool_free_gib"] <= 2:
            continue
        for identity in json.loads(row["tool_paused_ids"]):
            possible = [
                interval for interval in interval_by_invocation[identity]
                if interval["start_ms"] <= row["monotonic_ms"]
                and (interval["end_ms"] is None or interval["end_ms"] > row["monotonic_ms"])
            ]
            if len(possible) != 1:
                raise ValueError("sampled tool wait has no unique observed interval")
            interval = possible[0]
            following = None
            if interval["end_ms"] is not None:
                successor = next((
                    request for request in replay_requests[identity]
                    if request["ts_ms"] >= interval["end_ms"]
                ), None)
                if successor is not None:
                    following = native_by_request.get(successor["attributes"]["request_id"])
            opportunity_details.append({
                "offset_s": row["offset_s"], "invocation_id": identity,
                "tool_wait_start_offset_s": (interval["start_ms"] - origin) / 1000,
                "actual_tool_end_offset_s": (
                    (interval["end_ms"] - origin) / 1000
                    if interval["end_ms"] is not None else None
                ),
                "actual_remaining_tool_s": (
                    (interval["end_ms"] - row["monotonic_ms"]) / 1000
                    if interval["end_ms"] is not None else None
                ),
                "pool_free_gib": row["pool_free_gib"],
                "ready_paused": row["ready_paused"],
                "next_request_id": following["request_id"] if following else None,
                "next_request_native_host_hit_bytes": (
                    following["host_tokens"] * bytes_per_token if following else None
                ),
            })
    args.output_dir.mkdir(parents=True)
    full_summary = summarize(rows, 0., end, args.max_gap_s)
    common_summary = summarize(rows, *window, args.max_gap_s)
    bins = [
        summarize(rows, start, min(start + 300, end), args.max_gap_s)
        for start in range(0, math.ceil(end), 300)
    ]
    write_csv(args.output_dir / "paused_agents_timeline.csv", rows)
    if opportunity_details:
        write_csv(args.output_dir / "tool_paused_free2gib_samples.csv", opportunity_details)
    distinct_waits = {
        (row["invocation_id"], row["tool_wait_start_offset_s"])
        for row in opportunity_details
    }
    next_requests = {
        row["next_request_id"]: row["next_request_native_host_hit_bytes"]
        for row in opportunity_details if row["next_request_id"] is not None
    }
    result = {
        "registration": str(args.registration.resolve()),
        "run_root": str(run), "origin_monotonic_ms": origin,
        "end_offset_s": end, "common_window_s": window,
        "scope": {
            "latest_run_is_oracle": True,
            "fixed_measured_workflows": len(completion["measured_workflow_ids"]),
            "arrived_workflows": len(origin_values),
            "nonmeasured_background_right_censored": True,
            "counts_include_all_arrived_workflows": True,
            "agent_identity": "serial invocation_id",
            "native_eligibility_snapshot_cadence_s": 1,
            "subsecond_capacity_and_eligibility_windows_may_be_missed": True,
            "replay_dependencies_and_native_eligibility_are_not_an_atomic_snapshot": True,
            "blocking_source": "chronologically sorted actual replay tool/JOIN/call boundaries",
            "ready_definition": "native paused_pending minus observed external dependencies",
            "classification_priority": "join, then tool, then ready, then other",
            "terminal_ids_removed": True,
            "capacity_asof_max_age_s": args.max_gap_s,
            "max_integrated_snapshot_gap_s": args.max_gap_s,
            "capacity_does_not_measure_unique_useful_active_KV": True,
            "cooccurrence_does_not_prove_Host_backing_or_missing_KV_or_native_fit": True,
            "future_tool_end_and_host_hits_used_only_for_offline_diagnostics": True,
            "runtime_changed": False, "GPU_action": False,
        },
        "observed_configuration": observed_flags,
        "capacity": capacity, "full_bytes_per_token": bytes_per_token,
        "input_replay_timestamp_inversions_sorted": inversions,
        "snapshot_gap_s": distribution([
            (b["t"] - a["t"]) / 1000 for a, b in zip(snapshots, snapshots[1:])
        ]),
        "wall_minus_monotonic_range_ms": max(clock_offsets) - min(clock_offsets),
        "snapshot_count": len(rows), "pool_sample_count": len(physical),
        "samples_with_join_tool_overlap": overlap_samples,
        "full_execution_window": full_summary,
        "common_window": common_summary,
        "bins_300s": bins,
        "sampled_free2gib_wait_diagnostics": {
            "samples_are_not_independent_calls": True,
            "agent_sample_count": len(opportunity_details),
            "distinct_tool_wait_episodes": len(distinct_waits),
            "distinct_agents": len({row["invocation_id"] for row in opportunity_details}),
            "actual_remaining_tool_s_sample_distribution": distribution([
                row["actual_remaining_tool_s"] for row in opportunity_details
                if row["actual_remaining_tool_s"] is not None
            ]),
            "distinct_later_native_requests": len(next_requests),
            "later_request_native_host_hit_bytes": sum(next_requests.values()),
            "later_host_hits_do_not_prove_bytes_were_already_missing_at_sample": True,
        },
        "predicted_tool_offload_events": dict(action_counts),
        "predicted_tool_offload_cumulative_released_bytes": dict(action_bytes),
        "inputs": inputs.files,
    }
    result["inputs"][str(Path(__file__).resolve())] = {
        "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "bytes": Path(__file__).stat().st_size,
    }
    (args.output_dir / "palette.json").write_text(json.dumps({
        "palette": "Okabe-Ito blue / vermilion / bluish green, with neutral gray",
        "scope": "state categories, not experiment-method colors",
        "colors": COLORS,
        "redundant_encoding": "separate labeled panels; thin snapshots and thick means",
    }, indent=2) + "\n")
    (args.output_dir / "summary.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
    )
    plot(rows, args.output_dir, end, window, stem="paused_agents_timeline")
    plot(rows, args.output_dir, end, window, stem="paused_agents_common_window", xlim=window)
    table = []
    for key in ("ready_paused", "join_paused", "tool_paused"):
        stat = full_summary["agents"][key]
        table.append(
            f"| {key.replace('_', '-')} | {stat['time_weighted_mean']:.3f} | "
            f"{stat['time_weighted_p95']:.0f} | {stat['sampled_peak']} | "
            f"{stat['present_fraction']:.2%} |"
        )
    opportunities = full_summary["cooccurrence_not_verified_prefetch_fit"]
    report = f"""# 最新实验的 paused agent 时间曲线

输入实验：`{run.name}`。分析日期：2026-11-09。

18 个测量 workflow 完整结束；曲线包含全部 {len(origin_values)} 个已到达的
暖机、测量及背景 workflow（冻结计划共 83 个）。
实验在最后一个测量 workflow 结束时停止，其余背景任务按原规则右删失。
这是带未来工作量信息的 oracle 实验，不是可部署预测器的性能结论。

横轴从首批 workflow 到达开始，终点 {end:.3f} s。
灰色区域是冻结共同窗口 {window[0]:.3f}--{window[1]:.3f} s。
约 1 s 的 native 资格快照结合真实工具/JOIN/call 事件分类；
超过 {args.max_gap_s:g} s 的快照间隔不积分，覆盖率 {full_summary['coverage_fraction']:.3%}。
CSV 保留每个采样时刻的分类身份，图中粗线为 15 s 时间加权均值。
资格与客户端依赖事件不是同一次原子快照；约 1 s 的采样会漏掉更短的窗口，
不能把下面的共同出现时长当作预取机会或 JCT 收益的严格上界。

![全部执行窗口](paused_agents_timeline.png)

![冻结共同窗口](paused_agents_common_window.png)

| 类别 | 时间加权平均数量 | 时间加权 P95 数量 | 采样峰值 | 至少一个的时间比例 |
| --- | ---: | ---: | ---: | ---: |
{chr(10).join(table)}

分类互斥：JOIN 优先，之后工具阻塞，再是已登记且依赖就绪的请求。
其他 paused（调用间隙、同步调用阻塞等）另存，不塞入 ready。
已返回的 invocation 和已结束 workflow 不计入等待。
“paused”是逻辑资格，不能推出其 KV 全部位于 Host。

## HBM 与工具预取

HBM 曲线分别给出 allocator 实际空闲容量和可驱逐缓存容量。
它们不是有效 active-KV 利用率；可驱逐页也不直接授予预取权限。
物理容量与资格快照按时间向后匹配，最大陈旧度 {args.max_gap_s:g} s。
全程实际空闲容量均值
{full_summary['diagnostic_capacity']['pool_free_gib']['time_weighted_mean']:.3f} GiB，
中位数 {full_summary['diagnostic_capacity']['pool_free_gib']['time_weighted_p50']:.3f} GiB；
可驱逐缓存均值
{full_summary['diagnostic_capacity']['pool_evictable_gib']['time_weighted_mean']:.3f} GiB。
全程均值包含开始填池阶段；共同窗口的实际空闲容量均值
{common_summary['diagnostic_capacity']['pool_free_gib']['time_weighted_mean']:.3f} GiB。

| 空闲容量诊断门槛 | 与 tool-paused 同时出现 / s | 其中无 ready-paused / s |
| --- | ---: | ---: |
| > 1 GiB | {opportunities['1.0']['tool_paused_and_free_above_gib_s']:.3f} | {opportunities['1.0']['including_no_ready_paused_s']:.3f} |
| > 2 GiB | {opportunities['2.0']['tool_paused_and_free_above_gib_s']:.3f} | {opportunities['2.0']['including_no_ready_paused_s']:.3f} |
| > 4 GiB | {opportunities['4.0']['tool_paused_and_free_above_gib_s']:.3f} | {opportunities['4.0']['including_no_ready_paused_s']:.3f} |
| > 8 GiB | {opportunities['8.0']['tool_paused_and_free_above_gib_s']:.3f} | {opportunities['8.0']['including_no_ready_paused_s']:.3f} |

上述 1/2/4/8 GiB 是离线诊断切片，不是建议增加固定预取阈值。

工具 paused 与空闲容量大于 2 GiB 同时出现
{opportunities['2.0']['tool_paused_and_free_above_gib_s']:.3f} s；
其中没有 ready-paused 的观测时间为
{opportunities['2.0']['including_no_ready_paused_s']:.3f} s。
这只是共同出现，尚未扣除原生输入与 decode 增长预留，也未确认每个
候选的 Host 完整副本、实际缺失页和可复用 checkpoint，不能视为可执行
预取时长或可节省 JCT。

当前观测 `tool_prefetch={observed_flags['tool_prefetch']}`，
`tool_warming_eligibility={observed_flags['tool_warming_eligibility']}`。
现有工具预取限制在 active owner；直接打开开关不能覆盖 tool-paused。
若试验低压力工具 warming，应把物理预取与 paused->active 准入分开，
保留工具未返回时的 blocked/paused，实际后继请求到达后仍按原规则准入。
只能使用扣除原生输入、增长和已承诺恢复后的空闲容量，不能驱逐 ready peer。
条件返回概率和传输成本用于安排有限候选；压力出现时停止新增预取，
允许未消费的缓存按原生安全规则回收。不能读取本轮工具的未来合成时长。

验收需统计边界前实际 H2D、首次服务复用、提前恢复后再次驱逐、
后继请求 TTFT、额外迁移和同步控制成本，再比较完整 workflow JCT。
纯空闲容量模式的机会较短，不能承诺可观的全程 JCT 收益。
若要利用可驱逐缓存，必须另行验证具体页面是已完成 Host 备份、
无活跃依赖、无锁且无在途 DMA 的冷副本；不能直接把整条灰线当作
工具预取预算，也不能为 speculative warming 新建 D2H 腾空间。
本报告只做离线分析，没有更改 serving 代码或启动 GPU 实验。

原始汇总及完整输入哈希见 `summary.json`，逐点数据见
`paused_agents_timeline.csv`。
"""
    (args.output_dir / "report_zh.md").write_text(report)
    print(json.dumps({
        "output_dir": str(args.output_dir),
        "end_s": end, "snapshots": len(rows),
        "means": {
            key: full_summary["agents"][key]["time_weighted_mean"]
            for key in ("ready_paused", "join_paused", "tool_paused")
        },
        "tool_paused_free_gt2gib_s": opportunities["2.0"],
        "coverage": full_summary["coverage_fraction"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
