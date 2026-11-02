# 保留测量组的背景前缀扩展

2026-11-01。本文是已有完整原任务 trace 的 CPU 准备，
不是新的 GPU 运行或 workload 验收。当前共同引擎 Full/ThunderAgent
对比的冻结代码、original56 到达表和测量组继续保持原样。

## 已确认的缺口

原 original56 / initial12 / mean70 完整比较中：

| 方法 | 最后计划到达 | 最后测量完成 | 超出背景提交时段 |
| --- | ---: | ---: | ---: |
| Full | 3259.8586 s | 4415.8519 s | 1155.9933 s |
| ThunderAgent acting-decay | 3259.8586 s | 4753.3683 s | 1493.5097 s |

原 original69 的 ThunderAgent 最后计划到达为 4174.1363 s，
测量完成为 4283.2430 s，仍超出 109.1067 s。
这些都是旧完整报告，不能把当前仍在运行的共同引擎 Full 当成完成结果。

原始证据位于 `experiments/reports/remaining_original_projects_20261030/`：

- `measured_cohort_complete_toolaware_initial12_mean70_control_r1/cohort.json`
- `measured_cohort_complete_thunder_initial12_mean70_r1/cohort.json`
- `measured_cohort_original69_initial12_mean70_thunder_decay_r1/cohort.json`

## 候选 A：69 项追加四项

从已有 81 个完整原项目 inventory 中，保留原 69 个项目，
只从尚未使用的项目中按固有模型工作量选择最小的四个：

| 新增原项目 | 初始上下文 | 模型工作量代理 |
| --- | ---: | ---: |
| python_data_analytics_easy_010 | 35,766 | 15,894.43 |
| python_api_rest_easy_006 | 36,610 | 16,023.78 |
| python_web_ecommerce_expert_000 | 227,150 | 16,240.34 |
| javascript_desktop_productivity_easy_055 | 35,115 | 16,578.19 |

73 个不同原项目的 P80/P20 为 **2.934261**，P90/P10 为 **4.542611**。
它不达到原 3 或 4 的门槛，文件中保留这两个 false。
采用的最低值来自用户已接受的完整 original56 人口：**2.931418**。
没有重新要求 56 项达到旧门槛，也没有把门槛进一步放宽。
工作量代理仍为 `.01 * 必要新增输入 + 实际生成输出`，不是源 JCT。
工具关键路径和实际运行压力仍需后续单独核对。

原任务、原始 workflow 行、项目、自然尾部均保留。
新增项目同时包含短上下文与 227K 长上下文，未将所有新增任务都选成小 KV。
没有用方法胜负、JCT 或完成顺序挑选项目。

准备目录为
`experiments/reports/remaining_original_projects_20261030/useful_token_cost_r1/background_tail_original73_prepare_r1/`：

- `population_plan.json`：原始项目行和新增项目，明确不是可执行 source。
- `arrivals.json`：原 69 个事件的完整不变前缀，加上四个新到达。
- `analysis_cohort.json`：保留原 12 个预热、固定 18 个测量成员和相同吞吐窗口；
  背景人口及其绑定身份随扩展更新。
- `preparation.json`：输入/输出 hash、门槛来源、剩余资格工作和禁止自动 GPU 后继的声明。

## 到达契约与测量保持

`22b3f9b9 feat: preserve fixed workflow arrivals when extending background traffic`
新增显式 `prefix_extended_poisson` 契约：

- 先完整校验原 Poisson/warm/permuted 计划。
- 保留全部原 workflow ID、顺序、到达时间和其原声明。
- 按同一 seed35、平均 70 秒的原随机流继续取样，追加新项目。
- 拒绝重复身份、改写旧时间、换 rate、重排前缀和执行人口缺失。
- 新 cohort 重新绑定完整扩展到达表，但测量成员、共同窗口与完整 JCT 收尾规则不变。

新的末次计划到达为 **4376.4982 s**，比原 69 项延长 **202.3619 s**。
当前候选不会替代正在运行的 original56 比较，也没有注册新的 GPU 任务。
到达校验、旧到达形式和测量组回归共 **156 项通过**，
见 `useful_token_cost_r1/background_tail_extension_cpu_r1.xml`。

## 不能据此宣称解决了压力问题

第一个新增到达为 4291.6632 s，
比旧 69 项 ThunderAgent 的测量完成 4283.2430 s 还晚约 **8.4202 s**。
按那个旧完成时点，四个新增项目都不会实际提交。
因此，这个候选延长了已声明的开放到达过程，
**不能据此声称它增加了那次旧运行的实际 ready 工作或迁移压力。**

尚未提交的未来事件使计划覆盖字段变为 true，也不是稳态或持续计算的证明。
正式验证还必须看真实的背景提交、可执行候选、服务间隔、
缺 KV 等待、实际 HBM/Host 状态、前缀损失与完整测量 JCT。
若实际供给仍不足，需要调整后续背景分配/人口或补充自然原任务，
不能只靠在完成之后附加任务来宣布 workload 合格。

新人口的可执行因果图物化、来源注册、旧工具条目与先验保留、
完整 CPU 源码绑定及 fresh GPU 资格仍未完成。
本轮没有复制或重写原 token，也未更改运行中策略或恢复工作量排序。
当前 Full/ThunderAgent 对比仍按原固定测量组完成、实际退出和匹配引擎继续。

## 候选 B：56 项追加十七项

为增加旧 original56 测量尾部之前能够实际到达的人口，
同一准备程序还生成
`useful_token_cost_r1/background_tail_original56_plus17_prepare_r1/`。
它保留当前 56 个原项目、全部 56 个到达前缀和当前固定 18 个测量成员，
从已有 25 个增补项目中按同一固有工作量规则选取最小的 17 个完整项目。
总数同为 73，但项目集合、旧人口和测量成员继承关系与候选 A 不同。

其 P80/P20 也为 2.934261，高于已接受的 2.931418。
这是该分位数下的巧合，不表示两个人口相同。
所有原 original56 的长上下文任务仍保留；
新增项目的工具时间映射及原条目保留还未物化。

第一个新增到达为 **3413.7150 s**，末次仍为 **4376.4982 s**。
按旧 56 项完整报告的完成时点，17 个新增项目均已到达，
所以它与候选 A 的“所有新增事件晚于旧测量完成”有不同的供给含义。
但末次到达仍早于旧 Full 完成约 39.35 s、
早于旧 ThunderAgent 完成约 376.87 s，不能声称覆盖保证已经成立。
新增背景也可能改变两种方法的完成时间，旧时间不是新实验的完成预测。

两个目录均只保存 CPU 准备，没有登记 GPU 后继。
保留两个候选是为了明确到达覆盖与实际可执行人口的区别，
不根据尚未完成的新 ThunderAgent 结果选择或宣布负载获胜。

## 候选 B 的实际物化与逐项复核

`prepare_composite_workload.py --population-plan` 现在能直接物化已声明的人口，
不会再次按工作量分位数筛选。
它逐行匹配已验证 inventory 中的原项目、workflow、事件和 token 身份，
拒绝外来条目、重复身份、修改原行、不一致的工作量统计，
以及同时指定另一轮分位数选择。
原有不带该参数的选择行为保留。

候选 B 已实际生成：

- `useful_token_cost_r1/composite73_original56_plus17_r1/`：
  73 个不同原项目，11,567 个请求，1,483,559 个输出 token。
- `useful_token_cost_r1/composite73_original56_plus17_tool_timing_r1.json`：
  使用原 TraceLab 数据、seed21、分类采样规则和相同训练/评估划分。
  不修改当前工具时长分布。
- `useful_token_cost_r1/background_tail_original56_plus17_materialized_verified_r1.json`：
  独立 CPU 逐项复核证据，不是 GPU 注册。

复核程序 `scripts/verify_background_tail_materialization.py` 确认：

| 固定内容 | 核对结果 |
| --- | --- |
| 原始模型请求 | 9,858 个请求的预期 native 工作量和归属不变 |
| 原始请求/事件图 | 50,346 个节点的事件、延迟、依赖逐项相同 |
| 原始工具时间 | 2,150 个采样条目逐项相同 |
| 新工具时间 | 只为 17 个新增 workflow 增加 184 个采样条目 |
| 预测先验 | 实际文件 hash 仍为 `949e2b45...`，公开采样池及划分相同 |
| 到达与测量 | 原 56 个到达不变；12 预热、18 测量及共同窗口不变 |
| 背景 | 原 26 个背景成员保留，另加 17 个，合计 43 个 |

