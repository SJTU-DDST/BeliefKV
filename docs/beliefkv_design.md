# BeliefKV 当前系统设计

更新日期：2026-09-17

状态：本文是当前算法与系统边界的权威说明。历史版本保存在
`docs/archive/snapshots/beliefkv_design_2026-07-14_zh.md`。

## 1. 研究场景与目标

BeliefKV 面向单 GPU、HBM 受限的动态 Agent 工作流：

- 多个 root workflow 并发运行；
- workflow 在运行时产生工具调用、FRESH subagent、RETURN、JOIN 和 peer message；
- 系统不要求应用预先提供完整 DAG；
- GPU KV、CPU KV 和 raw-token recompute 可以共同参与容量管理；
- SGLang RadixCache/HiCache 仍是物理 KV 与 allocator 的唯一事实源。

优化目标按优先级为：

1. successful workflows/hour 和 action throughput；
2. GPU service utilization 与有效 batch；
3. action unlock、reentry 和 admission stall；
4. D2H/H2D、recompute 与控制面开销。

Workflow fairness 只作为有界防饿死和最终 tie-break，不以平均分配 GPU 时间为目标。

## 2. 当前核心设计

![BeliefKV 请求与 KV 联合调度](figures/beliefkv_joint_algorithm_overview.svg)

BeliefKV 使用两个相互正交的状态视图：

- RCCG 描述 Agent 的因果和执行关系；
- PageIndex/Radix 描述 KV 页的物理共享、驻留、锁和 generation。

二者只能在 JointPlan 中结合：

```text
runtime events -> RCCG causal frontier
                          \
                           -> JointPlan -> admission tickets -> GPU batch
                          /
Radix/PageIndex -> physical bundles
```

JointPlan 同时决定：

- 哪些 runnable request 优先获得 GPU service；
- 哪些 waiting request 获得当前 batch epoch 的 admission ticket；
- 哪些 KV 保留、迁移、恢复或丢弃；
- admission 是否依赖某笔 reclaim/restore ACK；
- 是否需要 selective running retraction。

## 3. P5：Observed-State JointPlan

### 3.1 请求调度

P5 只调度已经由 RCCG 证明 runnable 的 invocation。正常排序综合：

1. starvation floor；
2. JOIN straggler 和下游 action unlock；
3. causal MaxWeight utility；
4. resident-ready bytes 与 startup cost；
5. workflow fairness tie-break。

系统不限制每轮每个 workflow 只能选择一个请求。同一 workflow 的多个 child 可以在资源
允许时共同进入 batch。

请求始终保留在 SGLang visible waiting queue。BeliefKV 生成短期、单 epoch 有效的
AdmissionTicket；没有 ticket 或等待 restore 的请求在本轮被跳过，但不能阻塞后续可运行请求。

同步路径使用 O(K) bounded seed，复杂语义规划在异步 worker 中运行。计划必须在 scheduler
safe point 重新验证，SGLang PrefillAdder 和 allocator 保留最终否决权。

### 3.2 Residency 分类

| 类别 | 含义 | 默认处理 |
| --- | --- | --- |
| `PINNED` | running、engine lock、active reader 或事务占用 | 不迁移 |
| `IMMINENT` | 已 ready 或即将 reentry | 保留或优先恢复 |
| `PARKED` | WAIT_TOOL/CHILD/JOIN/MESSAGE | 可作为 victim |
| `DEAD_UNOWNED` | 无未来 owner | 直接释放 |

共享页使用所有 owner 中最强的保护等级。逻辑 context 不能直接作为迁移单位；系统必须先解析
为包含共享页和 ancestor closure 的 PhysicalBundle。

### 3.3 当前 HBM offload 是响应式的

真正释放 HBM 的 `COMMIT_CPU` 由已经可观测的 beneficiary HBM deficit 触发：

```text
deferred beneficiary
  -> startup + restore + growth deficit
  -> select PARKED/DEAD physical victims
  -> COMMIT_CPU(victim) or DROP(victim)
  -> completed reclaim ACK
  -> ADMIT(beneficiary)
  -> first real GPU service
```

高 HBM 使用率本身不是迁移理由。系统要求存在明确 beneficiary，并且估计的 saved admission
stall 大于 D2H、未来 restore/recompute 和干扰成本。

### 3.4 Restore 与活性

Running retraction 使用 durable RestoreTransaction：

```text
WAIT_FEASIBILITY -> WAIT_FUNDING -> READY_TO_COMMIT
 -> H2D_QUEUED/ADOPTED -> H2D_ACKED
 -> ADMISSION_RESERVED -> ADMITTED
 -> SERVICE_GRACE -> SATISFIED
```

普通 waiting prefix 不得升级为全局 restore barrier。显式 H2D 不可行时回退到 SGLang
native demand-load；Host copy 不可用时允许从 raw tokens 重算。Restore 只有在请求重新获得
至少一个真实 GPU service quantum 后才完成。

## 4. 物理 KV 数据面

