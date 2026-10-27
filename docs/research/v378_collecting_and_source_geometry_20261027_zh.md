# V378 运行中选择证据与原轨迹规模

本记录只追加 CPU 审计，不修改冻结 V378 的代码、请求、工具时间、
提示词或模型配置。V378 仍使用提交 `83d7c3c2eb34`；完整性能必须等
全部 workflow 结束、原生遥测 drain 和实际 owned 进程退出后判断。

## 新的完整回执前缀

V384 封存 2595 条完整回执，全部匹配 V330 的原始完整工作量、
token 内容、原生停止和 position 采样路径。共有 3674 条目标请求，
不能将该前缀当成完整实验或挑选后的性能样本。

该审计核对 1343 个同快照预算，覆盖原生请求前缀的约 1402.118 秒，
另有约 1.331 秒未覆盖。前缀 active 逻辑预算 token-time 中，
88.606% 为 model pending、6.866% 为已知阻塞、4.528% 为请求之间；
这些量不是物理 HBM 占用率或可移除等待。

前缀 worker 时间加权 batch 为 11.069、decode worker 输出率
373.244 token/s。由于工作进度与完整 V345 不同，不据此计算
最终吞吐加速比；正常尾部继续保留。

## Fitting 的实际取舍

V385 使用记录下来的当前合格 donor 集合，独立枚举验证 347 次
真实 fitting 决策的预算、owner 数量及稳定选择。每次的局部旧顺序
前缀也在同一候选集合上计算，未运行一个替代调度器。

| 相同候选集合上的局部统计 | 实际 fitting | 旧顺序足额前缀 |
| --- | ---: | ---: |
| 累计所需预算/token | 16582516 | 16582516 |
| 累计选中预算/token | 27785573 | 34879826 |
| 累计选中 owner 次数 | 447 | 428 |
| 选中预算/所需预算 P50 | 1.515 | 2.270 |

138 次决策选择了更小预算；16 次选择更多 owner，3 次选择更少，
328 次 owner 数量相同。约 20.3% 的局部预算下降伴随累计更多 owner
切换，不能以“预算下降”直接认定迁移、batch 或 JCT 改善。

这里不是历史 V364 的实测结果，也不是对未来行为的完整反事实模拟。
预算借出不直接驱逐物理页；其后恢复还受真实缓存位置、Host 副本、
原生分配与后续请求顺序影响。下一步以完整真实迁移和两个主指标
决定是否保留该策略，而不是继续追求单一组合优化目标。

## 原轨迹并没有遗漏软预算提醒

V383 从 V230 的真实客户端事件、sandbox 审计和原生请求对账：

| 原始完整工作 | Root | Direct Child |
| --- | ---: | ---: |
| Invocation 数量 | 18 | 92 |
| 模型调用 | 844 | 2830 |
| 输出 token | 188999 | 360672 |
| 工具调用 | 736 | 2738 |
| 每 invocation 模型调用 P50 | 42.5 | 29.5 |
| 每 invocation 模型调用最大值 | 109 | 66 |
| 最大输入上下文/token | 168962 | 53499 |

没有 depth=2 的 grandchild。92 个 child 的实际调用数均超过 8，
最少 10 次；92 个均在第 4、8 次调用收到通知。184 条通知通过
真实 `tool_call_id` 唯一关联到 invocation，而不是只按角色统计。
预算 return 的 workflow/角色/调用次数多重集合也与原生 child
调用数完全一致。其 `declared_status` 全为 unspecified，不能据此
认定业务任务已完成或主动 partial handoff。

这排除了本轨迹“参数遗漏导致根本没有提醒”的解释，也不能把
“允许嵌套”当作“实际发生嵌套”。软提醒已递送并不保证模型遵守
目标。若之后调整 workload，应先检查实际委派范围和模型行为，
不能只重复下调阈值或再次启用本来已有的 nesting 能力。任何提示词
或工作量改变仍需新的自然 capture，不改变正在比较的 V230。

## 已完成 child 的实际释放

V383 的 92 个 child RETURN 均明确 `context_retired=true`，因此
创建时 persistent 元数据不阻止这些终态回收。V382 对完整 V364
记录 92 个 child 的正回收：累计实际释放约 183.522 GB Device
FULL 和 66.478 GB Host FULL。首次正释放距 scheduler 观测 RETURN
的 P50 为 3.174 ms、P90 为 6.756 ms。

这些是累计实际 allocator 释放，不是同时空闲容量、唯一迁移节省
或 parent 的独占页。4 个上下文的最后可见记录还有 pending anchor，
但不释放页的后续 retry 可以不输出记录，因此该数字不能证明最终
仍有 4 个待处理项或泄漏。普通 native 缺少同类运行时记录也不代表
其缓存没有自然回收成本。当前证据不支持“所有完成 child 一直保留”
是 FULL 恢复增加的主要解释。

## 证据位置

- V382：`experiments/reports/v382_v364_terminal_cache_lifetime_20261027.json`。
- V383：`experiments/reports/v383_v230_source_invocation_budgets_20261027.json`。
- V384：`experiments/reports/v384_v378_collecting_exact_work_20261027.json`。
- V385：`experiments/reports/v385_v378_fitting_decision_tradeoff_prefix_20261027.json`。
- [冻结实验与实际进程](v378_fitting_lending_start_20261027_zh.md)。

以上前缀与离线诊断均不满足完整或可重复性能验收，其他 baseline
仍等待完整同工作量优势。
