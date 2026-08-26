# H200 Frozen-Demand GPU Oracle O0/O3 对比

日期：2026-08-26

状态：一组有效的 O0/O3 配对运行已完成。两臂使用完全相同的冻结需求，全部请求和
workflow 正常完成，控制面正确性门禁通过。当前有限动作空间的 O3 候选没有产生正
收益；若把 O0 no-op 作为候选，Oracle 下界选择 O0，收益为 0%。

## 1. 实验问题

本轮只回答：在冻结同一批 native-subagent demand 后，当前实现的 perfect-future
execution + KV 联合候选能否优于 observed-state O0。

本轮不是完整统计实验，也不是全局最优 Oracle。O3 只在当前实现的有限 execution/KV
动作空间中产生一个在线候选，因此负结果只能否定该候选，不能证明不存在更优调度。

## 2. 冻结契约

- 模型：Qwen3-Coder-30B BF16。
- GPU：单张 H200。
- profile：`configs/p6/h200_bf16_v6/frozen_runtime_profile.json`。
- KV pool：850K tokens；Host pool：96 GiB；max running：32；CUDA Graph：32。
- truth：18 workflow、54 invocation、18 JOIN、1,208 个真实 LLM call、1,651 个
  tool wait。
- token demand：20,119,906 prompt tokens、151,228 output tokens。
- truth digest：`6dd9bc6f28d5f24b2ce630033cd1cf3bd2fd63370082de515596e3a89339bc38`。
- 两臂共享完全相同的 root arrival、SPAWN/RETURN/JOIN、tool wait、prompt/output
  tokens 和 parent reentry。

冻结输入位于：

- `experiments/oracle/gpu_replay_native18_v1/frozen_agent_demand_v2.json`
- `experiments/oracle/gpu_replay_native18_v1/frozen_physical_sidecar_v1.json.gz`

## 3. 有效运行

| 指标 | O0 Current | O3 Joint candidate | O3 相对 O0 |
|---|---:|---:|---:|
| 完成 workflow | 18/18 | 18/18 | 相同 |
| 完成 request | 1,208/1,208 | 1,208/1,208 | 相同 |
| 失败 | 0 | 0 | 相同 |
| makespan | 2,803.65 s | 3,355.66 s | +19.69% |
| workflows/hour | 23.11 | 19.31 | -16.45% |
| future query | 0 | 55 | 能力隔离正确 |

有效目录：

- O0：`experiments/oracle/gpu_o0_o3_native18_v1/o0_r2`
- O3：`experiments/oracle/gpu_o0_o3_native18_v1/o3_r3`
- 机器可读对比：
  `experiments/oracle/gpu_o0_o3_native18_v1/comparison_summary.json`

O0 未查询任何 future view。O3 查询 19 次 `FUTURE_REUSE` 和 36 次
`NO_FUTURE_USE_PROOF`，没有跨 arm future 泄漏。

## 4. 正确性

两臂均满足：

- 所有非用户取消 obligation 均 satisfied；
- 所有在线动作均携带 source JointPlan ID；
- 没有 pending transaction、command、lease、funding 或 obligation；
- shutdown state 为 acknowledged；
- shutdown cleanup 未掩盖未解决事务。

O3 的 67 笔显式 residency transaction 全部 completed：

| 动作 | 次数 | 实际字节 |
|---|---:|---:|
| COMMIT_CPU | 18 | 2,993,160,192 B |
| PREFETCH_GPU | 13 | 1,364,852,736 B |
| DROP | 36 | 1,620,836,352 B |

因此本轮差异不是由失败、timeout、OOM、orphan transaction 或输入不一致造成。

## 5. 资源与服务行为

| 指标 | O0 | O3 |
|---|---:|---:|
| HBM >=80% 的采样占比 | 75.65% | 70.40% |
| HBM >=98% 的采样占比 | 56.53% | 49.31% |
| Host 峰值 | 50.48 GB | 54.11 GB |
| aggregate prefix hit | 93.62% | 93.67% |
| decode batch mean | 15.46 | 15.54 |
| decode batch P95 | 32 | 31 |
| decode batch >=16 | 44.66% | 45.51% |

