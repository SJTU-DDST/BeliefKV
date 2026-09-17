# BeliefKV 当前架构与实现状态

更新日期：2026-09-17
当前 P6 代码基线：`c2fb3a4`

本文只记录当前事实和下一阻塞项，不再追加逐日开发日志。2026-09-12 以前的完整历史保存在
`docs/archive/snapshots/architecture_status_zh.md`，单次实验细节保存在
`docs/experiments/`。

## 1. 当前结论

BeliefKV 当前由两部分组成：

- P5：已接入在线路径的 observed-state Agent/KV JointPlan；
- P6：已将异步预测输出接入统一 JointPlan action group，代码路径同时覆盖
  predictive execution/admission 排序、`PREPARE_HOST`、真实 deficit 下消费 prepared
  victim 的 `COMMIT_CPU`，以及 latest-start `PREFETCH_GPU`。初步 GPU 高压对比未
  获得吞吐提升，当前瓶颈是预测信息没有稳定转化为 work-conserving execution set 和
  可隐藏的迁移动作。

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
| FrontierBelief | v6 development-only | PREPARE/PREFETCH 独立校准；`online_eligible=false` |
| Predictive execution/admission | 已接入 | 仅使用校准 token demand 做 SRPT/HBM 排序；RCCG/observed seed 保留因果优先级，native allocator 最终验收 |
| Predictive `PREPARE_HOST` | 已接入 | D2H 后保留 GPU KV，并建立 beneficiary-victim binding |
| Beneficiary-bound `COMMIT_CPU` | 已接入 | 只由真实 deficit 授权，优先消费 prepared victim |
| Predictive `PREFETCH_GPU` | 控制面与数据面已接入，收益未验证 | 支持完整、ancestor-closed partial 和 commit-ready victim 资助的 prefetch；development artifact 需显式 override |
| `RECLAIM_AND_PREFETCH` | development canary | `COMMIT_CPU(victim) ACK -> H2D(target) ACK -> service lease`；尚无自然在线闭环证据 |
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

在线预测权限已收敛为 action-minimal 接口：execution/admission 只消费 remaining decode、
next output 和实际 startup/growth demand；`PREPARE_HOST/PREFETCH_GPU` 只消费 live transfer
`tau` 下的 wait/reentry survival、prompt/KV growth 与 RCCG dependency。boundary rare-class 和
tool-terminal 分类继续记录和评估，但不再参与排序、support gate 或物理动作授权。需求 head
不可用时保持 observed seed 顺序，不使用请求 ID 构造新的预测顺序。

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

### 4.5 2026-09-16 时间线与预测接口审计

已有 baseline/predictive trace 的独立时间线显示：

- mean running requests：25.74 / 14.73；
- GPU utilization mean：8.79% / 7.98%；
- DMA 与 GPU busy 重叠：18.79% / 13.06%；
- predictive 侧只有 6 个 intent、1 个 commit，未形成可测 saved stall。

因此当前 predictive arm 没有改善流水线，不能作为 P6 收益证据。详细结果见
`docs/experiments/beliefkv_p6_execution_timeline_and_action_interface_2026-09-16_zh.md`。

FrontierBelief v4 已改为 PREPARE/PREFETCH 动作专属校准。为快速排除旧 runtime 数据分布
问题，又使用最新 40-root observed baseline 训练了 development-only v5 adaptation。v5 在
独立 calibration 上的 PREPARE/PREFETCH Brier skill 为 +3.20%/+15.80%，PREFETCH recall@0.5
从 14.72% 提升到 20.34%；boundary 对 `spawn/final` 的 recall 仍为 0。v5 明确保持
`online_eligible=false`，同 workload 结果不能作为泛化证据。详细结果见
`docs/experiments/beliefkv_p6_baseline_adaptation_2026-09-16_zh.md`。

### 4.6 PREFETCH 召回与执行闭环

在不采集新 GPU trace 的前提下，当前 64-train、40-workflow baseline adaptation 和独立
16-workflow calibration 已重建为 development-only v6。PREFETCH 不再使用固定 0.5 阈值，
而是在 calibration 上以 F2 和 precision floor 选择动作阈值：

- 阈值：0.1850；
- recall：20.34% -> 64.67%；
- precision：68.93% -> 36.17%；
- Brier skill：+15.80%；
- calibration 正例率：17.29%。

