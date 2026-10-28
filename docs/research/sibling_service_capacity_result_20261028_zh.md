# Sibling service/capacity FULL 完整结果

本轮已完成完整工作，但未达到明确、可重复优于 ThunderAgent 的目标。
新增 sibling 服务路径确实运行；新增 JOIN 物理计划没有实际释放记录，
layer handoff 在整个正式 workload 中为零，不能将本轮收益归因于它。

## 比较与完整性

FULL 冻结应用 `100ec17e07ce0d6381df460d4784fbcb98b59019`，
原生 tree `cdd58a4acbb5038207d3a5020c54d7cbcef983b9`。
对照复用已完成 V345 工具衰减 ThunderAgent，原生树为
`11b84f7c31986782be3fbe699bae2574ef092389`；这是明确声明原生引擎
差异的组合系统比较，不是单项消融。

全部 18 个不同项目、3674 个请求、145565966 输入及 549671 输出
token、72 JOIN、V311 工具时间、Device805198/Host200GB、
running/decode24、seed21 和原始停止完全一致。保留全部正常长尾。
完整算子验证两次新鲜 idle 快照、writer drain、真实 owned cleanup
及 supervisor/server/driver 退出。FULL 于 2026-10-28 22:50:00
Asia/Shanghai 结束，之后只运行 CPU 分析，没有下一轮 GPU 排队。

| 配置 | 平均 JCT/s | 全程输出 token/s | Decode worker token/s |
| --- | ---: | ---: | ---: |
| V345 ThunderAgent 工具衰减 | 1697.583645 | 233.371801 | 340.158312 |
| 准入前预测卸载 FULL | 1631.194643 | 238.499008 | 346.325953 |
| 上一轮 JOIN/8-4 分组 FULL | 1647.484437 | 237.608725 | 350.872925 |
| 当前 sibling-service/capacity FULL | 1649.876055 | 237.908369 | 349.227211 |

当前相对 V345 平均 JCT 改善 2.810323%，全程输出率改善 1.943923%；
16 个 workflow 更快、2 个更慢。game_simulation 和 web_cms 分别慢
7.793168 秒、5.252606 秒，均保留。

相对上一轮分组版，当前平均 JCT 差 0.145168%，全程输出率好
0.126108%，没有明显改善。相对准入前卸载版，JCT 差 1.145259%，
输出率差 0.247648%。每个配置均只有一次完整执行，不能把这些
配置变化视为重复实验或恢复旧缺陷版本的依据。

Workflow TTFT 均值为 133.503 秒，对照为 139.028 秒；平均最大
post-first ready-output gap 为 86.649 秒，对照为 73.449 秒。
首次服务略有改善，后续间隔指标仍较差。后者不是普通请求 TBT，
也不是可直接扣除的 JCT。

## Sibling 的实际执行

`complete_sibling_service.json` 独立核对全部原生 worker、真实
ALL-JOIN、请求/session/epoch 及完成回执：

- 1034 条 journal、1047 个请求准入尝试全部通过；
- 覆盖原轨迹全部 20 个并行 JOIN，不是 1034 个 JOIN；
- 13 条记录包含至少两个 sibling 在同一真实 prefill batch 服务；
- 先前 collecting 前缀缺少的完成回执在最终数据中得到验证；
- attempt ID 仍未独立出现在 worker 行中，不能扩大身份保证范围。

末次 runtime snapshot 另记载 2 个 fitting-subset grant、
296 次容量释放重试检查、2 个显式共同选批重排计划。13 个实际共同
batch 也可能包括自然选批和逻辑准入的作用，不能全部归因于两次
重排，更不能把重复 planning opportunity 当作服务覆盖率分母。
这些最后记录计数不是 shutdown-final 计数。

## 物理机制的零结果

末次快照记录 91 次 JOIN 物理计划尝试、71 次 plan-associated
parent native service、16 次无需新逻辑 grant 的物理计划建立。
但完整 journal 中：

