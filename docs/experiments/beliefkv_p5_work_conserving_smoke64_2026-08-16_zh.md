# P5 Work-Conserving 64-Root Predictor-Off Smoke

日期：2026-08-16
状态：root-submission/work-conserving gate 通过；migration gate 未覆盖。

## 配置与范围

- BeliefKV commit：`866fdc37c068ebe77459e648d29836b7d26bdd1d`；
- H200 profile：`h200_bf16_v4`，SHA-256
  `5ece5b5075193856b1cf7fff081378fe1a4040734bd80133838713a9a90cd6ba`；
- 模型：Qwen3-Coder-30B-A3B-Instruct BF16；
- KV/Host pool：850,000 tokens / 96 GiB；
- server：32 max running，observed JointPlan、batch admission、dynamic working set 和 running
  retraction 开启；
- predictor、risk shadow 和 predictive action 全部关闭；
- workload：正式 train split 的 64 个唯一冻结实例，统一使用
  `parallel_analysis_2to3` 作为 development stress profile；
- 64 个 root 全部 eager 提交，观测约 8 分钟后受控停止；不进入训练、JCT 或正式 A/B 数据。

运行目录：
`experiments/raw/p5_work_conserving_smoke64/20260816T063017Z`。

## Root Backlog 与 Admission

真实 workload manifest 和运行 manifest 均记录：

- 64 个 root、64 个唯一 instance；
- `root_submission_mode=all_roots_eager`；
- `client_inflight_root_window=64`；
- `initial_unsubmitted_root_backlog=0`；
- runtime 创建 64 个 workflow、190 个 invocation/context。

SGLang metrics 共 1,102 个样本：平均 running 28.15、平均 waiting 68.50，最大分别为 32 和 95；
`queue > 0 && running = 0` 为 0。246 个 admission epoch 共准入 399 个请求，平均 native batch 1.62，
最大 15，91 个 epoch 的 batch 大于 1，native rejection 为 0。

因此本轮证明隐藏在 client iterator 中的 16 个 root 已被消除，且 server 在存在 GPU-ready backlog 时
保持 work-conserving。它不证明吞吐已经最优。

## KV 压力与迁移

最大 SGLang non-evictable pressure 为 15.43%，最大 observed JointPlan physical HBM pressure 为
30.97%；239 次
dynamic-working-set 变化全部处于 `gpu_fill`，从未开启 pressure action。因此：

- transfer telemetry：0；
- command dispatch/ACK：0/0；
- replacement beneficiary priority register/release：0/0；
- short residency reverse：0；
- retraction 只产生 low-pressure suppression，没有事务动作。

所以审阅要求的 `COMMIT_CPU -> ACK -> beneficiary first service` 没有被该 trace 覆盖。原因不是
beneficiary lifecycle 再次失败，而是八分钟内 physical KV pressure 仅达到 30.97%，未进入 80%
replacement watermark。`sglang:num_used_tokens` 扣除了 Radix evictable cache，不能解释为全部
GPU-resident KV。

## GPU 利用率

2,388 个 200 ms GPU sample 的平均利用率为 5.04%，61.22% 样本为 0%，仅 1.42% 样本达到 50% 以上；
但 server 同期持续保持约 30 个 running request，并且没有 queue-without-running sample。日志显示大量
短工具决策、小增量 prefill 和频繁 request turnover，最终 token usage 约 15%。

因此这轮低 GPU 利用率不能再归因于客户端没有提交新 root。更可能的剩余因素是 agent 工作阶段产生
短脉冲 GPU demand、频繁 prefill/decode 切换，以及当前模型/kernel 在该 batch composition 下的效率。
本轮不对这些因素做性能因果结论。

## 控制面与关闭

最终 summary 满足：

- 所有在线动作具有 source JointPlan ID；
- 无 pending transaction、command、restore obligation、lease 或 funding；
- shutdown cleanup 没有掩盖 unresolved transaction；
- audit writer 无 debug drop；
- 190 个本轮 Docker sandbox 已按运行目录 mount 白名单清理；
- SGLang 收到两阶段 shutdown ACK，无残留进程。

同时观察到 1,090 次 physical-commit budget exceeded 和 1,044 次 stale plan。由于本轮没有物理动作，
这些计数没有造成 transaction 错误，但说明完整性能 A/B 前仍需单独控制 JointPlan churn。

## Gate 结论

通过：64-root eager submission、server backlog、batch admission、work-conserving liveness、受控关闭。

未通过：策略性迁移、beneficiary first-service、D2H/H2D anti-thrash、迁移隐藏收益。

下一次迁移验证必须使用预先冻结、能在短窗口内形成足够 unique KV pressure 的 workload 或独立机制
micro-gate；不能反复延长本轮 trace 直到碰到迁移。完整 A/B 仍需使用独立 worktree，将 baseline 固定在
`5b15e65`，两边使用同一当前 workload launcher。
