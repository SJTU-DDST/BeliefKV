# 逐节点状态校验与完整预取提交的 CPU 降本

日期：2026-10-20（北京时间）。基线 `1b8befa`。
GPU 最早仍于北京时间 06:00 启动，本轮只执行 CPU 工作。

## 修改与边界

CPU profile 的 24-workflow/8-running、64 节点正 FULL 恢复计划中，
主要工作在实时闭包读取与步骤规划。优先减少闭包每个节点反复创建
的锁计数组、引用计数组、pending ID 组及其迭代器，并直接迭代 session
叶子 ID，省去短生成器。创建时间只在当前调用复用其类型。

每次观察仍读取当前节点/父节点、FULL/Mamba 驻留与长度、全部锁、
session 引用与叶子标记、在途 ID、创建时间和 key 长度。保留原边界、
类型检查及错误原因；没有跨调用复用物理状态、放松所有权或容量。
引擎、native allocator、事务登记、ACK 与首次消费证明未修改。

原生 FULL session_ref 沿路径累计，session_ids 仅标记叶子。
完整提交基准新增稀疏叶子标记和 numpy.float64 创建时间，保留旧默认值
供历史结果重建。这些设置仅属于 CPU 基准，未改变生产模型与 workload。

## 闭包对照

V2013 覆盖静态、分页静态及共享池布局，1/4/16/64/256 深度，
range/CPU int64 Tensor 索引，共 30 组。每组 51 个样本，3 次预热，
新旧交替执行；各轮改变驻留、锁、引用和 pending 状态。两侧观察同一
cache，所有标量字段相同，线程 CPU 均值降幅为 1.8%–7.1%。

静态池、CPU Tensor 索引的线程 CPU P50：

| 非根路径节点 | 基线 us | 候选 us |
| ---: | ---: | ---: |
| 64 | 330.376 | 314.674 |
| 256 | 1306.717 | 1234.941 |

这些只是完整观察函数的成本，不是整次 JOIN/tool 或调度器成本。

## 完整提交对照

V2016 使用 24 个 workflow、running 8 的 CPU fixture，保留多轮图历史，
稀疏叶子标记和原生 float64 创建时间。完整调用包括 JOIN/tool 选择、
实时观察、容量检查、提交前复核、逐节点 expectation/ledger 登记及可用
尾部容量规划。两侧 native enqueue/controller 为同样的标量替身。

JOIN/tool、4/16/64/256 深度、诊断开关、原生/回退预算共 32 组；
每组 151 个样本、10 次预热。计时是实际 thread_time，cProfile、
fixture 构造、reset 和对照核验在计时外。对齐决策及记录时钟、命令
namespace 和序号，生产命令的 UUID/序号生成未修改。

32 组 CPU P50 均降低，范围 0.8%–3.8%。逐条记录、完整预留内容、
物理目标、字节、预算及原生读取次数相同。动作仍为 pending，
没有制造 DMA 完成、ACK 或复用信用。
原生读取计数覆盖 node_by_id 和 session snapshot；标量字段以完整输出
对照验证，不将这两个调用计数解释成每个属性读取的独立埋点。

原生预算、关闭诊断，线程 CPU P50：

| 来源 | 非根路径节点 | 实际提交节点 | 基线 us | 候选 us |
| --- | ---: | ---: | ---: | ---: |
| JOIN | 64 | 64 | 1724.136 | 1691.813 |
| tool | 64 | 64 | 1750.804 | 1707.979 |
| JOIN | 256 | 64 | 3176.500 | 3119.284 |
| tool | 256 | 64 | 3213.775 | 3110.145 |

原生事务上限仍为 64，不能把 depth=256 解释成传输了 256 个节点。
native预算中的 prefill 上限 4、页大小 1、输入预留 256、每节点 128
token 与池容量仍是受控 CPU 参数，不是完整 GPU 配置重放。
这套 fixture 不证明线上恢复次数、机会覆盖或整体控制工作量。

V2014 初次使用独立随机命名空间，部分 64 节点诊断场景出现回退，
包括 JOIN/native-budget 的 1868.710→1944.931us。V2015 是 3 个样本
的对齐检查；V2016 对齐命令与时钟并扩大样本后，该回退没有复现。
三份记录全部保留。输入对齐、CPU 环境和样本规模均有变化，不能把
所有差异归因于 UUID，也不能只报告初次较好的场景。

## 检查与后续

V2012 的 96 项 observer 检查、V2017 的 2266 项扩大检查通过，
没有 failure/error/skip。范围包括 bool/负数/过界锁与引用、非法 session
标记、numpy 创建时间、pending、循环/深路径、动态驻留与 Tensor 形状，
以及控制、预取、PREPARE、准入、原生收据和消费相关路径。

扩大检查在 Python 启动前将冻结 V88/python 放到 PYTHONPATH 前部，
以真实 `python -m pytest` 入口运行，保留原生检查，没有使用 GPU。

本轮没有真实 allocator、CUDA、DMA、并发 writer/GIL 或端到端性能
测量。局部闭包和完整 CPU 提交的降幅均不能直接作为吞吐/JCT 收益。
24-root 配置与 V88/补丁保持；提交 main 后更新最新合格版本及冻结源码，
同一个等待进程最早于北京时间 06:00 核对 GPU 空闲后启动 predictive，
随后 native。最终仍按去长尾吞吐、平均 JCT 和有用迁移验收。

- [V2013 闭包对照](v2013_closure_scalar_validation_cpu_20261020.json)
- [V2016 最终完整提交](v2016_paired_sparse_native_wait_dispatch_cpu_20261020.json)
- [V2017 扩大检查](v2017_closure_scalar_qualified_checks_20261020.xml)
- [V2014 初次独立命名空间对照](v2014_sparse_native_wait_dispatch_cpu_20261020.json)
- [V2015 基准对齐检查](v2015_paired_wait_dispatch_fixture_20261020.json)
- [V2012 Observer 检查](v2012_closure_scalar_validation_checks_20261020.xml)
