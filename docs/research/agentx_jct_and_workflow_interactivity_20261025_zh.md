# AgentX JCT、直接重放与 Workflow Interactivity

2026-10-25。AgentX 是用户指定的主负载，不准备或推进备用负载。
使用公开轨迹直接执行 baseline；无需 native 再生成一条源轨迹。
native 如作为比较方法，执行相同发布轨迹；小规模数据面校验也不是重新采样。

## JCT 的定义

AgentX 可以测量已发布 workflow 的 JCT。固定选择一组完整会话，从
WORKFLOW_START 运行至 root 与全部后台子流完成，保留每个模型请求和
依赖边，服务时间由当前被测引擎实际执行决定。不能把原 API 时长作为
sleep 再加一次，也不能在 JOIN 之后重新等待已经由 child 覆盖的时间。

原始代码内容与测试结果未公开，不妨碍这项系统指标。需要区分：

- 发布轨迹在共同底座上的完整完成时间，可以测量。
- 原始代码任务的修复正确性，无法由脱敏轨迹恢复。
- 采用空闲压缩规则后的 benchmark JCT，不能称为原开发者会话的原始墙钟时长。

官方使用固定 profiling 时长、随机中段和会话回收以测稳态；本项目独立声明
固定人口从头至尾执行的适配。所有方法使用同一组完整会话、原始延迟、
输入/输出 token 数、依赖、模型、种子、容量和运行时空闲规则。
正常长尾不删除，不按测得的性能挑选会话或片段。

## 长外部间隔的官方处理

固定作者提交：
`SemiAnalysisAI/agentx-harness@e4bb2f835ffedbbe6b76252ea5c93caec68c5faf`。

在 `src/aiperf/common/scenario/inferencex_agentx_mvp.py` 中：

| 预设 | 会话树空闲上限 | 系统空闲上限 |
| --- | --- | --- |
| `agentx` | 默认 300 秒 | 10 秒 |
| `inferencex-agentx-mvp` | 默认不设置，可显式指定 | 10 秒 |

“默认不设置”指没有为单个会话树启用空闲上限，原始外部间隔仍然参与
重放，不表示忽略或删除外部间隔。MVP 同时明确禁止忽略 trace delays。
例如，A 的下次请求还有 300 秒，而 B 持续有模型请求在途，MVP 不会仅因
A 等待就提前触发它；若全系统都没有请求在途，则由全局 10 秒保护推进
待触发计时器。我们当前采用的是另一个预设 `agentx` 的 300/10 秒规则。

`agentx` 不是对每个 parent/child 间隔分别做 `min(delay, 300s)`。
会话树 watchdog 仅在 root 和全部后代都没有在途模型请求时启用；排在
服务端队列、等准入或等 KV 的请求仍是在途请求。预算耗尽后前移该树的
待触发计时器，继续受原有 spawn/JOIN 屏障约束。

系统 guard 在没有请求在途、没有立即可执行工作时，统一前移所有待触发
轨迹的计时器，让下一项工作在剩余 10 秒预算内触发，保持待触发计时器之间
的相对间距。控制任务、真实模型运行和依赖屏障不能当作外部 sleep 快进。

本项目显式采用 `agentx` 的 300/10 秒规则，保留原始计划延迟，逐次记录
会话树与全局快进。保留不压缩的 raw 选项。所有比较方法使用相同规则；
快进量是审计信息，不能加回压缩后 JCT 冒充真实原始调度的反事实 JCT。

结果中的 `replay_timing` 另外记录
`no_inflight_external_timer_wait_ms`、次数、最长一次和
`no_inflight_external_timer_wait_fraction`。它们只统计驱动没有在途请求或
控制协程时，实际等待外部计时器的时间；服务端排队、准入和 KV 恢复均不
属于可压缩外部等待。这是低并发下长间隔是否占据实际运行时间的证据，
不是从 CUDA 时间线测得的 GPU 空闲率。主 JCT 和全程吞吐仍包含这些实际等待。

此前“只有 6 个会话”的判断来自本项目自行加入的整会话筛除规则：
只要含超过 300 秒的外部间隔就丢弃整行。该规则不是官方做法，现已移除。
旧拒绝报告保留，不作为当前人口不足的依据。

## 当前固定源

为避免逐请求过滤，当前选择未经过 256K 请求删除的发布版本中，本身全部
调用都符合共同上下文限制的完整行：

- Dataset：`semianalysisai/cc-traces-weka-062126`
- Revision：`23f152f6f0f9399a85901b89a6458def0ef16729`
- SHA256：`29b6a19e751ff5230771519aab755f80a0f43a4ba9cf96b72d3a6a437ec99276`
- 393 行中，220 个完整会话符合上下文限制，28 个具有审计通过的显式并行 JOIN。
- seed21 固定选择 18 个会话，保留 2,229 个请求、189,039,040 个输入 token、
  2,114,833 个输出 token、64 个显式 subagent marker、20 个根据显式 marker
  的创建前驱和汇合后继推断出的多 child 分组。按同一 parent 后继合并后，
  对应 18 个含至少两个显式 child 的 JOIN 屏障。
- 加入作者分链规则处理辅助/溢出流后，执行图含 288 个 invocation、270 个
  spawn、37 个 JOIN。这些 spawn 不能全称为模型显式委派。
- 每个 workflow 使用独立 cache namespace，树内保留源 block hash 共享。
  脱敏数据不提供原始项目身份，不以会话 ID 冒充项目证明。

路径：

- `experiments/prepared/agentx_whole18_lifetime_20261025`
- `experiments/prepared/agentx_whole18_lifetime_tokens_20261025`

旧的 `runtime_idle` 准备保留。最新准备只修正生命周期：调用链返回不再
无条件声明会话关闭；未知和持久上下文保持普通可淘汰缓存，显式临时
上下文可在返回后退休。原始 64 个显式 child 均没有生命周期字段，
`subagent_type` 全为 `Subagent`，会话内没有重复的显式 `agent_id`。
因此不能声称已观察到临时/持久分组，也不以未来最后一次使用作为在线标签。

