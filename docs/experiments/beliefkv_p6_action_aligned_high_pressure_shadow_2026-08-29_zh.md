# P6 Action-Aligned 64-root 高压 Shadow

日期：2026-08-29

## 结论

本轮成功形成 64-root、32-running 的真实高压区间，验证了 PageIndex 修复、服务模型
契约、predictive worker 活性和 shutdown 守恒。但本轮不能评价预测动作收益，也没有
进入 PREPARE_HOST canary：高压后的 208 个 risk result 均未生成候选，最终 1,176 个
result 全部因 `closure_prediction_incomplete` 跳过。

根因不是 FrontierBelief 精度、transfer shape、收益阈值或 workload，而是 runtime 只对
任意前 64 个非终态 invocation 执行模型推理。实际 RCCG 含 64 个 parent 和 128 个
child；BeliefScope 会将 JOIN waiter 与所有未完成 child 作为一个原子闭包，因此任意
截断都会使闭包缺失 prediction。

实验后已改为：safe point 冻结全部 active invocation 的轻量特征，异步 risk worker
根据实际 KV 候选构造完整闭包并局部推理。该修复只完成 CPU 回归，本轮没有再次启动
GPU，也没有绕过 fresh-positive gate 开启 canary。

## 冻结配置

- 运行目录：
  `experiments/shadow/p6_action_aligned_high_pressure64/20260829T060040Z`
- 模型：Qwen3-Coder-30B-A3B-Instruct BF16，H200 NVL。
- profile：`h200_bf16_v6`，KV pool 850K token，Host pool 96 GiB。
- CUDA Graph：batch 1/2/4/8/16/24/32。
- workload：64 个预注册 `native_subagent_2to3` root，全部 eager submit，
  `max_running_requests=32`。
- predictor：schema-v4 action-aligned artifact；只读 risk shadow。
- 物理预测动作：overlay、prefetch canary 和 predictive retraction 全部关闭。

## GPU Service Artifact

未启动新 GPU calibration。使用既有 performance trace 的唯一 batch observation 重建：

`artifacts/p6/h200_bf16_v6/gpu_service_qwen3coder30b_bf16_h200_graph32_runtime_v1.json`

- 10,535 个唯一 batch sample；decode 9,772，prefill 763。
- 覆盖 batch 1-32 和 graph32 runtime。
- `evidence_role=runtime_validation`，`shadow_only=true`。
- 不将 scheduler/worker interval 冒充 CUDA kernel time。
- `online_canary_eligible=false`；受控 prefill/decode calibration 仍需后续补齐。

v6 launcher 会 fail-fast 校验 GPU/transfer artifact 路径及 hardware key。运行时 profile
contract 和 service binding 均通过，避免旧 artifact 静默回退。

## 提前停止

运行在 resident KV 首次超过 80% 后继续采样，并在高压 risk result 达到约 200 且仍无
fresh positive package 时停止。审计结果：

- `predictive_risk_progress`：1,176 条；
- HBM >= 80%：208 条；
- peak HBM pressure：100%；
- positive/fresh-positive/timing-available：全部为 0；
- RCCG：64 workflow、192 invocation/context；
- 事件：128 SPAWN、64 JOIN_CREATE、64 JOIN_WAIT、997 LLM submit/result；
- 受控停止时只有 2 个 child RETURN，因此该 trace 只用于系统 characterization。

## Predictive Worker

| 指标 | 结果 |
|---|---:|
| eligibility checked | 1,807 |
| enqueued | 1,176 |
| unchanged bucket suppressed | 631 |
| completed / failed / dropped / pending | 1,176 / 0 / 0 / 0 |
| candidate / certificate | 0 / 0 |
| `closure_prediction_incomplete` | 1,176 |
| skipped planning P50/P95/P99 | 2.92 / 18.22 / 44.71 ms |

这里的低 planning latency 只表示预测在闭包检查处提前返回，不能证明完整 scenario
evaluation 已经足够快。

## JointPlan 开销

| 路径 | P50 | P95 | P99 |
|---|---:|---:|---:|
| safe-point capture | 3.83 ms | 25.67 ms | 43.85 ms |
| snapshot build | 461.88 ms | 1,577.72 ms | 2,705.04 ms |
| plan compute | 528.03 ms | 1,704.17 ms | 3,081.92 ms |
| validation | 57.02 ms | 132.82 ms | 325.61 ms |
| trigger-to-validation | 661.30 ms | 2,831.60 ms | 5,644.02 ms |

worker 没有持续 backlog，但完整物理快照重新进入 predictor 路径，造成 CPU/GIL 竞争和
全局计划陈旧。`strict_global_stale` 为 1,807/1,808；action read-set stale 为 0，说明
全局 stale 指标包含大量无关 revision，但同步 capture/validation 开销仍然真实存在。

## 正确性

- PageIndex 未再触发 workflow charge 断言；高压跨过此前两次失败点。
- 997 个已启动 request 均在取消/drain 后 terminal。
- predictive worker、JointPlan worker 均正常关闭。
- 无 queued/inflight command、reservation、lease、obligation 或 transaction。
- `shutdown_cleanup_did_not_mask_unresolved_transactions=true`。
- `no_pending_transactions=true`，shutdown state 为 `acknowledged`。

## 修复

删除 scheduler-path 的全局 FrontierBelief 推理和任意 `[:64]` 截断：

1. safe point 为所有 active invocation 构造可序列化 `LocalFrontierFeatures`；
2. features 作为 observed metadata 进入冻结 PolicyInput；
3. risk worker 从实际 PREPARE/PREFETCH 候选构造 closure-complete BeliefScope；
4. worker 只为该 scope 的 invocation 调用 FrontierBeliefModel；
5. 旧 `frontier_predictions` 保留为 frozen replay fallback；
6. 缺失、身份错误或模型失败继续 fail closed，不影响 observed P5。

回归结果为 218 passed、8 subtests passed。P6 canary 继续关闭。下一步先实现 compact
semantic snapshot、候选局部 physicalization 和 bounded scenario evaluation；之后只需
一轮短高压 shadow 验证自然 fresh-positive package，不重复低压 w4，也不调整 shape
bucket、workload 或风险阈值。
