# P6 Future-Growth Top-4 Shadow64 验证

日期：2026-09-10

## 结论

本轮首次在真实 64-root 高压 trace 上得到稳定的 beneficiary-bound 正收益
`PREPARE_HOST` package。候选与价值门槛通过，但在线 fresh/timing 门槛没有通过，因此
没有开放 canary，也没有发送预测式物理迁移。

成功运行产生 65 个完整 risk result、54 个 PREPARE certificate、48 个正收益候选和
43 个 eligible 候选；planner 最终选择 35 次 PREPARE、30 次 observed baseline。
正收益不再依赖当前 beneficiary 已经 HBM-blocked，而来自精确 remaining prefill、p90
decode demand 和 GPU service timeline 推导的 future-growth deficit。

本轮的 54/54 stale 主要暴露了验证口径错误：action-local overlay 被拿去对照只在一次
full plan 中更新的全局 `PolicyInput`，因此出现大量 `context/invocation/join missing` 和
`bundle_missing`。实验后已改为 package-local causal read-set 对 live RCCG 验证，并以
victim context-local physical revision 验证 shadow evidence；真正提交时仍执行 safe-point
物理重物化及完整资源门禁。

即使排除上述假 stale，时序仍未通过。42 个具有 finite latest-start 的结果中，hint
42/42 在截止前发布，但 worker completion 和 validation 各只有 1/42 及时。因此下一轮
是 fresh/timing 回归 gate，不是直接 canary。

## 运行与排除项

- 有效运行：`experiments/shadow/p6_future_growth_top4_shadow64/20260910T122456Z`
- 运行时长：1,976.23 秒；首个完整 risk result 后继续观测 211.40 秒
- 模型：Qwen3-Coder-30B-A3B-Instruct BF16，NVIDIA H200 NVL
- KV pool：850,000 tokens；Host pool：96 GiB
- `max_running_requests=32`，CUDA Graph 最大 batch 32
- 64 个预注册 train root 同时提交，`native_subagent_2to3`
- observed P5 在线；predictor/risk 只读；predictive physical action 关闭
- 停止条件：完成至少 32 个 closure-complete candidate；实际完成 54 个 certificate
- 未生成 KV 时间线，因为 canary/correctness 结果门槛未通过

两个前置运行不计入结果：`20260910T110423Z_resource_conflict` 在 workload 前因 GPU
资源竞争启动失败；`20260910T113952Z` 暴露
`projected beneficiary deficit requires a block time`，修复 timeline block-time 推导后才
执行上述有效运行。

## Opportunity 与候选

运行结束时保持 31 running / 95 waiting。HBM 峰值接近 100%，峰值 migratable KV 为
65.51 GB，engine-locked KV 为 22.82 GB。受控停止后没有 transaction、command、lease、
reservation 或 obligation 遗留。

| 指标 | 数量 |
| --- | ---: |
| risk result | 65 |
| PREPARE certificate | 54 |
| positive benefit | 48 |
| eligible | 43 |
| selected PREPARE | 35 |
| selected observed baseline | 30 |

PREPARE expected benefit P50/P95 为 427.21/439.09 ms，expected recourse credit P50 为
459.13 ms。54 个候选均具有 `release_after_transfer` timing semantics；未选中的主要原因
包括 CVaR、causal slack、净收益和 morphology window 门禁，未通过门禁的候选没有被
强行转正。

safe point 最多检查 4 个 deferred request，但本轮 2,230 次有有效 rank 的 probe 均选择
rank 0。该结果证明 top-4 路径已运行，不能证明 rank 1-3 没有价值；正式结果应继续报告
候选排名与选择分布。

## Freshness 与时序

历史运行记录的 54 个 certificate 全部 stale。主要原因不是 action-local read-set 自身变化，
而是旧 validator 使用仅有一次 full-plan 更新的全局 snapshot：后续创建的 context、
invocation 和 JOIN 对该 snapshot 均表现为 missing，overlay bundle 也不属于其 physical
bundle 集合。

