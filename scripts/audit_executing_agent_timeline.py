#!/usr/bin/env python3
"""Plot native batch service counts on an existing paused-agent analysis clock."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import sys

os.environ.setdefault("MPLBACKEND", "Agg")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.audit_paused_agent_timeline import Inputs, weighted_quantile, write_csv

COLORS = {
    "total": "#0072B2", "prefill": "#009E73",
    "active_ready": "#D55E00", "active_eligibility": "#777777",
}


def integrate(intervals, start, end):
    totals = {
        "duration_s": end - start, "service_s": 0.,
        "total_agent_s": 0., "prefill_agent_s": 0., "decode_agent_s": 0.,
        "prefill_service_s": 0., "decode_service_s": 0.,
    }
    for row in intervals:
        duration = min(row["end_s"], end) - max(row["start_s"], start)
        if duration <= 0:
            continue
        totals["service_s"] += duration
        for phase in ("total", "prefill", "decode"):
            totals[f"{phase}_agent_s"] += row[phase] * duration
        totals["prefill_service_s"] += bool(row["prefill"]) * duration
        totals["decode_service_s"] += bool(row["decode"]) * duration
    for phase in ("total", "prefill", "decode"):
        totals[f"{phase}_wall_mean"] = totals[f"{phase}_agent_s"] / (end - start)
    totals["total_mean_during_recorded_service"] = (
        totals["total_agent_s"] / totals["service_s"] if totals["service_s"] else None
    )
    totals["decode_mean_during_decode_service"] = (
        totals["decode_agent_s"] / totals["decode_service_s"]
        if totals["decode_service_s"] else None
    )
    totals["recorded_service_wall_fraction"] = totals["service_s"] / (end - start)
    return totals


def make_bins(intervals, snapshots, end, seconds):
    rows = [{
        "start_s": start, "end_s": min(start + seconds, end),
        "service_s": 0., "total_agent_s": 0.,
        "prefill_agent_s": 0., "decode_agent_s": 0.,
        "eligibility_observed_s": 0., "active_ready_agent_s": 0.,
        "active_eligibility_agent_s": 0.,
    } for start in range(0, math.ceil(end), seconds)]
    for interval in intervals:
        first = max(0, int(interval["start_s"] // seconds))
        last = min(len(rows) - 1, int(interval["end_s"] // seconds))
        for index in range(first, last + 1):
            row = rows[index]
            duration = min(row["end_s"], interval["end_s"]) - max(
                row["start_s"], interval["start_s"],
            )
            if duration <= 0:
                continue
            row["service_s"] += duration
            for phase in ("total", "prefill", "decode"):
                row[f"{phase}_agent_s"] += interval[phase] * duration
    for left, right in zip(snapshots, snapshots[1:]):
        lo, hi = float(left["offset_s"]), float(right["offset_s"])
        if hi - lo > 2.5:
            continue
        for index in range(int(lo // seconds), min(len(rows) - 1, int(hi // seconds)) + 1):
            row = rows[index]
            duration = min(row["end_s"], hi) - max(row["start_s"], lo)
            if duration <= 0:
                continue
            row["eligibility_observed_s"] += duration
            row["active_ready_agent_s"] += int(left["active_ready"]) * duration
            row["active_eligibility_agent_s"] += int(left["active_total"]) * duration
    for row in rows:
        duration = row["end_s"] - row["start_s"]
        row["midpoint_s"] = (row["start_s"] + row["end_s"]) / 2
        for phase in ("total", "prefill", "decode"):
            row[f"{phase}_wall_mean"] = row[f"{phase}_agent_s"] / duration
        for kind in ("active_ready", "active_eligibility"):
            row[f"{kind}_mean"] = (
                row[f"{kind}_agent_s"] / row["eligibility_observed_s"]
                if row["eligibility_observed_s"] else None
            )
    return rows


def render(rows, output, end, window):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties

    font = FontProperties(fname="/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    plt.rcParams.update({
        "font.family": font.get_name(), "axes.unicode_minus": False,
        "font.size": 11, "axes.spines.top": False, "axes.spines.right": False,
        "pdf.fonttype": 42, "ps.fonttype": 42,
    })
    fig, axes = plt.subplots(2, 1, figsize=(13.5, 8.0), sharex=True)
    fig.subplots_adjust(left=.085, right=.98, top=.80, bottom=.18, hspace=.4)
    times = [row["midpoint_s"] for row in rows]
    prefill = [row["prefill_wall_mean"] for row in rows]
    decode = [row["decode_wall_mean"] for row in rows]
    total = [row["total_wall_mean"] for row in rows]
    axes[0].stackplot(
        times, decode, prefill, colors=[COLORS["total"], COLORS["prefill"]],
        alpha=.25, labels=["decode 服务", "prefill 服务"],
    )
    axes[0].plot(times, total, color=COLORS["total"], linewidth=1.7,
                 label="服务中的 agent 总数")
    axes[0].set_title(
        "实际 batch 服务：prefill / decode 分解（15 s 内按墙钟加权）",
        loc="left", fontsize=12, fontweight="bold", pad=10,
    )
    axes[0].legend(frameon=False, ncol=3, loc="upper right", fontsize=9)
    axes[1].plot(
        times, [row["active_eligibility_mean"] for row in rows],
        color=COLORS["active_eligibility"], linewidth=1.3, linestyle="--",
        label="持有 active 资格",
    )
    axes[1].plot(
        times, [row["active_ready_mean"] for row in rows],
        color=COLORS["active_ready"], linewidth=1.3,
        label="active 且客户端请求未完成",
    )
    axes[1].plot(
        times, total, color=COLORS["total"], linewidth=1.6,
        label="实际 batch 服务",
    )
    axes[1].set_title(
        "资格与执行对照：已提交请求仍可能等待准入、KV 恢复或 prefill",
        loc="left", fontsize=12, fontweight="bold", pad=10,
    )
    axes[1].legend(frameon=False, ncol=3, loc="upper right", fontsize=9)
    for ax in axes:
        ax.set_ylim(bottom=0)
        ax.set_ylabel("平均 agent 数")
        ax.grid(axis="y", alpha=.18)
        ax.axvspan(*window, color="#e5e5e5", alpha=.4, zorder=-5)
    axes[-1].set_xlim(0, end)
    axes[-1].set_xlabel("距首批 workflow 到达的时间 / s")
    fig.suptitle(
        "正在执行的 agent 随时间变化", x=.085, y=.97,
        ha="left", fontsize=19, fontweight="bold",
    )
    fig.text(
        .085, .916,
        "同上一张等待曲线：2026-11-08 tool-band-pressure oracle，70 个已到达 workflow",
        fontsize=10.5, color="#444444",
    )
    fig.text(
        .085, .878,
        "灰色背景为冻结共同窗口；橙线表示外部依赖已解除，但不保证 KV 就绪或已完成 prefill。",
        fontsize=10, color="#666666",
    )
    fig.text(
        .085, .035,
        "服务区间为 scheduler/worker 墙钟范围，包含处理和等待，不能解释为纯 CUDA 内核并发。\n"
        "未记录 batch 服务的间隔按 0 计入墙钟均值；15 s 聚合后的平均数量可为小数。",
        fontsize=9, color="#666666", linespacing=1.7,
    )
    for extension in ("png", "pdf"):
        fig.savefig(output / f"executing_agents_timeline.{extension}", dpi=180)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paused-analysis", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve earlier analysis")
    inputs = Inputs()
    previous = inputs.document(args.paused_analysis / "summary.json")
    run = Path(previous["run_root"])
    status = inputs.document(run / "full/server/native_telemetry_status.json")
    if status["dropped_records"] or status["failed_records"] or status["writer_error"]:
        raise ValueError("native telemetry is incomplete")
    source_csv = args.paused_analysis / "paused_agents_timeline.csv"
    data = source_csv.read_bytes()
    inputs.files[str(source_csv.resolve())] = {
        "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
    }
    with source_csv.open() as stream:
        snapshots = list(csv.DictReader(stream))
    origin = previous["origin_monotonic_ms"]
    end = previous["end_offset_s"]
    window = previous["common_window_s"]
    intervals = []
    count = 0
    last_end = -math.inf
    phases = {}
    unique_agents = set()
    for batch in inputs.records(
        run / "full/server/runtime_audit.jsonl", {"gpu_service_sample"},
    ):
        count += 1
        if batch["sample_id"] != f"native-service-{count:09d}":
            raise ValueError("native service IDs have a gap or duplicate")
        lo = (batch["service_start_monotonic_ms"] - origin) / 1000
        hi = (batch["complete_monotonic_ms"] - origin) / 1000
        if lo < last_end - 1e-7 or hi < lo:
            raise ValueError("native service attribution intervals overlap or reverse")
        last_end = hi
        samples = batch["request_samples"]
        identities = {row["invocation_id"] for row in samples}
        prefill = {row["invocation_id"] for row in samples if row["phase"] == "prefill"}
        decode = {row["invocation_id"] for row in samples if row["phase"] == "decode"}
        if None in identities or prefill & decode or prefill | decode != identities:
            raise ValueError("native batch phase/owner identities are incomplete")
        if len(identities) != batch["batch_size"]:
            raise ValueError("agent count differs from unique serial native requests")
        if hi <= 0 or lo >= end:
            continue
        unique_agents.update(identities)
        phases[batch["phase"]] = phases.get(batch["phase"], 0) + 1
        intervals.append({
            "start_s": max(0., lo), "end_s": min(end, hi),
            "total": len(identities), "prefill": len(prefill), "decode": len(decode),
        })
    if not intervals:
        raise ValueError("no actual model service")
    summaries = {
        "full_execution_window": integrate(intervals, 0., end),
        "common_window": integrate(intervals, *window),
    }
    weights = [
        (row["total"], row["end_s"] - row["start_s"]) for row in intervals
    ]
    summaries["full_execution_window"].update({
        "peak_batch_agents": max(row["total"] for row in intervals),
        "service_time_weighted_p50_agents": weighted_quantile(weights, .5),
        "service_time_weighted_p95_agents": weighted_quantile(weights, .95),
    })
    bins1 = make_bins(intervals, snapshots, end, 1)
    bins15 = make_bins(intervals, snapshots, end, 15)
    for kind in ("active_ready", "active_eligibility"):
        denominator = sum(row["eligibility_observed_s"] for row in bins15)
        summaries["full_execution_window"][f"{kind}_mean"] = sum(
            row[f"{kind}_agent_s"] for row in bins15
        ) / denominator
    args.output_dir.mkdir(parents=True)
    write_csv(args.output_dir / "executing_agents_1s.csv", bins1)
    write_csv(args.output_dir / "executing_agents_15s.csv", bins15)
    write_csv(args.output_dir / "native_batch_intervals.csv", intervals)
    result = {
        "run_root": str(run), "origin_monotonic_ms": origin,
        "end_offset_s": end, "common_window_s": window,
        "native_service_record_count": count,
        "in_window_batch_intervals": len(intervals),
        "in_window_unique_served_agents": len(unique_agents),
        "phase_batch_counts": phases,
        **summaries,
        "scope": {
            "counts_include_all_arrived_workflows": True,
            "same_time_axis_and_termination_as_paused_analysis": True,
            "batch_sample_sequence_contiguous": True,
            "service_intervals_do_not_overlap": True,
            "service_is_scheduler_worker_wall_interval_not_CUDA_kernel_time": True,
            "uncovered_service_intervals_counted_zero_not_claimed_GPU_idle": True,
            "active_ready_is_separate_snapshot_and_replay_observation": True,
            "active_ready_means_active_with_pending_client_request_not_native_KV_readiness": True,
            "GPU_action": False, "serving_code_changed": False,
        },
        "colors": COLORS, "inputs": inputs.files,
    }
    result["inputs"][str(Path(__file__).resolve())] = {
        "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
    )
    render(bins15, args.output_dir, end, window)
    full = summaries["full_execution_window"]
    report = f"""# 执行中的 agent 随时间变化

