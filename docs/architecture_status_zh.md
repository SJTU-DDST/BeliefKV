# BeliefKV 当前架构与实现状态

更新日期：2026-09-22
当前 P6 物理执行基线：原 Qwen3-Coder/SGLang 0.5.2rc1；
Qwen3.5/v0.5.20 已有可选 native admission、工具等待预测和
JOIN child-completion 时间提示；单 node PREPARE/H2D 原生事务仍默认关闭，
尚无完整预测物理调度。

本文只记录当前事实和下一阻塞项，不再追加逐日开发日志。2026-09-12 以前的完整历史保存在
`docs/archive/snapshots/architecture_status_zh.md`，单次实验细节保存在
`docs/experiments/`。

## 模型与运行时升级（进行中）

迁移前已冻结 tag `checkpoint/pre-sglang-model-upgrade-2026-09-22` 和旧环境
清单。目标是 Qwen3.5-35B-A3B BF16 + SGLang v0.5.20，agent、SGLang、
BeliefKV 与实验工具安装在同一个 `beliefkv-next` conda 环境。
模型文件哈希和新环境包版本见 `configs/migration/`。新版 checkout 的
request metadata、scheduler 生命周期及 cache-mode 原生 ACK 观察 hook
已导出为
`patches/sglang-v0.5.20-beliefkv-staging.patch`，但 unified FULL/MAMBA
完整物理 ownership 和动作事务仍未迁移；完整 P6 物理
BeliefKV 仍 fail closed。新增 `--enable-beliefkv-admission` 独立开关
只打开有界的 admission；默认使用 observed-causal 顺序，合格 artifact
可额外提供同类预测 demand 排序，不打开物理动作。
新版 staging 在原生 prefill 前增加仅针对 tagged 请求的同步排序/许可
接口；未标记请求继续按 native order，最终 FULL/MAMBA 资源验收仍在
SGLang `PrefillAdder`。v0.5.20 plan producer 已在安全点接入：
接收可选的 agent 事件 socket、增量更新 RCCG，并以现有 causal frontier
重排首 512 个 tagged 请求；终止的 waiting 请求被清理，未能认证
的请求不会获得排序许可。没有匹配的因果事件则保持 native order。
可选 `--beliefkv-admission-predictor-path` 和
`--beliefkv-admission-predictor-sha256` 已接入该 staging scheduler：
仅在指定 event socket、artifact 精确 SHA-256、`calibrated` 且
`online_eligible=true`，并与目标模型 `config.json` 及声明的权重索引、
tokenizer 哈希和 SGLang
`0.5.20` semantic source contract 匹配时，才启动独立进程加载
FrontierBelief。安全点最多提交 8 个 ready tagged 候选，只非阻塞
收取有 support、非 OOD 的 next-output P50；结果须仍匹配 live
request/context/epoch/attempt、session 和 invocation revision，
且同一因果类全部候选均有有效 hint 才按短输出排序。失败禁用 worker，
无 hint 时保持 observed-causal/native 顺序。这是**有条件的预测
admission 代码路径**，不是已验收的 Qwen3.5 预测运行：
当前迁移目录未提供新模型校准且 online-eligible 的 predictor artifact；
旧 rc1/development artifact 不可沿用。COMMIT 的独占 reclaim 证明、
child/parent reentry 策略、预测动作授权与服务归因仍缺失。
工具期 `TOOL_START` 现在可异步提交 P10/P50/P90 剩余时间预测；
worker 在 idle scheduler 上有结果 fd 唤醒。live `WAIT_TOOL`、session
generation、invocation revision、模型哈希和有效期须在结果消费时
重验。工具结果可在 admission 请求排队时优先保留，但当前仅触发一个
context 的有界、只读 FULL/MAMBA 祖先闭包检查（最多 64 node），
统计未备份的 token/node，并不签发 D2H 迁移命令。候选过期和 session
失效时清理；snapshot 没有原子 ownership 证明，不能在下一安全点
未经重验就作为动作证书。默认 runner 仍未启用 session 桥。
新增根优先的单 node Host-shadow 步骤选择、动作安全点再次读取物理
闭包，以及未提交物理命令的账本预留撤销。staging native cache 提供
受限单 node `prepare_host_shadow`：在 write_through、有效 FULL
session leaf、父节点 Host 连续且无 pending transfer 时才入队，
Host 不足不驱逐别的 Host KV，GPU KV 保留至 native ACK。
staging controller 现增加可选入队前 callback：在 native 确定主/辅助
Host slot 后冻结实际 FULL/MAMBA pool 数量和 sidecar DMA 总字节，
runtime 的 `issue_shadow_backup_step` 同安全点重验工具、session 与
node；回调登记物理账本后才允许入队。明确未入队则撤销预留，
被 native 接受后必须等同步 ACK 才确认 completion；拒绝时不能
释放非本次分配的 sidecar，也不能重复释放 native 预留。
**scheduler 尚未调用该入口**：当前 artifact 明确只有 admission
资格，尚无动作时序/收益证明和在线 physical gate；因此不能宣称
predictive PREPARE 真实执行，更没有提前 PREFETCH 或 COMMIT。
对称的 `prefetch_gpu_session_node` 原生入口与 runtime
`issue_prefetch_gpu_step` 已具备单 node H2D 的入队前登记和同步 ACK
核算：工具等待、经验证的 JOIN 等待或显式 action-eligible 的
READY admission session 可各自按身份重验 root-first 选择
CPU-only FULL/MAMBA node；原生动作重验祖先驻留和 node 创建身份，
FULL/MAMBA 设备槽不足时不抢占别的 GPU KV。MAMBA-only 和 derived
sidecar 字节也会计入预期。**scheduler 尚未调用 H2D 入口**，此代码
没有证明 online predictive H2D、首次服务收益或旧 checkpoint 等价；
JOIN 的子请求完成时间提示已有边际组合，但 child reentry 的
动态 latest-start、JOIN 自动派发与真实 deficit 触发的 COMMIT
仍需迁移。
启动脚本增加显式 `HICACHE_WRITE_POLICY` 与
`ENABLE_SESSION_RADIX_CACHE`，默认仍保持 write_back/关闭 session；
即使 opt-in 也不会自动开启 agent session 桥或物理调度。
新增 `scripts/promote_qwen35_admission_predictor.py` 只允许经
Qwen3.5/v0.5.20 冻结 train/calibration/test_id 证据和重放指标
晋升为 admission-only；旧 Qwen3-Coder artifact 及缺少目标数据的
环境不能凭此晋升，更不能把 admission 资格当 tool-wait/物理动作资格。
2026-09-22 使用固定 staging checkout、4 GiB HiCache、显式 admission
开关在 H200 完成单请求 smoke：tagged chat 返回 HTTP 200，非法
context epoch 返回 HTTP 400 且未挂起。该测试既未输入在线预测结果，
也未验证真实 Host 迁移或高压吞吐。
有界 plan 编译器在安全点把语义排序绑定到 request/context/
epoch/attempt 和原生 session ID/generation，失效授权会被拒绝；
它本身不做预测或容量准入；新版 runtime 的可选 worker 只提供
前述同类 demand 排序。
safe point 已改为原生 HiCache ACK 排空后再刷新 BeliefKV mirror/plan；
这只保证 ACK 的观察顺序，不构成预测动作的物理提交证书。
**下文所有 P5/P6 在线能力与旧实验结果仍仅指旧模型/旧 SGLang 合同**。
新模型原生服务或通过的 metadata 单元测试均不能视为预测式 KV 调度已迁移；
更换模型后还需新 baseline、容量和服务率标定，不能与旧模型吞吐直接比较。
当前新增的 unified cache 只读 observer 报告 FULL、MAMBA、Host
各自的容量上限及 live/used 计数；FULL/MAMBA device 容量共享一块
字节 buffer。这些计数不是可调度空闲/可回收容量，不能用于
PREPARE/COMMIT/PREFETCH 授权。现可对 Python `UnifiedTreeCore` 的一个
指定 node 及其祖先读取有界 FULL/MAMBA 驻留、锁和 pending 状态；Rust
tree 不支持该观察路径。节点快照没有原子 generation 或可转移性证明。
只读闭包额外区分 FULL/MAMBA 的 session 引用与叶标记数；
这些引用是可被驱逐的软保护，不是独占所有权，也不是物理命令授权。
新增按 session ID/generation 有界读取 FULL/MAMBA leaf anchor 的
原生接口；runtime 可在 tagged 请求完成后继续持有 context/session
绑定，并在工具等待期间按 context 查询；换 session、终止、取消或
epoch 失效时拒绝旧 anchor。只有启用 session radix cache 且成功
注册 leaf 的请求才有此路径；默认 runner 尚未启用该桥，
anchor 本身不证明共享 owner，也不触发预测迁移。
固定 v0.5.20 原生实现 radix 前缀共享与可选 session 引用保留，
**没有**与 agent TOOL 生命周期集成的自动 KV 保活，亦没有提前
Host -> GPU 的预测式恢复。原生 storage prefetch 为 storage -> Host，
原生 H2D load-back 在请求准入时发生。BeliefKV 仍须实现工具事件
到物理动作及 ACK 的完整事务；
具体边界见 `docs/v0520_scheduler_redesign_zh.md`。
现已加入显式 opt-in 的 agent-native-session 生命周期桥：根据 workflow/
context/epoch 生成隔离的 session ID，在 RETURN/CANCEL、WORKFLOW_END
和 epoch 更新时调用原生 `/close_session`；关闭失败不会忘记引用，
后续可以重试。默认 runner 未启用该桥，且原生 session 引用不保证
工具等待期间绝对保活，不能代替预测式 H2D。原生未标记的合并 ACK
仅有 node IDs 和 pool 总量；即使 `load()` 返回成功，H2D 也可能尚未
提交到 DMA，因此预测动作保持 fail closed。
staging native 路径现能对带 command ID 的 D2H/H2D 子操作记录
anchor、pool count 和总 bytes；只有 ACK 同步、tree finish，且全部
子 receipt 与 merged ACK 的 node IDs、pool 总量和 bytes 一致时，
才提供 `child_commits`。未标记 native 子操作仍计入合并校验，不能
归给 tagged command。本地 `PhysicalTransactionLedger` 可按预期的
child closure、方向、冻结的 FULL/MAMBA token bytes 和 live
context epoch/session generation 对账，整笔 children 齐备才返回 completion；过期、
重复、未知、缺失或不匹配的 receipt 均不授予部分 credit。
staging scheduler 在 admission 启用时已把
`UnifiedRadixCache.on_hicache_transfer_commit` 接到 runtime 的
`on_native_transfer_commit`；runtime 持有有界 ledger，定期过期
未完成期望、记录完成的对账结果，对账错误禁用后续物理 credit。
`register_physical_action` 只核验 live causal context/epoch 和
可见非终止请求并注册预期，**不派发物理动作**。这是 ACK
accounting 接线，而非可信预测物理事务：尚无动作局部共享
owner/独占 reclaim、原子 revision、锁及 split closure 的
ownership certificate，也没有动作 dispatch。提交时部分成功/
失败、取消、真实 beneficiary deficit 和 ACK/资源释放仍须封装
并重验；不能打开
`enable_beliefkv` 或宣称预测性 PREPARE/PREFETCH 可执行。

