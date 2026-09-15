# BeliefKV Perfect-Future Action-Space Oracle v2 执行方案

日期：2026-08-17

状态：GPU-first 路线已完成首组 Frozen-Demand O0/O3 配对。V2-0/V2-1 契约与 CPU
模拟器保留为调试资产；CPU opportunity/gain 不是 GPU 前置门槛。有效 O0/O3 均完成
18/18 workflow 和 1,208/1,208 request，但当前 O3 candidate 吞吐比 O0 低
16.45%；no-op-dominant finite-candidate Oracle gain 为 0%。按计划暂不运行 O1/O2，
先修复动作 beneficiary/stall 归因和 sequence-length-aware execution evaluation。详见
`docs/experiments/beliefkv_gpu_oracle_o0_o3_native18_2026-08-26_zh.md`。

## 1. 目标与裁决问题

本轮不继续调预测精度，也不先做独立的高压调度开销 gate。核心问题是：

> 在完全知道冻结 agent workflow 的未来需求后，当前 BeliefKV 所允许的 agent 执行、admission 和 KV 动作，最多能把固定任务集的完成吞吐提高多少？

实验必须分别回答三个问题：

1. 只改善 agent 执行和 admission 顺序，收益有多大？
2. 只改善 KV keep、offload、restore 和 victim 选择，收益有多大？
3. 同一个 JointPlan 联合决定执行与 KV 时，是否优于前两者中的最好结果？

只有第三项成立，才能把 execution-KV joint synergy 继续作为 BeliefKV 的核心论点。Oracle v2 是 action-space oracle，不声称求得全局最优调度。

## 2. 为什么旧 O1--O3 不能直接使用

旧 oracle 资产只能保留为 legacy offline diagnostic：

- 旧 O1 本质是静态拓扑排序，未运行当前 work-conserving、service-or-evict 和 beneficiary-bound reclaim。
- 旧 O2 只对 next-use eviction 使用 hindsight，没有联合当前 admission、restore 和 running retraction。
- 旧 O3 只是旧 O1 与旧 O2 的组合，不是单一 JointPlan。
- 旧 rolling replay 使用历史 wall-clock release、排队和迁移时延，已经受到旧 GPU、旧负载和旧策略污染。
- 旧 reactive 路径曾存在 pressure-only shrink、ordinary restore barrier 等矛盾，不能再作为 O0。

因此，新的 O0--O3 必须基于当前 h200_bf16_v5、修复后的 P5 work-conserving JointPlan 和真实 GPU 数据面重新实现。

## 3. 四个实验臂

| Arm | 未来执行信息 | 未来 KV 信息 | 物理执行机制 |
|---|---:|---:|---|
| O0 Current | 无 | 无 | 当前修复后的 observed-state JointPlan |
| O1 Agent Oracle | 有 | 无 | oracle 执行和 admission，observed KV |
| O2 KV Oracle | 无 | 有 | observed 执行和 admission，oracle KV |
| O3 Joint Oracle | 有 | 有 | 一个 JointPlan 联合决定两类动作 |

四个 arm 必须共享以下机制和约束：

- WORK_CONSERVING
- SERVICE_OR_EVICT
- BENEFICIARY_BOUND_RECLAIM
- NO_PRESSURE_ONLY_SHRINK
- RESTORE_ISOLATION
- NATIVE_AUTHORITY
- 相同的 30 秒 starvation floor，fairness 只作为最终 tie-break
- 相同 allocator、RadixCache、HiCache、transfer queue、safe point 和 ACK 路径

禁止为 oracle 增加实际系统不存在的资源，也禁止绕过物理状态检查直接修改 residency。

## 4. 冻结需求，而不是重跑自主 agent

O0--O3 不能分别重新运行自主模型决策。否则工具选择、subagent 数量、输出长度和工作流结构会变化，无法把吞吐差异归因于调度。

### 4.1 冻结内容

