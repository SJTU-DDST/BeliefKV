# Oracle 组合规划与即时物理准入分离

## 问题与证据

旧资源组合候选在规划 paused -> active 集合时，要求整个集合的缺失 KV、
下一输入 chunk 和增长预留立即放进当前 free list 加已验证的安全冷页。
逻辑预算足够并不保证这个瞬时条件成立。因此组合层可能直接回到 S0，
之后原生路径却能通过正常卸载和恢复推进 S0 中的请求。

旧 56 项目完整记录的 364 次实际完整搜索中，224 次没有满足该物理摘要的
子集。后续精确服务审计证明部分回退授予正常推进，但不能证明选择瞬间
所有替代集合都已经物理可执行。这个区别不能通过删除原生分配或锁检查解决。

新 69 项目的非排序控制仍然在运行，不能将旧 56 项目的上述机会次数
直接解释成新负载的实际竞争、完整 JCT 或新候选的 GPU 收益。

## 显式候选

新增 `r5_frozen_batch_cost_proxy_capacity_v6`，只能由显式的
`--plan-device-capacity` 配合完整 batch 成本、深前缀与可行替代集合契约生成。
默认和 v1 至 v5 的规划行为保持原样；没有修改当前冻结的实验。

候选将两个不同问题分开：

1. 规划决定哪些合法 ready-paused agent 获得逻辑资格。保留原入口逻辑预算、
   请求槽位、prefill/chunk 输入、decode 增长、必要前缀摘要与 required JOIN，
   并检查所需 KV 几何不超过实际观测的完整 FULL 池容量。
2. 实际准入继续由原生分配器、页引用、Host 副本、锁、在途 DMA 和最终
   prefill 容量检查决定。逻辑资格不产生空闲页，也不保证马上启动。

规划判据中，原来的设备部分为：

```text
required_device = unique_missing_prefix + next_chunk_input + growth_reserve
required_device <= current_free + verified_safe_cold
```

显式候选使用：

```text
required_device <= observed_full_pool_capacity
```

同时保留前一判据的真假值作为 `current_device_summary_fits`。
不能把后一个判据称为即时物理可行，或将整个 FULL 池写成当前空闲空间。
`device_tokens` 和 `cold_device_tokens` 仍记录原观测；
单独的 `device_capacity_tokens` 只表示规划的硬件几何上界。
未知、无效或与 free/cold 摘要矛盾的池容量使本轮回到原方案。

例如逻辑恢复预算可容纳 B 或 C，但 HBM 当前没有空闲页；
已有阻塞状态将由正常路径处理。旧组合层无法比较 B、C，
新候选可先在相同成本和剩余工作权重下选出其中一个。
其恢复仍需等待原生容量落实；预测错误、Host 压力和其他锁可能使它等待更久。
这不是新的物理抢占权限，也不是保证比原来的 S0 更快。

## 不变的执行约束

只从原入口允许的已提交且依赖满足的 ready-paused 候选中选择。
原有 active agent 的连续性、工具 retention、压力处理与真实 JOIN 保护保留。
不为一个更短 workflow 新增 ready-peer 驱逐或强制时间片。
JOIN/EOS 预取、prepare_host 和预测工具卸载沿既有路径；
工具预取、普通恢复执行 lease、逐层 handoff 仍然关闭。

缺失前缀的观察、共享 extent 去重、H2D/prefill 成本和完整 batch decode
成本继续参与评分。不是只根据逻辑 token 排序，也不豁免大上下文成本。
仍使用六候选、按需八候选和原 0.5 ms 搜索软预算，不新增扫描或后台模型。
实际资源获取和控制成本继续单独记录，不能用纯搜索耗时代替整轮控制成本。

日志明确记录 `device_fit_scope`，离线回放器要求它与冻结 profile 一致；
旧 profile 不能通过注入一个完整池容量字段获得新的规划权限。
全部日志继续记录 `physical_capacity_credit=false`。

## CPU 验证与差分边界

相关策略、实际 runtime 构造、深原生前缀读取及回放检查 230 项通过。
首次调用漏设原生源码路径，保留一项 skip 的原始报告；
补齐已资格化原生 checkout 后，230 项全部通过。
差分审计另有 6 项检查通过。
完整源码绑定回归
`combination_device_planning_bound_cpu_r1/evidence.json` 已通过：
6532 项测试和 23 个子项，保留先前全部 6467 个字面测试身份与源码路径；
2391 个源码输入、370 个外部输入及原生 CPU checkout 在执行期间均未改变，
无失败、错误或跳过。XML 有 6532 个 testcase，testsuite 的 tests 合计为
6555，23 个子项不能再冒充额外独立测试身份。
这份资格仍需要在干净冻结目录重新核验，不能直接算作 GPU 资格。

只读 `scripts/audit_combination_device_planning.py` 先精确重建旧 6484 次
控制入口中的实际搜索及截止点，再比较 364 次完整搜索。
6088 个元数据早退入口、29 个搜索函数早退和 3 个真正 deadline 分别保留，
没有把 32 个未完整搜索都称为 deadline。

| 相同候选、成本、逻辑预算与 JOIN 权利 | 即时物理摘要 | 完整池规划 |
| --- | ---: | ---: |
| 存在可比较子集的完整搜索 | 140 | 364 |
| 至少两个未受保护 workflow 各有可选集合 | 39 | 178 |
| 无法同时覆盖所有这些可选 workflow | 16 | 106 |
| 没有可比较子集 | 224 | 0 |
| 严格缩小 S0 的集合 | 41 | 22 |
| 选中 S0 之外成员，重复决策计数 | 11 | 38 |

74 次选中集合与即时摘要的 full-batch 结果不同，其中 35 次更大、
15 次更小。263 次选中集合仍不满足当前 free/cold 摘要。
这些集合需要后续原生腾空间，不能计为 263 次立即服务或无代价恢复。
本诊断没有模拟未来控制、在线 deadline、原生实际服务或 JCT。
既有负收益实验和默认策略保持不变。

## 后续验收

当前 69 项目的匹配非排序控制和 ThunderAgent 已完成：
Predictive 平均 JCT 813.056404 s，ThunderAgent 889.845843 s，
数值改善 8.629522%，共同窗口全部输出吞吐提升 2.033957%。
这是原有非排序 Full 的整体收益，不是本候选或 Oracle 排序的贡献。
两臂背景到达尾部未全部覆盖，且 Full 的共同窗口 ready-paused 样本比例
只有 15.837104%，原有正式验收仍未通过。保留这些失败后再决定后续负载
和 Oracle GPU 诊断，不以正向数值代替 workload 验收。
新负载的成本校准、源码绑定、干净冻结及新 position/session/stop 和采样
资格均不能由这份旧决策回放代替。

GPU 验证必须确认：选择发生变化，资格没有被紧接的压力处理撤销，
同 request/attempt 在有界观察内获得有效服务，原生容量等待没有显著增长，
并且最终平均 JCT、共同窗口吞吐、长任务服务间隔和重算一起改善。
若更长的容量等待、额外 H2D 或控制成本抵消收益，保留负结果并停用候选。
