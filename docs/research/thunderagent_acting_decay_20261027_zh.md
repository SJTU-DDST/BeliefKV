# ThunderAgent 可选 ACTING 衰减对照

V286 已完整执行冻结的作者默认 ThunderAgent 和 JOIN-dependent
Full，V295 随后独立完成 acting-token decay 对照。默认对照未开启
作者可选的 acting-token decay。为了判断
长工具下的收益，必须另行比较该公开选项，不能只超过默认选项便宣称
超过 ThunderAgent 的所有相关配置。新 arm 为
`thunderagent_acting_decay`，没有修改 V286。

## 最新完整结果

V295 于北京时间 2026-10-27 17:37:57 完整结束，exit code 为零，
监督状态为 `collected_performance_review_pending`。全部 18 个 workflow、
3674 次请求、145565966 输入 token 和 549671 输出 token 完成。
逐请求 prompt/output SHA256、token 数和原生结束原因与 V286 Full
相等。原始停止规则、全部正常等待与尾部均保留；两个新鲜 idle
telemetry 快照、零丢失/错误和 owned cleanup 均验证。

三组采用同一 V269 网络工具计时表、流式指标、engine tree、容量、
seed21、running/decode24 和 3600 秒客户端超时。它们属于同工作量
合成网络工具时长比较，不与自然 shell 时长实验混用。

| 系统指标 | V286 默认 ThunderAgent | V286 Full | V295 ACTING decay |
| --- | ---: | ---: | ---: |
| 平均 workflow JCT，秒 | 1876.335 | 1669.294 | 1700.062 |
| P50 workflow JCT，秒 | 1649.153 | 1669.635 | 1719.385 |
| P95 workflow JCT，秒 | 2937.667 | 2442.497 | 2292.546 |
| workflow event makespan，秒 | 3138.105 | 2903.095 | 2324.678 |
| 全程输出吞吐，token/s | 175.160 | 189.340 | 236.450 |
| 平均 decode batch | 2.280 | 3.143 | 5.023 |
| worker 区间加权 decode batch | 2.861 | 4.295 | 7.526 |
| H2D，十进制 GB | 291.851 | 698.516 | 2440.873 |
| D2H，十进制 GB | 445.433 | 258.999 | 2033.621 |

V295 相对默认策略平均 JCT 降低 9.3945%，吞吐提高 34.9910%，P95
JCT 降低 21.9603%。11/18 个 workflow 更快，七个更慢，不能将
平均值改善当作所有任务均受益。

相对 V286 Full，V295 平均 JCT 高 1.8432%，但吞吐高 24.8816%，
完整完成时间低 19.9241%，P95 JCT 低 6.1392%。Full 平均 JCT 仍低
1.8098%，但吞吐低 19.9241%，未达到相对此可选配置的共同收益目标。
V295 的七个 workflow 比 Full 更快，另十一个更慢，尾部与平均值的
排序不同。必须同时保留这三组结果，不能只用默认对照支持长工具优势。

### 并发与迁移的取舍

实际 runtime 为 `scheduler_only`，Full agent admission、预测器、
prepare_host、工具预取、JOIN 优先和 resident-first 均关闭。ACTING
decay 只用于周期性恢复的逻辑容量选择，原始容量检查和原生物理
分配权限不变。最终没有 1800 秒 force-resume 记录；默认 V286 有
两次。program resumed 累计从默认 204 次增加到 V295 的 1576 次，
不是独立 agent 数或物理预取次数。

V295 平均 batch 从默认的 2.280 增至 5.023，decode 迭代从 241040
次减少至 109397 次。相同输出工作量的 decode worker 区间累计为
1647.692 秒，默认为 2124.328 秒、Full 为 1876.615 秒。这些区间
包含 runtime 和等待，不能当作纯 CUDA kernel 时间或直接扣减的 JCT。

