# 24 roots 与 batch24 的容量核算

日期：2026-10-23，北京时间。以下为 CPU 历史轨迹分析，未运行新 GPU 实验。

## 起始建议

下一组候选采用 24 roots 同时到达，running request 上限 24，
decode batch 上限及所需 CUDA graph 覆盖相应对齐。它是待验证起点，
不能称为收益最大或最优配置。实际 decode batch 由请求就绪程度、
原生准入与 KV 容量决定，不为了凑到 24 而额外等待。

root 数量、同时执行的 LLM 请求数、graph 可覆盖的 batch 大小分别
核验，不能只增加 graph 大小而仍将 running 限制为 8。
本次只给出参数建议，未改 V134d 已冻结的 36-root/running8 源采集。
正式性能对照必须在相同任务/轨迹、容量、seed 与参数下重新配对。

## 最新完整轨迹的机会数量

使用 V133b 的完整同工作量重放。按既有
`configs/workloads/locobench_agent_long_36root_20261020.json`
的 seed21、项目轮转顺序取前 N 个任务，不按 JCT、是否加速或 JOIN
多少筛选。每个子集均覆盖九个原项目。

| roots | LLM 请求 | spawn | JOIN satisfied | 工具调用 | 实际工具耗时至少 1s |
| --- | ---: | ---: | ---: | ---: | ---: |
| 12 | 2,682 | 54 | 31 | 3,529 | 131 |
| 16 | 3,438 | 75 | 44 | 4,449 | 182 |
| 20 | 4,115 | 85 | 49 | 5,285 | 232 |
| 24 | 4,861 | 95 | 55 | 6,191 | 260 |
| 28 | 5,593 | 109 | 63 | 7,153 | 284 |
| 36 | 6,907 | 130 | 75 | 8,987 | 388 |

这是这些任务在已完成 36-root 运行中的事件归属统计，不是少并发
重跑后的时延、迁移量或动态模型决策预测。24-root 固定子集保留
约 73.3% 的 JOIN 和 68.9% 的工具调用；工具次数和实际长等待也
不等同于存在 Host-only 状态、可安全卸载空间或有效预取机会。

V134c 的可复用 subagent 新源被预约中断，仅有 5 个自然完成 workflow、
10 次 spawn、5 次 JOIN satisfied，无真实 resume。详细工具委派说明
修复后仍需 V134d 的完整源验证，不能把旧轨迹数量当成新 prompt 保证。

## 初始前缀的真实共享

不能用 root 数乘以长上下文大小证明真实物理压力。对 V132 源采集的
36 个 root 首请求，从 `request_tokens.jsonl` 解码并校验 token 哈希，
按 extra_key/cache_salt 分 namespace、排序后累计相邻精确公共前缀，
得到输入 radix 前缀并集。每个 FULL token 按实际 census 的 20,480 bytes
换算，实测 FULL 池为 1,797,968 tokens / 36.82240512GB。

| roots | 不共享时的初始 FULL，GB | 初始输入前缀并集 tokens | 初始并集 FULL，GB |
| --- | ---: | ---: | ---: |
| 12 | 33.821 | 1,225,064 | 25.089 |
| 16 | 44.681 | 1,227,598 | 25.141 |
| 20 | 57.504 | 1,230,445 | 25.200 |
| 24 | 67.913 | 1,233,320 | 25.258 |
| 36 | 104.181 | 1,241,482 | 25.426 |

这些是首请求完整输入前缀的精确并集代理，不是某一时刻的 HBM
分配或 Host 压力，未包含后续私有增长、child 历史、decode reserve、
不同 checkpoint/锁、休眠状态或全部历史分支。24-root 初始并集
比 36-root 仅少约 0.167GB。保留相同九个长项目，可以在减少队列
竞争时保留相近的公共长前缀工作集；是否仍有迁移压力由后续状态决定。

24-root 子集的请求输入 P50 为 46,140 tokens，均值约 70,846；
其中约 25.9% 请求达到 100K。完整 36-root 请求输入 P50 为 46,450，
均值约 74,327；170 个 agent context 的最大输入 P50 约 48,842。
不能把所有 child 当成 100K agent。若 24 个完全独立的执行前缀同时
驻留，FULL 池平均仅能容纳每个约 74,915 tokens，且尚未扣除增长预留。
实际 batch 内的共享可能降低需求，需实测可执行 batch 和锁定量。

## 验证与调整

24 roots 优先于直接减到 16，原因是保留更多 child 私有状态、JOIN
和工具等待机会；不能据此预先认定系统会加速。batch24 若提高计算
效率，迁移可能更显著；更多在跑请求也会锁住更多状态，可能减少
parent 恢复可用的空间。两种效应均需观测。

重点记录实际 batch 分布、忙时吞吐、完整 workflow JCT、原生回撤、
有用状态驱逐/恢复、parent 容量受限比例和暴露的恢复等待。
若 batch24 引起持续回撤或难以为 parent 恢复腾出空间，先保持
24 roots、单独将 running/decode 降至 16。若有用迁移几乎消失，
先检查共享后的活跃/休眠独占工作集，再判断是否回到 28 roots。
低 JCT、更多 H2D 字节或更多 JOIN 单独均不足以证明 KV 管理收益。

原始证据位于：

- `experiments/raw/qwen35_locobench_long_36root_running8_reviewed_position_20261023_v133b/native_matched/replay/runtime_events.replay.jsonl`
- 同组 `server/runtime_events.sglang.jsonl` 与 `server/native_capacity_census.json`
- V132 `native_capture/server/request_tokens.jsonl`，原位置由
  `experiments/reports/v3143_v133b_causal_jct_paths_20261023.json`
  的 `source_evidence.source_arm` 标明
- `experiments/reports/v3144_v134c_reservation_cutoff_partial_20261023.json`

北京时间 2026-10-24 06:00 前继续仅做 CPU 工作。GPU 性能目标仍未验收。
