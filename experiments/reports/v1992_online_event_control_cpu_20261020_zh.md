# 在线工具事件的无消费者特征与生命周期清理

日期：2026-10-20（北京时间）。基线 `52ccccb`。本轮只进行 CPU 工作，
新 GPU 实验最早仍为北京时间 06:00。

## 根因与实现

V88 候选启用在线工具计时。`_submit_tool_wait()` 在在线模式直接调用
`_submit_online_tool_wait()`，消费 `OnlineWorkloadToolTiming` 的工具历史，
不使用旧模型 worker 的 `LocalFrontierFeatures`。

旧 `on_events()` 只要启用在线工具计时，就仍调用 `observed_boundary_action`，
维护边界 deque、命令七元组以及按 tool-run 分组的特征元数据。这些只被
旧 worker 的特征构建和提交路径读取。

本轮修改：

- 只在 `_model_worker` 存在时维护这套特征，在线模型继续观察原事件。
- worker 路径的边界 deque 按需创建，已有队列不再分配未使用的默认 deque。
- 最后一个工具结束时删除空的 run 分组；workflow 结束时清除其残留分组。
- 因果镜像丢弃时同时清空 run 分组，避免残留旧调用元数据。

没有删除活跃 sibling 的特征，没有改变工具预测分布、JOIN 阶段、自然
RETURN 或工具动作。原生身份、容量、锁、ACK、恢复和首次消费路径未改。

## 历史事件对照

V1990 读取最新完整 V85d predictive 的 12 条
`runtime_events.deepagents.jsonl`，逐条绑定完整文件 SHA256。
共 9428 个事件，每条 workflow 在其原文件顺序下独立回放。

每个事件之后检查因果图、在线工具的 open/index/history/revision、
语义版本、实际 runtime 计数、visible/context 以及空物理状态一致。
工具开始/结束时，在计时外按该事件时间直接查询在线估计，核对完整
P10/P50/P90、CDF、scope 和样本支持；两侧均有 1351 次有支持查询，值相同。
这不是原在线运行的 2447 次周期预测，也不是预测精度或预取成功次数。

在线模式下，旧边界分类调用 9336→0；候选不再保留 worker 专用特征。
worker 对照继续核对每个事件之后的边界、命令特征和活跃 run 分组。
明确排除已结束 workflow 和空 run 分组，因为本轮正是在清除这些无消费者
记录；其余在线历史和实际状态仍逐项比较。

## CPU 计时

共 48 组：12 workflow × 在线/worker 模式 × profiling 关闭/开启。
每组 21 个样本，另有 5 个预热样本，新旧次序交替，完整创建全新 runtime。
状态序列化、定点预测检查、计数包装、构造和关闭均在计时外。
最终计时在 2104 项 CPU 检查完成之后独立执行。

每个样本累加该 workflow 的全部事件 handler CPU 时间，再除以事件数，
报告这组摊销值的 P50；不是单个事件延迟分布的 P50。

关闭 profiling 的部分结果，单位为摊销线程 CPU us：

| workflow | 事件数 | 基线 | 候选 | 降幅 |
| --- | ---: | ---: | ---: | ---: |
| api_gateway / feature_implementation | 914 | 23.327 | 20.688 | 11.31% |
| game_simulation / cross_file_refactoring | 1835 | 23.508 | 20.871 | 11.22% |
| system_networking / integration_testing | 895 | 22.752 | 20.247 | 11.01% |

在线模式 24 组全部降低 8.8%–12.0%。关闭 profiling 的 12 组，
按各 workflow 的事件数对其摊销 P50 加权，23.595→20.942us；
不能把加权值称为统一事件延迟分布的 P50。
按这些回放值合计，一遍 9428 条事件只省约 25ms。该值也不是在线累计
成本，因为回放没有实际绑定的原生请求、预测提交及 KV 操作。

worker 对照 24 组中 22 组更低、2 组回退；两项均为关闭 profiling 的
system_automation 任务，分别多约 0.06594us/0.297%、
0.03418us/0.152%。原始结果全部保留，不宣称 worker 路径普遍加速。

V1989 只用一个样本验证基准和逐事件比较可运行，不能用其较好的比例
替代最终 V1990。V1990 保存全部摊销线程 CPU/墙钟样本、源码和 trace
hash，以及每组比较结果。

## 正确性与局限

2104 项 CPU 检查通过，无 skip，包括在线预测实际产生 Hint、并行工具
开始/结束时的失效与重估、旧 worker 特征、最后工具/workflow/镜像失效
的分组清理，以及既有物理事务、部分恢复、原生锁和服务消费检查。

本轮回放是 CPU event application，不是固定需求 GPU 回放，也不是原始
wire 分批的重建。每个 workflow 使用独立历史，未重建跨 workflow 的
原始交付顺序。没有绑定 native request/session；定点估计在计时外直接
检查，而不是在运行时完成实际 Hint 提交或 prefetch。

worker 采用无模型进程的替身；没有 socket、writer I/O、容量管理、
DMA、GPU 或真实服务消费。局部事件降本不能证明在线控制总成本降低
同等比例，也不说明相对 native 的吞吐/JCT 已改善。

## 执行安排

本轮在 main 提交，更新现有关键文档。V88 引擎与完整补丁不变：
树 `1db6d8846499beba8f963db9ba8abc7a832ecda8`，
patch SHA256
`2cbb0b4699e000ffc28bd80f6ee70732fb13b4950e40aa303b9afbc7d5a418ef`。

提交后更新同一个 06:00 等待进程的 CPU 资格与冻结执行目录，保留旧目录。
24-root、16+8/900s、running8、256K、Host80:20 动态嵌套配置保持。
不恢复旧 V85d native；GPU 空闲后先 predictive、后 native。
继续检查大前缀恢复和消费证据中的复制/扫描，最终仍以 GPU 吞吐、平均
JCT、有效迁移及相对 native 的收益验收。

- [V1990 最终事件对照](v1990_online_event_control_cpu_20261020.json)。
- [V1991 CPU 检查](v1991_online_event_control_cpu_checks_20261020.xml)。
- [V1989 基准检查](v1989_online_event_cpu_fixture_check_20261020.json)。