新 `plan.json` SHA256：
`eecbe4799515c0bd2c8480227ba1f2b29a9fa9c3abb01c650b03551587017932`。
2,229 个请求及依赖、延迟、输入输出完全保留，重新生成的 `index.json`
与 `tokens.u32` SHA256 均与旧包相同。没有更换会话或删请求。

公开的是长度和 hash，输入内容按作者的“会话作用域内 hash 确定性采样”
原则离线合成，并以 token ID 直接使用原 `/generate` 入口。每个方法读取
同一份只读 token 包，固定输出长度、synthetic terminal token 和 seed21。
复用原生 sampler 后的位置强制输出路径，实际执行完整 prefill/decode。
原始输出到后继输入之间的精确 token 重叠不可恢复，已在 token manifest 中
明确标记；不把合成 token、terminal token 或匿名间隔称为原始内容、
原始 EOS 或已知工具语义。token 合成、hash 计算和打包在测量之外完成。

在线控制器仅接收允许在线使用的已发生事件。未来 DAG、后继请求和剩余
输出长度只属于私有驱动/重放数据面，不作为在线调度与预测器的输入。
以下后续审计修正了旧实现把推断 JOIN 当作已观察事件的错误。

关于重建程序能否报告其实际执行的 await、child 返回后的续接间隔和与
官方 JOIN timer 的差异，另见
[SPAWN/JOIN 重建方法审计](agentx_join_reconstruction_methodology_20261025_zh.md)。
该审计补充了可另行声明的信息组，不改变下述保守路径或冻结实验。

## Parent 阻塞证据修正

2026-10-25 用户明确指出：发布数据没有 parent 的实际 `join_wait` 工具
调用边界。当前适配按时间戳推断汇合后继，因此不能认为控制器能在真实
阻塞发生时立即收到通知。旧实现将这类事件应用到在线 RCCG，确实会把
parent 标为 `WAIT_JOIN`；普通 working-set/ThunderAgent 组合候选还会
因此撤销活跃资格。不能只修新增的备份入口。

开发分支现在把推断 JOIN 留在私有重放依赖与完整事件日志中，在投递给
策略前过滤；runtime 入口再次验证来源。过滤涵盖创建、等待、满足和超时，
识别 `confidence=inferred`、明确的推断来源或 `online_observable=false`。
因此旧的固定计划也可隔离，无需重新采样、改 token 包或改依赖。
新编译计划为三个 JOIN 边界都保留相同来源标记。

当前固定计划的审计为：

| 对象 | 数量 |
| --- | ---: |
| 私有 JOIN 屏障 | 37 |
| 推断 JOIN 事件：创建、等待、满足 | 111 |
| 原始全部事件，不含模型请求节点 | 9315 |
| 隔离后可投递给在线控制器的事件 | 9204 |
| 保留的实际模型请求 | 2229 |

`SPAWN` 仍为后台创建，parent 可以继续提交请求。已有增量备份只创建
Host 副本，不释放 Device 容量；没有真实等待证据时不能因推断 JOIN
卸载 parent、撤销活跃资格、提升 JOIN 优先级、建立 sibling 组或发行
恢复 ticket。`RETURN` 也不能把一个私有屏障升级为真实在线 JOIN/EOS 证据。
以后若接入真实框架等待通知，原来的等待备份、卸载和恢复路径仍可使用。

新登记的 baseline 明确记录每个 workflow 的在线事件数和私有 JOIN
事件数，完成时逐 workflow 严格核对；不通过放宽事件完整性检查来过滤。
V219c、正在运行的 V220g 和排队的 V224b 等冻结内容未改动，其结果属于
原来的“暴露重建 JOIN”信息组。尤其 KVFlow 的距离和 TokenCake 的
分组曾可能受该信息影响，不能直接与修正后的 Full 宣称同信息性能对照。
保持用户要求的当前周期和并发设置，后续对照必须说明信息契约。

四个 paper adapter 已有显式 sessionless 路径；Full 仍有绑定 native
session 的备份、恢复入口，不能以 paper 路径通过验证替代 Full 适配。
固定官方文档的示例使用 chat completion，并未要求本项目的 token 包。
但该场景锁定忽略模型 EOS、执行发布输出长度；换 HTTP 入口并不能恢复
源 child 的语义终止信号，也不能用计划中的最后一次调用作为在线信号。
需另行记录真实可观察的完成证据，不能通过改变 EOS 停止条件改变共同工作量。

验证保留先失败后通过的证据：

- `experiments/reports/agentx_inferred_join_before_fix_cpu_20261025.xml`：
  11 个失败复现，覆盖错误停放、备份、资格撤销和控制事件外泄。
- `experiments/reports/agentx_join_provenance_shared_tokenizer_cpu_20261025.xml`：
  使用本地 Qwen3-Coder tokenizer 的 921 项 CPU 检查全部通过，无跳过。
  包含私有 JOIN 仍阻止过早提交 parent 后继、普通/保留/workflow 活跃资格、
  KVFlow 距离、TokenCake 分组、真实等待正向路径及完整事件收尾核对。
- `experiments/reports/agentx_join_provenance_audit_20261025.json`：
  固定旧计划的 37 个 JOIN / 111 个事件和源码 SHA256 审计。

以上不代表修正路径已有新的 GPU 性能证据，也不替代 Full sessionless
迁移的物理接线与验证。

后续开发已新增默认关闭的 sessionless Full 原生前缀候选，接通资格、
增量备份、ACK 后卸载及真实 JOIN/EOS 恢复。当前 961 项共用 CPU 回归
通过，真实 GPU 组件和模型运行仍待验证；原冻结实验没有改动。设计和
能力限制见 `docs/design/full_sessionless_prefix_migration_2026-10-25.md`，
不把这项进展改写为已经完成性能比较。

