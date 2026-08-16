# P5 Work-Conserving 正式 Treatment：Restore Barrier 失败归因

日期：2026-08-16  
状态：正式输入与物理压力有效；correctness/liveness gate 未通过；不计入 A/B。

## 冻结配置

- BeliefKV commit：`aedfb7c44217abcca9a2bb73dc0e4ea9863475e2`；
- baseline commit：`5b15e65`，本轮未启动；
- H200 profile：`h200_bf16_v4`，SHA-256
  `5ece5b5075193856b1cf7fff081378fe1a4040734bd80133838713a9a90cd6ba`；
- Qwen3-Coder-30B-A3B-Instruct BF16，850,000 KV tokens，96 GiB Host pool；
- 64 个预冻结 train root，全部 `parallel_analysis_2to3`，`all_roots_eager`；
- client workers 64，server `max_running_requests=32`；
- observed JointPlan、batch admission、dynamic working set 和 running retraction 开启；
- predictor、risk shadow 和 predictive action 全部关闭；
- 131,072 context，保留最近 8,192 tokens，最大输出 4,096 tokens；
- request inactivity/queue timeout 均为 7,200 秒；
- 固定终止条件原为全部 workflow runtime terminal，没有按迁移事件自适应停止。

运行目录：
`experiments/raw/p5_work_conserving_ab_v1/treatment/20260816T071755Z`。

## 停止原因

运行约 200 分钟后出现超过 35 分钟的持续 admission stall：仍有 48 个 native waiting request，
running 从 14 逐步降至 4，JointPlan 连续选择 0 个 ready request、签发 0 张 ticket。按预先约定的
correctness stop rule 受控停止。SGLang 两阶段 shutdown 完成，191 条 dispatch 全部收到 ACK，最终无
pending command、transaction、lease 或 funding。

64 个 root 中只有 12 个自然返回 `outcome=completed`。这 12 个结果全部 system-JCT eligible，其中
11 个 native-agent-JCT eligible；由于其余 workflow 被停止，本轮约 3.47 completed workflows/hour 只是
截断下界，不能与 baseline 比较。baseline 因 treatment gate 失败而没有启动。

## 两种 KV Pressure 必须区分

`sglang:num_used_tokens` 的定义为：

```text
max_total_tokens - (allocator_available + radix_evictable)
```

它描述不可通过原生 Radix eviction 立即回收的 token，不是全部 GPU-resident KV。正式运行统计如下：

| 指标 | 均值 | 最大 | 最终 | 高于 80% 的时间比例 |
|---|---:|---:|---:|---:|
| SGLang non-evictable pressure | 29.46% | 55.99% | 8.25% | 0% |
| PageIndex physical-resident pressure | 80.99% | 99.47% | 99.47% | 71.47% |

Host physical pressure 最终为 99.99%。因此本轮并非“没有形成 KV 压力”：物理 Radix working set 已几乎
占满 HBM 和 Host；较低的 `num_used_tokens` 表示其中多数 GPU KV 可被 SGLang 原生 LRU 驱逐。旧的
`STOP_REASON.json` 中 `native resident pressure` 已修正为 `native non-evictable pressure`。

## Admission 与 Restore 失败链

共观测 4,678 个 admission epoch，全部存在 waiting request：

- 3,106 个 epoch 签发 0 张 ticket；
- 3,960 个 epoch 的 physical HBM pressure 不低于 80%；
- 其中 3,104 个同时满足高 physical pressure、waiting backlog 和 0 ticket；
- waiting 最大为 101，最终 DWS 为 `hbm_pressure_replacement`，但 selected/target ready 均为 0。

根因不是 beneficiary priority 生命周期，而是 restore debt 类型混淆：

```text
native HiCache 产生 CPU-only waiting prefix
  -> 创建 ORDINARY_WAITING_PREFIX obligation
  -> 显式 H2D 需要 direct allocator capacity
  -> funding D2H 因 Host pool 饱和而失败
  -> ordinary obligation 超过 escalation deadline
  -> 被误当成 BeliefKV running-retraction debt
  -> 全局 restore_debt_barrier 阻塞所有无关 waiting request
  -> running batch 逐步排空，GPU 长期空闲
```

