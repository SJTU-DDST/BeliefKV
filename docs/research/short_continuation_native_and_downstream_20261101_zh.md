# 短续接共批的物理检查与下游收益核对

## 结论

在上一轮高方差负载筛选后，本轮补齐两个此前没有验证的环节：

1. 构造只读原型，使用实际原生FULL缓存、请求身份和`PrefillAdder`
   预算，检查短续接与长chunk是否能共同装入。
2. 检查提前完成短prefill后，按当前真实decode节奏是否能提前完成请求；
   另检验每个prefill后增加一步decode的组合。

物理检查原型在CPU上可行，但没有连接到在线调度，未改变保留Full。
当前负载的下游机会很小：34个具有较早active/ready观察的候选中，
免费提前prefill并免费加入现有decode，局部提前量合计约1.315秒；
每个prefill后增加一步decode的组合仅一条可在窗口内完成，
局部提前约0.438秒，且没有早于最后一个长prefill完成。

这些相关局部提前量不是完整JCT模拟或全局上界，不能直接相加声称
性能收益；它们不足以支持这项完整GPU对比。本轮没有启动或排队GPU实验。
此结论进一步收紧了上一轮将短续接共批列为优先候选的判断：
必须同时证明后续decode及依赖进展，单独消除prefill排队不够。

## 只读物理检查原型

新增`beliefkv/runtime/short_continuation_probe.py`，
只在被显式调用时返回观察，不构造服务对象、不修改引擎、不改变默认行为。
它需要现有Full及ThunderAgent派生的agent资格，不适用于paper/native臂。

检查流程：

1. 原生已有长chunk、FULL-only布局、有限chunk预算、普通非mixed形态。
2. 至少有两条可用请求行，并保守地将chunk owner和额外成员计入运行上限。
3. 最多检查8条原生等待队列项，线程CPU软预算不超过1毫秒。
   软预算只能阻止开始下一次检查，不中断正在执行的原生前缀读取。
4. 每个候选重新校验完整request/attempt/session/generation/context身份、
   当前agent资格、依赖满足、尚未执行输出且没有已分配的请求行。
5. 调用实际`inspect_beliefkv_reentry`，确认完整checkpoint在Device，
   无缺失FULL/辅助状态；在途H2D不能算就绪。已有Device源上的D2H不必阻塞读。
6. 输入增量最多256 token且不超过本轮chunk预算的1/8。
7. 在原生adder的私有副本上先检查短续接，再为owner分配剩余chunk。
   使用**实际空闲页，零eviction credit**，保留原生max-new-token、
   page开销、running decode增长和请求行上下文限制。
8. 每个返回形状分别可检查；它们没有获得共同准入，也没有获得任何
   物理容量信用、页锁、迁移或agent资格。

`_LengthOnly`仅用于原生纯预算路径所需的长度，不复制长输入数组；
真实请求、prefix indices、KV状态、锁receipt、adder、队列和资格不变。
没有调用实际`init_load_back`、`match_prefix`、原生选批提交、备份或驱逐。
未知身份、接口或容量直接保留拒绝诊断，不放宽原生安全检查。

真实原生CPU树用例得到`2 token短续接 + 30 token长chunk`的预算形状；
另一个用例使用真实`add_one_req`和`add_chunked_req`重现相同形状，
实际申请并归还两条请求行及合计输入页，未产生复制命令。
CPU复制/事件仍是已有stand-in，不是GPU数值或模型执行证明。

## CPU成本与局限

`scripts/benchmark_short_continuation_probe.py`在真实CPU索引和radix树上
每档预热20次、测量100次，nice19、固定CPU亲和性、显式隐藏CUDA。
每次重新读取实际前缀，不复用runtime的50毫秒缓存提示。

| 前缀token | 树深度 | 单候选线程CPU P50 | P99 |
| --- | ---: | ---: | ---: |
| 8,192 | 1 | 75.523微秒 | 79.948微秒 |
| 81,920 | 80 | 247.473微秒 | 261.526微秒 |
| 245,760 | 80 | 436.412微秒 | 449.666微秒 |

这些是组件几何，不是新workload。各档100次没有超过本次1毫秒软预算。
每个候选的成本已不可忽略，不能把8个候选的检查视为固定0.5毫秒，
也不宜在逐decode步重复扫描；若未来重新评估，应在原生选批边界有界执行。
没有测量实际GPU调度器、日志或整个控制路径成本。