代价是更多迁移。V295 全部 2440.873 GB H2D 均为原生需求恢复，
其中工具后继关联 1759.538 GB、JOIN 后继关联 661.238 GB。关联按
请求 Host hit 统计，不将单个工具或逻辑切换直接归因到某条 DMA。
Full 比 V295 少传约 71.38% H2D 和 87.26% D2H，但没有获得更高
全程吞吐，说明当前负载下低迁移本身不足以抵消并发与服务分配代价。

V295 H2D transfer-stream 累计 77.270 秒、D2H 为 46.650 秒；Full
分别为 21.635 秒和 6.904 秒。这些是累计传输时间，不是独立可加、
可从 makespan 扣除的延迟。较大的迁移字节不等于同量增加的端到端
阻塞，不能据此假定减少迁移一定改善吞吐。

V295 workflow 初始 TTFT P90 为 246.463 秒，默认为 1843.843 秒、
Full 为 683.235 秒。该指标含 workflow 初始准备，首输出可来自
任一 descendant。单请求生成交互速率 P50 为 35.114 token/s，低于
Full 的 61.305 和默认的 88.706，表明更大并发也改变个体响应速度。
三组旧的同上下文已服务输入前缀丢失代理量均为零，不能据此宣称不存在
任意额外 prefill，也没有大量避免重算的证据。

### 完整证据

- `/home/longhao/experiment/ops/compare_v295_complete_20261027.json`
- `/home/longhao/experiment/ops/v295_complete_decode_20261027.json`
- `/home/longhao/experiment/ops/v295_complete_restore_churn_20261027.json`

汇总绑定实际输入 hash、完整同工作量校验、运行时策略、共同参数、
计时表和收尾证据。这是每策略一次完整运行，重复性与最终性能验收
仍待验证，不改写 `performance_accepted=false`。本次仅离线分析和
文档更新，没有改变冻结策略或启动新 GPU 实验。

## 作者策略与适配

固定公开 commit 为 `7ddc8610270e56d3b109eed8796b3a4360fc67c9`。
对照实际读取并执行该 commit 的：

- `ThunderAgent/backend/state.py::BackendState.remaining_capacity_with_decay`
- `ThunderAgent/scheduler/router.py::MultiBackendRouter._scheduled_check`
- 同一 router 的 `_greedy_resume` 和 `_pause_until_safe`
- `ThunderAgent/program/state.py` 的实际 Program、状态与 ACTING 时间字段

作者默认关闭此选项。开启后，只有周期性恢复暂停 program 的选择使用
指数衰减：已 ACTING 的 program 按进入该状态后的秒数乘 `2**(-t)`，
一秒减半。每个 active program 的 100-token buffer 不衰减；恢复后
的超容量处理与首次直接准入仍使用原始占用。公开实现的衰减公式不
乘 configurable tool coefficient，此处也保留这个行为。

例如容量 1000、一个 ACTING program 占 800 加 100 buffer：
原剩余容量是 100；五秒后用于恢复选择的剩余容量是 875，因此可选中
一个需 700 加 100 buffer 的暂停请求。选中后原始容量超额，必须再
暂停 ACTING owner，才能在原始总账中留下 800 的新工作集。不能把两个
owner 持续按折扣算在一起，也不能据此声称已经释放物理 HBM。

适配器使用实际 native 请求完成的 monotonic 时间记录 ACTING 年龄，
下一次实际请求注册即清除该年龄；它不接收工具结束预测、未来 trace
时长、JOIN 或 sibling 优先。一个串行 invocation 继续映射一个 program。
原生页分配、锁、缓存驱逐和需求恢复保持权威。新 arm 不启用 Full
的 prepare_host、工具预测、预取、resident-first 或 agent admission。

声明文件：`configs/baselines/thunderagent_acting_decay_20261027.json`。
运行时 identity 为 `thunderagent_public_single_backend_acting_decay`，
并明确输出一秒 half-life、resume-only、原始容量检查和无物理容量
credit。不同公式或 future-tool-end 元数据无法通过状态校验。

## 已有证据