### JOIN 与准入前 H2D（2026-09-22 增量）

旧的工具等待 H2D 只覆盖 `WAIT_TOOL`；现在预测 worker 可根据
`JOIN_WAIT` 的未完成 child 的 `remaining_to_return_ms`，按 ALL=max、
ANY=min 组成 parent 的滚动 JOIN reentry 提示。成员缺失、OOD、训练
支持缺失时无可执行时间；结果必须匹配 join ID、mode、完整成员集合、
parent invocation revision、每个 child 的 state/revision/context epoch
及 parent session generation。child RETURN 或 JOIN 满足立即失效。
**边际分位数组合不等同于经校准的联合 JOIN 分位数**；目标模型需单独
校准和验证 JOIN 覆盖率，不能借用工具调用时长。
`refreshed_prefetch_gpu_step(source="join_wait")` 支持 JOIN parent 的单
node 本地闭包重验及原生 H2D 事务入口；当前没有 JOIN latest-start
与搬运服务率校准，也没有 scheduler 自动派发，故不声称在线 JOIN
预测传输已发生。

对于被 semantic admission 选中的 READY request，原生
`PrefillAdder` 之前可执行 bounded pre-admission H2D：仅在显式
`--beliefkv-admission-prefetch`、已校准且
`predictive_action_eligible=true` 的精确模型 artifact 下启用；
最多一笔等待 lease、每笔至多两个 node，在 waiting 保留请求，
native H2D ACK 后再做原生 FULL/MAMBA 准入。无 CPU-backed node、
提示过期、物理失败或身份变化时回退 native，不占用 running slot；
该选项默认关闭，现有晋升脚本的 artifact 一律不具备动作资格。
此处尚未证明能够压低 H2D stall，也未迁移旧 checkpoint 的完整
JOIN latest-start、COMMIT 和可归因收益链，需后续在线 gate。
冻结环境中的 `sglang 0.5.20` 目前以 wheel 形式安装，
`source_is_active=false`；直接运行 `python -m sglang.launch_server`
默认不会加载 staging checkout。原生启动脚本保留 wheel smoke 默认，
显式设置 `SGLANG_SOURCE_CHECKOUT=third_party/sglang-v0.5.20` 时会检查
固定 commit、补丁反向撤销可行性和实际 `sglang.__file__` 指向该 checkout，
再用它启动服务。未设置该变量的 GPU 测试不能用来验证本次新增 hook。
2026-09-22 在 H200 上以安装的 v0.5.20 wheel、Qwen3.5-35B-A3B BF16
完成不带 HiCache 及启用 4 GiB HiCache 两组原生
chat -> tool call -> tool result 续写 smoke；后者确认挂载了 KV + MAMBA
Host pool，但未施加足够负载触发实际 D2H/H2D。
smoke 显式关闭 thinking 避免短 `max_tokens` 全部用于 reasoning。
该轮没有启用 BeliefKV，不能证明 Host KV 转移或预测调度迁移完成。
带 staging patch 的 checkout 在关闭 BeliefKV、开启 4 GiB HiCache 后也通过
并发原生请求和 tagged metadata 请求 smoke。默认 FlashInfer sampling 首次
JIT 遇到 CUDA 13.4 `nvcc` 与 13.0 headers 不兼容；这次仅以 PyTorch
sampling 验证补丁禁用态，不能用作正式性能比较。

工具预测、物理候选和晋升门禁的定向 CPU 回归已覆盖代码路径；
staging 单测必须明确以 `PYTHONPATH` 加载修改后的源码，而非当前安装
的 SGLang wheel。尚无新模型物理动作或高压 GPU 验收。
新模型下一步先冻结并验证 Qwen3.5/v0.5.20 的 predictor
artifact、prediction support/OOD/stale 回退和实际 admission GPU
gate；再补齐 action-local owner/closure/capacity 证明、安全动作
dispatch、部分提交和取消时的 fail-closed 事务，
重测 FULL/MAMBA 服务率与高压 baseline/P6 A/B。现有原生和 tagged
单请求 smoke 均不能替代这些 gate，**P6 尚未完成迁移**。

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
| Action frontier | 可用，已修正 sole-blocker credit | observed RCCG unlock 事实；预测只补充 demand/timing，不改变事实因果优先级 |
| Visible waiting queue + AdmissionTicket | 可用 | SGLang allocator 最终验收 |
| Work-conserving JointPlan | 可用 | 吞吐优先，fairness 仅防饿死/tie-break |
| PageIndex + PhysicalBundle | 可用 | 多 owner、generation、lock、closure |
| Reactive `COMMIT_CPU/DROP` | 可用 | 只由明确 beneficiary deficit 触发 |
| Running retraction | 可用 | 最新修复正在高压回归 |
| Transactional restore | 可用 | H2D/native load/recompute，service 后终结 |
| FrontierBelief | schema-v5 formal train/calibration artifact | 直接拟合 operational-tau、pooled demand 与稀有分类；`test_id` 仍封存，`online_eligible=false` |
| Predictive execution/admission | 已接入 | 仅使用校准 token demand 做 SRPT/HBM 排序；RCCG/observed seed 保留因果优先级，native allocator 最终验收 |
| Predictive `PREPARE_HOST` | 已接入 | D2H 后保留 GPU KV，并建立 beneficiary-victim binding |
| Beneficiary-bound `COMMIT_CPU` | 已接入 | 只由真实 deficit 授权，优先消费 prepared victim |
| Predictive `PREFETCH_GPU` | 完整服务路径已接入，收益未验证 | 支持完整、ancestor-closed partial、latest-start retry、首个 service quantum funding 和 first-service attribution；development artifact 需显式 override |
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

wait-shadow 路径已将“预测输入观测时间”和“intent 实际发布时间”拆分。v48 中真实
publish-to-validation P95 为 0.395 ms，same-safe-point validation P95 为 0.761 ms；但
source-observation-to-validation P95 仍为 750.16 ms。后者主要来自 bounded physical preview
枚举所有子树的近似 O(N^2) 扫描，`38c389b` 已改为基于一次 `private_subtree()` 结果的线性
候选选择。该优化仍需短 GPU gate 验证，不能仅凭 CPU 回归宣称端到端时延合格。

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
- Host pool：192 GB decimal（SGLang `hicache_size` 乘以 `1e9`；v10 由 64-root、
  max-running 96 高压实验使用）；
- CUDA Graph 已覆盖 batch 1/2/4/8/16/24/32/40/48/56/64/72/80/88/96；
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
admission 优先级，并暂时不能成为反向 eviction victim。若 reentry request 已可见，runtime
还会通过 allocator-backed funding 保留一个有界的 prefill/decode service quantum；native
admission 尝试前临时释放，失败后重获，首个 service 或 lease 终止时归还。该机制避免 H2D
成功后立刻被迁出或因后续 KV 增长长期得不到 service，但不会为完整 context 无限预留 HBM。
详细实现与证据见
`docs/experiments/beliefkv_p6_prefetch_recall_enablement_2026-09-16_zh.md`。

### 4.7 当前预测头质量

以下结果来自 schema-v5 artifact 在冻结的 16-workflow calibration split 上的重放；64 个
train workflow 用于拟合，LOPO 只在 7 个 train project 内选参，`test_id` 仍封存。
分类 accuracy 必须与多数类基线一起读，区间 head 使用 episode-weighted MAE 和 coverage：

