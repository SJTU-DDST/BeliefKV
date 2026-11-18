# D3 原冻结版本的完整匹配结果

2026-11-17，Host200、20 个初始 workflow、平均 55 秒泊松到达的两臂已完成。
负载为 133 个不同原项目，固定测量 18 条，实际策略是
**Full + R5** 对照 **Full + R5 + D3**。它们不是 Host400/mean60 的
非排序 Full/Oracle 配对，不跨配置计算收益。

两臂保留同一测量组的全部 2991 个请求、362157 个输出 token、完整自然 JCT；
完成证明通过原始工作、fresh idle drain、真实 owner 退出和实际容量核对。
两臂恰好都完成全部 133 条，没有未提交或右删失的背景。
这允许比较这两次完整人口的累计重算，但不证明整个测量生存期处于稳态。

## 结果

| 指标 | Full + R5 | Full + R5 + D3 | D3 相对变化 |
| --- | ---: | ---: | ---: |
| 固定 18 条平均完整 JCT，秒 | 1851.699339 | 2236.150711 | +20.7621% |
| 共同窗口全部输出，token/s | 288.674771 | 250.090910 | -13.3659% |
| 共同窗口 decode 输出，token/s | 286.039705 | 247.725139 | -13.3948% |
| 测量组实际 Host 丢失前缀再次 prefill，token | 2478348 | 3447194 | +39.0924% |
| 全部 133 条实际 Host 丢失前缀再次 prefill，token | 14777860 | 21102309 | +42.7968% |

共同吞吐窗口是到达后 `[43.758606, 1287.750239]` 秒。
重算必须由原生丢失区间与实际 prefill 输入求交支持；
完成审计分别与原生 counter 核对，287/346 个正丢失请求全部有支持的输入执行。
这些 token 不能换算为纯 GPU 时间或可消除 JCT，也不与 previous-prefix proxy 相加。

回退不只是最后一条任务拖长造成：共同压力窗口的吞吐已经下降。
逐条完整 JCT 变化中，microservice workflow 增加 2883.417322 秒，
对应平均 JCT 增量 160.189851 秒；最后完成的 desktop workflow
增加 724.428333 秒。这是保留全部 18 条后的差值分解，不是删去长尾后的新指标。
仍需检查实际服务和物理容量链路，不能只据总重算差值推断全部回退原因。

## 修复与结果边界

本轮服务使用原 `e117908e` 实现及 `9efca1d2` 分析/接续修复。
它没有包含后来准备的：

- `d070d314`：普通物化许可保留原完整控制和准入窗口时钟。
- `2cd79a6e`：扣除全部在途/已选输入承诺，开始前复核物化容量，
  按实际原生回收约束计算 Host 空间。
- `29e0ae09`：原生 `NO_TOKEN` 后有界退避与释放唤醒，
  当前 chunk 无输入预算时避免无效复核。

这几项已复现的实现缺口需要修复，但尚未证明它们解释全部负结果。
605 项最终定向 CPU 检查通过，不等于修复后的 GPU 收益已经成立。
后续验收仍要求同负载同资源下旧前缀重算减少、共同墙钟吞吐不下降、
固定测量组完整平均 JCT 不变差。

## 绘图失败与接续

原两臂完成后，比较 JSON 正常生成，但 `plot_workflow_cohort_comparison.py`
没有 `Full control` 等四组显示标签的颜色，渲染出现 `KeyError`，
协调器于 15:34:44 UTC 退出。它不是模型回放失败或负性能检查拒绝。
原比较、异常和阶段完成证明保留。

最小修复 `28b32285` 只改绘图器及一个实际四标签渲染测试，
新增检查修复前 1 失败/7 通过，修复后相关 28 项通过。
继承的服务和 native 源码逐文件未变，重新绑定 9326 个 CPU 身份，
不是重跑整套用例。初次恢复准备因未创建输出父目录而在模型启动前退出；
补齐目录创建后按原完成证明继续，没有重跑 Full 或 D3。

新协调器在 D3 工作树下运行：
`experiments/reports/subagent_materialization_20261116/gpu_campaign_guarded_3600/`
`plot_recovery_r1/continued_campaign_r3/plan.json`。
已复用 control/pressure 两组完整证明，只执行尚未启动的 ThunderAgent
acting-decay，再执行 native SGLang。15:54 UTC 核对时，
协调器 `231562/start96817430`、监督器 `232013/start96821682`、
replay `233143/start96836296`、fixed18 观察器 `233144/start96836326`
均实际存活，服务状态为 `running_thunderagent_acting_decay`。
后续状态须重新核验，不能把本段历史快照当作完整 baseline 结果。

绘图修复在 CPU 预整合候选中对应 `19bf613c`；恢复图片已实际打开检查，
JCT、共同窗口 decode 和时间加权 CDF 均正常。图中的 `Full control`
在本配置表示 Full + R5，`Full + materialization` 表示 Full + R5 + D3。

