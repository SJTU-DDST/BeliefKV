# V128 Full/native 忙时前缀诊断

日期：2026-10-23，北京时间。本报告使用 V128 同一冻结应用/引擎的 Full
与正在收集的 native_matched，在各自 GPU monitor 起点后的 600–1800 秒
完整窗口做诊断。没有截短整轮 JCT，也没有改变正在运行的实验。

V2927 保留 audit 的完整行前缀字节数、SHA-256、monitor 覆盖、注册哈希
和分析器哈希。GPU CSV 的朴素时间实际属于 America/Los_Angeles，
以首个 GPU 样本和原生 epoch 时间核查后使用该时区，不能误按北京时间。

## Decode

| 窗口内观测 | Full | Native |
| --- | ---: | ---: |
| Decode batch | 81737 | 79770 |
| 每请求可见上下文均值 | 111402.80 | 119430.08 |
| 未匹配 worker 区间均值 | 13.3981ms | 13.7485ms |

原始上下文工作量不同，不能将上述未匹配均值当作速度提升。
按 batch size、batch 平均上下文的 2048-token 桶匹配，双方每组至少
32 个 batch，共 82 组；以每组双方较小 batch 数加权，权重为 64383：

- Full 加权区间 13.366753ms，native 13.436980ms，比值 0.994774。
- 再加入真实 CUDA graph 标记匹配，本窗口得到相同的 82 组和结果。

这支持本窗口匹配 decode 区间基本持平，不证明完全等工作量、无控制
成本、GPU kernel 更快或平均 JCT 已达到目标。均值上下文分桶也没有
匹配每个请求的精确输入、混合状态与完整恢复依赖。

## 短 Prefill

一请求、实际 extend 少于 512 token 的已采样 prefill，Full 为 86 个，
native 为 75 个；阶段记录均可配对。原始工作量没有逐样本相等。

按 prefix 2048-token 桶、graph 和 overlap loop 匹配，双方每组至少
3 个样本：

| Extend 桶宽 | 共同分组 | Full/native 样本 |
| --- | ---: | ---: |
| 512 | 2 | 9 / 8 |
| 64 | 0 | 0 / 0 |
| 16 | 0 | 0 / 0 |

512 宽桶中加权 run span 为 Full 34.347ms、native 37.447ms；
selection thread CPU 为 6.672ms、11.393ms。但其中一组 Full 的实际
extend 均值只有 146.8，而 native 为 289.25，所以这些比值不能作为
短 prefill 加速比，也不能证明历史 1.31–1.84 倍的差距已完全修复。
该报告没有按前后 worker 邻居再匹配；更严格比较需要额外覆盖。

本窗口全部短样本的 `post_launch_physical` thread CPU 均值分别约
0.495ms、0.00187ms，Full 确实存在额外工作。这是未匹配样本的阶段
计时，不是独占系统开销、GPU 空闲或可直接从 JCT 扣除的时间。
run_batch 中也包含 launch telemetry，阶段不能相加。

只有 3 个 Full、4 个 native 短样本有明确层恢复等待记录；其他缺失
不能按零等待处理，不据少数样本估计全部 H2D 的可隐藏时间。

## 后续判据

当前数据不支持沿用旧版“短 prefill 仍明显慢很多”的数值，也不足以
宣布它已经解决。保留窄分桶缺失、实际工作量范围及全部正常长尾，
完成 native 后再核对完整质量与工作量；严格隔离机制仍需精确输入的
同轨迹依赖回放。没有以本忙时窗口替代整轮 JCT 或端到端验收。