物化、原图合成、混合引擎、来源、到达回归一组 100 项通过；
逐项复核和工具映射回归另一组 65 项通过。
其中 19 项物化测试重叠，合计覆盖 146 个不同测试用例。
报告为 `declared_background_composition_cpu_r1.xml` 和
`background_materialization_verifier_cpu_r1.xml`。
这不是完整当前源码的共享 GPU 入口资格。

形状局限继续保留：307 次 JOIN 中 222 次为单成员，
68 次双成员、11 次三成员、6 次四成员，共 85 次多成员。
Child 模型调用数中位数为 20，P90 为 36；
root 并行 JOIN 轮数中位数为 1。
该候选补足的是背景尾部人口，不代表已经满足多轮短 sibling 的理想结构。
原形状检查仍报告 `needs_new_candidates`，未改写为通过。
P80/P20 为 2.934261，采用此前接受的 2.931418 最低值，
原 3、4 门槛未达到的事实继续保留。

新增到达从 3413.7150 s 延续到 4376.4982 s。
实际 ready 供给、Host 独占/重复备份、额外重算和测量尾部覆盖仍需实际运行；
不能用未来尚未提交的到达证明压力成立。
目前仅完成 CPU 物化，没有注册 GPU 后继，也不替换正在运行的 56 项对照。

## 当前源码绑定、来源注册与实际 Full

上述 56 项对照结束后，扩展候选继续完成以下步骤。
用户接受的门槛仍为 2.931418，不重新要求 2.934261 达到原 3 或 4。

`background_tail_original56_plus17_bound_cpu_r1` 在一次执行中通过
**6,795 项测试和 23 项子测试**，保留此前 6,715 个 literal 用例、
全部既有源码路径，绑定 2,443 个源码输入和 276 个外部输入。
源码、原生引擎和外部输入在执行期间保持不变，零失败、错误和 skip。
它包括 Host 扫描轮转、到达扩展、声明人口物化和逐项核对。

干净操作目录为
`.worktrees/beliefkv-background73-operator-42156309`。
重新核对全部 2,443 个源码和 276 个外部输入。
原生引擎仍为 `434c35dd66211d0a230717c88bf1452056769a40`；
103 项既有 native CPU 证据只重新绑定 canonical patch 路径及其状态 hash，
补丁字节、实际 tree 和其余状态相同。
记录在 `background73_nonranking_controls_prepare_r1/clean_operator_and_native_binding_r1.json`。

25 项新增采集的旧 callback-clock 失败保留。
当前 CPU 重新分析确认原 25 个自然 workflow、3,249 个请求和
唯一一项 publication-clock 冲突，未重采、改时长或覆盖旧失败。
新的分析证明为
`background_tail_original56_plus17_bound_cpu_r1/source_requalification_r1.json`。

73 项合成来源登记为
`ops/workflow_original73_background_extension_source_20261101_r1.registration.json`，
绑定六份原始采集完成证据和当前 CPU 分析。
这是合成来源登记，不伪造新的自然 native capture 或全人口性能结果。

### 两种方法的准备

`ops/prepare_background73_controls_20261101.py` 生成
`background73_nonranking_controls_prepare_r1/{full,thunderagent_acting_decay}.json`。
保留共同引擎配置，更新 73 项来源、到达、工具映射和 Full bookkeeping 工件。
原 2,150 个工具采样和公开预测先验不变。

实际 runtime 构造器确认：

- Full：invocation 粒度，2 s control、2 s 普通合并窗口；
  JOIN/EOS、预测工具卸载、prepare_host 和 agent 连续性开启。
- 工作量排序、未来剩余工作决策、组合准入、工具预取与逐层 handoff 关闭。
  旧 Oracle bookkeeping 的宽泛 `future_information_used=true`、
  `deployable=false` 元数据保留，未改写为可部署预测器。
- ThunderAgent：独立 invocation program，5 s control、1 s acting-decay half-life、
  `periodic_resume_only`；无 Full 的预测卸载或备份策略。

### 新 GPU 入口与测量观察器

新的 position 资格注册为
`ops/workflow_original73_background_extension_position_20261101_r1.registration.json`，
端口 **18966**。
24 个 token/session/stop 请求全部通过，24 次均观察到原生 stop，
1/8/24 采样检查完成，随后自动登记仅 Full 的比较臂：
`ops/workflow_original73_background_extension_full_20261101_r1.registration.json`。

实际执行冻结为 `42156309`，原生 engine/configuration 保持共同版本。
现场核对 BF16、FA3、TP1、805,198-token Device 池、200 GB Host、
running24、chunk2048、mixed 关闭、decode graph 精确 1..24。
Supervisor 4106236、server 4106728、scheduler 4106805、
driver 4107767 均实际存活且 nice0；native writer 健康。
这是实际执行前缀，不是完整 JCT 或压力验收。

CPU 收尾脚本初次误读 Full 注册中不存在的 `source_registration` 字段，
退出为 `KeyError('source_registration')`。
保留
`ops/workflow_original73_background_extension_full_finish_20261101_r1.plan.state.json`
及原脚本，不修改或重启 GPU 实验。
恢复脚本通过 Full 的实际 `experiment_configuration` 和 `source_arm`
核对相同来源后单独登记观察器。
记录为 `background73_nonranking_controls_prepare_r1/observer_recovery_r1.json`。

实际 fixed18 观察器为 PID **4108802**，nice19、CUDA 隐藏，
注册为
`ops/workflow_original73_background_extension_full_measured_completion_20261101_r1.registration.json`。
它读取完整事件前缀，只在原固定 18 个成员全部完成后停止非测量背景，
并核对实际回执、drain 和 owned 退出，再生成 JCT、供给和压力审计。
额外 token 成本后处理没有由已退出的首个 CPU 收尾脚本自动启动。
当前没有登记 Full 之后的 GPU 后继，workload 和完整性能结论均未验收。

### 补齐成本收尾与独立现场核验

单独登记的 CPU 成本观察器为
`ops/workflow_original73_background_extension_full_costs_20261101_r1.registration.json`，
实际 PID **4113543**，nice19、CUDA 隐藏、OMP/OpenBLAS 各一线程。
`ops/finish_background73_costs_20261101.py` 等待 fixed18 观察器实际退出，
并要求完整测量回执、两个新鲜 idle snapshot、owned GPU 进程退出全部通过，
随后生成 `background73_nonranking_full_complete_costs_r1/` 下的
`token_cost.json`、`interactivity.json` 和 `previous_prefix_prefill.json`。
它不修改 GPU 配置、不登记后继，也不会把未完成组当作完整结果。

`background73_full_live_r1/execution_verified.json` 于
2026-11-01 12:34:33 America/Los_Angeles 完成独立现场核验：

- Supervisor/server/driver 均为实际存活的 nice0 进程，
  唯一 GPU scheduler 为 4106805，归属本次 server。
- fixed18 与成本观察器均实际存活且 nice19。
- HTTP 服务配置、共同 engine/graph/MoE 身份和实际 Device/Host 容量相符。
- 4,785 份已完成 native 回执、579,466 个输出 token 与原始 packet 哈希、
  实际输出数组和 workflow/invocation/context 归属逐项一致。
- 实际非排序 Full 开关保持声明值，native writer 未丢记录、未报错。

上述是独立日志前缀及现场配置证据，不是完整 JCT 或性能验收。
此时 fixed18 已完成 12/18，仍需保留余下测量成员的完整尾部。

### 早期供给与容量前缀

同目录的 `supply.json`、`periodic.json`、`demand.json` 均为隐藏 CUDA、
nice19 的只读 CPU 分析，输入分别封存，并不构成原子快照：

| 口径 | 早期观测 |
| --- | --- |
| 采样供给前缀 | 25 个到达、13 个结束、6/18 测量结束 |
| 共同窗口状态样本 | 1,167 个，其中 395 个存在 ready-paused |
| 采样逻辑集合竞争 | 7 个样本；其中 4 个候选剩余工作量比值至少为 2 |
| 实际 periodic control | 616 次，297 次存在 ready-paused，9 次跨 workflow 逻辑集合竞争 |
| Periodic grant 后续服务 | 309 次均有对应实际服务，291 次在有界观测窗口内完成有效批次 |
| 有预算但无 ready-paused | 319 次 periodic control |

