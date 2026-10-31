# 两秒全局 oracle 与 JOIN 组合准入的成本诊断

## 为什么继续使用当前负载

12 初始 workflow、平均 70 秒到达已经有完整匹配对照，不重跑该对照、
不同时改变到达方式，不修改 trace 工具时长。本候选复用全部 56 个原项目、
固定 12 warmup / 18 measured / 26 background、完整 token/依赖和 200 GB Host。
共同吞吐窗口及全部测量成员的完整 JCT 保持一致。

[上一轮完整结果](../research/toolaware_oracle_complete_and_costs_20261031_zh.md)
没有达到 JCT 目标。实际选择已经到达服务，但存在 prefill/decode 换算偏差
与单体排序减少可并行对象的情况。本候选同时调整时间成本与集合选择，
明确是联合机制诊断，不是只切换 remaining-work ranking 的消融。
旧配对及未通过的背景覆盖/前缀丢失检查全部保留。

## 运行路径

使用已有 `WorkflowCombinationController` 的六候选窗口，
仅为观察到的 sibling 扩展到八名，搜索软预算为 0.5 ms。
元数据构造、原生检查、日志和后台成本另计；该预算不是整轮控制的硬上限。
不实现逐 decode 全队列重排，不因短任务判断抢占已有 active。

周期恢复继续使用本入口的工具感知 decay 预算，普通事件补位继续使用
完整未折扣空闲额度和四个授予上限。两者使用相同组合评分，但不混用预算。
两秒窗口仍用于普通准入；必须已提交请求、满足真实依赖且当前 ready。

组合参考 S0 保留 ready JOIN 保护、原类别/上下文稳定顺序和已有 sibling
邻接，再比较窗口内可行子集。原方案入选者无法完整保留或摘要不可用时，
返回 S0。资源检查、Host backing、原生 allocator、锁与 DMA 仍有最终权限。
逻辑折扣和计划暂停不提供物理空闲页。

## 工具预测卸载继续启用

运行时 A→B 是原有预测式工具卸载的一部分，不增加关闭开关。
它不等两秒普通窗口；先比较当前真实 ready-paused 消费者，再检查真实阻塞
供体及完整单供体配对。只有实际安全释放后，才重新核对完整容量并授予 B。

这一入口不能使用“供体尚未释放时的当前空余额度”预先筛掉 B，
否则在最需要供体的时刻会得到空集合。为此工具配对使用相同工作量排序，
而不走普通空余额度组合筛选。多供体全局组合仍未实现，不能宣称所有入口
已经完成统一组合最优化。

工具预取、普通恢复租约、逐层 handoff 和 mixed chunk 保持关闭。
JOIN/EOS 和 prepare_host 保留；仍在等工具的 paused owner 不因预测返回时间
到达而获得资格。真实返回、其他依赖及已登记后继共同决定 ready。

## 成本与评分冻结

v2 oracle 使用已完成对照的 worker 区间代理：

- 输出步长 `0.023478283345973637 s/output token`；
- 输入成本 `0.0000832252503024952 s/input token`；
- 第一次实际观察到正输出进度后，移除本请求的剩余 prefill 成本；
  输入的固有工作记账仍在请求完成时结算。

固有工作量、0.01 人口统计、分层及源 DAG 均不改写。
工具关键路径继续按并行依赖取最大值，不累加并行等待。
这不是最优 JCT 上界或纯 GPU 时间；原生进度通常以 128 token 或完成事件上报。

组合沿用既有 R5 代理：

```text
workflow weight = 1 / (1 + remaining model/tool path seconds / 600)
set score = sum(workflow weight * capped JOIN coverage increment)
            / (0.1 ms + incremental copy/prefill/16-step decode cost)
```

600 秒沿用既有控制器默认归一化，不根据这次 GPU 结果调参。
JOIN coverage 使用当前未完成成员、已有 active-ready 成员和新增成员的
平方覆盖增量，每 workflow 封顶，避免 fanout 增加评价身份。
恢复成本按缺失 extent 去重，decode 成本按现有 batch 的增量查表。
候选资源摘要无法支持比较时保留原生回退。

关闭 remaining-work ranking 的显式配置不读取未来路径构造组合效用，
直接使用参考集合。新比较读者验证源请求、图、工具、到达、资源、窗口及
目标阈值一致，并拒绝将此候选标成 ranking-only。
不能对测量组身份增加权重或按完成结果重新选择测量成员。

## 已完成的 CPU 验证

`calibrated_combination_initial12_mean70_prepare_r2` 是最终 CPU 配置：
实际构造入口启用全局组合与两秒窗口。
保留 r1 准备记录；r2 补充准确的联合变化和既有对照复用声明，
两者都尚无 GPU 结果。

定向测试覆盖普通窗口、ready JOIN、真实 native 选批候选、
零空余额度下 A→B 排序、工具阻塞排除、关闭排序后的未来信息隔离，
以及流量/资源/固有工作/成本声明不匹配的拒绝。
初次定向 r1 的三个夹具失败保留；修正模拟时钟/前缀证据，并让空候选
跳过不必要的组合调用，随后独立定向检查通过。

源码绑定共享检查：
`experiments/reports/remaining_original_projects_20261030/calibrated_combination_initial12_mean70_bound_cpu_r1/evidence.json`。
6212 项及 23 子项通过，无失败、错误或跳过；6135 个先前 literal identity
全部保留，2249 个源码输入及原生树在执行中不变。

完整旧对照事件回放验证 9310 个已完成请求、52 个关闭 workflow；
固定测量组的 2819 请求、362072 输出完整守恒。
其余背景仍按原协议删失，不表示全部 56 个完成。
383 次 call-boundary 路径检查累计约 855.978 ms CPU，
不是实际在线控制成本或 GPU/JCT 收益。

## GPU 验收

需要干净冻结源码、当前预约/资源检查、新的 24 请求 position/session/stop
与采样资格。正式运行仍在 18 个固定成员全部自然完成后终止非测量背景，
收集新鲜排空及实际进程退出证据。

除完整平均/P95 JCT 和固定共同窗口吞吐外，核对：

- 各入口的组合次数、改变集合次数、回退原因和全部控制成本；
- 授予是否被压力撤销、是否为同 attempt 的后续真实服务；
- sibling 的实际服务覆盖、Host 前缀丢失及迁移/重算；
- 长 workflow 和背景任务退化，以及背景到达覆盖的原失败项。

不得因某次集合评分更高就宣称 JCT 改善。此文档记录 CPU 候选，
GPU 与性能结果在后续父实验的命名证据目录中保存。