| Head | Held-out calibration | 当前用途 |
| --- | --- | --- |
| Boundary type | NLL 0.1869；top-2 accuracy 99.67%；FINAL/SPAWN top-2 recall 97.72%/73.76% | 供 scenario composition；不以失衡的 top-1 argmax 直接授权动作 |
| Tool terminal | accuracy 81.75%（多数类 79.63%）；error recall 51.10%；NLL 0.4339 | 可区分部分失败风险；censored 仍不作为可预测终态 |
| Next output | MAE 175.84 tokens；workflow-macro episode coverage 88.40% | 有不确定性的短期 demand |
| Prompt growth | MAE 2,002.41 tokens；workflow-macro episode coverage 91.75% | 保守 HBM growth envelope，区间仍较宽 |
| Remaining decode | MAE 384.54 tokens；workflow-macro episode coverage 90.40% | 比 v6 降低约 11.9%，仍不等同 exact run-to-boundary |
| PREPARE operational tau | Brier 0.0501，skill +19.21%；高置信度 precision 99.90%、recall 76.51% | 直接预测 live D2H tau 下的等待裕量 |
| PREFETCH operational tau | Brier 0.0775，skill +45.67%；0.5 阈值 precision/recall 69.91%/62.80%；动作阈值 precision/recall 59.66%/90.56% | 直接预测 live H2D tau 下的 reentry 风险；仍须物理与价值门禁 |
| JOIN/WAIT_CHILD | 不直接学习 wall-clock；由 RCCG 对 child scenario 做 ALL/ANY 组合 | 依赖 child head，尚无独立 end-to-end accuracy |
| WAIT_MESSAGE | 当前 artifact 无独立模型 | unsupported/OOD，不驱动动作 |

schema-v5 不再把 action target 仅用于评估：20,864 条 train action outcomes 直接拟合
`P(release <= live tau | state, elapsed, role, tool, backend, command, context)`。旧层次经验模型
只作为 schema-v4 兼容 fallback。exact incremental boundary 仍为 0%，因此不支持 early
dispatch 或 exact run-to-action；这与 runtime boundary/top-2 scenario prediction是不同能力。

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

### 5.2 2026-09-17 schema-v5 在线 demand 接口修复

代码审计确认：schema-v5 的离线 train/calibration 与 predictive worker 候选局部推理已经
生效，但 bounded observed seed 过去只在旧 retraction predictor 开关启用时填充
`_last_frontier_predictions`。正式 P6 配置关闭该开关，因此 beneficiary hint 会退化为
`max_new_tokens=4096` 的静态 demand 上限，导致 beneficiary future-deficit、victim capture
和 latest-start 不能代表 schema-v5 输出。此前 GPU 结果可用于验证物理机制和 HBM 观测，
不能用于声称 schema-v5 demand 已经驱动在线动作。

`af1a0f9` 将 bounded seed 的预测接口改为 action-local、缓存化推理：

- 只检查 seed 排名前四且仍 deferred 的 invocation；
- 以 invocation state、boundary history、context/generated tokens 和 tool backend/command
  组成紧凑特征签名；
- 特征不变时复用预测，只有候选或特征变化时调用 schema-v5；
- 将 remaining decode P90、next output P50、support/OOD 回填到 runnable 与 beneficiary hint；
- predictive worker 继续对完整候选 closure 独立推理，safe point 不恢复全局 invocation 推理。

新增 `bounded_seed_prediction_ms/inferred/cache_hit/failed` 观测。定向 adapter 测试为
`5 passed`，P6 predictive 回归为 `68 passed`；完整 adapter 的 2 项失败仅因为当前 shell
没有 `CUDA_HOME`，其余 `174 passed, 8 subtests passed`。下一次长高压 gate 必须确认
在线 hint 的 `prediction_support_level` 不再为 `unavailable`，并覆盖完整
`PREFETCH_GPU -> H2D ACK -> service lease -> first GPU service` 路径。

### 5.3 2026-09-17 PREFETCH 完整服务路径修复

`c7fba92` 修复了 H2D 完成前后仍会破坏预测收益的三个状态机缺口：

- `prefetch_too_early` 不再删除 intent，而是保留到 action-dependent latest-start 后重试；
- H2D ACK 后为目标 reentry request 建立有界 allocator-backed service funding，复用现有
  admission-rescue 的“尝试前释放、失败后重获、首个 service 后终止”协议；
- 首个真实 GPU service 会同时释放 lease/funding，并将 predictive attribution 终结为
  `useful`，不再留到 shutdown 时被错误 censor；
- risk candidate generation 不再在第一个 target 后无条件退出，最多四个 semantic target
  可以进入完整价值评估；partial prefetch 不再要求 live bundle 与旧 `copy_bytes` 完全相等，
  而是使用已有的 reclaim/cross-context/copy envelope 做安全校验。

CPU 回归为 `176 passed, 2 deselected, 8 subtests passed`；两项 deselected 仍仅依赖当前
shell 缺失的 `CUDA_HOME`。predictive risk/JointPlan/worker/attribution 定向回归另有
`70 passed`。GPU 端尚未验证，不能据此声称吞吐收益或在线 PREFETCH precision 已通过。
`41a1239` 进一步让 performance mode 保留 lease registered/released 和
`predictive_action_outcome` 三类低频事件，确保长 gate 能观察完整闭环而不恢复逐 step 审计。

### 5.4 2026-09-18 多目标 reentry 与 JOIN prefetch 修复

长高压 v18/v19 gate 证明冻结 64-root、hard-64 workload 能自然达到接近 100% 的物理
HBM 压力，并产生原生 D2H、真实 CPU-side KV 和可物化的 predictive reentry 候选。
`4a82c4e` 将单一 reentry watcher 扩展为最多三个分层目标，覆盖 `WAIT_TOOL` 与
`WAIT_JOIN/WAIT_CHILD`，并按未完成 dependency 和 child progress 计算 JOIN ALL/ANY 的
release probability；v19 共发布 643 次 reentry risk、1,929 个目标，说明 watcher 不再只盯住
同一个 tool wait。

v19 在受控停止前形成 127 个 fresh-positive package，最大期望收益约 3,188.90 ms，但旧的
固定 0.5 dependency timing gate 又将它们拒绝。该 gate 与已经执行的 scenario expected
benefit、CVaR 和 future-HBM 检查重复，而且 JOIN/dependency release 没有独立校准阈值。
`5030207` 因此让未校准的 dependency-composed prefetch 信任完整 scenario risk 结果；
`WAIT_TOOL` 仍使用 artifact 中的动作校准阈值。此修改只移除错误的重复 veto，没有降低
expected-benefit、容量、物理证书或 latest-start 门禁。

v19 的 BeliefKV shutdown 已满足 ACK、transaction、lease 和 obligation 守恒，但启动器 shell
退出后 SGLang frontend 曾作为孤儿进程继续占用端口和 GPU。`5152382` 为 launcher 记录 PGID，
stop 脚本在验证进程组身份后清理残留 SGLang 组。当前 v20r 用于同时验证该关闭路径和完整
`PREFETCH_GPU -> H2D ACK -> funding/lease -> admission -> first service -> useful` 归因链。

### 5.5 2026-09-18 child tool-return 与事件摄取活性修复

`d13663f` 将 child 自身的 `TOOL_START -> TOOL_END` 纳入 rolling reentry watch。
watch 以 invocation/context epoch 和 tool-call identity 为版本边界，在 child 已存在 CPU-side
KV 时参与 latest-start `PREFETCH_GPU` 评估；`TOOL_END/REACTIVATE/RETURN/CANCEL`
或身份失效会清理 watch。该路径不要求等待 parent JOIN，因而可以恢复仍需继续多轮执行的 child。

同一提交修复了高事件量下的 scheduler 空转：ObservedDataConsumerIndex 原先在每批 runtime
event 上递归深拷贝完整历史，约 13K 事件时使 scheduler 单核长时间卡住、GPU 停止获得 batch。
ConsumerEdge/ConsumerIndexDelta 为不可变对象，现改为浅层容器快照并保留原子回滚。13,500
条历史微基准 P95 为 1.17 ms；相关 CPU 回归为 234 passed、8 subtests passed（另有两项仅因
本机 shell 缺失 CUDA_HOME 而排除）。

`2c30c9f` 新增 h200_bf16_v10，将 Host KV 从 96 GB 扩展到 192 GB decimal，保持 HBM 850K、
max-running 96 和 graph96 不变。v26 启动已确认 graph 捕获到 96、Host slab 为 192 GB，
workload 启动后 running 92-95、waiting 29-32，GPU 重新持续获得 prefill/decode 工作。
该启动证据只证明活性修复和容量契约生效；child rolling prefetch 的完整归因仍需等待后续
CPU-side child KV 与 timely latest-start 自然出现。

### 5.6 2026-09-19 wait-window gate 与状态机修复

v45 高压运行验证了 `778caa6` 的 stale-trigger starvation 修复：21 个持久化 risk
snapshot 的 trigger 数量始终为 3，最新集合只包含当前 reentry 目标，不再累积历史 trigger。
实验随后受控停止；shutdown summary 满足 `no_pending_transactions=true` 和
`shutdown_cleanup_did_not_mask_unresolved_transactions=true`。