采样数不能替换为时间加权比例，采样竞争也不等于实际 Oracle 决策。
后续服务只能证明授予被消费，不能证明某次选择降低了 JCT。

`demand.json` 的 22 个规则观测点最晚到 1,242.5957 s；
未来下一次可复用旧前缀的并集峰值为 1,806,873 token，
占 Device+Host 总池的 0.636289。
包含 pending/latest 需求的峰值为 1,812,960 token，占 0.638433。
这是离线利用下一请求信息的诊断量，不是线上保留权限、实际分配可行性，
也不能排除更晚的容量压力。

非原子 native 状态前缀中，3,107 次旧前缀 probe 未发现同上下文前缀损失；
Host-block 回访仍记录 5,465 个重算单位，两者不可相加或互相替代。
Host 已达约 200 GB 高水位，记录 97 次原生 Host 回收、198,517 个回收 token。
该前缀不能证明全部执行零重算。

新增 17 个 workflow 的首个到达是 3,413.7150 s，
上述供给和容量前缀均尚未包含它们。
因此不能将这些早期数值当作背景扩展已改善尾部压力的证据。

### 备份重复与后段容量诊断

`background73_full_live_r1/backup_repetition.json` 由只读操作脚本
`ops/audit_background73_backup_repetition_20261101.py` 生成，
分别封存机会日志前缀和独立 native 状态读取字节的 hash。
最后 runtime snapshot 中的 idle/SPAWN 备份 token 计数均与日志逐项对齐：

| 已记录备份子集 | 提交 token | 同一 node/creation/extent 的重复 token |
| --- | ---: | ---: |
| Idle | 200,483 | 0 |
| SPAWN | 1,974,450 | 12,843 |

未发现显式 `host_demand_mirrors_reclaimed` 后重新提交相同范围的
idle/SPAWN 备份。重复提交本身也可能由中间真实使用和迁移产生，
不能视作无用流量；本审计只覆盖这两类日志，不覆盖全部原生 D2H。
原生 Host 回收日志没有 creation 身份，不以 node ID 单独强行关联。
当前证据不支持将主要迁移流量归因为大量相同范围的 `prepare_host` 循环。

同一 runtime snapshot 记录 11,808 次事件工具替换尝试、
58 次实际授予、24 次备份尝试，累计线程 CPU 约 25.8204 s。
该计时覆盖被记录的尝试路径，不是全部控制成本、GPU 空闲时间，
也不能直接从 JCT 中扣除。它是后续检查无效候选扫描的依据，
本轮没有据此热改运行源码。

独立 native 状态已出现 3 次旧前缀损失 probe，合计 278,900 token；
早期零损失结论不再适用于更晚前缀。
只读调用既有 `audit_previous_prefix_prefill.inspect()` 后，
3 次均匹配到实际 native prefill：networking hard027 为 153,735、
system monitoring061 为 83,156、web blog040 为 42,009 token。
三个 request 的 attempt/session/归属和输入区间均有效，
共涉及 139 个 prefill 批次，没有重复范围计数。
受影响整批 worker 区间约 37.4088 s，仍不是这些 token 的独占成本或可避免 JCT；
最终逐项结果由已登记的完整成本观察器保存。

`background73_full_live_r1/late_demand.json` 封存了截至到达原点后
2,784.6835 s 的 51 个观测点，其中包含这 3 次损失时刻：

- 下一请求可复用旧前缀并集峰值为 2,825,222 token，
  占共同池 2,839,704 token 的 **99.4900%**，出现在 2,475.3862 s。
- Pending 输入或最新已服务状态并集峰值为 2,858,001 token，
  占总池 **100.6443%**，出现在 2,460 s。
- 三次损失时的下一请求可复用旧前缀比例分别为
  99.4900%、97.1052%、93.3659%。

可复用并集仍使用私有下一请求信息，只用于离线诊断。
Pending 中可能包含尚未计算的首次输入，这个量超过总池不能直接证明
全部必须同时驻留；小于总池也不能证明增长、复制和实际分配可行。
但这些后段数值明确说明，早期 63.6% 不能排除后段接近容量上限。
扫描缺陷、具体驱逐选择与这 3 次损失的逐次因果仍未建立，
不能只凭原生回收次数认定主要原因。
新增背景尚未到达，完整 workload、JCT 和后继比较继续待验收。

## 同一 73 项的静态到达排列候选

后续只读检查发现，保留 original56 旧组的当前扩展表中，
12 个 warmup 成员全部为 Python 项目，18 个 measured 成员有 3 个 JavaScript，
43 个 background 成员有 17 个 JavaScript。
三组初始输入中位数分别约 79,654、64,677、56,508 token；
原组身份保留使新增小上下文成员主要在后段才参与运行。
项目语言仅用于描述，不是新的候选选择条件。

CPU 准备为 `original73_context_balanced_arrivals_prepare_r1/`，
操作脚本为 `ops/prepare_original73_context_balanced_arrivals_20261101.py`。
它保留当前全部 73 个完整原项目、原模型 token、工具时长和策略，
只重新指定 workflow 到达槽位，不另采集轨迹或制造任务：

- 仍为 12 个初始 workflow、平均 70 s、arrival seed35。
- 每个到达时刻逐项与当前 73 项扩展表相同，末次仍为 4,376.498230 s。
- 仍为 12 warmup、固定到达序号的 18 measured、43 background，
  保留全部 measured 正常尾部，组完成后允许截断非测量工作。
- 共同吞吐窗口仍为 `[55.692771, 1638.954850)` s。
- 全人口模型工作量 P80/P20 仍为 2.934261；原 3、4 门槛仍未达到。
  使用用户此前接受的 2.931418 最低值，不改变其验收含义。

静态条件只读取源轨迹的模型工作量、初始上下文和共同 Device 容量。
要求测量组覆盖三个工作量档位、小上下文和长上下文；
初始 prompt 总量限定为 Device token 容量的 0.9--1.25 倍。
该 prompt 合计不是物理独占 KV，也不保证 native 准入或 Host 可行。
首次 100 个 seed 均未满足全部条件，保留拒绝记录后只扩大固定搜索范围，
没有降低接受条件。seed148 是顺序检查 102 个排列后的首个匹配：

| 静态特征 | 新 warmup | 新 measured |
| --- | ---: | ---: |
| 人口 | 12 | 18 |
| 初始上下文不超过 65,536 token | 5 | 8 |
| 初始上下文至少 131,072 token | 2 | 3 |
| 初始上下文至少 196,608 token | 1 | 2 |
| 模型工作量 P80/P20 | 3.2495 | 3.4576 |
| 工作量三个档位的人口 | 4 / 3 / 5 | 6 / 5 / 7 |
| JavaScript 项目数，仅描述 | 5 | 4 |

`independent_verification.json` 重新生成到达表，验证全部成员、
每个到达时刻、私有 schema2 cohort、首个匹配 seed 和全部输入/输出 hash，
8 项结构检查全部通过。没有读取方法性能、实际完成顺序或调度结果来选择排列。
新的测量成员已经改变，不能把它与旧固定组直接作配对性能比较。

这只是 CPU 候选，没有创建运行配置、GPU 注册或后继任务。
它也没有证明比旧表更容易容纳状态：新初始 prompt 合计为 1,003,314 token，
仍需检查实际增长、共享、工具阻塞、ready 供给、迁移与重复 prefill。
多轮短 child 的原结构局限继续保留。
当前运行及其 fixed18 观察器不变；先完成并审计当前 Full，再决定是否使用候选。

## 扩展 Full 的完整固定组结果

当前扩展 Full 已按协议结束，fixed18 观察器和成本观察器均实际退出。
`background73_nonranking_full_review_r1/independent_complete.json`
再次核对原始工作、共同引擎、两次新鲜 idle snapshot、实际 owned 退出，
以及成本报告的输出 ordinal。
固定 18 个 workflow 的 **2,819 个请求、362,072 个输出 token** 全部完成；
73 个计划到达均已提交，60 个 workflow 自然结束，
13 个非测量成员按协议截断，不将其作为完整 JCT 样本。
原始 supervisor 的停止标签继续保留，没有改写为全人口成功。