从 H200 BF16 canonical clean trace 导出 FrozenAgentDemand：

- stable workflow、invocation 和 context logical key
- RCCG 节点、SPAWN、RETURN、JOIN 和依赖边
- 每个 LLM call 的 prompt token、incremental token 和 output token demand
- action boundary 类型
- 工具从实际启动开始计算的相对 service duration
- child 的多轮 LLM 和工具 demand
- parent reentry 的 prompt growth
- context epoch 和语义 owner 关系

### 4.2 禁止导出的未来

以下字段受旧调度影响，不能作为 demand truth：

- 历史 queue wait
- 历史 admission 顺序
- 历史 request finish wall-clock
- 历史 JOIN absolute timestamp
- 历史 GPU service milliseconds
- 历史 D2H/H2D start 和 completion timestamp

JOIN、RETURN 和工具完成时间必须由冻结需求在候选调度下重新传播，不能照抄旧 trace。

### 4.3 稳定身份

Oracle truth 使用稳定逻辑键，不使用每次 replay 重新生成的 runtime UUID：

~~~text
LogicalInvocationKey =
  workload_instance
  + canonical_agent_path
  + parent_spawn_ordinal
  + invocation_ordinal
  + context_epoch
~~~

exporter 必须检测重复键、缺失边界、孤立 child 和不完整 JOIN。任何一项失败都不得进入 GPU replay。

## 5. OracleTruthProvider 契约

新增只读 OracleTruthProvider，并将能力拆成两个互相隔离的 view。

### 5.1 AgentFutureView

仅供 O1 和 O3：

- runnable frontier 的下一 action boundary
- 剩余 prompt、decode 和工具 demand
- child completion demand
- JOIN_ALL 或 JOIN_ANY 的依赖释放条件
- 当前 invocation 到 workflow terminal 的剩余 demand
- 某次 service 是否会解锁 TOOL、SPAWN、RETURN、JOIN 或 FINAL

### 5.2 KVFutureView

仅供 O2 和 O3：

- context 是否还会再次使用
- 下一次 use 或 reentry 前的因果 slack
- 未来 prompt 和 KV growth
- parked interval 的上下界
- 当前 bundle 的未来 beneficiary
- no-future-use 证明

### 5.3 防止信息泄漏

每次查询记录：

~~~text
arm
planner_epoch
logical_key
view
field
reason
~~~

O1 查询 KVFutureView、O2 查询 AgentFutureView、O0 查询任意 future view 都是 correctness failure。O3 可以访问两个 view，但只能通过同一个 JointPlan 发布动作。

### 5.4 Schema v2 replay cursor 与 context owner

future query 不接受调用者自行给出的 call ordinal。每次查询必须携带不可变
`OracleReplayCursor`，覆盖所有冻结 invocation 的：

- current call、PREFILL/DECODE/BOUNDARY_WAIT/terminal phase；
- 已 prefill prompt token 和已生成 token；
- active tool ordinal 与 elapsed service；
- completed invocation/tool 和 satisfied JOIN 集合。

provider 拒绝不存在的 call、phase/token 不一致、未完成外部边界后的越级推进，以及 revision、token、
tool 或 dependency 状态倒退。同一 revision 只能对应完全相同的 cursor。

KV future 先将 invocation 映射到 `semantic_owner`，再聚合同一物理 context 的所有
FRESH/FORK/RESUME/HANDOFF invocation。单个 invocation 结束不能独立产生 no-future-use 证明。

access audit 使用聚合计数和有限 recent ring；完整逐查询记录只能通过外部有界异步 JSONL sink
输出，provider 本身不随运行时长无限增长。

## 6. PerfectFutureJointPlanner

不要把 perfect future 伪装成 FrontierBeliefSnapshot。当前预测 overlay 主要为局部 PREPARE_HOST/PREFETCH_GPU 服务，而且 observed candidate ordering 明确不使用预测。复用该接口会让 oracle 仍受旧预测门禁和局部动作空间限制。

