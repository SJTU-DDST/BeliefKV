#!/usr/bin/env python3
"""Locate the observed active-pending/service count difference by native phase."""

from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.audit_executing_agent_timeline import render


def read_csv(path, *, numeric=False):
    with path.open() as stream:
        rows = list(csv.DictReader(stream))
    return [
        {key: float(value) if value else None for key, value in row.items()}
        for row in rows
    ] if numeric else rows


def phase_gap(intervals, states, start, end):
    ends = [row["end_s"] for row in intervals]
    stats = defaultdict(lambda: defaultdict(float))
    for left, right in zip(states, states[1:]):
        lo, hi = max(start, float(left["offset_s"])), min(end, float(right["offset_s"]))
        if hi <= lo or float(right["offset_s"]) - float(left["offset_s"]) > 2.5:
            continue
        index = bisect_right(ends, lo)
        covered = 0.
        while index < len(intervals) and intervals[index]["start_s"] < hi:
            interval = intervals[index]
            duration = min(hi, interval["end_s"]) - max(lo, interval["start_s"])
            if duration > 0:
                if interval["prefill"] and interval["decode"]:
                    raise ValueError("this decomposition requires the observed unmixed run")
                phase = "prefill" if interval["prefill"] else "decode"
                stats[phase]["duration_s"] += duration
                stats[phase]["active_pending_agent_s"] += int(left["active_ready"]) * duration
                stats[phase]["served_agent_s"] += interval["total"] * duration
                stats[phase]["single_agent_s"] += (interval["total"] == 1) * duration
                covered += duration
            index += 1
        gap = hi - lo - covered
        if gap < -1e-7:
            raise ValueError("overlapping service intervals")
        stats["no_recorded_service"]["duration_s"] += max(0., gap)
        stats["no_recorded_service"]["active_pending_agent_s"] += (
            int(left["active_ready"]) * max(0., gap)
        )
    covered = sum(item["duration_s"] for item in stats.values())
    difference = sum(
        item["active_pending_agent_s"] - item.get("served_agent_s", 0)
        for item in stats.values()
    )
    for item in stats.values():
        duration = item["duration_s"]
        item["active_pending_mean"] = item["active_pending_agent_s"] / duration if duration else None
        item["served_mean"] = item.get("served_agent_s", 0) / duration if duration else None
        item["duration_fraction"] = duration / covered
        item["difference_agent_s"] = item["active_pending_agent_s"] - item.get("served_agent_s", 0)
        item["fraction_of_observed_difference"] = (
            item["difference_agent_s"] / difference if difference else None
        )
        item["single_agent_duration_fraction"] = (
            item.get("single_agent_s", 0) / duration if duration else None
        )
    return {
        "covered_s": covered, "difference_agent_s": difference, "per_phase": dict(stats),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-analysis", type=Path, required=True)
    parser.add_argument("--paused-analysis", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve earlier evidence")
    source_summary = args.execution_analysis / "summary.json"
    summary = json.loads(source_summary.read_bytes())
    interval_path = args.execution_analysis / "native_batch_intervals.csv"
    state_path = args.paused_analysis / "paused_agents_timeline.csv"
    intervals = read_csv(interval_path, numeric=True)
    states = read_csv(state_path)
    server_path = Path(summary["run_root"]) / "full/server/server_info.json"
    server = json.loads(server_path.read_bytes())
    config = server.get("server_args", server)
    if config["enable_mixed_chunk"] is not False:
        raise ValueError("expected the frozen unmixed experiment")
    result = {
        "run_root": summary["run_root"],
        "full_execution_window": phase_gap(intervals, states, 0, summary["end_offset_s"]),
        "common_window": phase_gap(intervals, states, *summary["common_window_s"]),
        "configuration": {
            key: config[key] for key in (
                "chunked_prefill_size", "enable_mixed_chunk", "max_running_requests",
                "max_prefill_tokens", "prefill_decode_interval", "schedule_policy",
            )
        },
        "scope": {
            "active_ready_legacy_field": "active eligibility plus pending client model request, external dependencies cleared",
            "does_not_imply_KV_resident_native_admission_or_prefill_complete": True,
            "service_is_scheduler_worker_wall_interval_not_exclusive_GPU_kernel_time": True,
            "eligibility_snapshot_and_service_intervals_not_atomic": True,
            "difference_is_agent_time_not_JCT_or_avoidable_stall": True,
            "phase_cooccurrence_not_causal_speedup_estimate": True,
            "global_evictable_KV_owner_state_not_available_from_pool_counters": True,
            "GPU_action": False, "serving_changed": False,
        },
    }
    expected = sum(
        int(left["active_ready"]) * (
            float(right["offset_s"]) - float(left["offset_s"])
        ) for left, right in zip(states, states[1:])
        if float(right["offset_s"]) - float(left["offset_s"]) <= 2.5
    )
    reconstructed = sum(
        item["active_pending_agent_s"]
        for item in result["full_execution_window"]["per_phase"].values()
    )
    if not math.isclose(expected, reconstructed, abs_tol=1e-7):
        raise ValueError("phase split lost active-pending agent-time")
    bins_path = args.execution_analysis / "executing_agents_15s.csv"
    input_paths = (
        source_summary, interval_path, state_path, server_path, bins_path, Path(__file__),
        ROOT / "scripts/audit_executing_agent_timeline.py",
    )
    result["inputs_sha256"] = {
        str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in input_paths
    }
    args.output_dir.mkdir(parents=True)
    (args.output_dir / "summary.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
    )
    render(
        read_csv(bins_path, numeric=True), args.output_dir,
        summary["end_offset_s"], summary["common_window_s"],
    )
    phases = result["full_execution_window"]["per_phase"]
    table = "\n".join(
        f"| {name} | {item['duration_s']:.3f} | {item['duration_fraction']:.2%} | "
        f"{item['active_pending_mean']:.3f} | {item['served_mean']:.3f} | "
        f"{item['fraction_of_observed_difference']:.2%} |"
        for name, item in phases.items()
    )
    report = f"""# active 未完成请求与 batch 服务差距

数据沿用 2026-11-08 tool-band-pressure oracle 的完整测量窗口，
70 个已到达 workflow，18 个测量 workflow 完整结束。没有更改 serving。

## 口径修正

上一张图的橙线 active-ready 表示 agent 持有 active 资格、客户端模型
请求已提交且尚未完成，并且没有观察到工具/JOIN/call 外部阻塞。
它仍可等待 native ingress、物理准入、KV 恢复、prefill 或下一次 decode。
将它直接标作“模型请求已就绪”容易被理解为已可立即执行，现修正图中文字。

![标签修正后的执行曲线](executing_agents_timeline.png)

## 阶段对齐

约 1 s 的资格快照按观察状态保持到下一次快照，再与原生逐 batch 服务
区间相交。下表是数量差的时间积分分布，不是 JCT、纯 GPU 空闲、
可节省时长或某项机制的因果贡献。

| 原生阶段 | 时间 / s | 覆盖时间占比 | active 未完成请求均值 | batch 服务均值 | 数量差积分占比 |
| --- | ---: | ---: | ---: | ---: | ---: |
{table}

本轮 chunked_prefill_size=2048，mixed chunk 关闭，prefill_decode_interval=0，
max_running_requests=24，原生队列策略 fcfs。冻结调度器优先执行可构造的
prefill batch，仅在没有新 prefill batch 时执行 running decode；
mixed 关闭时不会将已有 decode 请求混入该 prefill batch。
prefill 服务区间 {phases['prefill']['single_agent_duration_fraction']:.2%} 的时间只有
一个 agent，因而“许多 pending 请求、当前 batch 只服务少量对象”会持续出现。
单请求 prefill 可以包含 2048 个输入 token，agent 数少不能证明 GPU 计算不足。

decode 区间的差距还可能涉及未完成的 prefill、入队/恢复/容量等待及
资格快照误差。本分析尚未逐请求将这些原因分开，不能全部归给 KV。

## 可驱逐 KV 的状态边界

先前 25.34 GiB 来自 native full_evictable_size 聚合计数，没有逐页 owner
状态归因；不是已确认可被工具预取替换的冷 agent KV 总量。
全局可驱逐状态与 workflow 是否 ready 是两个维度。当前主动冷回收
私有候选检查真实 WAIT_JOIN 或预测长 WAIT_TOOL，要求已完成 Host 备份、
无锁、无在途 DMA、身份匹配，并保护当前恢复；无引用且有 Host 副本的
叶子由单独路径检查。不能给整个 25.34 GiB 虚构这些状态的占比。

将两个都长期等待工具、近期都不会消费的 agent KV 互换，不增加可执行
工作，也可能增加迁移。替换只有在新对象近期确实能被执行消费、且收益
超过被替换对象损失和迁移成本时才有价值。纯空闲页预取不需要额外驱逐；
不能为了提高占用率而做 tool-wait 到 tool-wait 的循环替换。

完整数字与输入哈希见 summary.json。
"""
    (args.output_dir / "report_zh.md").write_text(report)
    print(json.dumps({
        "output_dir": str(args.output_dir),
        "full_execution_window": result["full_execution_window"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