O3 确实降低了一部分 HBM 高压时间，但没有提高 batch occupancy。相同输出 demand 下，
累计 decode service interval 从 2,373,688 ms 增至 2,963,172 ms，增加 24.83%。
该 interval 包含 scheduler/result-processing 边界，不是纯 CUDA kernel 时间；它与
O3 的负收益方向一致，但不能单独归因于 GPU kernel。

O3 新增 2.99 GB 显式 D2H 和 1.36 GB 显式 H2D。显式 H2D submit-to-complete
P50/P95 为 804/1,902 ms。O3 出现 16 次 residency 方向反转，其中 6 次在 5 秒内
发生。当前动作减少了 HBM residency，却没有产生足够的 beneficiary unlock，且增加了
restore 和调度扰动。

运行目录没有独立的 NVML 时间序列，因此本报告不声称精确 GPU utilization。两臂的
GPU service observer interval 都覆盖约 97% makespan，这至少说明 frozen replay
不是原 autonomous workload 中“多数 agent 等工具且 GPU 无 ready work”的低负载情形。

## 6. Parent 物理连续性

从 frozen physical sidecar 重算 root parent 首次调用到 JOIN 后首次 reentry 的 exact
token prefix：

- 18 个 parent 中 7 个保留至少 90% prefix；
- 其余 11 个低于 50%；
- retention ratio 中位数为 12.08%，最小 6.59%，最大 98.85%。

因此该 workload 的 parent continuation 呈两极分化。不能假设所有 parked parent 的旧
KV 都值得 round-trip；O3 已使用 exact physical reuse gate 排除低复用旧 suffix，但仍有
child 和后续 parent context 产生 18 笔 parked D2H。

## 7. 裁决

O3 候选吞吐下降 16.45%。按照 whole-run no-op dominance，有限候选 Oracle 必须在 O0
和 O3 candidate 中选择较快者，因此：

```text
finite-candidate Oracle gain = max(0, -16.45%) = 0%
```

该结果没有达到原计划的 3% 或 10% 门槛，不继续运行 O1/O2，也不能据此宣称
execution-KV joint synergy。

当前失败点不是迁移事务正确性，而是 Oracle 动作价值与执行选择：

1. execution order 虽以 O0 request-start order 为 no-op 候选，在线 directive 仍改变了
   batch 的 sequence-length composition；batch 数量相近但 decode service 显著变慢；
2. residency 动作没有严格绑定“当前被 HBM 阻塞且迁移后立即获得 service”的 beneficiary；
3. 16 次方向反转表明 latest-use 知识没有转化为稳定的 transfer calendar；
4. O0 原生 HiCache 已执行大量 write-back，O3 显式动作的边际空间有限。

## 8. 后续边界

本轮只包含一组 18-workflow 配对结果，不能给出置信区间。下一步不应重复相同 GPU
实验或继续调 workload 直到出现正例。应先离线重放 O3 的 67 笔动作，逐笔建立：

`victim -> released bytes -> beneficiary admission/service -> saved stall -> restore cost`

并让 execution package 以候选 batch 的 sequence-length-aware service cost进行 whole-run
no-op dominance。只有离线证明至少一个自然 action package 优于 O0 后，才值得再运行
一组预注册 GPU O0/O3。

## 9. 分析复现

```bash
conda run -n beliefkv python scripts/analyze_perfect_future_oracle_gpu.py \
  --o0-run experiments/oracle/gpu_o0_o3_native18_v1/o0_r2 \
  --o3-run experiments/oracle/gpu_o0_o3_native18_v1/o3_r3 \
  --output experiments/oracle/gpu_o0_o3_native18_v1/comparison_summary.json
```

此前因 stale keepalive、未刷新 visibility seed 或低复用 context 错误处理而停止的运行不
进入本报告；它们只用于修复 replay/runtime correctness。
