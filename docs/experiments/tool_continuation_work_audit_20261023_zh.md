# 短工具后继工作量审计

日期：2026-10-23，北京时间。未启用新的调度策略或 GPU 实验。

## 审计边界

新增 `scripts/audit_tool_continuation_work.py`，复用已有工具整轮重建。
读取完整 workflow 事件、原生请求 receipt 和首次服务的缓存计数，
逐请求核验身份、prompt 哈希、真实 output token IDs 的长度与哈希、
停止原因和同一 native clock。多组还核对所有请求的 token、哈希、
停止与 sampling 参数相同。每个输入读取均封存完整 JSONL 前缀摘要。

时间界限为工具整轮耗时至多 1 秒，且原生完成到下一请求 HTTP 提交
至多 1 秒；同时需要 session/generation 相同、context epoch 连续、
没有压缩。1 秒是用于分析的旧候选边界，不是新校准的调度参数。
不将客户端工具时间戳减去 native 时钟，也不将输出 token 称为 GPU
服务时间。后继输出只作为事后标签，不能用于运行时优先级。

native receipt 实际保存 `output_token_ids`，没有独立的 output_tokens/
output_sha256 字段。初版审计因错误假设拒绝真实输入，已经改为从真实
token IDs 计算长度与哈希，并用相同 schema 的 fixture 验证。
未修改原始 trace，也没有为通过审计补写原生字段。

## V133b 同工作量结果

native、Full 工具预取关闭、JOIN-only 每组为相同 36 workflows、
6907 请求、输入/输出 token、停止和 sampling 参数。
6580 个工具请求轮次中，6565 个有可归属后继；另 8 个工具成员关系
未证实，7 个混合了工具和委派。未知情况不补成零开销。

三组都得到相同的 6161 个快速同 session 后继，实际工作量为：

| 后继指标 | 数值 |
| --- | ---: |
| 输出 token P50 | 90 |
| 输出 token P90 | 458 |
| 输出 token P99 | 约 3497 |
| 最大输出 token | 16384 |
| 输出至少 1024 tokens | 278 |
| 输出至少 2048 tokens | 123 |
| 输出至少 4096 tokens | 50 |
| 输入 token P50 | 44484 |
| 输入 token P90 | 174039 |

仅使用先前已完成输出的分桶仍留下显著长后继：

| 上轮输出 | 样本 | 下轮输出 P50 / P90 | 下轮至少 2048 tokens |
| --- | ---: | ---: | ---: |
| 少于 128 tokens | 3869 | 78 / 307.2 | 70 |
| 128--511 tokens | 1796 | 154 / 457.5 | 38 |
| 至少 512 tokens | 496 | 131.5 / 1110 | 15 |

这些分桶是描述性结果，不是训练/验证隔离后的概率预测。
工具短暂中断可形成连续性机会，但不能保证下一轮便宜，也不能保证
同一 workflow 即将结束。旧的四请求额度没有限制这部分生成长尾。

首次服务记录的逻辑缓存命中计数为：

| 6161 个后继 | native | Full，工具预取关闭 | JOIN-only |
| --- | ---: | ---: | ---: |
| 有 FULL Host hit | 3908 | 3557 | 3910 |
| 有 Mamba Host hit | 3908 | 3517 | 3910 |

计数是在首次服务处读取的原生匹配计数，不证明工具结束时或准入前
的物理驻留，不等于真实 H2D DMA，也不证明 Full 减少了相应次数。
这些数据不能直接用于一个只给已驻留 agent 续接的反事实模拟。

## 下一步限制

继续保持旧短工具年龄继承默认关闭。兼容当前 workflow/resident
排序的新候选需要复用 live 驻留信息、保留 JOIN/老化边界、对已经
获得的服务设置有界续接额度。不能将同工具短等待直接升级成最高
优先级，也不能从这些工作标签推算节省的 JCT。
当前未增加运行时扫描、预测器、GPU 槽位或 KV 保护。

最终事实报告为
`experiments/reports/v3152_v133b_tool_continuation_work_final_20261023.json`。
V3151 保留为较早的成功报告；V3152 另记录单组 receipt 核验与多组
工作量核验的区别。相关新旧审计测试 94 项通过。
V3153 完整保留 V3149 的 5248 项，5284 项及 2 个 subtest 通过，
没有失败或 skip；新增 36 项覆盖真实 receipt schema、原生输出 token
哈希、身份/clock/session/generation、压缩、缺失观测、相同工作量与
单组/多组证据区分。既有 Docker 用例不在继承集合中，未扩大声明。
V3154 由实际 `qualify_sources` 生成并通过 `qualify_report` 核验。
GPU 源采集队列与预约不变，性能目标继续 active。