`scripts/qualify_thunder_acting_decay_author.py` 检查作者 commit 与三个
实际源码文件的 Git 原始字节，然后直接调用作者原方法，没有重写 AST
或替换其选择与暂停算法。只有 metrics 回复、单 backend、program
输入状态和时钟由 CPU 夹具提供，共享 token credit 固定为零。

seed21 的 2000 组状态全部匹配：衰减容量、恢复顺序、全部 program
最终状态、mark-for-pause 与原始容量一致。夹具覆盖 1170 次恢复选择、
11205 次 ACTING 暂停和 9637 个 REASONING 标记。这些是 CPU 合成
状态中的转移数，不是 workload 频率或已减少的迁移量。

证据：`experiments/reports/v289_thunder_acting_decay_author_cpu_20261027.json`。

定向回归通过 120 项。完整 shared CPU 在一次执行中通过 2376 项和
四个 subtest，无失败或跳过，保留 V285 全部 2350 项并新增 26 项：

- `experiments/reports/v288_thunder_acting_decay_targeted_cpu_20261027.xml`
- `experiments/reports/v290_thunder_acting_decay_shared_cpu_20261027.xml`

新检查覆盖完成到 ACTING、短工具、长等待恢复、重返 REASONING
立即恢复全额计费、首次直接准入、原始压力检查、marked 状态不提前
计容量释放、retire、close、错误时钟、独立 runtime factory、真实
prefill 选择接口和禁止 Full 策略混入。原有 native CPU 夹具仍使用
copy/event 替身，不是新的 CUDA 或模型数值资格。

额外比较确认默认 `ThunderProgram`、`ThunderWorkingSet` 的 AST
保持不变，Full 的 `thunder_agent_migration.py`、`agent_working_set.py`
和 `sglang_v0520_runtime.py` 字节与 V286 冻结版本一致。

## GPU 结果范围

V295 实际在 V286 supervisor 完整退出、两个 arm 完成并清理后独立
运行。上面的完整 GPU 结果不将 ACTING 折扣当作物理共享，不比较
自然工具时间的历史 arm，也不删除正常 workflow 尾部。CPU 资格与
单次完整 GPU 比较分别保留；本次结果不触发其他 baseline 的提前
运行，也不等于重复性或预测迁移独立模块收益证明。

## 历史独立排队记录

以下记录描述启动前的等待状态，最新完整结束结果见本文开头。

实现和初次 CPU 证据已提交为 `2d744d18`。从该版本独立冻结后的
application 为 `d6a4ce4c7bc11d6fc64b20edf45bb655907141b1`，
native tree 仍为 `11b84f7c31986782be3fbe699bae2574ef092389`。
准备目录再次一次性通过全部 2376 项及四个 subtest，没有失败或跳过；
原生引擎除 canonical patch 路径外所有字段相同，四个已经 GPU
检查的 sampler/receipt 模块字节也保持一致。

- `experiments/reports/v294_thunder_acting_decay_prepared_shared_cpu_20261027.xml`
- `experiments/reports/v294_thunder_acting_decay_prepared_sources_cpu_20261027.json`
- `experiments/reports/v294_thunder_acting_decay_native_state_cpu_20261027.json`

独立注册：
`/home/longhao/experiment/ops/v295_locobench_network_thunder_decay_20261027.registration.json`。
唯一 arm 是 `thunderagent_acting_decay`，端口 18653，显式依赖 V286。
supervisor 4052961/start ticks 725763196 已独立确认存活，状态为
`waiting_previous_causal_comparison`，没有 server、driver 或子进程。
source plan、所有容量、common environment、V269 map、streaming 和
3600 秒 client timeout 均逐项与 V286 相等，前置进程仍然存活且未满足
释放条件。证明为
`experiments/reports/v296_thunder_acting_decay_waiting_cpu_20261027.json`。

这是有来源约束的 CPU 等待进程，没有开始新的 GPU 实验，也没有
optional-decay 模型或吞吐/JCT 结果。当前 V286 保持原顺序不变。
