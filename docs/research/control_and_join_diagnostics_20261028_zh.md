# 控制成本与 JOIN 物理计划的后续核对

本页延续完整 sibling-service/capacity FULL 的审计，没有启动新的 GPU
实验。旧运行仍没有完整控制线程 CPU 计时，不能把服务到完成的增量
判为控制开销，也不能宣布控制开销已经排除。

## 完整预取与恢复证据

对象仍为冻结应用 `100ec17e07ce`、原生 tree `cdd58a4acbb5` 的完整
18 项目、3674 请求、72 JOIN；全部请求、token、原始停止及正常长尾
保持不变，对照仍为完整 V345 工具衰减 ThunderAgent。

独立预取生命周期审计核对全部原生 ACK、version 2 实际使用与完整
JSONL 封存。1153 条 JOIN 命令均已 ACK，总传输 220.781150208 GB：

| 原生使用证据 | 命令数 | GB |
| --- | ---: | ---: |
| version 2 复用已验证 | 1101 | 219.512537088 |
| 使用证据尚未知 | 52 | 1.268613120 |

未知不能计为已复用，也不能计为确定未使用。工具预取仍为零。
复用证据本身不证明完整恢复及时结束、隐藏了多少 DMA 或有净 JCT 收益。

63 个具有独立等待身份的 JOIN 中，57 个在完成前有正 FULL 计划；
22 个有完成前启动传输并在后续复用的证据，合计 91.893104640 GB。
计划 journal 的完成前 `submitted` 覆盖为 21 个；这是计划记录与实际
原生传输时间的不同证据，不能要求二者计数相同，更不能相加。
63 个已观察预取事件也不是完整轨迹的全部 72 个 JOIN。

57 个正计划的首个计划相对 JOIN 完成提前量 P50 约 18.35 ms。
25 个事件曾记录容量预算拒绝，6 个曾没有 Host-backed 缺失拷贝；
原因可重叠，不能据此推算唯一拒绝原因或潜在可恢复字节。
下一请求的 Host-hit 字节也不能直接转为物理 DMA 量。

完整原生等待采样分别有 FULL 35 个、对照 18 个 batch，样本累计
等待为 2034.011 / 2662.835 ms。FULL 首个已观察 FULL 依赖等待
131.780 ms、后续层 1902.231 ms。两侧 cohort、出现时点和样本数不同；
保留原样，不外推整轮等待、不分配到每个请求、不直接计算 JCT 收益。

## 为什么零物理释放仍未得到完整解释

末次 runtime 快照记录 91 次 JOIN 物理计划尝试。整轮没有该计划的
物理释放 journal，亦没有 layer handoff。旧代码没有记录每次尝试
的早退原因，以下两项只解释部分边界：

1. 六条实际 native handoff 拒绝记录均有
   `device_free_tokens < required_reserve_tokens`。冻结 native `begin()`
   在交接前就要求输入和增长预留已有真实 free slots，所以这一条件
   本身足以拒绝；不能认定其余锁、路径或生产者条件全部通过。donor
   的剩余尾页仍须等待真实 D2H ACK，不提前计入 free list。
2. 55 次 `agent_join_blocked_capacity_admitted` 共有 74 个 donor 引用。
   只应用截至 journal 单调时间已经发出的客户端事件，重建出 55 个
   `WAIT_TOOL`、19 个 `WAIT_JOIN`。当前逻辑交接接受两者，新增
   `JoinPhysicalRecovery._eligible()` 直接处理的只有 JOIN donor；
   工具 donor 仍必须走原有预测/成本检查与物理卸载路径。这个重建没有
   独立证明当时调度器已接收这些事件，也不证明实际 HBM 驻留。

74 个 donor 引用不是 91 次物理尝试的分母，也没有覆盖无需新逻辑
grant 的物理计划。不能把工具 donor 全部称为 bug，或绕过预测成本和
原生保护直接卸载。后续应先记录各次物理尝试的实际分支，再决定是否
有必要将同一消费者的工具卸载与 JOIN 恢复更直接衔接。