对所有 42 个 finite latest-start result 的时序重算如下：

| 阶段 | 截止前完成 | 相对 latest-start 的迟到 P50/P95 |
| --- | ---: | ---: |
| hint publish | 42/42 | - |
| risk worker complete | 1/42 | 814.50/2,728.68 ms |
| safe-point validation | 1/42 | 1,646.25/3,778.90 ms |

Joint worker 过去会因为任意更新的 semantic delta 而丢弃已经完成的 risk-only publication。
这使首批 hint 到 Predictive worker enqueue 出现约 1 秒额外延迟。实验后已允许无错误且
包含真实候选的 superseded risk-only result 立即发布，同时保留新 delta 的后续重算；
observed plan 仍保持 latest-wins。

## 控制面

| 指标 | P50 | P95 | P99 |
| --- | ---: | ---: | ---: |
| safe-point delta capture | 0.352 ms | 20.467 ms | 60.249 ms |
| action-local overlay capture | 0.002 ms | 66.988 ms | 204.171 ms |
| predictive submit | 0.014 ms | 0.019 ms | 0.031 ms |
| predictive planning | 244.762 ms | 484.221 ms | 870.375 ms |
| belief compose | 173.645 ms | 373.860 ms | 847.551 ms |
| scenario risk | 61.939 ms | 136.020 ms | 209.523 ms |
| action validation | 0.069 ms | 0.112 ms | 0.138 ms |
| trigger-to-validation | 985.684 ms | 2,603.617 ms | 2,915.141 ms |

Predictive worker 76/76/76 submitted/started/completed，0 failed、0 dropped、0 pending。
同步 submit 和 validation 足够轻，但同进程 Python worker 的 GIL 争用仍使在线 compose
远慢于无负载 CPU replay。

实验后的等价优化缓存 action-local closure preview，并以 context revision 失效；PageIndex
自身已经缓存 context summary，因此没有再建立重复的 runtime summary cache。
action-projected k-medoids 改为一次构造距离矩阵并复用，保持 cluster 输出不变。

使用同一运行保存的 12 个 snapshot 做 CPU replay，得到 3 个 positive、1 个
eligible/selected PREPARE；planning P50/P95 为 37.20/49.51 ms，belief compose P50/P95
为 24.17/24.97 ms。该结果仅说明无 GPU/GIL 争用时算法可在约 50 ms 内运行，不替代下一
次在线 GPU timing gate。

## 实验后正确性修复

1. PREPARE certificate 的 causal read-set 只包含 victim、beneficiary 及其递归
   blocking child/JOIN closure，不再绑定整个 BeliefScope。
2. causal certificate 可直接对 live `RuntimeCausalContextGraph` 验证，不构造全图 snapshot。
3. shadow physical freshness 使用 overlay 的 context epoch/revision 和 Host capacity；实际
   command dispatch 仍重新构造 live bundle 并检查 copy/cross-context/exclusive envelope、
   transfer slack 和 ownership。
4. overlay preview 仅在 victim context revision 不变时复用，revision 变化立即局部重建。
5. risk-only result 不再因无关 semantic progress 被延迟到事件静默期。

CPU 回归覆盖 package-local read-set、live RCCG revision、context-local physical revision、
overlay preview reuse 和 superseded risk publication。完整相关回归为 291 passed、2 个
已知 CUDA 环境测试 deselected、8 个 subtest passed。预测式物理动作继续关闭。

## 裁决

- 通过：future-growth beneficiary、正收益 package、action-specific eligibility、worker 活性、
  shutdown 守恒。
- 未通过：GPU 验证后的 fresh certificate、validation-before-latest-start、单笔 PREPARE
  物理闭环。
- 下一次只需短 64-root predictor-only 高压回归；若得到 fresh-positive 且 validation 在
  latest-start 前，才开放一笔 PREPARE canary。
- 若 live validation 已修复但 timing 仍失败，应隔离 Predictive worker 的 GIL/调度延迟，
  不能通过放宽 latest-start 或降低风险门禁掩盖。
