# BeliefKV Wait-Slack 与 Work-Conserving JointPlan 修复

日期：2026-08-14

## 结论

本轮修复针对两个实现断层，不把它们包装成已经成立的性能结论：

1. 工具、JOIN/child 和 message 等待在模型末端被错误合并为统一 external-wait；
2. dynamic working set 在中等 HBM pressure 下收缩计算集合，而 residency 只有在更晚的
   admission deficit/emergency 条件下才回收 KV。

修复后，预测器直接回答动作时间裕量，observed JointPlan 使用同一个 execution-demand deficit 同时
决定 admission 和 residency replacement。GPU 性能 A/B 尚未运行。

## 预测契约

`WaitBelief` 分为 TOOL、JOIN、CHILD、MESSAGE、UNKNOWN。只有 TOOL 拟合条件 competing-risk
survival；JOIN/CHILD 由 RCCG child completion 与 JOIN_ALL/JOIN_ANY 组合，MESSAGE 保留 producer
dependency。`WAIT_JOIN` 的 40,434 个重复 decision row 不再作为独立 wall-clock 样本训练。

动作查询为：

```text
tau = transfer_p95 + commit_guard
p_slack = P(wait remains open beyond tau | current state)
```

tool survival 使用 local-episode/workflow 归一化权重。held-out calibration 对 11 个对数 horizon 生成
二元 survival 标签；right-censored 样本只有在 censor time 晚于 horizon 时才提供已知 survived 标签。
校准只拟合一个 regularized logit map，不新增第二个在线 predictor。JOIN/child 不共享该 map。

新 schema-v3 artifact：

- LOPO：`frontier_belief_h200_bf16_v2_wait_slack_lopo.json`，SHA-256
  `b3791fb89686537080ca47c782af290862a393bb00966b6324203027492e38d2`；
- fit：`frontier_belief_h200_bf16_v2_wait_slack_uncalibrated.json`，SHA-256
  `0e9e48e66402d1dc6d27b205ef28009a4eaa13708c266fac7256eaf3ae69ce46`；
- calibrated：`frontier_belief_h200_bf16_v2_wait_slack_calibrated.json`，SHA-256
  `9493a8a85d2bcf33915150dfcfeebec907ac940bdcd99b7ff4f116204e796f4b`；
- calibration evaluation：`frontier_belief_h200_bf16_v2_wait_slack_calibration_evaluation.json`，
  SHA-256 `7243956de5ac45ca48743a2b84762331d6d4548f3b54ed5422469540f964f764`。

LOPO 仍只使用 64 个 train workflow/7 个 train project，并新增 tool causal-slack Brier。calibration
仍只使用 16 个 Astropy/Sphinx workflow；`test_id` 未访问。校准得到 survival scale=0.9216、
offset=-0.1188，共 30,096 个 slack 标签。10/100/1K/10K ms Brier 为
0.0028/0.2070/0.2242/0.0080。中间 horizon 误差仍高，因此 artifact 保持
`online_eligible=false`、`predictive_action_eligible=false`。

历史 40.86% composite OOD 是“任一预测头 unavailable”的并集，不代表 40.86% workload 未见。
schema-v3 改为 action/state/required-head availability；本次 calibration 中所需 head availability 为
100%，但这不等价于预测足够准确。

## 调度契约

动态 working set 不再因 HBM pressure 缩小 GPU-ready target。它先按 resident-ready bytes、startup
bytes/ready、action unlock、ready count 排序，30 秒 starvation aging 与 workflow fair rank 只作为
下界和 tie-break。实际 ticket 仍需通过 prefix rematch、token budget 和 allocator 容量校验。

统一 replacement 流程：

```text
select throughput-oriented execution set
  -> compute startup + missing restore - available HBM
  -> choose parked/lease-expired resident victims
  -> safe-point live Radix rematerialization
  -> COMMIT_CPU(victim, beneficiary, reclaim certificate)
  -> ACK
  -> persistent beneficiary admission priority until first real GPU service
  -> native capacity validation and service
```

`engine_waiting` 不再被当作 startup_bytes=0。每个 victim 只声明本次实际承担的 reclaim contribution；
不足时下一 epoch 继续补齐，不能用逻辑 context size冒充真实可释放量。safe point 会拒绝已消失的
beneficiary 或 exclusive reclaim bytes 不足的 bundle。ActionGroup 将 beneficiary 与 residency slice
设为 ALL_OR_NOTHING，并携带 live planned reclaim/host/HBM/PCIe certificate。

`KEEP_GPU => finite service`：未选中的 resident-ready context 若在
`resident_service_window_ms` 内没有真实 GPU service，则 residency lease 到期并成为 replacement victim；
engine-running、restore-mandatory、当前 selected 和 beneficiary context 继续受保护。

admission ticket 不再清除 beneficiary priority；只有首个真实 GPU service quantum、请求终止/取消或
request/context epoch 失效可以清除。默认 service window 由 1 秒调整为 5 秒，并记录同一 context 在
该窗口内发生的反向 residency 动作，用于识别 D2H/H2D 抖动。

JOIN/CHILD/MESSAGE slack 由已组合 RCCG scenario 的 dependency-release offset 直接统计，不再复用包含
HBM/PCIe 约束的 `future_feasibility_probability`。OTHER 或无法解析的 dependency release 贡献零 slack，
因此不能产生预测性物理动作。

## 饱和负载

底层 runner 和正式 P6 collection launcher 均支持 `--saturated-root-backlog`。该模式在等待任何
workflow 完成前提交全部冻结 root，要求 `concurrency >= max_workflows`。例如 H200 v4 的 64-root
workload 使用 64 个 client worker，而 server 仍由 32-request 上限与 JointPlan 控制 active set。这样
不存在尚未提交的 client iterator backlog；多出的 request 已真实进入 server waiting/admission 侧。

下一次性能 gate 应固定 H200 BF16 v4 profile（32 max-running、850K KV tokens），比较 predictor-off
P5 observed policy 的旧收缩策略与 work-conserving replacement，报告 GPU-ready/running、prefill
batch、GPU utilization、HBM stranded bytes、D2H/H2D、admission wait 与 successful workflows/hour。
随后才允许在同一 trace 上开启 schema-v3 predictor shadow。

## 验证

- predictor/risk/control focused：108 passed；
- Deep Agents workload/backlog：84 passed；
- 扩展 adapter/control suite：235 passed，1 skipped，6 subtests passed；
- 另有 2 个 SGLang 原生导入测试因当前 shell 无可识别 `CUDA_HOME/nvcc` 失败，与本轮逻辑无关；
- semantic replacement 正/负路径：beneficiary 可见时完整 dispatch/ACK，消失时整组拒绝；
- `git diff --check` 在最终整理阶段执行。