新增 PerfectFutureJointPlanner，复用 AsyncSemanticJointPlanner 的状态、动作和 safe-point 提交机制，但独立消费 OracleTruthProvider。

### 6.1 单次规划流程

1. 从最新 RCCG 和物理快照得到事实上的 runnable frontier。
2. 根据 arm 获取允许的 future view。
3. 生成有限个 execution package。
4. 对每个 package 计算 startup、restore 和短期 growth deficit。
5. 生成与 deficit 绑定的 KV victim、keep 和 restore 候选。
6. 联合评估 action unlock、GPU service、HBM 和 PCIe 可行性。
7. 输出唯一 JointPlan。
8. safe point 重新物化 live bundle 并校验 read-set。
9. 计划 stale 或物理不可行时回退当前 observed bounded seed。

### 6.2 Execution package

控制候选规模，只生成以下确定性 package：

- OBSERVED：当前 P5 选择
- MIN_REMAINING_DEMAND：优先完成剩余总 demand 较短的 invocation 或 workflow
- ACTION_UNLOCK：优先产生 TOOL、SPAWN、RETURN、JOIN 或 FINAL 的 service
- MAX_BATCH_FILL：在 token 和 active-request budget 内最大化立即可服务 demand

O1 和 O3 在这些 package 中选择；O0 和 O2 保持 observed execution。第一版不做指数级全排列，不需要证明全局最优。

### 6.3 KV 动作规则

O2 和 O3 使用以下顺序：

1. 已证明 no-future-use 的 KV 优先 drop 或回收。
2. 对 parked context 比较 causal slack 与 D2H、H2D、commit guard。
3. COMMIT_CPU 必须绑定一个存在 startup 或 growth deficit 的 beneficiary。
4. victim 排序优先考虑可释放 exclusive bytes、未来 use 距离和 restore 成本。
5. prefetch 使用 latest-feasible start，避免过早占用 HBM。
6. future truth 不完整时回退 observed 策略，不能将未知成本视为零。

每个动作仍必须满足：

- allocator capacity
- live Radix closure 和 owner
- transfer guard
- Host copy 和 Host capacity
- restore isolation
- safe-point certificate
- dispatch、ACK 和 terminal 事务守恒

## 7. 代码改动

### 7.1 新增文件

- beliefkv/oracle/__init__.py
- beliefkv/oracle/contracts.py
- beliefkv/oracle/truth_provider.py
- beliefkv/policy/perfect_future_joint.py
- beliefkv/experiments/oracle_gpu_replay.py
- scripts/export_perfect_future_truth.py
- scripts/run_perfect_future_oracle_gpu.py
- scripts/analyze_perfect_future_oracle.py
- configs/p6/h200_bf16_oracle_v2/

### 7.2 最小修改文件

- beliefkv/policy/joint_scheduler.py：抽取 observed seed、candidate 和 JointPlan 提交的公共接口。
- beliefkv/runtime/joint_shadow.py：允许携带 oracle planner 的不可变 semantic delta，但不得携带物理 extent 指针。
- beliefkv/runtime/sglang_v052rc1.py：在现有 safe point 调用 oracle plan rematerialization；默认完全关闭。
- beliefkv/experiments/deepagents_swebench.py：增加 frozen-demand replay 入口，不改变 autonomous collection 路径。
- 配置 loader：增加 oracle mode、truth identity/digest 和 replay contract。

### 7.3 配置项

~~~text
perfect_future_oracle_mode:
  disabled
  o1_agent
  o2_kv
  o3_joint

perfect_future_truth_path
perfect_future_truth_id
perfect_future_truth_digest
perfect_future_replay_id
perfect_future_max_execution_packages
perfect_future_horizon_calls
~~~

默认值必须是 disabled。普通 P5/P6 服务启动时不加载 truth、不建立 oracle 索引，也不增加在线开销。

