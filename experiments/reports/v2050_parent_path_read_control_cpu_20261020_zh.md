# 原生路径读取降本与启动前冻结

日期：2026-10-20（北京时间）。基线 `2b373c6`。
GPU 最早仍在北京时间 06:00 空闲后启动，本轮只做 CPU 工作。

## 修改与约束

冻结 V88 的 UnifiedTreeNode.parent 是普通字段。原路径采集在检查
parent 后又读取一次以推进节点；现在在当前节点步内复用已经读到的
parent。没有跨调用路径/长度缓存，没有改变 key 顺序、根节点、
None/缺 key、环或原有 4096 遍历边界。

发出、ACK、首用、原生节点/分配对象、代际和驻留证明保持。
原生 key 回调基准同时固定旧版 callback 与静态 _node_key_path/
_key_path_tokens，候选使用自己的 helper。此前 V2044 改的是 tracker
求和，helper 当时未变，所以原先范围仍有效。

## CPU 对照

V2047 使用 24-workflow/8-running、稀疏 session 叶子、原生 float64
创建时间、尾部规划、真实发出 tracker。JOIN/tool、4/16/64/256 路径、
诊断开关及原生/回退预算共 32 组，每组 151 样本、10 次预热。
命令/时钟仅在 CPU fixture 对齐，新旧交替。

全部逐条记录、完整 expectation、未 ACK 追踪、物理目标及原生 node/
session 查询次数相同。31 组 CPU P50 更低，最大约 3.05%；tool depth4、
原生预算且关闭诊断为 390.335→392.242us，多 1.907us，保留原始结果。
parent 的重复属性读取减少，不等于减少 node_by_id 或其他安全验证。

原生预算、关闭额外诊断的线程 CPU P50：

| 来源 | 路径节点 | 提交节点 | 基线 us | 候选 us |
| --- | ---: | ---: | ---: | ---: |
| JOIN | 64 | 64 | 2150.030 | 2097.084 |
| tool | 64 | 64 | 2170.149 | 2117.223 |
| JOIN | 256 | 64 | 3604.300 | 3527.293 |
| tool | 256 | 64 | 3621.040 | 3564.455 |

V2048 用真实 V88 RadixKey AST、相同 cache/key/input/action 地址对照
发出、显式模拟的 verified ACK 和首用。8K/32K/131K/262K、
1/16/42 目标、token 诊断开关共 24 组，每组 151 样本。
全部原生证明、ACK/首用记录、库存和快照字节相同。

关闭 token 诊断时 11/12 组发出 CPU P50 更低；262K 单目标
3.922→3.945us，多 0.023us。开启诊断的 8K 单目标多 0.034us，
131K/16 目标多 0.084us，其余更低。这些控制均保留，不声称所有路径
加速，也不按候选事件数换算 JCT。

## 检查与后续

V2049 固定 V88/python 导入，以真实 pytest 模块入口执行 59 个文件，
2398 项全部通过，无 failure/error/skip。新检查覆盖路径重新挂接、
丢失 key、自环、4095/4096 深度边界，以及旧静态 helper 的来源隔离。
首次 native-key 基准命令未带解释器而被 shell 拒绝，没有生成报告；
纠正 CPU 启动命令后测量，生产代码未因该命令错误调整。

cache/controller、native node 仍为 CPU fixture；RadixKey 用真实冻结
类实现。没有真实 allocator、DMA、GPU、writer/GIL 争用或端到端测量。
构造、reset、对照和 profile 在计时外，局部比例不相加。

提交 main 后更新原一次性 06:00 等待进程的合格源码及冻结 checkout。
保持 24 root、16+8/900s、running8、256K、Host80:20、动态嵌套、
token 诊断关闭与 V88 引擎/补丁。进入启动前候选冻结与观察准备；
6 点后优先检查最新 predictive 的实际启动、故障、有用 Host 副本损失、
恢复压力及消费，随后用 native 对照验收去长尾吞吐和平均 JCT。
目标继续 active。

- [V2047 完整提交](v2047_parent_path_read_dispatch_cpu_20261020.json)
- [V2048 固定静态 helper 的原生 key 回调](v2048_parent_path_read_native_keys_cpu_20261020.json)
- [V2049 扩大检查](v2049_parent_path_qualified_checks_20261020.xml)
