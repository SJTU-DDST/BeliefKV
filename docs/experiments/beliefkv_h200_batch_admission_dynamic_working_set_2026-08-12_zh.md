# H200 批量 Admission 与动态 Working Set 实施记录

日期：2026-08-12  
状态：GPU correctness/liveness gate 完成；批量 admission 通过，动态 residency 仍需改进。

## 问题证据

来源：`h200_bf16_pressure_v2/h200-pressure-02-parallel32-r0`。

- 806 个 admission epoch 的 BeliefKV token budget 均被 `rem_chunk_tokens` 限制为 4,096；
- ticket 数量大于 1 的 epoch 为 115 个，但 native prefill batch 大于 1 的 epoch 仅 11 个；
- 815 个 uncached prompt 的 P50/P95/max 约为 272/3,677/7,954 tokens；
- 后段出现 `policy_max_requests=1`，说明异步 JointPlan immediate set 也限制了批量准入。

因此不能只增大 workflow 并发，也不能只改 SGLang chunk；需要同时修复 JointPlan admission set、整批
容量证书和 native prefill quantum。

## 实现

1. `AdmissionTicket` 区分完整 request demand 与当前 epoch commitment；compiler 在 policy order 中
   优先打包可完整 prefill 的请求，再放至多一个 chunked tail。若最高优先级请求是 oversized
   prompt，为它保留至多 1/4 token budget，防止长 prompt 被持续短请求流饿死。
2. prefix/Radix rematch 后按同一 epoch 累加真实 prefill tokens 与 HBM bytes，任何超出 batch
   certificate 的 request 被拒绝，不依赖各 request 独立通过。
3. `DynamicWorkingSetScheduler` 以 workflow fairness 为外层顺序，以 GPU-ready target 控制工作集：
   低压 work-conserving，高压按 HBM pressure 连续收缩；RCCG unlock value 只提供至多四个 fairness
   rank 的有界提升。
4. `extend_joint_epoch_admission()` 在原 plan/epoch 内提升显式 DEFER 或补入缺失 admission slice，
   不替换 residency/retraction 动作。stale、dependency-invalid、restore-blocked slice 不会被提升。
5. observed D2H/commit/drop/recompute 和 running retraction 由统一 pressure gate 开启；restore H2D、
   terminal cleanup 和已在途事务继续执行。
6. 新 H200 v4 profile 同时冻结 `chunked_prefill_size=16384` 与
   `max_prefill_tokens=16384`，launcher 禁止命令行覆盖并在 `/get_server_info` 后校验。

## 配置与观测

生成的新 experiment config 默认启用 dynamic working set；核心 `BeliefKVConfig` 默认保持关闭，以便
历史 replay 和旧测试不改变语义。可配置项：

- `dynamic_working_set_pressure_enter_ratio=0.8`
- `dynamic_working_set_pressure_exit_ratio=0.7`
- `dynamic_working_set_min_ready_requests=4`
- `dynamic_working_set_min_hold_epochs=8`
- `joint_workflow_active_window=32` 作为 H200 新实验的 hard maximum，而非固定平分资源的窗口。

审计新增 issued/native prefill batch histogram、平均 batch size、working-set mode、target/selected ready
数量、HBM pressure、active workflows 和 pressure action 状态。

## GPU Gate 结果

固定运行：

- 代码 commit：`2ecebb6`；
- profile：`h200_bf16_v4`，850,000-token KV pool，96 GiB Host pool；
- workload：16 个 root workflow，分两批各 8 个启动，均使用
  `parallel_analysis_2to3`；
- artifact：`h200-batch-16root-2wave-r1/20260812T131516Z`。

正确性与活性：

- 16/16 workflow 自然结束，1,422 次 LLM submit/result、2,260 次 tool start/end
  全部成对；
- 32/32 child spawn，16/16 JOIN 满足；
- 没有 OOM、execution timeout、admission starvation 或 orphan transaction；
- shutdown 前所有 command、lease、funding、restore obligation 守恒，GPU 释放至 14 MiB。

批量 admission：

- 1,291 个 ticket epoch，native batch mean/P50/P95/max 为
  1.123/1/2/5；
- 148 个 epoch 的 native batch 大于 1，占 11.46%；
- admission wait P50/P95/max 为 162/404/3,416 ms；
- `prefix_rematch_after_ticket_invalidated=0`，说明 prefix rematch 修复有效。

资源与迁移：

- physical HBM peak 83,558,400,000 bytes，达到冻结 KV pool 上限但无 OOM；
- Host KV peak 80,948,527,104 bytes，未达到 96 GiB 上限；
- engine-locked peak 31.19 GB，migratable peak 83.41 GB；
- native HiCache 完成 2,991 次 D2H write-back，共 88.40 GB；
- BeliefKV lifecycle 完成 77 次 terminal private drop/cleanup，没有遗留事务。

性能与局限：

- 16 个 workflow 总墙钟 8,640.6 秒，吞吐 6.67 workflows/hour；
- workflow JCT P50/P95/max 为 4,656/8,631/8,631 秒；
- GPU utilization mean/P50/P95 为 6.39%/0%/18%，busy fraction 44.72%；
- 高压后 observed JointPlan 没有产生策略性 retraction/offload，主要依赖原生
  HiCache 的细粒度 reactive write-back；
- `online_joint_physical_commit_budget_exceeded=31,337`、
  `joint_plan_stale=28,968`，控制面 churn 仍然明显；
- `pylint-4604` 的一个 83.6K-token child prefix 在下一 epoch 只命中 385 token，
  触发约 84.6K token 重算。其他长 context 仍能命中 100K 级 prefix，因此这是特定
  ownership/Radix reentry 失配，不是统一的长上下文行为。

结论：batch admission correctness/fairness gate 通过，可以恢复关闭 predictor 的正式
train 数据采集；但本轮不能证明 dynamic residency 性能收益。GPU 利用率低主要由长时间工具阶段、
短 decode burst 和尾部长序列成本共同造成，不能仅通过继续增大 admission batch 解释。

## 验证状态

- focused admission/JointPlan/profile/runtime 与扩展 SGLang adapter/retraction/controller/contract
  均已通过；最终精确计数以提交时 CI 输出为准；
- Python compile、shell syntax 与 `git diff --check` 通过；
- GPU gate 已完成；formal train 采集使用新的 64-workflow 冻结清单，development gate
  的 16 个任务不进入正式训练集。

## 下一次唯一 GPU Gate

固定同一 workload manifest 和 v4 profile，运行一次短 trace，比较历史 v3 characterization。必须报告：

- native prefill mean/P50/P95 及 batch > 1 epoch 比例；
- GPU-ready、running、GPU busy 和 prefill/decode throughput；
- HBM pressure mode 占比及 working-set size；
- 低压 observed destructive action 数必须为 0；
- 高压 retraction/offload、restore completion 和 orphan transaction；
- workflow service lag 与最大等待，确认 batch fill 未破坏 fairness。

只有 batch size 显著高于 1.02 且无 OOM、restore liveness/fairness 回归后，才能恢复 64-train
collection。若 GPU-ready 本身不足，则问题属于 workload arrival/fan-out，不再通过调大 admission 参数掩盖。