直接入口：

```bash
python scripts/run_causal_gpu_replay.py \
  --agentx-prepared experiments/prepared/agentx_whole18_lifetime_20261025 \
  --agentx-token-dir experiments/prepared/agentx_whole18_lifetime_tokens_20261025 \
  --output-dir /path/to/new/baseline/client \
  --base-url http://127.0.0.1:PORT \
  --event-socket /path/to/baseline/runtime.sock
```

入口连接已配置的本地被测引擎，不自行启动 GPU 服务。新的 token/namespace/
停止条件已通过下述有界 GPU 数据通路检查，完整人口与各方法仍需单独验收。Agentix、KVFlow、ThunderAgent、
TokenCake 的适配完整性分别登记，不能把缺失组件以本项目策略代替后当作复现。

`scripts/run_agentx_baseline.py` 提供独立的冻结、启动、监控和收尾入口，
不借用要求自然模型采样的 LoCoBench 注册器。它校验原生实际容量、GPU
进程和策略隔离，完成后核对全部请求 token、事件投递、流式完成和完整
JCT，并等待两次新的空闲遥测快照再回收自有进程。它不将运行完一轮
当作已经证明性能优势。生命周期和启动器相关 CPU 回归共 163 项通过，
报告为 `experiments/reports/agentx_lifetime_and_baseline_operator_cpu_20261025.xml`。

## 2026-10-26 全量 Workflow 画像

本节直接读取同一固定 `062126` 完整发布文件，SHA256 与上文一致，
不使用删掉超长请求的 `-256k` 版本。全量 393 个会话共 98827 次 API
调用，其中 175 个会话带 1697 个显式 subagent marker。此前的 68266 次
属于 `-256k` 版本，不与这里混用。当前实验仍使用原来的 18 个会话，
没有重新选择人口；生命周期准备更新不改变这些请求统计。

一个 workflow 的统计单位是一条已发布 coding session。它可包含 root
的多轮模型调用、工具/客户端间隔、显式 child、其他模型辅助调用和压缩。
这不是固定的“root 一次推理—并行 child—root 一次汇总”三段结构。
会话原始跨度可能含长时间外部活动或用户空闲，不等于当前硬件的 GPU JCT。
全量原始记录跨度 P50 为 10135.18s，含显式 child 的会话 P50 为
19657.54s；不能将这些值当作当前 benchmark 的模型计算耗时。

### 调用步数

一次 step 在本节明确指一次模型 API 调用。root 和 child 的主链由固定
作者分链算法识别，标题/初始化 preamble 另计；工具内部辅助模型请求
不冒充独立模型委派。分位数采用线性插值，所以可出现小数步数。

| 对象 | 数量 | 模型调用步数 P50 | P90 | 最大 |
| --- | ---: | ---: | ---: | ---: |
| 全量 root 主链，去 preamble | 393 | 44 | 151.4 | 764 |
| 仅含显式 child 的 root，去 preamble | 175 | 63 | 239.2 | 764 |
| 全量显式 child 主链 | 1697 | 11 | 40 | 789 |
| 显式 child 条目内全部模型调用，含辅助流 | 1697 | 16 | 47.4 | 789 |
| 当前 18 个 root，去 preamble | 18 | 19.5 | 41.5 | 61 |
| 当前 64 个 child 主链 | 64 | 17.5 | 38.5 | 73 |

全量每会话所有模型调用总数 P50/P90/最大为 86/617.6/4114；
只看含显式 child 的 175 个会话，则为 233/1094.4/4114。
当前 18 个会话为 110.5/230.9/287。当前受共同上下文和完整并行分组
条件约束的人口，其 root 主链通常比全量多 agent 会话短，不能称为
对全部 393 个会话的等比例随机抽样。

### Spawn 轮次与每轮人数

源文件没有独立的 `spawn_round_id`。为与现有回放对齐，主口径把显式
child marker 归到其前面最近的保留 root 主链请求；相同前驱是一组。
这个创建分组与按“前驱/后继”推断的 JOIN 组、以及合并后的 JOIN 屏障
是三种对象。

在 175 个含 child 的会话中，root 前驱口径共得到 438 个创建分组：

| 统计 | P50 | P90 | 最大 |
| --- | ---: | ---: | ---: |
| 每个含 child 会话的创建分组数 | 2 | 5 | 13 |
| 每会话显式 child 总数 | 4 | 21.6 | 153 |
| 每创建分组 child 数 | 2 | 6 | 96 |
| 每会话按记录生命周期计算的同时存活 child 峰值 | 3 | 6 | 10 |

175 个会话中，78 个只有一个 root 前驱创建组，97 个有至少两组。
438 组中有 270 组含两个以上 child，263 组具有记录生命周期上的重叠。
这种并发是 child 存活时间的重叠，不等于同时获得 GPU 服务。

极端的 96 不能读作“一次模型输出同时 spawn96 个 child”：该组第一次
与最后一次创建相隔 286947.70s，峰值仅 6；这是主链归并、辅助流及捕获
结构共同形成的大分组。若按每个原始顶层 API 前驱细分，得到 638 组，
每会话组数 P50/P90/最大为 2/8/66，每组 child 数为 2/4/80；
其 80-child 组的创建也分散在 1677.06s 内。两种口径都不是未经推断的
原模型原子工具调用批次，不用极端累计分组数声称瞬时 fanout。

当前 18 个会话两种口径均得到 28 个创建组：

- 9 个会话 1 组、8 个会话 2 组、1 个会话 3 组。
- 每组 1/2/3/4 个 child 的次数分别为 6/9/12/1。
- 22 组有至少两个 child，均有记录生命周期上的重叠。
- 64 个显式 child、20 个多 child marker 前驱/后继组、18 个合并后的
  多显式 child JOIN 屏障。不能用适配图的 270 个 spawn 代替真实 child 数。