- SGLang/Radix/HiCache：真实 KV、allocator、DMA 和 lock 的 owner。
- PageIndex：按 `(page_id, allocation_generation)` 维护物理镜像。
- PhysicalBundle：共享页和 closure 完整的迁移单位。
- Transfer transaction：D2H/H2D/DROP 的提交、ACK 和终态。
- JointPlan：只表达语义动作和依赖，不直接修改 tensor。
每个物理动作都必须在 safe point 检查：

- context/request epoch；
- Radix ownership、ancestor closure 和 allocation generation；
- engine lock、semantic pin、active reader；
- HBM/Host 实际容量；
- transfer capability 和 in-flight conflict；
- beneficiary、deadline 和动作证书。

## 5. P6：FrontierBelief 预测旁路

P6 不建立第二个调度器。它在 P5 bounded seed 上识别 deferred beneficiary 和 parked victim，
用 FrontierBelief 生成 action-local scenarios，再把合格的 PredictiveIntent 合并回同一个
JointPlan。

预测目标是需求和因果窗口，而不是旧负载下的 wall-clock GPU 时间：

- WAIT_TOOL：按 tool/backend/command class 的 competing-risk survival；
- WAIT_CHILD/JOIN：由 RCCG 组合 child 的多轮 LLM、工具等待和 completion；
- WAIT_MESSAGE：producer dependency；
- prompt/output/KV growth；
- 等待窗口能否覆盖迁移时间 `tau`；
- beneficiary 的 projected future HBM deficit。

当前 development 在线预测路径支持：

- `PREPARE_HOST`：D2H 建立 CPU shadow，GPU KV 继续保留；
- `PREFETCH_GPU`：按实时 free HBM 恢复完整 context；
- `PARTIAL_PREFETCH_GPU`：完整 context 放不下时恢复 ancestor-closed prefix；
- `RECLAIM_AND_PREFETCH`：只消费已经拥有完整 CPU shadow 的 commit-ready victim，严格执行
  `COMMIT_CPU ACK -> H2D target ACK -> service lease`。

`PREPARE_HOST` 的 intent、safe-point rematerialization、D2H、ACK 和 terminal 机制门禁已经
通过；自然 workload 中尚未证明稳定吞吐收益。固定 5% HBM 单动作上限已经删除，容量安全由
实时 allocator/closure 证书和 safe-point rematerialization 保证。OOD、证书过期、物理形状
不支持、动作晚于 latest-start、单 victim 无法覆盖 deficit 或收益不足时必须回退 P5。

### 5.1 预测质量与动作权限

FrontierBelief v6 仍是 `development_only`，且 artifact 明确设置
`online_eligible=false`、`predictive_action_eligible=false`。显式 development canary 可以验证
prediction-to-action 机制，但不能形成正式性能结论。

当前各 head 的可用边界是：

- prompt growth 与 remaining decode 的校准区间 coverage 约为 93%，可提供粗粒度需求包络；
- PREFETCH operational-tau head 的 Brier skill 为 +15.80%，阈值 0.185 时
  precision/recall 为 36.17%/64.67%；
- PREPARE operational-tau head 只有 +3.20% Brier skill，balanced accuracy 约 50%，不能
  单独授权 D2H；
- boundary 与 tool-terminal 的 accuracy 分别为 94.96% 和 79.63%，但与多数类基线相同，
  `spawn/final` 与 `error/censored` recall 均为 0；
- JOIN 不学习独立 wall-clock，由 RCCG 组合 child scenarios；WAIT_MESSAGE 尚无独立 head；
- exact incremental action boundary 仍不可用。

因此系统当前“支持预测动作的安全执行”，但尚不支持“由所有预测头稳定提升吞吐的高效预测
调度”。execution ordering 必须依赖 RCCG 已知状态和 token/HBM demand；弱分类 head 只能作为
审计信号。PREPARE/PREFETCH 还必须经过 action-specific timing、beneficiary、physical closure、
capacity 和净收益门禁。

当前在线权限实现为 action-minimal v1：运行中请求按校准后的 remaining-decode 中位数排序，
waiting request 按 remaining-prefill + next-output demand 排序，并以 live HBM demand 和 observed
seed rank 作后续排序键。boundary/tool-terminal 不再属于 SCHEDULE required heads。该收敛减少了
多数类分类器对 JointPlan 的错误控制，但不会提高原始预测准确率；PREFETCH 的 precision/recall
仍必须按 held-out calibration 如实报告。

## 6. 未来可选方案：Predictive Eviction

### 6.1 动机和当前缺口

当前 P6 实现的是 predictive transfer/shadowing，不是 predictive eviction：

```text
PREPARE_HOST: GPU_ONLY -> GPU_AND_CPU_SHADOW
COMMIT_CPU:   GPU_AND_CPU_SHADOW -> CPU_ONLY
```

前者提前消除未来 D2H 成本，但不释放 HBM。当前真正释放 HBM 的 COMMIT 仍由已发生的
beneficiary deficit 响应式触发。如果竞争工作中的主要收益来自提前释放 HBM，BeliefKV
当前不能声称已经覆盖该收益来源。

