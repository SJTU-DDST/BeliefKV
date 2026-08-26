# BeliefKV Performance-First P3/P4 实现与 GPU 门禁

日期：2026-08-26

## 结论

JointPlan 输入压缩、Performance Mode、bundle-level D2H 和 observed Causal Package
Planner 已完成首版。CPU hot-path 门槛和单笔 restore correctness gate 通过。双向
D2H/H2D overlap 尚未获得硬件门禁授权，因此 TransferEngineV2 仍默认关闭，不能用于
正式 baseline/treatment A/B。

## 冻结实现

- `d4e3f73 perf(runtime): bind KV transfers to causal packages`：logical transfer
  lane、bundle-level D2H、beneficiary-bound package 和 H200 performance profile。
- `9ecdd75 fix(runtime): avoid redundant H2D after branch insertion`：允许 H2D 期间
  无害的 Radix child/sibling 挂接，保留 key、parent、handle generation 和 D2H
  descendant closure 校验。
- SGLang 版本未升级；performance patch 为
  `patches/sglang-0.5.2rc1-beliefkv-perf.patch`，历史 profile 继续使用原 patch。

## CPU 门禁

16,384 pages、32 runnable、384 changed pages、200 iterations：

| 路径 | P99 |
|---|---:|
| no-action snapshot | 0.234 ms |
| single-lock snapshot | 0.223 ms |
| worker delta apply | 1.915 ms |
| semantic JointPlan wall | 3.392 ms |

回归结果为 814 passed、7 skipped、8 subtests passed。跳过项不涉及 transfer、
JointPlan、restore 或 SGLang adapter。

## GPU 门禁

最终有效目录：
`experiments/micro/transfer_engine_v2_batch_gate_retryfix/20260826T060237Z/`。

配置为 H200 NVL、Qwen3-Coder-30B-A3B BF16、850K KV tokens、96 GiB Host pool、
max running 2、Performance Mode 开启、predictor 关闭、TransferEngineV2 关闭。

一条 4-extent、6,436,945,920-byte victim closure 完成了：

| 动作 | terminal command 数 | 时延 | 有效带宽 |
|---|---:|---:|---:|
| D2H OFFLOAD_CONTEXT | 1 | 249.8 ms | 25.8 GB/s |
| H2D PREFETCH_CONTEXT | 1 | 692.4 ms | 9.3 GB/s |

同一 bundle 的 D2H 在 native HiCache 中只入队一个 operation，但仍按四个 node ID
生成 callback，aggregate ACK 与 page/byte 统计守恒。restore verifier 的 14 项检查
全部通过：obligation satisfied、post-restore 32-token service quantum、无 pending
command/lease/reservation/transaction，shutdown cleanup 未掩盖未完成事务。

首轮门禁曾出现一次完整 H2D 后因 ancestor 新增 sibling 被 extent fingerprint 拒绝，
导致 6.437 GB 重搬。修复后复验只剩一笔 completed H2D，不再有 rejected full-copy
retry。该修复没有放宽 key split、parent change、callback failure 或 destructive D2H
closure 校验。

## 尚未证明

- 没有验证 D2H/H2D 同时执行或 PCIe 双向 overlap；backend capability 仍为 1。
- 该确定性 hook 验证 running-retraction/restore，不证明 Causal Package Planner 的
  自然 beneficiary 选择能够提高 workflows/hour。
- 单次 micro 的 28.7 秒 JCT 受 prefill/decode 波动影响，不进入性能 A/B。
- P4 下一门槛是在自然高压 workload 中观察 causal package dispatch、beneficiary
  first-service latency 和 saved stall；P3 双 lane 需单独明确授权后才能测试。