同一运行也确认一直缺少 predictive prefetch 的关键原因不是工具调用都太短。child tool
调用中存在大量 0.25--2 秒窗口，且一笔约 2.52 秒的 `execute` 已成功容纳 PREPARE D2H。
旧 gate 的问题是首笔 PREPARE 后用累计 `prepare_host_queued` 永久封锁后续动作、固定
1500 ms control lead 排除了大量可用窗口、native transfer busy 只产生瞬时拒绝，以及低于
80% HBM 时不进入收益判断。

`d43d8c1` 将 PREPARE limit 改为并发物理动作边界；完成动作不再消费后续窗口。native
transfer 或 residency transaction 忙时进入 100 ms 有界重试；低压只记录诊断，仍由 timing
probability、live D2H cost 和净收益决定是否发布。control lead 已成为显式配置，当前临时值为
250 ms，仅用于关闭 prefetch 机制闭环；其最终取值必须在完整 predictive H2D 验证后，基于
transfer 分布、预测刷新频率、误触发率和 saved stall 单独标定。该提交尚未经过新的 GPU
prefetch 闭环验证，不能据此声明吞吐收益。


### 5.7 2026-09-19 wait-shadow same-safe-point 提交修复

v46 最终有 444 个 `PREPARE_HOST` intent 因 invocation/tool 状态变化被拒绝。逐事件对齐后，
intent publish 到 safe-point validation 的 P50/P95 为 991.98/2,283.03 ms；TOOL_START 到
validation 的 P50/P95 为 1,478.83/3,094.94 ms。259 个 intent 在对应 TOOL_END 之后才发布。
199 个对应工具总时长超过 500 ms，但其中只有 16 个在 intent 发布时仍剩超过 500 ms。
因此该问题是控制链迟到，不是“没有长工具窗口”。

`994de39` 将 child TOOL_START 设为立即预测触发，并让新 wait-shadow intent 在发布所在的
physical safe point 直接复用当前 observed decision、缓存 physical preview 和 action-local
certificate 完成验证；不再等待下一轮 JointPlan reuse/refresh。TOOL_END、REACTIVATE、
RETURN 或 CANCEL 到达时会撤销尚未提交的同 invocation intent。live bundle、transfer
envelope、Host capacity、事务互斥和 commit budget 均继续重验，没有放宽安全门禁。

新增 `wait_shadow_publish_to_validation_ms` 及 same-safe-point wall/CPU 指标。CPU 门禁为
270 passed、8 subtests passed；另 2 项仅因当前 shell 缺少 `CUDA_HOME/deep_gemm` 无法
import SGLang。GPU P95 尚未复验，下一轮目标是 publish-to-validation P95 <50 ms，并至少
观察一笔在真实工具窗口结束前进入 D2H queue 的 PREPARE。详细审计见
`docs/experiments/beliefkv_p6_wait_shadow_validation_latency_v46_2026-09-19_zh.md`。

### 5.8 2026-09-19 PREPARE 收益绑定与 PREFETCH 证书刷新

v50 自然运行中的中期审计证明 wait-shadow 控制链已经足够快，但动作效用仍不足：
510 个 `PREPARE_HOST` command 中 453 个 D2H ACK，336 个后来因工具返回浪费，57 个失败；
其中 56 个失败是 D2H 完成后 Radix node 变为 engine-locked。更重要的是，这些 wait-shadow
PREPARE 没有携带 beneficiary identity，因此 ACK 后没有形成任何
`prepared_causal_binding`，自然无法在真实 deficit 出现时被 observed planner 消费。

PREFETCH 侧已有 19 个 semantic intent、4 个 root context 和 7 次 watch activation；
activation lag P50 约 108 ms、max 489 ms。未闭环的原因不是发布耗时，而是到达 latest-start
时 intent 证书年龄已达 3,376--6,180 秒，context epoch / invocation revision 已变化。telemetry
中 49 笔 `prefetch_context` H2D 均为 `restore-*` 且 `predictive_intent_id=null`，不能计为
predictive prefetch。

`87411cc` 做了三类窄修复：

1. 低于 observed admission watermark 的 wait-shadow 直接抑制，不再为了低价值 canary 复制；
2. wait-shadow PREPARE 必须绑定一个当前可见且存在 immediate/future HBM deficit 的
   beneficiary，并把 deficit、startup/growth 和 causal generation 写入 intent；ACK 后可形成
   prepared binding，供真实 `ReclaimRequirement` 消费；
3. 到达 latest-start 的 prefetch watch 只能激活新鲜证书；旧证书强制 worker refresh，
   每 50 ms 有界重试，而不是激活小时级旧读集。已完成 D2H 的瞬时 engine lock 改为等待锁释放
   后再提交，不再丢弃已完成的 DMA copy。

定向回归 7 项通过；SGLang adapter 全套为 200 passed、2 deselected、8 subtests passed，
两项仅因当前 shell 缺失 CUDA_HOME/deep_gemm 导入被排除。该修复尚未进入任何 GPU 运行；
v50 仍在旧进程上自然运行，不能用来评价新修复。

### 5.9 2026-09-19 predictive H2D 物理闭环

v51 暴露 `87411cc` 的一个崩溃缺陷：beneficiary 排序访问了不存在的
`BeneficiaryOpportunityProbe.service_lag_ms`。`5b0045e` 改用 hint 自带的
`service_lag_ms`，该类崩溃关闭。

`1d559d7` 将 PREPARE 拆为两种模式：高压且有明确 deficit 时仍 beneficiary-bound；
低压或高压暂无 beneficiary deficit 时，只要 Host 低于低水位且 PCIe 空闲，允许 unbound
opportunistic partial PREPARE。该动作仍受 64 MiB chunk、长等待概率、native transfer
互斥、干扰成本和 Host 水位约束。v54/v55 证明该路径能产生连续 predictive D2H。

长期 parent-JOIN watcher 的预测 reentry 可达数十分钟，不能用于快速验证物理 H2D。v54 的
native demand-load 统计显示，可见 request 到 native H2D 平均有约 49 秒窗口；因此
`312ab69` 增加 observed-service prefetch 快路径：当可见 waiting request 的 context 已有
CPU-only KV 且缺少 GPU KV 时，立即生成 `PREFETCH_GPU` intent，并在同一 safe point
物化。

v57 首次完成自然 predictive H2D 归因链：

```text
visible request
  -> observed-service PREFETCH_GPU intent
  -> same-safe-point commit
  -> PREFETCH_CONTEXT command queue
  -> H2D dispatch/ACK
  -> service lease
  -> request_started
  -> first GPU service
  -> useful attribution
  -> lease release
```

该笔 H2D 传输 325,189,632 bytes、11 extents、耗时 89.72 ms，telemetry 保留非空
`predictive_intent_id`。H2D ACK 后 667 ms 注册 service lease，request 获得首个 GPU
service 后标记 `useful` 并释放 lease。检查时无 scheduler 异常、无 orphan transaction、
无 active lease/obligation。

v57 同时暴露两个后续问题：17 笔 atomic H2D 因旧的 authoritative-free-tokens 检查被拒绝；
74 个 service intent 曾因 immediate window 重复扣除 25 ms guard 被误判为 too early。后者在
`312ab69` 已修复；前者在 `a772a61` 中让携带 `allow_native_eviction` 的 service H2D 与
native demand-load 一样使用安全 native eviction，并通过 201 项 adapter 回归。v57 运行时
不包含 `a772a61`，因此它的 1/18 H2D 成功率是下界，不是最终策略成功率。

### 5.10 2026-09-19 Host 语义化清理与容量边界

64-root 长上下文 workload 的 unique live KV 已接近或超过 HBM+Host 总容量。当前 Qwen3 Coder
BF16 KV 为 98,304 bytes/token：

| Tier | Tokens | 60K-token agents |
| --- | ---: | ---: |
| HBM 850K | 850,000 | 14.17 |
| Host 192 GB decimal | 1,953,125 | 32.55 |
| Total | 2,803,125 | 46.72 |

因此 64 个 root 加 children 会自然溢出，Host 不再是无限冷缓存；native writeback 会与
predictive shadow、future reentry 和 dead KV 竞争同一 Host 容量。

旧 Host high-watermark cleanup 有两个缺陷：所有 `DUAL_CLEAN` shadow 一律按 last-access 排序，
没有区分 native writeback 与 explicit/predictive copy；`CPU_ONLY` 只有 raw-prompt replay
guarantee 时才可清理，导致部分 dead/terminal context 在高压下无法释放。现已修复为：

1. `PhysicalPageRecord.host_copy_source` 区分 `native_writeback`、`explicit` 和 `predictive`；
2. Host cleanup 优先释放 dead `CPU_ONLY`；
3. 其次释放 dead `DUAL_CLEAN`，再优先 native-writeback shadow，最后考虑 explicit shadow；
4. active restore obligation、prefetch service lease、语义 pin 和非 parked owner 继续受保护；
5. dead CPU-only 不再要求 raw-prompt replay guarantee；live CPU-only 仍保留该要求，避免无法
   重算的 active context 被破坏。

全局 KV value model 暂不进入当前决策层。它可能提高长期命中率，但会把 execution、victim、
Host cleanup 和 reentry 预测耦合到一个大优化问题，增加控制面开销和决策不可解释性。后续只
作为独立分支评估，并用 shadow A/B 证明收益超过调度开销与决策耦合风险。

SSD 第三层同样暂不实现。它可扩展 cold/parked KV 容量，但会引入异步 I/O、 durable metadata、
tier staging、恢复路径和新的驱逐问题。在 Host 语义化清理和容量/命中率证据稳定前，加入 SSD
会使系统复杂度过高。后续只在 Host miss 或 forced eviction 证明存在大量可复用 cold KV 时评估。