一个实际例子是 `a26302776b9ed6524bac18bdf9a9430f4170`：root 主链
有 61 次调用。第 39 次后创建两个 child，主链分别运行 34、40 步；
第 40 次后再创建四个 child，分别运行 26、21、32、2 步。两组对应
相同的后续 parent 汇合请求，形成六成员屏障；按记录生命周期同时
存活的峰值为 5。这说明“两个创建轮次”和“一个六成员 JOIN”可以同时成立。

### 嵌套层次与持久性

显式 schema 表达 root 与 subagent 条目；subagent 的 requests 只有
普通/流式 API 请求，没有递归 subagent marker，也没有可验证真实嵌套
深度所需的原始 parent-agent 身份链。全量显式递归 marker 数为 0，
不能据此说原始应用的真实嵌套数为 0。

作者还对 child 内部扁平请求做上下文分链。本次 727/1697 个 child
含额外推断流，总计 6331 条，其中 6071 条按作者规则被归为辅助流，
另有 260 条其他推断流。它们说明 child 内部的调用结构也较复杂，
但不能把 6331 条都称作嵌套 subagent。当前 64 个 child 中有 16 个
出现额外流，共 177 条，其中 174 条辅助、3 条其他推断流。

临时/持久生命周期没有公开标注：

- 1697 个显式条目均为 `status=completed`、`subagent_type=Subagent`。
- 会话内重复显式 agent_id 数为 0，未提供可计数的 resume/唤醒事件。
- completed 只是该条记录完成，不是原上下文已经销毁的证据。
- 多步持续执行与返回后复用是两种不同的持久性；本数据无法准确分组
  “临时 child 有多少、返回后仍可复用的持久 child 有多少”。

可直接给出的生命周期分布是：

| child 记录存活时间 | 数量 |
| --- | ---: |
| 不超过 30s | 167 |
| 30--120s | 578 |
| 2--10min | 745 |
| 10--60min | 183 |
| 超过 1h | 24 |

child 存活 P50/P90/P99 为 136.14/723.02/5271.86s，最大
85251.87s；当前 64 个为 235.14/506.32/1759.16s，最大 1854.24s。
全量只有 4 个 child 主链为单次模型调用，但多步执行仍不能当作返回后
复用的证明。9 个条目的记录 API 结束超过声明 terminal，明细已标注；
当前选用的 64 个没有这种时序问题，没有改动原始时间。

### 请求间隔

以下为原始源轨迹、同一识别主链内的逐请求对分布，不是当前 GPU
运行结果，也不是每个 workflow 的等权平均：

| 口径 | 样本数 | P50 | P90 | P99 |
| --- | ---: | ---: | ---: | ---: |
| root 请求开始到下一次开始 | 28516 | 16.50s | 175.22s | 3091.26s |
| root 请求结束到下一次开始 | 28516 | 4.51s | 152.47s | 3079.06s |
| root 再剔除可识别的显式 child JOIN 覆盖 | 28516 | 4.40s | 126.44s | 2976.95s |
| child 请求开始到下一次开始 | 32105 | 7.63s | 26.25s | 120.80s |
| child 请求结束到下一次开始 | 32105 | 1.93s | 14.79s | 92.20s |

结束到开始的 gap 通过
`max(0, next_start - previous_start - previous_recorded_api_time)` 计算。
root 的第三行额外扣掉前驱之外仍覆盖该段的显式 child 等待；
仍可能含辅助调用、工具、用户输入和客户端时间，不能称为纯工具耗时。
各行的分位数不能相加。

root 结束后 gap 的 70.79% 不超过 10s，84.32% 不超过 60s；
child 分别为 86.86%、98.31%。最大 root gap 为 355531.76s，
child 为 12570.87s。均值受少数小时级间隔影响很大，不适合单独代表
典型工具等待。当前 18 个会话的 root gap P50/P90 为 2.81/233.48s，
child 为 1.80/10.64s。

原录制 API 耗时另列：root P50/P90 为 7.72/30.96s，
child 为 4.43/14.37s。它们是源端 API 墙钟，不是当前模型 GPU kernel 时间。
当前 benchmark 的 300/10 秒规则只推进整树或系统纯空闲计时器，
不把以上每个 gap 独立截到 300s；这些原始分布不能直接当成运行时
压缩后的实际等待分布。

### 工具调用轮次与耗时能回答到哪里

全量 98827 个请求没有非空 `stop`、`input_types` 或 `output_types`；
1697 个 child 的 `tool_use_count` 均为空。`think_time` 出现于 98434 个
请求，描述客户端间隔，并不是带身份、开始、结束的逐工具遥测。

因此不能从公开版本准确数出 root/child 的真实工具调用轮数、每轮并行
工具数或逐工具时长分布。缺失值不等于工具调用为零。上述 28516 个
root gap 和 32105 个 child gap 可用来表征服务外间隔，但不冒充
60621 轮真实工具调用；一次间隔可以含零个、一个或多个工具。

当前 18 个会话的适配图含 1932 对 `tool_start`/`tool_end`，它们由正的
未标注请求间隔编译为匿名外部等待阶段，不是源文件记录的 1932 次真实
工具调用，不能据此训练具有工具身份和逐工具真值的预测头。

完整数据、脚本与明细：

- `../../../ops/agentx_workflow_profile_20261026.json`
- `../../../ops/agentx_workflow_profile_20261026_workflows.csv`
- `../../../ops/agentx_workflow_profile_20261026_children.csv`
- `../../../ops/agentx_workflow_profile_20261026_rounds.csv`
- `../../../ops/profile_agentx_workflows_20261026.py`

分析使用固定作者 helper，统计主链与当前 18 会话准备中的请求引用逐一
匹配。全量源文件流式 SHA256 校验通过，不启动 GPU、不修改源轨迹和策略。
补充核对确认 `runtime_idle` 与当前 `lifetime` 准备的 18 个源会话身份、
全部 agent 调用字段和请求引用一致。画像 JSON 保留其生成时的 manifest
与准备文件 SHA256；生命周期更新后的文件哈希不同，不以旧哈希冒充当前
准备文件的身份。

