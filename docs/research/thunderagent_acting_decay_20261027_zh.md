# ThunderAgent 可选 ACTING 衰减对照

V286 继续执行冻结的作者默认 ThunderAgent，然后运行 JOIN-dependent
Full。当前默认对照未开启作者可选的 acting-token decay。为了判断
长工具下的收益，必须另行比较该公开选项，不能只超过默认选项便宣称
超过 ThunderAgent 的所有相关配置。新 arm 为
`thunderagent_acting_decay`，没有修改 V286。

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

## GPU 对比边界

该 arm 仍无完整 GPU/性能结果。须等待 V286 supervisor 实际退出、
两个 arm 完整完成和自有进程清理后，再在完全相同的 V230 请求/token、
V269 timing map、streaming、seed21、running/decode24、Device/Host
容量与 3600 秒共同客户端期限下独立运行。保留所有正常 workflow
尾部，不比较自然工具时间的历史 arm，也不把 ACTING 折扣当作物理共享。

只有获得相同完整工作量的 JCT 和吞吐，才能判断默认选项与此选项
各自的表现。当前正确性差分和 CPU 回归不能代替这项实验，也不触发
其他论文 baseline 的提前运行。

## 独立排队记录

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