| 当前固定组/共同窗口指标 | Predictive Full |
| --- | ---: |
| 完整平均 JCT | 1,011.467748 s |
| 完整 P50 JCT | 623.835780 s |
| 完整 P95 JCT | 2,808.516803 s |
| 最大 JCT | 3,191.540053 s |
| 共同窗口全部输出吞吐 | 307.841643 token/s |
| 共同窗口 decode 吞吐 | 305.387217 token/s |
| 共同窗口 decode worker 成本 | 2.313829 ms/output-token |
| 共同窗口 prefill worker 成本 | 0.102452 ms/extend-token |

以上成本仍包含传输依赖和 CPU 提交，不是纯 CUDA kernel 时间。
当前没有同一 73 项背景流量的 ThunderAgent 完整结果，
不能用原 56 项对照直接计算本轮方法收益。
新增背景、执行时序和 Host 扫描修复同时存在，
也不能将本轮与原 Full 的差异归因为其中一个变化。

### 未通过的负载条件

`workload_decision.json` 保留拒绝，失败项恰为：

1. 末次到达为 4,376.498230 s，末次测量完成为 4,483.401552 s，
   背景提交仍提前 **106.903322 s** 耗尽。
2. 14 次旧前缀损失共 **1,121,690 token**，
   全部匹配到实际 native prefill 区间，没有重复范围计数。
   其中固定测量组为 **304,835 token**。

实际旧输入涉及 556 个 prefill 批次；
这些整批 worker 区间合计 140.614210 s，不是旧 token 的独占成本，
不能直接从 JCT 中扣除。Host-block 回访口径继续单列。

共同窗口 ready-paused 占状态样本的 32.9627%，不是时间加权比例。
全观察跨度有 2,190 次实际 periodic control、131 次逻辑集合竞争；
1,808 次授予均有对应实际服务，1,603 次在有界窗口内完成有效批次。
这些次数不是 Oracle 作用次数，也不能换算为可避免 JCT。

后段下一请求可复用旧前缀并集在 3,420 s 达到
2,872,248 token，占总池 **101.1460%**；
pending 输入或最新状态并集在 2,940 s 达到 2,909,268 token，占 **102.4497%**。
这是离线逻辑并集诊断，不是实际分配或在线保留规则，
但已经不能把全部额外执行归因于扫描顺序。
现有门槛中的 regular reuse-fit 只考察共同窗口，
该项为 true 不表示后段全部状态也能容纳。

本轮不继续扩跑当前到达表的 ThunderAgent，也不把 workload 或性能标为验收。
下一步先验证上节 seed148 的同人口到达候选。
`original73_context_balanced_controls_prepare_r1/` 已核对 2,443 个实际源码、
276 个既有外部输入，并通过两种方法的实际构造器相等检查。
运行环境全部字段保持不变；新的到达表和私有测量组正在重新绑定 CPU 资格。
它仍需新的 GPU 入口检查和实际 Full 校准，不能由静态均衡推导收益。

### 新 CPU 绑定的路径修复

首次在干净 operator 内重跑完整集合得到 6,791 通过、4 失败、23 个子测试通过，
保留 `original73_static_balanced_bound_cpu_r1/` 和同名日志。
四项失败均因干净目录缺少此前 Main 内存在、但被 Git 忽略的 CPU fixture 路径：
原生 AST fixture 的 `third_party/sglang-v0.5.20`，
以及 composite48、事件工具替换和旧 12/70 配置的三个历史 JSON。
没有修改断言、跳过测试或修改任务原题。

`original73_context_balanced_controls_prepare_r1/CPU_fixture_bindings.json`
记录四个受限符号链接和五个实际读取文件的原始 hash。
绑定后 2,443 个应用源码输入逐项未变，operator 仍保持 Git clean。
这两个原生 AST 输入只服务于既有 CPU fixture，
不是实际 serving engine；服务仍使用独立冻结的 `434c35dd`。

原四项失败单独重跑已全部通过，见
`original73_static_balance_missing_fixtures_cpu.xml`。
完整 R2 正在重跑，并将原失败证据、fixture 原始字节和全部新到达/配置工件一起绑定。
完整通过及新的实际 GPU 检查仍未由这四项局部通过代替。

### R2 通过与实际 GPU 入口

`original73_static_balanced_bound_cpu_r2/` 已完整通过：
6,795 个原用例和 23 个子测试，零失败、错误和 skip。
全部原用例身份和源码路径保留，2,443 个源码输入、296 个外部输入
在执行期间均未改变，包含此前 276 个外部绑定及新流量/fixture 证据。
R1 的失败文件和原始路径问题继续保留，未用局部重跑替代完整报告。

确认旧 Full/两个 CPU 观察器实际退出、预约已释放、
GPU 无其他 compute owner、18968 端口空闲后，
登记
`ops/workflow_original73_static_balance_position_20261101_r1.registration.json`。
实际冻结代码仍为 `42156309`，native tree 仍为 `434c35dd`，
共同 graph/MoE 配置不变；只有到达排列和私有测量成员改变。

现场观察到 position watcher **4148693**、server **4149005**、
唯一 GPU scheduler **4149341** 均实际存活且 nice0。
模型和 805,198-token Device / 200 GB Host 初始化完成，
正在执行 24 个 token/session/stop 检查；
本条记录尚未声称这组检查完成或新 Full 已开始。

CPU 收尾进程 **4149743** 实际存活，nice19、CUDA 隐藏，
计划为
`ops/workflow_original73_static_balance_finish_20261101_r1.plan.json`。
它等待 Full 注册，再创建固定18观察器；成功完整结束后核对
回执、fresh drain 和 owned 退出，再生成 JCT、供给、容量和 token 成本报告。
新收尾脚本使用 Full 的实际配置路径/source arm 绑定来源，
并处理 position 初始状态尚未发布的短暂窗口。
此时没有登记 ThunderAgent 或 Full 之后的 GPU 后继，
完整 workload、JCT 改善和性能验收继续待实测。

### 资格通过、启动目录竞争与实际 Full R2

上述 position 检查已完成：24 个原始 token/session/stop 请求全部通过，
24 次均为 native stop，1/8/24 采样检查通过，完成 fresh drain 与实际 owned 清理。
证据保存在 `workflow_original73_static_balance_position_20261101_balance73r1/`。

自动登记的 Full R1 在模型启动前因 `FileExistsError` 退出，
server/driver 均未创建，没有正式模型请求。
原因是新收尾观察器过早创建 `cohort_termination`，
连带创建运行根目录；launcher 随后使用 `exist_ok=False` 创建同一路径失败。
实际目录时间为 13:47:31，launcher 失败时间为 13:47:38，源码路径相符。
保留 R1 registration、失败 state、空 owned cleanup 与观察器错误，
不将其算作一次完成的性能实验。

新的 CPU 包装器
`ops/finish_static73_after_running_20261101_r2.py`
先确认 Full 为 `running_full`、运行目录由 launcher 创建，
且 watcher/server/driver 均实际存活，再调用既有固定组收尾逻辑。
它不提前创建运行目录，也未改变 GPU 调度代码或已绑定源码。
已有 24 项 GPU 证据通过原注册校验复用，没有再跑相同入口检查。

新的 Full 注册为
`ops/workflow_original73_static_balance_full_20261101_r2.registration.json`，
端口 **18969**，实际目录为
`experiments/raw/workflow_original73_static_balance_full_20261101_balance73r2/`。
代码仍冻结为 `42156309`，native 仍为 `434c35dd`，相同73项、seed148、
平均70秒、工具时长、公共预测先验、资源和策略不变。

实际 Full watcher **4154437**、server **4154996**、scheduler **4155327**、
driver **4156344** 均为 nice0。
CPU 收尾 **4155783**、fixed18 观察器 **4156578** 均实际存活、nice19、CUDA隐藏。
收尾计划为
`ops/workflow_original73_static_balance_finish_20261101_r2.plan.json`；
等待实际服务的门槛及输入 hash 另存于同 stem 的 `.waiting.json` 和 state。

`original73_static_balanced_full_live_r2/execution_verified.json`
独立核对首批 15 份完成请求的原始 token、实际输出数组与 agent/context 归属，
同时确认唯一 GPU owner、真实进程关系、共同执行配置及非排序 Full 开关。
记录中的 `sequential_pair` 是沿用现场验证器的 CPU observer 字段名，
本次对应收尾进程，不表示已登记一组 GPU 比较。
此时 measured 为 0/18；这是实际服务前缀，完整 JCT、低重算、
持续供给和对照收益均待后续完成。

### 运行中的 Host NUMA 物理页分布