## 证据入口

CPU 预整合工作树的 `terminal_service_integration_20261117/` 下保存：

- `materialization_completed_pair_r1.json`：完整固定组差值和输入摘要。
- `control_host_recompute_complete_r1.json`、
  `pressure_host_recompute_complete_r1.json`：完整实际重算交集。
- `dirty_main_integration_preview_r3/evidence.json`：最新容量修复与原 main
  内容的私有索引预合并，保留 18 个未跟踪文件、3 处 planning-advice 挂接，
  125 个变更 Python 文件语法通过，真实 main 和索引未改。
- `matched_boundary_off_pair_r2/preparation.json`：无额外工具边界抢占的
  Host400/initial20/mean60 Full/Oracle 后续准备；只有两个排序布尔值不同，
  两臂实际 CPU 构造通过，描述已区分 Full 类别顺序与 Oracle 关键路径顺序。

r3 整合预览早于绘图修复；最终整合还要纳入上述两文件增量和本记录，
并核对 main 当时的内容。当前 ThunderAgent/native 完成并实际退出后，
才进行主目录线性整合及最终源资格核对；后续 Oracle 配对尚未注册 GPU。

## 修复版匹配准备

`repaired_materialization_pair_r1/` 已从本轮 control 生成修复版两组配置，
继续使用 133 个原项目、固定 18 条测量成员、Host200、20 初始和平均 55 秒到达。
工具时长、工作量预测器和其他服务参数完全相同，环境中只改变
`BELIEFKV_SUBAGENT_MATERIALIZATION`。两组实际 runtime 构造与缓存挂接检查通过，
均保留 R5 严格剩余工作选择、2 秒窗口、1800 秒独立执行兜底；
额外工具边界抢占、工具预取和逐层 handoff 均关闭。
生产源码与已执行的 605 项报告逐文件相同；这是新的配置装载检查，
不是重新执行 605 项，也不替代整合后的最终源资格或 GPU 验证。

首次准备检查漏传 `git_source_state()` 的 canonical patch 参数，
将普通状态摘要与已登记补丁状态摘要比较，因不等而在 runtime 构造前退出。
补上同一登记补丁后完整原生状态与冻结绑定相等；原生文件没有改动。
该准备失败另存 `initial_constructor_check_failure.json`，没有启动 GPU。

原 D3 最终 runtime 日志记录 804 个 child 登记并开始、10,256 次容量检查、
9,761 次完整普查、0 次不可观测回退、91 次许可撤销。
因此，不能将本轮保护不足解释为超过节点上限而频繁绕过控制；
完整测量生存期 held 首次请求时间加权均值约 0.167，但这不单独证明
每次放行错误或上述实现缺口解释全部重算。
修复版尚未注册 GPU，仍先完成当前两组基线，再整合和匹配验证。

## 重算增量落在哪类请求

后续只读取上述两份完整重算交集报告，未重新扫描原始遥测，
按原 source 的 `first_call` 与 `continuation` 分解：

| 范围与请求类别 | Full + R5 重算 token | Full + R5 + D3 重算 token | 增量 |
| --- | ---: | ---: | ---: |
| 测量组首次请求 | 71,601 | 79,317 | +7,716 |
| 测量组后继请求 | 2,406,747 | 3,367,877 | +961,130 |
| 全部 133 条首次请求 | 575,622 | 516,967 | -58,655 |
| 全部 133 条后继请求 | 14,202,238 | 20,585,342 | +6,383,104 |

测量组净增 968,846 token 中，后继请求占 99.2036%。
全部人口的首次请求重算略减，但后继重算大幅增加，说明不能只用首次 child
放行次数或首次 prefill 的局部表现判断 D3 是否保护了旧工作集。
其中 `first_call` 只是同一 source context 的首次请求，不自动等同于新 child。
重算归属请求也不等于造成驱逐的请求，不能据此直接建立 child 到某次驱逐的因果。

逐测量 workflow 分解中，microservice 的实际重算增加 300,275 token，
desktop 增加 234,979 token，web CMS 增加 150,072 token。
这些与 JCT 差值一起保存在 `materialization_complete_loss_mix_r1.json`；
没有把 token 差值换算为 GPU 时间或将相关性当作全部回退原因。
独立标准 SQL 从原始 633 个正丢失 case 重新聚合，全部类别和逐测量成员
结果一致，见 `materialization_complete_loss_mix_check_r1.json`。

最新修复继续保护 root、普通后继、分块输入及需求 H2D 暂存的容量承诺；
新 child 实际物化前复核，并保留控制节奏和原生回收约束。
修复源的 638 项定向检查及整合预览的 915 项实际 CPU 检查已通过。
它们尚未进入本轮冻结服务，旧负结果不重标；修复版实际收益仍待匹配 GPU 比较。
