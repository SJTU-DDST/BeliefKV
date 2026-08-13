# H200 BF16 正式 Train 采集结果

日期：2026-08-13  
状态：64 个 train workflow 采集完成；calibration/test_id 未启动。

## 目的与冻结条件

本轮用于采集 P6 FrontierBeliefModel 的 agent 语义轨迹，不是 P5/P6 性能 A/B，也不用于证明
KV offload 收益。冻结条件如下：

- 模型：Qwen3-Coder-30B-A3B-Instruct BF16；
- GPU：单张 NVIDIA H200；
- SGLang：0.5.2rc1，commit `18f91eb639084825717c0e3c3c7273492812ab71`；
- profile：`h200_bf16_v4`，850,000 KV tokens，96 GiB Host pool，262,144 模型窗口；
- policy：P5 observed JointPlan，启用 batch admission、dynamic working set 和 running retraction；
- predictor 与 predictive actions：关闭；
- workload：SWE-bench Verified train split，64 个预冻结实例，32 个
  `parallel_analysis_2to3`、32 个 natural fan-out；
- 每个 shard 16 个 root，按 8+8 两批启动，批间隔 20 秒。

冻结 collection plan SHA-256 为
`013e045b41a1760aa9c7776c74cb783ab8763c6ceae29dac76ad7c0b3b0c694e`，runtime profile
SHA-256 为 `5ece5b5075193856b1cf7fff081378fe1a4040734bd80133838713a9a90cd6ba`。

## 四个 Shard

| shard | fan-out | 完成 | measurement valid | native JCT | LLM | tools | child/JOIN | 时长 | peak running |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| train-01 | parallel | 16/16 | 11 | 10 | 1,241 | 1,824 | 32/16 | 7,113.8 s | 31 |
| train-02 | parallel | 16/16 | 8 | 10 | 1,249 | 1,960 | 32/16 | 8,470.9 s | 32 |
| train-03 | natural | 15/16 原始，补跑后 16/16 | 5+1 | 7+1 | 931+92 | 3,041+86 | 18/18 | 9,652.1 s | 18 |
| train-04 | natural | 16/16，替换 2 条 | 2 原始，替换后为 3 | 6 原始，替换后为 7 | canonical 计入替换轨迹 | canonical 计入替换轨迹 | 24/24 | 11,056.6 s | 17 |

原始 shard 的 BeliefKV commit 依次为 `00efd80`、`e887208`、`c6762c7`、`d3bd7b1`。
SGLang patch、runtime profile 和 P5 policy 代码保持不变；期间变化仅包括后续 shard 的 image lock
以及 SWE-bench workspace/artifact harness 修复。每个 run 的完整 commit 和 contract hash 已写入
`result_selection_manifest.json`。

## Harness 故障与定向替换

原始结果暴露了两个与 BeliefKV 数据面无关的 harness 问题：

1. `pydata__xarray-3993` 在 runtime 已结束后损坏 `.git/HEAD`，artifact extraction 无法生成
   `result.json`；
2. `pydata__xarray-7229` 和 `pytest-dev__pytest-5840` 的 workspace 由
   `git clone --shared` 创建，容器内的 Git alternates 指向不可见的 Host 路径，分别产生 4 次和
   66 次 Git object/path 错误。

修复后 workspace 改用 `git clone --no-hardlinks --no-checkout`，不再生成 alternates，同时保持每个
workflow 的 object store 独立。`tests/test_deepagents_swebench.py` 在 `beliefkv-agents` 环境中为
76 passed。

三条实例均只按“同一预冻结 instance 定向替换”原则重跑，没有按模型结果筛选：

- `xarray-3993`：完整 clean replacement；
- `pytest-5840`：完整 clean replacement；
- `xarray-7229`：Git 故障消失且系统轨迹完成，但模型达到 512 superstep 并触发 guard；仅保留干预前
  的局部 action/token 标签并显式 censor，不使用其 clean terminal/native JCT 标签。

定向补跑无 OOM、scheduler exception 或 SGLang 数据面错误；shutdown 前事务关闭。

## Canonical 64 结果

替换原始三条受污染/不完整记录后，canonical 数据为：

