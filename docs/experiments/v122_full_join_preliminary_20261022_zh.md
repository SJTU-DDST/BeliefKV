# V122 Full 与 JOIN-only 的最新完成对照

日期：2026-10-22，北京时间。Full 与 JOIN-only 已完成、后处理结束；
工程对齐 native 已启动，尚未完成。本记录不替代三组最终比较。

两组均 36/36 completed、系统测量及无 guard agent JCT 资格全部通过，
没有 guard、child cancel、JOIN timeout。任务正确性仍未独立评分。
两组使用同一冻结应用和引擎、任务、prompt、容量、batch8 和 seed21，
实际模型轨迹、请求与生成 token 不同，不能将调度效果与工作量变化混同。

独立比较报告：
`experiments/reports/v2738_v122_full_join_comparison_20261022.json`。

## 原始完成时间与实际工作量

| 指标 | Full | JOIN-only |
|---|---:|---:|
| 原始平均 workflow JCT | 1899.64s | 1903.38s |
| 收集时长 | 3410.30s | 3402.08s |
| 输出 token | 1527709 | 1760934 |
| LLM 请求 | 6119 | 6768 |
| 工具调用 | 7915 | 8560 |
| 动态 child | 122 | 130 |
| 委派/JOIN 轮次 | 68 | 78 |

Full 平均 JCT 只短 3.74s，约 0.20%；输出 token 则少约 13.24%，请求少
约 9.59%。不能宣称已取得可归因的 JCT 提升，也不能仅按 token 比例线性
修正 JCT，因为上下文、prefill 和依赖路径同样不同。

## 统一去长尾口径

按预先规定的“四个活跃 workflow”边界，取两组较早边界 2701.399s；
两组均使用该截止时间，忙时窗口为 600--2701.399s，不依据谁更快选窗。
未在截止前完成的 workflow 保留为删失到截止时间，不只挑已完成任务。

| 指标 | Full | JOIN-only |
|---|---:|---:|
| 截止前完成数 | 33 | 30 |
| 平均受限完成时间 | 1870.23s | 1846.07s |
| 忙时 decode token/s | 498.18 | 551.20 |
| 忙时已完成请求输出 token/s | 504.31 | 555.80 |
| 平均 running 请求 | 7.08 | 7.38 |
| decode 时间加权 batch | 7.36 | 7.67 |
| 平均 decode worker 区间 | 13.18ms | 12.84ms |
| 平均 prefill worker 区间 | 41.00ms | 35.83ms |

Full 忙时 decode 吞吐低约 9.62%，受限完成时间高约 1.31%。Full 的
prefill worker 平均区间也更高，但这些区间尚未按上下文、输入 token
和 batch 分桶，不能把全部差额归为自定义控制开销。

截止前完成数更多与平均受限完成时间更差可以同时发生：后者包括所有
到达 workflow 在窗口内的完成等待，不只是截止时的完成个数。

## 对下一项优化的含义

普通执行前沿恢复的独立关闭候选已在 main 实现，提交 `53aa53a`，
V2735/V2737 对应 3875 项 CPU 通过及源码绑定。新 Full/no-frontier
保持 JOIN/EOS、备份、卸载和相同排序，暂不叠加最后 child 优先。

实际 runtime 记录确认 `parallel_wait_prefetch=true`、
`wait_prefetch_contexts=2`。不能将非并行分支的“任一未结束 H2D 阻塞新
预取”直接套用到本轮；当前容量上下文数量达到 2、同 context 已在传输
或物理命令额度不足时仍会返回 busy。普通前沿恢复可能消耗这些额度，
但尚未量化它与 EOS 的实际冲突次数和净延迟，不能仅凭代码路径宣称
这是本轮吞吐差距的根因。删除候选也保留并行模式及 2 个 context 的额度。

当前证据说明 Full 尚未证明优于最简单强基线，继续优化的验收目标不变。
等待本轮 native 正常采集后，优先运行独立删除前沿恢复的 Full，复用这轮
合格对照，记录应用版本差异；不为重复 native 消耗额外 GPU 时间。

## 最新短 Prefill 的分桶与阶段证据

V2739 使用两组同一预定忙时窗口，按实际 native 准备的输入和前缀几何
匹配 worker 区间。共同 prefill 分桶需两侧各至少 32 个 batch，并匹配
batch 大小、512-token 输入工作分桶及 2048-token 平均前缀分桶。
13 个共同分桶全部为 batch1、实际新增输入小于 512 token；Full 的均值
为 JOIN-only 的 1.31--1.84 倍。这说明原始工作量差异不是足够的解释；
但分桶仍不等于相同请求、完全相同输入或纯 CUDA kernel 计时。

V2740 复用已测试的 prefill turnover 分析器，读取 V2739 封存的精确 audit
前缀，按时钟域、forward_iter、顺序请求身份和 native 几何配对。
Full/JOIN-only 分别有 134/163 个短单请求 prefill 采样；各有一个缺失服务
配对的观测保留为未知。以下为采样阶段墙钟 P50：

| 阶段 | Full | JOIN-only |
|---|---:|---:|
| selection | 10.151ms | 9.091ms |
| run_batch | 39.842ms | 36.921ms |
| post-launch physical | 1.623ms | 0.007ms |
| result processing | 7.137ms | 7.307ms |
| run 返回至 result 开始 | 7.297ms | 2.999ms |

最后一项包含其他重叠阶段，不是可独立累加的额外 CPU 时间。run_batch
包含 CPU 提交和遥测；GPU stream span 也包含提交间隙和依赖，不能当作
纯 kernel。上述采样汇总尚未进一步匹配前缀分布，不能将所有差额直接
归因为自定义控制。仅 3/3 个短样本能配对到恢复等待，不能据此概括
所有短 prefill 的 H2D 等待，更不能将采样之和或差额从 JCT 中直接扣除。

post-launch physical 的实测成本支持优先消融普通前沿恢复；其他阶段
仍待按更细几何和下一相邻 batch 拆分，不跳过剩余瓶颈。