该阈值只扩大语义候选召回，不能绕过物理 closure、HBM、transfer、latest-start 和净收益
门禁。候选器会检查前四个 target。固定 5% HBM 单动作上限已经删除；目标超过实时 free HBM
时，可以选择 ancestor-closed partial prefix，或由一个 commit-ready `DUAL_CLEAN` victim 为
target 提供容量。所有路径仍受实时容量、closure、latest-start 和净收益约束。

PREFETCH H2D ACK 后建立有界 service lease：目标 request 在首次真实 GPU service 前获得
admission 优先级，并暂时不能成为反向 eviction victim。lease 在首次 service、request
终止、identity/epoch 变化或 5 秒到期时释放。该机制避免 H2D 成功后立刻被迁出或长期得不到
service，但不提供无限 HBM reservation。详细实现与证据见
`docs/experiments/beliefkv_p6_prefetch_recall_enablement_2026-09-16_zh.md`。

### 4.7 当前预测头质量

以下结果来自 v6 artifact 在冻结的 16-workflow calibration split 上的重放；`test_id` 仍封存。
分类 accuracy 必须与多数类基线一起读，区间 head 使用 episode-weighted MAE 和 coverage：

| Head | Held-out calibration | 当前用途 |
| --- | --- | --- |
| Boundary type | accuracy 94.96%，与多数类基线相同；macro recall 33.33%；`spawn/final` recall 均为 0 | 不能驱动 execution reorder；只使用 RCCG 已观测事件 |
| Tool terminal | accuracy 79.63%，与多数类基线相同；macro recall 33.33%；`error/censored` recall 均为 0 | 不能预测失败类型，只作保守 survival 输入 |
| Next output | MAE 178.64 tokens；point coverage 86.55%；workflow-macro episode coverage 88.86% | 仅作有不确定性的短期 demand |
| Prompt growth | MAE 2,024.10 tokens；point coverage 93.88%；workflow-macro episode coverage 93.33% | 可作保守 HBM growth envelope |
| Remaining decode | MAE 436.67 tokens；point coverage 93.02%；workflow-macro episode coverage 91.44% | 可作粗粒度 service demand，不能精确 run-to-boundary |
| PREPARE operational tau | Brier 0.0601，skill +3.20%；balanced accuracy 49.99% | 几乎等同高正例率先验，不能单独授权动作 |
| PREFETCH operational tau | Brier 0.1204，skill +15.80%；阈值 0.185 时 precision 36.17%、recall 64.67% | 有增量信息，但必须经过物理与价值门禁 |
| JOIN/WAIT_CHILD | 不直接学习 wall-clock；由 RCCG 对 child scenario 做 ALL/ANY 组合 | 依赖 child head，尚无独立 end-to-end accuracy |
| WAIT_MESSAGE | 当前 artifact 无独立模型 | unsupported/OOD，不驱动动作 |

Required-head unavailable rate 只有 0.108%，但这只表示模型能返回结果，不代表精度高。WAIT_TOOL
动作支持几乎全部来自层次 backoff：约 66.3% 为 role-command backoff、19.5% 为
role-family backoff、14.0% 为 role-state backoff。exact incremental boundary 仍为 0%，因此不支持
early dispatch 或 run-to-action。

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

### 5.1 2026-09-16 prediction-to-action 时序修复

当前工作区已将 PREPARE 动作形成改为同一 safe point 内的事件对齐流程：

- `TOOL_START`、`WAIT_JOIN/WAIT_CHILD` 先发布轻量 semantic delta，不再携带旧 hint
  直接执行 PREPARE risk evaluation；
- semantic event 不再受 100 ms progress coalescing 限制，最迟在下一次 5 ms policy check
  进入 worker mirror；
- runtime 使用一次性 PREPARE latch，随后绑定本轮最新 bounded seed beneficiary，并强制
  重建最多两个 parked victim 的 action-local overlay；
- beneficiary/HBM bucket 不变也会提交一次事件对齐 risk evaluation，成功提交后才消费
  latch；没有 beneficiary 时保留 latch；
- pressure/full-plan 与 agent event 同批出现时，不再使用旧 hint 重复执行 PREPARE；
- 新增 `prepare_event_alignment_latched`、`prepare_event_aligned_hint_published`、
  `prepare_event_aligned_risk_published` 和 `prepare_event_to_hint_publish_ms` 观测。

CPU 回归为 `272 passed, 2 deselected, 8 subtests passed`；两项 deselected 仅依赖当前
shell 缺失的 `CUDA_HOME`。GPU 上的 event-to-hint、hint-to-risk 和
validation-to-latest-start 已完成短高压 gate 验证：40-root 运行中 HBM >= 80% 持续
322.56 秒，315 次 risk evaluation 产生 38 个正收益候选、37 个 timely fresh positive
package，最终 8 次选择 PREPARE，其中 7 次在 latest-start 前完成验证。worker 为
515 submitted / 515 completed / 0 failed / 0 pending。详细结果见
`docs/experiments/beliefkv_p6_event_aligned_high_pressure_shadow40_2026-09-16_zh.md`。