### 本轮 18 个 Workflow 的独立画像

本节只统计当前固定的 18 个会话、2229 次模型请求和 64 个显式 child。
下列时间来自发布轨迹，不是当前 GPU 实验的实测时间；请求间隔按同一
主链的相邻请求对汇总。18 个会话全部包含显式并行 child，但创建组只有
28 个：9 个会话一组、8 个两组、1 个三组。每组人数 1/2/3/4 分别出现
6/9/12/1 次；每组人数 P50/P90/最大为 2/3/4，每会话 child 数为 3/6/7。
记录生命周期上的同时存活 child 峰值 P50/P90/最大为 3/3.3/5。

| 本轮统计 | 样本数 | P50 | P90 | 最大 |
| --- | ---: | ---: | ---: | ---: |
| 每会话全部模型调用 | 18 | 110.5 | 230.9 | 287 |
| root 主链调用，去 preamble | 18 | 19.5 | 41.5 | 61 |
| child 主链调用 | 64 | 17.5 | 38.5 | 73 |
| child 条目内全部调用，含辅助流 | 64 | 23.5 | 44.8 | 82 |

root 主链去 preamble 合计 418 次，child 主链合计 1318 次；2229 次
总请求还包含 preamble 和辅助调用，不能只将主链相加。

| 本轮原录制时间 | 请求/请求对数 | P50 | P90 | P99 |
| --- | ---: | ---: | ---: | ---: |
| root 请求开始到下次开始 | 410 | 20.84s | 270.46s | 2260.02s |
| root 请求结束到下次开始 | 410 | 2.81s | 233.48s | 2257.15s |
| root 结束间隔，再去显式 JOIN 覆盖 | 410 | 2.56s | 182.44s | 2234.91s |
| child 请求开始到下次开始 | 1254 | 6.06s | 24.15s | 95.30s |
| child 请求结束到下次开始 | 1254 | 1.80s | 10.64s | 61.77s |
| root 单次 API 录制耗时 | 428 | 9.64s | 44.18s | 141.01s |
| child 单次 API 录制耗时 | 1318 | 3.43s | 14.01s | 74.78s |

64 个 child 的记录存活时间 P50/P90/P99/最大为
235.14/506.32/1759.16/1854.24s；不超过 30s 的 2 个、30--120s 的
14 个、2--10min 的 43 个、10--60min 的 5 个，没有超过一小时的。
这不提供返回后 resume 的生命周期证据：64 个都是未知生命周期。
16 个 child 内出现 177 条额外推断流，其中 174 条为辅助流；没有显式
递归 marker，真实嵌套深度不可确认。

每请求输入长度 P50/P90/最大为 75904/162470.4/260288 token，输出长度
为 363/2213/26014 token；全体输入/输出量为 189039040/2114833 token。
长输入包含复用前缀，累计输入量不能直接当作新增 prefill 工作量。

逐会话明细如下。ID 使用唯一的前八位；“每组 child 数”按创建顺序列出，
“child 步数”按源条目顺序列出主链模型调用数，root 步数去 preamble。
存活峰值不代表同时获得 GPU 服务。最后一列为原录制 root 结束间隔。

| Workflow ID | 全部请求 | root 步数 | 每组 child 数 | child 步数 | child 存活峰值 | root gap P50/P90(s) |
| --- | ---: | ---: | --- | --- | ---: | --- |
| 00ca01c4 | 35 | 28 | 3 | 2,2,2 | 3 | 2.82/285.05 |
| 07dd4053 | 119 | 9 | 3 | 31,62,16 | 3 | 0.83/107.36 |
| 085411a4 | 102 | 19 | 3 | 29,35,18 | 3 | 28.34/270.38 |
| 0bc9c4f7 | 102 | 16 | 3 | 30,40,14 | 3 | 25.63/351.66 |
| 1499c406 | 140 | 11 | 1+3 | 16,24,33,6 | 3 | 1.55/66.92 |
| 3e573575 | 205 | 20 | 3+1+3 | 35,16,18,3,47,47,18 | 3 | 21.32/1412.95 |
| 4cb074e3 | 57 | 4 | 2 | 25,12 | 2 | 98.35/289.39 |
| 65e68541 | 218 | 52 | 3+2 | 8,7,8,9,15 | 3 | 2.78/32.26 |
| 94fa70f6 | 80 | 36 | 2 | 11,8 | 2 | 2.59/158.47 |
| 9a7043c3 | 58 | 12 | 2 | 22,23 | 2 | 23.85/227.64 |
| 9c5d5610 | 125 | 37 | 2+1 | 33,11,14 | 2 | 5.97/392.72 |
| a2630277 | 261 | 61 | 2+4 | 34,40,26,21,32,2 | 5 | 2.12/7.04 |
| ac8484a3 | 142 | 21 | 2 | 26,34 | 2 | 2.15/97.47 |
| c36a6ae1 | 73 | 15 | 2+1 | 17,13,27 | 2 | 72.14/414.84 |
| cbbac1ac | 130 | 34 | 3+1 | 25,23,16,28 | 4 | 1.74/61.83 |
| d4f0aae9 | 50 | 13 | 3+3 | 8,6,4,1,7,8 | 3 | 1.85/464.53 |
| eb48065a | 45 | 22 | 2 | 2,3 | 2 | 8.74/175.60 |
| ecd3ea07 | 287 | 8 | 3+1 | 25,54,13,73 | 3 | 2.93/174.81 |

本轮工具调用真值仍不可得；1932 对匿名外部等待事件不是已识别的真实
工具次数。当前配置保留原始间隔并应用整树 300s、全系统 10s 空闲推进，
不按请求间隔逐个截断。静态源时间、计时器推进后的回放时间、GPU 实测
排队与执行时间分别报告。