2026-11-01 14:15:46（America/Los_Angeles），只读检查实际 scheduler
`4155327` 的 `/proc/maps`、`numa_maps`、`status` 和 GPU topology，
证据保存于 `original73_static_balanced_full_live_r2/host_numa.json`。
审计器为 `ops/audit_registered_host_numa_20261101.py`，
实际使用 nice19、CUDA 隐藏和单线程环境；没有移动页面、修改内存策略、
启动 CUDA 操作或改变运行中配置。

找到唯一一段大小与 native census 的完整 FULL Host 分配恰好相同的
共享 `/dev/zero (deleted)` 映射，全部页面已经驻留：

| 实际观察 | 数值 |
| --- | ---: |
| 映射大小 / census Host 分配 | 200,000,077,824 bytes |
| GPU0 的 NUMA affinity | node1 |
| 映射策略 | `prefer:1` |
| node1 驻留 | 152,452,124,672 bytes |
| node0 驻留 | 47,547,953,152 bytes |
| 相对 GPU 的远端驻留比例 | 23.773967% |
| kernel page | 4 KiB |
| 进程 CPU affinity | `32-63,96-127` |
| 进程允许内存节点 | `0-1` |

保留原始映射行、各输入 hash 和进程身份，并在读取后重新核对映射与存活状态。
实际引擎的分配路径为 `HostTensorAllocator.allocate -> alloc_mmap`；
普通页面路径显式预触页，随后进行 `cudaHostRegister`。
本次未独立导出 native buffer 指针，映射身份的依据仍是共享类型、
唯一性和完整容量精确匹配，不能将其描述为指针级归属证明。

这个结果说明当前 200 GB 配置已经存在跨 NUMA 驻留，
不能继续把跨节点仅视为未来扩大 Host 池才会出现的风险。
但分配页驻留不等于有用缓存 KV 的分布，也不等于各次 H2D/D2H 的远端比例。
尚未测量传输带宽损失、互连流量或因果 JCT 收益，
也未查明页面为何落在 node0；不能仅凭此修改运行中的绑定或宣称性能根因。
后续匹配 ThunderAgent 对照应使用相同资源和分配策略，并采集同口径证据。

14:21:31 的 fixed18 观察器记录为 6/18 完成、28 个实际到达。
GPU owner、server、driver 和两个 CPU 收尾观察器均实际存活；
这仍是未完成前缀，没有注册后继 GPU 或将 workload 标为验收。

### 完整共同窗口的只读诊断与条件对照

`original73_static_balanced_full_live_r2/common_window_{supply,periodic,demand}.json`
是同一运行的三个独立封存前缀，不是三个新实验。
共同吞吐窗口 `[55.692771, 1638.954850)` s 已完整覆盖；
供给观察截止到 1,716.099744 s，此时为 30 个实际到达、9/18 测量完成。
各 JSONL 独立封存，未声称原子快照。

| 共同窗口诊断 | 实际结果 |
| --- | ---: |
| 状态样本 | 1,502 |
| 有 ready-paused 的状态样本 | 447，29.7603% |
| 跨 workflow 集合竞争状态样本 | 7 |
| 其中候选剩余工作量比至少 2 的样本 | 5 |
| 实际 periodic control | 773 |
| predecision 有 ready-paused 的 control | 348，45.0194% |
| 实际跨 workflow 逻辑集合竞争 control | 11 |
| periodic 授予 / 压力检查后保留 | 407 / 407 |

状态样本比例与决策比例分别统计，不是时间加权利用率，也不是 Oracle 生效次数。
整个 periodic 封存前缀另有 424 次恢复授予，全部具有对应 exact service，
其中 396 次在下一控制或已记录干预前完成有效批次。
该数字包含共同窗口以外的观测，不能与表内 407 相加。

供给前缀尾部的旧同 context 输入前缀损失代理仍为 0；
Host-block 回访记录 **2 token** 重算，两种口径不相加，也不称零重算。
Host 高水位已接近 200 GB，并已发生 2,000 次 Host eviction 调用；
仅凭调用数不能判定有用状态丢失或其 JCT 代价。
每 60 秒采样的下一请求可复用旧前缀并集在此诊断中最高为
1,849,099 token，占 combined pool 的 **65.1159%**，出现在 1,320 s。
这仍不是完整测量尾部的容量上界、物理准入证明或线上可用未来信息。

条件对照脚本为 `ops/finish_static73_matched_pair_20261101.py`，
计划及 state 为
`ops/workflow_original73_static_balanced_pair_20261101_r1.plan{,.state}.json`。
CPU 准备重新检查相同 clean `42156309`、已绑定源码与外部输入、
24 项实际 native-stop 资格、共同执行 identity 和实际 ThunderAgent 构造器。
单独的 gate 检查接受完整通过记录，并拒绝四种未完成/失败记录；
没有重跑 GPU 资格或改变 serving 源码。

新固定18从原始 expected 图动态得到 **3,263 请求、424,240 输出 token**。
不再沿用旧测量组的 2,819 / 362,072。
条件观察器 **4167313** 已实际启动，尚未创建 ThunderAgent 的 GPU 运行目录。
它只在以下条件同时满足后登记一次匹配的 acting-decay 对照：
Full 的完整测量与成本收尾成功、预先声明的 workload 诊断全部通过、
fresh drain 与实际 owned 退出验证成功、预约与当前 GPU/Host/端口检查通过。
若任一诊断失败，保留拒绝且不启动该对照。

后续对照保留相同73项、seed148、到达时刻、私有测量组、工具时间、
Device/Host 容量、共同 graph/MoE 配置及 frozen native `434c35dd`。
固定18观察器必须等真实 `running_thunderagent_acting_decay`、
已由 launcher 创建的目录和实际存活 owners 后才启动，
避免重现本次 R1 的目录竞争；同时采集同口径 Host NUMA 分布。
两臂完成后核对完整工作，生成 JCT、共同窗口吞吐、token 成本、
E2E normalized interactivity 和实际旧前缀 prefill 的匹配报告与图。
比较结束不自动启动其他 GPU，也不启用工作量排序、工具预取或 handoff。

14:32:56 条件观察器心跳为 11/18 测量完成、44 个实际到达；
现场唯一 GPU owner 仍为 Full scheduler `4155327`。
本节更新了此前“没有后继计划”的现场状态，
不把条件计划写成已启动 ThunderAgent 或已获得完整收益。

### 共同窗口之后的实际前缀损失

后段的 native 计数打破了此前封存前缀中的零损失。
Full 继续执行固定18的完整尾部；条件对照保留原门槛，
不会用共同窗口的早期零计数替代最终结果。
工作量差异最低值仍为用户已接受的 2.931418，没有恢复旧的 3 或 4。

`original73_static_balanced_full_live_r2/first_loss_actual_prefill.json`
复用已验证的 prefill 区间匹配器，使用独立封存的实时日志：

| 测量组原项目 | 损失 probe 的到达原点偏移 | 已服务旧输入重新 prefill |
| --- | ---: | ---: |
| `python_system_security_medium_028` root | 2,321.560961 s | 57,733 token |
| `python_data_lake_medium_050` root | 2,523.612832 s | 101,800 token |
| 合计 | 两次 probe | 159,533 token |

两次均核对 request、attempt、session/generation、context 与实际 extend 输入范围，
全部旧输入完整观察到，没有重复范围计数。
涉及 79 个 prefill 批次；整批 worker 区间合计 18,044.190178 ms，
不能将其全部归给旧 token、视为纯 GPU 时间或直接从 JCT 中扣除。
它是实时前缀，不是整轮最终重算合计，也没有提前声称完整测量验证。
审计器为 `ops/audit_live_previous_prefix_work_20261101.py`。

`first_loss_demand.json` 在首次 probe 的对应逻辑时点观察到：
23 个 live workflow、41 个 live context、2 个首次模型 pending context。
最新已完成状态并集为 2,616,155 token，pending input 或最新状态并集为
2,720,588 token，占 combined ceiling 的 95.8053%；
下一请求可复用旧前缀并集为 2,589,569 token，占 **91.1915%**。
截至该封存前缀的每60秒采样中，下一请求可复用并集最高为
2,606,520 token，占 91.7884%。
这些逻辑并集仍不证明物理页可用、迁移暂存空间足够或未采样时刻也可容纳。

