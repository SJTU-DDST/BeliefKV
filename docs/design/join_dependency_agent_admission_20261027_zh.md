# 已阻塞 JOIN 的 agent 准入

这是 V260 运行期间发现的独立候选方向。V260 已完整结束并保留
负结果；新候选现已实现并通过定向 CPU 验证，默认关闭，尚无该候选的
GPU 性能结论。按用户最新顺序，先运行冻结的 V273 ThunderAgent，
再验证新候选。原目标仍是完整平均 JCT 和全程输出吞吐共同优于
ThunderAgent，并取得重复证据。

## 已复现的排序缺口

`NativeAdmissionRuntime.plan_native_prefill()` 先过滤没有 agent 资格的
请求，再处理请求优先级。`ThunderAgentMigration.lend_backed_blocked()`
选择资格受益者时，已运行 agent 的普通续接排在首次请求的 child 前，
然后截取前八个候选。因此，请求层的依赖优先不能帮助已经被资格
过滤的 child。

CPU 反例使用实际 Full runtime、真实观察事件和 agent policy：

| agent | 当时状态 | 已观察上下文 token | 调用次数 |
| --- | --- | ---: | ---: |
| parent | active、WAIT_JOIN、没有 pending/in-flight | 402 | 1 |
| guardian | active、pending、in-flight | 202 | 1 |
| ordinary | inactive、普通续接 pending | 400 | 2 |
| child | inactive、该 JOIN 的唯一剩余成员、首次请求 pending | 400 | 1 |

逻辑容量为 1200 token，每 agent buffer 为 100。parent 与 guardian
共占 804；ordinary 和 child 都需要 500。夹具明确将 parent 的完整
settled Host backing 作为布尔替身，只允许它借出预算。

真实 runtime 的 `_last_join_child_has_waiting_parent()` 已返回 true，
但 `lend_backed_blocked()` 选择 ordinary。parent 仍为 WAIT_JOIN，
child 仍 inactive，guardian 保持 in-flight。这证明排序行为可以
发生，不证明其在 V247/V260 的频率、JCT 损失或物理迁移成本。

证据为
`experiments/reports/v261_join_dependency_admission_counterexample_cpu_20261027.json`。
报告绑定三个实际源码文件的 SHA256；没有 GPU、D2H/H2D 或物理页释放。
它不是新策略的正确性或性能资格。

## 合适的实现边界

应在必要的 agent 准入/交接点使用已观察的阻塞依赖，继续保持正常
agent 连续执行，而非每个 decode 步改变请求顺序：

1. 已提交的 JOIN parent 仍优先。其后可考虑某个真实 WAIT_JOIN 的
   唯一剩余 child；该信息只来自当前成员完成集合与实际 waiter 状态，
   不预测 child 很快结束，不使用未来 trace 长度。
2. 首先使用已有逻辑余量，或原本就可借出的阻塞预算。保留完整上下文、
   buffer、in-flight、critical、恢复保护和 Host backing 的全部检查。
   不为这一普通容量交接增加 ready-peer 物理驱逐、预取或租约权限。
3. 准入后仍执行正常 retained 策略。依赖优先不提供不可撤销的保护，
   原有老化和原生准入继续生效。依赖变化后重新判断，不缓存永久优先。
4. 统一覆盖周期恢复、idle 恢复与 backed lending 的受益者选择，
   保留 JOIN parent 次序、容量不足时继续推进其他可用工作的能力，
   以及已有 ALL-JOIN sibling co-admission。不能只修一种排序入口。
5. 只在这些已有控制点查询当前依赖索引，并缓存该次选择中的结果；
   避免逐 token 扫描全 DAG。记录实际激活的受益者和合法候选，而非
   把一个候选建议写成已释放的物理容量。

当前 `JOIN_LAST_CHILD_PRIORITY` 只在请求调度的 `join_priority` 模式
生效，`arm_environment()` 和 runtime 都限制了这一点。V260 使用
`workflow` 模式。不能只开启旧开关就宣称 agent 准入已经考虑依赖；
后续候选需要独立、显式声明的 agent 准入选项，默认保持关闭。
这也不同于重新开启普通 execution-frontier restore。

## 已实现的独立候选

声明文件：
`configs/migration/predictive_join_dependency_admission_20261027.json`。

`AGENT_JOIN_DEPENDENCY_ADMISSION=1` 在原有周期恢复、idle 恢复和
backed blocked lending 三条准入路径使用同一依赖次序：

1. 已提交的 JOIN parent 保持优先。
2. 当前真实 ALL-JOIN 唯一剩余的必要 child。
3. 当前真实 ALL-JOIN 的其他未完成必要 child。
4. 其余对象沿用原有续接分组、上下文大小与稳定队列次序。

