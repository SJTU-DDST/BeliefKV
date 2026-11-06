# Oracle 事件准入覆盖与服务归因修复

2026-11-06，America/Los_Angeles。继续遵守 GPU 暂停指令。
仅重读已完成的 original73、20 初始、平均 70 秒、400 GB Host 实验，
不修改任务、到达表、工具时长、测量成员、在线调度器或 GPU 执行。

## 结论与问题边界

周期恢复之外确实存在事件授予，但不能把这些日志条目都算成互相独立的
新排序机会。尤其工具 A→B 替换先发布 B 的免费额度授予，随后发布同一动作的
替换说明。后者不是第二次授予，也不应立即截断刚授予 B 的服务观察窗口。

修复这一分析错误后，同一批 48 个 ready 授予的精确后续服务仍为 48 个，
在下一次真实控制或干预前完成有用 batch 的记录从 7 个修正为 33 个。
这修正“选择后有没有真正推进”的证据，**没有产生新的服务或 JCT 收益**。
原完整平均 JCT 下降 1.895652% 的结果不因读器修复而变化。

## 已完成执行的入口清单

输入注册：

```text
ops/workflow_original73_host400_oracle_20261102_r2.registration.json
```

原始机会日志固定为此前精确服务审计的同一完整前缀，
499137755 字节。原 token、完成回执、测量组与历史冻结源码绑定继续验证。

| 日志入口或说明 | 记录数 | 正确含义 |
| --- | ---: | --- |
| initial registration | 466 | 全部 `active_after_arrival=false`，没有绕过共享选择的首次直接授予 |
| periodic admission decision | 1868 | 原控制边界记录，含没有对应排序选择的边界 |
| workflow size resume selection | 1867 | 已有周期 Oracle 选择审计的人群 |
| dispatch free capacity admitted | 184 | 实际发布的免费额度授予，184 个不同 request attempt |
| predicted tool replacement | 165 | 严格关联到上述 184 条中的 165 条，不另算准入 |
| 免费授予扣除上述工具替换子集 | 19 | 其余事件补位记录 |
| JOIN free capacity admitted | 184 | 依赖恢复额度，不能一律视作应用 ready 请求 |
| JOIN blocked capacity admitted | 99 | 阻塞预算交接，仍需原生物理恢复 |

这是一份实际授予/说明的日志清单，不是独立竞争次数或全系统覆盖率分母。
JOIN 预留、同一请求的多次状态转换、同一 native 边界内的相关决定不能简单相加。
没有把上述入口改称完全受 Oracle 控制的物理选批。

## 竞争观察与实际服务

`agent_free_capacity_choice` 共记录 62 条跨 workflow 的逻辑容量冲突，
按已有 dispatch identity 去重为 59 个决定。其中 36 条对应普通 dispatch
授予日志，26 条对应 JOIN 免费额度日志；前一类仍可能是工具替换的授予子集。
无保护且应用 ready 的竞争去重为 25 个。

固定共同吞吐窗口内，有 22 条冲突授予记录、19 个去重决定，其中 10 个
无保护且应用 ready。其余观察落在完整测量执行跨度的后段。
这些都是有界候选窗口，不证明 HBM/Host/锁的即时可行性或反事实策略收益，
也不能与周期选择的 170 个逻辑竞争直接相加。

62 条观察中，14 个 warming target 不进入 ready 服务人群，剩余 48 个：

| 相同授予与原生服务证据 | 旧归因 | 修复归因 |
| --- | ---: | ---: |
| 有精确后续服务 | 48 | 48 |
| 窗口内新发起有用 batch | 7 | 34 |
| 窗口内获得有用服务 | 7 | 34 |
| 窗口内完成有用 batch | 7 | 33 |
| 无保护竞争且窗口内完成有用 batch 的去重决定 | 1 | 22 |

29 个 episode 的观察窗口被修正；26 个由未计入窗口内完成变为计入。
固定共同窗口内，完成有用 batch 的去重决定从 1 个修正为 12 个，
其中无保护部分从 0 个修正为 9 个。
33 个完成、48 个精确后续服务属于本节人群，不能与周期的 112/120 相加
当作独立请求数或完整调度收益。

