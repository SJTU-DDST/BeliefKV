# BeliefKV 当前架构与实现状态

更新日期：2026-09-15  
当前高压 A/B 代码基线：`f548210`

本文只记录当前事实和下一阻塞项，不再追加逐日开发日志。2026-09-12 以前的完整历史保存在
`docs/archive/snapshots/architecture_status_zh.md`，单次实验细节保存在
`docs/experiments/`。

## 1. 当前结论

BeliefKV 当前由两部分组成：

- P5：已接入在线路径的 observed-state Agent/KV JointPlan；
- P6：已将异步预测输出接入统一 JointPlan action group，代码路径同时覆盖
  predictive execution/admission 排序、`PREPARE_HOST`、真实 deficit 下消费 prepared
  victim 的 `COMMIT_CPU`，以及 latest-start `PREFETCH_GPU`。当前尚缺 GPU 高压 A/B。

预测器不直接提前驱逐 GPU KV。`PREPARE_HOST` ACK 会建立版本化的 beneficiary-victim
binding；真实 `ReclaimRequirement` 到来后，observed planner 优先消费仍有效且容量足够的
prepared victim，再执行 `COMMIT_CPU -> beneficiary priority -> first GPU service`。任何身份、
epoch、物理 closure 或容量失效都回退当前 P5。

## 2. 模块状态

| 模块 | 状态 | 当前边界 |
| --- | --- | --- |
| Runtime event + RCCG | 可用 | 动态 SPAWN/RETURN/JOIN/TOOL/MESSAGE |
| Visible waiting queue + AdmissionTicket | 可用 | SGLang allocator 最终验收 |
| Work-conserving JointPlan | 可用 | 吞吐优先，fairness 仅防饿死/tie-break |
| PageIndex + PhysicalBundle | 可用 | 多 owner、generation、lock、closure |
| Reactive `COMMIT_CPU/DROP` | 可用 | 只由明确 beneficiary deficit 触发 |
| Running retraction | 可用 | 最新修复正在高压回归 |
| Transactional restore | 可用 | H2D/native load/recompute，service 后终结 |
| FrontierBelief | 可用 | action-local demand/scenario，OOD fail closed |
| Predictive execution/admission | 已接入 | action-unlock/token 与 HBM demand 联合排序，native allocator 最终验收 |
| Predictive `PREPARE_HOST` | 已接入 | D2H 后保留 GPU KV，并建立 beneficiary-victim binding |
| Beneficiary-bound `COMMIT_CPU` | 已接入 | 只由真实 deficit 授权，优先消费 prepared victim |
| Predictive `PREFETCH_GPU` | 已接入、默认关闭 | latest-start 门禁和 safe-point rematerialization；GPU canary 待执行 |
| Oracle | 暂停 | 仅作契约测试和诊断 |
| Morphology 独立策略 | 不再使用 | transfer shape 仅作成本/OOD 输入 |

## 3. 当前在线算法

```text
Agent events
    -> RCCG
    -> bounded observed seed + FrontierBelief
    -> CausalActionPackage
       {execution order, ADMIT/DEFER, beneficiary, victim, deadline}
    -> JointPlan ActionGroup
       {SCHEDULE, PREPARE_HOST, COMMIT_CPU, PREFETCH_GPU, RESTORE, RETRACT}
    -> safe-point physical validation
    -> AdmissionTicket / HiCache command
    -> SGLang batch / DMA
    -> ACK + first GPU service
```

同步 safe-point 只执行有界状态捕获、seed 和动作局部校验；预测与 scenario evaluation 在
异步 worker 中运行。任何 stale、OOD、资源不可行或收益不足的预测结果都回退 P5。

当前架构图：

![BeliefKV 当前联合调度](figures/beliefkv_joint_algorithm_overview.svg)

## 4. 已验证证据

### 4.1 控制面

最近有效结果中：

- natural PREPARE run 的 safe-point capture P50/P95/P99：
  0.227/0.433/1.083 ms；
- deterministic PREPARE gate：
  0.293/0.526/0.829 ms；
- predictive compose/scenario 的最近 P95：
  27.41/19.32 ms，total 49.45 ms；
- worker 在对应 gate 中无 pending、drop 或 failure。

同步路径已经达到可接受范围；异步预测计算仍需限制触发频率和候选规模，但不会阻塞
SGLang scheduler。