`first_loss_host_history.json` 进一步通过 eviction ID、
完整 namespaced prefix HMAC 和 prefix 长度核对先前 Host 释放与后续回访。
第一项匹配 62 条重算 block 记录，最后驱逐距回访约 42.47–42.60 秒；
第二项匹配 110 条，约 124.97–244.52 秒。
block span 的和分别为 58,005 / 101,966，属于另一统计，
不与上表去重后的实际旧输入相加。
该证据保留所有相邻 Host 事件；相邻项可能是 ACK 或 eviction，
不能将它们自动当作 occupancy snapshot。
审计器为 `ops/audit_prefix_loss_host_history_20261101.py`。

从同一 Host 日志另外筛选真正的 `host_pool_usage`：
两项首次关联释放发生在同一对一秒采样之间，
前一采样时间为 wall 1,793,572,448,179.0562 ms，
后一采样为 1,793,572,449,182.3977 ms。

| 相邻实际分配采样 | 释放前 | 释放后 |
| --- | ---: | ---: |
| Host FULL 使用 | 2,033,452 token | 2,033,689 token |
| Host 使用比例 | 99.9482% | 99.9598% |
| HBM 空闲 | 1,900 token | 127,176 token |
| HBM resident | 803,298 token | 678,022 token |
| HBM non-evictable | 400,982 token | 603,657 token |

这组邻近采样支持继续检查迁移暂存空间和缓存回收时序，
尚不能证明两层无法保留全部有用状态，也不能反向证明是扫描或释放实现错误。
原生 `drive_host_eviction` 已有两遍安全重复副本回收；
释放前累计 6,593 次调用中，5,623 次已由重复副本回收满足。
Full 前置 Host demand 管理也已经轮转64候选，并非完全没有备份回收路径。
block 事件未记录释放当时是否还有 Device 副本，因此具体首因仍待核对，
不能仅凭 node ID 或一次 occupancy 把责任归给 prepare_host。

14:46:24 的 fixed18 观察器为 15/18 完成、53 个实际到达。
实验和两个收尾进程、条件对照进程均实际存活，Full 保持冻结配置。
后续先保留完整结果及负载拒绝，再据完整跨度的容量/供给证据决定修复；
不因这两次损失改门槛、截短测量尾部或自动扩跑该到达表的 baseline。

## 静态排列 Full 的完整结果

固定18已经完整结束。`original73_static_balanced_full_review_r2/independent_complete.json`
重新核对原始工作、实际输出 ordinal、完整结束证书、新鲜 drain、
实际 owned 退出和三个 CPU 观察器退出，并从底层报告重新计算负载判定。
3,263 个测量请求、424,240 个输出 token 全部一致。
56 个 workflow 自然结束，17 个非测量成员按协议截断，
其中5个背景成员尚未到达；没有将它们列入完整 JCT 样本。

| 固定组 / 共同窗口 | 本轮非排序 Full |
| --- | ---: |
| 完整平均 JCT | 1,107.123645 s |
| 完整 P95 JCT | 2,551.951038 s |
| 最大 JCT | 2,853.668328 s |
| 共同窗口全部输出吞吐 | 300.671636 token/s |
| 共同窗口 decode 吞吐 | 298.301214 token/s |
| Decode worker 成本 | 2.190554 ms/output-token |
| Prefill worker 成本 | 0.115179 ms/extend-token |

末次测量完成为 4,083.916397 s，末次实际到达为 4,066.249444 s；
冻结背景计划继续到 4,376.498230 s，因此本次背景覆盖通过。
完整跨度的采样可复用旧前缀并集最高为 2,606,520 token、91.7884%，
pending input 或最新状态并集最高为 2,720,588 token、95.8053%。
这些仍是逻辑采样诊断，不能从小于100%推导物理迁移必然可容纳。

最终负载判定的11项检查有10项通过，唯一失败项仍为
`zero_previously_served_same_context_prefix_loss_proxy`。
完整 prefill 审计确认恰为前述两例、159,533 token，全部属于测量组，
没有新出现未匹配范围；Host-block 回访口径另为328,973 token，不相加。

所有模型、driver、收尾与条件对照进程均已实际退出。
条件对照明确记录 `Full workload not qualified` 并停止，
没有创建或启动 ThunderAgent GPU 臂。保留本次完整拒绝，不修改门槛。
新的测量成员与旧 original56/旧 original73 不同，
上述 JCT 和吞吐不能直接换算成相对旧 baseline 的方法收益。

## 保留测量组的背景替换候选

为减少大状态同时存活的风险，CPU 筛查复用已有8个未使用的完整原项目。
只允许替换43个背景槽位，固定前30个预热及测量成员、
所有到达时刻、原工具时长分布与公共预测先验。
评分仅使用原任务的初始 prompt、最大 prompt 和固有模型工作量；
没有使用方法 JCT、完成顺序或对照胜负。

`original73_static_balanced_full_live_r2/background_KV_alternatives.json`
按事先写明的“最大 prompt 降幅、初始 prompt 降幅、身份”顺序筛查，
至多替换3项，每项两种 prompt 降幅均需至少65,536 token，
同时保留已接受的完整人口 P80/P20 最低值2.931418。
三轮分别有50、35、22个符合条件的候选，选中：

| 原背景项目 | 替换为完整原项目 | 零基到达槽位 | 最大 prompt 降低 |
| --- | --- | ---: | ---: |
| `python_data_warehouse_hard_048` | `javascript_web_portfolio_easy_077` | 32 | 137,448 token |
| `python_ml_training_expert_051` | `javascript_web_portfolio_easy_005` | 43 | 171,876 token |
| `python_web_ecommerce_expert_000` | `javascript_ml_training_easy_015` | 56 | 197,670 token |

保留73个不同项目、12个预热、18个测量、43个背景。
模型工作量 P80/P20 仍为 **2.934261**，大于等于3/4的标记仍为false；
保留6个初始上下文至少131,072 token 的原任务，其中3个至少196,608。
新人口计划为11,796请求、1,499,356输出，较旧人口增加229请求、15,797输出；
测量成员及其3,263 / 424,240完整工作保持不变。
最大 prompt 降幅之和不是并发 KV 节省、实际峰值或可避免 JCT。
它是降低背景容量需求的候选，尚未证明低重算、供给或性能通过。

`substitute_background_arrivals` 引入显式 schema6：
完整保存原 Poisson/排列声明，按原随机流重新生成全部时刻，
只替换固定前景前缀以后的身份；拒绝重复/外来旧身份、替换前景、
复用已有身份、伪造时刻、摘要或来源。
原 Poisson、排列、背景追加和同时到达形式保持原规则。
物化器同时增加完整原任务的背景替换声明，
逐行核对原 inventory，拒绝改变前景、隐去被替换成员或改变人口规模。

`background_replacement_combined_cpu.xml` 的189项检查全部通过，
覆盖真实两 workflow 回放、固定测量组与完整尾部协议、
原始 token/依赖的实际合成，以及旧到达、合成和收尾回归。
这是针对性 CPU 验证，尚非重新绑定的完整 serving 资格或 GPU 结果。

`original73_background_substitution_prepare_r1/` 已生成
`population_plan.json`、`arrivals.json`、`analysis_cohort.json` 和输入绑定。
首次 CPU 准备误用了未实际生成的 `composite/inventories/source_N.json` 路径，
在创建候选目录前以 `FileNotFoundError` 拒绝；
改为读取原 composite 已哈希绑定的六个 inventory，
逐项哈希及 canonical 原行匹配后才生成候选，没有改写原 source 或任务。
可执行 trace 物化、工具映射、完整源码绑定、GPU 入口与性能校准仍需完成；
当前没有登记新 GPU 任务。

### 背景替换的实际物化与例外边界

上述候选已经物化为
`useful_token_cost_r1/composite73_background_substitution_r1/`，
独立复核保存于
`original73_background_substitution_prepare_r1/materialized_verified.json`。
70个保留 workflow 的原事件字段、内部依赖与内部工具延迟均相同，
11,242个保留模型请求的工作身份保持不变；
全人口23,592份实际 prompt/output packet 字节分别与保留源或新增原任务相符。
保留2,300个工具采样，移除34个旧背景采样，新增58个采样，
仍使用同一公开采样池、分类、seed21和训练划分。
公共预测先验完整 hash 继续为
`949e2b45b7f6c31c39ac4ff497a2a8bd9bcc6c1fdd055a3a94cc847bbc0835da`。

