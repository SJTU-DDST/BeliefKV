# BeliefKV P5 restore isolation 正式 v4 运行报告

日期：2026-08-16 至 2026-08-17  
运行目录：`experiments/raw/p5_work_conserving_ab_v1/treatment_restore_isolation/20260816T121102Z`  
代码提交：`66b7fb72e2e5613e7274d94ba08f061d22e20fcf`  
运行配置：`h200_bf16_v4`，Qwen3-Coder-30B-A3B-Instruct BF16，850,000-token KV pool，96 GiB Host pool

## 结论

本轮验证了 ordinary waiting prefix 隔离后的长时间活性，但没有形成足够 HBM 压力，因而没有覆盖 work-conserving replacement 数据面，不能作为正式 offload treatment 或后续 v5 baseline 的配对样本。

- 64 个 root 全部 eager 提交，峰值 32 running / 96 queued；运行 20,761.50 秒。
- 64/64 workflow 发布 `workflow_end`；61 个 outcome 为 completed，3 个因 `APITimeoutError` 为 error。
- 16 个 workflow measurement-valid，36 个 system-JCT eligible，13 个 native-agent-JCT eligible。
- resident pressure P50/P95/P99/Max 为 11.33% / 51.70% / 57.01% / 60.09%；高于 80% 的样本为 0。
- 未生成 running-retraction obligation、`COMMIT_CPU -> ACK -> beneficiary first service` 或策略性 KV transfer。
- ordinary restore debt 触发全局 barrier 为 0；有 backlog 时 running 长期保持 30-32，未复现 0-running convoy。
- shutdown 前 active obligation、lease、funding、command、retraction 和 residency transaction 均为 0，且 shutdown ACK 完整。
- 14 个 durable restore bookkeeping 全部来自 `ordinary_waiting_prefix`；13 个 cancelled、1 个 satisfied。两个 queue-timeout ordinary 记录使旧的 `all_non_user_cancelled_obligations_satisfied` gate 为 false。这不是 orphan，但证明 ordinary miss 仍不应进入 durable debt index。

## Workload 与吞吐

| 指标 | 结果 |
|---|---:|
| Workflow | 64 |
| 动态 subagent | 128 |
| LLM request | 2,809 |
| Tool call | 3,805 |
| Workflow wall-clock | 5.767 h |
| workflow-end/hour | 11.097 |
| completed/hour | 10.577 |
| measurement-valid/hour | 2.774 |
| Natural semantic completions | 75 |
| Guard-intervened completions | 55 |
| Duplicate tool calls suppressed | 57 |

3 个 API timeout workflow 为 `psf__requests-1724`、`pylint-dev__pylint-7080` 和 `pytest-dev__pytest-7521`。它们均只完成 4 次 LLM request、0 次工具调用，约 14.45-14.57 ks 后终止。它们不进入 clean throughput/JCT 数据。

## GPU 与 KV 压力

| 指标 | 结果 |
|---|---:|
| GPU utilization mean | 2.874% |
| GPU utilization P50 / P95 / P99 | 0% / 18% / 29% |
| GPU utilization > 0 比例 | 22.84% |
| GPU utilization >= 10% 比例 | 10.25% |
| Resident pressure mean | 22.10% |
| Resident pressure P95 / Max | 51.70% / 60.09% |
| Peak resident tokens | 510,747 / 850,000 |

低利用率不是本轮 restore convoy 导致：存在 server backlog 时 running 集合没有持续排空。主要原因是 agent 的工具/Join 空窗、单请求小 prefill 和脉冲式 decode，以及 850K pool 下 active contexts 没有形成 HBM 高压。

## KV 迁移与事务

- Native HiCache D2H：6,912 次，231,978,467,328 bytes，submit-to-complete P50/P95 为 177.93/1,441.70 ms。
- Native HiCache H2D：2 次，114,229,248 bytes。
- BeliefKV acknowledged transfer：1,533 次、92,074,967,040 bytes，全部是 `drop_terminal_private`，不是策略性 KV offload。
- `acknowledged_kv_transfer_bytes=0`，`acknowledged_reclamation_bytes=0`。
- 无 pending transaction、orphan command、active lease 或 shutdown cleanup 掩盖的未完成事务。

因此 native write-back 和 terminal private drop 不能被解释为 BeliefKV replacement 成功。

## JointPlan 控制面

| 阶段 | P50 | P95 | P99 |
|---|---:|---:|---:|
| Safe-point delta capture | 16.19 ms | 34.70 ms | 41.40 ms |
| Snapshot delta apply | 364.36 ms | 1,811.34 ms | 2,408.64 ms |
| Snapshot materialization | 137.14 ms | 1,092.95 ms | 1,611.37 ms |
| Snapshot build | 484.06 ms | 2,090.01 ms | 2,972.68 ms |
| Plan compute | 404.64 ms | 1,901.86 ms | 2,568.97 ms |
| Plan validation | 48.74 ms | 171.84 ms | 341.52 ms |
| Plan age | 147.70 ms | 2,493.16 ms | 3,428.92 ms |

最终累计：

- 33,537 次 shadow delta enqueue；
- 31,447 次 stale plan；
- 31,416 次 physical-commit budget exceeded；
- 1,758 次 published；
- 仅 2 次 `current_state_applicable`。

`max_joint_plan_age_ms=100` 与当前 snapshot/plan 开销不相容。下一阶段必须先实现事件驱动 fast path、缩小 delta、惰性物化和 action-local validation，再考虑进程隔离。

## CUDA Graph 覆盖

v4 仅捕获 `[1, 2, 4, 8, 16]`：

- 32-way decode 188 条，全部 `cuda graph: False`；
- 31-way decode 66 条，全部 `cuda graph: False`；
- 全部 decode log 中 490 条 graph=True、285 条 graph=False，false 主要集中在 batch 17-32。

后续建立 `h200_bf16_v5`，将 `cuda_graph_max_bs` 提升为 32，并用短 gate 验证 graph 32 capture、>=1 GiB 稳态余量、31/32-way replay 和数值正确性。v5 的 baseline/treatment 必须共同使用 v5。

## Gate 判定

| Gate | 结果 |
|---|---|
| Ordinary debt global barrier = 0 | 通过 |
| Backlog 下无持续 0-running | 通过 |
| Ordinary 不阻塞 running-retraction obligation | 单测通过；本轮无真实 retraction 覆盖 |
| `COMMIT_CPU -> ACK -> beneficiary first service` | 未覆盖 |
| 无 orphan transaction/lease/command | 通过 |
| 高 HBM pressure | 未通过，峰值 60.09% |
| 可进入 baseline A/B | 否 |

本轮不运行 baseline。下一次性能 A/B 必须在 JointPlan 开销修复和 v5 CUDA Graph gate 通过后重新冻结，并构造能自然达到持续 HBM pressure 的长 context workload。