40 个 obligation 全部是 `ordinary_waiting_prefix`，没有一笔来自 running retraction。29 笔最终恢复并获得
GPU service，11 笔在受控停止时取消；累计 22,421 次 blocked。主要 blocker 为：

- `funding_preview_scan_budget_exhausted`：22,291；
- `host_capacity`：22,171；
- `device_capacity`：5,757；
- `protected_restore_owner`：2,284。

这证明 restore 物理机制可以成功，但普通 cache miss 不应获得冻结全局 admission 的权限。

## Transfer 与 GPU 利用率

完成的 transfer：

| 类型 | 方向 | 次数 | 字节 | submit-to-complete P50 | 最大 |
|---|---|---:|---:|---:|---:|
| native write-back | D2H | 3,915 | 93.95 GB | 130.27 ms | 1,626.68 ms |
| explicit offload | D2H | 97 | 6.57 GB | 229.51 ms | 2,720.34 ms |
| explicit prefetch | H2D | 23 | 1.56 GB | 602.18 ms | 2,432.80 ms |
| native demand-load | H2D | 2 | 304.15 MB | 1,171.50 ms | 1,171.50 ms |

显式 context transfer 出现 18 次方向反转，其中 5/10/30 秒内分别为 1/1/4 次。本轮所有显式迁移均由
ordinary restore/funding liveness 路径触发；`replacement_beneficiary_priority_registered=0`，没有形成
评审要求的 `COMMIT_CPU -> ACK -> beneficiary first service` 语义 replacement 链。

207.52 分钟 metrics 窗口内，平均 running/waiting 为 24.71/55.68，最大为 32/96。GPU 采样平均利用率
2.46%，82.96% 样本为 0%，仅 0.45% 样本达到 50% 以上。累计观测 1,615,267 prefill tokens 和
478,280 decode tokens，对应 wall-clock 约 129.7/38.4 tokens/s。低利用率的直接原因是 admission 被
restore barrier 冻结，而不是缺少 client root backlog。

## JointPlan 控制面

本轮还有 12,178 次 stale plan 和 9,611 次 `max_joint_plan_budget_ms=1` 超限。大量事件对应零候选、
零 residency action 的 safe-point seed；当前 audit 不能给出“其中多少次实际阻塞了一笔 physical
replacement”的可靠比例。因此这些计数是明确的控制面开销/新鲜度问题，但不是本轮 restore deadlock
的已证实主因。后续需给 budget/stale 事件加入 action count、action group 和 candidate ID 后才能计算
physical-action block rate。

## 已实施修复

修复后采用两级 restore 语义：

1. `RUNNING_RETRACTION` 是 BeliefKV 主动制造的债务，继续使用 durable obligation、allocator-backed
   lease、service grace 和全局 overdue barrier；
2. `ORDINARY_WAITING_PREFIX` 是 native HiCache cache miss，只做局部优先级管理。显式 H2D 无法立即
   满足时，退回 SGLang PrefillAdder 的 `available + evictable` 容量裁决和 native demand-load，不再
   创建重复 allocator lease，也不能冻结无关 admission。
3. 普通 obligation 最多占用 8 个通用槽位，另有 2 个槽位只对 RUNNING_RETRACTION 开放；
4. ORDINARY_WAITING_PREFIX + native_admission_fallback 不再进入 restore-ready priority 或 working-set mandatory，NO_TOKEN 后按普通 native waiting 排序。

回归验证：聚焦门禁 19 passed；完整相关回归为 162 passed、6 subtests passed。新增测试覆盖普通容量占满后 running-retraction 仍可建立 debt，以及 native fallback 不覆盖后续可运行请求。

## Gate 结论

- 正式输入、root backlog、物理 HBM/Host 压力和 transfer telemetry：有效；
- transaction/ACK/shutdown 守恒：通过；
- work-conserving liveness：失败；
- semantic replacement、beneficiary first service 和 workflows/hour A/B：未覆盖；
- 本轮 treatment：不得进入正式性能结果；
- baseline：不运行。

下一次 GPU 运行应复用同一冻结 manifest/profile，只验证修复后的 ordinary restore liveness 和自然
replacement。必须看到普通 restore 无全局 barrier、waiting backlog 能继续获得 ticket，并至少完成一笔
semantic replacement，之后该新 treatment 才能与 `5b15e65` baseline 配对。
