# P6 PREPARE CPU-Budget Canary

日期：2026-09-11

## 结论

本轮通过高压、控制面和 shutdown 正确性门槛，但没有自然正收益 PREPARE，因此没有
进入修复后的 thread-CPU commit budget 分支。它不能验证物理 canary，也不能解释为
物理提交失败；正确结论是本次随机 agent trajectory 的策略价值门槛未通过。

7 个冻结高压快照离线重放后仍全部选择 observed baseline。主要原因是 beneficiary
没有形成可归因的 HBM block、预测 pressure 不早于 victim reentry，或 shadow copy
来不及在 pressure 前完成；其中 1 个候选的 transfer shape 不受当前 artifact 支持。

## 冻结配置

- 运行目录：
  `experiments/canary/p6_prepare_host_cpu_budget/20260911T123605Z`
- 代码：`be605bb perf(p6): preserve timely predictive commits`
- GPU/模型：H200 NVL GPU0，Qwen3-Coder-30B BF16
- Runtime profile：`h200_bf16_v6`
- HBM KV pool：850K tokens；Host KV pool：96 GiB
- Workload：冻结 64-root `native_subagent_2to3`，all-roots eager
- 策略：P5 observed JointPlan + P6 predictive overlay
- 权限：单笔 `PREPARE_HOST`；COMMIT/PREFETCH/predictive retraction 关闭

HBM 峰值为 99.98%，80% 以上持续约 14.5 分钟，峰值 migratable KV 约 77.4 GiB。
因此本轮没有正收益动作不能归因于压力不足。

## 在线漏斗

| 指标 | 结果 |
| --- | ---: |
| Predictive worker submitted/evaluated | 101 / 98 |
| worker failed/dropped/pending | 0 / 0 / 0 |
| action certificate fresh/stale | 75 / 23 |
| victim overlay | 214 |
| near-HBM-risk / slot-then-HBM-blocked | 50 / 48 |
| selected observed baseline | 98 |
| fresh positive before latest-start | 0 |
| semantic intent / physical command | 0 / 0 |

高压阶段持久化了 7 个按 action signature 去重的 snapshot。离线 morphology-aware replay
得到 7 个 PREPARE 候选、0 个正收益、0 个 eligible，failure 归因为：

- `projected_beneficiary_hbm_block_unavailable`：11 个 scenario；
- `pressure_not_before_parent_reentry`：10 个 scenario；
- `shape_unsupported`：4 个 scenario；
- `shadow_completes_after_pressure`：2 个 scenario；
- `insufficient_exclusive_reclaim`：1 个 scenario。

以上计数是 scenario 级，可在同一候选中同时出现。7 个候选的 expected benefit 均为负，
范围约 -17.87 至 -2.76 ms。

## 控制面

| 路径 | P50 | P95 | P99 |
| --- | ---: | ---: | ---: |
| safe-point delta capture | 0.261 ms | 0.755 ms | 1.530 ms |
| action-local overlay capture | 0.002 ms | 0.550 ms | 1.129 ms |
| predictive submit | 0.100 ms | 5.422 ms | 10.344 ms |
| belief compose | 18.77 ms | 27.41 ms | 37.11 ms |
| scenario risk | 13.77 ms | 19.32 ms | 28.18 ms |
| predictive total | 36.72 ms | 49.45 ms | 62.89 ms |
| action certificate validation | 0.127 ms | 0.312 ms | 0.593 ms |
| trigger to validation | 475 ms | 2254 ms | 6790 ms |

异步 risk compute 已保持在 50 ms P95 左右；同步 capture 和 submit 的尾部仍高于
0.5/1 ms 目标，但没有 worker backlog。13 次稀有 observed full plan 的 snapshot build
P95 为 227.92 ms，不能与日常 fast path 混为同一分布。

`predictive_safe_point_commit` 没有有效动作样本，因此本轮没有测到
`validation_wall_ms`、`validation_cpu_ms` 或 transfer-estimate cache 对真实提交的收益。

## 正确性与收尾

最终 `shutdown_state=acknowledged`，所有 command、lease、reservation、obligation 和
transaction 均为空；`shutdown_cleanup_did_not_mask_unresolved_transactions=true`。
PageIndex、RCCG、worker 和 transfer 路径无异常。

Workload driver 在第一次 SIGINT 后等待 ThreadPoolExecutor worker，随后使用 TERM 结束；
SGLang 仍通过显式 shutdown prepare/drain/ACK 完成收尾。该轮主动停止，不用于 workflow
JCT 或任务正确性结论。

## 裁决

1. `be605bb` 的策略前置路径没有回归：高压下 risk worker、overlay 和证书均正常。
2. 本轮没有自然 positive intent，因此 thread-CPU budget 与物理 D2H/ACK 仍待验证。
3. 不放宽收益阈值，也不把高 HBM 本身当作收益。下一次物理机制验证可使用确定性单动作
   gate；自然策略结论仍必须来自 planner 自然选中的 positive package。
4. 本轮未通过物理动作门槛，不生成 KV 时间线。

