# BeliefKV H200 Native-Subagent 64-Root Trace

日期：2026-08-21

## 1. 实验目的

本轮只采集真实 native-subagent characterization trace，不运行 O0/O3，不启用
FrontierBelief 或任何预测动作。工作流由同一个 Deep Agents parent 原生调用两个
FRESH child，parent 在 JOIN 后沿同一对话继续执行。

原始数据目录：

`experiments/raw/oracle_v2_native_subagent_trace/20260821T111605Z`

## 2. 冻结配置

- 模型：Qwen3-Coder-30B-A3B-Instruct，BF16，TP=1；
- GPU：NVIDIA H200 NVL；
- context length：262,144 tokens；
- GPU KV pool：850,000 tokens，约 83.56 GB；
- Host KV pool：96 GB；
- `max_running_requests=32`，CUDA Graph 最大 batch size 32；
- 64 个预注册 train root 同时提交，client in-flight=64；
- fan-out：`native_subagent_2to3`，本轮模型实际均创建 2 个 child；
- 策略：frozen P5 observed JointPlan；
- predictor、predictive overlay 和 predictive action 全部关闭；
- request timeout 和 agent activation wall-clock 均为 7,200 秒；
- 不替换失败或超时任务。

运行期间 BeliefKV 源码保持不变。最终 collection contract 记录
`runtime_source_stable=true`。

## 3. Agent Trace 覆盖

| 指标 | 结果 |
|---|---:|
| root workflow | 64 |
| child SPAWN | 128 |
| child RETURN | 117 |
| JOIN create / wait | 64 / 64 |
| JOIN satisfied | 19 |
| LLM submit / result | 1,880 / 1,880 |
| tool start / end | 3,683 / 3,683 |
| workflow end | 64 |
| call censored | 209 |

所有 root 都真实生成了两个并行 child；不是预先由外部 orchestrator 创建。语义门禁
已在上一轮证明 parent JOIN 前后 context/epoch 连续且 prefix retention 为 100%。本轮进一步
覆盖了长时间 child 执行、部分 RETURN/JOIN、parent reentry、工具错误和固定窗口取消。

## 4. 完成与截止

整轮 wall-clock 为 8,967.58 秒：

- 18/64 workflow 返回结构化 completion；
- 39/64 因单请求 7,200 秒 `APITimeoutError` 结束；
- 固定窗口和 drain grace 耗尽时还有 11 个 workflow 未结束；停止服务后，其中 7 个以
  `APIConnectionError` 写入结果，其余已在关闭传播期间结束；
- 18 个 workflow 满足 system JCT eligibility；
- 0 个满足严格 native-agent JCT gate，主要因为 activation wall-clock guard 参与收尾；
- 0 个 SWE-bench correctness success 不代表模型正确率，本轮没有运行官方 harness，且性能
  trace 不以任务补丁正确性为门槛。

截止记录位于 `collection_cutoff.json`。未完成 workflow 保留在分母，且没有 outcome
replacement。

本轮暴露出一个 runtime 边界问题：activation deadline 只在下一次 model boundary 检查，
没有取消已经排队或执行中的 HTTP request。因此 7,200 秒后仍可能提交或等待新的长请求。
后续正式 O0/O3 前必须把绝对 workflow deadline 传播到所有 descendants 和在途请求。

## 5. GPU 与 KV 压力

| 指标 | 结果 |
|---|---:|
| GPU utilization mean / P50 / P95 | 4.07% / 0% / 30% |
| HBM pressure 最大值 | 100% |
| HBM pressure >=80% 的采样占比 | 63.00% |
| HBM pressure >=95% 的采样占比 | 53.35% |
| Host KV 峰值 | 95.996 GB |
| migratable GPU KV 峰值 | 78.22 GB |
| engine-locked GPU KV 峰值 | 69.17 GB |

服务端长期维持约 27-32 个 running request，并存在 waiting backlog，但 GPU 利用率仍低。
这说明问题不是 root 数量不足。主要结构是：长 child decode 与工具阶段交错、HBM 满载、
大量 active Radix path 被锁、模型请求获得稀疏 service，表面 continuous-batching 并发没有
转化为高 GPU 利用率。

## 6. KV 传输

| 来源 | 方向 | 次数 | 总字节 |
|---|---|---:|---:|
| Native HiCache write-back | D2H | 4,494 | 123.03 GB |
| Native HiCache demand-load | H2D | 10 | 2.87 GB |
| BeliefKV `offload_context` | D2H | 4 | 15.30 GB |
| BeliefKV `prefetch_context` | H2D | 5 | 14.89 GB |

Native write-back 是小 extent 的后台落盘式 Host copy，不能记作 BeliefKV 策略动作。
BeliefKV 的四笔显式 retraction/offload 均到达 completed，随后四个对应 context 完成 H2D
恢复；另有一笔 109 MB 的 restore prefetch。显式 D2H completion 约为 0.55-1.18 秒，
H2D completion 约为 0.64-2.58 秒。

最终 runtime summary 记录：

- 4 次 retraction planned / reclaim confirmed；
- 4 次 overlap barrier request / drain；
- 4 次 residency offload queued；
- 所有 online action 均携带 source JointPlan ID；
- 无 pending residency/retraction transaction、command、lease 或 funding reservation。

由于固定截止发生时仍有三个 ordinary restore obligation，严格 shutdown gate 为 false；它们
最终以 `runtime_shutdown` 终止，而不是被伪记为 satisfied。该事实必须保留，不能将本轮描述
为完整 P5 correctness gate。

## 7. 结论

本轮成功采集了真实 H200 native-subagent 高压 trace，并确认真实 workload 同时包含：

1. parent parked KV 的未来 reentry 价值；
2. 长 child 执行导致的 lock-heavy HBM pressure；
3. Native HiCache 大量 write-back 与少量 demand-load；
4. BeliefKV 显式 D2H/H2D replacement 闭环；
5. Host pool 饱和和截止时的取消路径。

但它也说明当前 workload/runtime 不能直接作为 clean Oracle throughput 输入：只有 18 个完整
system-valid workflow，且固定 deadline 没有取消在途请求。后续 Frozen GPU Replay 应：

- 使用 18 个完整 trajectory 构造完整 demand；
- 对其余 workflow 只保留 cutoff 前闭合的 LLM、tool、parked/reentry 局部区间；
- 明确右删失，不把 timeout/cutoff 当作 RETURN 或成功 JOIN；
- 在 O0/O3 前修复绝对 deadline 的 descendant/in-flight cancellation；
- 四个 arm 使用相同 frozen demand，不能重新运行自主模型产生不同路径。

本轮不生成 KV timeline；先完成 frozen-demand eligibility 和 censor audit，再决定 O0/O3
的正式输入集合。
