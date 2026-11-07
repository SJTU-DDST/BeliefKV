# 83 项目：ThunderAgent 完整结果与 SGLang 启动

2026-11-07，America/Los_Angeles。保持用户最后确认的 83 项目配置，
复用已完成的非排序 Full、Oracle 和 learned R5，只接续两组基线。
本文记录完整 ThunderAgent 结果和实际 SGLang 启动，不是五方法
全部完成、正式稳态或重复性验收。

## 相同工作与资源

- 83 个不同原项目的完整原始轨迹，20 个初始 workflow，平均 70 秒
  泊松到达；相同固定 18 条测量成员，完整自然尾部。
- Qwen3-Coder-30B-A3B-Instruct、BF16、FULL-only；Device 805198
  token / 79154282496 字节，Host 4069011 token / 400000057344 字节。
- serving `631911fb89b3`，native tree `434c35dd6621`；FA3、
  running/decode24、chunk2048、精确 graph1..24、mixed off。
- 相同原始 token、依赖和工具时间 overlay。共同吞吐窗口为
  55.6927713505--1638.9548502657 秒，包含所有背景输出。
- 各组固定测量成员全部结束后停止背景。NUMA 放置保留诊断，
  不要求跨节点均匀分布或页面计数相等。
- ThunderAgent 保留 invocation program、5 秒控制和 1 秒
  acting-decay 半衰期，不继承 Full 的预测卸载、JOIN/EOS 预取、
  prepare_host 或剩余工作排序。

此前被用户临时切换要求中断的 ThunderAgent 尝试仍保留为行政中断。
本次是新的完整基线；没有把旧部分计时接回，也没有重跑 Full、
Oracle、learned。

## 完整结果

四组固定测量工作一致：2991 个请求、362157 个输出 token，
输入输出身份/哈希和原生停止路径守恒，保留全部正常测量尾部。

| 方法 | 平均 JCT，秒 | P95 JCT，秒 | 平均 JCT 相对 ThunderAgent 降幅 | 共同窗口输出 token/s |
| --- | ---: | ---: | ---: | ---: |
| ThunderAgent acting-decay | 1125.102412 | 3032.987108 | 0 | 328.158558 |
| 非排序 Full | 1061.121917 | 2775.338936 | 5.686638% | 346.998142 |
| Oracle | 1036.098550 | 2720.397967 | 7.910734% | 346.064627 |
| learned R5 | 1049.209646 | 2704.624676 | 6.745410% | 347.541956 |

R5 相比 ThunderAgent，15 条测量 workflow 改善、3 条变差；平均
减少 75.892766 秒，P95 减少 328.362432 秒，共同窗口输出吞吐
提高 5.906717%。三条变差成员为 dashboard、CMS 和 desktop-media；
其中 CMS 回退 192.712370 秒，仍是后续服务顺序检查的重点。

排序自身的比较使用非排序 Full：Oracle 平均 JCT 改善 2.358199%，
R5 改善 1.122611%。整体对 ThunderAgent 的改进不能全部归因于
剩余工作预测或排序，离线 CPU 候选也没有用于这些 GPU 结果。

ThunderAgent 最后测量完成偏移为 4299.885264 秒，背景计划最后
到达为 4598.502355 秒，未覆盖测量尾部为 0 秒。实际到达 79 个，
自然结束 66 个；17 个非测量成员右截尾，其中 4 个未提交。
未用这些条件完成子集替代固定测量组的平均 JCT。

`baseline_restart_20261107_r1/thunderagent_comparison.json` 验证
完整测量工作、两份新鲜空闲快照及实际 owned-process 退出，三个
固定测量性能检查均通过。报告的 `formal_workload_accepted`、
`performance_accepted` 保留为 false：此审计不自动授予正式稳态、
统计显著性或重复性结论。paper 臂也不冒用 Full 专属的
ready-paused/周期授予诊断。

四方法图位于本 campaign 的
`baseline_restart_20261107_r1/four_method_completed_figures/`：
`complete_JCT_and_decode.png`、PDF、CSV 和来源清单均已生成，
PNG 已实际查看。JCT 使用完整固定测量组；decode 趋势与 CDF
使用相同墙钟窗口，CDF 按真实 bin 时长加权，沿用 Okabe-Ito 配色，
没有从单轮构造置信区间。

## 完整服务指标

复用 `audit_measured_request_interactivity.py`；四组均为相同
2991 个完整请求和 18/18 workflow 流式首输出覆盖。
请求 E2E normalized interactivity 为完整请求秒数/token 的 P90
取倒数，包含首次服务等待；workflow TTFT 是从原始到达到任一
descendant 的首个实际流式输出。

| 指标 | ThunderAgent | 非排序 Full | Oracle | learned R5 |
| --- | ---: | ---: | ---: | ---: |
| pooled 请求 E2E normalized interactivity，token/s | 9.833988 | 14.413483 | 14.185364 | 14.899629 |
| 每 workflow 独立计算后等权平均，token/s | 10.906149 | 15.633241 | 15.692796 | 16.157328 |
| workflow 平均 TTFT，秒 | 87.344796 | 74.609520 | 58.784412 | 73.484559 |
| workflow TTFT P90，秒 | 162.206561 | 158.398874 | 91.039642 | 169.310529 |

R5 的平均 TTFT 和请求交互性优于 ThunderAgent，TTFT P90 则更差。
完整平均 JCT 仍是主验收指标；各服务指标独立报告，不能从一种
改善推导所有尾部或所有 workflow 都改善。没有新增 50 token/s SLO。

## 实际 SGLang 回放

ThunderAgent supervisor/server/scheduler/driver/固定测量观察器
均已实际退出后，原 coordinator 自动启动相同 workload 的
`workflow_original83_host400_mean70_native_20261107_r1`，端口 19023。
没有重复资格试验或另启工作负载。

04:17 的 `baseline_restart_20261107_r1/live_native_verified.json`
独立核对：

- 实际唯一 GPU scheduler 为 392898，server 392813、driver
  393784、supervisor 392354；GPU owner 均为 nice0。
- 固定测量观察器 393795 为 nice19；coordinator 370129 存活。
- 实际模型、FA3、MoE 配置文件及 graph 与共享执行声明一致，
  Device/Host 容量精确匹配上述四组。
- `enable_beliefkv_admission=false`，实际无自定义 runtime；
  当前臂使用 native 调度和 KV 路径。
- 首批 15 个已完成请求、636 个输出 token 的源包哈希、身份、
  position sampler 和 native sampler 保留标记匹配。

现场读取器为
`ops/verify_shared_native_execution_live_20261107.py`；它按 native
合同验证实际引擎和原始回放，不要求 Full 的准入快照。证明绑定
源码、注册、独立封闭前缀和实际进程，不是完整 native JCT 或
未经修改的上游 SGLang 证明。

04:20 状态为实际回放、23 个已到达、0/18 测量成员完成，error
为空。CPU 服务指标接续 386508 已完成 ThunderAgent 审计，继续
等待 native 完整对照，随后自动汇总五方法服务指标；原 coordinator
继续生成完整 native 对照及五方法 JCT/decode 图。
SGLang 完整结果和约 1% 排序收益的重复性验证仍未完成，目标保持 active。