不能将全部57,467个保留因果节点写成逐字节相同。
首次严格核对拒绝了3个无依赖 `WORKFLOW_START.delay_ms` 差异：

| 原项目 | 旧 source-start delay | 新 source-start delay |
| --- | ---: | ---: |
| `python_api_graphql_expert_007` | 115.931294 ms | 30.959760 ms |
| `python_desktop_development_expert_021` | 115.689469 ms | 30.717935 ms |
| `python_web_cms_expert_002` | 84.971535 ms | 0 ms |

这是移除原采集内最早 root 后的源起点归一化；
这3项的原始 event 和其余节点字段仍完全相同，其余57,464个节点包含
source delay 也完全相同。实际回放的 `execute_causal_replay`
使用冻结外部到达表覆盖这些起点，独立核对确认全部到达时刻和前30个成员不变。
CPU执行证明另将fixture源起点改为60,000 ms，外部指定0/5 ms，
在3秒超时内完成4个stand-in请求，实际workflow起点相隔精确5 ms。
这是外部起点覆盖的CPU证据，不是GPU执行或性能结果；
该例外仅在这份已逐项验证的外部到达表下成立。

固定18个测量成员仍为3,263个请求、424,240个输出token。
没有更改任务原题、源码、测试或内部执行依赖。

### 完整 CPU 资格与首次入口拒绝

`original73_background_substitution_bound_cpu_r1/` 已通过
**6,834项测试和23项子测试**，零失败、错误和skip。
保留此前6,795个literal用例、全部2,444个源码输入与原296个外部绑定；
本次外部输入共339项。源码、原生引擎和外部输入在执行期间均未改变。
干净operator为
`.worktrees/beliefkv-background-substitution-operator-2fe1b74c`。
四个历史CPU fixture路径按原五份文件hash绑定，
没有改动断言或把旧AST fixture当作实际serving engine。

首次入口
`original73_background_substitution_gpu_entry_r1/source_registration_entry.json`
在来源注册阶段拒绝旧background25重新分析证明：
该证明绑定旧版 `replay_arrivals.py`，与新schema6源码不同。
它尚未创建来源/position注册或启动GPU，不能计作失败的性能实验。
原拒绝及stderr保持原样。

随后用相同原始background25采集、相同corrected causal audit
和本次完整CPU资格生成
`original73_background_substitution_bound_cpu_r1/source_requalification_r1.json`。
原始 `capture_failed` 状态、实际工具时长及唯一publication-clock冲突继续保留。
R2入口只更换该证明绑定，没有改动应用或原生源码。

### 资格通过与 Full 启动

R2实际来源登记为
`ops/workflow_original73_background_substitution_source_20261101_r1.registration.json`。
position注册为
`ops/workflow_original73_background_substitution_position_20261101_r1.registration.json`，
端口18972。
24个token/session/stop请求全部通过，1/8/24采样检查通过，
完成清理后自动登记仅Full的比较臂：
`ops/workflow_original73_background_substitution_full_20261101_r1.registration.json`。

应用冻结为 `2fe1b74c665e3a69f7f416238f48e82724ec5839`，
实际native tree仍为 `434c35dd66211d0a230717c88bf1452056769a40`。
Qwen3-Coder、BF16、FA3、TP1、Device805,198 token、Host200 GB、
running24、chunk2048、mixed关闭、精确1..24 decode graph和共同MoE配置不变。
Full继续保留JOIN/EOS预取、预测工具卸载、prepare_host、agent连续性与sibling支持；
工作量排序、组合准入、工具预取和逐层handoff均关闭。

2026-11-01 16:00 America/Los_Angeles，Full处于 `starting_full`，
watcher7296与server7804实际存活，driver尚未创建。
这不是正式回放已开始的证明，也没有新的完整JCT或方法收益。
CPU包装器5485实际存活，nice19、CUDA隐藏，
仅在实际 `running_full`、运行目录已存在和owner存活后创建固定18观察器，
避免此前过早创建运行目录的竞争。
包装器为 `ops/finish_background_substitution_full_20261101.py`，
等待状态为同名experiment的 `.waiting.state.json`。
此时没有登记ThunderAgent GPU后继；先保留完整测量尾部、
真实旧前缀prefill与供给审计，再决定匹配对照。

### 实际回放、NUMA 与条件匹配对照

16:02后的现场核对确认Full已进入 `running_full`。
`original73_background_substitution_full_live_r1/execution_verified.json`
逐项验证首批12个完成请求、474个输出token及实际归属，
共同engine身份、实际Device/Host池和非排序Full开关均相同。
实际watcher7296、server7804、唯一GPU scheduler7927、driver8744均为nice0。
CPU收尾5485和fixed18观察器8968均实际存活、nice19、CUDA隐藏。
首批回执不是完整性能结论；16:07:56时已到达15个workflow、
测量成员完成0/18，继续按原固定成员保留完整尾部。

同目录 `host_numa.json` 只读核对唯一200,000,077,824-byte共享映射：
策略为 `prefer:1`，GPU位于NUMA1，
其中node1为152,791,285,760 bytes、node0为47,208,792,064 bytes，
远端页面比例 **23.6044%**。
这与上次约23.7740%的分配接近，但不是实际有用KV或每次传输的远端比例，
也没有测得这部分页面的因果JCT成本。没有迁页、扩池或改变NUMA策略。

条件对照准备为
`ops/workflow_original73_background_substitution_pair_20261101_r1.plan.json`。
新包装器 `ops/finish_background_substitution_pair_20261101.py`
复用此前完整匹配对照的收尾逻辑，但重新绑定本次干净operator、
当前6,834项CPU资格、相同来源/工具/到达/测量成员和实际ThunderAgent构造器。
准备阶段没有创建未来GPU运行目录。
首次shell后台启动没有形成可观察进程或state，空日志保留；
独立进程组启动后，实际协调器11133的start ticks为774832047，
状态为 `waiting_qualified_complete_static73_Full`。
该旧状态名称仅来自复用的协调器，不指向旧到达表或旧Full。

协调器只有在Full固定18完整完成、全部已声明负载条件通过、
fresh drain及实际owned退出均通过后，才重新检查预约、资源、输入hash，
在空闲端口18973登记同一workload的ThunderAgent acting-decay。
它复用本次共同数据面已通过的24项position/native-stop及1/8/24资格，
不重复旧对照、不新增Oracle、不更改工具时长；
Full被拒绝时保留拒绝并停止接续。
完成匹配比较后没有额外GPU后继。
截至该现场观察，ThunderAgent仍未登记或启动，workload与方法收益未验收。

### 背景替换 Full 的早期供给与实际 sibling 服务

`original73_background_substitution_full_live_r1/early_supply.json`
封存至到达原点后599.980621 s，观察到17个到达、4个自然完成、
1/18测量成员完成。共同窗口内已有517个状态样本，
其中231个存在ready-paused、5个存在跨workflow集合竞争。
231/517约为44.68%，仍是状态样本占比，不是时间加权比例；
共同窗口尚未闭合，不能据此验收完整供给。

独立 `early_periodic.json` 记录287次真实周期控制，
其中174次有ready-paused，7次跨workflow逻辑集合竞争。
171次ready授予均有对应实际服务，153次在有界窗口内完成有效批次，
另1次授予的观察窗口仍开放。
这些数字证明当前链路确有执行候选和被消费的授予，
不表示排序已启用或每次授予都降低了JCT。

`early_demand.json` 的11个规则/末事件观测点中，
下一请求可复用旧前缀并集峰值为1,830,063 token、总池的64.4456%；
包含pending输入的并集峰值为1,843,153 token、64.9065%，均在480 s。
它仍是使用私有未来信息的只读逻辑并集，不能证明后段实际分配或迁移可容纳。
供给分析的独立native snapshot有1,560次前缀probe，
已服务同上下文前缀损失代理为0，Host-block回访归因重算也为0；
Host高水位为179,875,577,856 bytes。
不同日志前缀不是原子快照，以上零值不延伸到未来。

`early_sibling_service.json` 逐项核对463条服务记录、471个request attempt，
全部匹配到实际原生worker、显式阻塞多成员ALL-JOIN和完成session回执。
Attempt编号仍来自journal自身，没有独立来源验证，不称为独立attempt证明。
其中8条为多个成员共同进入同一prefill批次，其余455条单成员记录
不计为共同准入，也不将记录数称作JOIN组数。
这证明sibling选批路径实际执行；没有匹配对照，尚不证明独有收益或JCT改善。