## 修复如何绑定同一次动作

`PublishedGrantInterventions` 只在以下条件全部满足时，把替换说明识别为
刚发布授予的补充记录：

1. 前面紧邻一条通过完整容量检查的单消费者 dispatch grant；
   中间最多允许一条与该 grant 完全对应的 choice observation。
2. consumer 的 request、workflow、invocation、context、epoch、attempt、
   session 和 generation 全部一致。
3. 替换说明中的 before/after 预算与 grant 相同，单调时钟次序正确，
   且携带该事件替换路径的明确物理授权说明和标志。
4. 每条 grant 最多关联一次替换说明；任何其他事件、身份或预算不符都会
   保留原先保守的干预处理。

严格匹配的 marker 仍结束 donor 的窗口；真实后续暂停、重新授予、周期控制
和不匹配 marker 仍结束相应对象的窗口。没有根据“时间很接近”来推断关联，
没有取消真正的调度干预。原日志 165 条替换说明全部满足这一关联，没有
无法绑定的说明。

修复应用于周期、免费竞争和 short-workflow prefill 三个分析入口，
没有改变服务进程中的策略或日志生产代码。

## 周期证据保持独立

重新读取原始批次与机会日志，检查原周期 progress 全部累计计数，以及原
104 次集合变化中新选的 120 个 grant episode。逐字段比较统一 JSON 表示，
保留所有字段、精确身份、时钟、干预边界和有用服务结果。
最终复核确认全部周期累计计数一致，120 个原新选 episode 全部逐字段复现。

最初辅助复核的累计计数已经一致，但它直接比较内存 tuple 与 JSON list，
使逐 episode 相等检查误报为 0；失败记录保留为
`periodic_progress_revalidated.json`。最终复核改为规范 JSON 表示，结果以
`coverage_revalidated.json` 中的 `periodic_progress_revalidation` 为准。
不把辅助检查失败当作原周期执行或性能结果回退。

## 对后续实验的影响

本轮新证据进一步降低了“选中的 agent 大多没有执行，需要再新增优先级”
这一解释的优先级。事件授予的服务传递比原读器报告的完整，但现有有界
无保护竞争仍只有 25 个，不能据此保证短任务排序会有很大收益。

旧 choice 的 320 个候选记录均缺少恢复类别和 queue sequence，不能精确
重建非排序 Full 在这些事件窗口中的完整选择。因此，本轮没有声称 59 个
事件决定都被 Oracle 改变，也没有把 33 个完成都归因为排序收益。
这属于可归因证据的边界，不表示有大量入口漏排。

继续保留已准备的 83 项目、20 初始、平均 60 秒压力诊断候选。GPU 恢复后，
成熟代码合并比较时使用修复后的分析器，并保留原 18 成员完整 JCT 和共同窗口
吞吐。仍需实测新供给是否形成更多合法竞争；本轮不启动 GPU，也不注册自动任务。

## 产物与验证

所有结果位于既有配对的具名子目录，没有新增主实验版本：

```text
experiments/reports/remaining_original_projects_20261030/useful_token_cost_r1/
  host400_registered_copy_pressure_r1/capacity_review_correction_r1/
    admission_entry_coverage_r1/
      free_choice_service.json
      free_choice_service_repaired.json
      coverage.json
      coverage_revalidated.json
      periodic_progress_revalidated.json
      intervention_cpu.xml
      final_cpu.xml
      final_cpu_r2.xml
```

测试覆盖精确授予关联、donor 边界保留、旧 attempt/session、预算或时钟不匹配、
无 choice 记录、重复 marker、异步无关事件、右侧真实干预、原服务证据不变，
以及序列化后的逐字段比较。最终 `final_cpu_r2.xml` 中 121 项相关 CPU 检查
全部通过，无失败或跳过；
CPU 分析和单元测试不建立新的 GPU 或性能结论。