## 指标组合

Interactivity 有不同口径，显式同时报告生成速率和包含排队的 E2E 口径：

| 指标 | 定义 | 作用 |
| --- | --- | --- |
| request decode interactivity | `(Nout - 1) / (tlast - tfirst)` | 生成期间的输出连续性 |
| request E2E interactivity | `Nout / (tcomplete - tsubmit)` | 纳入本地准入、排队、恢复及首 token 等待 |
| E2E normalized interactivity p90 | `1 / Q90((tcomplete - tsubmit) / Nout)` | 先算秒/token 的慢尾分位数再取倒数 |
| workflow TTFT | `min(tfirst_output across descendants) - tworkflow_start` | 整个 workflow 最初获得可见服务的等待 |
| workflow ready output service gap | 任一后代有已提交未完成模型请求时，到下一次输出的间隔 | 观察首次输出之后的饥饿与碎片化 |
| complete workflow JCT | `tworkflow_end - tworkflow_start` | 端到端完成目标，包含正常长尾 |
| full-run output throughput | 全部输出 token / 从首个 workflow 释放到最后完成的时间 | 防止以服务公平为名牺牲整体效率 |

NVIDIA GenAI-Perf 的 TPOT 与 AIPerf 的 E2E normalized interactivity 是
相关但不同的定义，不能混用。“p90 interactivity”尤其要写清是慢尾
延迟/token 的倒数，还是直接对速率取快尾分位数。

TTFT 使用首个非空流式输出，不用 request launch 或 first worker launch
替代。流式合并 token 的 chunk 单列，补充扣除首 chunk token 数的速率；
单 token 或全部合并在一个 chunk 的请求不报无限大 decode interactivity。
E2E 口径仍可用于这些有正时长/输出的请求。

workflow ready gap 从 LLM_SUBMIT 开始，覆盖本地调度、加载 token、服务端
排队及 KV 恢复。若所有分支都在真实外部等待，没有模型请求就绪，则不算
服务饥饿。该指标按客户端流式输出观测，不冒充 CUDA kernel 服务间隔。

同时报告所有请求的分布和每个 workflow 等权的聚合。fanout 高的 workflow
不能只因为产生更多请求而在 workflow 公平性评价中获得更大权重。保留各
workflow 的 JCT、TTFT、最大 ready gap 及直方图；未只看已开始执行的请求。
普通首次服务、TTFT 和稳态生成速率任一项都不能单独证明公平或 JCT 收益。

控制路径只利用已经解析的流式 chunk 更新计数、首末时间和固定大小直方图，
不新增逐 decode 步扫描，不逐 token 写文件。请求结束后只记录一条汇总。

## AgentX 系统瓶颈与 Baseline 压力分析

本节回应计算、HBM 容量/带宽、迁移和调度瓶颈，不把工具环境或数据
可接入性当成系统瓶颈。以下统计来自当前固定 18 个完整会话的离线复核；
尚无各 baseline 在这组人口上的完整 GPU profile，不能称为实测性能排序。
不改 workload、空闲规则、模型映射或比较顺序，不启动新 GPU 实验。

### 已量化的需求

| 数量 | 当前固定人口 |
| --- | ---: |
| root / 显式 child / 辅助流 / 其他推断流 | 18 / 64 / 191 / 15 |
| root 请求输入 P50 | 107936 token |
| 显式 child 请求输入 P50 | 62240 token |
| 全部请求输出 P50 / P90 / 最大 | 363 / 2213 / 26014 token |
| 多显式 child 屏障的成员数 | 8 组为 2，8 组为 3，2 组为 6 |
| 同组 sibling 总输出量最大/最小比 P50 / P90 | 1.79 / 3.32 |
| parent 在多 child JOIN 前后输入共同前缀 P50 / P90 | 87168 / 154169.6 token |
| parent 与 child 首次输入共同前缀 P50 / 最大 | 0 / 768 token |
| 使用多个发布模型标识的 workflow | 16 / 18 |
| 与创建它的 parent 请求模型标识不同的显式 child | 41 / 64 |
| sibling 首次模型标识不同的多 child 屏障 | 0 / 18 |

两种共同前缀不可混淆：parent 自身 JOIN 前后的可复用历史很大；
parent 和新 child 之间的初始共享前缀很小。不能据此否定同一 child
后续多轮的前缀复用，也不能把共享小直接解释为某缓存算法无效。

按当前共同 Qwen3-Coder BF16 FULL 几何，48 层、4 个 KV head、
head_dim128、K/V 各一份，每 token 为 98304 bytes。root 和 child
输入中位数对应的逻辑 KV 为 10.61GB、6.12GB；多 child JOIN 的
parent 共同输入前缀中位数为 8.57GB，P90 为 15.16GB。
这些是逻辑需求，不是实测物理占用；不能把不同请求的中位数相加当作
并发峰值，也不能据此前缀大小认定已经有 Host-only 副本或可隐藏 H2D。

20 个 marker 分组并不等于 20 个不同的汇合时刻。两个会话各有两组 marker
指向相同的 parent 后继，编译后各合并成一个屏障，得到 18 个多显式 child
屏障。完整适配图的 37 个 JOIN 另外还包含单 child、辅助及推断流的依赖。
270 个 spawn 中只有 64 个对应显式 child，其余 206 个是辅助/推断流。

发布模型标识只能说明轨迹中的角色调用分布；当前物理回放统一映射到一个
Qwen，因此多模型切换瓶颈属于后续多模型配置下的条件推导。不能把下面
权重换入换出问题当作当前单模型 GPU 实验中已经发生的现象。

### 1. 活跃 KV 集合限制可并行执行量

