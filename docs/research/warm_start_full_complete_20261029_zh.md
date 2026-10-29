# 平均 60 秒到达的完整配对结果

2026-10-29，Asia/Shanghai。FULL 在 21:17:44 完成收集，原始退出码
为零；全部请求、原生停止、两份新鲜 idle 快照和实际所属进程退出
已独立核验。后继平均 120 秒到达的 ThunderAgent 已开始运行，
不改动它和已排队 FULL 的冻结策略。

## 完整性能

两侧均为 18 个不同项目，10 个初始 workflow 和 8 个平均间隔 60 秒的
泊松到达，seed21；请求 3674、输入 145565966、输出 549671 token，
依赖、工具计时、容量、采样及正常尾部一致。控制使用 common11b84
原生树，FULL 使用已验证的 free-extent434c 树，属于组合方法比较，
不能作为同一 native tree 上 handoff 的独立消融。

| 指标 | ThunderAgent decay | FULL |
| --- | ---: | ---: |
| 平均完整 workflow JCT | 1599.890513 秒 | 1538.164561 秒 |
| 首到达到全部完成 | 2323.047855 秒 | 2281.264306 秒 |
| 全程输出吞吐 | 236.616305 token/s | 240.950160 token/s |
| workflow 首输出等待均值 | 75.363380 秒 | 73.891349 秒 |
| 每个 workflow 最大 ready 输出间隔的均值 | 67.508718 秒 | 71.137810 秒 |
| decode worker 时间加权 batch | 8.100356 | 8.340071 |
| decode worker 累计区间 | 1604.526978 秒 | 1587.484703 秒 |
| prefill worker 累计区间 | 430.129462 秒 | 372.316476 秒 |

JCT 降低 **3.8581%**、全程吞吐提高 **1.8316%**，仍未达到明确、
可重复地优于所有适用 baseline 的目标。不据此启动其他 baseline。
首服务略好，但最大 ready 间隔均值变差，不能称作全面公平性改善。
单次配对也不能证明可重复性。
worker 区间包含等待及运行时处理，不等于独占 CUDA kernel 时间。
特别是 staged demand 改变了恢复等待所在阶段，不能将 prefill 区间的
下降全部视为计算加速或已隐藏的 DMA。

完整曲线保留从计划到达到正常结束的全部区间和不完整尾部 bin。
主要服务阶段两条吞吐曲线多次交叉；后期 batch 降到约一个请求，
两侧均有排空尾部，不能删掉它再声称 JCT 改善。

图与完整数据：
`experiments/reports/layer_free_extent_20261029/warm_start_complete_r2/`
中的 `decode_timeline.png`、`decode_distributions.png`、
`time_bins.csv`、`decode_evidence.json.gz` 和 `review.json`。

第一次离线出图在完整工作验证后因主服务 Python 缺少 matplotlib
失败；R1 的原始 decode 提取与失败标记保留。R2 改用已有
`ops/cpu_plotting_20261027` 环境，没有改变服务环境、GPU 实验或结果。
正在运行的旧 CPU 排队器也使用服务 Python 执行末尾出图，因此另设
独立 CPU 结果处理进程，等待两侧 GPU 真实完成后用正确环境生成报告；
不热改冻结排队器或 GPU 进程。

## 迁移与实际工作

| 实测量 | ThunderAgent decay | FULL |
| --- | ---: | ---: |
| 原生实际 prefill 输入 | 3789634 token | 3770429 token |
| 实际 H2D | 2826.104439 GB | 2790.839157 GB |
| 实际 D2H | 2199.933616 GB | 1302.827729 GB |
| 已驱逐 FULL 精确重访的重算归因量 | 35749 token | 7775 token |

实际 prefill 减少 0.5068%，H2D 减少 1.2478%，D2H 减少
40.7788%。D2H 下降明显，但恢复方向的字节量接近；不能把减少的
复制流时间直接扣除为 JCT 收益。会话前缀丢失代理两侧均为零，
与已驱逐 FULL 节点重访的归因量属于不同统计边界，不能互相替换。

FULL 的 497 次 staged-demand episode 涉及 798 个成员关联，合计
2499306848256 字节；全部关联都有后续原生服务和完成回执。
成员关联不是独立请求数。普通 Host hit、staged demand 和逐层交接
合并后，未标记 H2D 恰好守恒，没有未解释字节。完整审计在
`warm_start_native_demand_r1.json`、`warm_start_thunder_native_demand_r1.json`
和 `warm_start_mechanism_review_r1.json`。

两侧恢复等待探针仅有 19 / 2 个样本，且 staged demand 改变了等待
所在位置；不能据此外推完整 GPU 等待降低比例，也不能把移出 GPU
worker 的等待视为已经隐藏。

Sibling 选批也完成实际服务审计：1042 条日志全部对应真实 prefill、
显式阻塞 JOIN 和已完成的同会话请求。其中只有 26 个批次包含至少
两个成员，覆盖 15 个不同 JOIN；不能把全部日志当作共同选批次数。
这证明偏好已落实到部分实际批次，尚不是其独立 JCT 收益。
证据为 `warm_start_sibling_service_r1.json`。

## 逐层交接覆盖

最后记录的 runtime 计数是 70 次尝试、1 次实际提交；后者完整恢复
73 token，约 7.176 MB，donor 备份 220 token。新增 free extent
为零，没有在正式负载中覆盖新组合路径。实际 ACK 与消费者服务已核验，
但这次很小的交接无法解释整体性能差异。

53 次前置拒绝记为 donor 不足以覆盖所需恢复；未记录这些前置检查
的完整逐次容量，不进一步推断原因。3 次有完整快照的 native 拒绝中，
当时 free token 分别为 36/32/33，输入及增长预留为 73/1312/171，
均缺少独立可用的输入/增长空间；没有证据支持删除这些保护来强行触发。
这仍不排除未来设计进一步扩大适用范围，但不能把组件场景中的成功
当作真实 workload 已获得逐层 overlap 收益。