### 6.2 可选的两阶段算法

未来可以在不改变现有安全边界的前提下增加 `PREDICTIVE_COMMIT_CPU`：

1. PCIe 有余量且预测等待窗口足够长时执行 `PREPARE_HOST`；
2. CPU shadow 完整后持续观察 projected HBM timeline；
3. 在 `latest_safe_commit_time` 到达时重新评估；
4. 满足风险和收益门槛才解除 GPU residency；
5. 预测失败时使用已有 H2D、native demand-load 或 recompute 恢复。

提交条件至少包括：

```text
shadow_complete
AND victim is still PARKED and unpinned
AND P(wait remains open beyond commit/restore horizon) >= p_min
AND projected_hbm_deficit > 0
AND concrete/projected beneficiary exists
AND expected_saved_stall
    > expected_restore_or_recompute_debt + transfer_interference + risk_margin
AND live causal/physical certificate is fresh
```

### 6.3 与现有 P6 的关系

该方案复用现有 FrontierBelief、beneficiary-bound package、latest-start、PhysicalBundle 和
safe-point transaction，不新增独立 eviction scheduler。建议把它保留为未来可选实验分支，
而不是当前论文主张，直到满足以下门槛：

- 现有 `PREPARE_HOST` 在自然 workload 中出现可归因的 useful shadow；
- trace 中存在“响应式 COMMIT 已经太晚”的 admission stall；
-离线/影子重放显示 predictive commit 相比 reactive commit 有稳定正收益；
- 单动作 canary 没有显著增加反向 H2D、recompute 或 HBM-time 浪费。

必须报告 useful/wasted commit bytes、提前释放的 HBM-time、beneficiary saved stall、
restore/recompute debt、方向反转率以及最终 workflows/hour。

## 7. 当前实现边界

| 能力 | 当前状态 |
| --- | --- |
| 动态 RCCG 与 FRESH subagent/JOIN | 已实现 |
| Visible-but-gated admission | 已实现 |
| P5 beneficiary-bound reactive offload | 已实现 |
| Running retraction 与 transactional restore | 已实现，持续做 GPU 回归 |
| P6 action-local prediction 与风险规划 | 已实现 |
| Predictive `PREPARE_HOST` | 机制已验证，自然收益未证明 |
| Predictive `PREFETCH_GPU` | 完整/partial/funded 路径已实现；仅 development canary，收益未验证 |
| Predictive `RECLAIM_AND_PREFETCH` | 已实现 staged transaction；自然闭环未验证 |
| Predictive `COMMIT_CPU` / eviction | 未实现，未来可选 |
| Peer multi-agent 专项优化 | 非当前关键路径 |
| Oracle action-space 优化 | 已暂停，仅保留诊断资产 |
| Morphology 独立策略 | 已降级；shape 仅作 transfer cost/OOD 输入 |

## 8. 不变量

- 不要求预定义 DAG，但要求最小运行时因果事件。
- 不根据 prompt 文本猜测 parent/child 身份。
- SGLang allocator 和 Radix 始终具有最终权威。
- 计划字节不能代替 completed ACK 的实际释放字节。
- 预测动作不能绕过 P5 correctness 和 liveness。
- 训练目标不使用 batch size 或旧调度策略污染的 GPU wall-clock。
- 正式性能比较必须使用相同 workload、模型、runtime profile 和 instrumentation。

## 9. 当前实验环境

- GPU：NVIDIA H200 NVL，单卡；
- 模型：Qwen3-Coder-30B-A3B-Instruct BF16；
- SGLang：0.5.2rc1，固定上游提交与 BeliefKV patch；
- context limit：262,144，正式验证覆盖到 196,608；
- KV pool：850,000 tokens；
- Host KV pool：96 GiB；
- max running requests：32；
- CUDA Graph batch：1/2/4/8/16/24/32。

具体冻结值以 `configs/p6/h200_bf16_v7/frozen_runtime_profile.json` 为准。

## 10. 代码入口

- RCCG：`beliefkv/control/causal_graph.py`
- JointPlan：`beliefkv/policy/joint_scheduler.py`
- Admission：`beliefkv/policy/admission.py`
- Residency：`beliefkv/policy/residency.py`
- FrontierBelief：`beliefkv/predictor/structured_frontier.py`
- Predictive risk：`beliefkv/policy/risk_shadow.py`
- PageIndex/Bundle：`beliefkv/runtime/page_index.py`、`beliefkv/runtime/bundles.py`
- SGLang bridge：`beliefkv/runtime/sglang_v052rc1.py`
- Restore transaction：`beliefkv/runtime/restore_obligation.py`

## 11. 文档权威顺序

1. `docs/README_zh.md`：统一导航和生命周期。
2. 本文：当前算法与系统设计。
3. `docs/architecture_status_zh.md`：当前代码与实验状态。
4. `docs/implementation_plan.md`：当前执行顺序。
5. `docs/experiments/`：不可变实验依据，不直接代表当前设计。
6. `docs/archive/`：历史计划和旧权威文档快照。