### 4.2 PREPARE_HOST 机制

2026-09-12 的 deterministic gate 完成一笔真实 D2H：

- 766,083,072 bytes；
- 2 extents；
- intent、safe-point、queue、dispatch、DMA、ACK、terminal 全链路守恒；
- physical commit wall/thread CPU：4.284/2.217 ms；
- 无 orphan command、lease、reservation 或 transaction。

该结果只证明机制正确，不证明自然 workload 有吞吐收益。

### 4.3 自然机会

最近 64-root natural run：

- HBM peak 86.28%；
- HBM 大于 80% 持续 413 秒；
- 最大 migratable KV 68.79 GB；
- bounded-seed beneficiary 的 future-growth deficit 始终为 0；
- 367 次 `capacity_available`、242 次 `slot_only`；
- 0 natural predictive command。

这些数据只能说明旧探针针对 bounded-seed 首个 beneficiary 没有识别出 deficit。它们
不能区分“workload 没有优化机会”和“机会存在，但 beneficiary 搜索、动作组合或提交
时机没有利用该机会”。当前工程判断更倾向于后者，后续必须用完整 decision funnel
验证，不能再把 0 predictive command 直接解释为 0 opportunity。

### 4.4 数据面与 CUDA Graph

- H200 BF16 KV pool：850,000 tokens；
- Host pool：96 GiB；
- CUDA Graph 已覆盖 batch 1/2/4/8/16/24/32；
- 4-extents 6.44 GB gate 中 D2H 249.8 ms、H2D 692.4 ms；
- 当前 backend 不声明 concurrent PCIe transfer capability。

## 5. 2026-09-15 最新正确性修复

当前工作区已完成：

- 失败 JointPlan mirror 的恢复；
- workflow 不再被固定 activation cutoff 强制截断；
- running retraction 前强制核对 allocator、Radix 和 engine live ownership；
- 修复 live KV 被错误留在 allocator free pool 的风险；
- restore ticket 在物理路径变化后会回退到可推进状态，restore owner 未就绪时不再
  冻结整个 waiting queue；
- v7 runtime profile 和 versioned SGLang ownership patch。

这些变更尚不能自动转化为性能收益，必须先通过同一冻结 workload 的高压回归。

## 6. 当前阻塞项

1. prediction-to-action utilization gap 已在代码层闭合，但尚未经过 H200 高压 GPU 路径验证，
   因而不能声称吞吐提升。
2. 当前 bounded composer 只评估前 2--4 个 request，并将最终物理候选限制为
   `1 beneficiary x 2 victims`。这是在线成本边界，不是全局最优保证。
3. `PREFETCH_GPU` 默认关闭；启用前仍需验证 live H2D 模型、latest-start 新鲜度和
   restore liveness。
4. prepared binding 目前每个 beneficiary 只保留一个 victim；失效时回退 P5，不执行
   预测性 COMMIT。
5. v7 GPU service artifact 适合排序和 shadow；正式吞吐结论必须来自与冻结 observed
   baseline 相同 workload/profile 的 GPU A/B。

## 7. 下一步

当前关键路径：

1. 等待或读取冻结 observed baseline，固定 workflows/hour、GPU utilization、token
   throughput、HBM/Host 和迁移指标。
2. 运行短 predictor-enabled 高压 gate，验证 schedule package、PREPARE ACK、prepared
   binding、真实 deficit COMMIT、beneficiary first service 和 PREFETCH 决策漏斗。
3. Gate 中任何 stale/OOD/物理化失败均回退 P5；不通过降低收益阈值制造动作。
4. 通过后运行相同输入的完整 predictive JointPlan arm，与 frozen baseline 直接比较
   successful workflows/hour、decode throughput、GPU utilization、saved stall 和无效迁移率。
5. 只有 GPU A/B 后生成预测调度时序图，并将收益按 execution reorder、提前 D2H、
   deficit-time COMMIT 和 latest-start H2D 分解。

执行细节见 `docs/implementation_plan.md`。

## 8. 权威资料

- 当前设计：`docs/beliefkv_design_2026-07-14_zh.md`
- 当前计划：`docs/implementation_plan.md`
- 文档导航：`docs/README_zh.md`
- 当前图解：`docs/beliefkv_jointplan_visual_zh.md`
- 历史状态：`docs/archive/snapshots/architecture_status_zh.md`
- 实验报告：`docs/experiments/`