## 8. Workload 与运行配置

### 8.1 Workload 选择

正式输入只有 configs/p6/oracle_v2_native_subagent_v1/collection_plan.json。64 个任务来自既有 formal-train 冻结集合，不按历史 outcome 或 Oracle 结果筛选；全部 root 同时提交。模型在同一 parent conversation 中使用原生 task 产生 2--3 个 FRESH child，JOIN 后由同一个 parent context 继续。

旧 natural opportunity pool 与 KV-pressure context-pack/two-wave 输入保留为 diagnostic，不估计正式 workload prevalence，也不进入论文主结果。GPU O0/O3 不再等待 CPU opportunity gate。

### 8.2 运行配置

- Qwen3-Coder-30B-A3B-Instruct BF16
- SGLang 0.5.2rc1 加当前 BeliefKV patch
- h200_bf16_v5
- max_total_tokens = 850000
- Host KV pool = 96 GiB
- max_running_requests = 32
- CUDA Graph batch = 1、2、4、8、16、24、32
- 64 root eager submit，client in-flight=64
- 无事件驱动放量、context pack 或 outcome replacement
- 相同 tokenizer、模型 revision、seed 和外部工具 demand

不要为了制造 KV pressure 缩小 KV pool。若 native workload 缺少 KV 竞争，O3 应自然退化为 O0。

### 8.3 GPU replay 语义

replay 仍向真实 SGLang 发起请求并真实生成 KV：

- temperature = 0
- 固定 seed
- ignore_eos = true
- 按冻结 output token demand 截断
- observation payload 经 tokenizer 验证 exact prompt delta
- 每个 arm 使用本 arm 实际生成的 token 内容继续上下文

四个 arm 必须读取同一个 truth artifact，并保持 token demand、RCCG topology、context epoch 和 external demand 的 canonical bytes 一致。若这些结构字段不同，本组 A/B 无效。token identity 差异单独记录，不作为结构失配。

## 9. 实施顺序

### V2-0：冻结契约

- 定义 FrozenAgentDemand、LogicalInvocationKey 和 arm capability。
- 为 truth access ledger、canonical byte stability 和跨 arm 泄漏编写单测。
- 将旧 O1--O3 明确标记为 legacy_offline_diagnostic_only。

通过条件：相同 trace 导出逐字节稳定，错误 arm 查询立即失败。

实施状态（2026-08-17）：schema v2 已完成。严格 parser 拒绝未知字段、重复 JSON key 和隐式
标量转换，并校验 parent/child creation edge、context mode/semantic owner、tool boundary、每个 JOIN
waiter 和 workload terminal 闭包。所有查询必须携带单调 `OracleReplayCursor`；KV future 按
semantic owner 聚合。O0 无 future view，O1/O2 只能读取各自视图，O3 可读取两者。access audit
使用聚合计数和有限 recent ring。

按项目约定，普通调度对象不增加内容摘要；但 frozen truth 需要跨四次顺序运行防止同名 artifact
被覆盖，因此在导出/加载时一次性计算 `SHA256(canonical_bytes)`。四臂必须同时匹配显式
`truth_id` 和 `truth_digest`。相关 CPU 定向与策略/RCCG 回归为 41 passed。

### V2-1：导出 16-root truth

- 复用 CounterfactualTraceBuilder 的解析和 dependency extraction。
- 不复用其受负载影响的 release delay。
- 生成 truth、workload manifest、provenance、显式 truth_id 和 truth_digest。
- 审计 SPAWN、RETURN、JOIN、tool duration 和 token demand 闭合。

通过条件：16/16 workflow closure complete，无 runtime UUID 依赖。

实施状态（2026-08-20）：已从两条 H200 BF16 16-root trace 导出 schema v2 truth 与独立的
`FrozenPhysicalSidecar`。两条工件分别覆盖 1,241/1,249 次 LLM call、1,824/1,960 次工具调用、
32 次 SPAWN 和 16 次 JOIN；16/16 workflow closure complete。物理 sidecar 只保存匿名 token path、
semantic owner、call ordinal、shared/exclusive token 和 context growth，不冻结历史 residency 或 admission。

