# 跨轮复用 workload 的准备与验证

日期：2026-10-23，北京时间。代码位于 main；新 workload 尚无 GPU 结果。

## 切换依据

V133b 的三个完整组通过同请求、token、停止条件、源码和容量复核。
每组均为 36 workflows、6907 请求、513,375,471 输入 token、
1,851,543 输出 token，保留所有正常长尾。

| 指标 | native | Full，工具预取关闭 | JOIN-only |
| --- | ---: | ---: | ---: |
| 平均 JCT，秒 | 2339.66 | 2289.13 | 2340.24 |
| 全程输出 token/s | 462.34 | 464.29 | 465.12 |

Full 相对 native 的平均 JCT 下降 2.16%，全程吞吐增加 0.42%；
相对 JOIN-only，JCT 下降 2.18%，吞吐低 0.18%。这是一轮观测，
不能写成可重复优势；全程吞吐也不是忙时 decode 吞吐。
独立证据为 `experiments/reports/v3115_v133b_all_controls_complete_20261023.json`。
V133b 使用原冻结源码，不包含这次 CPU 修改。

因此保留现有结果，将下一条真实源轨迹切换到可复用 subagent 场景，
检验返回后不断增长的私有状态是否带来值得管理的工作集。
不以筛选 Full 加速比的方式选择源轨迹。

## 系统问题与收益条件

一次 invocation 返回，只完成了本次委派；模型以后可能继续调用这个
agent 实例。删除物理 KV 与删除其逻辑会话需要分开处理。后续仍传入
相同私有历史时，缓存策略可以改变恢复或重算代价，不能改变任务语义。

选择性保留只有在以下事实同时出现时才有实际空间：

- 模型自主多轮委派，并真实重新激活了一部分旧 handle；
- 保留这些历史会与活跃请求、parent 恢复或其他休眠状态争用容量；
- 预测和容量决策避免的重算/等待，超过新增备份、恢复与 CPU 成本。

如果全部休眠状态都能作为软缓存低成本保存，`all` 可能最好；如果
后续复用极少，`drop` 可能最好。选择性策略必须与同预算 `lru` 比较，
不能仅击败这两个极端。语义复用预测本身不作为已成立的新颖性。

## 会话、prompt 与缓存策略

沿用已经存在的 `native_in_graph_reusable_nested` 可选 profile，
没有重写旧 workload。`task(description, subagent_type, agent_id=None)`
可新建或继续同一实例；每次激活创建新的 invocation 和 JOIN，
旧 invocation 保持 DONE。复用时追加新任务，保留自己的私有历史、
压缩状态与文件状态；KV 淘汰后仍能重算相同输入。

prompt 鼓励在集成、编辑、测试后，根据新证据继续委派；相关追问可以
回访原 specialist，独立判断另建 agent。每次成功 task 返回稳定 handle、
角色和简短的可选续问说明。模型决定是否再次调用、创建新实例或关闭；
没有强制第二轮、固定 DAG、自动回访或人为工具延迟。所有策略使用
完全相同的 API、prompt 与逻辑会话。

休眠缓存保留策略为：

| 策略 | 行为 |
| --- | --- |
| all | 原生可撤销软缓存，不强迫每次 RETURN 全量 D2H |
| drop | 原生安全释放物理 KV，保留可再次执行的 transcript |
| lru | 在相同休眠预算内优先保存近期返回的实例 |
| selective | 按复用概率与节省代价选择 Host/HBM/重算 |

初始保持 36 roots、同时到达、running/decode 8、seed 21、
256K 上下文及 95% 压缩。Host 共 200GB，FULL:Mamba 为 80:20；
选择性与 LRU 的休眠 Host 预算同为 32GiB，HBM 可撤销预算 2GiB。
这些是初始配置，不是最优容量结论。只有观测到有用活跃历史被持续
挤掉并重算，才对全部策略共同降低 roots，而非提前降到 16。

共同运行策略对齐当前 V108/EOS 分支：workflow 排序、resident-first、
JOIN parent 与阶段优先、EOS JOIN 预取、完整恢复和增量 PREPARE；
普通 execution-frontier restore 与短工具排队连续性关闭。
本次保留对照沿用 V133b 关闭工具提前预取的设置，同时保留工具时长
统计和压力卸载，避免混入另一个尚有争议的模块。

## 预测与本次修复

预测只读决策当时可见的职责范围、角色、结构化未解决项、实际复用
次数和已经发生的新委派范围相似性。采用有界 scope hashing 和收缩的
在线特征分桶，位于 RETURN、委派、实际 resume 和显式关闭等边界，
不逐 decode token 处理文本，不增加 GPU 预测模型。

冷启动采用预算 MRU；至少 16 个标签且各有至少两个正负例后才启用
概率排序。正例是同 handle 的新 invocation 首次实际 LLM_SUBMIT，
单纯声明 resume 不算。显式关闭结束该 episode，可立即成为负例；
其他未复用 episode 仅在 workflow 正常结束时记负例。
guard、执行前取消和未完成 episode 删失。准确率与 JCT 收益尚未知，
不能继承旧工具时长模型的指标。

