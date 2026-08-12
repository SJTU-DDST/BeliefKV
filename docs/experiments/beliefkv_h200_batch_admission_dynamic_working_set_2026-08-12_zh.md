# H200 批量 Admission 与动态 Working Set 实施记录

日期：2026-08-12  
状态：代码与 CPU correctness 完成；GPU 性能验证暂停。

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

## 验证状态

- focused admission/JointPlan/profile/runtime 与扩展 SGLang adapter/retraction/controller/contract
  均已通过；最终精确计数以提交时 CI 输出为准；
- Python compile、shell syntax 与 `git diff --check` 通过；
- 未启动 SGLang，未运行 GPU 实验。

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