- `agent_join_planned_physical_release` 为零；
- 该计划释放到原生 free list 的 token 为零；
- 该计划记录的 reserved handoff token 为零；
- 整轮 `layer_kv_handoff_submitted` 为零。

这说明不能将计划之后的服务描述为物理交接收益。它并不表示整轮
没有其他提前备份、预测工具卸载、原生压力卸载或普通恢复。
独立小 GPU/模型夹具曾真实完成 handoff，不能替代本轮的零触发结果。
51 次输入预留检查、6 次 native refusal 和 4 次不匹配 decode tail
是不同层级计数，不能据此推算完整 handoff 机会通过率。

| 迁移/worker 观测 | 上一轮分组 FULL | 当前 FULL |
| --- | ---: | ---: |
| H2D/GB | 2788.069147 | 2848.215663 |
| D2H/GB | 1274.526302 | 1333.847261 |
| prefill worker 区间/s | 425.891564 | 427.660198 |
| decode worker 区间/s | 1556.110380 | 1563.443464 |

当前 H2D/D2H 多 60.146516/59.320959GB。完整 H2D 的未标记部分
与原生请求 Host hit 字节核算一致，工具预取实际命令数为零；
1153 条已发预取 journal 均为 JOIN。标记 H2D 为 220.781150GB，
但该体积本身没有证明及时复用或净收益，仍需独立 reuse/critical-path
分析。H2D/D2H stream 累计 90.969283/32.065784 秒包含依赖等待，
不是可直接消除或从 JCT 相减的暴露传输时间。

最后记录的额外预测工具卸载释放约 24.900600GB；这不是全部原生
工具压力卸载，也不是同步空闲容量。Idle prepare 实际发出 37 轮，
说明小于 512MiB 的预算路径能运行，但没有独立性能收益证明。

## 重算与控制边界

两侧 140952652 个此前已服务的同 session 公共输入 token，在
first-service cache-hit proxy 中均没有缺失。Host 驱逐后重访
归因中，FULL 的 recomputed units 为 11738，ThunderAgent 为 38657。
这是两种有限、可能重叠的视图，不是所有 kernel 重算的完整统计，
也不能把重复 eviction episode 当作唯一丢失 token。

正式运行仍没有完整控制线程 CPU 计时。小夹具开销和相近 geometry
不能替代这一缺口；也不能把 worker 区间或当前 2.39 秒 JCT 回退
全部归因于新增控制逻辑。

## 后续约束与证据

暂不启动其他 baseline、消融或无机制依据的相同配置重跑。
下一步应区分新增 JOIN 物理计划为何未真正释放容量，以及额外
JOIN/原生恢复与服务间隔的关系，再决定实现修复或预先声明的
workload 机制校准。保持工具预取关闭、原生容量/ACK 权威和全部负结果。
整体目标继续未完成。

CPU 算子位于 `experiments/reports/sibling_service_capacity_20261028/`：
`complete_review.py/.json`、`complete_transfer_review.py/.json`、
`complete_sibling_service.json`。原始执行日志与较早 collecting 前缀
保持不变。新增 sibling 审计 96 项检查通过，不宣称旧 3580 资格覆盖
新增观察脚本。

完整图表在 `experiments/figures/sibling_service_capacity_20261028_r1/`，
趋势、分布和 geometry 三张 PNG 均已实际检查。保留两侧原始引擎
身份、60 秒分箱、末箱真实宽度、空闲及全部正常长尾，不填充结束后零值。
CSV 生成原件另存 `.csv.gz`，可读文本仅规范化为 LF 换行，数据内容不变。

后续完整预取/恢复采样与新增 CPU 诊断见
[`control_and_join_diagnostics_20261028_zh.md`](control_and_join_diagnostics_20261028_zh.md)。
219.512537GB 已证明复用，另有 1.268613GB 的使用状态未知；不能将
220.781150GB 全部视为已复用或已隐藏。新增诊断未运行在本轮冻结应用中，
不改变上面的性能结果或历史控制成本未知边界。
