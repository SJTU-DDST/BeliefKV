# 按实际缺口借出阻塞 agent 预算

该候选使用 `configs/migration/predictive_fitting_lending_20261027.json`，
在 V364 压力计划及其后续标记复核修复的基础上，单独改变阻塞预算
借出时的 donor 选择。旧配置与所有 paper baseline 仍使用原选择。

## 动机与证据范围

V374 对完整 V364 的实际控制事件、原生请求与 V372 Host hit 对账：

- 317 次实际借出共暂停 416 个 donor。
- 被选请求的逻辑预算缺口合计 16286396 token，实际借出 30395360 token，
  超额 14108964 token。单次借出/缺口的 P50 为 1.606、P90 为 10.231。
- 175 个被借出 owner 在 5 秒内再次提交原生请求，27 个在 1 秒内提交。
- 39 次 JOIN 召回涉及 70 个 borrower，其中 39 个在 5 秒内再次提交。

这说明“总是先暂停最大的阻塞 owner”值得单独审查。上述数字是逻辑
预算和后续请求关联，不是物理空闲空间、唯一迁移字节或可移除 JCT。
日志没有记录旧决策的全部备选 proof，不能据此声称历史上每次都有
更小的安全 donor。后续请求时刻仅供离线诊断，不能成为在线特征。

## 选择与权限

`AGENT_LENDING_SELECTION=fit` 仅允许显式 retained FULL、
非 idle lending 和 `observed_demand` proof：

1. 保留既有等待对象排序、JOIN 依赖优先及最多 8 个等待对象。
2. 保留原 donor 排序后截取的最多 8 个 owner，以及全部 blocking、
   non-running、non-pending、critical 和恢复保护条件。
3. 对这些 owner 的当前 proof 每轮最多检查一次，跨等待对象复用。
   该 proof 只检查已有可见状态和身份，不扫描 Host/native 前缀路径。
4. 在通过 proof 的候选内，以所选请求的真实逻辑缺口为目标，选择
   总借出预算最小的集合。最多 256 个组合；同预算优先更少 owner，
   再按原 JOIN、大小和队列顺序确定稳定选择。
5. 不足以完整覆盖缺口时，不暂停任何部分集合。实际暂停后仍需走
   原生分配、必要备份、锁、DMA 和需求恢复，逻辑借出不提供物理页。
6. 保留原五秒控制、一秒 maintenance、JOIN 召回、EOS 预取、工具
   卸载和普通 agent 连续性。没有新增 ready owner 驱逐或预取权限。

搜索在实际借出维护点执行，不在逐 decode token 路径执行。新增日志
记录当前缺口、通过 proof 的 donor、所选预算和明确策略，便于之后
判断实际选择机会，不再仅凭采样状态猜测。

## 可能的代价

较小的 blocked owner 也可能较快返回；较少超额预算不能保证较少
迁移或更低 JCT。该策略按当前可见容量选择，不引入新的剩余时间
预测，也不为提高传输量更换工具时间表。必须在完整相同 V311
工作量上测量实际工具后继 H2D、重复恢复、平均 JCT、makespan
及控制成本，并保留负结果。

## 验证与实验

V375 的 160 项针对性检查通过，包含：单 donor 和多 donor 选择、
24 组独立穷举对照、proof 拒绝与探测上限、ready/running/critical/
restore 排除、实际 runtime 绑定，以及真实 native tree/allocator
配合 copy/event 替身验证“逻辑借出不释放任何 Device 页或发起拷贝”。
GPU 数值与性能仍须分别验证。

V376b 在一次执行中通过 3003 项共享检查及 4 个子测试，无失败或
跳过，保留原有 2951 个 case 身份并新增 52 个。子测试不另作独立
case 身份计数。V377b 绑定全部源文件及三个真实构造器，FULL 选择
`fit`，默认与工具衰减 ThunderAgent 仍为 `largest`。
V376 的共享检查也全部通过，但后续资格脚本漏走正式启动环境转换，
构造器核验失败；保留其原始报告及 source map，修复仅涉及资格脚本。

共享回归、真实构造器隔离及局部 selector CPU 成本由
`scripts/qualify_fitting_agent_lending.py` 单独封存。1000 次局部选择
的 thread CPU 中位数约 41.28 微秒、P99 约 46.09 微秒；不能将
该测量扩张为整个控制器开销。下一轮只运行该 FULL 候选并复用完整
V345 工具衰减对照，不重放 native 或更换 source/工具时间。
主目标仍要求明显、可重复的完整工作改善，不能以预算超额减少
代替性能验收。