### 5.11 2026-09-20 v58 predictive H2D gate

v58 自然完成 64/64 workflow。`a772a61` 的 service H2D native-eviction 修复将物理成功率
从 v57 的 1/18 提升到 24/25：22.15 GB predictive H2D 完成，20.97 GB 在首个 GPU service 后
进入 useful attribution。H2D duration P50/P95 为 510/1,245 ms。

运行 HBM pressure mean/max 为 97.4%/100%，Host used mean/max 为 169.5/192.0 GB。v58 未包含
`7ae2be6` Host 语义化清理，因此它证明了 predictive H2D 机制和高压容量边界，但没有解决 Host
侧竞争。唯一 API timeout workflow 的 restore obligation 因 request abort 取消；shutdown drain
将残留 command 显式置为 cancelled，最终事务、lease、obligation 全部清空。运行时仍有 1 笔
H2D completion ownership race 需要修复。

### 5.12 2026-09-20 predictive DMA queue 与 batch 修复

v58 execution timeline 复核显示：predictive D2H 最后一笔出现在 27.15 分钟，
predictive H2D 最后一笔出现在 12.92 分钟，但 `prepare_host` intent 一直发布到约
161.5 分钟。后续 1,648 次 semantic rejection 中，1,598 次包含 `pcie_dispatch_busy`，
且这些样本的 `native_hicache_inflight_bytes` 均为 0。late 阶段
`migratable_gpu_bytes` mean 为 36.62 GB、Host used mean 为 185.24/192 GB。因此
停止传输的原因不是 HBM 无可迁移 KV，而是全局队列布尔值和 Host 侧 CPU shadow
竞争造成 predictive 动作饥饿。

`efb5ded` 已修复在线调度路径：

1. `controller.has_pending_transfer_work()` 继续表示 drain/liveness 意义，但不再被
   predictive gate 解释为 PCIe 饱和；
2. observed residency、semantic residency 和 urgent restore 只阻塞相同
   target/victim context 的 predictive action，不再全局否决不重叠动作；
3. deadline-bearing predictive command 进入 urgent transfer queue，可排在同方向
   工作之后，并在 dispatch 前重新解析物理 bundle；
4. predictive D2H 默认最多可将 256 MiB 的多个 disjoint exclusive suffix 合成一个
   closure-complete native batch，经 `write_backup_batch()` 一次提交；
5. 合并 bundle dispatch 时重新检查 owner、ancestor、lock、residency、action 和
   generation fingerprint，不信任 safe-point 旧快照；
6. H2D 继续复用 SGLang native load worker 的既有 operation merge，不借此启用未验证的
   反方向并发 PCIe gate。

该修复不声称拥有实时 PCIe 带宽信号；它把决策改为 action-local conflict、deadline
和权威物理重验证。下一轮 GPU gate 必须统计 `pcie_dispatch_busy` 是否归零、30 分钟后
是否仍有 predictive D2H/H2D、merged batch extent/bytes、queue-to-dispatch 延迟、
stale 率和 scheduler P95/P99。

### 5.13 2026-09-20 baseline restore D2H watchdog

同契约 observed baseline attempt0 在旧 `38e37d5` 上运行约两小时后卡死：
`restore-5-command-11` 是 48.27 MB、3 extents 的 restore funding D2H，dispatch 后
超过 80 分钟没有 backend telemetry 或 ACK；期间 running=0、admission epoch 持续
空转，HBM/Host 维持在容量边界。shutdown drain 最终只能将该 command 显式置为
`cancelled(reason=runtime_shutdown_drain_timeout)`。该 attempt 已保留为
`baseline_attempt0`，不能作为 A/B 结果。

`fd8d7d7` 将 controller 既有 transfer watchdog 从仅审计升级为强制终态：

1. backend 对 stalled explicit command 生成 `CANCELLED` ACK 和 telemetry；
2. controller 回滚 PageIndex transfer 状态并释放 restore/residency transaction；
3. runtime 标记 full Radix mirror rebuild，避免信任可能仍在 native 层存活的 partial
   callback；
4. 下一个 scheduler step 立即 drain ACK，防止 admission 空转。

该恢复是 fail-closed，不宣称丢失的 D2H 已完成；相关 restore obligation 会走失败/
回退路径。下一次 baseline 或 predictive gate 需统计 forced-cancel 次数，理想值为 0。

### 5.14 2026-09-20 baseline telemetry journal compaction 修复

baseline attempt1（`baseline_attempt1`）在约两小时后进入低 GPU 病态：

- 最近 10 分钟 GPU mean util 约 0.79%，busy fraction 约 5.4%；
 - SGLang decode log interval 从正常几十毫秒级退化到约 64--68 秒；
- running 只有 6--10，waiting 约 70；
- `sglang::scheduler` 主线程持续约 100% CPU；
- 最近 5 分钟没有 H2D/D2H telemetry，因此不是持续 DMA 占用；
- 同时出现 2,536 次
  `RuntimeError: transfer telemetry journal gap; shadow rebuild is fail-closed`。

根因是 controller telemetry journal 使用
`deque(maxlen=service_curve_window=256)`。高压 native transfer burst 会淘汰旧
sequence；shadow worker cursor 落后超过 256 条时，`transfer_telemetry_since()`
返回 `full_rebuild_required=True`。旧 safe-point 路径将其视作 fatal exception，且
失败路径不推进 `_shadow_telemetry_sequence`，导致每个 safe point 重复尝试重建、
重复失败并消耗 scheduler CPU。该 attempt1 不能作为 baseline A/B 结果。

`c23b380` 的修复语义：

1. transfer telemetry 是观测流，不是 authoritative physical state；
2. journal compaction 后接受 retained suffix，并记录
   `joint_shadow_telemetry_journal_compacted`；
3. 成功发布 delta 后推进 shadow telemetry cursor；
4. 物理 KV 一致性仍由 PageIndex/Radix authoritative full sync 保证；
5. RCCG event journal gap 仍保持 fail-closed，因为事件是语义状态本身。

### 5.15 2026-09-20 restore 局部等待与统一 lead budget

`6c03357` 修复 restore 的两个全局 head-of-line blocking：

1. controller 新增 pending command 的 context / physical-closure conflict 查询；
2. `_drive_restore_obligations()` 不再被任意 queued/inflight command 全局阻塞，
   只跳过与该 obligation 的 owner context 或 required extents 重叠的 command；
3. `_advance_restore_authority()` 获取 exclusive authority 前同样只等待 owner
   重叠 command，并记录 `restore_authority_wait`；
4. ACK conservation、same-context canonical command、dispatch closure overlap、
   stalled-transfer watchdog 和 full resync 语义保持不变。

当前 baseline `9c4e6c4` 不包含该修复；它属于下一轮 predictive gate 的 runtime
变更，不能混入正在运行的公平 baseline attempt。

同一提交将 predictive timing lead 改为 action-specific budget：

1. 新增 `PredictiveLeadBudgetModel`，按 action 维护 offline prior 与 bounded
   online P90；
2. v58 trace 导出 `predictive_lead_budget_v1.json`：
   - PREPARE dispatch P90：约 676ms，按 500ms hard cap 初始化；
   - PREFETCH dispatch P90：约 2.42s，按 500ms hard cap 初始化；
   - H2D complete 到 service lease / commit-ready P90：约 479ms；
   - H2D ACK 到 first service P90：约 3.64s，只作为 soft service-wait prior；
3. PREPARE 的 `control lead`、PREFETCH 的 `desired lead`、latest-start 和
   too-early 判断读取同一个模型；
4. predictive telemetry 记录 decision-to-submit delay，useful H2D 记录
   ACK-to-first-service delay，并进入 256-sample rolling quantile；
5. online 样本达到 8 个后替代 offline prior，所有 hard action lead 均有硬上限；
   H2D complete 到 service lease 注册会在线更新 `prefetch_commit_ready`；
6. native busy 只更新 prefetch `retry_not_before`，不再向后滑动 hard
   latest-start，避免重复 busy 造成 starvation；
7. too-early 判断补上 commit guard，使 activation/defer/latest-start 公式一致。

PREFETCH 的 hard desired lead 只包含 dispatch 与 commit-ready，不包含 H2D ACK
后的 first GPU service 等待；后者通过 `prefetch_soft_service_wait_ms()` 记录到
watch audit，后续再接入服务优先级和收益归因。v58 的离线 dispatch 分布来自
predictive DMA queue
修复前，不能代表修复后的在线 safe-point 开销。

同轮 follow-up 将 explicit transfer watchdog 改为 progress-aware：

1. 首次 watchdog request 后，若 DMA 无任何 progress，给予 5 秒 grace；
2. 任一 handle/byte progress 变化会重置 30 秒 progress grace，避免“native DMA
   已完成但 logical ACK 等待锁/提交”的命令被过早强制取消；
3. 30 秒无新 progress 才生成 terminal `CANCELLED`，并继续保留 ACK conservation
   与 full resync 语义。

已终止的 baseline `9c4e6c4` 不包含 progress-aware watchdog、P90 lead
artifact 与在线 commit-ready 观测；其大量 forced cancel 属于旧 watchdog 对
已完成 DMA 但未 terminal 的 logical command 的保守终止。该 baseline 保留作
v58 contract-matched 对照，下一轮 predictive gate 使用 main 分支修复。

