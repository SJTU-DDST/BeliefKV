# 单节点 H2D 发出记录与完整回调降本

日期：2026-10-20（北京时间）。基线 `423a670`。本轮只使用 CPU，
GPU 新实验仍最早于北京时间 06:00 核对空闲资源后启动。

## 修改

`prefetch_expectation_from_native_op` 已将每个原生单节点 H2D 命令冻结为
一个 child，池字节按规范顺序记录。发出通知现在直接复用这个 child 的
节点、池字节和总字节，省去为多 child 聚合创建的字典、生成器和排序。
提交预算也直接读取该 child 的真实总字节，而不是重新求和。

原生两个核算方法、全部身份、代际、容量和提交前检查保留。零 FULL 的
Mamba-only 操作仍有 kv=0，派生 sidecar 的字节仍包含在 num_bytes。
这个单 child 约定不外推到通用 PhysicalTransactionLedger、D2H 或合并
ACK；它们没有修改，也没有将发出通知转为完成或复用信用。

## 完整提交对照

以前完整提交夹具未接入真实的发出追踪回调。V2039 让两侧均调用
NativeReactiveTelemetry.on_prefetch_issued，保留原生对象/代际/前缀长度
追踪；按照新实验配置关闭额外逐 token 诊断。每次 reset 分别清理
未 ACK 和首用库存，不能让前一轮样本污染后一轮。

24-workflow/8-running、稀疏 session 叶子标记、numpy.float64 创建时间、
可用尾部回收；JOIN/tool、4/16/64/256 路径深度、额外诊断开关和
原生/回退预算共 32 组。每组 151 样本、10 次预热，新旧交替。
命令 namespace/序号和记录时钟仅在 CPU fixture 对齐。

每组比较逐条记录、完整 expectation、物理目标、发出后尚未 ACK 的
action/节点追踪和原生读取次数。全部相同，CPU P50 降低
4.32%–8.50%。没有制造 ACK、first-service 或 DMA 完成。

原生预算、关闭额外诊断的线程 CPU P50：

| 来源 | 路径节点 | 提交节点 | 基线 us | 候选 us |
| --- | ---: | ---: | ---: | ---: |
| JOIN | 64 | 64 | 2367.061 | 2177.665 |
| tool | 64 | 64 | 2349.675 | 2194.786 |
| JOIN | 256 | 64 | 3790.110 | 3587.589 |
| tool | 256 | 64 | 3809.545 | 3608.838 |

64 节点、原生预算的两侧均为 session snapshot 1 次、node_by_id 132 次。
该计数包含 CPU 原生 enqueue 替身及真实 tracker 的查询，不是每个节点
字段的全量埋点。256 路径观察 257 个含根节点，最多实际提交 64 个命令，
不能按路径深度统计传输次数。

## 无回调控制

V2040 保留原先不调用发出 tracker 的控制，共 32 组、每组 101 样本。
30 组 CPU P50 较低；JOIN depth4、诊断开启的回退/原生预算分别为
319.339→321.107us、329.099→330.078us，多 1.768/0.979us。
这些回退完整保留。

控制组仍受提交预算不再创建求和生成器的修改影响，不能将它的变化都
归因为从未执行的发出记录分支。各控制在独立 CPU 样本中运行，其
绝对差值不能直接相减来分摊真实回调成本。

## 检查与范围

V2041 在 Python 启动前固定 V88/python 源码，以真实 pytest 模块入口
执行 59 个文件，2392 项检查全部通过，无 failure/error/skip。
新增六组边界核对 FULL-only、hybrid、Mamba-only、派生 FULL/Mamba
sidecar、零池及总字节；发出后事务仍 pending，completed 库存为空。
原有 ACK、首次服务、代际、驻留、容量、工具/JOIN、报告与恢复检查保留。

完整提交使用真实 Python runtime、规划和 tracker，但 cache/controller
仍是 CPU 标量替身。没有真实原生分配、CUDA、DMA、并发 writer/GIL、
端到端服务或线上频率测量。构造、reset、状态对照和 cProfile 在计时外。
局部 CPU 降幅不能作为吞吐/JCT 改善，也不能与其他局部百分比相加。

提交 main 后发布新资格及冻结 checkout，保留同一个一次性等待进程：
最早在北京时间 2026-10-20 06:00 检查 GPU 空闲后先 predictive、后 native。
仍为 24 root、16+8/900s、running8、256K、Host80:20、动态嵌套，
额外逐 token 诊断关闭，V88 引擎/补丁保持。目标继续 active，后续仍以
有用恢复、消费等待、去长尾吞吐及平均 JCT 验收。

- [V2039 完整提交与真实发出回调](v2039_single_child_real_issuance_dispatch_cpu_20261020.json)
- [V2040 无回调控制](v2040_single_child_no_callback_control_cpu_20261020.json)
- [V2041 扩大 CPU 检查](v2041_single_child_qualified_checks_20261020.xml)
