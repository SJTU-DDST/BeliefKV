# H200 Native-Subagent Parent Continuation 语义门禁

日期：2026-08-21

## 结论

本轮验证了 BeliefKV 正式 workload 所需的物理对话连续性，但没有通过完整的
4-root clean-completion gate：

- prompt 修复后的 4 个 root 均在首个 assistant task turn 中创建 2 个 FRESH child；
- 3 个完整 workflow 的独立物理 analyzer 为 3/3 passed；
- 第 4 个 pytest workflow 在 1/2 child RETURN 后出现工具调用长尾，被人工取消；
- 因此可以声称 native parent continuation 机制成立，不能声称 4/4 workload 正常结束；
- 本轮不是吞吐或 KV migration 实验，不能用于 O0/O3 性能结论。

最终 analyzer：

experiments/raw/oracle_v2_native_subagent_semantic_gate/20260821T103631Z/native_subagent_semantic_gate_completed3.json

## 冻结环境

- commit：3680ed2
- profile：h200_bf16_v5
- model：Qwen3-Coder-30B-A3B-Instruct BF16
- context limit：262,144
- KV pool：850,000 tokens
- Host pool：96 GiB
- max running requests：32
- CUDA Graph：[1, 2, 4, 8, 16, 24, 32]
- predictor/predictive actions：关闭
- policy：P5 observed JointPlan

## 首轮负结果

首轮路径：

experiments/raw/oracle_v2_native_subagent_semantic_gate/20260821T102037Z

首轮 4 个 workflow 都保持 parent context 连续并复用了 prefix，但仅 Django 在同一个
AIMessage 中发出 2 个 task call。其余 3 个 workflow 将两个 child 拆成两个单成员 JOIN。
根因是 system prompt 同时包含：

- natural mode 的“没有 required/preconfigured count”；
- native profile 的“exactly two”。

修复将 natural/native delegation prompt 完全分离，并要求 native profile 在任何
repository tool 之前用同一个 AIMessage 发出两个 task call。回归测试为 113 passed。

## 修复后结果

| instance | 完整结果 | child | 时长 | parent prefix retention | post-prompt hit |
|---|---:|---:|---:|---:|---:|
| django__django-15368 | passed | 2 | 110.89 s | 100% | 89.12% |
| psf__requests-2931 | passed | 2 | 228.47 s | 100% | 86.69% |
| pydata__xarray-4356 | passed | 2 | 531.52 s | 100% | 88.57% |
| pytest-dev__pytest-5631 | cancelled | 2 | 未完成 | 未进入 post-JOIN | 不适用 |

三个完整 workflow 均满足：

1. 两个 child 在一个原生 JOIN 中创建；
2. child context 为 FRESH，且互相及与 parent 不同；
3. 两个 task ToolMessage 都按 tool_call_id 回到 parent trajectory；
4. JOIN 前后 parent context_id 相同，context_epoch 从 0 递增到 1；
5. post-JOIN request 可关联到真实 SGLang physical start；
6. post_join_cache_hit_tokens / pre_join_parent_prompt_tokens = 1.0。

post-prompt hit 低于 90% 是正常的：child ToolMessage 是新增的 uncached prompt delta，
不能用整个 post-JOIN prompt 作 parent prefix retention 的分母。

## GPU 与系统观测

- GPU utilization：mean 51.35%，P50 48.5%，P95/max 100%；
- utilization >= 90% 的采样占比：45.29%；
- 物理 HBM 最小空闲：218 MiB，无 OOM；
- BeliefKV 逻辑 resident pressure 峰值：57.55%；
- D2H/H2D：0，本轮 pressure 不足以触发迁移；
- critical allocator/Radix/scheduler failure：0；
- 服务已停止，无残留模型、workload 或 sandbox 容器。

## 长尾说明

Xarray workflow 在完成前出现一次 guard intervention，因此只用于语义和物理 prefix
证据，不用于 clean JCT。未完成的 pytest workflow 在人工取消时记录：

- 首个 JOIN 成员数：2；
- child RETURN：1/2；
- LLM submit：36；
- tool end：642；
- censor：0。

这不是 BeliefKV admission/restore 死锁，但说明 native child 执行存在显著长尾。后续
64-root trace 必须保留固定 wall-clock/取消语义并将未完成 workflow 留在分母，不能等待
所有 root 无限完成，也不能把长尾任务替换掉。

## 裁决

Native parent continuation 的语义和物理 prefix reuse gate 通过。下一步可以采集冻结的
64-root native trace，但必须等明确指令；O0/O3、O1/O2 和预测动作仍未执行。