限制：predictive process worker 在构造时取得 offline desired lead，尚未接收
runtime 在线分位数增量。下一轮 predictive gate 需要检查 runtime 与 worker 的
lead version 是否发散；若在线样本显著下降，应再把 compact timing snapshot
传入 worker。

### 5.16 2026-09-20 baseline waiting-only admission 空转

contract-matched baseline `9c4e6c4` 运行约 3 小时 40 分后进入长尾空转：

1. `running=0`、native waiting=28、GPU utilization=0；
2. allocator 只剩约 1.3MB HBM，PageIndex 仍有约 83.6GB GPU KV；
3. 约 18.2GB 标记为 migratable，约 2.0GB 为 native reclaimable；
4. 28 个 native waiting request 关联约 65.3GB engine lock，且 2041/2041 个
   lock extent 的 request ref 与可归因路径数量不匹配；
5. 无 inflight/queued command、无未满足 restore obligation，说明不是 restore
   或 DMA 卡死；
6. waiting request 按各自 30 分钟 queue timeout 串行退出，无法形成有效 A/B
   长尾吞吐数据。

根因是两个门禁叠加：

1. controller 的 admission liveness 把 `_engine_request_count == 0` 当作 idle，
   但该计数包含 native waiting request；本场景实际 `_running_request_count=0`，
   liveness/native reclaim 永远不启用；
2. observed JointPlan 处于 seed-only/no-action 时仍独占 authority，
   `allow_reactive_transfer=False`，controller 不能通过 frontier spill 释放
   migratable KV。

main 分支修复为：

1. admission liveness 的 idle 判定改为 `_running_request_count == 0`；
2. JointPlan 在 running=0、oldest visible pending 超过 force-progress timeout
   且 HBM 不足以 admission 时，临时恢复 reactive transfer fallback；
3. fallback 触发/释放记录 `joint_reactive_admission_liveness_fallback`；
4. 不降低压力阈值，不绕过 PageIndex/transfer validation，只恢复原有安全
   liveness 路径。

该修复已通过 controller 与 adapter CPU 回归，尚未经过 GPU 回归。当前运行中的
baseline attempt 属于失败证据，不能渲染为 v58 A/B baseline。

第一次重试还暴露 `167edc3` 的真实构造顺序错误：runtime 在创建
`RuntimeAuditLog` 前执行 `self.backend.audit = self.audit`，导致 scheduler 在
192GB Host KV 分配完成后立即崩溃。该错误已改为 audit 初始化完成后绑定，并补
真实 constructor 回归；对应启动目录保留为 `baseline_startup_attempt0`，不进入
A/B。

### 5.17 2026-09-20 Action frontier 与 v58 KV 生命周期审计

Action frontier 已做一处保守 correctness 修复：`blocking_chain` 只有在父节点
的 blocking child 集合恰好只剩当前 child 时才给 unlock credit。旧实现只要当前
child 属于 `blocking_child_ids` 就累计深度，父节点仍有其他 children 时会高估其
即时解锁能力。`FrontierCandidate` 新增：

1. `known_downstream_count`：当前 nonterminal descendant 数；
2. `join_waiter_count`：该 member 成为 JOIN 最后未完成项时将被唤醒的 waiter 数。

同类别 max-weight utility 现在加入有界 fanout credit：
`0.25 * downstream`（最多 8）与 `0.5 * join_waiters`（最多 4）。类别排序、
starvation floor、native allocator authority 和预测安全门禁不变。因此该修改只修正
观测 unlock credit，不把预测概率伪装成 RCCG 事实。

三层调度边界如下：

1. Dynamic working set：workflow 级 active-set/soft-target 选择，输入 effective
   native HBM 和 ready frontier 聚合，不生成 ticket 或物理 command；
2. AdmissionTicket compiler：一个 prefill epoch 内的 bounded eligibility/certificate，
   输出 ticket、skip 原因和 ReclaimRequirement，不 mutate queue/allocator；
3. JointPlan worker：capacity-one 异步 mirror 和候选计划形成，只消费 compact delta，
   不访问 live scheduler；safe point 消费 latest result 并重新验证后才允许物理变更。

v58 predictive 运行的 KV 生命周期审计结果如下（decimal bytes；完整机器可读结果见
`experiments/ab/p6_h200_high_pressure_v3/20260920_v58_pair/v58_predictive_kv_lifecycle_summary.json`）：

- 7,229 个 LLM request 对应 199 个 invocation/context：64 root、135 subagent；
- 按 `uncached prompt tokens + output tokens` 聚合，新生成 KV 约 842.56GB；
- 平均每个 invocation 约 43,070 tokens / 4.23GB；root 平均约 4.82GB，subagent
  平均约 3.96GB；每个 root workflow 平均约 13.16GB；
- 完成 D2H 约 803.66GB，其中 native write-back 797.81GB；
- 完成 H2D 约 6.13TB；该值包含同一 Host KV 被多次 demand-load，不是唯一 KV 量；
- 结束时 PageIndex 仍有 GPU 78.22GB、CPU 112.14GB，其中 dual-resident GPU
  55.98GB；
- 显式 BeliefKV GPU DROP command 为 0；native write-back 表示“GPU copy 释放并写
  入 Host”，不是逻辑丢弃；
- Host native LRU/替换没有被逐笔 telemetry 记录。以
  `completed D2H - final CPU bytes` 计算，至少约 691.52GB Host KV 被替换/挤出；
  这是下界，不是精确丢弃量。

terminal subagent 清理语义：

1. `RETURN/CANCEL` 先把 RCCG invocation 置为 terminal，并释放 nonpersistent
   context 的 semantic ownership；
2. 已经 CPU-only 且无 owner 的 terminal private page 会进入 terminal cleanup，
   由 `DROP_TERMINAL_PRIVATE` 直接释放 Host copy；
3. 系统不会仅为 dead context 执行一次新的 D2H 保存；GPU dead/unowned KV 在压力
   下可由 `DROP_UNOWNED` 释放；
4. shared page 必须所有 owner 都 terminal/dead 才能按 dead candidate 处理；
5. Host 高水位 cleanup 在 v58 之后的实现中按 dead、native-writeback shadow、
   explicit shadow、CPU-only recompute 的顺序选择。v58 运行时该语义化 host
   cleanup 尚未进入 trace，`host_cleanup_queued=0`。

### 5.18 2026-09-21 baseline v3 完整审计与恢复修复

contract-matched baseline v3 在 main `27d25de` 上自然完成：

1. 64/64 workflow 均有 result，运行 30,969s；
2. workload return code 为 1：52 completed、12 error，measurement-valid 17；
3. shutdown acknowledged，queue 清空，144 个 restore obligation 全部
   `gpu_service_resumed`，无遗留 command/lease/transaction；
4. waiting-only liveness fallback 触发 108 次、释放 58 次，运行期间没有复现
   running=0 且 waiting 长期空转；
5. telemetry journal compaction 7 次均通过 retained suffix 恢复。

该 attempt 不能作为 clean formal A/B，原因如下：

1. 12 个 error 均为
   `TerminalProtocolError: agent stopped twice without required WorkflowCompletion schema`；
2. Joint worker 171 次 fail-closed 并请求 mirror resync。最后样本为
   PageOwnershipIndex CPU/GPU bytes 超过 native authoritative usage；
3. 34 个 restore funding command 停在 `parked_wait` 后被 watchdog 取消；
   telemetry 显示其中大量命令已有实际 DMA bytes。取消后 obligation 重试，
   造成重复 D2H 和额外延迟；
4. 84 个 `DROP_UNOWNED` 生命周期动作缺少 reason，被误分类为
   `unified_liveness` 并污染
   `all_online_actions_have_source_joint_plan_id` correctness gate。

main 分支已做三项修复：

1. `DROP_UNOWNED` pressure cleanup 显式标记
   `dead_unowned_pressure_cleanup`，归类为 lifecycle，不再要求 JointPlan ID；
2. PageOwnership mirror 与 native allocator 的短暂 overage 采用保守上界：
   snapshot 记录 `ownership_overage_hbm/host_bytes`，并以 tracked bytes 作为
   HBM/Host used 上界；tracked bytes 超过物理 capacity 仍 fail-closed；
3. explicit transfer watchdog 对已有 partial DMA progress 的命令不再按时间
   强制取消。时间 watchdog 只终止完全无 progress 的命令；有 progress 的命令
   等待 lock/extent 收敛，request cancel/shutdown 仍可显式丢弃。

上述修复已通过 adapter、policy snapshot、controller 和 transfer 回归。下一轮
predictive gate 使用这些修复；若需要最终论文级 A/B，baseline 也必须使用同一
commit 重跑。

### 5.19 2026-09-21 v59 predictive gate

v59 predictive arm 使用 main `8312f66`、v8 冻结 64-root high-pressure plan、
v10 profile、graph96 和 Host 192GB 自然完成：

- 64/64 workflow 均有 result：62 completed、2 error、22 measurement-valid；
- 运行 30,488.57s；
- queue 清空，shutdown acknowledged；
- 所有 correctness gate 通过，包括 online action source、transaction
  conservation、restore obligation 和 shutdown summary；
- 无 scheduler exception、OOM、Joint worker mirror failure 或
  partial-progress watchdog cancel。