只有 waiter 当前确实处于该 JOIN 的 WAIT_JOIN，且没有其他活动工具或
前台 child 阻塞时，child 才获得依赖次序。不使用未来输出长度或工具
结束时间；ANY-JOIN、未开始等待、已满足或旧 JOIN 均不能授予该次序。
inferred JOIN 仍由原 ingress 屏蔽。依赖在现有控制点重新查询，
不增加 decode token 路径上的 DAG 扫描。

容量容不下高优先对象时继续尝试可适配的工作，不等待未就绪 sibling，
不为 child 撤销 ready peer 资格。准入后仍执行普通 retained agent
策略；这是 agent 资格排序，不是每轮重新抢占请求。既有 sibling
co-admission 继续只使用已有余量，不要求一定同时开始计算。

`AGENT_BLOCKED_BUDGET_PROOF=observed_blocked` 则解除逻辑预算借出对
长工具预测或 prepare_host 压力分类的依赖：当前身份必须匹配真实
WAIT_TOOL / WAIT_CHILD / WAIT_JOIN，仍需完整、已结算的原生 Host
checkpoint 证明，且不能存在在途写入、加载或缺失路径。即便 native
已经换出了 Device 副本，只要 Host 完整，仍可通过该证明；后续增量
未备份时重新拒绝。这不依赖预测器是否适配新的工具分布。

逻辑借出发生在存在实际排队需求时，每秒至多一次、至多八个 donor
证明，每条路径至多 64 个节点。尚未结算的 D2H、缺失 Host backing、
正在运行、已有 pending 调用、critical 或恢复保护 owner 仍不能借出。
记录的借出关系继续用于实际 JOIN 后继提交后的预算归还；不把逻辑
借出当作页已经释放，不添加物理驱逐权限，不更改原生 allocator。

## 物理共享与证据范围

完整 V230 的 92 对有请求的 parent/child 已通过原始 token corpus
核查；每对都在兼容缓存 namespace 中，但共同前缀均只有 3 token。
child 初始输入均值约 6911 token，parent 历史均值约 111903 token。
因此，当前负载没有大规模 parent/child 前缀重计费的证据。报告：
`experiments/reports/v276_v230_spawn_prefix_sharing_cpu_20261027.json`。

相同 token 和 namespace 只是物理共享的必要条件，不证明同步驻留、
实际 extent 或独占可回收页。新候选没有按 workflow 或角色折扣容量；
`shared_prefixes_deduplicated` 仍如实为 false。未来若实际 workload
含大规模共享，仍须先建立原生 extent 身份、分裂及生命周期证明，
再实现物理 union 记账。此次不为只有 3-token 的 overlap 引入新页账本。

新增 `active_agent_budgets` 只在候选已有的状态采样中记录当前每 agent
的 observed tokens、buffer、pending、in-flight 和 blocked 状态；
审计器可直接核对同一快照的逻辑总账，不再靠请求边界猜测 streaming
增长。旧结果保留原来的重建与未匹配区间，不追写历史 telemetry。
新增 `agent_blocked_budget_lent` 记录实际执行的逻辑交接及选中 donor；
它仍不证明 HBM 释放量或节省的传输时间。

## 后续验证

完整共享 CPU 回归在一次执行中通过 2342 个 case 及四个 subtest，
保留 V273c 的全部 2304 个唯一 case，新增 38 个，没有失败或跳过。
真实 runtime 构造检查确认新开关、EOS-only JOIN、prepare_host、
有条件工具预取与原生普通需求恢复的配置一致。报告：

- `experiments/reports/v279_join_dependency_shared_cpu_20261027.xml`
- `experiments/reports/v280_join_dependency_cpu_evidence_20261027.json`

其中三条准入路径都保留旧行为负对照，验证 V261 反例中新的资格
受益者变为必要 child；另覆盖 inferred/ANY/stale/已满足 JOIN、
其他 parent 阻塞、parent 优先、八目标截断前排序、无 Host backing、
容量不足、运行中 donor、恢复保护及新资格连续性。真实 native CPU
树和 allocator 验证备份未完成时拒绝、完成后通过、实际 demote 后
仍通过、出现未备份增量后再次拒绝；GPU 拷贝和事件仍是替身。

接着审计完整轨迹中真实 JOIN child 的等待、资格与安全预算交接，区分
同样没有可用预算、存在更高优先 JOIN parent、以及普通续接先获得
资格的情况。采样快照不能恢复两次快照之间的完整候选集合，也不能
据此声称另一个选择一定可行。

GPU 候选应使用与 V273 相同完整 V230 请求/token、V269 工具计时表、
容量、seed21 和流式指标，并同时检查全部 workflow 的 JCT、吞吐、
重复迁移和普通请求等待。V273 结束和清理前不分配下一轮 GPU；
不重跑未变化的 native，不用旧自然工具时间的结果替代对照。
尚未验证这些收益，不据 CPU 反例提前登记性能结论。
