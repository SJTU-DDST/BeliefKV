# AgentX SPAWN/JOIN 重建的证据与评测边界

审计日期：2026-10-25，America/Los_Angeles。
本文仅记录读源码和离线统计结果，没有更改运行策略、固定计划或冻结实验。

## 判断

从公开 AgentX trace 重建 SPAWN/JOIN 可以作为系统评测负载。固定作者
重放器也这样构造执行图。因此，“源数据缺少独立 JOIN_WAIT 字段”并不
意味着任何重建 runtime 都不能报告正在执行的等待操作。

需要区分两个命题：

- 重建程序实际执行了某个 await，其 runtime 可以在此时报告阻塞成员、
  各 child 的实际返回和屏障解除；这是该重建程序的在线状态。
- 原应用在相同位置已经开始等待相同成员；公开时间戳不能独立证明它。

此前保守信息组过滤 timestamp-inferred JOIN 的行为保持原样。若另设带有
可观测等待的重建 runtime 信息组，应显式声明并对每个比较方法采用相同
事件契约，不能将其与原始等待边界的直接观测混为一谈。

## 固定作者依据

作者仓库：`SemiAnalysisAI/agentx-harness`，提交
`e4bb2f835ffedbbe6b76252ea5c93caec68c5faf`。本地 checkout 干净。

- `docs/tutorials/agentx-mvp.md` 的 Subagents 节明确说明：有前驱和后继
  parent anchor 的条目变成 SPAWN/JOIN，相同 anchors 的条目可合并；
  无后继的后台 child 不阻塞 parent。
- `src/aiperf/dataset/loader/weka_trace.py` 的 `_reconstruct_serial` 从
  subagent marker 之前的保留 parent 请求选创建前驱，并选择时间戳不早于
  child 结束的首个后继 parent 请求作为 JOIN anchor。
- `src/aiperf/timing/branch_orchestrator.py` 的 `_maybe_suspend_parent`
  在 parent 到达 gated turn 前才挂起；parent 在此前可以继续发请求。
  `_arm_join_replay_deadline`、`PendingBranchJoin.can_release` 和
  `_satisfy_prerequisite` 共同要求 child 完成与计时条件就绪。

SPAWN marker、按 hash 推断出的辅助流和时间戳重建 JOIN 的证据强度不同。
不能把所有检测到的流都称为模型显式创建的 subagent，也不能把创建时刻
直接当作阻塞时刻。

## 当前固定人口统计

输入：`experiments/prepared/agentx_whole18_lifetime_20261025/workflows.json`。
SHA256：
`dbd4bf13db5df36ce53c6a3de7313dc926399c18b551f94be141276f28c0d55f`。
18 个完整会话，2229 个请求，37 个重建 JOIN。

每个 JOIN 的统计量为
`next_parent.recorded_start_s - max(member.recorded_terminal_s)`。
没有单独 terminal 的成员使用末次请求开始加 API 时长。
分位数采用线性插值；这是原始记录间隔，不是本轮 GPU 测量。

| 重建 JOIN 口径 | 数量 | 间隔 P50 | P90 | 最大 |
| --- | ---: | ---: | ---: | ---: |
| 全部成员 | 37 | 1.564s | 46.101s | 417.471s |
| 含显式 subagent | 26 | 1.617s | 47.379s | 417.471s |
| 至少两个显式 subagent | 18 | 1.749s | 110.311s | 417.471s |
| 仅推断辅助/worker 流 | 11 | 0.497s | 37.341s | 247.422s |

全部 37 组中，11 组在 1 秒内续接，23 组在 2 秒内，27 组在 5 秒内。
18 个多显式 child 屏障中，12 组在 2 秒内，14 组在 5 秒内。
含显式 child 的这些组中，最后一个显式 child 也是最后一个成员。
正常长间隔全部保留。

例：会话 `07dd40536557a1d6440a923557c3129dc929` 的最后一个成员
在 484.681s 完成，parent 后继请求于 486.655s 开始，相差 1.974s。
这个样本支持较快的 JOIN 续接解释，但无法确定原始 wait 的开始位置。

六组的 child 在 parent 前一个请求结束前已经完成，其中两组含显式
subagent。它们在源码锚点上是 JOIN 依赖，但不建立一个到达时仍在等待
child 的阻塞区间。重建 runtime 不能因此无条件卸载 parent。

上述正向顺序部分由“选择结束后的首个 parent 请求”的算法直接保证，
不能用它循环论证原应用存在因果依赖。短间隔是佐证，无法辨认遗漏的
客户端处理、用户活动、工具等待或其他前置条件。

## 已确认的计时适配差异

固定作者实现从 parent 前一请求实际完成处启动独立 replay timer，
同时等待对应 child 完成。计时器和 child 两个条件都满足才解除阻塞。
因此即使 child 已返回，官方运行也可能尚未提交 parent 后继。

本项目 `beliefkv/experiments/agentx_replay.py` 当前以 child RETURN 等
依赖为屏障，再从原始开始时刻减去最晚前驱结束得到 residual gap。
该 residual 在依赖满足后执行。其他跨流前驱也参与 reference_end。
当前编译源码 SHA256：
`e57b447c81d1ef9af7655e48e41a409f334059ac95b55bd6fa65b91f7b2fb33e`。

这是两种不同的反事实计时模型。它们可在原服务时长下对齐，但在 child
执行更快或更慢时不保证相同。因此当前结果应称为本项目声明的依赖驱动
AgentX 结构重放，不能称为计时完全相同的官方 harness 执行。
这个差异应以共同人口、共同 token 和相同容量下的计时敏感性比较验证，
不通过修改冻结实验消除记录。

## 可观测重建 runtime 的评测要求

若采用此信息组，在线策略只在操作实际执行时接收事件：

- child 实际创建时报告 SPAWN；后台创建不自动撤销 parent 执行资格。
- parent 实际走到声明的 await 且仍有未完成成员时报告 JOIN_WAIT。
  若成员已全部完成，不建立有持续时间的阻塞状态。
- 每个成员真正返回时报告 RETURN；满足对应屏障后报告 JOIN_SATISFIED。
  JOIN 的成员集以实际注册的依赖为准，未来图保留在私有驱动。
- child 最后一次模型调用、剩余输出长度、synthetic terminal token
  都不能提前当作原生 EOS 或即将完成的语义证据。

所有比较方法共享同一控制事件可见性；各论文策略按各自规则消费这些
信息。已冻结历史信息组保留，不能无声明地与新组混合。

要提高论文说服力，适合保留当前公开 trace 的保守信息组，并单独验证
上述重建 runtime。敏感性重点包括显式 child 与仅推断流、等待开始位置、
独立 timer 与 residual-gap 两种时序；所有正常间隔和完整会话均保留。
这些是后续验证建议，本文未新增注册或取得性能结论。