两个 error 不是 runtime 崩溃：一个为 TerminalProtocolError，另一个为
`input 258,618 + output 4,096 > context 262,144` 的显式 BadRequest。后者说明
agent workload driver 仍需要在提交前按模型 context 上限裁剪 prompt 或降低
max completion，而不是由调度器 silently truncate。

v59 机制漏斗：

1. 11,341 次 risk evaluation；
2. 214 次选择 `PREFETCH_GPU`，73 次选择 schedule；
3. 281 个 semantic residency action；
4. 236 个完成，44 个拒绝，1 个 shutdown 前中止但在 shutdown 中正确终结；
5. 25 次 predictive H2D 全部 completed，32.19GB；
6. 15 次 H2D useful attribution，16.99GB；10 次 wasted，15.20GB；
7. 211 次 predictive shadow D2H completed，43 次拒绝；
8. 15/25 H2D useful rate 为 60%，按 bytes 为 52.8%。

v59 与 baseline v3 的初步同 workload 对比：

| Metric | baseline v3 | predictive v59 | relative |
| --- | ---: | ---: | ---: |
| GPU service tokens/s | 222.58 | 245.90 | +10.48% |
| Tool calls/min | 26.11 | 30.60 | +17.19% |
| LLM requests/min | 11.66 | 11.70 | +0.38% |
| Completed workflows | 52 | 62 | +19.23% |
| Measurement-valid workflows | 17 | 22 | +29.41% |
| Mean GPU util | 8.74% | 9.93% | +13.66% |
| Mean running | 15.41 | 21.55 | +39.91% |
| Duration | 30,969s | 30,489s | -1.55% |

这不是最终论文级 formal A/B：baseline v3 使用 `27d25de`，predictive v59 使用
`8312f66`；二者相差 ownership transition、watchdog、Action frontier 和 source
gate 修复。该对比只能说明 predictive arm 在长高压自然 workload 中首次形成
正向吞吐信号。

v59 暴露的下一步问题：

1. `PREPARE_HOST` 仍无 useful attribution：211 个 completed shadow D2H 全部
   wasted，43 个 failed。PREFETCH 已闭环，但 PREPARE 的 beneficiary/victim
   绑定没有转化为真实 deficit 消费；
2. `PREFETCH_GPU` 10/25 wasted，仍需提高 beneficiary 甄别和 latest-start
   精度；
3. 6,492 次 wait-shadow 因已有 residency transaction 暂停，说明单事务/单
   inflight 限制在高机会窗口造成串行化；
4. 18,891 次候选因 insufficient expected benefit 被拒绝，其中可能存在收益
   模型低估，需要与真实 reentry service 差值做离线校准；
5. 47,609 次因 unchanged action signature 被去重，需抽样确认是合理去重还是
   证书/刷新粒度过粗；
6. shutdown tail 中的最后一个 predictive residency 需要等 shutdown drain 才
   终结，应考虑 workload 自然收尾时更早释放或降级无必要 action；
7. agent workload 层仍有 2 个 protocol/context 错误，正式 A/B 前应修复
   driver 的 context limit preflight。

产物：

- timeline:
  `experiments/ab/p6_h200_high_pressure_v3/20260921_v59_predictive/predictive_execution_timeline.html`
- 对比 JSON:
  `experiments/ab/p6_h200_high_pressure_v3/20260921_v59_predictive/v59_vs_baseline_v3_summary.json`
- baseline timeline:
  `experiments/ab/p6_h200_high_pressure_v3/20260920_v58_pair/baseline_v3_execution_timeline.html`

### 5.20 2026-09-21 v59 后半程 predictive H2D 停止审计

v59 的 25 次 predictive H2D 全部集中在第 1 小时内。后续 trace 审计确定了一个
状态机 P0，而不是模型或 PCIe 停止：

1. H2D ACK 后创建的 `prefetch_service_lease` 被错误计入
   `predictive_prefetch_canary_max_inflight`；
2. 141 个后续 `PREFETCH_GPU` intent 因此以
   `predictive_prefetch_inflight_limit` 被拒绝；
3. 10 个 lease 在 5--8 秒服务窗口过期后释放，但在此之前持续阻塞后续
   unrelated H2D；
4. Host pool 从约第 1 小时起长期高于 99%，后半程 reentry 目标多为
   `reentry_no_prefetchable_cpu_bytes`。这是因果断层重演，与 lease 串行化
   叠加，造成 predictive H2D 完全停止。

main 分支修复：service lease 只保护已经完成 H2D 的 beneficiary，不再占用
唯一 predictive transfer inflight 名额；并发限制只对真正未完成的 transfer
生效。该修复通过 adapter 回归，尚未进入 GPU gate。

仍需后续处理的 v59 问题：

1. `PREPARE_HOST` 0 useful；
2. PREPARE/PREFETCH 的 expected-benefit 校准；
3. Host 侧 reentry-aware retention/cleanup；
4. reentry intent 在 CPU bytes 不足时应主动触发 PREPARE；
5. workload driver context-limit preflight；
6. `resident_service_window_ms` 与 target service deadline 的一致性。

### 5.21 2026-09-21 v58/v59 调度活性差异

相同 64-root manifest 下，v58/v59 分别运行约 10,316/30,489 秒，平均 GPU 利用率
28.72%/9.93%。两轮代码不同，不能单凭差异认定预测策略退化；v59 前 10,316 秒利用率
也仅约 7.5%，长尾并非唯一原因。v59 有 59 笔请求在首次物理 GPU service 前达到
30 分钟 queue timeout，v58 没有。v59 的一次 `admission_rescue` 遇到 native
`NO_TOKEN` 后约 23 分钟未出现新的 prefill ticket epoch，期间 rescue 持续占有
单请求准入优先权。代码确认原先没有 rescue 无进展/总占有期限，这是可导致 waiting
请求饥饿的活性缺陷，但尚不能证明它单独导致所有低利用率或恢复 prefill 所需的
native KV 可回收条件已经满足。

当前修复在 scheduler safe point 检查 rescue：默认 20 秒无进展或 60 秒总占有期限
到期，释放 allocator 预留、解除单请求优先权、短期禁止同一请求重新独占，并让
native `batch_is_full` 重新尝试一次。原生 `NO_TOKEN` 与物理锁的安全判定不变。
需要在后续 GPU 运行中分别观测 rescue 终结后是否恢复 prefill、retraction
候选不合格的具体原因和真正可回收的 native token 容量。

v59 工具调用也出现独立长尾：`tool_start` 至 sandbox `execute` 开始的中位等待
约 1.375 秒（v58 约 0.451 秒），但 sandbox 执行 P95 反而缩短。工具前的
`TOOL_START` 事件需等待 scheduler 轮询端 ACK；现有 trace 未记录 ACK 等待时长，
因此不能将全部工具变慢归因于事件通道或预测策略，后续需分段计时。

### 5.22 2026-09-21 TOOL_START 控制通道关键路径

v58/v59 的工具实现未发生相应变化，但 agent 回调在 `TOOL_START` trace 持久化
之后、真正执行工具之前同步发送 Unix datagram，并等待 SGLang scheduler safe point
应用事件及 ACK（单次超时配置 10 秒）。v59 的 scheduler/agent 并发差异可以放大
该等待；旧 trace 没有客户端送达与 ACK 时间戳，故不能将历史 1.375 秒的全部
中位启动等待唯一归因于此机制。

当前仅对普通 `TOOL_START` 使用 per-workflow 单发送线程：权威 trace 先写入，
回调将控制事件放入有界 FIFO 后立即执行工具；后续 `TOOL_END`、RETURN 和其他
控制事件仍在 FIFO 上按顺序等待前序 ACK。队列满、发送失败或 ACK 超时会使
控制通道 measurement 降级，不会静默丢失事件；workflow 退出前同步排空队列。
这消除了工具启动依赖 scheduler ACK 的同步障碍，但工具开始与 RCCG 应用之间
可能存在短暂延迟，短工具窗口能否用于迁移仍须在线实测。每个 workflow 的
`runtime_control_delivery_timing` 新增 tool-start queue/ACK P50/P95，可区分
事件发送积压和 scheduler 应用延迟；下一次 A/B 应同时报告原始
`tool_start -> sandbox invocation` 与 `tool_end -> next model request` 时延。

## 6. 当前阻塞项

1. prediction-to-action utilization gap 尚未闭合。初步 H200 高压运行中 predictive arm
   的 running set、GPU utilization 和 DMA/GPU overlap 均低于 baseline。
2. 当前 bounded composer 只评估前 2--4 个 request，并将最终物理候选限制为
   `1 beneficiary x 2 victims`。这是在线成本边界，不是全局最优保证。
3. schema-v5 已修复 action timing 低召回和 tool-error 多数类退化；boundary 仍应以 top-2
   scenarios 使用，不能把 top-1 accuracy 当作 exact action-unlock 预测。
4. `PREFETCH_GPU -> H2D ACK -> service lease -> first GPU service -> useful` 已在 v57 形成
   完整实测闭环；在线 precision、saved-stall 和吞吐收益仍未测量。
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
11. v46 的 wait-shadow publish-to-validation P95 为 2,283.03 ms；v49 已验证 same-safe-point
    修复将 true publish-to-validation P95 降至 0.304 ms、source-to-validation P95 降至
    44.83 ms。该阻塞项关闭，剩余问题是动作效用而不是控制链延迟。
