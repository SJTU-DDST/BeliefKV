# BeliefKV GPU-First Native-Subagent Oracle 执行计划

日期：2026-08-20

状态：代码和冻结输入已准备完成；GPU 语义门禁尚未执行，必须等待明确指令。

## 1. 路线变更

CPU Counterfactual Oracle 不再是正式实验的收益门禁。相关 schema、exporter 和模拟器保留用于契约测试与调试，不继续优化，也不能阻止 GPU O0/O3。

旧 parallel_analysis_2to3 通过外部 planner 启动 child、拼接报告并新建 supervisor。它只有 RCCG parent-child 关系，没有同一个 parent 对话在 JOIN 后继续的物理语义。旧 64K--160K context pack、两波到达 workload 因此降级为 diagnostic，不用于论文主要结论。

当前正式 profile 为 native_subagent_2to3：

~~~text
同一个 Deep Agents parent conversation
  -> 模型在一个 assistant turn 中发出 2--3 个原生 task calls
  -> 2--3 个 FRESH child context 并行执行
  -> parent 发布 JOIN_WAIT
  -> child RETURN 作为 task ToolMessage 回到原 parent messages
  -> JOIN_SATISFIED
  -> 同一个 parent context_id 在下一 context_epoch 继续 LLM
~~~

系统 prompt 只规定现实的 agent 角色配置和 2--3 个正交委派。runtime 不预先构造 DAG、不补造 child，也不按执行结果替换任务。

## 2. 已实现内容

- native_subagent_2to3 已接入 experiment config、P6 collection loader、server profile 和两个 launcher。
- 新 profile 使用 Deep Agents 原生 task 中间件，child 为 ContextMode.FRESH 且拥有独立 context；parent 不进入旧外部 planner/supervisor 特例。
- 新增只用于 semantic gate 的 --stop-after-first-native-join。它允许首个 JOIN 后的 parent LLM 调用完成，再受控停止。该轨迹标记为 semantic_gate_completed，不具有 JCT 或训练资格。
- 新增 scripts/analyze_native_subagent_semantic_gate.py，通过 native request ID 将 Deep Agents 事件与 SGLang request_physical_start 精确关联。
- 新增冻结清单 configs/p6/oracle_v2_native_subagent_v1/collection_plan.json。
- 旧 parallel_analysis_2to3、context pack 和两波 workload 保留，但证据角色仅为 diagnostic。

## 3. 冻结 workload

正式批次：

~~~text
batch_id                  oracle-v2-native-trace-64-r0
root                      64 个预注册 train instance
client in-flight          64
SGLang max running        32
KV pool                   850,000 tokens
Host pool                 96 GiB
arrival                    所有 root 同时提交
event-driven release      禁止
outcome replacement       禁止
context pack              禁止
predictor/actions          关闭
runtime policy             frozen P5 observed
~~~

64 个任务来自既有 H200 formal-train 冻结集合，只改变 agent runtime profile，不依据历史 completion、guard、patch 或 Oracle outcome 重新选择任务。

语义门禁批次 oracle-v2-native-semantic-gate-4-r0 使用其中 4 个项目不同的任务，不测吞吐。

## 4. GPU 语义门禁

GPU 空闲并得到指令后，只先运行 2--4 个 workflow。每个 workflow 必须满足：

1. 首个原生 JOIN 包含 2--3 个 child。
2. child 都是 FRESH 且 context 与 parent、其他 child 不同。
3. 每个 child report 都以匹配原生 task tool_call_id 的 ToolMessage 回到 parent trajectory。
4. parent JOIN 前后使用同一个 context_id。
5. JOIN 后 parent context_epoch 恰好递增一。
6. JOIN 后首个 parent request 能关联到真实 request_physical_start。
7. post_join_cache_hit_tokens / pre_join_parent_prompt_tokens >= 90%。
8. post-JOIN cache hit 非零，证明 parked parent KV 具有未来物理价值。
9. 受控停止发生在 post-JOIN parent LLM 完成后。

门禁不使用 post_join_cache_hit_tokens / post_join_prompt_tokens 作为唯一条件，因为 child ToolMessage 是合理的 uncached prompt delta。两种比例都会报告。

## 5. Native Trace

语义门禁通过后，运行 64-root frozen batch：

- predictor 和 predictive actions 关闭；
- 当前 work-conserving P5；
- 64 root eager submit；
- 不按结果替换；
- guard/timeout 留在分母；
- 完整的 censor-safe 局部区间继续保留；
- 记录 LLM token、RCCG、Tool、JOIN、Radix ownership、physical prefix reuse 和 transfer telemetry。

这一步直接做 GPU characterization，不再先运行 CPU opportunity gate。

## 6. Frozen GPU Replay

从 native trace 导出同一份冻结需求，固定：

- root arrival；
- LLM prompt/output token demand；
- SPAWN/RETURN/JOIN；
- tool wait；
- parent reentry；
- semantic owner 和 context epoch。

四个 arm 只能改变 scheduler/KV decision，不能重新调用自主模型产生不同路径。物理 sidecar 记录 token path、prefix sharing 和 observed shape，但不冻结旧 residency decision。

## 7. GPU Arm 顺序

先运行：

- O0：当前 observed JointPlan。
- O3：perfect-future execution + KV，由同一个 JointPlan 联合决策。

主指标：

- workflows/hour、makespan；
- GPU utilization、batch/action throughput；
- parked useful KV byte-time；
- HBM-blocked ready work；
- D2H/H2D overlap；
- parent resume stall；
- Oracle KEEP/DROP/D2H/H2D/recompute 动作数。

裁决：

~~~text
O3 gain >= 10%   -> 继续 O1/O2，分解 execution、KV 和 joint synergy
3% <= gain < 10% -> 存在优化空间，但不足以单独支撑 Major contribution
gain < 3%        -> 当前真实 H200 native-subagent 场景下核心收益不足
~~~

O3 没有可用 KV 动作时自然退化为 O0，不再要求 CPU gate 先证明 opportunity。

## 8. 当前停止点

当前只完成代码、测试、分析器和冻结 workload。未启动 SGLang、未执行 GPU semantic gate、未采集 native trace、未运行 O0/O3。下一条 GPU 指令必须从 4-root semantic gate 开始，不能直接跳到 64-root 长跑。