- 64 个结果、64 个唯一 instance，64/64 outcome 为 `completed`；
- 64/64 `system_jct_eligible`；
- 35/64 `native_agent_jct_eligible`；
- 28/64 `measurement_valid`，28/64 `task_correctness_valid`；
- 5,000 次 LLM request、11,194 次 tool call；
- 108 个动态 subagent、76 次 JOIN_ALL；
- 23 个 workflow 出现 runtime guard，共 30 次 intervention；
- canonical artifact collection error 为 0。

`task_correctness_valid` 不作为本轮性能轨迹的总门禁。后续训练按标签类型分别过滤：系统性能标签要求
`system_jct_eligible`，自然 agent terminal/JCT 要求 `native_agent_jct_eligible`，Frontier 局部标签可保留
censor 前的 decision point，但必须携带准确的 censor reason。

项目分布为 Django 24、pytest 12、xarray 14、requests 6、pylint 6、Flask 1、seaborn 1。该分布来自
预冻结 train manifest，不允许为改善结果重新选择任务。

## Batch Admission

`observed_admission_summary` 的 native prefill 结果如下。非空 mean 排除了没有签发 ticket 的空 epoch：

| shard | all-epoch mean | non-empty mean | max | batch > 1 / non-empty |
|---|---:|---:|---:|---:|
| train-01 | 1.113 | 1.148 | 5 | 127/1,094 = 11.61% |
| train-02 | 1.140 | 1.152 | 4 | 137/1,095 = 12.51% |
| train-03 | 1.054 | 1.061 | 4 | 56/1,023 = 5.47% |
| train-04 | 1.024 | 1.059 | 3 | 71/1,228 = 5.78% |

相较旧实现约 1.02 的平均 prefill batch，parallel shard 有明确改善；natural shard 的改善很小。
限制因素不是 batch compiler correctness，而是 natural workload 在任一时刻可同时执行的 GPU-ready
request 较少，这也与 peak running 仅 17--18 一致。

## Dynamic Working Set

动态 working set 在四个 shard 都发生了 `gpu_fill` 与 `hbm_pressure` 模式切换。状态变化事件中：

- 最大 active workflow 数依次为 4、4、4、3；
- 最大 selected ready request 数依次为 5、4、4、3；
- pressure action 在变化事件中分别出现 373、347、332、525 次。

这里必须区分两个 HBM 口径：

- workload summary 的 `max_resident_pressure` 只统计 SGLang `/metrics` 暴露的 resident token，四轮为
  33.5%--46.2%；
- dynamic working set 使用
  `max(BeliefKV tracked bytes, capacity - native allocator available bytes)`，包含 native allocator 已占用但
  尚未归因到 BeliefKV context 的空间，因此 audit 中最高接近 100%。

后者用于安全地收缩 working set，不能与 tracked resident ratio 直接比较。下一阶段需要继续补齐 native
allocator delta 的归因，不能把两者差值直接解释为 protected KV。

## KV 迁移与局限

四个 formal shard 的 `physical_kv_transfer_observed` 均为 false。BeliefKV dispatch 的 39、46、49、158
笔动作全部是 `drop_terminal_private` 生命周期清理，总计约 28.68 GB，不是策略性 D2H/H2D。因此本轮只能
用于 agent semantic/data coverage 与 admission correctness，不能用于证明 KV migration 性能收益。

其他限制：

- natural shard 的 GPU-ready 并发不足，尾部工具等待仍明显；
- SGLang metrics monitor 有少量瞬时 HTTP polling error，但 server log 无 OOM/scheduler exception；
- 35/64 native clean JCT 与 28/64 measurement-valid 说明 runtime guard/censor 协议仍必须进入训练数据契约；
- 本轮没有启动 calibration/test_id，也没有启用 predictor，避免训练前数据泄漏。

## 结论

64-train 采集已完成，可以进入 replacement-aware P6 dataset export、reentry/censor coverage 审核和
FrontierBeliefModel 训练。当前结果同时说明，仅用 16 root/8+8 arrival 并不能稳定形成 H200 上的高 KV
迁移压力；正式系统性能 A/B 需要单独设计更持续的 GPU-ready arrival，而不能修改这批已冻结训练轨迹。