## 已补充的诊断

`JoinPhysicalRecovery.advance()` 现在在原有 200 ms 退避之后，对每个
真正开始的有界尝试记录一个结果：

- 消费者或恢复证据失效、headroom 不可见；
- 已无缺失前缀、真实物理容量已经足够；
- donor 不再可用、无 Device 副本、共享或 Host 锁等检查；
- 工具 donor 仍走既有预测路径；
- 已发备份等待 ACK、实际 free-list 释放、reserved layer handoff。

计数与单条 `agent_join_physical_plan_result` 记录明确区分真实释放和
reserved extent；成功释放仍保留原 journal。保留八名 donor、64 节点、
原有退避及全部准入/Host/锁/ACK 规则，不新增状态扫描或逻辑额度。
失败或记录器异常均恢复临时绑定。结果记录不是可消除机会或节省时间。

现有 `HotpathTiming` 增加 `thread_time_ns`，与 wall 时间分别记录，
分别扣除嵌套子调用。runtime 仍需显式
`BELIEFKV_PROFILE_SHARED_PATH=1` 且有 opportunity writer 才启用；
顶层每个阶段从第一次开始每 64 次采样，后代继承本次采样决定，
保证样本内嵌套完整。未采样调用不读取两个时钟，未启用时保留原快速返回。

新快照为 schema 2，明确总值仅为已测样本，不做倍乘外推。它仍不直接
测量 GPU 暴露等待；控制 CPU 与 GPU 计算的重叠和下一次提交推迟量
需要结合原生 forward 边界另外分析。不能将采样线程 CPU 当作全部
控制成本，更不能从 JCT 中直接扣除。

## CPU 验证与文件

一轮共享 CPU 验证通过 3668 个唯一 testcase 和六个子测试，无
failure/error/skip，保留之前全部 3580 个唯一用例，新增 88 个。
三个实际构造器隔离通过，reference 核心方法继续原样继承。
更新的 source manifest 包含较早未被 3580 资格覆盖的 sibling 审计器，
以及计时、事件通道和 completion 检查。

小型真实 native CPU 树/分配器夹具使用 copy/event 替身和异步 journal；
三 program、两 sibling、512 个 Device 槽的重复选批结果完全相同：

| 计时方式 | 选批线程 CPU P50/us | P99/us |
| --- | ---: | ---: |
| 关闭 | 89.4425 | 97.344 |
| 每 64 次顶层采样 | 94.7665 | 107.416 |
| 每次计时 | 103.5200 | 109.910 |

采样版本夹具 P50 增加约 5.324 us。这不是最大队列、整个控制器、
writer 线程、DMA 或 GPU 的开销，也不是历史 JCT 的归因。
当前正式配置仍关闭 profiling，没有产生新的 GPU 性能结果。

证据位于 `experiments/reports/sibling_service_capacity_20261028/`：

- `complete_wait_prefetch_bound.json.gz` 及其 `_summary.json`；
- `complete_sampled_restore_waits.json`；
- `control_diagnostics_shared_cpu_r1.xml/.sources.json/.evidence.json`；
- `control_diagnostics_cpu.py`、`control_diagnostics_cpu_r1.json`；
- `diagnostic_followup_review.py/.json`。

独立 follow-up 算子核对旧完整结果的原始 SHA、冻结源码、新审计完整
封存、共享 CPU manifest 及局部成本的源文件身份。初次预取审计将
分析端 commit 写入 `collection_code_commit`；原文件保留，重新绑定
冻结采集端 commit 后完整重跑，测量摘要和所有输入 seal 相同。
两个开发测试问题也保留原 XML：一次夹具断言漏计一个增长页，一次
故障注入 writer 未在 teardown 前恢复；均已修正并包含在共享通过轮次。

整体优化目标仍未完成。下一轮只有在明确诊断或修复目的、冻结当前
资格与源码并检查共享 GPU 状态后才可执行；工具预取继续关闭，其他
baseline 和消融仍等待完整 JCT/吞吐的明确、可重复提升。