12. v50 已证明 publish 链路延迟达标，但暴露 prepared binding 缺失、瞬时 engine-lock 失败
    和 stale prefetch certificate。`87411cc` 已修复，仍需在 v50 自然结束后运行新代码 gate。
13. v57 的 17 笔 predictive H2D 曾被 atomic allocator 检查拒绝；v58 已验证
    `a772a61` 将 service H2D 物理成功率提升到 24/25。
14. 64-root 长上下文 workload 超过 HBM+Host 的 2.95M-token 总容量；native writeback、
    predictive shadow 和 future reentry 在 Host 侧竞争。语义化 Host cleanup 已实现，但
    尚未进入已完成的 v58 运行。
15. 全局 KV value model 与 SSD tiering 均为可选未来分支，不进入当前关键路径；必须先用
    shadow 证据量化收益、开销和决策耦合风险。
16. v58 暴露的 request-abort restore command 清理和 H2D authority race 已由 `167edc3`
    代码修复，尚未经过 GPU 回归。
17. `efb5ded` 的 predictive DMA queue/batch 修复已通过 CPU correctness 回归，但尚未经
    GPU 高压验证；不能根据静态代码或离线测试宣称恢复全程 predictive 传输。
18. baseline attempt0 暴露 restore funding D2H callback 丢失；`fd8d7d7` 已增加强制
    watchdog 终态，但尚未经 GPU 回归。
19. baseline attempt1 暴露 telemetry journal compaction 被误判为 fatal；
    `c23b380` 已修复，尚需进入下一轮 baseline。
20. restore 全局等待和固定 timing lead 已由 `6c03357` 修复；两者尚未经过
    predictive GPU gate。
21. P90 bounded lead、soft service-wait 分离、在线 `prefetch_commit_ready`
    观测和 progress-aware watchdog 已通过 CPU 回归；均尚未经过 predictive
    GPU gate。已终止的 baseline `9c4e6c4` 不包含这些变更。
22. waiting-only admission 空转已由 main 修复；需要重新运行 contract-matched
    baseline，不得使用已空转的当前 attempt 计算 A/B 吞吐。
23. backend audit 绑定顺序已由 main 修复；此前 `baseline_startup_attempt0`
    只证明启动失败，不包含 workload 结果。
24. Action frontier 的 sole-blocker/fanout 修复已通过 policy/admission/retraction
    CPU 回归，尚未进入 GPU A/B。
25. native Host LRU replacement 缺少逐笔 telemetry；当前只能报告 Host displacement
    下界。若后续需要精确 value-model 评估，应先为 native host eviction 增加低频
    counters。
26. baseline v3 暴露的 ownership overage、partial-progress watchdog 和
    `DROP_UNOWNED` gate 分类已修复，尚未经过 predictive GPU 回归。
27. v59 已证明 predictive H2D useful attribution 和正向初步吞吐信号；但
    255 笔 PREPARE 全部是无 beneficiary 的 child tool-wait opportunistic partial，
    其中 211 笔完成拷贝、212 笔在 RETURN 后被标记 wasted，未注册 prepared causal
    binding。这不是“短工具一定没有迁移窗口”：旧策略只检查等待概率、HBM 和 Host
    水位，没有验证副本是否很可能用于后续卸载。当前修改将没有 projected deficit
    的 child tool-wait 剔除，优先选择确有未完成依赖的 WAIT_CHILD/WAIT_JOIN parent，
    从直接依赖的预测完成时间估计 PREPARE 窗口；缺少依赖时序则不猜测长窗口。
    parent 的局部影子备份无需在发布时找到 admission beneficiary，但仍不提前释放 GPU KV；
    无绑定模式下同一等待代际成功备份后不重复拷贝。单次只复制可安全物化的部分
    private KV，动作提交时重验物理闭包、Host 容量与锁；大于字节预算的单个 extent
    仍不能分割。
    CPU 回归与真实 GPU 有效消费尚待确认，不能把此修改称为已实现吞吐收益。

### 5.22 2026-09-22 v60 predictive H2D 回退定位

v59 有 25 笔完成的 predictive H2D（15 笔 useful）；v60_retry1 的
1,902 笔 `observed_service_prefetch` intent 中仅 1 笔 PREFETCH 入队，
最终随 shutdown aborted，完成的 predictive H2D 为 0。native 路径的
Host KV 可以直接成为预测性 H2D 来源，不要求此前发生 predictive PREPARE。
v60 仍有约 51.93 GB `prefetch_context` ACK，但不能归入 predictive H2D。

两个独立的在线阻塞已由 v60 audit 与代码定位：

- 同一个 context 的 PREFETCH 有 470 次拒绝、694 次 watch defer，
  拒绝原因仅为 `residency_transaction_inflight`。此前把尚未入队的
  `_current_semantic_residency_commit` 当作真实事务，而 predictive
  动作实际上在 observed 动作之前入队。该 context 无对应 observed
  residency enqueue。现在只将真实 pending transaction/restore 作为阻塞，
  暂存 observed 候选可在 predictive 入队失败时继续作为回退。
- 同 safe-point 的 2,848 次验证因 CPU 预算回退（包括重复尝试）；
  一个 native service-prefetch 样本的 `physical_rematerialization`
  达 120-150 ms，预算为 20 ms。原实现在可见 CPU KV 上穷举全部
  H2D closure 再选目标；现在该服务路径只构造到首个可用 closure，
  保留完整的物理证书、锁与容量校验，允许分批预取 native Host KV。
  其他需要全候选排序的路径仍保留穷举语义。

本轮代码修改已经通过定向 CPU 回归；该机制的真实 GPU 成功率和
首轮 H2D useful attribution **尚未经修复后 GPU 验证**，不能据此宣称吞吐回升。

## 7. 下一步

以下旧基线的关键路径仅适用于 Qwen3-Coder/0.5.2rc1，不能作为
Qwen3.5/v0.5.20 的已完成 gate。新模型应先按上文的 artifact、
admission、物理 ownership/事务及高压 A/B 顺序单独验收。

旧基线关键路径：

1. 修复 workload driver 的 context-limit preflight 和 TerminalProtocolError
   重试策略，确保 formal A/B 没有 workload 层错误。
2. 使用与 v59 完全相同的 `8312f66` 运行 contract-matched observed baseline，
   将 v59 的正向信号固化为同代码 formal A/B。
3. 下一次 predictive GPU gate 验证有目标的 parent PREPARE 是否形成真实 Host
   副本消费；逐笔统计 child/tool 与 parent/child-wait 候选、D2H 部分字节、
   actual COMMIT 的 page overlap、Host 驻留时长及 false-positive。v59 的
   unbound child tool-wait 拷贝不能视作收益；没有物理消费证据时归因维持
   censored/wasted，不能仅凭 parent reentry 标记 useful。
4. 将 Host 语义化清理与 request-abort/H2D authority correctness 修复一并纳入下一轮
   GPU gate，统计 dead/native-writeback/explicit
   cleanup bytes、forced recompute、Host miss 和 predictive H2D success rate。
5. 在 predictive H2D 成功率稳定后，测量相对于 reactive native demand-load 的 first-service
   latency 差值和端到端吞吐收益。
6. 若 Host forced eviction 仍然挤掉高价值 reentry KV，再离线评估全局 KV value model 和
   SSD cold tier；二者不得与当前调度修复同时上线。
7. 同时继续记录 event-to-hint 长尾和 safe-point P95/P99，不为降低开销重新关闭必要的
   `TOOL/JOIN` 风险触发。
8. execution 排序使用 RCCG 已知 unlock、schema-v5 token/HBM demand 和 top-2 boundary
   scenarios；任何单一分类 argmax 都不能覆盖 RCCG 确定性事实。
9. Gate 中任何 stale/OOD/物理化失败均回退 P5；不通过降低收益阈值制造动作。
10. PREPARE/PREFETCH 各自完成真实 beneficiary 消费后，再按 execution reorder、提前
   D2H、deficit-time COMMIT 和 latest-start H2D 分解收益，最后启动冻结 baseline/P6 A/B。
11. 当前 PREFETCH、shutdown 和归因 gate 通过后，再评估“固定物理上限 48、动态软目标
   `{32,48}`”：低 HBM 压力且存在 GPU-ready backlog 时扩展到 48；预测到 HBM 压力时
   停止新 admission 并自然排空到 32，不因阈值直接撤回 running request；parked KV 仍只
   通过 beneficiary-bound causal package 回收。该优化不得修改当前冻结实验。

该项属于后续吞吐优化，而不是当前 prediction-to-action gap 的替代解释。冻结 baseline 中，
`running >= 32 && waiting > 0` 约占 active time 的 4.89%（11.40 分钟），其中约 5.75 分钟
处于低于 70% HBM pressure 的状态；因此可能存在增益，但预期应通过统一 hard-48/graph-48
契约下的 fixed-32、fixed-48、dynamic-32/48 和 dynamic-32/48+P6 配对实验裁决。

执行细节见 `docs/implementation_plan.md`。

## 8. 权威资料

- 当前设计：`docs/beliefkv_design.md`
- 当前计划：`docs/implementation_plan.md`
- 文档导航：`docs/README_zh.md`
- 当前图解：`docs/beliefkv_jointplan_visual_zh.md`
- 历史状态：`docs/archive/snapshots/architecture_status_zh.md`
- 实验报告：`docs/experiments/`