三项替换的实际到达槽位为32/43/56，
时刻分别为1,763.121756 / 2,076.561228 / 3,413.715008 s。
它们均晚于共同吞吐窗口结束1,638.954850 s。
结构化核对确认整个共同窗口内的新旧到达成员和时刻完全相同。
因此当前早期供给，以及后续共同窗口相对旧静态Full的差异，
不能归因为这三项尚未提交的背景替换。
替换是否减少后段容量冲突与实际旧前缀prefill，
仍以完整固定测量尾部和匹配ThunderAgent结果为准。

### 最终独立复核入口

新增只读操作入口
`ops/verify_background_substitution_complete_20261101.py`。
默认核对本次完整Full，`--pair`再核对匹配ThunderAgent。
输出路径必须尚不存在；它不等待或启动GPU，也不预建未来review/cost目录。

复核从原始source expected、固定cohort和实际完成证书重建测量请求集合，
核对新鲜drain、owned退出、观察器实际退出、输出ordinal、
共同窗口输出守恒、全部已保存报告的输入及完整journal前缀hash。
随后从原供给、periodic、demand和population重新计算负载判定，
保留已接受的2.931418最低值与原3/4标记，不放宽任何其他条件。
Pair模式还重新计算完整测量JCT/吞吐比较，核对实际容量和共同执行身份。
未匹配的旧前缀几何会单独保留，不能写成零重算；
脚本本身不会将 `performance_accepted` 改为true。

已完成两项真实CPU入口检查，均为nice19、CUDA隐藏：

- 本次尚在运行的Full以“observer未完成/实际未退出”拒绝，
  `precompletion_must_not_exist.json`确实未创建，
  没有将部分结果伪装成完成证书。
- 在独立进程内将输入绑定到上次已完成静态Full，重新核对
  3,263请求、424,240输出及各完整成本报告，
  仍得到唯一拒绝项 `zero_previously_served_same_context_prefix_loss_proxy`，
  实际旧前缀输入仍为159,533 token。
  这检查的是复核器的完整路径，没有重跑模型或重标旧结果。

Pair模式的实际新比较仍需等两个方法都结束后执行；
以上CPU入口检查不是新GPU性能结果。

### 闭合共同窗口的成本与供给

共同窗口 `[55.692771, 1638.954850)` s 已完整经过，
时长1,583.262079 s。
只读入口 `ops/audit_background_substitution_closed_window_20261101.py`
以nice19、CUDA隐藏运行，核对实际Full owner、冻结配置、
worker输出ordinal、完整日志前缀hash及writer健康，
没有改变GPU运行或提早结束测量尾部。
结果为 `original73_background_substitution_full_live_r1/closed_common_window.json`。

| 相同墙钟窗口内的本轮 Full | 观测值 |
| --- | ---: |
| 全部实际输出，含背景 | 474,962 token |
| 全部输出吞吐 | 299.989500 token/s |
| Decode 输出吞吐 | 297.619078 token/s |
| 总 worker 成本 | 3.330786 ms/output-token |
| Decode worker 成本 | 2.204736 ms/decode-output-token |
| Prefill worker 成本 | 0.114232 ms/extend-input-token |
| 实际 extend 输入 | 4,754,388 token |
| Prefill 占总 worker 区间 | 34.3304% |
| Decode worker 时间加权有效宽度 | 7.7291 |
| Decode 图填充行 / 实际行 | 0 |

Worker区间覆盖1,581.467647 s，窗口其余1.794432 s。
该区间包含GPU、依赖等待与CPU提交，不能当作GPU利用率、
纯kernel成本或可消除主机开销。
Prefill输入包括实际执行的extend，不将它全部称为新语义输入或重算。
成本事件前缀时测量成员完成8/18；随后observer为9/18，
两者是独立前缀，不将后者写回成本证据。
完整测量回执、drain和退出仍待收尾，
报告保留 `complete_measured_execution_verified=false` 和 `JCT_gain=null`。
当前没有同负载ThunderAgent结果，不能给出新的方法收益。

独立 `closed_window_supply.json` 有1,504个共同窗口状态样本，
429个存在ready-paused，比例 **28.5239%**，超过原25%采样检查线。
采样集合竞争仍为5个；不是时间加权占比或真实Oracle选择次数。
`closed_window_periodic.json` 则封存854次全前缀真实控制，
其中388次有ready-paused、7次跨workflow逻辑集合竞争。
464次ready授予全部有对应实际服务，
425次在有界窗口内完成有效批次，1次观察窗口尚开放。
其全前缀已包括共同窗口之后，不能把854次全部写成窗口内控制。
当前7次竞争尚未达到原10次检查线；不修改该线或提前宣称负载通过。

`closed_window_demand.json` 的31个规则/末事件观测点中，
共同窗口内可复用旧前缀并集最高为1,864,347 token、总池的65.6529%。
独立native snapshot的4,020次probe中，旧前缀损失代理为0，
Host-block回访归因重算也为0。
Host已发生真实驱逐和回访，不能由上述逻辑并集小于总池推导
物理迁移必然可行，也不将两个零值外推到尚未结束的尾部。
三项背景替换全部晚于共同窗口，当前窗口数字不归因于这些替换。

### 两条旧损失请求的实际重放

为区分“尚未执行到旧损失位置”与“该请求已经保住前缀”，
`ops/audit_repeated_prefix_loss_cases_20261101.py`
只读跟踪上次静态Full中两个有完整prefill几何支持的测量请求。
结果保存在同一live目录的
`repeated_loss_cases_after_two_replacements.json`。
它逐项核对两次运行的完整prompt/output哈希、实际输出数组、
workflow/invocation/context/epoch、前一个已服务request及输入公共前缀，
再核对当前实际prefill的唯一attempt/session及输入区间。
未执行或未完成的请求会单独保留pending状态，不以缺失记录计为零重算。

两个请求在本次封存前缀中均已完成：

| 测量请求 | 相同旧输入公共前缀 | 上次cached | 本次cached | 上次实际旧前缀prefill | 本次实际旧前缀prefill |
| --- | ---: | ---: | ---: | ---: | ---: |
| `python_system_security_medium_028`，root epoch17 | 58,280 | 547 | 58,552 | 57,733 | 0 |
| `python_data_lake_medium_050`，root epoch34 | 120,660 | 18,860 | 120,825 | 101,800 | 0 |

每个本次请求均有一条实际prefill样本，
其输入区间没有重新进入旧公共前缀；完整回执及原始工作相同，
状态均为 `completed_exact_request_old_prefix_preserved`。
因此旧两例合计159,533个重复输入在这两个已完成请求中未再次出现，
不只是全局计数仍为零或请求尚未到达。
该比较只证明这两个请求的观测差异，
没有隔离背景替换与运行时序变化的因果，也不换算成节省的JCT。

16:45的独立observer为47个实际到达、固定测量成员完成15/18。
Native writer健康，独立状态的旧前缀损失代理仍为0；
剩余测量尾部及是否出现新的损失仍待完整结果，
ThunderAgent条件接续继续等待原负载检查和实际退出。

### 剩余测量尾部的进展核对

16:54的只读 `measured_tail_state.json` 使用原生产因果图，
只回放当前已观察到的runtime事件，不读取未来工具时长或剩余工作标签。
入口为 `ops/audit_measured_tail_state_20261101.py`，
以nice19、CUDA隐藏执行，没有改变模型、准入或结束条件。
固定测量成员此时完成16/18，剩余两个原项目为
`python_desktop_development_expert_021` 与 `python_fintech_trading_medium_066`。

两个workflow均只剩未结束root，在该事件前缀中均为 `running_llm`，
没有活动工具或阻塞child；独立准入snapshot均显示active、
未标记暂停，也不是paused-pending。
Desktop请求恰在事件前缀末尾提交，不能把尚无native样本视为未获服务。
Fintech请求提交约0.643 s，已有36条实际服务样本，
最后为decode、序列位置124,960且输出增量为1。
该次观察支持继续保留正常尾部，而不是据15/18的暂时平台期判定死锁。

因果事件与准入snapshot分别封存，并非原子状态。
`running_llm`本身包含本地或原生等待，不等于GPU正在计算；
本核对也不估计未完成JCT、不计算已完成子集的替代均值，
不能据单次正常进度证明整个历史等待均合理。
