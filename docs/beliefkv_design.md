# BeliefKV 当前系统设计

更新日期：2026-09-27

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

### 1.1 当前研究聚焦：动态中高压力

论文主场景选在**HBM 竞争真实存在、但尚未被计算或 Host 容量彻底压垮**的
动态多 workflow 负载：既有 runnable backlog，也有工具等待和 child JOIN，
且 Host 中存在可恢复 KV、HBM 中存在可安全回收的低价值驻留页。这是待检验的
工作区间，不以 root 数量、GPU 利用率或单个 usage 比例定义“内存瓶颈”。
以同一 workload 的 64+64 两波到达作为候选压力配置，最终按实际
recompute、Host/Device hit、排队、可回收容量和传输重叠机会确认。
低压组验证是否能用闲置 HBM/PCIe 隐藏传输；极端过载组验证退化到响应式
策略是否安全，不以极端过载下的虚假预取次数作为主要结果。

在这个工作区间，同时研究两种**可能互相竞争**的收益来源：

1. 等待工具/JOIN 的 parent 在将重新可运行之前恢复有用 KV，减少 reentry
   等待；如果 parent 是关键路径，允许其有界占用 HBM，必要时替换冷 KV。
2. 高压时把执行选择和 KV 准入/回收联合起来：优先推进能实际解锁下游、
   尽快完成 workflow 并释放容量的工作，但将其它 workflow 的 JCT 尾部、
   饥饿和重算债务计入机会成本，不能简单地固定一个 workflow 跑到底。

主指标为同配置下成功 workflow 吞吐和 JCT 分布，同时报告最慢 workflow、
任务正确性、GPU 服务、重算、Host/Device hit、HBM 占用时间及公平性。
若 GPU 计算始终满载、或 Host 已把可复用 KV 大量丢弃，预取可能净负收益；
策略应降低预测动作强度并保留 P5 的活性/正确性回退，而非强行制造 H2D。

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

以下 schema-v5、校准精度和动作权限是**原 Qwen3-Coder/SGLang 0.5.2rc1
基线**的描述；迁移后的 Qwen3.5/SGLang 0.5.20 不继承其精度、物理收益或
`predictive_action_eligible` 资格。新版具体进度见架构状态页。

预测目标是需求和动作相关因果窗口，而不是旧负载下的 wall-clock GPU 时间。schema-v5
使用一个版本化 artifact 发布三类局部分布：

- `OperationalReleaseModel`：直接拟合
  `P(tool release <= live transfer tau | elapsed, role, tool, backend, command, context)`；
- pooled conditional demand：remaining decode、next output、prompt growth；
- pooled conditional classification：boundary 与 tool terminal，稀有类训练后恢复真实类先验；
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

FrontierBelief schema-v5 使用 64 个冻结 train workflow 拟合，并在 7 个 train project 内做
LOPO 选参；16 个 repository 隔离的 calibration workflow 只用于概率和区间校准，`test_id`
仍封存。artifact 仍明确设置 `online_eligible=false`、`predictive_action_eligible=false`，直到
真实长任务完成 latest-start、物理闭环和吞吐门禁。显式 development canary 可以验证
prediction-to-action 机制，但不能形成正式性能结论。

当前各 head 的可用边界是：

- remaining decode calibration MAE 为 384.54 tokens，比 v6 下降约 11.9%；next output 与
  prompt growth MAE 为 175.84/2,002.41 tokens，继续以校准区间进入资源场景；
- PREFETCH operational-tau Brier skill 为 +45.67%，动作阈值 precision/recall 为
  59.66%/90.56%；
- PREPARE operational-tau Brier skill 为 +19.21%；高置信度 precision/recall 为
  99.90%/76.51%；
- boundary top-2 accuracy 为 99.67%，FINAL/SPAWN top-2 recall 为 97.72%/73.76%；
  top-1 仍受 tool 类 94.96% 先验支配，因此只进入 scenario composition；
- tool-terminal accuracy 为 81.75%，error recall 为 51.10%，不再退化为恒定 success；
- JOIN 不学习独立 wall-clock，由 RCCG 组合 child scenarios；WAIT_MESSAGE 尚无独立 head；
- exact incremental action boundary 仍不可用。

因此系统已具备比 v6 明显更强的动作相关预测，不再由无关 head 或层次 backoff 统一门禁。
但“离线概率变准”仍不等于“在线吞吐提升”：execution ordering 必须保留 RCCG 确定性状态，
PREPARE/PREFETCH 还必须经过 beneficiary、physical closure、capacity、latest-start 和净收益门禁。

当前在线权限实现为 action-minimal v2：运行中请求按校准后的 remaining-decode 分布排序，
waiting request 按 remaining-prefill + next-output demand 排序，并以 live HBM demand 和 observed
seed rank 作后续排序键。boundary top-2 scenarios 可以估计 unlock 分支，但不属于物理动作的
单点 required head；tool-terminal 用于失败风险，不替代 operational-tau。每个动作只消费其
需要的预测分布，避免恢复 composite OOD 一票否决。