多轮私有上下文增长时，程序可执行数和 HBM 实际容纳数会分离。保持多个
等待 parent 的大历史，可以减少返回时的恢复，却压缩 child 的可用
batch；优先腾空 parent，则要在 JOIN 后恢复它。parent-child 初始共享
很小，使这类私有状态容量竞争比仅共享大 system prompt 的负载更突出。

Agentix 的服务公平、ThunderAgent 的工作集连续性、TokenCake 的预留和
KVFlow 的未来使用顺序，都需要面对同一容量约束。提高命中或准入数量
并非独立目标：需要检查活跃 batch、重复恢复、已服务前缀重算和 JCT。
Prism/kvcached 可减少模型间静态预留浪费，但不能增加物理 HBM 总容量。

### 2. 长上下文 decode 带宽与缺页 prefill 的相互干扰

每步 attention 访问较长历史；多模型共置还会同时竞争权重、KV 读取及
算力。若负载受 HBM 带宽限制，增加模型并发并不保证增加有效 token 吞吐。
MuxServe 的 prefill/decode 复用仍有价值，实际收益取决于两类 kernel
是否有可用的互补资源，不能只看并发模型数。

一旦长历史丢失，额外 prefill 又会挤占正在推进的 decode。应分开统计
首次必要 prefill、新增输入和之前已服务历史的额外重算。若保留 Host
可避免重算，即使 H2D 本体占时小，状态管理仍可能有较大收益；
若恢复和原生保留已经很好，则不能预设预取会有明显端到端收益。

### 3. JOIN 的尾部 child 与 parent 恢复需要连续推进

同组 sibling 的总输出工作量存在差异，但总输出比不是在线剩余工作估计。
parent 只能在所需成员都返回后继续；加速已不再阻塞 JOIN 的分支不一定
缩短其解锁时间。最后未完成成员的排队、恢复和服务间隙因此值得单列。

JOIN 解锁也不等于 parent 已可执行：parent 请求还须提交，首步数据依赖
须就绪，且须获得 GPU 准入。8.57GB 中位历史带来显著容量需求；
其实际关键路径恢复耗时需要测量，不能按全部 bytes/带宽直接加进 JCT。
有效的预取应减少暴露恢复或避免重算，同时不挤慢仍在运行的 child。

Prism/MuxServe/Aegaeon 的模型资源/SLO 优化不自动等价于整树 JCT 优化；
Talaria 已有会话完成与续接保护，仍需检验多个 child 会话与 parent 的
协调。Agentix/ATLAS 已面向动态程序依赖，是这里必须保留的强对照。

### 4. 公平推进与 KV 局部性的取舍

Agentix 已有程序服务继承、MLFQ、量子、抢占、防饥饿及聚合 swap，
不能先断言它完全忽略迁移或一定频繁颠簸。可检验的问题是：在本组
较大私有上下文下，缩短等待所得的收益能否覆盖状态切换和额外 prefill。

ThunderAgent 保持工作集可减少交换，但需要测新 root 首次推进、并发
child 入场和已解锁 parent 的服务长尾。保留阻塞 parent 的逻辑预算
与释放物理页是不同决策。它在其他人口上的长等待不能直接外推到 AgentX。

191 个辅助流只有 191 个请求，输入中位数 2432 token。把流数量等同于
workflow 剩余工作会严重失真。各 child 和辅助流应在同一个 root 的
公平账户内评测，而不是因 fanout 获得更多程序服务权。

### 5. 多模型配置下的权重与 KV 迁移竞争

当前数据更接近 A-parent 生成一批同模型 B-child，然后回到 A，
而不是一个 JOIN 内每个 sibling 都访问不同专家。多个 workflow 的
A/B 阶段交错后，仍可能同时产生两个模型的服务和容量需求。

若权重均常驻，主要矛盾是两个模型的 KV 份额和服务机会如何调整；
若权重也需换出，则 PCIe 上同时出现权重加载、parent 恢复和备份。
JointServe 已明确做这类联合驻留、流式加载和传输协调，不能称其
忽略该问题。其在 AgentX 上需要实测的瓶颈是：预取能否及时被消费，
恢复 A 时是否延迟 B 的尾部成员，以及保留 A 是否减小 B 的有效 batch。

模型 B 释放的显存若留在独立静态 pool 中，A 无法使用；Prism 的弹性
物理池就是必要的强参照。不同模型共享物理容量不等于共享 KV 内容。
即使正确预测下一模型，物理容量和带宽不足也不会因预测正确而消失。

### Baseline 对应的主要系统压力

| 方法 | 本组应重点量化的瓶颈或取舍，不是已证实缺陷 |
| --- | --- |
| Agentix/ATLAS | 程序级排队下降能否覆盖长 KV 抢占/恢复代价，整树服务是否连续 |
| ThunderAgent | 小工作集的连续性与新 root、child、JOIN parent 等待之间的取舍 |
| KVFlow | 多个近未来 consumer 同时竞争容量；使用距离能否减少实际暴露恢复/重算 |
| TokenCake | 预测窗口、空间预留与真实消费时刻错位，提前驻留是否挤压 child batch |
| Prism/kvcached | 消除预留浪费之后，仍受实际活跃 KV 总量与 HBM 读取带宽限制 |
| MuxServe | 多模型并发的带宽/算力互补性，按模型聚合服务与整树尾部的取舍 |
| Aegaeon | token 级模型切换成本和局部性，在本组会话长前缀上的真实摊销 |
| JointServe | 权重预取与 KV 恢复的共享带宽，以及 B-child 收尾到 A-parent 的切换 |
| Talaria | 已有续接/会话完成保护对多 child 整树的覆盖，恢复保护的容量代价 |
| Pythia | 预测/预热能否形成有用的物理就绪，是否存在预热后长时间不被服务 |

Prism/MuxServe 的目标差异不证明其在此组必然更慢；Pythia、TokenCake、
JointServe 和 Talaria 都已有预热或续接协调机制。不能通过只比较缺失
组件的移植版来制造优势。各方法的函数级控制成本也须真实计时，而不能
以检查次数或服务区间剩余量替代。

