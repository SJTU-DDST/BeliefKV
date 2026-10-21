#!/usr/bin/env python3
"""Render a compact Chinese overview from an existing comparison figure bundle."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import html
import json
from pathlib import Path
import shutil


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def capture_transfer_metrics(report: Path, comparison_path: Path, audit_path: Path) -> None:
    comparison_raw = comparison_path.read_bytes()
    audit_raw = audit_path.read_bytes()
    comparison = json.loads(comparison_raw)
    audit = json.loads(audit_raw)["summary"]
    totals = {}
    for arm, run in comparison["runs"].items():
        totals[arm] = {}
        for direction in ("h2d", "d2h"):
            parts = [
                part for name, part in run["transfers"].items()
                if name.endswith(f"_{direction}")
            ]
            totals[arm][direction] = {
                key: sum(part.get(key, 0) for part in parts)
                for key in ("bytes", "kv_bytes", "mamba_bytes")
            }
    controlled = comparison["runs"]["predictive_h2d"]["h2d_sources"]["categories"][
        "predictive"]
    by_source = {
        source: {
            "bytes": audit["by_source"][source]["actual_bytes"],
            "kv_bytes": audit["by_source"][source]["full_transferred_bytes_known"],
        }
        for source in ("join_ticket", "tool_wait")
    }
    for values in by_source.values():
        values["mamba_bytes"] = values["bytes"] - values["kv_bytes"]
    if sum(values["bytes"] for values in by_source.values()) != controlled["bytes"]:
        raise ValueError("JOIN/tool audit bytes disagree with the comparison")
    background = audit["native_full_prepare"]
    explicit = comparison["runs"]["predictive_h2d"]["transfers"]["PREPARE_HOST_d2h"]
    data = {
        "schema_version": 1,
        "scope": "Full-run completed transfers; decimal GB = 10^9 bytes",
        "sources": {
            str(path.resolve()): {"sha256": hashlib.sha256(raw).hexdigest()}
            for path, raw in (
                (comparison_path, comparison_raw), (audit_path, audit_raw),
            )
        },
        "totals": totals,
        "controlled_h2d": {
            key: controlled[key] for key in ("bytes", "kv_bytes", "mamba_bytes")
        },
        "controlled_h2d_by_source": by_source,
        "early_reused_full": {
            source: {
                "events": audit["wait_events"]["by_source"][source][
                    "events_with_early_reused_full"],
                "bytes": audit["wait_events"]["by_source"][source][
                    "early_started_and_reused_full_bytes"],
            }
            for source in ("join_ticket", "tool_wait")
        },
        "prepare_d2h": {
            "background_full_bytes": background["acknowledged_full_bytes"],
            "explicit_full_bytes": explicit["kv_bytes"],
            "explicit_mamba_bytes": explicit.get("mamba_bytes", 0),
            "unreconciled_background_issued_full_bytes": (
                background["issued_full_bytes_known"] - background["acknowledged_full_bytes"]
            ),
        },
        "accounting": {
            "totals": "Each completed payload counted once, including controlled transfers",
            "controlled_h2d": "JOIN/tool source; can include post-completion restoration",
            "early_reused_full": "Started before JOIN/tool completion and verified next-request reuse",
            "prepare_d2h": "Reconciled background/spawn/critical backups plus explicit PREPARE_HOST; already included in total D2H",
        },
    }
    (report / "transfer_metrics.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )


def render_transfer_summary(report: Path) -> str:
    path = report / "transfer_metrics.json"
    if not path.exists():
        return ""
    data = json.loads(path.read_text(encoding="utf-8"))
    totals = data["totals"]
    rows = []
    csv_rows = []
    for direction in ("h2d", "d2h"):
        native = totals["native"][direction]
        predictive = totals["predictive_h2d"][direction]
        delta = (predictive["bytes"] / native["bytes"] - 1) * 100
        cells = []
        for values in (native, predictive):
            cells.append(
                f"<td>{values['bytes'] / 1e9:,.3f}"
                f"<small>FULL {values['kv_bytes'] / 1e9:,.3f} · "
                f"Mamba {values['mamba_bytes'] / 1e9:,.3f}</small></td>"
            )
        rows.append(
            f"<tr><th>{direction.upper()} 总量</th>{''.join(cells)}"
            f"<td>{delta:+.2f}%</td></tr>"
        )
        for arm, values in (("native", native), ("predictive_h2d", predictive)):
            csv_rows.append({
                "metric": f"total_{direction}", "arm": arm, **values,
            })
    prepare = data["prepare_d2h"]
    background = prepare["background_full_bytes"]
    explicit = prepare["explicit_full_bytes"]
    explicit_mamba = prepare["explicit_mamba_bytes"]
    breakdown = []
    details = (
        ("controlled_h2d", "JOIN/tool H2D 下发合计", data["controlled_h2d"]),
        ("join_h2d", "其中：JOIN H2D", data["controlled_h2d_by_source"]["join_ticket"]),
        ("tool_h2d", "其中：tool H2D", data["controlled_h2d_by_source"]["tool_wait"]),
        ("prepare_d2h", "提前备份 D2H 合计", {
            "bytes": background + explicit + explicit_mamba,
            "kv_bytes": background + explicit, "mamba_bytes": explicit_mamba,
        }),
        ("background_d2h", "其中：后台 / spawn / 容量备份", {
            "bytes": background, "kv_bytes": background, "mamba_bytes": 0,
        }),
        ("explicit_prepare_d2h", "其中：显式 PREPARE_HOST", {
            "bytes": explicit + explicit_mamba,
            "kv_bytes": explicit, "mamba_bytes": explicit_mamba,
        }),
    )
    for key, label, values in details:
        breakdown.append(
            f"<tr><th>{label}</th><td>{values['kv_bytes'] / 1e9:,.3f}</td>"
            f"<td>{values['mamba_bytes'] / 1e9:,.3f}</td>"
            f"<td>{values['bytes'] / 1e9:,.3f}</td></tr>"
        )
        csv_rows.append({"metric": key, "arm": "predictive_h2d", **values})
    early = data["early_reused_full"]
    join, tool = early["join_ticket"], early["tool_wait"]
    for source, values in early.items():
        csv_rows.append({
            "metric": f"{source}_early_reused_full", "arm": "predictive_h2d",
            "bytes": values["bytes"], "kv_bytes": values["bytes"],
            "mamba_bytes": "", "events": values["events"],
        })
    with (report / "transfer_metrics.csv").open(
        "w", newline="", encoding="utf-8",
    ) as stream:
        writer = csv.DictWriter(
            stream, fieldnames=("metric", "arm", "bytes", "kv_bytes", "mamba_bytes", "events"),
        )
        writer.writeheader()
        writer.writerows(csv_rows)
    return f"""
