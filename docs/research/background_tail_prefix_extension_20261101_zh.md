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
