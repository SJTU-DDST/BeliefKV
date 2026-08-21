# BeliefKV P5 Gate C Native-64 Rematch 失败 Characterization

日期：2026-08-22

## 1. 裁决

修复 ordinary restore debt 与 gross-pressure 误判后，使用相同冻结配置执行了一次
Gate C 复验。本轮在约 60 分钟检查点受控停止，仍不进入训练集、性能 A/B 或
Frozen GPU Replay。

前一轮的全局 restore convoy 已消失，但满池后出现新的 admission 活性缺陷：
ticket 编译使用 rematch 前的 prefix demand；SGLang 随后重新匹配 Radix/HiCache
prefix，需求增长后整张 ticket 被拒绝，下一 epoch 又以旧需求签发。该循环造成
大量零 native batch，并使 ordinary CPU-only prefix 无法真正 load-back。

原始目录：

```text
experiments/raw/p5_gate_c_native64_fixed/20260821T215840Z
```

停止后 workload、SGLang 和 64 个本轮 Docker container 均已清理，GPU 回到
14 MiB/0%。最终 summary 中 waiting/running、command、transaction、lease、
funding 和 obligation 均为空。由于本轮是主动早停，shutdown 后的 request finish
不能计为自然完成。

## 2. 冻结配置

- 代码基线：`0986398`；
- Qwen3-Coder-30B-A3B-Instruct BF16，单张 H200 NVL；
- `h200_bf16_v5`，KV pool 850,000 tokens，Host pool 96 GiB；
- CUDA Graph max batch 32，SGLang max running requests 32；
- 同一预注册 64-root manifest，全部 eager 提交，client concurrency=64；
- `native_subagent_2to3`，predictor/predictive action 关闭；
- workload event span 约 58.3 分钟，包含 server warm-up/shutdown 的审计跨度约
  61.3 分钟。

## 3. 已验证的前一轮修复

| 指标 | 结果 |
|---|---:|
| ordinary durable obligation | 0 |
| restore capacity blocked | 0 |
| restore/overlap barrier | 0 |
| ordinary native delegation | 34 次 / 17 个唯一请求 |
| 完整 JointPlan | 1 |
| apply-only delta | 4,301 |
| shutdown unresolved transaction/lease/command | 0 |

这证明：

1. ordinary native cache miss 不再占用 durable restore authority；
2. gross Radix cache 满载不再被错误解释为不可回收 HBM pressure；
3. predictor-off 低有效压力阶段的因果事件不再反复触发完整物理规划。

因此，本轮失败不能继续归因于旧的 obligation-slot convoy。

## 4. 新的 Admission 根因

在停止前的高压阶段：

- 17 个唯一 ordinary CPU-only prefix 被委托给 native PrefillAdder；
- 这些请求在停止前均未获得后续 `request_physical_start`；
- 共记录 4,582 次
  `prefix_rematch:prefix_demand_increased`；
- 478 个已签发 ticket 的 epoch 最终 native batch 为 0；
- 典型 epoch 签发 11--12 个 ticket，但 11--12 个均在 prefix rematch 后失效。

控制流为：

```text
旧 prefix hit 编译 ticket
  -> req.init_next_round_input() 重新匹配 GPU/Host prefix
  -> uncached/startup demand 增长
  -> 整张 ticket 因 prefix_demand_increased 拒绝
  -> side index 仍保留旧 demand
  -> 下一 epoch 再次签发同一 stale ticket
```

这不是 restore debt，也不是 durable lease 不足，而是 ticket 的物理需求证书没有在
同一 safe point 内重算。普通请求虽然不再冻结全局 admission，但仍无法通过 native
load-back 获得 service。

停止点附近 running 从 32 降到 20--21，queue 增至约 106。此时服务仍在推进，
但该趋势已经违反 Gate C 的 work-conserving 活性要求，因此不继续等待 2 小时
deadline。

## 5. Agent Workload 与终止性

停止决策点约有 1,017 次 LLM submit、996 次自然 result 和 3,732 次工具调用；
abort 清理后的最终事件文件为：