### V2-1.5：CPU Counterfactual Oracle Estimate

- 离散事件重放 LLM ready、prefill/decode、TOOL、SPAWN、RETURN、JOIN 和 parent reentry。
- C0 为 observed-order + reactive LRU；C1 枚举四类有限 execution package；C2 使用 causal-next-use
  KV、proactive shadow 和 latest-feasible restore；C3 在一个模拟器内联合两类动作。
- 固定 HBM 850K tokens、Host 96 GiB、max running 32、prefill chunk 16K。
- 同时运行 SLOW、NOMINAL、GRAPH32-SENSITIVITY 与 zero/measured-fastpath 包络。
- CPU 结果不能替代真实 GPU O0--O3；C1/C3 当前是 bounded candidate planner，不是全局 optimum。

实施状态（2026-08-20）：两条 C0 reconstruction 的 makespan 误差为 2.98%/2.70%，batch mean
误差为 0.59%/0.12%，pressure 误差为 3.67/0.66 个百分点。C1 将 C0 与四种固定 execution
policy 分别完整运行，C3 同时比较 C0、C2、全部 C1 和四种 execution+C2 候选，因而在有限候选
集合内严格支配 no-op。32-root 复跑中 C1/C3 gain 为 [0, 0.523%]，C2 和 joint synergy 为 0；
固定 NOMINAL+measured C0 row 中 eviction/stall-free/net-positive opportunity 仍均为 0。结论仍是
旧 workload 机会不足，有限候选 lower bound 也不能冒充全局 Oracle。完整报告见
[CPU Counterfactual Oracle Estimate](experiments/beliefkv_cpu_counterfactual_oracle_estimate_2026-08-20_zh.md)。

### V2-2：两 workflow O0 replay smoke

- 只验证冻结 demand 能在真实 GPU 上重放。
- 检查 token count、context epoch、RCCG 和 terminal。
- 检查 oracle disabled 时与当前 O0 路径一致。

通过条件：2/2 terminal，无 orphan transaction、lease 或 command。

### V2-3：接入 O1

- 实现 AgentFutureView。
- 生成四类 execution package。
- 使用 observed KV policy。
- 输出每次选择的 package、unlock 类型和被跳过的 observed candidate。

通过条件：访问隔离正确，O1 不读取 KV future，所有 admission 经过 native authority。

### V2-4：接入 O2

- 实现 KVFutureView。
- 使用 observed execution package。
- 联合 deficit、victim 和 beneficiary。
- 验证 no-future-use、latest prefetch 和 restore 路径。

通过条件：O2 不读取 agent future；至少通过注入式用例覆盖一笔 D2H replacement 和一笔 H2D restore。

### V2-5：接入 O3

- 在一个 planner epoch 内同时生成 execution package 与 KV action。
- 只发布一个 JointPlan。
- safe point 对 execution 和 residency 使用同一 read-set generation。

通过条件：不存在先选 agent、后由独立 KV planner 推翻的双重调度。

### V2-6：Native parent continuation 与 GPU-first replay

1. 使用 native_subagent_2to3。模型通过原生 task 在同一轮发起 2--3 个 FRESH child；parent JOIN_WAIT 后接收 child ToolMessage，并由同一 context_id 在下一 context_epoch 继续。
2. 旧 parallel_analysis_2to3、context pack 和两波放量只保留为 diagnostic，不作为论文 workload。
3. 首先执行 4-root semantic gate，并在首次 JOIN 后 parent LLM 完成时受控停止；通过 native request ID 验证 parent prefix retention 至少 90%。
4. 随后按 configs/p6/oracle_v2_native_subagent_v1/collection_plan.json 同时提交 64 个冻结 train root，client in-flight=64，server max running=32，KV pool=850K，Host=96 GiB；无事件驱动放量或 outcome replacement。
5. 从真实 native trace 导出 frozen GPU demand，使所有 arm 共享相同 LLM token、SPAWN/RETURN/JOIN、tool wait、arrival 和 parent reentry。
6. 先运行 O0/O3；不再用 CPU opportunity gate 阻止 GPU replay。O3 gain 达到 10% 后才运行 O1/O2。