基准前后实际空闲页、请求行、复制队列、session锚点、请求shape、
agent资格和adder预算均不变；详细样本保留在`cpu_cost_r2.json`。
初轮`cpu_cost_r1.json`仍保留其原源码身份；最终原型增加了runtime当前
native cache必须与传入cache为同一对象的检查，fixture显式绑定后重新测量，
没有用旧报告资格化最终源码。

## 为什么提前prefill没有直接变成请求完成

冻结引擎在存在可执行prefill时优先执行prefill，
之后才执行running decode。短续接即使与长chunk共批完成，
通常只产生首token；其后续输出依然会被连续prefill推迟。
该行为可在原生`get_next_batch_to_run`的prefill优先分支直接核对。

本轮`audit_short_continuation_downstream.py`重新读取同一original56完整日志。
使用旧审计的92条关键路径短续接，仅在出现实际active、pending且ready观察后，
选择早于其原首次服务的下一个长chunk边界。

得到34条候选；这与旧审计32条不矛盾：
旧条件要求观察点**位于chunk worker区间内部**，
新条件允许观察点位于更早的边界间隔，只要求接下来的chunk晚于该观察。
两者都不能据此证明当时物理驻留。

| 同一34条的只读观察/乐观模型 | 数值 |
| --- | ---: |
| 完整输出只有1 token的请求 | 0 |
| 完整输出长度P50 | 63 token |
| 提前prefill后、原prefill之前已有decode机会 | 2 |
| 乐观prefill提前量P50 | 5.326秒 |
| 乐观请求完成提前量P50/P90 | 0 / 0秒 |
| 乐观请求完成提前量合计 | 1.315秒 |

模型假设较早共批prefill没有增量成本、立即产生首token，
随后所有实际decode都可以免费增加该成员，直至其真实输出长度完成。
没有扣除增长容量、输入替换、长chunk延迟、图形状和控制成本。
因此该数字只用于判断机会尺度，不是实际可实现的JCT收益。
按18条测量任务形式上除得0.073秒，也不能把它当作平均JCT改善。

## 与有界decode推进组合

`audit_short_continuation_joint_cadence.py`沿完全相同34个边界，
在每个prefill后插入一步完整decode，使用紧接其后的实际decode
worker时长作局部成本；插入的decode累积推迟后面的prefill。

- 只有1个候选在可用的额外步数内完成，局部提前约437.560毫秒。
- 没有候选早于该段最后一个prefill完成。
- 每段全部额外decode的局部成本代理P50约263.586毫秒。
- 不在该段内完成的候选可能积累输出，但后续服务变化尚未模拟，
  不能把它们记为零潜力或已获得JCT收益。

新增成员会改变decode成本，实际收益还需支付共批、增长及其他workflow
延迟；这些尚未扣除。该组合没有提供足够大的早完成窗口，
因此也不追加完整GPU实验。没有重新开启已暂停的工作量恢复Oracle，
也没有更换负载、工具时间或到达方式来制造机会。

## 验证和保留结果

本轮结果位于
`experiments/reports/remaining_original_projects_20261030/short_continuation_native_probe_r1/`：

- `focused_r4.xml`：最终110项相关CPU检查通过，无失败、错误或skip；
  保留`focused_r3.xml`的102项原始结果。
- `joint_cadence_checks.xml`：此前7项下游与组合模型检查通过，也包含在最终110项中。
- `cpu_cost_r2.json`：最终源码对应的真实CPU树成本、原始样本及源码SHA256。
- `downstream_r1.json`：完整封存输入、34个候选及原decode节奏核对。
- `joint_cadence_r1.json`：同边界的额外decode成本及完成机会。

首轮有1个验证失败：CPU fixture将备份方法包装为真实函数后，
新测试仍当成Mock调用`assert_not_called`。保留`focused_r1.xml`，
修正测试的显式spy，不改变原生检查；之后100项、102项及最终110项检查通过。

当前保留Full、关闭工具预取/逐层handoff、JOIN/EOS、预测工具卸载、
prepare_host及agent连续性均不变。现有平均JCT与吞吐结果没有更新，
整体目标仍未因这些组件或局部模型而完成。
