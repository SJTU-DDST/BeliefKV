# 条件剩余工作头的 CPU 候选比较

阶段头、MiniLM encoder 和阈值保持原部署产物不变，只比较
条件剩余 token 工作。使用同一历史 rolling-250ms 因果快照，
六项目拟合、Astropy selector 选择、Sphinx 只报告隔离项目结果。
有 FINALIZE/格式修复干预的 workflow 从自然 RETURN 拟合剔除。
不是新密封验证，也不能把 token 误差转换成已达到的墙钟误差。

神经网络在冻结语义设计矩阵上增加已送达尾部的标点、代码围栏、
括号、结尾短语与通知进度特征，比较 total/remaining 两种目标
和三档正则强度。32/64 两层 MLP 的模型形式由 selector 选择。
结构平衡只是有界尾部特征，不宣称识别完整语法。

树模型使用同一数据、冻结设计矩阵和结构特征，调用现有环境
LightGBM 的原生 API，不安装或升级依赖；比较两种工作目标与
7/15/31 leaves，使用 L1 目标与 workflow 权重。

| 同一 Sphinx 35 个终态请求的末段指标 | 部署工作头 | MLP 候选 | 树候选 |
| --- | ---: | ---: | ---: |
| 中位绝对 token 误差 | 110.00 | 72.78 | 79.20 |
| 中位有符号 token 误差 | +104.65 | -32.59 | +59.83 |
| P90 绝对 token 误差 | 202.87 | 210.26 | 154.05 |

实际剩余不超过 64 token 的 17 个请求，中位绝对误差分别为
129.09 / 63.00 / 109.60。模型不能凭总体 MAE 就被视为合适的
临近结束触发器：MLP 中位数改善但尾部未改善，树模型 P90
较好但仍系统性高估，最末 32-token 区间仅两个独立请求。

候选均只在 CPU 运行，GPU 64-root 对照的部署权重未改变。
产物分别在
`experiments/models/child_work_neural_candidate_20261004_v1/`、
`experiments/models/child_work_tree_candidate_20261004_v1/`。
缓存放在 `experiments/processed/semantic_work_neural_cache_20261004/`，
不是运行代码或新的训练集资格声明。

后续应以 EOF 前因果快照、自然 RETURN 独立请求以及真实
controller submit 的提前量评价，优先解决末段偏置和短区间
概率，而非只提高整段长度拟合。需在完整 64-root pair 结束后
另行验证候选，不能中途挑换部署权重或按 Sphinx 结果再选择模型。

## v2 回放否决上线

在完整 v2 predictive trace 上重新采集因果 rolling 快照，
剔除七个有 graph FINALIZE 的 workflow 后得到 1149 个快照、
69 个自然终态请求。未用 v2 标签重新拟合，比较原部署头和
同一 MLP 候选：

| v2 回放末段指标 | 原部署头 | MLP 候选 |
| --- | ---: | ---: |
| 中位绝对 token 误差 | 49.00 | 87.72 |
| 中位有符号 token 误差 | +31.38 | +87.72 |
| P90 绝对 token 误差 | 222.90 | 368.23 |

实际剩余不超过 32 token 的 33 个请求，误差中位数由 31.00
恶化为 71.69。因此候选拒绝默认上线，不能用旧 Sphinx 的
中位误差改善宣称新负载可用。回放也不是新的独立密封验证。
结果保存为 v2 根目录的 `neural_work_replay.json`。

生产加载接口已支持指纹/encoder 校验的 work-only JSON，
phase 分数保持逐位相同；导出与原 PyTorch 候选在验证输入上的
点差异小于 0.001 token。该接口完成不改变候选被否决的结论，
实验默认仍使用原冻结部署头，不手动提高 eligibility。

## 短 Token 区间完成概率

新增 `scripts/fit_child_completion_windows.py`，固定 phase/encoder，
比较条件概率 P(剩余输出 token <= h | 自然最终报告)，h 为
8、16、32、64。它不是 RETURN 墙钟时刻，也不是预取净收益。
五类剩余 token 区间共用一个多分类树模型，累计概率天然有序；
六项目拟合，Astropy 的独立 workflow 子集校准温度和选择模型，
另一子集选择首次触发阈值，Sphinx 及 v2 只作开发比较。工具轮次
误触发与过早触发分别统计，不把同一 request 的多快照当独立命中。

250 ms 回放训练的 4563 个有剩余工作标签的快照中，四个短区间
各有 4 / 18 / 100 / 237 条，其余 4204 条超过 64 token。
100 ms 回放对应为 7 / 23 / 125 / 357，超过 64 token 为 5824。
这些是相关快照数量，不是独立 RETURN 事件。两者均未在
Astropy selector 找到首次触发 precision >= 0.8、至少五个
独立 request 的操作点；固定 0.8 概率也没有提供有用触发。
因此两个候选均不部署，不因“无误报但也无动作”声称模型更好。

产物为 `experiments/models/child_completion_windows_candidate_20261004_v1/`
和 `child_completion_windows_candidate_20261004_v2_100ms/`。
它们是开发试验，不是新的密封验证或替代默认工作头的依据。
仅增加旧快照回放密度不能补回原来未观察到的末段文本，也不能
抹掉旧数据中的保守时钟余量。

后续采集补上真正的 100 ms 正文快照，不再只等标点/128 字符
milestone；client/server 同时记录 Linux boot/time-namespace/
CLOCK_MONOTONIC 的哈希域及已完成服务的单调时间。仅域匹配才用
文本送达前的精确 decode 进度，不加入未来进度；不匹配或旧数据
继续使用带 100 ms 余量的 clock bracket。新观测与旧样本的输入
口径不同，须分别记录和重新验证，而非宣称修改采样已提高精度。