### 需要真实 Profile 回答的问题

完整固定人口对照应分别观察 GPU kernel 的 HBM/计算瓶颈，必要与额外
prefill，缓存恢复真正暴露的等待，最后 child 的 ready 服务间隙，
JOIN 解锁到 parent 首 token，以及预取 ACK 到首次消费的时间与 idle
byte-seconds。两模型情况下再按方向拆权重/KV 传输及模型切换频率。

这些指标解释 JCT、TTFT 和吞吐，不能替代它们。只提升 GPU 利用率、
只增加 H2D 或只减小请求等待，都不能证明整个 workflow 更快。
本次仅确认需求结构与候选瓶颈，没有产生 AgentX baseline 性能排名。

匿名内容会限制基于原始语义的预测器复现，这同样适用于本项目的内容头
和 announce 机制；它不影响固定发布轨迹的系统 JCT 比较。不得让任何
方法提前读取驱动中的剩余长度、真实结束时间或未来 DAG 以伪造预测收益。

原始论文范围：Agentix NSDI26；ThunderAgent arXiv:2602.13692；
KVFlow arXiv:2507.07400；TokenCake arXiv:2510.18586v5；
Prism OSDI26；MuxServe ICML24；Aegaeon SOSP25；
JointServe arXiv:2610.19822；Talaria arXiv:2607.17181；
Pythia arXiv:2604.25899。详细结构统计及输入 SHA256：
`../../../ops/agentx_baseline_fit_20261025.json`；
分析器：`../../../ops/analyze_agentx_baseline_fit_20261025.py`。

## 本轮验证范围

实现涉及：

- `beliefkv/experiments/replay_timing.py`：原始依赖不变的会话/全局 idle guard。
- `beliefkv/experiments/agentx_replay.py`：完整发布源加载与校验。
- `beliefkv/experiments/agentx_tokens.py`：离线 token 包与只读随机访问。
- `beliefkv/experiments/workflow_metrics.py`：流式 TTFT、interactivity 与 root 共享服务间隔。
- `scripts/run_causal_gpu_replay.py`：直接发布源入口，不依赖 source-arm/native capture。

小规模端到端测试使用 HTTP mock 和原生 receipt 格式，不能当作实际 GPU
性能。完整人口 CPU 复现保留原始 delay 值，仅将 fixture 的 runtime idle cap
设为 0.001ms，以验证全部调用、JOIN、后台返回、streaming 账户与正常完成。
正式配置仍为 300/10 秒。CPU 通过不代表四个 baseline 已运行或性能验收通过。

真实 GPU 校验 v217b 发现空的 `stop_token_ids=[]` 会在原生 SamplingParams
中规范化为 `None`，使适配器的严格回执检查失败。现已让 AgentX 适配器
直接使用原生的无停止 token 表示，并使用实际 SamplingParams 验证其终止
回执；没有放宽 token、长度、采样器或 finish reason 检查。v217b 的失败
记录和清理证据保留，修复后的相关 82 项 CPU 检查通过：
`experiments/reports/agentx_native_stopping_fix_cpu_retry_20261025.xml`。

修复后的 v217c 在真实 H200 上通过 15 个原生探针，每个使用发布源衍生的
72,704 输入 token 和 2 输出 token：同上下文重复命中 72,703 个 HBM token，
相同 token 换 workflow namespace 后 HBM/Host 命中均为零；11 个额外压力
namespace 填充后，原上下文从 Host 恢复 72,703 个 token，原生 H2D ACK
数量相同。15 条均验证实际 token、停止条件、原生 sampler 和流式输出时序。
进程归属、实际 GPU 执行、两次新鲜 idle 遥测和进程清理均通过。
这不是另一条源轨迹或性能实验，探针中的 JCT 不能作为完整人口结果。
报告：`experiments/reports/agentx_bounded_gpu_data_plane_20261025.json`。

针对本次“默认不设置”的疑问另增一项时序检查，证明未设置会话树 cap 时，
其他请求在途期间不修改外部间隔，只有全系统空闲才按 10 秒预算推进。
相关 15 项时序检查通过：
`experiments/reports/agentx_idle_semantics_cpu_20261025.xml`。

继承 CPU 回归：6,567 项及 2 个 subtest 通过，0 failure/error/skip，原有
6,536 个独立 case 全部保留。完整人口通过真实因果状态机与 streaming
账户检查，2,229 次调用、288 个 invocation、37 个 JOIN、18 个 workflow
全部完成，没有同 agent 重叠。fixture 约用 0.428 CPU 秒；这是纯 CPU
结构检查，不是 GPU 开销或性能提升的估计。

登记与报告：

- `configs/workloads/agentx_whole18_20261025.json`
- `experiments/reports/agentx_runtime_idle_application_cpu_20261025.json`
- `experiments/reports/agentx_runtime_idle_application_cpu_20261025.xml`

## 原始资料

- https://inferencex.semianalysis.com/agentx/methodology
- https://github.com/SemiAnalysisAI/agentx-harness
- https://raw.githubusercontent.com/SemiAnalysisAI/agentx-harness/e4bb2f835ffedbbe6b76252ea5c93caec68c5faf/src/aiperf/common/scenario/inferencex_agentx_mvp.py
- https://raw.githubusercontent.com/SemiAnalysisAI/agentx-harness/e4bb2f835ffedbbe6b76252ea5c93caec68c5faf/src/aiperf/timing/replay_dependencies.py
- https://raw.githubusercontent.com/SemiAnalysisAI/agentx-harness/e4bb2f835ffedbbe6b76252ea5c93caec68c5faf/src/aiperf/timing/strategies/agentic_replay.py
- https://docs.nvidia.com/aiperf/dev/reference/ai-perf-metrics-reference
- https://docs.nvidia.com/nemo/megatron-bridge/0.1.0/deployment/genai-perf.html