本次修复了两个会污染标签的缺陷：

1. 旧字符串检查把结构化结果中的 `"unresolved": []` 也判为未解决。
   现在优先读取 Pydantic/Mapping 的真实 `status` 和 `unresolved`，
   自然语言仅作为有界回退，并忽略常见明确否定。
2. 旧显式关闭只设置 closed，后续 workflow 结束又跳过 closed，
   因而漏记负例。现在核验 workflow、context、epoch、invocation
   和 handle，关闭时只记一次；过期或外来关闭不能训练或回收状态。

成本分数仍是冷启动先验下的排序代理，不是 oracle 或校准的 JCT 模型。
目前预算虽按物理 extent 去重，仍保守包含与活跃 context 共享的路径，
不能宣称已精确计算所有边际容量价值。

## 相同工作量的实验入口

`scripts/run_causal_source_capture.py --source-profile reusable_nested`
显式启用新 profile，保留现有任务清单、seed、容量和并发，采用 native
策略采集真实模型决策；此时物理休眠策略为 off。旧采集默认不变。
源审计统计实际 started resume、handle、JOIN、root 轮次和嵌套轮次，
不沿用旧的 80 JOIN 或长尾截断门槛。零真实复用不算该设计的有效验证。

修复了旧重放器的两个兼容性问题：

- 关闭事件属于已经 RETURN 的 child，但由仍在执行的 parent 发出。
  新事件记录调用 parent；因果图同时依赖真实 parent 进度和该 child
  的 RETURN，workflow 结束也等待关闭事件。
- 丢弃休眠物理状态后，原生可在同一逻辑 session 上建立新 generation。
  只在有明确 RETURN/新 activation 证据的第一次请求重新绑定；同次
  activation 内仍禁止 generation 改变，记录每次绑定的来源和结果。

`scripts/run_causal_comparison.py` 新增独立的保留策略矩阵。需显式传入
`--retention-config configs/migration/reusable_subagent_retention_20261022.json`
及 `--arms native_matched subagent_all subagent_drop subagent_lru subagent_selective`。
它继续要求新源轨迹的 GPU smoke、CPU/引擎资格及相同实际容量证据，
不能把旧轨迹的 smoke 冒充新轨迹的验证。保留策略不能混入旧消融矩阵。
运行时还检查实际 retention policy 和 Host/HBM 预算。

原始请求、输入/输出 token、依赖和停止条件在所有 replay 组逐请求
核验。主结果报告完整平均 JCT、P90、吞吐与完成质量；正常长尾保留。
另报告真实复用比例、必要 checkpoint 的独占/共享字节、Host 恢复与
重算、有效活跃状态淘汰、备份量、控制 CPU 和在线概率校准。

## 本次 CPU 验证

- `v3120_reusable_retained_qualified_cpu_20261023.xml`：5103 项及 2 个
  subtest 通过，无失败或 skip，完整保留 V3112 的 5056 项，增加 47 项。
  原有 Docker 沙箱用例不在继承资格集合中，没有扩大为已验证范围。
- `v3122_reusable_qualified_sources_20261023.json`：由实际
  `qualify_sources` 生成；`qualify_report` 对当前源码再次核验通过。
- `v3123_reusable_runtime_constructors_cpu_20261023.json`：四个新控制组
  的真实 `NativeAdmissionRuntime` 构造与关闭通过，不用 GPU。
- 新用例覆盖结构化/非结构化完成结果、显式关闭标签的身份与重复校验、
  原生容量压力下软缓存的可驱逐性、跨 activation 的 generation
  重建与同 activation 内异常变化、关闭动作对 parent 的因果依赖、
  新源采集 profile、共同设置及实际策略/预算观察。

`v3121_reusable_final_boundary_control_cpu_20261023.json` 保存 101 次
正常 GC 的 metadata benchmark 全样本。36 个休眠 context、每个
9 个 extent 的规划 P50 为冷启动 0.220ms / 已训练 0.372ms；
128/17 为 0.727 / 1.403ms，512/33 为 4.160 / 6.060ms。
空 dispatch 约 0.078us，结构化边界特征约 137us，非结构化回退约 260us。
这些是边界元数据成本，不包括实际 native 路径检查、DMA、控制传输或
GPU 等待，不能据此推算 JCT。512 状态时的毫秒级规划仍是实测风险，
不能描述为控制成本已经清零。先前 V3119 的测量也保留。

## 时间限制与下一步

北京时间 **2026-10-23 20:00 至 2026-10-24 06:00** 禁止 GPU 实验。
用户已将开始时间由 18:00 更正为 20:00；此前允许在资格、资源和预计
运行时间满足要求时进行 GPU 实验，不能跨越禁用时段。
已安装单次 reservation guard，维护现有 hold 和 pause marker；
它只停止已登记的本项目进程，结束后仅移除自己仍未变更的 marker，
不会自动启动未经资格验证的实验。

禁用期完成 CPU 回归和配置准备；期限后确认资源、源码冻结和新 hold
状态，再采集第一条自然复用源轨迹。先判断真实复用与容量竞争是否
充分，再运行同轨迹保留对照。当前目标保持 active，未宣布性能达标。