### 5.2 新版的两层预测与验收目标（待实现/验证）

**时间头**仍单独评估工具 release 与完整 JOIN 的预测：使用项目隔离的
自然事件，按触发阶段、压力桶报告覆盖、误报、条件 P50/P90 误差和删失，
不只在事后成功的长窗口子集计算精度。JOIN 不等同于单个 child RETURN；
有多个未完成 child 时需按实时 blocker 集合组合；只剩最后一个 child 时
可把结构化终态提示作为近端信号，但不能把其发出视为已确认 RETURN。
继续追求有用的亚秒级时间精度，但不能把该阈值当成每次预取获益的必要条件。

**动作头**预测在当前可见状态下某笔迁移的条件收益，而非旧 P5 调度下
绝对 wall-clock JOIN 时间。令 `R` 为 JOIN 真正满足、parent 可提交的时刻，
`C` 为同一物理 KV/epoch 的有效 H2D ACK：完全隐藏传输要求 `C <= R`，
且 `R-C` 不超过受 HBM 机会成本约束的驻留预算；部分完成只计实测可节省的
等待。即便 `C <= R`，若没有被首次服务实际消费或挤掉更高价值 KV，也
不得算 useful。受益必须由 request 级 ACK/extent、实际首次服务及同配置
reactive 对照验证；不能拿 reactive 的长排队窗口当作预测式 H2D 的节省。

parent 的关键路径价值以剩余 blocker、下游解锁、当前工作流进度和真实
可回收物理 KV 表示，而不是“所有 parent 永远高优先级”。同一 JointPlan
比较空闲空间、可安全置换的冷页以及其它 runnable 工作；只在可用 Host
副本、closure/owner/epoch 与容量证书成立、预计解锁收益覆盖搬运/置换/
未来 restore 或 recompute 成本时，给 parent 短期驻留租约。租约有
字节/时间预算、到期或状态变化撤销、防饿死界限；ACK 未到不得按已恢复
KV 准入。JOIN 之外的 admission handoff 也按相同规则，在选出下一
beneficiary 后先预留空间、协调 D2H/H2D，再给执行 ticket。

child RETURN 时间拆解为可观测的执行阶段/剩余 GPU 工作、未来 GPU
服务份额、排队和工具执行，不把未来排队时长作为在线特征。训练时可以
用有身份关联的 request 服务事件重建**实际发生的**服务时间和排队时间，
检验“剩余工作/服务需求”头是否跨压力更稳健；工具等待对真实 RETURN
仍有贡献，不能一律从标签中删除。预测调度会改变服务分配，需在
reactive 与 predictive 下分别校准/按压力分层，之后只用历史已确认事件
滚动校正；在线更新必须经过因果时序、漂移及安全回退门禁。
目前 JOIN 标签仍是墙钟 RETURN 差，分头方案**不是**已验证的精度提升。

先做离线可识别性与 shadow 策略评价，再做真实物理闭环和配对 canary。
不基于正在运行的密封留出集调整压力阈值、ETA、模型或准入门禁。

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

## 7. 实现边界（旧 P6 路径及新版缺口）

下表前四条和预测动作机制沿用旧 Qwen3 P6 的能力描述；
**不能**据此认定 Qwen3.5 已获准在线预测动作。新增的新版
研究项独立标记为未实现或待验证。

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
| 新版有界关键路径 parent 驻留/抢占 | 设计目标，尚未实现或经 GPU 验证 |
| 新版 child 工作/服务/排队分解与在线更新 | 待训练侧可识别性验证，尚未上线 |
| Peer multi-agent 专项优化 | 非当前关键路径 |
| Oracle action-space 优化 | 已暂停，仅保留诊断资产 |
| Morphology 独立策略 | 已降级；shape 仅作 transfer cost/OOD 输入 |

## 8. 不变量

- 不要求预定义 DAG，但要求最小运行时因果事件。
- 不根据 prompt 文本猜测 parent/child 身份。
- SGLang allocator 和 Radix 始终具有最终权威。
- 计划字节不能代替 completed ACK 的实际释放字节。
- 预测动作不能绕过 P5 correctness 和 liveness。
- 不把旧策略的 wall-clock RETURN 误当调度不变的纯服务需求；未来服务/
  排队分头必须使用因果可观测的历史输入，并显式报告压力与策略分布差异。
- 正式性能比较必须使用相同 workload、模型、runtime profile 和 instrumentation。

## 9. 实验环境（下列为旧基线）

以下配置为 Qwen3-Coder/0.5.2rc1 的已冻结旧实验，**不是**
当前 Qwen3.5/0.5.20 密封评估的运行配置。新版实验参数以对应
`configs/migration/` 冻结合同与架构状态页为准。

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
