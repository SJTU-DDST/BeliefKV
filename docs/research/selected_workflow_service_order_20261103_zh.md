# 已选 workflow 的实际服务顺序

2026-11-03，Asia/Shanghai。等待新 GPU 实验期间，对 Host400、original73、
20 初始加 mean70 的两轮已完成 R5 horizon64 做只读诊断。
没有改变冻结队列、模型、工作量、到达表或 GPU 配置。

## 问题与方法

需要区分两个问题：是否选中了更合适的 agent 集合，以及已入选集合内
的短 workflow 是否被其他成员推迟。本次只检查后者。

重用已经封闭的成本分母审计中的完整决策与真实剩余模型工作量标签，
重新验证实际选择、exact request/attempt/session 授予和 native 正进展。
标签仅作离线分析，没有作为本轮在线输入。

读器按原生 service helper 的既有边界，要求：

- 实际选择与原记录、完整 search 一致，不能用假想的替代选择关联服务。
- 实际获得额度并保留资格，排除另外的工具 A→B 替换授予。
- 新的正进展在该次授予之后、下一次控制或真实干预之前出现。
- 每个 workflow 在一次决策中只取首个有效服务，避免 sibling 数量放大权重。
- 排除具有 required JOIN 成员的 workflow，避免将正常依赖优先解释为长任务插队。

首次服务采用已观测 worker 区间开始时间，不是 CUDA kernel 开始或
streaming TTFT。窗口外服务单独保留为未知，不改称该次选择的长等待。

## 结果

| 诊断计数 | R5 首轮 | R5 确认轮 |
| --- | ---: | ---: |
| 有完整选择和工作量标签的决策 | 530 | 508 |
| 至少两个未保护 workflow 实际入选的窗口 | 104 | 155 |
| 所有这些 workflow 均有窗口内服务证据 | 92 | 141 |
| 窗口内服务证据不完整，保持未知 | 12 | 14 |
| 最短已选 workflow 位于首个服务波次 | 80 / 92 | 130 / 141 |
| 最短已选 workflow 后于其他成员服务 | 12 / 92 | 11 / 141 |
| 后者涉及至少一个测量成员的窗口 | 5 | 2 |
| 最短成员相对最早成员延后，所有完整窗口 P50 / ms | 0 | 0 |
| 同一延后，P90 / ms | 80.702 | 0 |
| 同一延后，最大值 / ms | 480.683 | 841.614 |

不同决策可能涉及同一个 workflow 或请求，这些窗口不是独立的 JCT
样本；不能把延后相加后除以 18，或解释为可消除的 JCT 上界。
这也不证明所有未选候选均物理可行。

完整决策中的非原 S0 新入选成员，分别有 58、48 个观察；
其中 48、37 个在对应有效窗口内获得新正进展。
已观测窗口内选择到服务的中位时间分别为 353.947、186.022ms，
P90 为 837.631、773.945ms。其余观察保持未知，
不能以之后的重授予服务证明早先那一次选择已经生效。

该总体只覆盖有完整标签的 search，排除了原先的 soft deadline 和元数据
fallback，不能替换已有全入口新成员审计的 62 等计数。
诊断覆盖完整运行前缀的预热和背景工作，不是共同墙钟吞吐窗口统计。

## 对后续工作的影响

可观测的同组短 workflow 大多已经率先获得服务；剩余已确认延后均低于
一秒。因此，目前应降低“只调整已入选成员首次服务顺序”的优先级，
继续验证准入集合和同步控制优化，而不是立即新增原生队列重排。
这是对当前观察范围的判断，不是其完整 JCT 收益上界。

这个检查不覆盖每个 workflow 后续全部 chunk/decode 的公平推进，
也不能排除长 chunk owner 或物理恢复影响。
12、14 个不完整窗口，以及新入选者未在原窗口服务的观察，仍可进一步
区分实际 launch、控制重授予、KV 恢复与执行阶段，不能笼统归因于
“排序没有传到 native”。

此前的跨集合成本反转审计是另一个问题：更短 workflow 可能根本没有
被选中。当前结果不能据此否定已排队 benefit-only 的集合选择实验。
新实验仍需固定 18 成员的完整 JCT、共同窗口吞吐和实际服务验证，
约 1% 可重复收益目标尚未达到。

## 来源与验证

新入口为 `scripts/audit_selected_workflow_service_order.py`，主提交
`1160af15`。为避免新 benefit policy 影响旧 ratio 的重放，分析从
`b8aa9a44` 创建独立目录并只加入该读器，冻结为 `69ffce4c`。
其 policy SHA256 为
`59b82e9691a988dcd79d212363dc24e843a617563efcc3b169d735b154983aba`，
与两轮实际 serving policy 一致。当前待跑服务 `6f1c648b` 未变化。

新读器和已有授予/服务边界检查共 38 项通过，干净分析目录也实际执行
通过同一组检查。包括错误 attempt、错误选择、窗口外服务、资格丢失、
另一路工具授予及 protected JOIN 排除。CPU 使用 nice19、
CUDA hidden、单线程；没有运行 GPU 或推断性能收益。

```text
experiments/reports/remaining_original_projects_20261030/useful_token_cost_r1/
  r5_host400_horizon64_r1/
    selected_service_order_20261103_r1/
      development_cpu.xml
      frozen_cpu.xml
      first.json
      confirmation.json
```

输出绑定原成本分析、注册、模型、固定 cohort、实际 policy、读器和
封闭原生日志 SHA256，重新核对两轮测量组完整的
2991 请求 / 362157 输出 token、自然完成、fresh drain 与 owned exit。
原确认轮背景到达尾部覆盖失败继续保留，未修改为合格 workload。

## 首次服务身份覆盖补查

另行检查了两轮全部 92、141 个原完整窗口，读取相同封闭 native 前缀，
搜索每个已选成员在其 workflow 首个可验证服务之前的样本。
已知其他 attempt/context/session、零进展和选择前已启动的 batch 排除；
较早的缺身份正进展、未知进展，以及未被原授予证据覆盖的 exact 正进展
均会使该窗口保持不确定，不把部分身份补成确定服务。

两轮均未发现上述更早的不确定或未关联样本。因此，补查后首波服务
80/92、130/141 和稍后服务 12/92、11/141 的计数保持不变。
这只加强原有总体内的服务顺序证据，未扩展到原 12、14 个未知窗口，
也未将首次服务顺序解释为完整 JCT 收益。

补查入口为 `scripts/audit_selected_service_identity_coverage.py`，
与原服务和周期边界测试共 45 项通过。新增证据仍在同一目录：

```text
identity_coverage_cpu.xml
first_identity_coverage.json
confirmation_identity_coverage.json
```