## 6. 当前阻塞项

1. prediction-to-action utilization gap 尚未闭合。初步 H200 高压运行中 predictive arm
   的 running set、GPU utilization 和 DMA/GPU overlap 均低于 baseline。
2. 当前 bounded composer 只评估前 2--4 个 request，并将最终物理候选限制为
   `1 beneficiary x 2 victims`。这是在线成本边界，不是全局最优保证。
3. boundary 和 tool-terminal head 尚未优于多数类基线；predictive execution 不得把
   94.96%/79.63% 的表面 accuracy 当作有效 action-unlock 信号。
4. `PREFETCH_GPU` 的完整、partial 和 funded 路径已实现，但 v6 仍是 development-only。
   calibration recall 为 64.67%，线上 precision、latest-start、first-service 和吞吐收益均未验证。
5. prepared binding 目前每个 beneficiary 只保留一个 victim；失效时回退 P5，不执行
   预测性 COMMIT。
6. v7 GPU service artifact 适合排序和 shadow；正式吞吐结论必须来自与冻结 observed
   baseline 相同 workload/profile 的 GPU A/B。
7. event-aligned 高压 shadow 已证明 `validation_ts < latest_start_ts`：37 个及时正收益
   package，最小 margin 76.09 ms。但 event-to-hint P95/P99 仍为 889.74/3236.16 ms，
   其中包含等待 beneficiary 出现的时间；提交端必须继续执行 latest-start 复验。
8. 高压 safe-point capture P95/P99 为 0.692/1.393 ms，略高于严格的 0.5/1 ms 目标；
   predictive planning P95/P99 已降至 43.81/59.50 ms。同步尾延迟仍需观测，但不再阻塞
   单笔 canary。
9. 2026-09-16 funded-prefetch gate 暴露 12 个 PREPARE 假阳性：典型 beneficiary 约
   21 ms 后阻塞，而 D2H P95 约 838 ms；单 victim 约 67 MB 也不足以覆盖约 416 MB deficit。
   `0ab8c09` 已在发布 intent 前拒绝过晚和回收不足的 package。
10. 修复后 bounded 在线复验运行约 20 分钟，1,300 个 hint 中 880 个容量充足、420 个仅受
    running slot 限制，原生 KV usage 约 31%，因此 0 victim、0 risk evaluation、0 prefetch。
    该轮证明假阳性消失，不证明预测调度收益。

## 7. 下一步

当前关键路径：

1. 冻结一个能让 32 个 active context 自然形成更大 unique KV working set 的长上下文 workload；
   继续增加 waiting root 不会提高 resident KV，禁止通过缩小 KV pool 制造机会。
2. 在该 workload 上重新运行 development canary。动作提交时必须重验 certificate、物理
   closure、实时容量、transfer envelope 和 latest-start；晚到、回收不足或负收益动作回退 P5。
3. PREFETCH gate 必须覆盖 `intent -> H2D -> ACK -> service lease -> first GPU service`，
   并统计在线 precision、recall proxy、H2D 后 first-service latency 和 5 秒内反向迁移。
4. 同时继续记录 event-to-hint 长尾和 safe-point P95/P99，不为降低开销重新关闭必要的
   `TOOL/JOIN` 风险触发。
5. execution 排序只使用经 calibration 证明有增量信息的 head；当前 boundary rare class
   需要补数据或改为 RCCG 已知 unlock + token/HBM demand 排序。
6. Gate 中任何 stale/OOD/物理化失败均回退 P5；不通过降低收益阈值制造动作。
7. PREPARE/PREFETCH 各自完成真实 beneficiary 消费后，再按 execution reorder、提前
   D2H、deficit-time COMMIT 和 latest-start H2D 分解收益，最后启动冻结 baseline/P6 A/B。

执行细节见 `docs/implementation_plan.md`。

## 8. 权威资料

- 当前设计：`docs/beliefkv_design.md`
- 当前计划：`docs/implementation_plan.md`
- 文档导航：`docs/README_zh.md`
- 当前图解：`docs/beliefkv_jointplan_visual_zh.md`
- 历史状态：`docs/archive/snapshots/architecture_status_zh.md`
- 实验报告：`docs/experiments/`