## 10. 正确性与机会门槛

### 10.1 硬正确性

- 每个 arm 16/16 到达 terminal 或具有相同的明确外部失败。
- 四个 arm 使用同一个 truth_id 和 truth_digest，且 frozen demand canonical bytes 完全一致。
- O0 不访问 future；O1/O2 不发生跨 view 泄漏。
- predictor、旧 risk overlay 和 legacy rolling oracle 全部关闭。
- 无 OOM、scheduler exception、ordinary restore barrier。
- shutdown 时 0 pending transaction、lease、funding、command 和 restore obligation。
- 所有物理动作均有 safe-point commit、dispatch、ACK 和 terminal 记录。

### 10.2 GPU native opportunity

Peak HBM 仍然不是充分证据。GPU O0/O3 必须同时报告 parked useful KV、HBM-blocked ready work、可迁移 closure、future reentry、D2H/H2D 与 parent resume stall。但这些统计不再构成实验启动前的 CPU gate：O3 没有机会时应在相同 frozen demand 上自然退化为 O0。

禁止通过缩小 KV pool、事件驱动放量、context pack 或 outcome replacement 制造正例。

## 11. 指标与最终裁决

固定 workload 时，主指标为 makespan 和 workflows/hour：

~~~text
gain(Oi) = makespan(O0) / makespan(Oi) - 1

joint_synergy =
  min(makespan(O1), makespan(O2)) / makespan(O3) - 1
~~~

辅助指标：

- workflow JCT mean、P50、P95
- time-to-action-unlock
- GPU utilization 和 decode throughput
- non-empty batch mean、running 和 waiting
- HBM/Host pressure
- D2H/H2D bytes、时间和有效带宽
- stranded resident KV
- beneficiary service latency
- recompute tokens
- starvation floor 触发次数

裁决规则：

- O3 相对 O0 小于 10%：当前 action space 或 workload 的联合优化空间偏弱，应停止扩大预测模块。
- O3 相对 O0 为 10%--20%：存在中等空间，需要定位收益来自 execution 还是 KV。
- O3 相对 O0 超过 20%：值得继续用 FrontierBelief 逼近该上界。
- 只有 O3 明显优于 O1 和 O2 中更好的一个，才能声称 execution-KV joint synergy。
- 若 O1 显著、O2 不显著，主线应转为 agent execution/admission。
- 若 O2 显著、O1 不显著，主线应转为 causal KV residency。
- 若二者都不显著，不能继续假设预测性 JointPlan 会自然提高吞吐。

## 12. 当前关键路径

已完成：

1. V2-0 schema v2、cursor、semantic-owner future 和 arm 隔离契约。
2. V2-1 truth/physical sidecar exporter；CPU estimator 保留为 contract/debug 工具。
3. native_subagent_2to3 runtime profile、受控 first-JOIN gate、物理 prefix reuse analyzer。
4. 4-root semantic gate 与 64-root eager-submit 冻结清单。

等待 GPU 指令后执行：

1. 4-root semantic gate；失败时只修语义连续性，不进入长跑。
2. 64-root predictor-off native trace characterization。
3. Frozen GPU replay export。
4. O0 与 O3；按 10%/3% 门槛决定是否继续 O1/O2 与预测式 BeliefKV。

暂不执行：CPU Oracle 调参、旧 pressure workload 正式实验、独立高压 JointPlan 开销 gate、baseline 移植和 predictor 重训。