<div class="section-heading"><h2>KV 迁移与提前传输</h2>
<a href="transfer_metrics.csv">传输 CSV</a></div>
<table aria-label="H2D 与 D2H 总传输量">
<thead><tr><th>全程传输量（GB）</th><th>V103 native</th>
<th>V108 predictive</th><th>变化</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<table aria-label="预测 H2D 与提前备份 D2H 分项">
<thead><tr><th>V108 分项（GB）</th><th>FULL KV</th><th>Mamba</th><th>合计</th></tr></thead>
<tbody>{''.join(breakdown)}</tbody></table>
<p class="finding"><strong>完成前启动、且被下一请求确认复用的 FULL KV：</strong>
JOIN {join['events']} 次 / {join['bytes'] / 1e9:.3f} GB，
tool {tool['events']} 次 / {tool['bytes'] / 1e9:.3f} GB，
合计 {(join['bytes'] + tool['bytes']) / 1e9:.3f} GB。次数按独立 JOIN/tool 等待事件统计。</p>
<p class="scope">所有容量均取全程已完成传输，1 GB = 10⁹ 字节。
JOIN/tool 下发量包含完成后的恢复；完成前预取另按实际启动与首次复用证据确认。
提前备份 D2H 已包含在总 D2H 中，不能再次相加；其中后台备份沿用原生 D2H 记账。
有 {prepare['unreconciled_background_issued_full_bytes'] / 1e9:.3f} GB 后台下发量无法唯一匹配 ACK，
未计入已归因的提前备份量。</p>
"""


def render_overview(report: Path, output: Path) -> int:
    metrics = {row["metric"]: row for row in read_csv(report / "summary_metrics.csv")}
    pairs = read_csv(report / "workflow_pairs.csv")
    events = read_csv(report / "workflow_events.csv")
    provenance = json.loads((report / "provenance.json").read_text(encoding="utf-8"))
    native, predictive = "native", "predictive_h2d"
    faster = sum(float(row["jct_delta_s"]) < 0 for row in pairs)
    slower = sum(float(row["jct_delta_s"]) > 0 for row in pairs)
    count = len(pairs)
    window = provenance["common_window_policy"]
    begin, end = window["warmup_seconds"] / 60, window["common_cutoff_seconds"] / 60
    last = {
        arm: max(float(row["complete_s"]) for row in events if row["arm"] == arm) / 60
        for arm in (native, predictive)
    }

    def change(key: str) -> str:
        return f"{float(metrics[key]['predictive_change_pct']):+.2f}%"

    table = []
    for key, label, divisor, unit in (
        ("llm_requests", "LLM 请求数", 1, "次"),
        ("tool_calls", "工具调用数", 1, "次"),
        ("output_tokens", "输出 token", 1, "token"),
        ("jct_mean_seconds", "平均 JCT", 60, "min"),
        ("jct_p50_seconds", "JCT 中位数", 60, "min"),
        ("jct_p95_seconds", "JCT P95", 60, "min"),
        ("common_window_decode_tokens_per_s", "共同窗口 decode 吞吐", 1, "token/s"),
        ("server_request_latency_p50_s", "服务端请求时延 P50", 1, "s"),
        ("server_request_latency_p95_s", "服务端请求时延 P95", 1, "s"),
    ):
        row = metrics[key]
        number_format = ",.0f" if unit in ("次", "token") else ",.2f"
        table.append(
            f"<tr><th>{label}</th>"
            f"<td>{float(row['native']) / divisor:{number_format}} {unit}</td>"
            f"<td>{float(row['predictive']) / divisor:{number_format}} {unit}</td>"
            f"<td>{change(key)}</td></tr>"
        )

    figures = (
        (
            "jct_distribution", "JCT 分布与同任务配对",
            "左图为完整 JCT 的累积分布，同一耗时下曲线越高，完成比例越大。"
            "右图每个灰点对应同一任务，点在对角线下方表示 predictive 更快。",
            f"平均 JCT {change('jct_mean_seconds')}，中位数 "
            f"{change('jct_p50_seconds')}；{faster} 个任务更快、{slower} 个更慢，"
            "分布存在交叉。",
        ),
        (
            "workflow_progress", "Workflow 完成进度与完成吞吐",
            "左图为实际累计完成数；右图为每 5 分钟的完成吞吐。"
            "不足 5 分钟的末桶不画速率，其完成事件仍全部保留在左图。",
            f"最后一个 workflow 的结束时刻：predictive {last[predictive]:.2f} min，"
            f"native {last[native]:.2f} min；时间以各侧首次 GPU 采样为原点。",
        ),
        (
            "system_timeseries", "吞吐、队列与 GPU 运行状态",
            "每 60 秒固定分桶，无额外平滑。四个面板分别显示 decode token/s、"
            "等待请求数、GPU 利用率和 running 请求数。",
            f"共同窗口为 {begin:.2f}–{end:.2f} min，decode 吞吐 "
            f"{change('common_window_decode_tokens_per_s')}。灰区为预热及共同尾部排除区；"
            "running 数并非时间加权 decode batch，GPU 利用率也不等同于算力效率。",
        ),
        (
            "request_latency", "服务端请求时延",
            "时延取原生 llm_submit 到 llm_result，含排队与模型服务，"
            "不含请求前的客户端构造和请求间工具执行。左图横轴为对数刻度；"
            "右图按完成时间分桶，圆点为 P50、三角为 P95。",
            f"请求 P50 {change('server_request_latency_p50_s')}，"
            f"P95 {change('server_request_latency_p95_s')}，并未全面下降。"
            "服务端配对记录数与客户端逻辑调用数采用不同口径；这里不是 TTFT。",
        ),
    )
    sections = []
    navigation = []
    image_hashes = {}
    for index, (name, title, caption, finding) in enumerate(figures, 1):
        raw = (report / f"{name}.png").read_bytes()
        image_hashes[name] = hashlib.sha256(raw).hexdigest()
        image = base64.b64encode(raw).decode("ascii")
        navigation.append(f'<a href="#{name}">{index}. {title}</a>')
        sections.append(
            f'<section id="{name}"><div class="section-heading">'
            f'<h2>{index}. {title}</h2><a href="{name}.pdf">矢量 PDF</a></div>'
            f'<figure><img src="data:image/png;base64,{image}" alt="{title}" '
            f'loading="lazy" decoding="async"><figcaption>{html.escape(caption)}</figcaption>'
            f'</figure><p class="finding">{html.escape(finding)}</p></section>'
        )
    seeds = provenance["validation"]
    geometry = provenance.get("scope_of_context_diagnostic")
    context = ""
    if geometry:
        context = (
            f"另在 {geometry['start_seconds'] / 60:g}–"
            f"{geometry['end_seconds'] / 60:g} min 窗口，平均可见上下文为 "
            f"{geometry['native_context_tokens_mean']:,.0f} / "
            f"{geometry['predictive_context_tokens_mean']:,.0f} token"
            "（native / predictive）；该统计不代表全程。"
        )
    metadata = {
        "summary_metrics_sha256": hashlib.sha256(
            (report / "summary_metrics.csv").read_bytes()).hexdigest(),
        "renderer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "image_sha256": image_hashes,
    }
    transfers = render_transfer_summary(report)
    if transfers:
        metadata["transfer_metrics_sha256"] = hashlib.sha256(
            (report / "transfer_metrics.json").read_bytes()).hexdigest()
    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>V108 predictive 与 V103 native：图表总览</title>
<style>
*{{box-sizing:border-box}}
body{{margin:28px auto;padding:0 20px;max-width:1080px;color:#202124;background:#fff;
font:15px/1.65 system-ui,-apple-system,"Noto Sans CJK SC","Microsoft YaHei",sans-serif;letter-spacing:0}}
h1{{font-size:25px;line-height:1.35;margin:0 0 12px}}
h2{{font-size:19px;line-height:1.45;margin:0}}
a{{color:#07689f;text-underline-offset:3px}}
p{{margin:10px 0}}
.intro,.scope,figcaption{{color:#545b64}}
.legend{{display:flex;flex-wrap:wrap;gap:8px 24px;margin:12px 0}}
.legend span{{display:inline-flex;align-items:center;gap:8px}}
.line{{width:28px;height:0;border-top:3px solid #d55e00}}
.line.native{{border-top:3px dashed #0072b2}}
nav{{display:flex;flex-wrap:wrap;gap:8px 20px;padding:14px 0;border-top:1px solid #dce0e4;
border-bottom:1px solid #dce0e4;font-size:13px}}
table{{width:100%;border-collapse:collapse;margin:18px 0;font-variant-numeric:tabular-nums}}
th,td{{padding:8px 10px;border-bottom:1px solid #e2e5e9;text-align:right}}
th:first-child{{text-align:left}}
tbody th{{font-weight:400}}
td small{{display:block;font-size:11px;color:#545b64;line-height:1.5}}
thead{{font-size:13px;color:#545b64}}
section{{padding:22px 0 16px;border-top:1px solid #dce0e4;scroll-margin-top:16px}}
.section-heading{{display:flex;align-items:baseline;justify-content:space-between;gap:12px}}
.section-heading a{{font-size:13px;flex-shrink:0}}
figure{{margin:12px 0 0}}
img{{display:block;width:100%;height:auto}}
figcaption,.finding{{font-size:13px;line-height:1.7;margin:8px 0}}
.scope{{font-size:13px;margin:12px 0 20px}}
details{{padding:14px 0;border-top:1px solid #dce0e4;font-size:13px}}
summary{{cursor:pointer;font-weight:600}}
footer{{padding:14px 0;font-size:13px;color:#545b64}}
@media(max-width:600px){{
body{{margin:20px auto;padding:0 12px;font-size:14px}}h1{{font-size:21px}}
h2{{font-size:17px}}th,td{{padding:7px 4px;font-size:12px}}
.section-heading{{align-items:flex-start}}nav{{gap:7px 14px}}
}}
@media print{{body{{max-width:none;margin:0}}nav,.section-heading a{{display:none}}
section{{break-inside:avoid}}details{{display:block}}}}
</style>
</head>
<body>
<header>
<h1>V108 predictive 与 V103 native</h1>
<p class="intro">同一组 {count} 个任务，两侧均完成 {count}/{count}。
以下为单轮历史对照的实际观测，完整 JCT 不筛除任务、不截尾。</p>
<div class="legend"><span><i class="line native" aria-hidden="true"></i>V103 native</span>
<span><i class="line" aria-hidden="true"></i>V108 predictive</span></div>
</header>
<nav aria-label="图表目录">{''.join(navigation)}</nav>
<table aria-label="主要性能与工作量指标">
<thead><tr><th>指标</th><th>V103 native</th><th>V108 predictive</th><th>变化</th></tr></thead>
<tbody>{''.join(table)}</tbody>
</table>
<p class="scope">吞吐取共同窗口 {begin:.2f}–{end:.2f} min，其余表项取全程。
每侧仅一轮实验，未绘制跨实验置信区间；这些时序不是跨负载的吞吐—延迟曲线。</p>
{transfers}
<main>{''.join(sections)}</main>
<details>
<summary>统计口径与对照限制</summary>
<p>JCT 使用客户端完整记录；完成进度使用真实 workflow 结束事件。
时序采用固定非重叠分桶，不完整末桶不画速率。P50/P95 采用线性插值。
请求记录彼此相关，不视为独立重复实验。</p>
<p>两侧代码、引擎与实际执行轨迹不同。服务端随机种子为
{seeds[native]['server_random_seed']} / {seeds[predictive]['server_random_seed']}
（native / predictive）。相同任务不等于相同计算需求，观测优势不能全部归因于预测迁移。</p>
<p>{context}</p>
</details>
<footer>
<a href="index.html">完整英文图注</a> ·
<a href="summary_metrics.csv">指标 CSV</a> ·
{('<a href="transfer_metrics.json">迁移来源与口径</a> ·' if transfers else '')}
<a href="workflow_pairs.csv">逐任务 CSV</a> ·
<a href="provenance.json">来源与校验</a>
</footer>
<!-- source-metadata: {html.escape(json.dumps(metadata, ensure_ascii=True))} -->
</body>
</html>
"""
    output.write_text(document, encoding="utf-8")
    source_copy = report / Path(__file__).name
    if source_copy.resolve() != Path(__file__).resolve():
        shutil.copyfile(__file__, source_copy)
    return len(figures)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--comparison", type=Path)
    parser.add_argument("--prefetch-audit", type=Path)
    args = parser.parse_args()
    report = args.report_dir.resolve()
    if bool(args.comparison) != bool(args.prefetch_audit):
        parser.error("--comparison and --prefetch-audit must be provided together")
    if args.comparison:
        capture_transfer_metrics(
            report, args.comparison.resolve(), args.prefetch_audit.resolve(),
        )
    output = args.output.resolve() if args.output else report / "index_zh.html"
    figure_count = render_overview(report, output)
    print(json.dumps({
        "output": str(output), "embedded_figures": figure_count,
        "bytes": output.stat().st_size,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
