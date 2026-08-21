# BeliefKV P5 Gate C Native-64 失败 Characterization

日期：2026-08-22

## 1. 裁决

本轮在 60 分钟早停检查点判定失败并受控终止，不进入训练集、性能 A/B 或
Frozen GPU Replay。它证明 native 2-child workload 能形成持续 server backlog 和
物理 KV 高压，但同时暴露出 ordinary-prefix restore debt、错误 pressure 语义和
高压 JointPlan 控制面开销。

原始目录：

```text
experiments/raw/p5_gate_c_native64/20260821T193433Z
```

停止后 SGLang shutdown ACK 成功，GPU 回到 14 MiB/0%，64 个本轮 Docker
container 已清理。由于 workload client 对 SIGINT 没有响应，最终使用 SIGTERM；
因此 shutdown 后的 cancelled obligation 只证明清理守恒，不算正常 restore 成功。

## 2. 冻结配置

- Qwen3-Coder-30B-A3B-Instruct BF16，单张 H200 NVL；
- `h200_bf16_v5`，KV pool 850,000 tokens，Host pool 96 GiB；
- CUDA Graph 捕获到 batch 32；
- 64 个预注册 train root 全部 eager 提交，client concurrency=64；
- SGLang `max_running_requests=32`；
- `native_subagent_2to3`，predictor 与 predictive action 均关闭；
- 运行 3,703.12 秒，达到约 61.7 分钟早停检查点。

## 3. Agent 语义负载

| 事件 | 数量 |
|---|---:|
| workflow start | 64 |
| invocation create | 192 |
| SPAWN | 128 |
| JOIN create / wait | 64 / 64 |
| LLM submit / result | 1,218 / 1,096 |
| tool start / end | 3,037 / 3,037 |
| child RETURN | 1 |
| JOIN satisfied | 0 |

64 个 parent 均真实创建两个 FRESH child 并进入 JOIN_WAIT。60 分钟内只有一个
child RETURN，因此本轮尚未覆盖 parent continuation 或多轮动态 delegation；这不是
单独的 correctness 结论，因为实验在预注册的早停检查点被终止。

## 4. Admission 与活性失败

| 指标 | 结果 |
|---|---:|
| unique visible request | 1,218 |
| unique physical start | 1,121 |
| 停止时从未 physical start | 97 |
| physical-start wait P50 | 156.58 s |
| physical-start wait P95 | 819.25 s |
| physical-start wait Max | 938.79 s |

9 个 CPU-only ordinary waiting prefix 被错误创建为 durable restore obligation；
9/9 在 fallback 后都没有再次获得 GPU service。前 8 个占满默认 obligation 容量，
随后出现 9 次 `ordinary_waiting_restore_capacity_blocked`。这违反核心不变量：

```text
durable restore debt <=> BeliefKV 主动撤回过 running request
```

普通 waiting prefix 是 SGLang native cache miss，不能建立 transaction、lease、
restore priority 或全局 liveness 权限。

## 5. HBM、Host 与迁移

| 指标 | 结果 |
|---|---:|
| physical resident HBM peak | 83.54 GB / 850K tokens |
| SGLang non-evictable pressure mean / max | 13.80% / 32% |
| Host peak | 29.30 GB |
| native write-back D2H | 1,290 次 / 29.30 GB |
| native D2H submit-to-complete P50 / P95 | 144.60 / 376.53 ms |
| explicit BeliefKV transfer | 0 |
| reclaim requirement / rescue / replacement | 0 / 0 / 0 |

这里不存在 PageIndex 与 allocator 的物理不一致。physical resident 接近 100%
表示 Radix cache 填满；SGLang token usage 扣除了 native evictable cache，表示真正
不能立即回收的占用只有 13.8% 均值。错误在于 DynamicWorkingSet 和完整规划触发器
使用 gross residency 作为 admission pressure，导致可驱逐 cache 填满时仍主动收缩
active set。

## 6. GPU 与控制面

| 指标 | 结果 |
|---|---:|
| GPU utilization mean / P50 / P95 / P99 | 4.22% / 0% / 21% / 74% |
| GPU utilization 为 0 的样本比例 | 83.58% |
| prefill batch mean / tokens | 1.18 / 1,009,282 |
| decode batch mean / tokens | 31.37 / 170,008 |
| decode batch >=24 占比 | 99.26% |

GPU 利用率低不能归因于缺少 decode 并发；decode batch 已接近 32。控制面在高基数
状态下重新退化：

| 阶段 | P50 | P95 |
|---|---:|---:|
| admission ticket compile | 52.18 ms | 169.39 ms |
| incremental Radix sync | 2.40 ms | 20.03 ms |
| full Radix sync | 83.78 ms | 162.39 ms |
| safe-point delta capture | 7.89 ms | 30.65 ms |
| snapshot delta apply | 221.48 ms | 1,029.92 ms |
| snapshot build | 311.75 ms | 1,171.13 ms |
| plan compute | 381.93 ms | 1,299.40 ms |
| validation | 37.61 ms | 102.29 ms |
| plan age | 821.35 ms | 2,800.92 ms |

2,188 个完整计划中 2,186 个 strict-global stale。低压 fast path 本身没有失效；
问题是每个 TOOL/SPAWN/RETURN 在 gross HBM 高、effective pressure 低时仍请求完整
物理规划，Python worker 与 scheduler 竞争 GIL。

## 7. 已实施修复

1. ordinary CPU-only prefix 直接交给 SGLang PrefillAdder/native load-back；不再
   创建 obligation、restore transaction、lease、funding 或优先级。
2. waiting request 的当前 Radix path 仍进行 generation-safe ownership rebind，
   但只产生一次性 native delegation telemetry。
3. DynamicWorkingSet 使用 `capacity - (allocator free + native evictable)` 作为
   effective pressure；gross physical residency 仅保留为观测与 victim 空间。
4. predictor-off observed P5 中，低 effective pressure 的因果事件只提交
   apply-only delta。有效 pressure、beneficiary deficit、transfer ACK、restore/
   retraction revision 仍触发完整计划；predictive worker 启用时因果事件仍触发完整
   belief planning。
5. native-fallback 去重状态按 request 有界，并在 finish/abort/timeout 时清理。

验证：adapter/restore/admission 定向 175 passed；完整 core 分组 771 passed、
2 skipped；Deep Agents/runtime/collection 分组 160 passed；`py_compile` 和
`git diff --check` 通过。

## 8. 下一步 Gate

使用相同 v5 profile 和同一 64-root manifest 复验，不改变 KV/Host pool 或
watermark。60 分钟检查要求：

- ordinary durable obligation 和 ordinary capacity blocked 均为 0；
- effective pressure 低时完整计划频率显著下降；
- waiting backlog 下 running 不因 ordinary prefix 单调排空；
- physical-start wait P95 明显低于本轮 819 秒；
- 无 orphan command、lease、transaction 或 container；
- 只有出现真实 reclaim requirement 时才要求
  `COMMIT_CPU -> ACK -> beneficiary first service`，不以迁移次数作为硬门槛。

该 Gate 通过后才冻结 native trace 并进入 O0/O3；否则继续修 P5，不允许由预测器
掩盖 observed-policy 活性缺陷。