与前一张等待曲线使用同一实验、到达原点、70 个已到达 workflow 和
{end:.3f} s 的完整测量终点。非测量背景任务按原规则右删失。

![执行中的 agent](executing_agents_timeline.png)

服务数依据 {len(intervals)} 个原生 batch 区间及真实 invocation 身份，
prefill / decode 分开计数。日志服务 ID 连续且无丢失，归因区间不重叠；
该区间是 scheduler/worker 墙钟范围，包含处理与等待，不是纯 CUDA
内核执行时间，也不能将未覆盖间隔直接解释为 GPU 空闲。
图中曲线为 15 s 内按墙钟时间加权，CSV 同时提供 1 s 聚合及原始区间。

| 指标 | agent 数 |
| --- | ---: |
| 全程按墙钟加权的服务数量 | {full['total_wall_mean']:.3f} |
| 仅在记录到模型服务的区间内加权 | {full['total_mean_during_recorded_service']:.3f} |
| decode 服务区间的平均 decode 数量 | {full['decode_mean_during_decode_service']:.3f} |
| 实际 batch 峰值 | {full['peak_batch_agents']} |
| 持有 active 资格的平均数量 | {full['active_eligibility_mean']:.3f} |
| active 且客户端请求未完成的平均数量 | {full['active_ready_mean']:.3f} |

active 资格可以覆盖工具等待和调用间隙，不能用它推断同时执行数量。
active-ready 仅移除了观察到的外部依赖，不保证 KV 就绪、原生物理准入
或 prefill 完成。该名称保留为数据字段，图中文字明确为客户端未完成请求。
资格快照和服务日志不是原子快照，第二幅图仅供观测对照。

逐点数据、输入哈希和统计口径见 CSV 与 `summary.json`。
未改动 serving 代码，未启动 GPU。
"""
    (args.output_dir / "report_zh.md").write_text(report)
    print(json.dumps({
        "output_dir": str(args.output_dir),
        "batch_intervals": len(intervals), "full_execution_window": full,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