| 事件 | 数量 |
|---|---:|
| workflow start | 64 |
| invocation create | 192 |
| SPAWN | 128 |
| JOIN create / wait | 64 / 64 |
| LLM submit / result | 1,040 / 1,040 |
| tool start / end | 3,757 / 3,754 |
| child RETURN | 0 |
| JOIN satisfied | 0 |

64 个 parent 均真实创建两个 FRESH child 并进入 JOIN_WAIT，但没有 child 在本轮
自然 RETURN。runtime 只观测到 6 次 guard pattern，全部 `enforced=false`；
重复失败调用抑制 4 次，未发生 workflow deadline。因此 0 RETURN 不能归因于
guard 误杀，但该 trace 仍不能覆盖 parent continuation、H2D reentry 或多轮动态
delegation，也不能冻结为完整 Oracle truth。

## 6. HBM、Host、GPU 与控制面

| 指标 | 结果 |
|---|---:|
| gross HBM peak | 83.558 GB / 850K tokens |
| effective pressure peak | 33.93% |
| Host peak | 31.534 GB |
| native D2H | 1,158 次 / 31.534 GB |
| native D2H P50 / P95 | 119.75 / 328.62 ms |
| native H2D | 0 |
| explicit BeliefKV transfer | 0 |
| reclaim / rescue / semantic replacement | 0 |
| GPU utilization mean | 4.44% |
| GPU utilization=0 | 83.66% |
| GPU utilization>=50% | 2.80% |
| safe-point delta capture P50 / P95 / P99 | 5.42 / 26.38 / 31.75 ms |

满池传输全部是 SGLang HiCache `native_write_back`，不能计为 BeliefKV 动作。
effective pressure 最高只有 33.93%，所以 observed P5 没有生成 semantic reclaim
符合当前策略；但 rematch 后的真实 startup demand 未进入 requirement，导致该次
运行也没有覆盖 beneficiary-bound replacement。

## 7. 已实施修复

1. `VisibleAdmissionIndex` 默认仍拒绝 demand growth；只有 runtime 确认
   request/context/bundle generation 均有效时，才允许记录增长后的 prefix demand。
2. 纯 `prefix_demand_increased` 在当前 safe-point HBM 与 prefill budget 内时，
   原子重签该请求的局部 ticket；无关 ticket 不失效。
3. 当前 prefill budget 不足时只延后本请求，但增长后的 demand 已写回，下一 epoch
   将按新值编译，不再形成 stale retry。
4. 当前 HBM budget 不足时生成
   `prefix_rematch_bounded_hbm_budget` ReclaimRequirement，并复用既有
   JointPlan/replacement/rescue 状态机，不新增第三个策略源。
5. bundle set/generation、context epoch 或 request version 变化仍然严格拒绝。
6. 新增单请求重签、HBM deficit、bundle 负路径和多 ticket 零批次回归测试。

CPU 验证：受影响的 admission/chunked-prefill/JointPlan/restore/retraction 集合
`249 passed, 8 subtests passed`；聚焦 rematch/native-fallback 集合 `9 passed`；
`py_compile` 与 `git diff --check` 通过。

## 8. 下一步唯一 Gate

使用相同 v5 profile、KV/Host pool 和冻结 64-root manifest 再执行一次 Gate C，
不修改 workload 或阈值。60 分钟检查必须同时满足：

- `prefix_demand_increased` 不再形成连续零 native batch；
- ordinary delegation 后至少有请求通过 native H2D/prefill 获得 physical start；
- ordinary obligation、capacity block 和 barrier 保持 0；
- waiting backlog 下 running 不因 rematch ticket 失效而单调排空；
- 若 rematch 后 HBM 真正不足，必须观察
  ReclaimRequirement -> replacement/rescue -> beneficiary service；
- 无 orphan command、lease、transaction 或 container；
- 至少出现自然 child RETURN/JOIN 才能冻结完整 demand；否则该轮仅用于 P5
  物理活性，不进入 O0/O3。

正确性 gate 通过时才生成可视化时间线。
