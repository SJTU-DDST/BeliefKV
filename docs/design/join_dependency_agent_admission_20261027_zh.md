# 已阻塞 JOIN 的 agent 准入

这是 V260 运行期间发现的独立候选方向，尚未实现或开启新策略。
先完成 V260 的计费修补实验，保留 V247 的完整负结果。原目标仍是
完整平均 JCT 和全程输出吞吐共同优于 ThunderAgent，并取得重复证据。

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

## 后续验证

先审计完整轨迹中真实 JOIN child 的等待、资格与安全预算交接，区分
同样没有可用预算、存在更高优先 JOIN parent、以及普通续接先获得
资格的情况。采样快照不能恢复两次快照之间的完整候选集合，也不能
据此声称另一个选择一定可行。

若需要实施，CPU 应包含原策略负对照、相同容量下的依赖受益者选择、
inferred/stale/已满足 JOIN 拒绝、parent 仍优先、不暂停运行中请求、
Host 证明不足或完整目标不适配时回退，以及三条准入路径的一致性。
随后使用相同完整 V230 请求/token/工具时间/容量/seed 的独立 GPU
候选，并同时检查全部 workflow 的 JCT、吞吐、重复迁移和普通请求
等待。尚未验证这些收益，不据 CPU 反例提前登记性能结论。
